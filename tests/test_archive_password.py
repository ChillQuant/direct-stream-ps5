"""Regression tests for archive password handling and extraction failure reporting.

A password inferred from a file name is a guess, not a fact. Applying one to an archive
that has none makes unrar fail with "wrong password", and the raw failure text
("code=11, reason=bad") named neither the cause nor the fix. Worse, the dashboard decides
whether to offer its password dialog by matching that text, so an unreadable message also
withheld the only control that could resolve the problem.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ps5_streamer import Manager  # noqa: E402
from transfer_core import TransferError, describe_unrar_failure  # noqa: E402

# The pattern web/workflow.js uses to decide whether to offer the password dialog.
PASSWORD_PROMPT = re.compile(
    r"archive.*password|password.*archive|encrypted|decrypt|wrong password", re.I)


class UnrarFailureMessageTests(unittest.TestCase):
    def test_bad_password_is_named_and_actionable(self):
        msg = describe_unrar_failure(11, "bad").lower()
        self.assertIn("password", msg)
        self.assertIn("only the extraction is retried", msg,
                      "the message should say a retry costs no upload")

    def test_bad_password_message_offers_the_password_dialog(self):
        self.assertTrue(PASSWORD_PROMPT.search(describe_unrar_failure(11, "bad")),
                        "a wrong-password failure would not offer the password dialog")

    def test_damaged_archive_does_not_offer_a_password(self):
        msg = describe_unrar_failure(3, "bad")
        self.assertIn("checksum", msg.lower())
        self.assertFalse(PASSWORD_PROMPT.search(msg),
                         "a damaged archive must not be reported as a password problem")

    def test_other_known_codes_are_explained(self):
        self.assertIn("write", describe_unrar_failure(5, "bad").lower())
        self.assertIn("memory", describe_unrar_failure(8, "bad").lower())

    def test_unknown_code_keeps_the_raw_detail(self):
        msg = describe_unrar_failure(99, "weird")
        self.assertIn("99", msg)
        self.assertIn("weird", msg)

    def test_non_numeric_code_does_not_raise(self):
        self.assertIsInstance(describe_unrar_failure("x", ""), str)


class GuessedPasswordTests(unittest.TestCase):
    def _add(self, manager, name, **extra):
        payload = {"kind": "url", "folder": "/data/homebrew",
                   "items": [{"source": "http://example.com/" + name, "name": name}],
                   "decompress": True, "extract_mode": "ps5"}
        payload.update(extra)
        manager.add_jobs(payload)
        return manager.jobs[-1]

    def test_name_derived_password_is_flagged_as_a_guess(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            try:
                job = self._add(manager, "[SITE.COM]-GAME.rar")
                self.assertEqual(job["archive_password"], "SITE.COM")
                self.assertTrue(job["archive_password_guessed"])
            finally:
                manager.stop()

    def test_user_supplied_password_is_not_a_guess(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            try:
                job = self._add(manager, "[SITE.COM]-GAME.rar", archive_password="chosen")
                self.assertEqual(job["archive_password"], "chosen")
                self.assertFalse(job["archive_password_guessed"])
            finally:
                manager.stop()

    def test_password_can_be_cleared_and_the_job_retries(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            try:
                job = self._add(manager, "[SITE.COM]-GAME.rar")
                job["state"] = "failed"
                manager.action("set_password", job["id"], {"password": ""})
                updated = manager.jobs[-1]
                self.assertIsNone(updated["archive_password"],
                                  "an empty password must clear the guessed one")
                self.assertFalse(updated["archive_password_guessed"])
                self.assertEqual(updated["state"], "queued",
                                 "clearing a password is a fix, so the job must retry")
            finally:
                manager.stop()

    def test_recovery_dialog_accepts_an_empty_password(self):
        html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(
            encoding="utf-8", errors="replace")
        idx = html.index('id="recovery-password-input"')
        tag = html[idx:html.index(">", idx)]
        self.assertNotIn("required", tag,
                         "the dialog still refuses an empty value, so a guessed "
                         "password cannot be cleared")


class DestinationGuardTests(unittest.TestCase):
    """The guard should fire when the console moves, not when an unrelated global
    default changes under a job that targets its own folder."""

    def _destination(self, job, cfg):
        dest = {k: cfg[k] for k in ("host", "port", "username")}
        dest["folder"] = job.get("folder") or cfg["folder"]
        return dest

    def test_job_folder_wins_over_the_global_default(self):
        cfg = {"host": "192.168.1.9", "port": 2121, "username": "anonymous",
               "folder": "/data/homebrew"}
        job = {"folder": "/mnt/usb0"}
        self.assertEqual(self._destination(job, cfg)["folder"], "/mnt/usb0")
        self.assertEqual(self._destination(job, cfg),
                         self._destination(job, {**cfg, "folder": "/data/pkg"}))

    def test_job_without_a_folder_follows_the_default(self):
        cfg = {"host": "192.168.1.9", "port": 2121, "username": "anonymous",
               "folder": "/data/homebrew"}
        self.assertEqual(self._destination({}, cfg)["folder"], "/data/homebrew")

    def test_a_different_console_still_trips_the_guard(self):
        job = {"folder": "/mnt/usb0"}
        a = self._destination(job, {"host": "192.168.1.9", "port": 2121,
                                    "username": "anonymous", "folder": "/x"})
        b = self._destination(job, {"host": "10.0.0.5", "port": 2121,
                                    "username": "anonymous", "folder": "/x"})
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
