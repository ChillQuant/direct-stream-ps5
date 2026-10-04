"""Bounded, cancellable HTTP → FTP pipeline. Python 3.9+, standard library only."""
import ftplib
import hashlib
import http.client
import math
import os
import posixpath
import queue
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MIB = 1024 * 1024
BLOCK = MIB
HEADERS = {"User-Agent": "PS5-Transfer/2.0", "Accept-Encoding": "identity"}

class TransferError(Exception):
    """A problem that needs user action; don't blindly retry it."""

class Cancelled(Exception):
    pass

class StopToken:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.resources = set()

    def check(self):
        if self.event.is_set():
            raise Cancelled()

    def track(self, obj):
        with self.lock:
            if self.event.is_set():
                self._close(obj)
                raise Cancelled()
            self.resources.add(obj)
        return obj

    def untrack(self, obj):
        with self.lock:
            self.resources.discard(obj)

    @staticmethod
    def _close(obj):
        try:
            s = obj if isinstance(obj, socket.socket) else getattr(obj, "sock", None)
            if s:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            obj.close()
        except Exception:
            pass

    def cancel(self):
        self.event.set()
        with self.lock:
            resources = list(self.resources)
        for obj in resources:
            self._close(obj)

    def wait(self, delay):
        if self.event.wait(delay):
            raise Cancelled()


def safe_text(value, label, maximum=2048):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise TransferError(f"Invalid {label}.")
    return value.strip()


def valid_name(value):
    value = safe_text(value, "file name", 200)
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        raise TransferError("Use a file name without slashes or control characters.")
    return value


def valid_folder(value):
    value = safe_text(value, "destination folder")
    if not value.startswith("/") or ".." in value.split("/") or "\\" in value:
        raise TransferError("Destination must be an absolute PS5 folder without '..'.")
    return posixpath.normpath(value)


def valid_url(value):
    value = safe_text(value, "download URL", 16384)
    try:
        p = urllib.parse.urlsplit(value)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            raise ValueError()
        _ = p.port
        value.encode("ascii")
    except (ValueError, UnicodeError):
        raise TransferError("Use an HTTP or HTTPS direct link (URL-encode spaces and non-ASCII characters).")
    return value


def tls_context():
    """Default TLS verification, falling back to the macOS system CA bundle when
    python.org Python has no certificates installed. Verification stays on."""
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca") and os.path.isfile("/etc/ssl/cert.pem"):
        ctx.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return ctx


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        valid_url(newurl)
        if req.full_url.startswith("https:") and not newurl.startswith("https:"):
            raise TransferError("Blocked an HTTPS-to-HTTP redirect. Use a secure direct link.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener():
    # Match the raw persistent workers: direct connections, no implicit environment proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect(),
        urllib.request.HTTPSHandler(context=tls_context()))


def parse_range(headers, start, end, total=None):
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers.get("Content-Range", ""))
    if not match:
        raise TransferError("The download server sent an invalid Content-Range.")
    a, b, n = map(int, match.groups())
    if a != start or b != end or b >= n or (total is not None and n != total):
        raise TransferError("The download server returned the wrong byte range; transfer stopped to protect the file.")
    if headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        raise TransferError("Compressed range responses cannot be safely assembled.")
    return n


@dataclass
class SourceInfo:
    kind: str
    location: str
    size: object
    ranges: bool = False
    etag: str = ""
    modified: str = ""
    fingerprint: str = ""

    def identity(self):
        return {"kind": self.kind, "size": self.size, "etag": self.etag,
                "modified": self.modified, "fingerprint": self.fingerprint}

    def resumable(self):
        return self.kind == "local" or (self.ranges and bool(self.etag or self.modified))


