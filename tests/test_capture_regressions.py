"""Regression tests for browser-assisted capture, link resolution hops and the
dashboard's security headers.

Every test here pins behaviour that was broken at least once in practice, so a
future change that reintroduces the bug fails loudly instead of silently
degrading the app.
"""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_capture import CaptureSession, is_payload_name, site_of  # noqa: E402
from ps5_streamer import Handler, Manager, build_allowed_hosts  # noqa: E402
from transfer_core import (StopToken, TransferError, declared_total_size,  # noqa: E402
                           probe_source)


# ---------------------------------------------------------------------------
# Capture filtering: adverts must never be mistaken for the payload
# ---------------------------------------------------------------------------

class CaptureFilterTests(unittest.TestCase):
    def test_site_of_uses_registrable_domain(self):
        # A CDN subdomain is the SAME site: closing those tabs would kill real downloads.
        self.assertEqual(site_of("https://cdn7.datanodes.to/d/x.rar"), "datanodes.to")
        self.assertEqual(site_of("https://datanodes.to/download"), "datanodes.to")
        # An advert network is a different site.
        self.assertEqual(site_of("https://nh.loyaltymirv.cfd/cx/abc"), "loyaltymirv.cfd")
        self.assertNotEqual(site_of("https://nh.loyaltymirv.cfd/cx/abc"),
                            site_of("https://datanodes.to/download"))
        # about:blank must not look like an off-site navigation.
        self.assertEqual(site_of("about:blank"), "")
        self.assertEqual(site_of(""), "")

    def test_payload_allowlist_rejects_adverts(self):
        # Real advert downloads seen in the wild.
        for name in ("OperaSetup.exe", "f.txt", "setup.msi", "installer.dmg",
                     "app.apk", "notes.pdf", ""):
            self.assertFalse(is_payload_name(name), name)
        # The advert URL that carries NO extension at all -- a blocklist on the
        # URL path cannot catch this, which is why the allowlist exists.
        self.assertFalse(is_payload_name("https://net.geo.opera.com/opera/stable/windows?utm=1"))

    def test_payload_allowlist_accepts_real_payloads(self):
        for name in ("Sample.Game.PPSA00000.pkg", "sample-1.rar", "Game.iso",
                     "Game.7z", "bundle.zip", "disc.ffpfsc", "disc.exfat",
                     "Game.pkg.001", "Game.part1.rar"):
            self.assertTrue(is_payload_name(name), name)
        # Spaces and parentheses, raw and percent-encoded.
        self.assertTrue(is_payload_name("sample-1 (2).rar"))
        self.assertTrue(is_payload_name("sample-1%20(2).rar"))
        self.assertTrue(is_payload_name("https://cdn.x.to/d/sample-1%20(2).rar?token=9"))


class CaptureEventTests(unittest.TestCase):
    """_url_from_event decides what counts as a capture. No browser required."""

    def setUp(self):
        self.session = CaptureSession("https://host.tld/f/abc")

    def _download_event(self, url, suggested=None):
        params = {"url": url}
        if suggested is not None:
            params["suggestedFilename"] = suggested
        return {"method": "Page.downloadWillBegin", "params": params}

    def _response_event(self, url, disposition="", ctype="application/octet-stream"):
        return {"method": "Network.responseReceived",
                "params": {"response": {"url": url, "headers": {
                    "content-disposition": disposition, "content-type": ctype}}}}

    def test_advert_download_is_ignored_and_recorded(self):
        # The advert URL has no extension; only suggestedFilename reveals it.
        event = self._download_event("https://ads.example.com/get?partner=opera",
                                     "OperaSetup.exe")
        self.assertEqual(self.session._url_from_event(event), "")
        self.assertIn("OperaSetup.exe", self.session.skipped)

    def test_tracking_text_file_is_ignored(self):
        self.assertEqual(
            self.session._url_from_event(self._download_event("https://ads.x/f", "f.txt")), "")

    def test_real_download_is_captured(self):
        url = "https://cdn.host.tld/d/sample-1.rar?token=9"
        self.assertEqual(self.session._url_from_event(self._download_event(url, "sample-1.rar")), url)
        self.assertEqual(self.session.skipped, [])

    def test_download_without_suggested_name_falls_back_to_url(self):
        url = "https://cdn.host.tld/d/Game.pkg"
        self.assertEqual(self.session._url_from_event(self._download_event(url)), url)
        # ...but only when the URL itself looks like a payload.
        self.assertEqual(self.session._url_from_event(self._download_event("https://ads.x/go")), "")

    def test_attachment_response_respects_the_allowlist(self):
        good = "https://cdn.host.tld/d/file"
        self.assertEqual(
            self.session._url_from_event(
                self._response_event(good, 'attachment; filename="Game.iso"')), good)
        self.assertEqual(
            self.session._url_from_event(
                self._response_event("https://ads.x/d", 'attachment; filename="OperaSetup.exe"')), "")

    def test_rfc5987_encoded_attachment_filename(self):
        url = "https://cdn.host.tld/d/file"
        self.assertEqual(
            self.session._url_from_event(
                self._response_event(url, "attachment; filename*=UTF-8''Game%20Disc.pkg")), url)


