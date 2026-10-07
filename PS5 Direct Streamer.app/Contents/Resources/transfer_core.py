"""Bounded, cancellable HTTP → FTP pipeline. Python 3.9+, standard library only."""
import ftplib
import hashlib
import http.client
import io
import math
import os
import posixpath
import queue
import re
import socket
import ssl
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from resolver import (
    pre_resolve_url,
    extract_download_link_from_html,
    parse_filename_from_headers,
    get_captcha_hint_if_applicable,
    get_request_headers_for_url,
    is_single_connection_host,
    parse_multipart_info,
    detect_multipart_sequence,
)
from zip_streamer import (
    is_zip_candidate,
    is_archive_candidate,
    inspect_zip_archive,
    inspect_archive,
    get_zip_payload_offset,
    ZipStreamingReader,
    ArchiveStreamingReader,
    EncryptedArchiveError,
    extract_password_hint,
)

MIB = 1024 * 1024
BLOCK = MIB
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}

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
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req:
            # Preserve critical transfer headers (Range, User-Agent, Referer, Accept, etc.) across redirect
            for k, v in req.headers.items():
                if k.lower() not in ("host", "content-length"):
                    new_req.add_header(k, v)
            if "Referer" not in new_req.headers:
                parsed_orig = urllib.parse.urlsplit(req.full_url)
                new_req.add_header("Referer", f"{parsed_orig.scheme}://{parsed_orig.netloc}/")
        return new_req


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
    location: object
    size: object
    ranges: bool = False
    etag: str = ""
    modified: str = ""
    fingerprint: str = ""
    filename: str = ""
    parts: list = None
    archive_info: dict = None

    def identity(self):
        if (self.kind.startswith("zip_") or self.kind.startswith("archive_")) and self.archive_info:
            return {
                "kind": self.kind,
                "size": self.size,
                "target_name": self.archive_info.get("filename"),
                "crc": self.archive_info.get("crc"),
                "fingerprint": self.fingerprint,
                "etag": self.etag,
            }
        if self.kind == "multipart" and self.parts:
            return {
                "kind": self.kind,
                "size": self.size,
                "fingerprint": self.fingerprint,
                "parts": [p.identity() for p in self.parts],
            }
        return {"kind": self.kind, "size": self.size, "etag": self.etag,
                "modified": self.modified, "fingerprint": self.fingerprint}

    def resumable(self):
        if self.kind.startswith("zip_") or self.kind.startswith("archive_"):
            return False
        if self.kind == "multipart":
            return bool(self.parts) and all(p.resumable() and p.size is not None for p in self.parts)
        return self.kind == "local" or (self.ranges and bool(self.etag or self.modified))