def probe_source(kind, location, token):
    token.check()
    if kind == "local":
        path = os.path.abspath(os.path.expanduser(safe_text(location, "local path", 8192)))
        if not os.path.isfile(path):
            raise TransferError("Local file was not found. Choose an existing file on this Mac.")
        with open(path, "rb") as f:
            stat = os.fstat(f.fileno())
            digest = hashlib.sha256(f.read(65536))
            f.seek(max(0, stat.st_size - 65536))
            digest.update(f.read(65536))
        return SourceInfo("local", path, stat.st_size, True,
            fingerprint=f"{stat.st_mtime_ns}:{digest.hexdigest()}")
    location = valid_url(location)
    req = urllib.request.Request(location, headers={**HEADERS, "Range": "bytes=0-0"})
    try:
        response = opener().open(req, timeout=20)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code in (408, 429, 500, 502, 503, 504):
            raise OSError(f"Temporary download server error: HTTP {code}") from e
        raise TransferError(f"Download server returned HTTP {code}. Check the direct link or get a fresh one.")
    with response:
        token.check()
        h = response.headers
        if "text/html" in h.get("Content-Type", "").lower():
            raise TransferError("This is a web page, not a downloadable file. Paste the direct download link.")
        if h.get("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise TransferError("Server ignored the request for uncompressed bytes.")
        ranges = response.status == 206
        if ranges:
            size = parse_range(h, 0, 0)
            # Headers define the probe. The body is not reused as transfer data.
        elif response.status == 200:
            size = int(h["Content-Length"]) if h.get("Content-Length") else None
        else:
            raise TransferError(f"Unexpected HTTP {response.status}.")
        etag = h.get("ETag", "")
        if etag.startswith("W/"):
            etag = ""
        return SourceInfo("url", valid_url(response.geturl()), size, ranges, etag,
                          h.get("Last-Modified", ""))


class Meter:
    def __init__(self):
        self.lock = threading.Lock()
        self.downloaded = 0
        self.uploaded = 0
        self.buffered = 0
        self.waiting = 0.0
        self.sending = 0.0

    def add(self, **values):
        with self.lock:
            for k, v in values.items():
                setattr(self, k, getattr(self, k) + v)

    def snapshot(self):
        with self.lock:
            return {k: getattr(self, k) for k in ("downloaded", "uploaded", "buffered", "waiting", "sending")}


class ParallelReader:
    """A fixed window includes in-flight chunks, completed chunks and the current chunk.

    Persistent workers request 8 MiB ranges by default. The consumer uses memoryview
    slices, avoiding an extra multi-megabyte copy on every FTP write.
    """
    def __init__(self, source, offset, workers, buffer_mb, chunk_mb, token, meter, headers=None):
        self.source, self.offset, self.token, self.meter = source, offset, token, meter
        self.base_headers = headers or HEADERS
        self.chunk = chunk_mb * MIB
        self.slots = max(1, buffer_mb * MIB // self.chunk)
        self.count = math.ceil((source.size - offset) / self.chunk)
        self.cv = threading.Condition()
        self.assign = self.read_index = 0
        self.ready = {}
        self.current = None
        self.position = 0
        self.error = None
        self.closed = threading.Event()
        self.connections = set()
        self.threads = []
        for i in range(min(workers, self.slots, self.count)):
            t = threading.Thread(target=self._worker, daemon=True, name=f"http-{i}")
            self.threads.append(t)
            t.start()

    def _worker(self):
        conn = None
        p = urllib.parse.urlsplit(self.source.location)
        path = urllib.parse.urlunsplit(("", "", p.path or "/", p.query, ""))
        try:
            while not self.closed.is_set():
                self.token.check()
                with self.cv:
                    while self.assign - self.read_index >= self.slots and not self.closed.is_set():
                        self.cv.wait(.1)
                        self.token.check()
                    if self.closed.is_set() or self.assign >= self.count:
                        return
                    idx = self.assign
                    self.assign += 1
                start = self.offset + idx * self.chunk
                end = min(self.source.size - 1, start + self.chunk - 1)
                for attempt in range(3):
                    self.token.check()
                    try:
                        if conn is None:
                            if p.scheme == "https":
                                conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=20,
                                                                   context=tls_context())
                            else:
                                conn = http.client.HTTPConnection(p.hostname, p.port, timeout=20)
                            self.token.track(conn)
                            with self.cv:
                                self.connections.add(conn)
                        headers = {**self.base_headers, "Range": f"bytes={start}-{end}"}
                        if self.source.etag or self.source.modified:
                            headers["If-Range"] = self.source.etag or self.source.modified
                        conn.request("GET", path, headers=headers)
                        if getattr(conn, "sock", None):
                            try:
                                conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                                conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * MIB)
                            except OSError:
                                pass
                        r = conn.getresponse()
                        if r.status in (408, 429, 500, 502, 503, 504):
                            # Release the connection before retrying a temporary source failure.
                            r.close()
                            raise OSError("Temporary HTTP error or source rate limit")
                        if r.status != 206:
                            raise TransferError(f"Range request returned HTTP {r.status}; source may have changed. Refresh the link or use one stream.")
                        parse_range(r.headers, start, end, self.source.size)
                        if self.source.etag and r.headers.get("ETag", self.source.etag) != self.source.etag:
                            raise TransferError("Source ETag changed during transfer.")
                        if not self.source.etag and self.source.modified and r.headers.get("Last-Modified", self.source.modified) != self.source.modified:
                            raise TransferError("Source modification date changed during transfer.")
                        length = end - start + 1
                        buf = bytearray(length)
                        view = memoryview(buf)
                        done = 0
                        while done < length:
                            self.token.check()
                            if self.closed.is_set():
                                return
                            n = r.readinto(view[done: min(length, done + BLOCK)])
                            if not n:
                                raise OSError("Download ended before the requested range was complete.")
                            done += n
                            self.meter.add(downloaded=n)
                        if r.read(1):
                            raise TransferError("Server returned more bytes than requested.")
                        r.close()
                        break
                    except TransferError:
                        raise
                    except (OSError, http.client.HTTPException) as e:
                        if conn:
                            self.token.untrack(conn)
                            with self.cv:
                                self.connections.discard(conn)
                            conn.close()
                            conn = None
                        if attempt == 2:
                            raise OSError(f"HTTP range failed after 3 attempts: {type(e).__name__}")
                        self.token.wait(.5 * (attempt + 1))
                with self.cv:
                    if self.closed.is_set():
                        return
                    self.ready[idx] = view
                    self.meter.add(buffered=len(view))
                    self.cv.notify_all()
                # Do not retain a consumed chunk in an idle worker's local variables.
                del buf, view
        except Exception as e:
            with self.cv:
                if not self.error:
                    self.error = e
                self.closed.set()
                self.cv.notify_all()
        finally:
            if conn:
                self.token.untrack(conn)
                with self.cv:
                    self.connections.discard(conn)
                conn.close()

    def read(self, size=BLOCK):
        self.token.check()
        with self.cv:
            if self.current is not None and self.position == len(self.current):
                self.current = None
                self.read_index += 1
                self.cv.notify_all()
            if self.read_index >= self.count:
                return b""
            if self.current is None:
                while self.read_index not in self.ready:
                    self.token.check()
                    if self.error:
                        raise self.error
                    if self.closed.is_set():
                        raise Cancelled()
                    self.cv.wait(.1)
                self.current = self.ready.pop(self.read_index)
                self.position = 0
            block = self.current[self.position:self.position + size]
            self.position += len(block)
            self.meter.add(buffered=-len(block))
            return block

    def close(self):
        self.closed.set()
        with self.cv:
            conns = list(self.connections)
            self.ready.clear()
            self.current = None
            self.cv.notify_all()
        for conn in conns:
            StopToken._close(conn)
        deadline = time.monotonic() + 2
        for t in self.threads:
            t.join(max(0, deadline - time.monotonic()))


