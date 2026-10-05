"""
Tests for On-The-Fly Streaming Archive Decompression Engine (v2.9.0).
Tests both unit functionality and full HTTP -> RAM -> FTP streaming pipeline.
"""

import hashlib
import http.server
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from transfer_core import (
    BLOCK,
    Meter,
    SourceInfo,
    StopToken,
    TransferError,
    connect_ftp,
    probe_source,
    remote_size,
    transfer,
    validate_source_url,
)
from zip_streamer import (
    ZipStreamingReader,
    get_zip_payload_offset,
    inspect_zip_archive,
    is_zip_candidate,
    select_best_member,
)

RAW_PKG_CONTENT = (b"PS5_PKG_HEADER_MAGIC_TEST_1234567890\n" + b"A" * 1024) * 500  # ~530 KB


class TestZipStreamer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Set up a temporary directory for FTP root and HTTP serving
        cls.test_dir = tempfile.mkdtemp()
        cls.ftp_root = os.path.join(cls.test_dir, "ftp_root")
        os.makedirs(cls.ftp_root, exist_ok=True)

        # Setup Deflated ZIP file
        cls.zip_deflated_path = os.path.join(cls.test_dir, "game_archive.zip")
        with zipfile.ZipFile(cls.zip_deflated_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("readme.txt", b"Instructions for installing.")
            zf.writestr("EP0001-CUSA99999_00.pkg", RAW_PKG_CONTENT)
            zf.writestr("cover.jpg", b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)

        # Setup Stored (Uncompressed) ZIP file
        cls.zip_stored_path = os.path.join(cls.test_dir, "stored_archive.zip")
        with zipfile.ZipFile(cls.zip_stored_path, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("EP0001-CUSA88888_00.pkg", RAW_PKG_CONTENT)

        # Setup Non-PKG ZIP file
        cls.zip_non_pkg_path = os.path.join(cls.test_dir, "data_archive.zip")
        with zipfile.ZipFile(cls.zip_non_pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("small.txt", b"hello")
            zf.writestr("big_payload.bin", RAW_PKG_CONTENT)

        # Setup HTTP server that supports byte ranges
        class RangeHTTPHandler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=cls.test_dir, **kwargs)

            def log_message(self, *args):
                pass

        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHTTPHandler)
        cls.http_port = cls.httpd.server_address[1]
        cls.http_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.http_thread.start()

        # Setup FTP server
        authorizer = DummyAuthorizer()
        authorizer.add_anonymous(cls.ftp_root, perm="elradfmwMT")
        handler = FTPHandler
        handler.authorizer = authorizer
        handler.banner = "pyftpdlib test banner"
        cls.ftpd = FTPServer(("127.0.0.1", 0), handler)
        cls.ftp_port = cls.ftpd.socket.getsockname()[1]
        cls.ftp_thread = threading.Thread(target=cls.ftpd.serve_forever, daemon=True)
        cls.ftp_thread.start()

        cls.settings = {
            "host": "127.0.0.1",
            "port": cls.ftp_port,
            "folder": "/downloads",
            "username": "anonymous",
            "password": "",
            "streams": 4,
            "buffer_mb": 64,
            "chunk_mb": 4,
            "limit_mbps": 0,
            "retries": 1,
        }

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
        except Exception:
            pass
        try:
            cls.ftpd.close_all()
        except Exception:
            pass
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_is_zip_candidate(self):
        self.assertTrue(is_zip_candidate("game.zip"))
        self.assertTrue(is_zip_candidate("GAME.ZIP"))
        self.assertTrue(is_zip_candidate("https://example.com/downloads/game.zip?token=xyz"))
        self.assertTrue(is_zip_candidate("archive.zip64"))
        self.assertFalse(is_zip_candidate("game.pkg"))
        self.assertFalse(is_zip_candidate("https://example.com/file.bin"))

    def test_select_best_member(self):
        # Prioritize .pkg over readme or jpg
        with zipfile.ZipFile(self.zip_deflated_path, "r") as zf:
            best = select_best_member(zf.infolist())
            self.assertIsNotNone(best)
            self.assertEqual(best.filename, "EP0001-CUSA99999_00.pkg")

        # Fallback to largest file when no .pkg present
        with zipfile.ZipFile(self.zip_non_pkg_path, "r") as zf:
            best = select_best_member(zf.infolist())
            self.assertIsNotNone(best)
            self.assertEqual(best.filename, "big_payload.bin")

    def test_probe_local_zip(self):
        token = StopToken()
        src = probe_source("local", self.zip_deflated_path, token)
        self.assertEqual(src.kind, "zip_local")
        self.assertEqual(src.filename, "EP0001-CUSA99999_00.pkg")
        self.assertEqual(src.size, len(RAW_PKG_CONTENT))
        self.assertFalse(src.resumable())
        self.assertIsNotNone(src.archive_info)
        self.assertEqual(src.archive_info["compress_type"], 8)

    def test_probe_remote_http_zip(self):
        token = StopToken()
        url = f"http://127.0.0.1:{self.http_port}/game_archive.zip"
        res = validate_source_url(url, token)
        self.assertTrue(res["valid"])
        self.assertTrue(res["is_zip"])
        self.assertEqual(res["filename"], "EP0001-CUSA99999_00.pkg")
        self.assertEqual(res["size"], len(RAW_PKG_CONTENT))
        self.assertFalse(res["resumable"])

    def test_local_zip_streaming_transfer(self):
        job = {
            "id": "job_zip_local_1",
            "kind": "local",
            "source": self.zip_deflated_path,
            "name": "auto",
            "overwrite": True,
        }
        token = StopToken()
        reports = []

        def report(evt, data):
            reports.append((evt, data))

        transfer(job, self.settings, token, report, lambda: None)

        # Verify the file on FTP
        dest_file = os.path.join(self.ftp_root, "downloads", "EP0001-CUSA99999_00.pkg")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "rb") as f:
            content = f.read()
        self.assertEqual(len(content), len(RAW_PKG_CONTENT))
        self.assertEqual(hashlib.sha256(content).hexdigest(), hashlib.sha256(RAW_PKG_CONTENT).hexdigest())

    def test_remote_http_zip_streaming_transfer(self):
        url = f"http://127.0.0.1:{self.http_port}/game_archive.zip"
        job = {
            "id": "job_zip_remote_1",
            "kind": "url",
            "source": url,
            "name": "download.bin",
            "overwrite": True,
        }
        token = StopToken()
        reports = []

        def report(evt, data):
            reports.append((evt, data))

        transfer(job, self.settings, token, report, lambda: None)

        dest_file = os.path.join(self.ftp_root, "downloads", "EP0001-CUSA99999_00.pkg")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "rb") as f:
            content = f.read()
        self.assertEqual(hashlib.sha256(content).hexdigest(), hashlib.sha256(RAW_PKG_CONTENT).hexdigest())

    def test_remote_http_stored_zip_transfer(self):
        url = f"http://127.0.0.1:{self.http_port}/stored_archive.zip"
        job = {
            "id": "job_zip_stored_1",
            "kind": "url",
            "source": url,
            "name": "auto",
            "overwrite": True,
        }
        token = StopToken()
        transfer(job, self.settings, token, lambda e, d: None, lambda: None)

        dest_file = os.path.join(self.ftp_root, "downloads", "EP0001-CUSA88888_00.pkg")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "rb") as f:
            content = f.read()
        self.assertEqual(content, RAW_PKG_CONTENT)

    def test_decompress_disabled_preserves_raw_zip(self):
        token = StopToken()
        url = f"http://127.0.0.1:{self.http_port}/game_archive.zip"
        # Probe with decompress=False
        src = probe_source("url", url, token, decompress=False)
        self.assertEqual(src.kind, "url")
        self.assertFalse(src.kind.startswith("zip_"))
        self.assertEqual(src.size, os.path.getsize(self.zip_deflated_path))


if __name__ == "__main__":
    unittest.main()
