"""Local HTTP + FTP integration tests. Install requirements-dev.txt to run."""
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transfer_core import (MIB, Meter, StopToken, TransferError, make_reader, probe_source,
    transfer, valid_name, valid_folder, check_ftp_storage, validate_source_url, validate_multipart_source,
    find_unar_tool, find_extracted_payload, cleanup_stale_staging_directories, transfer_staged_archive, connect_ftp, close_ftp,
    find_unrar_ps5_payload, send_ps5_payload, generate_unrar_config, fetch_ps5_unrar_log, transfer_ps5_remote_archive,
    DEFAULT_UNRAR_DIR, DEFAULT_UNRAR_CONFIG_PATH, DEFAULT_UNRAR_LOG_PATH, DEFAULT_ETA_HEN_PAYLOAD_PORT)
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
        raw = self.headers.get('Range','');
        custom = getattr(HTTPFixture, 'custom_files', {})
        if path in custom:
            full_data = custom[path]
        else:
            full_data = PAYLOAD
        if path.startswith('/multipart-'):
            p_idx = int(path.split('-')[1])
            part_sz = len(PAYLOAD) // 3
            if p_idx == 1:
                full_data = PAYLOAD[:part_sz]
            elif p_idx == 2:
                full_data = PAYLOAD[part_sz:2*part_sz]
            else:
                full_data = PAYLOAD[2*part_sz:]
        start, end = 0, len(full_data)-1
        use_range = bool(raw) and path not in ('/no-range','/unknown')
        if path == '/ignore-resume' and raw and not raw.endswith('0-0'): use_range = False
        if use_range:
            a,b=raw.removeprefix('bytes=').split('-'); start=int(a); end=min(int(b) if b else end,end)
        if path == '/retry' and end-start>1 and HTTPFixture.retry_count == 0:
            HTTPFixture.retry_count += 1
            self.send_response(503);self.send_header('Content-Length','0');self.end_headers();return
        body = full_data[start:end+1]
        self.send_response(206 if use_range else 200)
        if use_range:
            bad = path == '/wrong-range' and end-start > 1
            self.send_header('Content-Range', f'bytes {start+1 if bad else start}-{end}/{len(full_data)}')
        self.send_header('Content-Type','application/octet-stream')
        if path == '/compressed' and end-start>1: self.send_header('Content-Encoding','gzip')
        if path != '/no-validator': self.send_header('ETag', f'"mp-{path}"' if path.startswith('/multipart-') else ('"v2"' if path == '/changed' or (path == '/changes-during-read' and end-start>1) else '"fixture-v1"'))
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
    def test_folder_upload(self):
        folder_dir = self.root / 'game_folder'
        folder_dir.mkdir(parents=True, exist_ok=True)
        (folder_dir / 'eboot.bin').write_bytes(b'EBOOT_BINARY_DATA')
        (folder_dir / 'empty.dat').write_bytes(b'')
        sub = folder_dir / 'sce_sys'
        sub.mkdir(parents=True, exist_ok=True)
        (sub / 'param.sfo').write_bytes(b'PARAM_SFO_DATA')
        sub2 = folder_dir / 'data' / 'audio'
        sub2.mkdir(parents=True, exist_ok=True)
        (sub2 / 'bgm.wav').write_bytes(b'WAV_AUDIO_DATA')
        j = {
            'id': 'folder_job_1',
            'name': 'game_folder',
            'kind': 'folder',
            'source': str(folder_dir),
            'folder': '/target/nested/game_folder',
            'transferred': 0,
            'completed_files': []
        }
        events = self.run_job(j)
        self.assertEqual(events[-1][0], 'complete')
        self.assertEqual(j['transferred'], len(b'EBOOT_BINARY_DATA') + len(b'PARAM_SFO_DATA') + len(b'WAV_AUDIO_DATA'))
        self.assertEqual(j['files_count'], 4)
        self.assertEqual((self.root / 'target' / 'nested' / 'game_folder' / 'eboot.bin').read_bytes(), b'EBOOT_BINARY_DATA')
        self.assertEqual((self.root / 'target' / 'nested' / 'game_folder' / 'empty.dat').stat().st_size, 0)
        self.assertEqual((self.root / 'target' / 'nested' / 'game_folder' / 'sce_sys' / 'param.sfo').read_bytes(), b'PARAM_SFO_DATA')
        self.assertEqual((self.root / 'target' / 'nested' / 'game_folder' / 'data' / 'audio' / 'bgm.wav').read_bytes(), b'WAV_AUDIO_DATA')
    def test_folder_upload_resume_skips_completed(self):
        folder_dir = self.root / 'game_folder_resume'
        folder_dir.mkdir(parents=True, exist_ok=True)
        (folder_dir / 'file1.bin').write_bytes(b'11111')
        (folder_dir / 'file2.bin').write_bytes(b'22222')
        target_f1 = self.root / 'target' / 'nested' / 'game_folder_resume' / 'file1.bin'
        target_f1.parent.mkdir(parents=True, exist_ok=True)
        target_f1.write_bytes(b'11111')
        j = {
            'id': 'folder_job_2',
            'name': 'game_folder_resume',
            'kind': 'folder',
            'source': str(folder_dir),
            'folder': '/target/nested/game_folder_resume',
            'transferred': 5,
            'completed_files': ['file1.bin']
        }
        events = self.run_job(j)
        self.assertEqual(events[-1][0], 'complete')
        self.assertEqual(j['transferred'], 10)
        self.assertEqual((self.root / 'target' / 'nested' / 'game_folder_resume' / 'file2.bin').read_bytes(), b'22222')
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
    def test_zero_byte_partial_accepted(self):
        j=self.job();self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(b'')
        self.run_job(j)
        self.assertEqual(self.dest(j).read_bytes(),PAYLOAD)
    def test_overwrite_clears_unowned_partial(self):
        j=self.job();self.partial(j).parent.mkdir(parents=True,exist_ok=True);self.partial(j).write_bytes(b'unrelated')
        j['overwrite']=True
        self.run_job(j)
        self.assertEqual(self.dest(j).read_bytes(),PAYLOAD)
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
    def test_resume_after_unowned_partial_restarts_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            m=Manager(d)
            try:
                m.configure(self.cfg)
                m.add_jobs({'items':[{'source':self.url()}]})
                job=m.jobs[0]
                job['detail']='Found a partial file without source history; restart this job.'
                job['error_info']={'message':'Found a partial file without source history; restart this job.'}
                job['state']='failed'
                old_id=job['id']
                m.action('resume',job_id=old_id)
                self.assertNotEqual(m.jobs[0]['id'],old_id)
                self.assertEqual(m.jobs[0]['state'],'queued')
            finally:m.stop()
    def test_toggle_overwrite_action(self):
        with tempfile.TemporaryDirectory() as d:
            m=Manager(d)
            try:
                m.configure(self.cfg)
                m.add_jobs({'items':[{'source':self.url()}]})
                job=m.jobs[0]
                self.assertFalse(job.get('overwrite',False))
                m.action('toggle_overwrite',job_id=job['id'])
                self.assertTrue(job['overwrite'])
                m.action('toggle_overwrite',job_id=job['id'])
                self.assertFalse(job['overwrite'])
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

    def test_lock_instance_posix_and_windows_simulation(self):
        from ps5_streamer import _lock_instance
        from unittest.mock import patch, MagicMock

        with tempfile.TemporaryDirectory() as d:
            dir_path = Path(d)
            # 1. Native POSIX lock test
            l1 = _lock_instance(dir_path)
            self.assertIsNotNone(l1)
            l2 = _lock_instance(dir_path)
            self.assertIsNone(l2)
            l1.close()
            l3 = _lock_instance(dir_path)
            self.assertIsNotNone(l3)
            l3.close()

            # 2. Windows msvcrt simulation test
            mock_msvcrt = MagicMock()
            locked = [False]
            def fake_locking(fd, mode, nbytes):
                if locked[0]:
                    raise OSError("File is locked by another process")
                locked[0] = True
            mock_msvcrt.locking = fake_locking
            mock_msvcrt.LK_NBLCK = 1

            with patch('os.name', 'nt'), patch.dict('sys.modules', {'msvcrt': mock_msvcrt}):
                win_dir = dir_path / "win_test"
                win_dir.mkdir()
                w1 = _lock_instance(win_dir)
                self.assertIsNotNone(w1)
                w2 = _lock_instance(win_dir)
                self.assertIsNone(w2)
                w1.close()

    def test_smart_destination_routing_and_per_job_folder(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            # 1. Native PS5 game (.ffpfsc) routes to /data/ShadowMount
            m.add_jobs({"kind": "url", "items": [{"source": "http://127.0.0.1:9099/demons.ffpfsc"}]})
            j1 = next(j for j in m.jobs if "demons.ffpfsc" in j["name"])
            self.assertEqual(j1.get("folder"), "/data/ShadowMount")

            # 2. Native PS5 raw disk (.exfat) routes to /data/ShadowMount
            m.add_jobs({"kind": "url", "items": [{"source": "http://127.0.0.1:9099/spider.exfat"}]})
            j2 = next(j for j in m.jobs if "spider.exfat" in j["name"])
            self.assertEqual(j2.get("folder"), "/data/ShadowMount")

            # 3. Explicit folder override in request is respected
            m.add_jobs({"kind": "url", "folder": "/data/custom_dir", "items": [{"source": "http://127.0.0.1:9099/custom.bin"}]})
            j3 = next(j for j in m.jobs if "custom.bin" in j["name"])
            self.assertEqual(j3.get("folder"), "/data/custom_dir")

            # 4. Default destination routes to /data/homebrew
            m.add_jobs({"kind": "url", "items": [{"source": "http://127.0.0.1:9099/homebrew_tool.elf"}]})
            j4 = next(j for j in m.jobs if "homebrew_tool.elf" in j["name"])
            self.assertEqual(j4.get("folder"), "/data/homebrew")
            m.stop()

    def test_multipart_local_upload_and_stitch(self):
        part_sz = len(PAYLOAD) // 3
        p1 = self.root / 'game.pkg.001'
        p2 = self.root / 'game.pkg.002'
        p3 = self.root / 'game.pkg.003'
        p1.write_bytes(PAYLOAD[:part_sz])
        p2.write_bytes(PAYLOAD[part_sz:2*part_sz])
        p3.write_bytes(PAYLOAD[2*part_sz:])

        j = {
            'id': 'mp_local_test',
            'name': 'game.pkg',
            'kind': 'multipart',
            'source': f'3 parts: game.pkg',
            'parts': [
                {'source': str(p1), 'kind': 'local', 'name': 'game.pkg.001'},
                {'source': str(p2), 'kind': 'local', 'name': 'game.pkg.002'},
                {'source': str(p3), 'kind': 'local', 'name': 'game.pkg.003'},
            ],
            'identity': None,
            'transferred': 0,
        }
        events = self.run_job(j)
        dest_file = self.root / 'target' / 'nested' / 'game.pkg'
        self.assertTrue(dest_file.exists())
        self.assertEqual(dest_file.read_bytes(), PAYLOAD)
        self.assertFalse(self.partial(j).exists())
        self.assertEqual(events[-1][0], 'complete')

    def test_multipart_http_upload_and_stitch(self):
        urls = [self.url('/multipart-1'), self.url('/multipart-2'), self.url('/multipart-3')]
        j = {
            'id': 'mp_http_test',
            'name': 'webgame.pkg',
            'kind': 'multipart',
            'source': '3 parts: webgame.pkg',
            'parts': [
                {'source': urls[0], 'kind': 'url', 'name': 'webgame.pkg.001'},
                {'source': urls[1], 'kind': 'url', 'name': 'webgame.pkg.002'},
                {'source': urls[2], 'kind': 'url', 'name': 'webgame.pkg.003'},
            ],
            'identity': None,
            'transferred': 0,
        }
        events = self.run_job(j)
        dest_file = self.root / 'target' / 'nested' / 'webgame.pkg'
        self.assertTrue(dest_file.exists())
        self.assertEqual(dest_file.read_bytes(), PAYLOAD)
        self.assertFalse(self.partial(j).exists())
        self.assertEqual(events[-1][0], 'complete')
        status_msgs = [v for e, v in events if e == 'status']
        self.assertTrue(any('Part 1/3' in msg for msg in status_msgs))
        self.assertTrue(any('Part 2/3' in msg for msg in status_msgs))
        self.assertTrue(any('Part 3/3' in msg for msg in status_msgs))

    def test_multipart_http_resume_in_middle_part(self):
        urls = [self.url('/multipart-1'), self.url('/multipart-2'), self.url('/multipart-3')]
        part_sz = len(PAYLOAD) // 3
        resume_offset = part_sz + 12345
        j = {
            'id': 'mp_resume_test',
            'name': 'resumed_game.pkg',
            'kind': 'multipart',
            'source': '3 parts: resumed_game.pkg',
            'parts': [
                {'source': urls[0], 'kind': 'url', 'name': 'resumed_game.pkg.001'},
                {'source': urls[1], 'kind': 'url', 'name': 'resumed_game.pkg.002'},
                {'source': urls[2], 'kind': 'url', 'name': 'resumed_game.pkg.003'},
            ],
            'identity': None,
            'transferred': 0,
            'stage_owned': True,
        }
        part_file = self.partial(j)
        part_file.parent.mkdir(parents=True, exist_ok=True)
        part_file.write_bytes(PAYLOAD[:resume_offset])

        events = self.run_job(j)
        dest_file = self.root / 'target' / 'nested' / 'resumed_game.pkg'
        self.assertTrue(dest_file.exists())
        self.assertEqual(dest_file.read_bytes(), PAYLOAD)
        self.assertFalse(part_file.exists())

    def test_multipart_manager_auto_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                m.configure(self.cfg)
                r = m.add_jobs({
                    'items': [
                        {'source': self.url('/multipart-2'), 'name': 'Spiderman.pkg.002'},
                        {'source': self.url('/multipart-1'), 'name': 'Spiderman.pkg.001'},
                        {'source': self.url('/multipart-3'), 'name': 'Spiderman.pkg.003'},
                    ]
                })
                self.assertEqual(r['count'], 1)
                self.assertEqual(len(m.jobs), 1)
                job = m.jobs[0]
                self.assertEqual(job['kind'], 'multipart')
                self.assertEqual(job['name'], 'Spiderman.pkg')
                self.assertEqual(len(job['parts']), 3)
                self.assertEqual([p['name'] for p in job['parts']], [
                    'Spiderman.pkg.001', 'Spiderman.pkg.002', 'Spiderman.pkg.003'
                ])
            finally:
                m.stop()

    def test_validate_multipart_source(self):
        urls = [self.url('/multipart-1'), self.url('/multipart-2'), self.url('/multipart-3')]
        parts = [
            {'source': urls[0], 'kind': 'url', 'name': 'game.pkg.001'},
            {'source': urls[1], 'kind': 'url', 'name': 'game.pkg.002'},
            {'source': urls[2], 'kind': 'url', 'name': 'game.pkg.003'},
        ]
        info = validate_multipart_source(parts, StopToken())
        self.assertTrue(info['valid'])
        self.assertEqual(info['parts_count'], 3)
        self.assertEqual(info['size'], len(PAYLOAD))
        self.assertEqual(info['filename'], 'game.pkg')
        self.assertTrue(info['ranges'])
        self.assertTrue(info['resumable'])

    def test_find_unar_tool(self):
        tool = find_unar_tool()
        self.assertIsNotNone(tool, "unar executable must be discoverable")
        self.assertTrue(Path(tool).is_file())

    def test_find_extracted_payload(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            # Empty error
            with self.assertRaises(TransferError):
                find_extracted_payload(d)

            # Single package
            pkg_p = Path(d) / "Game.pkg"
            pkg_p.write_bytes(b"PKG")
            pt, pp, pn = find_extracted_payload(d)
            self.assertEqual(pt, "file")
            self.assertEqual(pn, "Game.pkg")
            pkg_p.unlink()

            # Game folder with eboot.bin
            game_dir = Path(d) / "Balatro 01.000"
            game_dir.mkdir()
            (game_dir / "eboot.bin").write_bytes(b"ELF")
            pt, pp, pn = find_extracted_payload(d)
            self.assertEqual(pt, "folder")
            self.assertEqual(pn, "Balatro 01.000")

    def test_cleanup_stale_staging_directories(self):
        import os, time, shutil
        tmp_base = tempfile.gettempdir()
        stale_dir = Path(tmp_base) / "ps5_staged_test_stale"
        fresh_dir = Path(tmp_base) / "ps5_staged_test_fresh"
        stale_dir.mkdir(exist_ok=True)
        fresh_dir.mkdir(exist_ok=True)
        try:
            # Backdate stale_dir by 2 hours
            past = time.time() - 7200
            os.utime(stale_dir, (past, past))
            cleanup_stale_staging_directories()
            self.assertFalse(stale_dir.exists(), "Stale staging dir should be cleaned up")
            self.assertTrue(fresh_dir.exists(), "Fresh staging dir should remain")
        finally:
            shutil.rmtree(stale_dir, ignore_errors=True)
            shutil.rmtree(fresh_dir, ignore_errors=True)

    def test_transfer_staged_archive_encrypted(self):
        test_archive = Path("/tmp/balatro_test.rar")
        if not test_archive.is_file():
            self.skipTest("Test archive /tmp/balatro_test.rar not available")

        job = {
            "id": "staged_test_1",
            "name": "[DLPSGAME.COM]-PPSA21402.rar",
            "source": str(test_archive),
            "kind": "local",
            "folder": "/data/ShadowMount",
            "decompress": True,
            "archive_password": "DLPSGAME.COM",
            "completed_files": []
        }
        reports = []
        def report(k, v):
            reports.append((k, v))
        def save():
            pass

        tok = StopToken()
        transfer_staged_archive(job, self.cfg, tok, report, save)

        dest_game = self.root / "data" / "ShadowMount" / "Balatro 01.015.000 PPSA21402"
        self.assertTrue(dest_game.is_dir(), "Game folder should exist on FTP server")
        self.assertTrue((dest_game / "eboot.bin").is_file(), "eboot.bin should exist")
        self.assertTrue((dest_game / "sce_sys" / "param.json").is_file(), "param.json should exist")
        self.assertGreater(len(list(dest_game.iterdir())), 5)

    def test_transfer_staged_archive_url_zip(self):
        if not find_unar_tool():
            self.skipTest("unar binary not found")

        # Create a small valid zip archive in memory with game structure
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("BalatroTestGame/eboot.bin", b"ELF_HEADER_DUMMY_EBOOT_CONTENT" * 100)
            zf.writestr("BalatroTestGame/sce_sys/param.json", b'{"titleId": "CUSA12345"}')
            zf.writestr("BalatroTestGame/data/game.dat", b"GAME_ASSET_DATA" * 50)
        zip_bytes = buf.getvalue()

        HTTPFixture.custom_files = getattr(HTTPFixture, "custom_files", {})
        HTTPFixture.custom_files["/balatro_game.zip"] = zip_bytes

        job = {
            "id": "staged_url_test",
            "name": "balatro_game.zip",
            "source": self.url("/balatro_game.zip"),
            "kind": "url",
            "folder": "/data/ShadowMount",
            "decompress": True,
            "staged_extraction": True,
            "completed_files": []
        }
        reports = []
        def report(k, v):
            reports.append((k, v))
        def save():
            pass

        tok = StopToken()
        transfer_staged_archive(job, self.cfg, tok, report, save)

        dest_game = self.root / "data" / "ShadowMount" / "BalatroTestGame"
        self.assertTrue(dest_game.is_dir(), "Game folder should exist on FTP server")
        self.assertTrue((dest_game / "eboot.bin").is_file(), "eboot.bin should exist on FTP server")
        self.assertTrue((dest_game / "sce_sys" / "param.json").is_file(), "param.json should exist on FTP server")
        self.assertTrue((dest_game / "data" / "game.dat").is_file(), "game.dat should exist on FTP server")

    def test_transfer_staged_archive_multipart(self):
        if not find_unar_tool():
            self.skipTest("unar binary not found")

        # Create split zip archive
        split_work = tempfile.mkdtemp(prefix="split_test_work_")
        try:
            game_dir = os.path.join(split_work, "SplitPS5Game")
            os.makedirs(os.path.join(game_dir, "sce_sys"), exist_ok=True)
            with open(os.path.join(game_dir, "eboot.bin"), "wb") as f:
                f.write(os.urandom(140000))
            with open(os.path.join(game_dir, "sce_sys", "param.json"), "wb") as f:
                f.write(b'{"titleId": "CUSA99999"}')

            import subprocess
            subprocess.check_call(["zip", "-q", "-s", "64k", "-r", "game.zip", "SplitPS5Game"], cwd=split_work)

            parts = []
            for fname in sorted(os.listdir(split_work)):
                if fname.startswith("game."):
                    parts.append({
                        "source": os.path.join(split_work, fname),
                        "name": fname,
                        "kind": "local"
                    })

            self.assertGreaterEqual(len(parts), 2, "Should create at least 2 split parts")

            job = {
                "id": "staged_multipart_job",
                "name": "game.zip",
                "source": f"{len(parts)} parts: game.zip",
                "kind": "multipart",
                "parts": parts,
                "folder": "/data/ShadowMount",
                "decompress": True,
                "staged_extraction": True,
                "completed_files": []
            }
            reports = []
            def report(k, v): reports.append((k, v))
            def save(): pass

            tok = StopToken()
            transfer_staged_archive(job, self.cfg, tok, report, save)

            dest_game = self.root / "data" / "ShadowMount" / "SplitPS5Game"
            self.assertTrue(dest_game.is_dir(), "Game folder should exist on FTP server")
            self.assertTrue((dest_game / "eboot.bin").is_file(), "eboot.bin should exist on FTP server")
            self.assertTrue((dest_game / "sce_sys" / "param.json").is_file(), "param.json should exist on FTP server")
        finally:
            shutil.rmtree(split_work, ignore_errors=True)

    def test_set_password_action(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                m.add_jobs({"items": [{"source": "http://127.0.0.1:9/game.rar"}]})
                job = m.jobs[0]
                job["state"] = "failed"
                job["detail"] = "Archive extraction failed: Password required"

                # Set password
                m.action("set_password", job["id"], {"password": "secret_password"})
                self.assertEqual(job["archive_password"], "secret_password")
                self.assertTrue(job["archive_encrypted"])
                self.assertTrue(job["staged_extraction"])
                self.assertTrue(job["decompress"])
                self.assertEqual(job["state"], "queued")
                self.assertIn("Password configured", job["detail"])

                # Clear password
                m.action("set_password", job["id"], {"password": ""})
                self.assertIsNone(job["archive_password"])
                self.assertEqual(job["detail"], "Password cleared")
            finally:
                m.stop()

    def test_report_completion_progress_sync(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                m.add_jobs({"items": [{"source": "http://127.0.0.1:9/game.pkg"}]})
                job = m.jobs[0]
                job["total"] = 400000000
                job["transferred"] = 200000000

                # Test progress event updates job transferred and total
                m._report(job, "progress", {
                    "upload_bps": 5000000,
                    "download_bps": 0,
                    "transferred": 250000000,
                    "total": 400000000
                })
                self.assertEqual(job["transferred"], 250000000)

                # Test complete event ensures job transferred equals total
                m._report(job, "complete", {
                    "size": 400000000,
                    "verification": "PS5 file size verified"
                })
                self.assertEqual(job["state"], "completed")
                self.assertEqual(job["transferred"], 400000000)
                self.assertEqual(job["transferred"], job["total"])
            finally:
                m.stop()

    def test_error_info_capture_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                m.configure(self.cfg)
                # Add a job that points to a non-existent port to force immediate failure
                m.add_jobs({"items": [{"source": "http://127.0.0.1:1/failing_test.pkg"}]})
                job = m.jobs[0]
                m.action("start")
                deadline = time.monotonic() + 5
                while job["state"] in ("queued", "starting", "running", "retrying") and time.monotonic() < deadline:
                    time.sleep(0.05)

                self.assertEqual(job["state"], "failed")
                self.assertIn("error_info", job)
                err = job["error_info"]
                self.assertIsNotNone(err)
                self.assertIn("traceback", err)
                self.assertIn("summary", err)
                self.assertIn("type", err)
                self.assertIn("platform", err)
                self.assertIn("python", err)
                self.assertIn("app_version", err)

                # Now report completion to verify error_info is cleaned up
                m._report(job, "complete", {
                    "size": 1000,
                    "verification": "Verified"
                })
                self.assertNotIn("error_info", job)
            finally:
                m.stop()

    def test_disk_space_check_informative_error(self):
        job = {
            "id": "disk_space_test",
            "name": "huge_game.rar",
            "source": self.url("/balatro_game.zip"),
            "kind": "url",
            "folder": "/data/ShadowMount",
            "decompress": True,
            "staged_extraction": True,
            "completed_files": []
        }
        reports = []
        def report(k, v):
            reports.append((k, v))
        def save():
            pass

        tok = StopToken()
        # Mock disk usage to return only 1 MB free space
        with unittest.mock.patch("shutil.disk_usage", return_value=unittest.mock.Mock(free=1024 * 1024)):
            with self.assertRaises(TransferError) as ctx:
                transfer_staged_archive(job, self.cfg, tok, report, save)
            self.assertIn("Insufficient Mac storage for local extraction", str(ctx.exception))
            self.assertIn("0 GB Mac disk usage", str(ctx.exception))
            self.assertIn("Disable extraction", str(ctx.exception))

    def test_toggle_extract_bypasses_staging(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                m.add_jobs({"items": [{"source": "http://127.0.0.1:9/game.rar"}]})
                job = m.jobs[0]
                job["staged_extraction"] = True
                job["decompress"] = True
                job["state"] = "failed"
                job["error_info"] = {"summary": "Disk full"}

                # Toggle extraction off
                m.action("toggle_extract", job["id"])
                self.assertFalse(job["decompress"])
                self.assertFalse(job["staged_extraction"])
                self.assertEqual(job["state"], "queued")
                self.assertIsNone(job["error_info"])
                self.assertIn("0 GB Mac disk space", job["detail"])

                # Toggle extraction back on
                m.action("toggle_extract", job["id"])
                self.assertTrue(job["decompress"])
                self.assertTrue(job["staged_extraction"])
            finally:
                m.stop()

    def test_custom_staging_dir_setting(self):
        with tempfile.TemporaryDirectory() as custom_dir:
            from ps5_streamer import validated_settings
            # Valid directory
            cfg = validated_settings({"staging_dir": custom_dir})
            self.assertEqual(cfg["staging_dir"], os.path.realpath(custom_dir))

            # Non-existent directory
            with self.assertRaises(TransferError):
                validated_settings({"staging_dir": "/non/existent/path/for/staging"})

    def test_generate_unrar_config(self):
        cfg = generate_unrar_config(
            filename="game.rar",
            archive_location="/data/unrar",
            extract_location="/data/ShadowMount",
            password="secret_password",
            delete_after=True,
            progress=10,
            threads=0,
            nice=-20,
            cpu_mask=0
        )
        self.assertIn("filename=game.rar", cfg)
        self.assertIn("archive_location=/data/unrar", cfg)
        self.assertIn("extract_location=/data/ShadowMount", cfg)
        self.assertIn("archive_password=secret_password", cfg)
        self.assertIn("delete_after=1", cfg)
        self.assertIn("progress=10", cfg)

        # Test delete_after=False
        cfg_keep = generate_unrar_config(
            filename="game.7z",
            archive_location="/data/unrar",
            extract_location="/data/pkg",
            password="",
            delete_after=False
        )
        self.assertIn("filename=game.7z", cfg_keep)
        self.assertIn("extract_location=/data/pkg", cfg_keep)
        self.assertIn("delete_after=0", cfg_keep)
        self.assertIn("archive_password=\n", cfg_keep)

        # Test default extract_location is /data/homebrew
        cfg_default = generate_unrar_config(filename="homebrew.rar")
        self.assertIn("extract_location=/data/homebrew", cfg_default)

    def test_send_ps5_payload(self):
        import socket
        received_bytes = bytearray()
        server_ready = threading.Event()
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        def listener():
            server_ready.set()
            try:
                conn, _ = server_sock.accept()
                with conn:
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        received_bytes.extend(chunk)
            finally:
                server_sock.close()

        t = threading.Thread(target=listener, daemon=True)
        t.start()
        server_ready.wait(timeout=2.0)

        dummy_elf = b"\x7fELF" + b"TEST_PAYLOAD_CONTENT" * 100
        send_ps5_payload("127.0.0.1", port=port, elf_bytes=dummy_elf, timeout=3.0)
        t.join(timeout=2.0)
        self.assertEqual(bytes(received_bytes), dummy_elf)

    def test_transfer_ps5_remote_archive(self):
        # Setup local dummy archive
        with tempfile.TemporaryDirectory() as d:
            arch_path = Path(d) / "test_game.rar"
            arch_path.write_bytes(b"RAR_DUMMY_CONTENT_" * 1024)

            job = {
                "id": "test_ps5_unrar_job_1",
                "name": "test_game.rar",
                "source": str(arch_path),
                "kind": "local",
                "folder": "/data/ShadowMount",
                "archive_password": "test_pass",
                "unrar_extract_location": "/data/ShadowMount",
                "unrar_delete_after": True,
                "unrar_auto_payload": False,
                "transferred": 0,
                "total": None
            }
            tok = StopToken()
            statuses = []
            def report(ev, val):
                if ev == "status":
                    statuses.append(val)
            def save():
                pass

            transfer_ps5_remote_archive(job, self.cfg, tok, report, save)

            # Check that archive and config.ini are in FTP root under /data/unrar
            target_file = self.root / "data" / "unrar" / "test_game.rar"
            target_cfg = self.root / "data" / "unrar" / "config.ini"

            self.assertTrue(target_file.exists())
            self.assertEqual(target_file.read_bytes(), arch_path.read_bytes())
            self.assertTrue(target_cfg.exists())
            cfg_content = target_cfg.read_text(encoding="utf-8")
            self.assertIn("filename=test_game.rar", cfg_content)
            self.assertIn("extract_location=/data/ShadowMount", cfg_content)
            self.assertIn("archive_password=test_pass", cfg_content)
            self.assertIn("delete_after=1", cfg_content)

    def test_transfer_ps5_remote_archive_replaces_stale_staging_file(self):
        with tempfile.TemporaryDirectory() as d:
            arch_path = Path(d) / "test_stale.rar"
            arch_path.write_bytes(b"FRESH_RAR_PAYLOAD_" * 512)

            unrar_dir = self.root / "data" / "unrar"
            unrar_dir.mkdir(parents=True, exist_ok=True)
            stale_file = unrar_dir / "test_stale.rar"
            stale_file.write_bytes(b"STALE_INCOMPLETE_BYTES")

            stale_part = unrar_dir / "test_stale.rar.test_job_stale_id.ps5part"
            stale_part.write_bytes(b"OLD_PARTIAL_DATA")

            job = {
                "id": "test_job_stale_id",
                "name": "test_stale.rar",
                "source": str(arch_path),
                "kind": "local",
                "folder": "/data/ShadowMount",
                "unrar_extract_location": "/data/ShadowMount",
                "unrar_delete_after": True,
                "unrar_auto_payload": False,
                "overwrite": False,
                "transferred": 0,
                "total": None
            }
            tok = StopToken()
            statuses = []
            transfer_ps5_remote_archive(job, self.cfg, tok, lambda ev, val: statuses.append(val) if ev == "status" else None, lambda: None)

            self.assertTrue(stale_file.exists())
            self.assertEqual(stale_file.read_bytes(), arch_path.read_bytes())
            self.assertFalse(stale_part.exists())

    def test_transfer_ps5_remote_archive_multipart_disambiguation(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "p1.bin"
            p1.write_bytes(b"PART_1_DATA_" * 128)
            p2 = Path(d) / "p2.bin"
            p2.write_bytes(b"PART_2_DATA_" * 128)

            unrar_dir = self.root / "data" / "unrar"
            unrar_dir.mkdir(parents=True, exist_ok=True)

            job = {
                "id": "test_job_multi_disam",
                "name": "archive.rar",
                "parts": [
                    {"source": str(p1), "kind": "local", "name": "duplicate.rar"},
                    {"source": str(p2), "kind": "local", "name": "duplicate.rar"},
                ],
                "kind": "multipart",
                "folder": "/data/ShadowMount",
                "unrar_extract_location": "/data/ShadowMount",
                "unrar_delete_after": True,
                "unrar_auto_payload": False,
                "overwrite": False,
                "transferred": 0,
                "total": None
            }
            tok = StopToken()
            transfer_ps5_remote_archive(job, self.cfg, tok, lambda ev, val: None, lambda: None)

            dup1 = unrar_dir / "duplicate.rar"
            dup2 = unrar_dir / "duplicate.part2.rar"
            self.assertTrue(dup1.exists())
            self.assertEqual(dup1.read_bytes(), p1.read_bytes())
            self.assertTrue(dup2.exists())
            self.assertEqual(dup2.read_bytes(), p2.read_bytes())

    def test_ps5_unrar_settings_and_actions(self):
        with tempfile.TemporaryDirectory() as d:
            m = Manager(d)
            try:
                # Test validated settings
                cfg = validated_settings({
                    "extract_mode": "ps5",
                    "payload_port": 9021,
                    "unrar_extract_location": "/data/ShadowMount",
                    "unrar_delete_after": True,
                    "unrar_auto_payload": True
                })
                self.assertEqual(cfg["extract_mode"], "ps5")
                self.assertEqual(cfg["payload_port"], 9021)
                self.assertEqual(cfg["unrar_extract_location"], "/data/ShadowMount")
                self.assertTrue(cfg["unrar_delete_after"])
                self.assertTrue(cfg["unrar_auto_payload"])

                # Test invalid payload_port
                with self.assertRaises(TransferError):
                    validated_settings({"payload_port": 999999})

                # Test set_extract_mode queue action
                m.add_jobs({"items": [{"source": "http://127.0.0.1:9/game.rar"}]})
                job = m.jobs[0]

                m.action("set_extract_mode", job["id"], extra={"extract_mode": "ps5"})
                self.assertEqual(job["extract_mode"], "ps5")
                self.assertTrue(job["decompress"])
                self.assertFalse(job["staged_extraction"])
                self.assertIn("Extract on PS5", job["detail"])

                m.action("set_extract_mode", job["id"], extra={"extract_mode": "none"})
                self.assertEqual(job["extract_mode"], "none")
                self.assertFalse(job["decompress"])
                self.assertIn("Extraction disabled", job["detail"])

                m.action("set_extract_mode", job["id"], extra={"extract_mode": "mac"})
                self.assertEqual(job["extract_mode"], "mac")
                self.assertTrue(job["decompress"])
                self.assertTrue(job["staged_extraction"])
                self.assertIn("Extract on Mac", job["detail"])
            finally:
                m.stop()

    def test_size_uint64_sentinel_means_missing(self):
        from transfer_core import remote_size
        class FakeFTP:
            def __init__(self, size_val):
                self._size_val = size_val
            def size(self, path):
                return self._size_val

        # UINT64_MAX: 18446744073709551615 (returned by some PS5 FTP servers for missing files)
        self.assertIsNone(remote_size(FakeFTP(18446744073709551615), "nope.bin"))
        self.assertIsNone(remote_size(FakeFTP(1 << 63), "nope.bin"))
        # Normal size returns integer
        self.assertEqual(remote_size(FakeFTP(12345), "real.bin"), 12345)
        self.assertEqual(remote_size(FakeFTP(0), "empty.bin"), 0)

if __name__=='__main__':unittest.main()