class SequentialReader:
    def __init__(self, source, offset, buffer_mb, token, meter, headers=None):
        self.source, self.offset, self.token, self.meter = source, offset, token, meter
        self.base_headers = headers or HEADERS
        self.closed = threading.Event()
        self.q = queue.Queue(maxsize=max(1, buffer_mb - 2))
        self.resource = None
        self.error = None
        self.thread = threading.Thread(target=self._fill, daemon=True, name="source-reader")
        self.thread.start()

    def _put(self, block):
        while not self.closed.is_set():
            self.token.check()
            try:
                self.q.put(block, timeout=.1)
                return
            except queue.Full:
                pass

    def _fill(self):
        try:
            if self.source.kind == "local":
                r = open(self.source.location, "rb")
                r.seek(self.offset)
            else:
                headers = dict(self.base_headers)
                if self.offset:
                    headers["Range"] = f"bytes={self.offset}-"
                if self.source.etag:
                    headers["If-Match"] = self.source.etag
                elif self.source.modified:
                    headers["If-Unmodified-Since"] = self.source.modified
                r = opener().open(urllib.request.Request(self.source.location, headers=headers), timeout=20)
                if self.offset:
                    if r.status != 206:
                        r.close()
                        raise TransferError("Server ignored resume. Stopped before appending incorrect bytes.")
                    parse_range(r.headers, self.offset, self.source.size - 1, self.source.size)
                elif r.status != 200:
                    r.close()
                    raise TransferError("Expected a complete HTTP response.")
                if self.source.etag and r.headers.get("ETag", self.source.etag) != self.source.etag:
                    r.close()
                    raise TransferError("Source ETag changed before the download started.")
                if not self.source.etag and self.source.modified and r.headers.get("Last-Modified", self.source.modified) != self.source.modified:
                    r.close()
                    raise TransferError("Source modification date changed before download.")
                expected = None if self.source.size is None else self.source.size - self.offset
                if expected is not None and r.headers.get("Content-Length") and int(r.headers["Content-Length"]) != expected:
                    r.close()
                    raise TransferError("Source length changed before the download started.")
                if "text/html" in r.headers.get("Content-Type", "").lower() or r.headers.get("Content-Encoding", "identity").lower() != "identity":
                    r.close()
                    raise TransferError("Server returned a web page or compressed bytes.")
            self.resource = r
            remaining = None if self.source.size is None else self.source.size - self.offset
            with r:
                while not self.closed.is_set():
                    self.token.check()
                    data = r.read(BLOCK if remaining is None else min(BLOCK, remaining + 1))
                    if not data:
                        if remaining not in (None, 0):
                            raise OSError("Source ended early; partial file retained.")
                        break
                    if remaining is not None:
                        remaining -= len(data)
                        if remaining < 0:
                            raise TransferError("Source length changed while reading.")
                    self.meter.add(downloaded=len(data), buffered=len(data))
                    self._put(data)
            self._put(None)
        except Exception as e:
            self.error = e
            try:
                self._put(None)
            except Cancelled:
                pass

    def read(self, size=BLOCK):
        while not self.closed.is_set():
            self.token.check()
            try:
                block = self.q.get(timeout=.1)
            except queue.Empty:
                continue
            if block is None:
                if self.error:
                    raise self.error
                return b""
            self.meter.add(buffered=-len(block))
            return block
        raise Cancelled()

    def close(self):
        self.closed.set()
        # Closing an HTTPResponse from a second thread can block on its file lock.
        # Shut down its socket first to interrupt a stalled read.
        r = self.resource
        try:
            sock = r.fp.raw._sock
            sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass
        if r:
            try:
                r.close()
            except Exception:
                pass
        self.thread.join(1)
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