class MainTabGuardTests(unittest.TestCase):
    """The advert script hijacks the main tab; the guard must steer it back."""

    def setUp(self):
        self.session = CaptureSession("https://datanodes.to/abc123")

    def test_same_site_navigation_is_left_alone(self):
        self.session._guard_main_tab("https://cdn7.datanodes.to/d/x.rar", "datanodes.to")
        self.assertIsNone(self.session._offsite_since)
        self.session._guard_main_tab("about:blank", "datanodes.to")
        self.assertIsNone(self.session._offsite_since)

    def test_dead_error_page_starts_the_grace_timer(self):
        # A chrome-error:// dead end is the one case worth recovering from.
        self.session._guard_main_tab("chrome-error://chromewebdata/", "datanodes.to")
        self.assertIsNotNone(self.session._offsite_since)

    def _recover(self, session, url, origin="datanodes.to"):
        """Drive the guard past its grace period and return the CDP calls sent."""
        sent = []

        class FakeWS:
            def send(self, msg):
                sent.append(msg)

        session._main_tid = "t1"
        session._attached["t1"] = FakeWS()
        # The tab settles on the landing page first, as it does in reality.
        session._guard_main_tab(session.page_url, origin)
        session._guard_main_tab(url, origin)          # starts the grace timer
        session._offsite_since = time.time() - 5      # expire it
        session._guard_main_tab(url, origin)          # now recovers
        return sent

    def test_recovery_prefers_history_back_over_reloading(self):
        # Reloading the landing page restarts the host's multi-click download
        # flow, which produced an endless reload loop on AkiraBox. Going back
        # restores the page with its click state intact.
        sent = self._recover(self.session, "https://nh.loyaltymirv.cfd/cx/abc")
        methods = [m.get("method") for m in sent]
        self.assertTrue(
            any(m in ("Page.navigateToHistoryEntry", "Runtime.evaluate") for m in methods),
            "no history.back() attempt: %s" % methods)
        reloads = [m for m in sent
                   if m.get("method") == "Page.navigate"
                   and (m.get("params") or {}).get("url") == self.session.page_url]
        self.assertEqual(reloads, [], "landing page was reloaded on first recovery")

    def test_host_canonical_redirect_is_not_a_hijack(self):
        # akirabox.com redirects to akirabox.to. Treating that as a hijack sent
        # the tab back to .com, which redirected again -- an endless loop.
        session = CaptureSession("https://akirabox.com/k0embkKZW3Vb/file")
        session._guard_main_tab("https://akirabox.com/k0embkKZW3Vb/file", "akirabox.com")
        session._guard_main_tab("https://akirabox.to/k0embkKZW3Vb/file", "akirabox.com")
        self.assertIsNone(session._offsite_since,
                          "the host's own canonical redirect was treated as an advert")

    def test_advert_is_still_a_hijack_after_a_redirect(self):
        session = CaptureSession("https://akirabox.com/k0embkKZW3Vb/file")
        session._guard_main_tab("https://akirabox.to/k0embkKZW3Vb/file", "akirabox.com")
        session._guard_main_tab("https://nh.loyaltymirv.cfd/cx/abc", "akirabox.com")
        self.assertIsNotNone(session._offsite_since)

    def test_dead_error_page_is_also_recovered(self):
        # DataNodes' advert redirect dies on ERR_ADDRESS_INVALID; that tab must
        # not be left stranded.
        sent = self._recover(self.session, "chrome-error://chromewebdata/")
        self.assertTrue(sent, "dead error page was never recovered")

    def test_reload_is_the_last_resort_only(self):
        # If back never takes and the tab stays stuck on the advert page, a
        # reload must eventually rescue it rather than leaving the user stranded.
        sent = []

        class FakeWS:
            def send(self, msg):
                sent.append(msg)

        session = CaptureSession("https://datanodes.to/abc123")
        session._main_tid = "t1"
        session._attached["t1"] = FakeWS()
        session._guard_main_tab(session.page_url, "datanodes.to")   # settle first

        url = "https://nh.loyaltymirv.cfd/cx/abc"
        for _ in range(3):
            # Still stuck on the advert: no on-site observation in between, so
            # the attempt counter keeps climbing.
            session._guard_main_tab(url, "datanodes.to")
            session._offsite_since = time.time() - 5
            session._guard_main_tab(url, "datanodes.to")

        reloaded = any(m.get("method") == "Page.navigate"
                       and (m.get("params") or {}).get("url") == session.page_url
                       for m in sent)
        self.assertTrue(reloaded, "never fell back to a reload after repeated failures")

    def test_returning_on_site_resets_the_attempt_counter(self):
        # A successful recovery must not count towards the reload fallback.
        session = CaptureSession("https://datanodes.to/abc123")
        session._guard_main_tab(session.page_url, "datanodes.to")
        session._recover_attempts = 2
        session._guard_main_tab("https://datanodes.to/download", "datanodes.to")
        self.assertEqual(session._recover_attempts, 0)

    def test_returning_on_site_clears_the_grace_timer(self):
        self.session._guard_main_tab("chrome-error://chromewebdata/", "datanodes.to")
        self.session._guard_main_tab("https://datanodes.to/download", "datanodes.to")
        self.assertIsNone(self.session._offsite_since)

    def test_navigations_are_never_intercepted(self):
        # Fetch-based navigation blocking was tried and reverted: it suppressed
        # download events entirely, breaking the whole point of the capture.
        source = Path(__file__).resolve().parents[1] / "browser_capture.py"
        self.assertNotIn("Fetch.enable", source.read_text(encoding="utf-8"))

    def test_payload_navigation_is_never_bounced(self):
        # A real download may navigate the tab to an off-site CDN file.
        self.session._guard_main_tab("https://cdn.elsewhere.net/d/Game.pkg", "datanodes.to")
        self.assertIsNone(self.session._offsite_since)