def probe_source(kind, location, token, resolve_depth=0, decompress=True):
    token.check()
    if kind == "multipart":
        return probe_multipart_source(location, token)
    if kind == "local":
        path = os.path.abspath(os.path.expanduser(safe_text(location, "local path", 8192)))
        if not os.path.isfile(path):
            raise TransferError("Local file was not found. Choose an existing file on this Mac.")
        with open(path, "rb") as f:
            stat = os.fstat(f.fileno())
            digest = hashlib.sha256(f.read(65536))
            f.seek(max(0, stat.st_size - 65536))
            digest.update(f.read(65536))
        fp = f"{stat.st_mtime_ns}:{digest.hexdigest()}"
        filename = Path(path).name
        if decompress and is_archive_candidate(path):
            try:
                meta = inspect_archive("local", path)
                sel = meta["selected"]
                arch_type = meta.get("archive_type", "zip")
                if arch_type == "zip":
                    p_off = get_zip_payload_offset("local", path, sel["header_offset"])
                    return SourceInfo(
                        kind="zip_local",
                        location=path,
                        size=sel["uncompressed_size"],
                        ranges=True,
                        fingerprint=fp,
                        filename=sel["basename"],
                        archive_info={**sel, "payload_offset": p_off, "total_archive_size": meta["total_archive_size"], "archive_type": "zip"},
                    )
                else:
                    return SourceInfo(
                        kind="archive_local",
                        location=path,
                        size=sel["uncompressed_size"],
                        ranges=True,
                        fingerprint=fp,
                        filename=sel["basename"],
                        archive_info={**sel, "payload_offset": 0, "total_archive_size": meta["total_archive_size"], "archive_type": arch_type},
                    )
            except EncryptedArchiveError as ex:
                raise TransferError(str(ex)) from ex
            except Exception as ex:
                raise TransferError(f"Cannot decompress '{filename}': {ex}. Uncheck 'Decompress Archive' to transfer the raw archive file.") from ex
        return SourceInfo("local", path, stat.st_size, True,
            fingerprint=fp, filename=filename)
    location = valid_url(location)
    if resolve_depth == 0:
        location = pre_resolve_url(location)
    req_headers = get_request_headers_for_url(location, HEADERS)
    req = urllib.request.Request(location, headers={**req_headers, "Range": "bytes=0-0"})
    try:
        response = opener().open(req, timeout=20)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code in (408, 429, 500, 502, 503, 504):
            raise OSError(f"Temporary download server error: HTTP {code}") from e
        hint = get_captcha_hint_if_applicable(location)
        if hint:
            raise TransferError(hint)
        raise TransferError(f"Download server returned HTTP {code}. Check the direct link or get a fresh one.")
    with response:
        token.check()
        h = response.headers
        if "text/html" in h.get("Content-Type", "").lower():
            if resolve_depth < 3:
                try:
                    html_chunk = response.read(256 * 1024).decode("utf-8", errors="replace")
                    resolved = extract_download_link_from_html(response.geturl(), html_chunk)
                except Exception:
                    resolved = None
                if resolved and resolved != location and resolved != response.geturl():
                    return probe_source(kind, resolved, token, resolve_depth + 1, decompress=decompress)
            hint = get_captcha_hint_if_applicable(location)
            if hint:
                raise TransferError(hint)
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
        filename = parse_filename_from_headers(h, response.geturl())
        final_url = valid_url(response.geturl())
        is_arch = is_archive_candidate(final_url) or is_archive_candidate(filename)
        content_type_arch = any(t in h.get("Content-Type", "").lower() for t in ("application/zip", "application/x-rar", "application/vnd.rar", "application/x-7z-compressed", "application/x-tar"))
        if decompress and (is_arch or content_type_arch):
            try:
                arch_headers = get_request_headers_for_url(final_url, HEADERS)
                meta = inspect_archive("url", final_url, headers=arch_headers, total_size=size)
                sel = meta["selected"]
                arch_type = meta.get("archive_type", "zip")
                if arch_type == "zip":
                    p_off = get_zip_payload_offset("url", final_url, sel["header_offset"], headers=arch_headers)
                    return SourceInfo(
                        kind="zip_url",
                        location=final_url,
                        size=sel["uncompressed_size"],
                        ranges=ranges,
                        etag=etag,
                        modified=h.get("Last-Modified", ""),
                        filename=sel["basename"],
                        archive_info={**sel, "payload_offset": p_off, "total_archive_size": size, "archive_type": "zip"},
                    )
                else:
                    return SourceInfo(
                        kind="archive_url",
                        location=final_url,
                        size=sel["uncompressed_size"],
                        ranges=ranges,
                        etag=etag,
                        modified=h.get("Last-Modified", ""),
                        filename=sel["basename"],
                        archive_info={**sel, "payload_offset": 0, "total_archive_size": size, "archive_type": arch_type},
                    )
            except EncryptedArchiveError as ex:
                raise TransferError(str(ex)) from ex
            except Exception as ex:
                if is_arch:
                    raise TransferError(f"Cannot decompress '{filename}': {ex}. Uncheck 'Decompress Archive' to transfer the raw archive file.") from ex
                pass
        return SourceInfo("url", final_url, size, ranges, etag,
                          h.get("Last-Modified", ""), filename=filename)


