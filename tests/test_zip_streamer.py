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
import tarfile
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
    ArchiveStreamingReader,
    get_zip_payload_offset,
    inspect_zip_archive,
    inspect_archive,
    is_zip_candidate,
    is_archive_candidate,
    get_archive_type,
    escape_bsdtar_pattern,
    select_best_member,
    EncryptedArchiveError,
    extract_password_hint,
    detect_archive_encryption,
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

        # Prioritize native PS5 .ffpfsc or .exfat over generic files
        zip_ps5_path = os.path.join(self.test_dir, "ps5_game.zip")
        with zipfile.ZipFile(zip_ps5_path, "w") as zf:
            zf.writestr("instructions.txt", b"instructions")
            zf.writestr("Demon_Souls_PPSA01411.ffpfsc", RAW_PKG_CONTENT)
            best = select_best_member(zf.infolist())
            self.assertIsNotNone(best)
            self.assertEqual(best.filename, "Demon_Souls_PPSA01411.ffpfsc")

        # Fallback to largest file when no primary game format present
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

    def test_is_archive_candidate(self):
        self.assertTrue(is_archive_candidate("game.zip"))
        self.assertTrue(is_archive_candidate("game.zip64"))
        self.assertTrue(is_archive_candidate("package.rar"))
        self.assertTrue(is_archive_candidate("archive.7z"))
        self.assertTrue(is_archive_candidate("data.tar"))
        self.assertTrue(is_archive_candidate("data.tar.gz"))
        self.assertTrue(is_archive_candidate("https://example.com/file.rar?download=true"))
        self.assertFalse(is_archive_candidate("game.pkg"))
        self.assertFalse(is_archive_candidate("disk.ffpfsc"))
        self.assertFalse(is_archive_candidate("image.iso"))

    def test_get_archive_type(self):
        self.assertEqual(get_archive_type("game.zip"), "zip")
        self.assertEqual(get_archive_type("game.rar"), "rar")
        self.assertEqual(get_archive_type("game.7z"), "7z")
        self.assertEqual(get_archive_type("game.tar.gz"), "tar")

    def test_escape_bsdtar_pattern(self):
        esc = escape_bsdtar_pattern("Game [01007EF00011E000][v0].nsp")
        self.assertEqual(esc, r"Game \[01007EF00011E000\]\[v0\].nsp")
        self.assertEqual(escape_bsdtar_pattern("normal_game.pkg"), "normal_game.pkg")
        self.assertEqual(escape_bsdtar_pattern("wild*card?.bin"), r"wild\*card\?.bin")

    def test_tar_archive_extraction_pipeline(self):
        # Create a real .tar archive containing a .pkg file
        tar_path = os.path.join(self.test_dir, "test_game_archive.tar")
        with tarfile.open(tar_path, "w") as tf:
            ti = tarfile.TarInfo(name="UP0001-CUSA77777_00.pkg")
            ti.size = len(RAW_PKG_CONTENT)
            tf.addfile(ti, io.BytesIO(RAW_PKG_CONTENT))

        token = StopToken()
        src = probe_source("local", tar_path, token, decompress=True)
        self.assertEqual(src.kind, "archive_local")
        self.assertEqual(src.filename, "UP0001-CUSA77777_00.pkg")
        self.assertEqual(src.size, len(RAW_PKG_CONTENT))

        job = {
            "id": "job_tar_1",
            "kind": "local",
            "source": tar_path,
            "name": "auto",
            "overwrite": True,
            "decompress": True,
        }
        transfer(job, self.settings, token, lambda e, d: None, lambda: None)

        dest_file = os.path.join(self.ftp_root, "downloads", "UP0001-CUSA77777_00.pkg")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "rb") as f:
            content = f.read()
        self.assertEqual(content, RAW_PKG_CONTENT)

    def test_rar_probe_and_extraction(self):
        rar_path = "/Users/kistapas/Downloads/The Legend of Zelda Breath of the Wild Switch NSP Base Game.rar"
        if not os.path.isfile(rar_path):
            return  # Skip if file was moved or deleted

        token = StopToken()
        # Probe with extraction enabled
        src = probe_source("local", rar_path, token, decompress=True)
        self.assertEqual(src.kind, "archive_local")
        self.assertEqual(src.filename, "The Legend of Zelda Breath of the Wild [01007EF00011E000][v0].nsp")
        self.assertEqual(src.size, 14476288345)
        self.assertEqual(src.archive_info["archive_type"], "rar")

        # Probe with extraction disabled (raw file)
        raw_src = probe_source("local", rar_path, token, decompress=False)
        self.assertEqual(raw_src.kind, "local")
        self.assertTrue(raw_src.filename.endswith(".rar"))

    def test_rar_streaming_transfer_ftp(self):
        rar_path = "/Users/kistapas/Downloads/law-and-order-special-victims-unit-svu-third-season_english-412155.rar"
        if not os.path.isfile(rar_path):
            return

        token = StopToken()
        job = {
            "id": "job_rar_test_1",
            "kind": "local",
            "source": rar_path,
            "name": "auto",
            "overwrite": True,
            "decompress": True,
        }
        transfer(job, self.settings, token, lambda e, d: None, lambda: None)

        dest_file = os.path.join(self.ftp_root, "downloads", "Law & Order SVU 0304 Rooftop.srt")
        self.assertTrue(os.path.isfile(dest_file))
        with open(dest_file, "rb") as f:
            content = f.read()
        self.assertEqual(len(content), 74281)
        self.assertTrue(content.startswith(b"1\r\n"))

    def test_extract_password_hint(self):
        self.assertEqual(extract_password_hint("[DLPSGAME.COM]-PPSA21402.rar"), "DLPSGAME.COM")
        self.assertEqual(extract_password_hint("[ROMSFUN.COM]-GrandTheftAuto.zip"), "ROMSFUN.COM")
        self.assertEqual(extract_password_hint("https://example.com/dl?file=%5BDLPSGAME.COM%5D-Game.rar"), "DLPSGAME.COM")
        self.assertEqual(extract_password_hint("Game_pass:12345.rar"), "12345")
        self.assertIsNone(extract_password_hint("PPSA01234.pkg"))

    def test_detect_archive_encryption(self):
        # Stderr detection
        self.assertTrue(detect_archive_encryption(b"", "bsdtar: Encryption is not supported"))
        self.assertTrue(detect_archive_encryption(b"", "ERROR: Cannot open encrypted archive. Wrong password?"))
        self.assertFalse(detect_archive_encryption(b"", ""))

        # RAR5 Header type 4 (HEAD_ENCRYPT)
        rar5_enc = b"Rar!\x1a\x07\x01\x00\x54\x95\x3e\x95\x21\x04\x00\x00\x01\x0f"
        self.assertTrue(detect_archive_encryption(rar5_enc))

        # Plain RAR5
        rar5_plain = b"Rar!\x1a\x07\x01\x00\x54\x95\x3e\x95\x21\x01\x00\x00\x01\x0f"
        self.assertFalse(detect_archive_encryption(rar5_plain))

    def test_encrypted_archive_probe_and_validation(self):
        rar_path = "/tmp/full_test.rar"
        if not os.path.isfile(rar_path):
            return

        token = StopToken()
        # Direct inspect_archive should raise EncryptedArchiveError
        with self.assertRaises(EncryptedArchiveError) as ctx:
            inspect_archive("local", rar_path)
        self.assertIn("password-protected or encrypted", str(ctx.exception))

        # probe_source with decompress=True MUST raise TransferError, NEVER silently fall back to raw file
        with self.assertRaises(TransferError) as ctx_probe:
            probe_source("local", rar_path, token, decompress=True)
        self.assertIn("password-protected or encrypted", str(ctx_probe.exception))

        # probe_source with decompress=False should cleanly return raw file
        raw_src = probe_source("local", rar_path, token, decompress=False)
        self.assertEqual(raw_src.kind, "local")
        self.assertTrue(raw_src.filename.endswith(".rar"))

    def test_get_archive_type_rar_and_hints(self):
        self.assertEqual(get_archive_type("game.rar"), "rar")
        self.assertEqual(get_archive_type("http://example.com/download/Game%20[DLPSGAME.COM].part1.rar"), "rar")
        self.assertEqual(get_archive_type("Game.7z"), "7z")
        self.assertEqual(get_archive_type("Game.zip"), "zip")
        self.assertEqual(extract_password_hint("Game [DLPSGAME.COM].rar"), "DLPSGAME.COM")

    def test_encrypted_archive_error_options(self):
        err = EncryptedArchiveError(
            "This RAR archive is password-protected or encrypted (password: DLPSGAME.COM required). "
            "On-the-fly streaming cannot decompress encrypted archives without unpacking. "
            "Choose 'PS5 on-console extraction' or 'Local staging extraction' with password DLPSGAME.COM, "
            "or transfer the raw archive directly.",
            archive_type="rar",
            password_hint="DLPSGAME.COM"
        )
        self.assertIn("password-protected or encrypted", str(err))
        self.assertEqual(err.password_hint, "DLPSGAME.COM")


if __name__ == "__main__":
    unittest.main()