class BrowserLaunchTests(unittest.TestCase):
    """The capture window must open normally, not minimised or hidden."""

    @unittest.skipUnless(sys.platform == "win32", "Windows window-state behaviour")
    def test_window_is_shown_normally_on_windows(self):
        import subprocess as sp

        session = CaptureSession("https://host.tld/f/abc")
        with mock.patch("browser_capture.find_browser", return_value="C:\chrome.exe"),              mock.patch("browser_capture.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            session.start()
            session.cancel()

        self.assertTrue(popen.called, "browser was never launched")
        kwargs = popen.call_args.kwargs
        info = kwargs.get("startupinfo")
        self.assertIsNotNone(
            info, "STARTUPINFO missing: the child inherits the parent's hidden/minimised state")
        self.assertTrue(info.dwFlags & sp.STARTF_USESHOWWINDOW)
        self.assertEqual(info.wShowWindow, 1, "wShowWindow must be SW_SHOWNORMAL")
        # CREATE_NO_WINDOW is for console apps and propagated the hidden state.
        self.assertFalse(kwargs.get("creationflags", 0) & getattr(sp, "CREATE_NO_WINDOW", 0))

    @unittest.skipUnless(sys.platform == "win32", "Windows foreground behaviour")
    def test_window_is_raised_to_the_foreground(self):
        # Setting SW_SHOWNORMAL alone is not enough: a background process cannot
        # take the foreground, so the window opened BEHIND the existing ones.
        session = CaptureSession("https://host.tld/f/abc")
        with mock.patch("browser_capture.find_browser", return_value="C:\chrome.exe"),              mock.patch("browser_capture.subprocess.Popen") as popen,              mock.patch("browser_capture.raise_browser_window") as raiser:
            popen.return_value.poll.return_value = None
            popen.return_value.pid = 4321
            session.start()
            for _ in range(50):
                if raiser.called:
                    break
                time.sleep(0.02)
            session.cancel()
        self.assertTrue(raiser.called, "capture window is never raised to the foreground")
        self.assertEqual(raiser.call_args.args[0], 4321)

    def test_launch_uses_a_throwaway_profile_without_extensions(self):
        session = CaptureSession("https://host.tld/f/abc")
        with mock.patch("browser_capture.find_browser", return_value="chrome"),              mock.patch("browser_capture.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            session.start()
            session.cancel()
        argv = popen.call_args.args[0]
        self.assertIn("--disable-extensions", argv)
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in argv))
        # Must start blank: attaching before navigation is what stops the download
        # firing before we are listening.
        self.assertEqual(argv[-1], "about:blank")


# ---------------------------------------------------------------------------
# Resolution hops
# ---------------------------------------------------------------------------

class HopFixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    SIZE = 4096

    def log_message(self, *_):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = self.headers.get("Host")
        path = self.path.split("?")[0]
        if path == "/api":
            body = json.dumps({"status": "ok", "data": {
                "download_url": "http://%s/cdn/Game.pkg?token=1" % host}}).encode()
            return self._send(200, "application/json", body)
        if path == "/cdn/Game.pkg":
            if self.headers.get("Range") == "bytes=0-0":
                return self._send(206, "application/octet-stream", b"X", {
                    "Content-Range": "bytes 0-0/%d" % self.SIZE,
                    "Accept-Ranges": "bytes",
                    "ETag": '"abc"',
                    "Content-Disposition": 'attachment; filename="Game.pkg"'})
            return self._send(200, "application/octet-stream", b"X" * self.SIZE)
        self._send(404, "text/plain", b"nope")


class JsonHopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), HopFixture)
        cls.http.daemon_threads = True
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.http.server_port

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()

    def test_json_api_response_is_followed_to_the_file(self):
        info = probe_source("url", self.base + "/api", StopToken(), decompress=False)
        self.assertEqual(info.location, self.base + "/cdn/Game.pkg?token=1")
        self.assertEqual(info.filename, "Game.pkg")
        self.assertEqual(info.size, HopFixture.SIZE)

    def test_original_url_survives_every_hop(self):
        # Host classification (single-connection, Referer) keys off the ORIGINAL
        # url, so a CDN redirect must not overwrite it.
        info = probe_source("url", self.base + "/api", StopToken(), decompress=False)
        self.assertEqual(info.original_url, self.base + "/api")

    def test_declared_total_size_prefers_content_range(self):
        self.assertEqual(declared_total_size({"Content-Range": "bytes 0-0/4096"}), 4096)
        self.assertEqual(declared_total_size({"Content-Length": "77"}), 77)
        self.assertEqual(declared_total_size({}), 0)
        self.assertEqual(declared_total_size({"Content-Length": "bogus"}), 0)


# ---------------------------------------------------------------------------
# Dashboard security headers and host allowlist
# ---------------------------------------------------------------------------

class DashboardHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server.manager = Manager(cls.tmp.name)
        cls.server.token = "test-session"
        cls.server.allowed_hosts = build_allowed_hosts(cls.server.server_port)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def _get(self, path="/", host=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        req.add_header("Host", host or "127.0.0.1:%d" % self.port)
        return urllib.request.urlopen(req, timeout=10)

    def test_inline_styles_are_permitted(self):
        # The app ships ~54 inline style attributes. Without 'unsafe-inline' the
        # browser drops every one of them and the dashboard renders broken.
        csp = self._get("/").headers.get("Content-Security-Policy", "")
        style = [d for d in csp.split(";") if d.strip().startswith("style-src")][0]
        self.assertIn("'unsafe-inline'", style)

    def test_inline_scripts_stay_forbidden(self):
        # Relaxing style-src must never leak into script-src.
        csp = self._get("/").headers.get("Content-Security-Policy", "")
        script = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
        self.assertNotIn("unsafe-inline", script)
        self.assertNotIn("unsafe-eval", script)

    def test_all_loopback_spellings_are_accepted(self):
        for host in ("127.0.0.1", "localhost"):
            resp = self._get("/", host="%s:%d" % (host, self.port))
            self.assertEqual(resp.status, 200, host)

    def test_foreign_host_header_is_rejected(self):
        # DNS-rebinding guard: a hostile domain pointed at 127.0.0.1 must fail.
        for host in ("evil.com", "datanodes.to.evil.net"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self._get("/", host="%s:%d" % (host, self.port))
            self.assertEqual(ctx.exception.code, 403, host)

    def test_build_allowed_hosts_contents(self):
        hosts = build_allowed_hosts(8799)
        self.assertEqual(hosts, {"127.0.0.1:8799", "localhost:8799", "[::1]:8799"})
        self.assertIn("192.168.1.10:8799", build_allowed_hosts(8799, "192.168.1.10"))


class VerifyPayloadTests(unittest.TestCase):
    """verify_links must hand the UI what it needs to offer browser capture."""

    def test_failure_carries_original_url_and_host_hint(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            with mock.patch("ps5_streamer.probe_source",
                            side_effect=TransferError("blocked")):
                out = manager.verify_links({"urls": ["https://1fichier.com/?abc123"]})
        result = out["results"][0]
        self.assertFalse(result["ok"])
        self.assertEqual(result["original_url"], "https://1fichier.com/?abc123")
        self.assertIsNotNone(result["resolve_hint"])
        self.assertIn("1fichier", result["resolve_hint"])

    def test_hintless_host_reports_no_hint(self):
        with tempfile.TemporaryDirectory() as d:
            manager = Manager(d)
            with mock.patch("ps5_streamer.probe_source",
                            side_effect=TransferError("boom")):
                out = manager.verify_links({"urls": ["https://example.com/file.pkg"]})
        self.assertIsNone(out["results"][0]["resolve_hint"])


if __name__ == "__main__":
    unittest.main()