def probe_multipart_source(parts, token, target_filename=None):
    token.check()
    if isinstance(parts, (str, bytes)):
        parts = [parts]
    if not parts:
        raise TransferError("No parts provided for multi-part transfer.")

    probed_parts = [None] * len(parts)
    errors = [None] * len(parts)

    def _probe_part(idx, item):
        try:
            token.check()
            if isinstance(item, dict):
                p_kind = item.get("kind", "url")
                p_loc = item.get("source", "")
            else:
                p_loc = str(item)
                p_kind = "local" if os.path.exists(p_loc) else "url"
            probed_parts[idx] = probe_source(p_kind, p_loc, token)
        except Exception as ex:
            errors[idx] = ex

    threads = [threading.Thread(target=_probe_part, args=(i, p), daemon=True) for i, p in enumerate(parts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    token.check()
    for i, err in enumerate(errors):
        if err:
            raise TransferError(f"Failed to inspect part {i + 1}: {err}")

    for i, p in enumerate(probed_parts):
        if p is None:
            raise TransferError(f"Inspection timed out for part {i + 1}.")
        if p.size is None:
            raise TransferError(f"Part {i + 1} ({p.filename or 'part'}) has unknown size. Direct stitching requires exact part sizes.")

    total_size = sum(p.size for p in probed_parts)
    combined_fp = hashlib.sha256(
        "".join(f"{p.size}:{p.fingerprint or p.etag or p.modified}" for p in probed_parts).encode()
    ).hexdigest()

    fname = target_filename
    if not fname:
        user_names = [p.get("name") for p in parts if isinstance(p, dict) and p.get("name")]
        if user_names:
            parsed = parse_multipart_info(user_names[0])
            fname = parsed[0] if parsed else user_names[0]
        else:
            first_name = probed_parts[0].filename or ""
            parsed = parse_multipart_info(first_name)
    if not fname:
        fname = "combined.pkg"
    elif "." not in os.path.basename(fname):
        fname += ".pkg"

    return SourceInfo(
        kind="multipart",
        location=[p.location for p in probed_parts],
        size=total_size,
        ranges=all(p.ranges for p in probed_parts),
        fingerprint=combined_fp,
        filename=fname,
        parts=probed_parts,
    )


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
                        if r.status in (301, 302, 303, 307, 308):
                            redir_hops = 0
                            curr_url = self.source.location
                            while r.status in (301, 302, 303, 307, 308) and redir_hops < 5:
                                redir_hops += 1
                                redir_loc = r.headers.get("Location")
                                if not redir_loc:
                                    break
                                curr_url = urllib.parse.urljoin(curr_url, redir_loc)
                                p = urllib.parse.urlsplit(curr_url)
                                path = urllib.parse.urlunsplit(("", "", p.path or "/", p.query, ""))
                                r.close()
                                if conn:
                                    self.token.untrack(conn)
                                    with self.cv:
                                        self.connections.discard(conn)
                                    conn.close()
                                    conn = None
                                if p.scheme == "https":
                                    conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=20, context=tls_context())
                                else:
                                    conn = http.client.HTTPConnection(p.hostname, p.port, timeout=20)
                                self.token.track(conn)
                                with self.cv:
                                    self.connections.add(conn)
                                headers = {**self.base_headers, "Range": f"bytes={start}-{end}"}
                                if "Referer" not in headers:
                                    headers["Referer"] = f"{p.scheme}://{p.hostname}/"
                                conn.request("GET", path, headers=headers)
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
                elif r.status not in (200, 206):
                    r.close()
                    raise TransferError(f"Expected HTTP 200 or 206, got HTTP {r.status}.")
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


class MultiPartReader:
    """Seamlessly streams multi-part split files (.001, .002... / .part1, .part2...)
    into a single continuous byte stream for PS5 FTP.

    Zero-Degradation Performance Architecture:
    1. Zero local disk space: bytes flow through memory buffers directly to the PS5 FTP socket.
    2. Overlapped pre-warming: when Part N is within 32 MiB of finishing, Part N+1's reader
       is started in the background. Handshakes, HTTP GET range requests, and initial buffer
       filling complete ahead of time.
    3. Microsecond boundary transition: when Part N reaches EOF, Part N+1 is already hot and
       buffered in RAM. The FTP socket continues receiving blocks without dropping speed.
    4. Exact byte resume: maps any arbitrary PS5 file offset X to the correct part index and
       local offset within that part.
    """
    def __init__(self, source, offset, settings, token, meter, on_part_change=None):
        self.source = source
        self.settings = settings
        self.token = token
        self.meter = meter
        self.on_part_change = on_part_change
        self.parts = source.parts or []
        self.closed = threading.Event()
        self.lock = threading.Lock()

        # Cumulative start offsets for each part
        self.offsets = []
        cum = 0
        for p in self.parts:
            self.offsets.append(cum)
            cum += p.size
        self.total_size = cum

        self.current_idx = len(self.parts)
        self.current_reader = None
        self.part_remaining = 0

        self.next_reader = None
        self.next_idx = None
        self.prewarming = False
        self.prewarm_thread = None
        self.prewarm_error = None

        self._init_at_offset(offset)

    def _init_at_offset(self, offset):
        if offset >= self.total_size or not self.parts:
            self.current_idx = len(self.parts)
            return

        for i, cum in enumerate(self.offsets):
            part_end = cum + self.parts[i].size
            if cum <= offset < part_end:
                self.current_idx = i
                local_offset = offset - cum
                self.part_remaining = self.parts[i].size - local_offset
                if self.on_part_change:
                    self.on_part_change(i, len(self.parts), self.parts[i].filename)
                self.current_reader = make_reader(
                    self.parts[i], local_offset, self.settings, self.token, self.meter
                )
                break

    def _start_prewarm(self, target_idx):
        self.prewarming = True
        self.next_idx = target_idx

        def _worker():
            try:
                self.token.check()
                part = self.parts[target_idx]
                reader = make_reader(part, 0, self.settings, self.token, self.meter)
                with self.lock:
                    if self.closed.is_set():
                        reader.close()
                        return
                    self.next_reader = reader
            except Exception as ex:
                with self.lock:
                    self.prewarm_error = ex
            finally:
                self.prewarming = False

        self.prewarm_thread = threading.Thread(
            target=_worker, daemon=True, name=f"prewarm-part-{target_idx}"
        )
        self.prewarm_thread.start()

    def read(self, size=BLOCK):
        while not self.closed.is_set():
            self.token.check()
            if self.current_idx >= len(self.parts):
                return b""

            if self.current_reader is None:
                return b""

            # Trigger background pre-warming of next part when nearing end of current part
            if (
                self.part_remaining <= 32 * MIB
                and self.current_idx + 1 < len(self.parts)
                and self.next_reader is None
                and not self.prewarming
            ):
                self._start_prewarm(self.current_idx + 1)

            block = self.current_reader.read(size)
            if block:
                self.part_remaining -= len(block)
                return block

            # Current part reached EOF
            old_reader = self.current_reader
            self.current_reader = None
            threading.Thread(target=old_reader.close, daemon=True).start()

            # Advance to next part
            self.current_idx += 1
            if self.current_idx >= len(self.parts):
                return b""

            if self.prewarm_thread and self.prewarm_thread.is_alive():
                self.prewarm_thread.join(timeout=10)

            with self.lock:
                if self.prewarm_error:
                    err = self.prewarm_error
                    self.prewarm_error = None
                    raise err

                if self.next_reader is not None and self.next_idx == self.current_idx:
                    self.current_reader = self.next_reader
                    self.next_reader = None
                    self.next_idx = None
                else:
                    part = self.parts[self.current_idx]
                    self.current_reader = make_reader(
                        part, 0, self.settings, self.token, self.meter
                    )

            self.part_remaining = self.parts[self.current_idx].size
            if self.on_part_change:
                self.on_part_change(self.current_idx, len(self.parts), self.parts[self.current_idx].filename)

    def close(self):
        self.closed.set()
        with self.lock:
            cur = self.current_reader
            nxt = self.next_reader
            self.current_reader = None
            self.next_reader = None
        if cur:
            cur.close()
        if nxt:
            nxt.close()
        if self.prewarm_thread and self.prewarm_thread.is_alive():
            self.prewarm_thread.join(timeout=2)


def make_reader(source, offset, settings, token, meter, on_part_change=None):
    loc = source.location if hasattr(source, "location") and isinstance(source.location, str) else ""
    headers = get_request_headers_for_url(loc, settings.get("_headers") or HEADERS)
    if source.kind.startswith("zip_"):
        return ZipStreamingReader(source, settings["buffer_mb"], token, meter, headers)
    if source.kind.startswith("archive_"):
        return ArchiveStreamingReader(source, offset, settings["buffer_mb"], token, meter, headers)
    if source.kind == "multipart":
        return MultiPartReader(source, offset, settings, token, meter, on_part_change=on_part_change)
    if source.kind == "url" and source.ranges and source.size is not None and settings["streams"] > 1:
        if not is_single_connection_host(source.location):
            return ParallelReader(source, offset, settings["streams"], settings["buffer_mb"], settings["chunk_mb"], token, meter,
                                  headers)
    return SequentialReader(source, offset, settings["buffer_mb"], token, meter, headers)


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
        msg = str(e)
        if "ASCII" in msg:
            try:
                ftp.voidcmd("TYPE I")
                return ftp.size(path)
            except Exception:
                pass
        if msg.startswith("550"):
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


def format_bytes(n):
    if n is None:
        return "Unknown size"
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    if n >= 1e3:
        return f"{n / 1e3:.0f} KB"
    return f"{n} B"


def validate_source_url(url, token, decompress=True):
    """Fast probe of a URL to check validity, range support, and size without downloading."""
    try:
        src = probe_source("url", url, token, decompress=decompress)
        return {
            "valid": True,
            "ranges": src.ranges,
            "resumable": src.resumable(),
            "size": src.size,
            "etag": src.etag,
            "modified": src.modified,
            "filename": src.filename,
            "is_zip": (src.kind.startswith("zip_") or src.kind.startswith("archive_")),
            "is_archive": (src.kind.startswith("zip_") or src.kind.startswith("archive_")),
            "archive_type": (src.archive_info or {}).get("archive_type", "zip" if src.kind.startswith("zip_") else "archive"),
            "archive_info": src.archive_info,
            "archive_encrypted": False,
            "staged_extraction": False,
            "error": None
        }
    except Exception as e:
        is_enc = "encrypted" in str(e).lower() or "password" in str(e).lower()
        unar = find_unar_tool()
        if (is_enc or "loose files" in str(e).lower() or "cannot decompress" in str(e).lower()) and unar:
            try:
                raw_src = probe_source("url", url, token, decompress=False)
                fn = raw_src.filename or os.path.basename(urllib.parse.urlsplit(url).path) or "archive.bin"
                pwd_hint = extract_password_hint(fn) or extract_password_hint(url)
                return {
                    "valid": True,
                    "ranges": raw_src.ranges,
                    "resumable": raw_src.resumable(),
                    "size": raw_src.size,
                    "etag": raw_src.etag,
                    "modified": raw_src.modified,
                    "filename": fn,
                    "is_zip": False,
                    "is_archive": True,
                    "archive_type": Path(fn).suffix.lstrip(".").lower() or "archive",
                    "archive_info": None,
                    "archive_encrypted": is_enc,
                    "password_hint": pwd_hint,
                    "staged_extraction": True,
                    "error": None
                }
            except Exception:
                pass
        return {
            "valid": False,
            "ranges": False,
            "resumable": False,
            "size": None,
            "archive_encrypted": is_enc,
            "staged_extraction": False,
            "error": str(e)
        }


def validate_multipart_source(parts, token):
    """Probe all parts in a multi-part sequence to verify availability, sizes, and resume support."""
    try:
        src = probe_multipart_source(parts, token)
        return {
            "valid": True,
            "ranges": src.ranges,
            "resumable": src.resumable(),
            "size": src.size,
            "filename": src.filename,
            "parts_count": len(src.parts),
            "error": None
        }
    except Exception as e:
        return {
            "valid": False,
            "ranges": False,
            "resumable": False,
            "size": None,
            "filename": "",
            "parts_count": 0,
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


def transfer_folder(job, settings, token, report, save):
    """Recursively transfers an entire folder to the PS5 via FTP, preserving directory structure.
    Tracks completed_files so that resumed or restarted transfers skip already verified files."""
    report("status", "Scanning folder contents")
    src_dir = os.path.abspath(os.path.expanduser(job["source"]))
    if not os.path.isdir(src_dir):
        raise TransferError(f"Local folder not found: {src_dir}")

    # Gather all readable files excluding OS junk
    file_entries = []  # (rel_path, abs_path, size)
    for root, dirs, files in os.walk(src_dir):
        dirs.sort()
        for f in sorted(files):
            if f.startswith(".") or f == "Thumbs.db":
                continue
            abs_p = os.path.join(root, f)
            rel_p = os.path.relpath(abs_p, src_dir).replace("\\", "/")
            try:
                sz = os.path.getsize(abs_p)
                file_entries.append((rel_p, abs_p, sz))
            except OSError:
                pass

    if not file_entries:
        raise TransferError("Local folder is empty or contains no readable files.")

    total_bytes = sum(sz for _, _, sz in file_entries)
    job["total"] = total_bytes
    job["files_count"] = len(file_entries)

    completed_list = job.get("completed_files") or []
    valid_rel_paths = {rel_p for rel_p, _, _ in file_entries}
    completed_set = set(completed_list).intersection(valid_rel_paths)
    cumulative_transferred = sum(sz for rel_p, _, sz in file_entries if rel_p in completed_set)
    job["transferred"] = cumulative_transferred
    job["completed_files"] = list(completed_set)
    job["resumable"] = True
    save()

    report("source", {
        "ranges": True,
        "resumable": True,
        "size": total_bytes,
        "is_zip": False,
        "archive_info": None,
        "is_folder": True,
        "files_count": len(file_entries)
    })

    ftp = None
    data_socket = None
    clean = False
    meter = Meter()

    try:
        report("status", "Connecting to PS5")
        ftp = connect_ftp(settings, token)
        base_target_folder = valid_folder(job.get("folder") or settings["folder"])
        ensure_folder(ftp, base_target_folder)

        free_space = check_ftp_storage(ftp, base_target_folder)
        needed = max(0, total_bytes - cumulative_transferred)
        if free_space is not None and needed > free_space:
            raise TransferError(f"Insufficient PS5 disk space: needs {needed/1e9:.2f} GB, but only {free_space/1e9:.2f} GB is available.")

        started = last = time.monotonic()
        previous = meter.snapshot()
        hist = [(started, previous["uploaded"], previous["downloaded"])]

        for idx, (rel_p, abs_p, file_sz) in enumerate(file_entries):
            token.check()
            if rel_p in completed_set:
                continue

            rel_dir = os.path.dirname(rel_p)
            file_name = os.path.basename(rel_p)
            if rel_dir:
                remote_dir = f"{base_target_folder.rstrip('/')}/{rel_dir}"
            else:
                remote_dir = base_target_folder

            ensure_folder(ftp, remote_dir)

            # Check if remote file exists and is already identical in size
            dest_size = remote_size(ftp, file_name)
            if dest_size is not None and dest_size == file_sz and not job.get("overwrite"):
                completed_set.add(rel_p)
                job["completed_files"] = list(completed_set)
                cumulative_transferred += file_sz
                job["transferred"] = cumulative_transferred
                save()
                continue

            part_name = f"{file_name}.{job['id']}.ps5part"
            partial_size = remote_size(ftp, part_name)
            file_offset = partial_size if (partial_size and partial_size <= file_sz) else 0

            report("status", f"Streaming {rel_p} ({idx + 1}/{len(file_entries)})")

            if file_sz == 0:
                try:
                    ftp.storbinary("STOR " + part_name, io.BytesIO(b""))
                    if remote_size(ftp, file_name) is not None and job.get("overwrite"):
                        try:
                            ftp.delete(file_name)
                        except Exception:
                            pass
                    ftp.rename(part_name, file_name)
                except ftplib.all_errors as ex:
                    raise TransferError(f"Failed to create empty file {rel_p}: {ex}")
                completed_set.add(rel_p)
                job["completed_files"] = list(completed_set)
                save()
                continue

            try:
                data_socket = token.track(ftp.transfercmd("STOR " + part_name, rest=file_offset or None))
            except ftplib.error_perm as e:
                raise TransferError(f"PS5 FTP refused upload/resume for {rel_p}.") from e

            try:
                data_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * MIB)
            except OSError:
                pass
            data_socket.settimeout(30)

            sent_in_file = file_offset
            with open(abs_p, "rb") as f:
                if file_offset:
                    f.seek(file_offset)
                while not token.event.is_set():
                    token.check()
                    chunk = f.read(128 * 1024)
                    if not chunk:
                        break
                    cap = settings.get("limit_mbps", 0) * 1_000_000
                    if cap:
                        wait = (meter.snapshot()["uploaded"] + len(chunk)) / cap - (time.monotonic() - started)
                        if wait > 0:
                            token.wait(wait)
                    t_send = time.monotonic()
                    data_socket.sendall(chunk)
                    meter.add(uploaded=len(chunk), sending=time.monotonic() - t_send)
                    sent_in_file += len(chunk)
                    now = time.monotonic()
                    if now - last >= 0.4:
                        snap = meter.snapshot()
                        up, down = windowed_rates(hist, now, snap)
                        current_overall = cumulative_transferred + (sent_in_file - file_offset)
                        job["transferred"] = current_overall
                        rem = max(0, total_bytes - current_overall)
                        eta = rem / up if up > 0 else None
                        bottleneck = "Speed cap enabled" if settings.get("limit_mbps") else "PS5 / local network"
                        report("progress", {
                            "transferred": current_overall,
                            "total": total_bytes,
                            "upload_bps": up,
                            "download_bps": 0,
                            "buffered": 0,
                            "buffer_capacity": settings.get("buffer_mb", 32) * MIB,
                            "elapsed": now - started,
                            "eta": eta,
                            "bottleneck": bottleneck,
                            "offset": cumulative_transferred,
                            "current_file": rel_p,
                            "file_index": idx + 1,
                            "file_count": len(file_entries)
                        })
                        last = now

            token.untrack(data_socket)
            data_socket.close()
            data_socket = None
            ftp.voidresp()

            token.check()
            actual = remote_size(ftp, part_name)
            if actual != file_sz:
                raise TransferError(f"Size mismatch on {rel_p}: expected {file_sz}, received {actual}.")

            if remote_size(ftp, file_name) is not None and job.get("overwrite"):
                try:
                    ftp.delete(file_name)
                except Exception:
                    pass

            try:
                ftp.rename(part_name, file_name)
            except ftplib.all_errors as e:
                raise TransferError(f"Rename failed for {rel_p}: {e}")

            cumulative_transferred += file_sz
            completed_set.add(rel_p)
            job["completed_files"] = list(completed_set)
            job["transferred"] = cumulative_transferred
            job["detail"] = f"Transferred {len(completed_set)}/{len(file_entries)} files"
            save()

        report("status", "Verifying folder structure")
        clean = True
        job["transferred"] = total_bytes
        job["total"] = total_bytes
        job["detail"] = f"Completed ({len(file_entries)} files verified)"
        report("complete", {
            "size": total_bytes,
            "files_count": len(file_entries),
            "verification": "All files transferred and verified"
        })
    finally:
        if data_socket:
            token.untrack(data_socket)
            data_socket.close()
        close_ftp(ftp, token, clean)


def find_unar_tool():
    """Find the path to the unar CLI executable (bundled in app, local bin, or PATH)."""
    # 1. Bundled inside macOS .app Resources/bin
    res_dir = os.environ.get("RESOURCEPATH")
    if res_dir:
        p = os.path.join(res_dir, "bin", "unar")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    # 2. Workspace / repository relative bin/unar or App bundle bin/unar
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "bin", "unar"),
        os.path.join(base, "PS5 Direct Streamer.app", "Contents", "Resources", "bin", "unar"),
        os.path.join(os.path.dirname(base), "Resources", "bin", "unar"),
        "/opt/homebrew/bin/unar",
        "/usr/local/bin/unar",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    # 3. System PATH
    found = shutil.which("unar")
    if found and os.access(found, os.X_OK):
        return found
    return None


