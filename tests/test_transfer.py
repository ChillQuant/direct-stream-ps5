"""Local HTTP + FTP integration tests. Install requirements-dev.txt to run."""
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transfer_core import (MIB, Meter, StopToken, TransferError, make_reader, probe_source,
    transfer, valid_name, valid_folder, check_ftp_storage, validate_source_url, connect_ftp, close_ftp)
from ps5_streamer import Manager, Handler, validated_settings
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from pyftpdlib.ioloop import IOLoop
PAYLOAD = bytes(range(256)) * (5 * 4096 + 73)

class HTTPFixture(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    retry_count = 0
    def handle(self):
        try: super().handle()
        except (ConnectionResetError, BrokenPipeError): pass
    def log_message(self, *_): pass
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/redirect':
            self.send_response(302); self.send_header('Location', '/file'); self.send_header('Content-Length','0'); self.end_headers(); return
        if path == '/html':
            self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length','3'); self.end_headers(); self.wfile.write(b'bad'); return
        raw = self.headers.get('Range',''); start, end = 0, len(PAYLOAD)-1
        use_range = bool(raw) and path not in ('/no-range','/unknown')
        if path == '/ignore-resume' and raw and not raw.endswith('0-0'): use_range = False
        if use_range:
            a,b=raw.removeprefix('bytes=').split('-'); start=int(a); end=min(int(b) if b else end,end)
        if path == '/retry' and end-start>1 and HTTPFixture.retry_count == 0:
            HTTPFixture.retry_count += 1
            self.send_response(503);self.send_header('Content-Length','0');self.end_headers();return
        body = PAYLOAD[start:end+1]
        self.send_response(206 if use_range else 200)
        if use_range:
            bad = path == '/wrong-range' and end-start > 1
            self.send_header('Content-Range', f'bytes {start+1 if bad else start}-{end}/{len(PAYLOAD)}')
        self.send_header('Content-Type','application/octet-stream')
        if path == '/compressed' and end-start>1: self.send_header('Content-Encoding','gzip')
        if path != '/no-validator': self.send_header('ETag', '"v2"' if path == '/changed' or (path == '/changes-during-read' and end-start>1) else '"fixture-v1"')
        if path != '/unknown': self.send_header('Content-Length',str(len(body)))
        else: self.send_header('Connection','close'); self.close_connection=True
        self.end_headers()
        try:
            if path == '/short' and len(body)>1:
                self.wfile.write(body[:len(body)//2]); self.close_connection=True
            elif path == '/slow':
                for i in range(0,len(body),65536):
                    self.wfile.write(body[i:i+65536]); self.wfile.flush(); time.sleep(.04)
            else: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): pass

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.temp=tempfile.TemporaryDirectory(); cls.root=Path(cls.temp.name)
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),HTTPFixture);cls.http.daemon_threads=True
        cls.http_thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.http_thread.start()
        authorizer=DummyAuthorizer(); authorizer.add_user('test','pass',str(cls.root),perm='elradfmwMT')
        class FTPFixture(FTPHandler): pass
        FTPFixture.authorizer=authorizer
        cls.ioloop=IOLoop(); cls.ftp=FTPServer(('127.0.0.1',0),FTPFixture,ioloop=cls.ioloop)
        cls.ftp_thread=threading.Thread(target=cls.ftp.serve_forever,kwargs={'timeout':.05,'handle_exit':False},daemon=True);cls.ftp_thread.start()
        cls.cfg=validated_settings({'host':'127.0.0.1','port':cls.ftp.socket.getsockname()[1], 'folder':'/target/nested','username':'test','password':'pass','streams':4,'buffer_mb':16,'chunk_mb':1,'retries':0})
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.ftp.close_all();cls.ftp_thread.join(2);cls.temp.cleanup()
    def url(self,path='/file'):return f'http://127.0.0.1:{self.http.server_port}{path}'
    def job(self,path='/file',name=None):
        return {'id':self._testMethodName,'name':name or self._testMethodName+'.bin','kind':'url','source':self.url(path),'identity':None,'transferred':0}
    def run_job(self,j,cfg=None,token=None):
        events=[];transfer(j,cfg or self.cfg,token or StopToken(),lambda e,v:events.append((e,v)),lambda:None);return events
    def dest(self,j):return self.root/'target'/'nested'/j['name']
    def partial(self,j):return self.root/'target'/'nested'/f"{j['name']}.{j['id']}.ps5part"
    def read_source(self,path='/file',offset=0,streams=4):
        tok=StopToken();src=probe_source('url',self.url(path),tok);m=Meter();r=make_reader(src,offset,{**self.cfg,'streams':streams},tok,m);out=bytearray()
        try:
            while True:
                block=r.read()
                if not block:break
                out.extend(block)
        finally:r.close();tok.cancel()
        return bytes(out)
    def test_parallel_upload_hash_and_finalize(self):
        j=self.job();events=self.run_job(j)
        self.assertEqual(hashlib.sha256(self.dest(j).read_bytes()).digest(),hashlib.sha256(PAYLOAD).digest());self.assertFalse(self.partial(j).exists());self.assertEqual(events[-1][0],'complete')
    def test_redirect_parallel(self):self.assertEqual(self.read_source('/redirect'),PAYLOAD)
    def test_no_range_fallback(self):self.assertEqual(self.read_source('/no-range'),PAYLOAD)
    def test_unknown_length(self):
        j=self.job('/unknown');self.run_job(j);self.assertEqual(self.dest(j).read_bytes(),PAYLOAD);self.assertEqual(j['total'],len(PAYLOAD))
    def test_parallel_wrong_range_rejected(self):
        with self.assertRaises(TransferError):self.read_source('/wrong-range')
    def test_short_http_rejected(self):
        with self.assertRaises(OSError):self.read_source('/short',streams=1)
    def test_html_rejected(self):
        with self.assertRaises(TransferError):probe_source('url',self.url('/html'),StopToken())
    def test_resume_non_aligned_offset(self):
        j=self.job();j['identity']=probe_source('url',j['source'],StopToken()).identity();j['stage_owned']=True;self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(PAYLOAD[:1234567]);self.run_job(j);self.assertEqual(self.dest(j).read_bytes(),PAYLOAD)
    def test_ignored_resume_rejected_without_corruption(self):
        j=self.job('/ignore-resume');j['identity']=probe_source('url',j['source'],StopToken()).identity();j['stage_owned']=True;self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(PAYLOAD[:1234567])
        with self.assertRaises(TransferError):self.run_job(j,{**self.cfg,'streams':1})
        self.assertFalse(self.dest(j).exists());self.assertEqual(self.partial(j).read_bytes(),PAYLOAD[:1234567])
    def test_source_identity_change_rejected(self):
        j=self.job();j['identity']=probe_source('url',j['source'],StopToken()).identity();j['stage_owned']=True;j['source']=self.url('/changed')
        with self.assertRaises(TransferError):self.run_job(j)
        self.assertFalse(self.dest(j).exists())
    def test_resume_without_validator_rejected(self):
        j=self.job('/no-validator');j['identity']=probe_source('url',j['source'],StopToken()).identity();j['stage_owned']=True;self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(PAYLOAD[:12345])
        with self.assertRaises(TransferError):self.run_job(j)
    def test_existing_final_protected(self):
        j=self.job();self.dest(j).parent.mkdir(parents=True,exist_ok=True);self.dest(j).write_bytes(b'existing data')
        with self.assertRaises(TransferError):self.run_job(j)
        self.assertEqual(self.dest(j).read_bytes(),b'existing data')
    def test_explicit_overwrite(self):
        j=self.job();self.dest(j).parent.mkdir(parents=True,exist_ok=True);self.dest(j).write_bytes(b'old');j['overwrite']=True;self.run_job(j);self.assertEqual(self.dest(j).read_bytes(),PAYLOAD)
    def test_local_upload(self):
        source=self.root/'local.bin';source.write_bytes(PAYLOAD);j=self.job();j.update(kind='local',source=str(source));self.run_job(j);self.assertEqual(self.dest(j).read_bytes(),PAYLOAD)
    def test_zero_byte_local_upload(self):
        source=self.root/'empty.bin';source.write_bytes(b'');j=self.job();j.update(kind='local',source=str(source));self.run_job(j);self.assertEqual(self.dest(j).stat().st_size,0)
    def test_bounded_buffer(self):
        tok=StopToken();m=Meter();src=probe_source('url',self.url(),tok);reader=make_reader(src,0,{**self.cfg,'buffer_mb':4},tok,m)
        try:
            time.sleep(.3);self.assertLessEqual(reader.assign-reader.read_index,4);self.assertLessEqual(m.snapshot()['buffered'],4*MIB)
        finally:reader.close();tok.cancel()
    def test_cancel_interrupts_pipeline(self):
        j=self.job('/slow');token=StopToken();errors=[]
        def run():
            try:self.run_job(j,token=token)
            except Exception as e:errors.append(e)
        t=threading.Thread(target=run);t.start();time.sleep(.25);t0=time.monotonic();token.cancel();t.join(3);self.assertFalse(t.is_alive());self.assertLess(time.monotonic()-t0,3);self.assertTrue(errors);self.assertFalse(self.dest(j).exists())
    def test_temporary_http_error_retried(self):
        HTTPFixture.retry_count=0
        self.assertEqual(self.read_source('/retry'),PAYLOAD)
        self.assertEqual(HTTPFixture.retry_count,1)
    def test_source_changes_during_parallel_download(self):
        with self.assertRaises(TransferError):self.read_source('/changes-during-read')
    def test_source_changes_during_single_download(self):
        with self.assertRaises(TransferError):self.read_source('/changes-during-read',streams=1)
    def test_compressed_ranges_rejected(self):
        with self.assertRaises(TransferError):self.read_source('/compressed')
    def test_remote_partial_larger_than_source_rejected(self):
        j=self.job();j['identity']=probe_source('url',j['source'],StopToken()).identity();j['stage_owned']=True
        self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(PAYLOAD+b'wrong')
        with self.assertRaises(TransferError):self.run_job(j)
        self.assertFalse(self.dest(j).exists())
    def test_unowned_partial_rejected(self):
        j=self.job();self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(b'unrelated')
        with self.assertRaises(TransferError):self.run_job(j)
        with self.assertRaises(TransferError):self.run_job(j)
        self.assertEqual(self.partial(j).read_bytes(),b'unrelated')
    def test_local_resume(self):
        source=self.root/'local-resume.bin';source.write_bytes(PAYLOAD);j=self.job();j.update(kind='local',source=str(source))
        j['identity']=probe_source('local',str(source),StopToken()).identity();j['stage_owned']=True
        self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(PAYLOAD[:1234567])
        self.run_job(j);self.assertEqual(self.dest(j).read_bytes(),PAYLOAD)
    def test_destination_change_guard(self):
        with tempfile.TemporaryDirectory() as d:
            m=Manager(d)
            try:
                m.configure(self.cfg);m.add_jobs({'items':[{'source':self.url()}]})
                m.jobs[0]['destination']={k:self.cfg[k] for k in ('host','port','folder','username')}
                m.configure({**self.cfg,'folder':'/other'});m.action('start')
                deadline=time.monotonic()+3
                while m.jobs[0]['state']=='queued' and time.monotonic()<deadline:time.sleep(.02)
                self.assertEqual(m.jobs[0]['state'],'failed');self.assertIn('destination changed',m.jobs[0]['detail'])
            finally:m.stop()
    def test_input_injection_rejected(self):
        for value in ['../x','x\r\nDELE file','..','a/b']:
            with self.assertRaises(TransferError):valid_name(value)
        for value in ['/data/../etc','/data\nNOOP','relative']:
            with self.assertRaises(TransferError):valid_folder(value)
    def test_invalid_settings(self):
        for change in [{'limit_mbps':float('nan')},{'port':65536},{'streams':64},{'buffer_mb':16,'chunk_mb':8,'streams':8}]:
            with self.assertRaises(TransferError):validated_settings({**self.cfg,**change})
    def test_queue_restart_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            m=Manager(d);m.configure(self.cfg);m.add_jobs({'items':[{'source':self.url()}]});m.stop();m2=Manager(d)
            try:
                self.assertEqual(m2.jobs[0]['state'],'paused');self.assertFalse(m2.running);self.assertNotIn('password',json.loads((Path(d)/'state.json').read_text())['settings']);self.assertEqual((Path(d)/'state.json').stat().st_mode&0o777,0o600)
            finally:m2.stop()
    def test_api_auth_and_origin(self):
        with tempfile.TemporaryDirectory() as d:
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.manager=Manager(d);server.token='test-session';t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            def get(headers):
                c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('GET','/api/state',headers=headers);r=c.getresponse();status=r.status;r.read();c.close();return status
            try:
                self.assertEqual(get({}),401);self.assertEqual(get({'X-Session-Token':'test-session'}),200);self.assertEqual(get({'X-Session-Token':'test-session','Origin':'https://attacker.example'}),403);self.assertEqual(get({'X-Session-Token':'test-session','Host':'attacker.example'}),403)
            finally:server.shutdown();server.server_close();server.manager.stop()
    def test_validate_source_url(self):
        v = validate_source_url(self.url('/file'), StopToken())
        self.assertTrue(v['valid'])
        self.assertTrue(v['ranges'])
        self.assertEqual(v['size'], len(PAYLOAD))
        v_bad = validate_source_url(self.url('/html'), StopToken())
        self.assertFalse(v_bad['valid'])
        self.assertIsNotNone(v_bad['error'])
    def test_manager_bulk_and_reorder_actions(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                m.configure(self.cfg)
                m.add_jobs({'items': [{'source': self.url(), 'name': 'file1.bin'}, {'source': self.url(), 'name': 'file2.bin'}, {'source': self.url(), 'name': 'file3.bin'}]})
                id1, id2, id3 = m.jobs[0]['id'], m.jobs[1]['id'], m.jobs[2]['id']
                m.action('reorder', id3, extra={'new_index': 0})
                self.assertEqual(m.jobs[0]['id'], id3)
                self.assertEqual(m.jobs[1]['id'], id1)
                m.action('bulk_pause', extra={'ids': [id1, id3]})
                self.assertEqual(m.jobs[0]['state'], 'paused')
                self.assertEqual(m.jobs[1]['state'], 'paused')
                m.action('bulk_resume', extra={'ids': [id1, id3]})
                self.assertEqual(m.jobs[0]['state'], 'queued')
                m.action('bulk_remove', extra={'ids': [id3]})
                self.assertEqual(len(m.jobs), 2)
                self.assertNotIn(id3, [j['id'] for j in m.jobs])
            finally:
                m.stop()
    def test_check_ftp_storage_unsupported_safe(self):
        tok = StopToken()
        ftp = connect_ftp(self.cfg, tok)
        try:
            space = check_ftp_storage(ftp, self.cfg['folder'])
            self.assertTrue(space is None or isinstance(space, int))
        finally:
            close_ftp(ftp, tok)
if __name__=='__main__':unittest.main()