def make_reader(source, offset, settings, token, meter):
    if source.kind == "url" and source.ranges and source.size is not None and settings["streams"] > 1:
        return ParallelReader(source, offset, settings["streams"], settings["buffer_mb"], settings["chunk_mb"], token, meter,
                              settings.get("_headers"))
    return SequentialReader(source, offset, settings["buffer_mb"], token, meter, settings.get("_headers"))


def connect_ftp(settings, token):
    ftp = token.track(ftplib.FTP())
    try:
        ftp.connect(settings["host"], settings["port"], timeout=15)
        ftp.login(settings.get("username") or "anonymous", settings.get("password", ""))
        ftp.voidcmd("TYPE I")
        ftp.timeout = 30
        ftp.sock.settimeout(30)
        return ftp
    except Exception:
        token.untrack(ftp)
        ftp.close()
        raise


def close_ftp(ftp, token, clean=False):
    if not ftp:
        return
    token.untrack(ftp)
    if clean:
        try:
            ftp.sock.settimeout(2)
            ftp.quit()
            return
        except Exception:
            pass
    ftp.close()


def remote_size(ftp, path):
    try:
        n = ftp.size(path)
        if n is None:
            raise TransferError("PS5 FTP did not return a file size.")
        return n
    except ftplib.error_perm as e:
        if str(e).startswith("550"):
            # 550 can also mean denied: STOR will then fail without silently replacing a known file.
            return None
        raise TransferError("PS5 FTP must support SIZE for safe transfers and resume.")


def ensure_folder(ftp, folder):
    ftp.cwd("/")
    for part in folder.strip("/").split("/"):
        if not part:
            continue
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def check_ftp_storage(ftp, folder):
    """Attempt to query available free disk space from PS5 FTP. Returns bytes (int) or None."""
    try:
        resp = ftp.sendcmd("AVBL " + folder)
        parts = resp.strip().split()
        if parts and parts[0] == "213" and len(parts) > 1 and parts[1].isdigit():
            return int(parts[1])
    except Exception:
        pass
    try:
        resp = ftp.sendcmd("SITE FREESPACE " + folder)
        m = re.search(r"(\d+)\s*(?:bytes|b)?\s*free", resp, re.IGNORECASE) or re.search(r"200\s+(\d+)", resp)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def validate_source_url(url, token):
    """Fast probe of a URL to check validity, range support, and size without downloading."""
    try:
        src = probe_source("url", url, token)
        return {
            "valid": True,
            "ranges": src.ranges,
            "resumable": src.resumable(),
            "size": src.size,
            "etag": src.etag,
            "modified": src.modified,
            "error": None
        }
    except Exception as e:
        return {
            "valid": False,
            "ranges": False,
            "resumable": False,
            "size": None,
            "error": str(e)
        }