def find_extracted_payload(extract_dir, fallback_name="ExtractedGame"):
    """Inspects the extraction directory and identifies whether the payload is a
    single game package (.pkg/.ffpfsc) or a game folder hierarchy containing eboot.bin / param.sfo / param.json.
    Returns (payload_type, payload_path, payload_name) where payload_type is 'file' or 'folder'."""
    valid_entries = []
    for e in os.listdir(extract_dir):
        if e.startswith(".") or e == "__MACOSX":
            continue
        valid_entries.append(os.path.join(extract_dir, e))

    if not valid_entries:
        raise TransferError("Archive extraction produced no usable files.")

    pkgs = []
    game_folders = []
    for root, dirs, files in os.walk(extract_dir):
        if "__MACOSX" in root:
            continue
        for f in files:
            if f.startswith("."):
                continue
            f_lower = f.lower()
            if f_lower.endswith((".pkg", ".ffpfsc")):
                pkgs.append(os.path.join(root, f))
            if f_lower in ("eboot.bin", "param.sfo", "param.json", "app.xml"):
                p = Path(root)
                if p.name in ("sce_sys", "decrypted", "sce_module", "sce_pfs"):
                    game_folders.append(str(p.parent))
                else:
                    game_folders.append(str(p))

    # Single package file (e.g. game.pkg)
    if len(pkgs) == 1 and not game_folders:
        pkg_p = pkgs[0]
        return "file", pkg_p, os.path.basename(pkg_p)

    # Recognised PS5 / PS4 game folder hierarchy
    if game_folders:
        best_folder = min(set(game_folders), key=lambda x: len(Path(x).parts))
        return "folder", best_folder, os.path.basename(best_folder)

    # Single top-level directory
    if len(valid_entries) == 1 and os.path.isdir(valid_entries[0]):
        return "folder", valid_entries[0], os.path.basename(valid_entries[0])

    # Multiple files or folders at root of extraction
    return "folder", extract_dir, fallback_name


