"""Regression tests for reading the saved queue back off disk.

state.json is written as UTF-8 with ensure_ascii=False, but was read back without an
explicit encoding. On Windows that means cp1252, so a single non-ASCII character — and
the app writes "·" into job details routinely — raised UnicodeDecodeError, the broad
handler in Manager._load reset self.jobs to [], and the entire saved queue silently
disappeared.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ps5_streamer import Manager  # noqa: E402


class StatePersistenceTests(unittest.TestCase):
    def _queue_one(self, manager):
        manager.add_jobs({"kind": "url", "folder": "/data/homebrew",
                          "items": [{"source": "http://example.com/Game.pkg",
                                     "name": "Game.pkg"}],
                          "decompress": False, "extract_mode": "none"})
        return manager.jobs[-1]

    def test_queue_survives_non_ascii_detail_text(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            job = self._queue_one(manager)
            # Exactly the kind of text the app writes itself. A paused job keeps its
            # detail verbatim; queued/active ones are deliberately re-labelled on load.
            job["state"] = "paused"
            job["detail"] = "Complete · PS5 file size verified · 1,2 GB"
            job["name"] = "Jeu — spécial (日本).pkg"
            manager.save()
            manager.stop()

            reopened = Manager(d)
            try:
                self.assertEqual(len(reopened.jobs), 1,
                                 "the saved queue was lost when it was reloaded")
                self.assertIn("·", reopened.jobs[0]["detail"])
                self.assertEqual(reopened.jobs[0]["name"], "Jeu — spécial (日本).pkg")
            finally:
                reopened.stop()

    def test_no_warning_is_logged_for_a_valid_queue(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            job = self._queue_one(manager)
            job["state"] = "paused"
            job["detail"] = "Stopped · partial retained on PS5"
            manager.save()
            manager.stop()

            reopened = Manager(d)
            try:
                warnings = [entry for entry in reopened.logs
                            if entry.get("level") == "warning"
                            and "could not be loaded" in str(entry.get("message", ""))]
                self.assertEqual(warnings, [],
                                 "a readable queue was reported as unloadable")
            finally:
                reopened.stop()

    def test_state_file_is_utf8_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            job = self._queue_one(manager)
            job["detail"] = "Stopped · partial retained"
            manager.save()
            manager.stop()
            raw = (Path(d) / "state.json").read_bytes()
            self.assertIn("·".encode("utf-8"), raw,
                          "state.json is not written as UTF-8")
            # And it must round-trip through an explicit UTF-8 read.
            json.loads((Path(d) / "state.json").read_text(encoding="utf-8"))

    def test_sources_never_read_saved_json_without_an_encoding(self):
        # The write side already pins UTF-8; the read side must not rely on the
        # platform default, which differs between Windows and everything else.
        source = (Path(__file__).resolve().parents[1] / "ps5_streamer.py").read_text(
            encoding="utf-8")
        self.assertNotIn("read_text()", source,
                         "a saved-state reader still uses the platform codepage")


if __name__ == "__main__":
    unittest.main()