def windowed_rates(hist, now, snap, window=15.0):
    """Upload/download rates over the last few seconds, so bursty chunk delivery
    doesn't make the dashboard (or the ETA) swing between 0 and the burst speed."""
    hist.append((now, snap["uploaded"], snap["downloaded"]))
    while len(hist) > 2 and now - hist[1][0] >= window:
        hist.pop(0)
    t0, up0, down0 = hist[0]
    dt = max(.001, now - t0)
    return (snap["uploaded"] - up0) / dt, (snap["downloaded"] - down0) / dt


def transfer(job, settings, token, report, save):
    """One transfer attempt. Partial files are uniquely owned by the persisted job ID."""
    report("status", "Inspecting source")
    source = probe_source(job["kind"], job["source"], token)
    old_identity = job.get("identity")
    if old_identity and old_identity != source.identity():
        raise TransferError("Source changed since this job started. Use Restart to begin a new partial file.")
    job["identity"] = source.identity()
    job["total"] = source.size
    job["resumable"] = source.resumable()
    save()
    report("source", {"ranges": source.ranges, "resumable": source.resumable(), "size": source.size})
    ftp = reader = data_socket = None
    clean = False
    meter = Meter()
    try:
        report("status", "Connecting to PS5")
        ftp = connect_ftp(settings, token)
        ensure_folder(ftp, settings["folder"])
        name = valid_name(job["name"])
        part = f"{name}.{job['id']}.ps5part"
        existing = remote_size(ftp, name)
        if existing is not None and not job.get("overwrite"):
            raise TransferError("Destination file already exists. Rename this job or explicitly enable replacement in a new job.")
        partial_size = remote_size(ftp, part)
        offset = partial_size or 0
        if partial_size is not None and not job.get("stage_owned"):
            raise TransferError("Found a partial file without source history; restart this job.")
        if offset and (source.size is None or offset > source.size):
            raise TransferError("Partial file is larger than the source or source length is unknown. Restart the job.")
        if offset and not source.resumable():
            raise TransferError("This link does not provide stable resume metadata. Restart, or download to your Mac and send the local file.")
        job["stage_owned"] = True
        save()
        job["transferred"] = offset
        free_space = check_ftp_storage(ftp, settings["folder"])
        if free_space is not None and source.size is not None:
            needed = max(0, source.size - offset)
            if needed > free_space:
                raise TransferError(f"Insufficient PS5 disk space: needs {needed/1e9:.2f} GB, but only {free_space/1e9:.2f} GB is available.")
        if source.size is None or offset < source.size or source.size == 0:
            report("status", f"Resuming at {offset} bytes" if offset else "Transferring")
            # REST failure is fatal: never silently truncate a resumed upload.
            try:
                data_socket = token.track(ftp.transfercmd("STOR " + part, rest=offset or None))
            except ftplib.error_perm as e:
                raise TransferError("PS5 FTP refused upload/resume. Confirm write access and REST support, or restart the job.") from e
            try:
                data_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * MIB)
            except OSError:
                pass
            data_socket.settimeout(30)
            reader = make_reader(source, offset, settings, token, meter)

            # Pre-buffer a cushion of data (e.g. 16-64 MiB) for URL streams before sending to PS5.
            # This prevents the initial starve-and-burst (sawtooth) cycle on cold start.
            if source.kind == "url" and source.size and (source.size - offset) > 16 * MIB:
                prebuffer_target = min(64 * MIB, max(16 * MIB, (settings["buffer_mb"] * MIB) // 4))
                t_pre = time.monotonic()
                report("status", "Pre-buffering pipeline…")
                while not token.event.is_set():
                    snap = meter.snapshot()
                    if snap["buffered"] >= prebuffer_target or (time.monotonic() - t_pre) > 3.0:
                        break
                    token.wait(0.1)
                report("status", f"Resuming at {offset} bytes" if offset else "Transferring")

            started = last = time.monotonic()
            previous = meter.snapshot()
            hist = [(started, previous["uploaded"], previous["downloaded"])]
            transferred = offset

            q_depth = max(16, min(64, (settings["buffer_mb"] * MIB) // BLOCK // 2))
            send_q = queue.Queue(maxsize=q_depth)
            sender_error = [None]
            send_done = threading.Event()

            def sender_loop():
                curr = offset
                try:
                    while not token.event.is_set():
                        item = send_q.get()
                        if item is None:
                            send_q.task_done()
                            break
                        cap = settings["limit_mbps"] * 1_000_000
                        if cap:
                            wait = (curr - offset + len(item)) / cap - (time.monotonic() - started)
                            if wait > 0:
                                token.wait(wait)
                        t = time.monotonic()
                        data_socket.sendall(item)
                        meter.add(uploaded=len(item), sending=time.monotonic() - t)
                        curr += len(item)
                        send_q.task_done()
                except Exception as ex:
                    sender_error[0] = ex
                finally:
                    send_done.set()

            sender_thread = threading.Thread(target=sender_loop, daemon=True, name="ftp-async-sender")
            sender_thread.start()

            try:
                while True:
                    token.check()
                    if sender_error[0]:
                        raise sender_error[0]
                    t = time.monotonic()
                    block = reader.read(BLOCK)
                    meter.add(waiting=time.monotonic() - t)
                    if not block:
                        break
                    while not token.event.is_set():
                        if sender_error[0]:
                            raise sender_error[0]
                        try:
                            send_q.put(block, timeout=0.1)
                            break
                        except queue.Full:
                            pass
                    token.check()
                    transferred += len(block)
                    job["transferred"] = transferred
                    now = time.monotonic()
                    if now - last >= .4:
                        snap = meter.snapshot()
                        up, down = windowed_rates(hist, now, snap)
                        capacity = settings["buffer_mb"] * MIB
                        bottleneck = "Speed cap enabled" if settings["limit_mbps"] else (
                            "Waiting for source" if snap["buffered"] < capacity * .25 else "PS5 / local network")
                        report("progress", {"transferred": transferred, "total": source.size, "upload_bps": up,
                            "download_bps": down, "buffered": snap["buffered"], "buffer_capacity": settings["buffer_mb"] * MIB,
                            "elapsed": now - started, "eta": (source.size - transferred) / up if source.size and up else None,
                            "bottleneck": bottleneck, "offset": offset})
                        last, previous = now, snap
                while not token.event.is_set():
                    if sender_error[0]:
                        raise sender_error[0]
                    try:
                        send_q.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                sender_thread.join(timeout=30)
                if sender_error[0]:
                    raise sender_error[0]
            finally:
                if sender_thread.is_alive():
                    try:
                        send_q.put_nowait(None)
                    except Exception:
                        pass
                    sender_thread.join(timeout=1.0)

            if source.size is not None and transferred != source.size:
                raise TransferError("Source byte count did not match. Incomplete file retained.")
            reader.close()
            reader = None
            token.untrack(data_socket)
            data_socket.close()
            data_socket = None
            ftp.voidresp()
        token.check()
        report("status", "Verifying PS5 file size")
        actual = remote_size(ftp, part)
        expected = source.size if source.size is not None else job["transferred"]
        if actual != expected:
            raise TransferError(f"PS5 size mismatch: expected {expected} bytes, received {actual}. Partial retained; do not use it.")
        # Recheck local metadata after read to catch concurrent edits.
        if source.kind == "local" and probe_source("local", source.location, token).identity() != source.identity():
            raise TransferError("Local file changed while uploading. Partial retained; restart with a stable file.")
        # Check again in case another client created the final name during this transfer.
        if not job.get("overwrite") and remote_size(ftp, name) is not None:
            raise TransferError("Destination appeared during upload. Verified partial retained to avoid replacing it.")
        report("status", "Finalizing file")
        try:
            ftp.rename(part, name)
        except ftplib.all_errors as e:
            raise TransferError(f"Upload size verified, but rename failed. Your complete file is {part}; rename it on PS5. No file was deleted.") from e
        job["transferred"] = expected
        job["total"] = expected
        clean = True
        report("complete", {"size": expected, "verification": "Remote file size verified; not a cryptographic checksum."})
    finally:
        if reader:
            reader.close()
        if data_socket:
            token.untrack(data_socket)
            data_socket.close()
        close_ftp(ftp, token, clean)
