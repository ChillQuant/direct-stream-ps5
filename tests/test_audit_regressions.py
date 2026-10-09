"""Focused regressions for data preservation, integrity, and safety."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import zipfile

from transfer_core import (
    Cancelled,
    Meter,
    StopToken,
    TransferError,
    probe_source,
    generate_unrar_config,
    transfer_staged_archive,
)
from ps5_streamer import redact_diagnostic
from resolver import detect_multipart_sequence
from zip_streamer import ZipStreamingReader, select_best_member


class AuditSafetyTests(unittest.TestCase):
    def test_invalid_multipart_sequences(self):
        # Missing part in between
        self.assertFalse(detect_multipart_sequence([{"name": "game.pkg.001"}, {"name": "game.pkg.003"}])[0])
        # Duplicate part
        self.assertFalse(detect_multipart_sequence([{"name": "game.pkg.001"}, {"name": "game.pkg.001"}])[0])
        # Valid 1-based sequence
        self.assertTrue(detect_multipart_sequence([{"name": "game.pkg.002"}, {"name": "game.pkg.001"}])[0])
        # Valid 0-based sequence
        self.assertTrue(detect_multipart_sequence([{"name": "game.pkg.000"}, {"name": "game.pkg.001"}])[0])

    def test_ini_injection_rejected_and_password_spaces_preserved(self):
        with self.assertRaises(TransferError):
            generate_unrar_config(password="x\ndelete_after=1")
        with self.assertRaises(TransferError):
            generate_unrar_config(filename="../game.rar")
        cfg = generate_unrar_config(password=" password with spaces ")
        self.assertIn("archive_password= password with spaces ", cfg)

    def test_loose_folder_requires_full_extraction(self):
        with self.assertRaises(ValueError):
            select_best_member([
                {"filename": "game/eboot.bin", "uncompressed_size": 100},
                {"filename": "game/param.json", "uncompressed_size": 10}
            ])

    def test_diagnostic_redaction(self):
        text = redact_diagnostic("https://host/path?sig=secret123 secret-password", ["secret-password"])
        self.assertNotIn("secret123", text)
        self.assertNotIn("secret-password", text)
        self.assertIn("<redacted>", text)

    def test_staging_preserves_original_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "game.zip"
            archive.write_bytes(b"original-archive-content")
            job = {
                "id": "test-stage-id",
                "kind": "local",
                "source": str(archive),
                "extract_mode": "mac",
                "staged_extraction": True
            }

            def mock_extract(command, **kwargs):
                output = Path(command[command.index("-o") + 1])
                (output / "game.pkg").write_bytes(b"package")
                proc = Mock(returncode=0)
                proc.communicate.return_value = ("", "")
                return proc

            with patch("transfer_core.find_unar_tool", return_value="unar"), \
                 patch("transfer_core.subprocess.Popen", side_effect=mock_extract), \
                 patch("transfer_core.transfer", side_effect=Cancelled):
                with self.assertRaises(Cancelled):
                    transfer_staged_archive(job, {"folder": "/data/homebrew"}, StopToken(), lambda *a: None, lambda: None)

            # The original local file MUST NOT be deleted
            self.assertTrue(archive.exists())
            self.assertEqual(archive.read_bytes(), b"original-archive-content")

    def test_zip_bounded_chunks_crc_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.zip"
            payload = b"A" * (2 * 1024 * 1024)
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("game.pkg", payload)

            token = StopToken()
            source = probe_source("local", str(path), token)
            reader = ZipStreamingReader(source, 4, token, Meter())
            result = bytearray()
            try:
                while True:
                    block = reader.read()
                    if not block:
                        break
                    self.assertLessEqual(len(block), 1024 * 1024)
                    result.extend(block)
            finally:
                reader.close()
            self.assertEqual(bytes(result), payload)

            # Corrupt CRC
            source.archive_info["crc"] ^= 1
            reader = ZipStreamingReader(source, 4, token, Meter())
            try:
                with self.assertRaisesRegex(ValueError, "CRC"):
                    while reader.read():
                        pass
            finally:
                reader.close()


if __name__ == "__main__":
    unittest.main()