def cleanup_stale_staging_directories():
    """Removes any abandoned ps5_staged_* directories in the temporary directory older than 1 hour."""
    tmp_base = tempfile.gettempdir()
    now = time.time()
    try:
        for entry in os.listdir(tmp_base):
            if entry.startswith("ps5_staged_"):
                full_p = os.path.join(tmp_base, entry)
                try:
                    if os.path.isdir(full_p):
                        mtime = os.path.getmtime(full_p)
                        if now - mtime > 3600:  # 1 hour
                            shutil.rmtree(full_p, ignore_errors=True)
                except Exception:
                    pass
    except Exception:
        pass


def transfer_staged_archive(job, settings, token, report, save):
    """Downloads (if URL) or stages a local compressed archive, unpacks it using the bundled unar
    tool with automatic or user-specified password decryption, discovers the game payload
    (folder or single package), and recursively transfers it to the PS5 over FTP.
    Guarantees zero storage leaks by removing the staging directory in a finally block."""
    unar_bin = find_unar_tool()
    if not unar_bin:
        raise TransferError("Extraction tool (unar) not found on this system. Cannot extract archive.")

    staging_dir = tempfile.mkdtemp(prefix="ps5_staged_")
    report("status", "Preparing isolated staging extraction cache")
    try:
        if job.get("kind") == "url":
            raw_src = probe_source("url", job["source"], token, decompress=False)
            if raw_src.location != job["source"]:
                job["source"] = raw_src.location
            total_size = raw_src.size
            job["total"] = total_size
            archive_name = valid_name(raw_src.filename or job.get("name") or "archive.bin")
            archive_path = os.path.join(staging_dir, archive_name)

            # Check free Mac disk space
            try:
                free_disk = shutil.disk_usage(staging_dir).free
                needed_disk = (total_size or 0) * 2.5
                if total_size and needed_disk > free_disk:
                    raise TransferError(
                        f"Insufficient Mac storage for extraction: requires {format_bytes(needed_disk)} free disk space, but only {format_bytes(free_disk)} available."
                    )
            except OSError:
                pass

            report("status", f"Downloading archive ({format_bytes(0)} / {format_bytes(total_size)})")
            report("source", {
                "ranges": raw_src.ranges,
                "resumable": raw_src.resumable(),
                "size": total_size,
                "is_zip": False,
                "is_archive": True,
                "archive_info": None,
                "is_folder": False
            })

            download_settings = {
                "buffer_mb": 32,
                "streams": 4,
                "chunk_mb": 8,
                **settings
            }
            meter = Meter()
            reader = make_reader(raw_src, 0, download_settings, token, meter)
            started = last = time.monotonic()
            hist = [(started, 0, 0)]
            downloaded = 0

            try:
                with open(archive_path, "wb") as f_out:
                    while not token.event.is_set():
                        token.check()
                        chunk = reader.read(256 * 1024)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        downloaded += len(chunk)
                        meter.add(downloaded=len(chunk))
                        now = time.monotonic()
                        if now - last >= 0.4:
                            snap = meter.snapshot()
                            _, down_rate = windowed_rates(hist, now, snap)
                            rem = max(0, total_size - downloaded) if total_size else 0
                            eta = rem / down_rate if (down_rate > 0 and total_size) else None
                            report("progress", {
                                "transferred": downloaded,
                                "total": total_size or downloaded,
                                "upload_bps": 0,
                                "download_bps": down_rate,
                                "buffered": 0,
                                "buffer_capacity": download_settings.get("buffer_mb", 32) * MIB,
                                "elapsed": now - started,
                                "eta": eta,
                                "bottleneck": "Download server",
                                "offset": 0,
                                "current_file": archive_name
                            })
                            job["transferred"] = downloaded
                            job["detail"] = f"Downloading archive ({format_bytes(downloaded)} / {format_bytes(total_size)})"
                            last = now
            finally:
                reader.close()

            if total_size and downloaded != total_size:
                raise TransferError(f"Download incomplete: expected {total_size} bytes, received {downloaded} bytes.")
        else:
            archive_path = os.path.abspath(os.path.expanduser(job["source"]))
            if not os.path.isfile(archive_path):
                raise TransferError(f"Local archive not found: {archive_path}")
            archive_name = os.path.basename(archive_path)

        report("status", f"Extracting {archive_name}")
        job["detail"] = "Extracting archive contents with password..."
        save()

        extract_dir = os.path.join(staging_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        pwd = (
            job.get("archive_password")
            or extract_password_hint(archive_name)
            or extract_password_hint(job.get("source", ""))
            or ""
        )

        cmd = [unar_bin, "-q", "-f", "-o", extract_dir]
        if pwd:
            cmd.extend(["-p", pwd])
        cmd.append(archive_path)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            while True:
                if token.event.is_set():
                    proc.kill()
                    token.check()
                try:
                    stdout, stderr = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            raise

        ret = proc.returncode
        if ret != 0:
            err = (stderr or "").strip() or (stdout or "").strip()
            if "password" in err.lower() or "encrypted" in err.lower():
                hint_str = f" (tried password '{pwd}')" if pwd else ""
                raise TransferError(f"Archive extraction failed: Password required or incorrect{hint_str}.")
            raise TransferError(f"Archive extraction failed (code {ret}): {err[:200]}")

        payload_type, payload_path, payload_name = find_extracted_payload(extract_dir, fallback_name=Path(archive_name).stem)

        dest_base = valid_folder(job.get("folder") or settings.get("folder") or "/data/ShadowMount")

        if payload_type == "folder":
            # If destination was /data/pkg but extracted payload is a game folder, route to /data/ShadowMount
            if dest_base == "/data/pkg":
                dest_base = "/data/ShadowMount"
            if dest_base.rstrip("/").endswith("/" + payload_name):
                target_ps5_folder = dest_base.rstrip("/")
            else:
                target_ps5_folder = f"{dest_base.rstrip('/')}/{payload_name}"

            folder_job = {
                **job,
                "kind": "folder",
                "source": payload_path,
                "name": payload_name,
                "folder": target_ps5_folder,
                "staged_extraction": False,
                "is_archive": False,
                "completed_files": job.get("completed_files", [])
            }
            report("status", f"Transferring {payload_name} to PS5")
            return transfer_folder(folder_job, settings, token, report, save)
        else:
            # Single package file (.pkg / .ffpfsc)
            if dest_base == "/data/ShadowMount" and payload_name.lower().endswith(".pkg"):
                dest_base = "/data/pkg"
            file_job = {
                **job,
                "kind": "local",
                "source": payload_path,
                "name": payload_name,
                "folder": dest_base,
                "decompress": False,
                "staged_extraction": False,
                "is_archive": False,
                "total": os.path.getsize(payload_path)
            }
            report("status", f"Transferring {payload_name} to PS5")
            return transfer(file_job, settings, token, report, save)

    finally:
        # Guarantee zero storage leak on Mac
        shutil.rmtree(staging_dir, ignore_errors=True)


def transfer(job, settings, token, report, save):
    """One transfer attempt. Partial files are uniquely owned by the persisted job ID."""
    if job.get("kind") == "folder":
        return transfer_folder(job, settings, token, report, save)

    report("status", "Inspecting source")

    # If explicitly marked for staged extraction or an encrypted archive
    if job.get("staged_extraction") and find_unar_tool():
        return transfer_staged_archive(job, settings, token, report, save)

    is_decomp = job.get("decompress", True)
    is_arch = (
        job.get("is_archive")
        or is_archive_candidate(job.get("name", ""))
        or is_archive_candidate(job.get("source", ""))
    )

    if is_decomp and is_arch:
        try:
            if job.get("kind") == "multipart":
                source = probe_multipart_source(job.get("parts") or job["source"], token, target_filename=job.get("name"))
            else:
                source = probe_source(job["kind"], job["source"], token, decompress=True)
            if not (source.kind.startswith("zip_") or source.kind.startswith("archive_")):
                if find_unar_tool():
                    return transfer_staged_archive(job, settings, token, report, save)
        except Exception:
            if find_unar_tool():
                return transfer_staged_archive(job, settings, token, report, save)
            raise
    else:
        if job.get("kind") == "multipart":
            source = probe_multipart_source(job.get("parts") or job["source"], token, target_filename=job.get("name"))
        else:
            source = probe_source(job["kind"], job["source"], token, decompress=job.get("decompress", True))
    if source.kind == "url" and source.location != job["source"]:
        job["source"] = source.location
    if (source.kind.startswith("zip_") or source.kind.startswith("archive_")) and source.filename:
        job["name"] = source.filename
        job["is_zip"] = True
        job["is_archive"] = True
        job["archive_info"] = source.archive_info
    elif source.filename and (
        job.get("name") in ("download.bin", "file", "view", "uc", "")
        or re.match(r'^[a-f0-9-]{16,}$', job.get("name", ""), re.IGNORECASE)
        or "." not in job.get("name", "")
    ):
        job["name"] = source.filename
    old_identity = job.get("identity")
    link_updated = job.pop("link_updated", False)
    if old_identity:
        if link_updated:
            if old_identity.get("size") is not None and source.size is not None and old_identity["size"] != source.size:
                raise TransferError(f"New link file size ({source.size} bytes) does not match original ({old_identity['size']} bytes). Restart the job.")
        elif old_identity != source.identity():
            raise TransferError("Source changed since this job started. Use Restart to begin a new partial file.")
    job["identity"] = source.identity()
    job["total"] = source.size
    job["resumable"] = source.resumable()
    save()
    report("source", {"ranges": source.ranges, "resumable": source.resumable(), "size": source.size,
                      "is_zip": (source.kind.startswith("zip_") or source.kind.startswith("archive_")),
                      "is_archive": (source.kind.startswith("zip_") or source.kind.startswith("archive_")),
                      "archive_info": source.archive_info})
    ftp = reader = data_socket = None
    clean = False
    meter = Meter()
    try:
        report("status", "Connecting to PS5")
        ftp = connect_ftp(settings, token)
        target_folder = valid_folder(job.get("folder") or settings["folder"])
        ensure_folder(ftp, target_folder)
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
        free_space = check_ftp_storage(ftp, target_folder)
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

            def on_part_change(idx, total, part_name):
                report("status", f"Streaming Part {idx+1}/{total} ({part_name})")

            reader = make_reader(source, offset, settings, token, meter, on_part_change=on_part_change)

            # Pre-buffer a cushion of data (e.g. 16-64 MiB) for URL/multipart streams before sending to PS5.
            # This prevents the initial starve-and-burst (sawtooth) cycle on cold start.
            if source.kind in ("url", "multipart") and source.size and (source.size - offset) > 16 * MIB:
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
        elif source.kind == "multipart":
            for p in (source.parts or []):
                if p.kind == "local" and probe_source("local", p.location, token).identity() != p.identity():
                    raise TransferError(f"Local part {p.filename} changed while uploading. Partial retained; restart with a stable file.")
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
