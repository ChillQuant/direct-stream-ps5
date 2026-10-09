"""Bounded, cancellable HTTP → FTP pipeline. Python 3.9+, standard library only."""
import concurrent.futures
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
    Cancelled,
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

DEFAULT_UNRAR_DIR = "/data/unrar"
DEFAULT_UNRAR_CONFIG_PATH = "/data/unrar/config.ini"
DEFAULT_UNRAR_LOG_PATH = "/data/unrar/unrar.log"
DEFAULT_ETA_HEN_PAYLOAD_PORT = 9021

class TransferError(Exception):
    """A problem that needs user action; don't blindly retry it."""

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
            old_origin = urllib.parse.urlsplit(req.full_url)
            new_origin = urllib.parse.urlsplit(newurl)
            cross_origin = (old_origin.scheme, old_origin.hostname, old_origin.port) != (new_origin.scheme, new_origin.hostname, new_origin.port)
            sensitive = {"authorization", "cookie", "proxy-authorization", "x-page-token"}
            if cross_origin:
                for key in list(new_req.headers):
                    if key.lower() in sensitive:
                        new_req.remove_header(key)
            # Preserve critical transfer headers (Range, User-Agent, Referer, Accept, etc.) across redirect
            for k, v in req.headers.items():
                if k.lower() not in ("host", "content-length") and not (cross_origin and k.lower() in sensitive):
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
                pwd_h = extract_password_hint(path) or extract_password_hint(filename)
                meta = inspect_archive("local", path, password=pwd_h)
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
                pwd_h = extract_password_hint(final_url) or extract_password_hint(filename)
                meta = inspect_archive("url", final_url, headers=arch_headers, total_size=size, password=pwd_h)
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

    if len(parts) > 100:
        raise TransferError("Cannot inspect more than 100 parts at once.")
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(parts))) as executor:
        futures = [executor.submit(_probe_part, i, p) for i, p in enumerate(parts)]
        for f in futures:
            f.result()

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
                                    r.close()
                                    break
                                new_url = urllib.parse.urljoin(curr_url, redir_loc)
                                if curr_url.startswith("https:") and not new_url.startswith("https:"):
                                    r.close()
                                    raise TransferError("Blocked an HTTPS-to-HTTP redirect. Use a secure direct link.")
                                old_origin = urllib.parse.urlsplit(curr_url)
                                curr_url = new_url
                                p = urllib.parse.urlsplit(curr_url)
                                new_origin = p
                                cross_origin = (old_origin.scheme, old_origin.hostname, old_origin.port) != (new_origin.scheme, new_origin.hostname, new_origin.port)
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
                                headers = dict(self.base_headers)
                                if cross_origin:
                                    sensitive = {"authorization", "cookie", "proxy-authorization", "x-page-token"}
                                    headers = {k: v for k, v in headers.items() if k.lower() not in sensitive}
                                headers["Range"] = f"bytes={start}-{end}"
                                if self.source.etag or self.source.modified:
                                    headers["If-Range"] = self.source.etag or self.source.modified
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


def _missing_if_sentinel(n):
    if n is None or n < 0 or n >= (1 << 63):
        return None
    return n


def remote_size(ftp, path):
    try:
        n = ftp.size(path)
        if n is None:
            raise TransferError("PS5 FTP did not return a file size.")
        return _missing_if_sentinel(n)
    except ftplib.error_perm as e:
        msg = str(e)
        if "ASCII" in msg:
            try:
                ftp.voidcmd("TYPE I")
                return _missing_if_sentinel(ftp.size(path))
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


def format_duration(seconds):
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"


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

        current_remote_dir = None
        ensured_dirs = set()

        for idx, (rel_p, abs_p, file_sz) in enumerate(file_entries):
            token.check()
            if rel_p in completed_set:
                continue

            rel_dir = os.path.dirname(rel_p)
            file_name = os.path.basename(rel_p)
            remote_dir = f"{base_target_folder.rstrip('/')}/{rel_dir}" if rel_dir else base_target_folder

            if remote_dir != current_remote_dir:
                if remote_dir in ensured_dirs:
                    ftp.cwd(remote_dir)
                else:
                    ensure_folder(ftp, remote_dir)
                    ensured_dirs.add(remote_dir)
                current_remote_dir = remote_dir

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
            token.check()
            ftp.voidresp()

            token.check()
            actual = remote_size(ftp, part_name)
            if actual != file_sz:
                raise TransferError(f"Size mismatch on {rel_p}: expected {file_sz}, received {actual}.")

            if job.get("overwrite") and remote_size(ftp, file_name) is not None:
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


def find_unrar_ps5_payload():
    """Find the path to the unrar_ps5.elf executable (bundled in app, local bin, or resources)."""
    res_dir = os.environ.get("RESOURCEPATH")
    if res_dir:
        p = os.path.join(res_dir, "bin", "unrar_ps5.elf")
        if os.path.isfile(p):
            return p

    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "bin", "unrar_ps5.elf"),
        os.path.join(base, "PS5 Direct Streamer.app", "Contents", "Resources", "bin", "unrar_ps5.elf"),
        os.path.join(os.path.dirname(base), "Resources", "bin", "unrar_ps5.elf"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def send_ps5_payload(host, port=9021, elf_path=None, elf_bytes=None, timeout=10):
    """Sends an ELF payload (e.g. unrar_ps5.elf) over TCP to the PS5 payload loader port (e.g. 9021 for etaHEN)."""
    if not host:
        raise TransferError("PS5 host address is required to inject payload.")
    if elf_bytes is None:
        if not elf_path:
            elf_path = find_unrar_ps5_payload()
        if not elf_path or not os.path.isfile(elf_path):
            raise TransferError("PS5 unrar payload binary (unrar_ps5.elf) not found.")
        with open(elf_path, "rb") as f:
            elf_bytes = f.read()

    if not elf_bytes:
        raise TransferError("ELF payload is empty.")

    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.sendall(elf_bytes)
    except (socket.error, OSError) as e:
        raise TransferError(
            f"Failed to connect to PS5 payload loader at {host}:{port}: {e}. "
            f"Ensure etaHEN or ELF loader is active on port {port} on your PS5."
        )
    return True


def generate_unrar_config(filename="", archive_location="/data/unrar", extract_location="/data/homebrew",
                          password=None, delete_after=True, progress=10, threads=0, nice=-20, cpu_mask=0):
    """Generates the config.ini content for bizkut/unrar-ps5."""
    if password and ("\r" in password or "\n" in password):
        raise TransferError("Archive password cannot contain line breaks.")
    if filename and ("\r" in filename or "\n" in filename or ".." in filename or filename.startswith("/")):
        raise TransferError("Invalid archive filename for console unrar.")
    lines = [
        f"filename={filename or ''}",
        f"archive_location={archive_location}",
        f"archive_password={password or ''}",
        f"delete_after={'1' if delete_after else '0'}",
        f"extract_location={extract_location}",
        f"threads={int(threads)}",
        f"nice={int(nice)}",
        f"cpu_mask={cpu_mask}",
        f"progress={max(1, min(100, int(progress)))}",
        ""
    ]
    return "\n".join(lines)


def fetch_ps5_unrar_log(settings, max_bytes=65536):
    """Fetches /data/unrar/unrar.log from PS5 over FTP if it exists."""
    tok = StopToken()
    ftp = None
    try:
        ftp = connect_ftp(settings, tok)
        size = remote_size(ftp, "/data/unrar/unrar.log")
        if size is None or size <= 0:
            return "No log found at /data/unrar/unrar.log on PS5."
        buf = io.BytesIO()
        rest = max(0, size - max_bytes) if size > max_bytes else None
        ftp.retrbinary("RETR /data/unrar/unrar.log", buf.write, rest=rest)
        return buf.getvalue().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Could not read /data/unrar/unrar.log: {e}"
    finally:
        if ftp:
            close_ftp(ftp, tok, clean=False)


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


def cleanup_stale_staging_directories(custom_dir=None):
    """Removes any abandoned ps5_staged_* directories in the temporary directory older than 1 hour."""
    search_dirs = [tempfile.gettempdir()]
    if custom_dir and os.path.isdir(custom_dir) and custom_dir not in search_dirs:
        search_dirs.append(custom_dir)
    now = time.time()
    for tmp_base in search_dirs:
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

    custom_staging = settings.get("staging_dir") or os.environ.get("PS5_STAGING_DIR")
    if custom_staging and os.path.isdir(custom_staging):
        staging_dir = tempfile.mkdtemp(prefix="ps5_staged_", dir=custom_staging)
    else:
        staging_dir = tempfile.mkdtemp(prefix="ps5_staged_")
    report("status", "Preparing isolated staging extraction cache")
    staged_part_files = []
    archive_path = None
    archive_name = ""
    try:
        if job.get("kind") == "multipart" or job.get("parts"):
            parts = list(job.get("parts") or [])
            if not parts:
                raise TransferError("No parts provided for multi-part archive extraction.")

            normalized_parts = []
            for p in parts:
                if isinstance(p, dict):
                    p_src = p.get("source", "")
                    p_kind = p.get("kind", "url" if str(p_src).startswith(("http://", "https://")) else "local")
                    p_name = p.get("name") or valid_name(os.path.basename(str(p_src)) or "part.bin")
                else:
                    p_src = str(p)
                    p_kind = "url" if p_src.startswith(("http://", "https://")) else "local"
                    p_name = valid_name(os.path.basename(p_src) or "part.bin")
                normalized_parts.append({"source": p_src, "kind": p_kind, "name": p_name})

            is_seq, _, sorted_parts = detect_multipart_sequence(normalized_parts)
            use_parts = sorted_parts if is_seq else normalized_parts

            part_infos = []
            for p in use_parts:
                token.check()
                info = probe_source(p["kind"], p["source"], token, decompress=False)
                part_infos.append((p, info))

            total_size = sum((info.size or 0) for _, info in part_infos)
            job["total"] = total_size

            try:
                free_disk = shutil.disk_usage(staging_dir).free
                needed_disk = (total_size or 0) * 1.5
                if total_size and needed_disk > free_disk:
                    raise TransferError(
                        f"Insufficient Mac storage for local extraction: requires {format_bytes(needed_disk)} free disk space, but only {format_bytes(free_disk)} available. "
                        f"To stream the raw archive directly to PS5 with 0 GB Mac disk usage, click '···' on this item and choose 'Disable extraction'."
                    )
            except OSError:
                pass

            download_settings = {
                "buffer_mb": 32,
                "streams": 4,
                "chunk_mb": 8,
                **settings
            }
            meter = Meter()
            cumulative_downloaded = 0
            started = last = time.monotonic()
            hist = [(started, 0, 0)]

            for idx, (p, info) in enumerate(part_infos):
                token.check()
                part_fname = valid_name(p.get("name") or info.filename or f"part_{idx+1}.bin")
                target_p = os.path.join(staging_dir, part_fname)
                staged_part_files.append((part_fname, target_p))

                if p["kind"] == "local":
                    local_abs = os.path.abspath(os.path.expanduser(p["source"]))
                    if not os.path.isfile(local_abs):
                        raise TransferError(f"Local part file not found: {local_abs}")
                    try:
                        os.symlink(local_abs, target_p)
                    except OSError:
                        shutil.copy2(local_abs, target_p)
                    cumulative_downloaded += info.size or 0
                else:
                    report("status", f"Downloading part {idx+1}/{len(part_infos)} ({part_fname})")
                    reader = make_reader(info, 0, download_settings, token, meter)
                    part_downloaded = 0
                    try:
                        with open(target_p, "wb") as f_out:
                            while not token.event.is_set():
                                token.check()
                                chunk = reader.read(1024 * 1024)
                                if not chunk:
                                    break
                                f_out.write(chunk)
                                part_downloaded += len(chunk)
                                meter.add(downloaded=len(chunk))
                                now = time.monotonic()
                                if now - last >= 0.4:
                                    snap = meter.snapshot()
                                    _, down_rate = windowed_rates(hist, now, snap)
                                    cur_tot = cumulative_downloaded + part_downloaded
                                    rem = max(0, total_size - cur_tot) if total_size else 0
                                    eta = rem / down_rate if (down_rate > 0 and total_size) else None
                                    report("progress", {
                                        "transferred": cur_tot,
                                        "total": total_size or cur_tot,
                                        "upload_bps": 0,
                                        "download_bps": down_rate,
                                        "buffered": 0,
                                        "buffer_capacity": download_settings.get("buffer_mb", 32) * MIB,
                                        "elapsed": now - started,
                                        "eta": eta,
                                        "bottleneck": "Download server",
                                        "offset": cumulative_downloaded,
                                        "current_file": part_fname,
                                        "file_index": idx + 1,
                                        "file_count": len(part_infos)
                                    })
                                    job["transferred"] = cur_tot
                                    job["detail"] = f"Downloading parts ({idx+1}/{len(part_infos)}): {part_fname}"
                                    last = now
                    finally:
                        reader.close()
                    cumulative_downloaded += part_downloaded

            # Select primary archive file (.zip if present, or first sorted part)
            archive_name, archive_path = next(
                ((n, p) for n, p in staged_part_files if n.lower().endswith(".zip")),
                staged_part_files[0]
            )

        elif job.get("kind") == "url":
            raw_src = probe_source("url", job["source"], token, decompress=False)
            if raw_src.location != job["source"]:
                job["source"] = raw_src.location
            total_size = raw_src.size
            job["total"] = total_size
            archive_name = valid_name(raw_src.filename or job.get("name") or "archive.bin")
            archive_path = os.path.join(staging_dir, archive_name)
            staged_part_files.append((archive_name, archive_path))

            # Check free Mac disk space
            try:
                free_disk = shutil.disk_usage(staging_dir).free
                needed_disk = (total_size or 0) * 1.5
                if total_size and needed_disk > free_disk:
                    raise TransferError(
                        f"Insufficient Mac storage for local extraction: requires {format_bytes(needed_disk)} free disk space, but only {format_bytes(free_disk)} available. "
                        f"To stream the raw archive directly to PS5 with 0 GB Mac disk usage, click '···' on this item and choose 'Disable extraction'."
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
                        chunk = reader.read(1024 * 1024)
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

        cmd = [unar_bin, "-q", "-f", "-nq", "-k", "skip", "-o", extract_dir]
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
                job["archive_encrypted"] = True
                raise TransferError("Archive extraction failed: Password required or incorrect.")
            raise TransferError(f"Archive extraction failed (code {ret}): {err[:200]}")

        # Free downloaded archive files immediately before uploading payload to PS5 (never touch original files outside staging)
        staging_abs = os.path.abspath(staging_dir)
        try:
            if archive_path and os.path.isfile(archive_path) and os.path.abspath(archive_path).startswith(staging_abs):
                os.remove(archive_path)
            for _, p_path in staged_part_files:
                if os.path.isfile(p_path) and not os.path.islink(p_path) and os.path.abspath(p_path).startswith(staging_abs):
                    try:
                        os.remove(p_path)
                    except OSError:
                        pass
        except OSError:
            pass

        payload_type, payload_path, payload_name = find_extracted_payload(extract_dir, fallback_name=Path(archive_name).stem)

        dest_base = valid_folder(job.get("folder") or settings.get("folder") or "/data/homebrew")

        if payload_type == "folder":
            # If destination was /data/pkg but extracted payload is a game folder, route to /data/homebrew
            if dest_base == "/data/pkg":
                dest_base = "/data/homebrew"
            if dest_base.rstrip("/").endswith("/" + payload_name):
                target_ps5_folder = dest_base.rstrip("/")
            else:
                target_ps5_folder = f"{dest_base.rstrip('/')}/{payload_name}"

            job["kind"] = "folder"
            job["source"] = payload_path
            job["extracted_name"] = payload_name
            job["folder"] = target_ps5_folder
            job["staged_extraction"] = False
            job["is_archive"] = False
            report("status", f"Transferring {payload_name} to PS5")
            res = transfer_folder(job, settings, token, report, save)
            job["transferred"] = job.get("total", job.get("transferred", 0))
            save()
            return res
        else:
            # Single package file (.pkg / .ffpfsc)
            if dest_base in ("/data/ShadowMount", "/data/homebrew") and payload_name.lower().endswith(".pkg"):
                dest_base = "/data/pkg"
            job["kind"] = "local"
            job["source"] = payload_path
            job["extracted_name"] = payload_name
            job["folder"] = dest_base
            job["decompress"] = False
            job["staged_extraction"] = False
            job["is_archive"] = False
            job["total"] = os.path.getsize(payload_path)
            report("status", f"Transferring {payload_name} to PS5")
            res = transfer(job, settings, token, report, save)
            job["transferred"] = job.get("total", job.get("transferred", 0))
            save()
            return res

    finally:
        # Guarantee zero storage leak on Mac
        shutil.rmtree(staging_dir, ignore_errors=True)


def stream_file_to_ftp(ftp, source, name, job, settings, token, report, save,
                       meter=None, hist=None, started=None, cumulative_offset=0, total_bytes=None,
                       file_label="", on_part_change=None, is_staging=False):
    """Streams a single SourceInfo or reader-compatible source directly to FTP in the current folder,
    handling .ps5part temporary naming, resume checks, async queue buffering, speed calculations,
    remote size verification, and final rename. Uses 0 bytes of Mac disk space."""
    if meter is None:
        meter = Meter()
    if started is None:
        started = time.monotonic()
    last = started
    if hist is None:
        previous = meter.snapshot()
        hist = [(started, previous["uploaded"], previous["downloaded"])]

    part = f"{name}.{job['id']}.ps5part"
    existing = remote_size(ftp, name)
    if existing is not None:
        if is_staging or job.get("overwrite"):
            try:
                ftp.delete(name)
            except Exception:
                pass
            existing = None
        else:
            try:
                cur_dir = ftp.pwd()
            except Exception:
                cur_dir = ""
            dest_loc = f"{cur_dir}/{name}" if cur_dir else name
            raise TransferError(f"Destination file already exists on PS5 ({dest_loc}). Rename this job or explicitly enable replacement in a new job.")

    partial_size = remote_size(ftp, part)
    if (job.get("overwrite") or is_staging) and partial_size is not None:
        if job.get("overwrite") or not job.get("stage_owned") or not source.resumable() or (source.size is not None and partial_size > source.size):
            try:
                ftp.delete(part)
            except Exception:
                pass
            partial_size = None

    if partial_size is not None and partial_size > 0:
        if not job.get("stage_owned"):
            raise TransferError("Found a partial file without source history; restart this job.")
        if source.size is None or partial_size > source.size:
            raise TransferError("Partial file is larger than the source or source length is unknown. Restart the job.")
        if not source.resumable():
            raise TransferError("This link does not provide stable resume metadata. Restart, or download to your Mac and send the local file.")
        offset = partial_size
    else:
        offset = 0
    job["stage_owned"] = True
    save()

    data_socket = None
    reader = None
    transfer_success = False

    file_total = source.size
    display_total = total_bytes or file_total

    if file_total is None or offset < file_total or file_total == 0:
        report("status", f"Resuming at {offset} bytes" if offset else (f"Transferring {file_label}" if file_label else "Transferring"))
        try:
            data_socket = token.track(ftp.transfercmd("STOR " + part, rest=offset or None))
        except ftplib.error_perm as e:
            raise TransferError("PS5 FTP refused upload/resume. Confirm write access and REST support, or restart the job.") from e
        try:
            data_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * MIB)
        except OSError:
            pass
        data_socket.settimeout(30)

        reader = make_reader(source, offset, settings, token, meter, on_part_change=on_part_change)

        if source.kind in ("url", "multipart") and source.size and (source.size - offset) > 16 * MIB:
            prebuffer_target = min(64 * MIB, max(16 * MIB, (settings["buffer_mb"] * MIB) // 4))
            t_pre = time.monotonic()
            report("status", "Pre-buffering pipeline…")
            while not token.event.is_set():
                snap = meter.snapshot()
                if snap["buffered"] >= prebuffer_target or (time.monotonic() - t_pre) > 3.0:
                    break
                token.wait(0.1)
            report("status", f"Resuming at {offset} bytes" if offset else (f"Transferring {file_label}" if file_label else "Transferring"))

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
                    cap = settings.get("limit_mbps", 0) * 1_000_000
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
                overall_transferred = cumulative_offset + transferred
                job["transferred"] = overall_transferred
                now = time.monotonic()
                if now - last >= 0.4:
                    snap = meter.snapshot()
                    up, down = windowed_rates(hist, now, snap)
                    capacity = settings["buffer_mb"] * MIB
                    bottleneck = "Speed cap enabled" if settings.get("limit_mbps") else (
                        "Waiting for source" if snap["buffered"] < capacity * 0.25 else "PS5 / local network")
                    rem = (display_total - overall_transferred) if display_total else 0
                    report("progress", {
                        "transferred": overall_transferred,
                        "total": display_total or overall_transferred,
                        "upload_bps": up,
                        "download_bps": down,
                        "buffered": snap["buffered"],
                        "buffer_capacity": settings["buffer_mb"] * MIB,
                        "elapsed": now - started,
                        "eta": rem / up if display_total and up else None,
                        "bottleneck": bottleneck,
                        "offset": cumulative_offset + offset,
                        "current_file": file_label or name,
                    })
                    last = now
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
            token.check()
            transfer_success = True
        finally:
            if sender_thread.is_alive():
                try:
                    send_q.put_nowait(None)
                except Exception:
                    pass
                sender_thread.join(timeout=1.0)
            if reader:
                reader.close()
                reader = None
            if data_socket:
                token.untrack(data_socket)
                data_socket.close()
                data_socket = None
            if transfer_success:
                ftp.voidresp()
            else:
                try:
                    ftp.voidresp()
                except Exception:
                    pass

        if source.size is not None and transferred != source.size:
            raise TransferError("Source byte count did not match. Incomplete file retained.")

    token.check()
    report("status", f"Verifying PS5 file size for {name}")
    actual = remote_size(ftp, part)
    expected = source.size if source.size is not None else transferred
    if actual != expected:
        raise TransferError(f"PS5 size mismatch for {name}: expected {expected} bytes, received {actual}. Partial retained; do not use it.")

    if source.kind == "local":
        curr_id = probe_source("local", source.location, token, decompress=False).identity()
        orig_id = source.identity()
        if curr_id.get("size") != orig_id.get("size"):
            raise TransferError("Local file changed while uploading. Partial retained; restart with a stable file.")
    elif source.kind == "multipart":
        for p in (source.parts or []):
            if p.kind == "local":
                curr_p_id = probe_source("local", p.location, token, decompress=False).identity()
                orig_p_id = p.identity()
                if curr_p_id.get("size") != orig_p_id.get("size"):
                    raise TransferError(f"Local part {p.filename} changed while uploading. Partial retained; restart with a stable file.")

    if not job.get("overwrite") and not is_staging and remote_size(ftp, name) is not None:
        raise TransferError(f"Destination appeared during upload for {name}. Verified partial retained to avoid replacing it.")
    if (job.get("overwrite") or is_staging) and remote_size(ftp, name) is not None:
        try:
            ftp.delete(name)
        except Exception:
            pass

    report("status", f"Finalizing {name}")
    try:
        ftp.rename(part, name)
    except ftplib.all_errors as e:
        raise TransferError(f"Upload size verified, but rename failed for {name}: {e}") from e

    return expected


def transfer_ps5_remote_archive(job, settings, token, report, save):
    """Streams single or multipart compressed archives directly to the PS5 internal SSD
    at /data/unrar/[filename] over FTP (using exactly 0 GB of Mac disk space),
    writes the /data/unrar/config.ini configuration file, and triggers the unrar_ps5.elf
    payload injection to the PS5 payload loader port (default 9021) for high-speed on-console extraction."""
    ftp = None
    clean = False
    meter = Meter()

    # Determine files to upload
    files_to_upload = []
    primary_archive_name = ""

    if job.get("kind") == "multipart" or job.get("parts"):
        parts = list(job.get("parts") or [])
        if not parts:
            raise TransferError("No parts provided for multi-part archive transfer.")

        normalized_parts = []
        for p in parts:
            if isinstance(p, dict):
                p_src = p.get("source", "")
                p_kind = p.get("kind", "url" if str(p_src).startswith(("http://", "https://")) else "local")
                p_name = p.get("name") or valid_name(os.path.basename(str(p_src)) or "part.bin")
            else:
                p_src = str(p)
                p_kind = "url" if p_src.startswith(("http://", "https://")) else "local"
                p_name = valid_name(os.path.basename(p_src) or "part.bin")
            normalized_parts.append({"source": p_src, "kind": p_kind, "name": p_name})

        is_seq, _, sorted_parts = detect_multipart_sequence(normalized_parts)
        use_parts = sorted_parts if is_seq else normalized_parts

        seen_names = set()
        for idx, p in enumerate(use_parts):
            token.check()
            info = probe_source(p["kind"], p["source"], token, decompress=False)
            base_fname = valid_name(p.get("name") or info.filename or f"part_{idx+1}.bin")
            p_fname = base_fname
            if p_fname in seen_names:
                stem, ext = os.path.splitext(base_fname)
                p_fname = valid_name(f"{stem}.part{idx+1}{ext}")
                count = 1
                while p_fname in seen_names:
                    p_fname = valid_name(f"{stem}.part{idx+1}_{count}{ext}")
                    count += 1
            seen_names.add(p_fname)
            files_to_upload.append({"name": p_fname, "info": info, "kind": p["kind"], "source": p["source"]})

        primary_archive_name = files_to_upload[0]["name"]
        total_size = sum((item["info"].size or 0) for item in files_to_upload)
    else:
        raw_kind = job.get("kind", "url")
        raw_src = probe_source(raw_kind, job["source"], token, decompress=False)
        if raw_src.kind == "url" and raw_src.location != job.get("source"):
            job["source"] = raw_src.location
        archive_name = valid_name(raw_src.filename or job.get("name") or "archive.bin")
        primary_archive_name = archive_name
        total_size = raw_src.size
        files_to_upload.append({"name": archive_name, "info": raw_src, "kind": raw_kind, "source": job["source"]})

    job["total"] = total_size
    job["resumable"] = all(item["info"].resumable() for item in files_to_upload)
    save()

    report("source", {
        "ranges": all(item["info"].ranges for item in files_to_upload),
        "resumable": job["resumable"],
        "size": total_size,
        "is_zip": False,
        "is_archive": True,
        "archive_info": None,
        "is_folder": False
    })

    try:
        report("status", "Connecting to PS5 FTP (/data/unrar)")
        ftp = connect_ftp(settings, token)
        unrar_base = "/data/unrar"
        ensure_folder(ftp, unrar_base)
        ftp.cwd(unrar_base)

        free_space = check_ftp_storage(ftp, unrar_base)
        if free_space is not None and total_size:
            if total_size > free_space:
                raise TransferError(
                    f"Insufficient PS5 disk space: needs {total_size/1e9:.2f} GB in /data/unrar, but only {free_space/1e9:.2f} GB is available."
                )

        started = time.monotonic()
        previous = meter.snapshot()
        hist = [(started, previous["uploaded"], previous["downloaded"])]
        cumulative_transferred = 0

        for file_idx, f_item in enumerate(files_to_upload):
            token.check()
            file_name = f_item["name"]
            src_info = f_item["info"]
            file_size = src_info.size or 0

            existing = remote_size(ftp, file_name)
            if existing is not None and existing == file_size and not job.get("overwrite"):
                report("status", f"Staged {file_name} already complete in /data/unrar ({file_idx+1}/{len(files_to_upload)})")
                cumulative_transferred += file_size
                continue

            if existing is not None:
                try:
                    ftp.delete(file_name)
                except Exception:
                    pass

            report("status", f"Streaming {file_name} directly to PS5 ({file_idx+1}/{len(files_to_upload)})")
            f_transferred = stream_file_to_ftp(
                ftp, src_info, file_name, job, settings, token, report, save,
                meter=meter, hist=hist, started=started,
                cumulative_offset=cumulative_transferred, total_bytes=total_size,
                file_label=file_name, is_staging=True
            )
            cumulative_transferred += (f_transferred or file_size)

        # Generate and upload /data/unrar/config.ini
        pwd = (
            job.get("archive_password")
            or extract_password_hint(primary_archive_name)
            or extract_password_hint(job.get("source", ""))
            or ""
        )
        extract_loc = valid_folder(
            job.get("unrar_extract_location")
            or settings.get("unrar_extract_location")
            or job.get("folder")
            or "/data/homebrew"
        )
        if extract_loc == "/data/unrar":
            extract_loc = "/data/homebrew"

        del_after = bool(job.get("unrar_delete_after", settings.get("unrar_delete_after", True)))
        cfg_text = generate_unrar_config(
            filename=primary_archive_name,
            archive_location="/data/unrar",
            extract_location=extract_loc,
            password=pwd,
            delete_after=del_after
        )

        report("status", "Writing /data/unrar/config.ini on PS5")
        try:
            ftp.cwd(unrar_base)
            cfg_bytes = cfg_text.encode("utf-8")
            ftp.storbinary("STOR config.ini", io.BytesIO(cfg_bytes))
        except ftplib.all_errors as e:
            raise TransferError(f"Failed to write /data/unrar/config.ini on PS5: {e}")

        # Inject payload if auto_payload enabled
        auto_payload = bool(job.get("unrar_auto_payload", settings.get("unrar_auto_payload", True)))
        payload_port = int(settings.get("payload_port", 9021))
        payload_msg = ""

        if auto_payload and settings.get("host"):
            log_path = "/data/unrar/unrar.log"
            log_offset = remote_size(ftp, log_path) or 0

            report("status", f"Sending unrar-ps5 payload to port {payload_port}")
            try:
                send_ps5_payload(settings["host"], payload_port)
                payload_msg = f"Payload launched on port {payload_port}."
            except Exception as pe:
                payload_msg = f"Archive uploaded to /data/unrar. Payload injection notice: {pe}"

            if "Payload launched" in payload_msg:
                report("status", f"PS5 is extracting {primary_archive_name}...")
                job["transferred"] = total_size or cumulative_transferred
                job["total"] = total_size or cumulative_transferred
                job["detail"] = f"Extracting on PS5 (0% · unpacking {primary_archive_name})..."
                save()

                stage_dir = f"{extract_loc}/.unrar-staging"
                extract_done = False
                extract_failed = False
                failure_reason = ""
                extracted_title_id = ""
                extracted_final_path = ""
                poll_count = 0
                extract_start_time = time.monotonic()

                # Poll unrar log and staging size until completion
                while not extract_done:
                    token.check()
                    time.sleep(2.0)
                    poll_count += 1

                    # 1. Fetch newly appended lines from unrar.log via existing FTP connection
                    new_lines = []
                    try:
                        buf = io.BytesIO()
                        ftp.retrbinary(f"RETR {log_path}", buf.write, rest=log_offset)
                        appended = buf.getvalue()
                        if appended:
                            log_offset += len(appended)
                            new_text = appended.decode("utf-8", errors="replace")
                            new_lines = [ln.strip() for ln in new_text.splitlines() if ln.strip()]
                    except Exception:
                        pass

                    # 2. Check for completion or error markers in newly appended log lines
                    for line in new_lines:
                        # Success with metadata: done archive=...
                        if line.startswith("done archive="):
                            extract_done = True
                            m_tid = re.search(r"title_id=([^\s]+)", line)
                            if m_tid:
                                extracted_title_id = m_tid.group(1)
                            m_fp = re.search(r"final_path=([^\s]+)", line)
                            if m_fp:
                                extracted_final_path = m_fp.group(1)
                            break

                        # Extract result line: code=0 is success, non-zero is error
                        if "extract_result" in line:
                            m_code = re.search(r"code=(\d+)", line)
                            m_reason = re.search(r"reason=([^\s]+)", line)
                            code = int(m_code.group(1)) if m_code else -1
                            reason = m_reason.group(1) if m_reason else "unknown"
                            if code == 0 or reason == "success":
                                extract_done = True
                            else:
                                extract_done = True
                                extract_failed = True
                                failure_reason = f"Extraction failed (code={code}, reason={reason})"
                            break

                        # Already installed check
                        if line.startswith("skip archive="):
                            extract_done = True
                            break

                        # Normalization issue (archive unpacked fine, but title id path needs Python move)
                        if line.startswith("normalize_error"):
                            extract_done = True
                            break

                        # Hard errors
                        if line.startswith(("archive_error", "extract_error", "sidecar_config_error", "config_error", "UnRAR error:")):
                            extract_done = True
                            extract_failed = True
                            failure_reason = line
                            break

                    if extract_done:
                        break

                    # 3. Check if archive was deleted on PS5 (unrar-ps5 delete_after=1 deletes it upon success)
                    if poll_count % 3 == 0:
                        try:
                            unrar_files = ftp.nlst(unrar_base)
                            if primary_archive_name not in unrar_files:
                                extract_done = True
                                break
                        except Exception:
                            pass

                    # 4. Calculate live unpacked size, percentage, speed, and remaining time
                    unpacked_bytes = 0
                    try:
                        for item in ftp.mlsd(stage_dir):
                            n, facts = item
                            if n in ('.', '..'): continue
                            if facts.get('type') == 'file':
                                unpacked_bytes += int(facts.get('size', 0))
                            elif facts.get('type') == 'dir':
                                try:
                                    for sub_item in ftp.mlsd(f"{stage_dir}/{n}"):
                                        sn, sfacts = sub_item
                                        if sn in ('.', '..'): continue
                                        if sfacts.get('type') == 'file':
                                            unpacked_bytes += int(sfacts.get('size', 0))
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    elapsed = max(1.0, time.monotonic() - extract_start_time)
                    unpack_rate = unpacked_bytes / elapsed if unpacked_bytes > 0 else 0
                    pct = min(99, int((unpacked_bytes / total_size) * 100)) if total_size and unpacked_bytes > 0 else 0
                    rem_sec = max(0, int((total_size - unpacked_bytes) / max(1.0, unpack_rate))) if total_size and unpack_rate > 100000 else None

                    speed_str = f" · {format_bytes(unpack_rate)}/s" if unpack_rate > 100000 else ""
                    eta_str = f" · {format_duration(rem_sec)} left" if rem_sec and rem_sec > 0 else ""

                    if pct > 0:
                        clean_detail = f"Extracting on PS5 ({pct}% · {format_bytes(unpacked_bytes)} unpacked{speed_str}{eta_str})"
                        status_str = f"Extracting on PS5 ({pct}%)"
                    else:
                        clean_detail = "Extracting on PS5 (Unpacking archive...)"
                        status_str = "Extracting on PS5..."

                    job["detail"] = clean_detail
                    report("status", status_str)
                    report("progress", {
                        "transferred": total_size or cumulative_transferred,
                        "total": total_size or cumulative_transferred,
                        "upload_bps": 0,
                        "download_bps": 0,
                        "eta": rem_sec,
                        "bottleneck": "PS5 SSD Unpack"
                    })
                    save()

                if extract_failed:
                    raise TransferError(f"PS5 unrar extraction failed: {failure_reason}")

                # Extraction finished! Check if staging directory still has unnormalized content
                report("status", "Finalizing game files on PS5...")
                try:
                    staged_entries = [e for e in ftp.nlst(stage_dir) if e not in (".", "..")]
                    if staged_entries:
                        for se in staged_entries:
                            se_path = f"{stage_dir}/{se}"
                            try:
                                sub_entries = [s for s in ftp.nlst(se_path) if s not in (".", "..")]
                                for sub in sub_entries:
                                    if sub.endswith((".txt", ".nfo")):
                                        continue
                                    src_p = f"{se_path}/{sub}"
                                    dst_p = f"{extract_loc}/{sub}"
                                    ftp.rename(src_p, dst_p)
                            except Exception:
                                ftp.rename(se_path, f"{extract_loc}/{se}")

                        def _rm_r(p):
                            for item in ftp.mlsd(p):
                                n, f = item
                                if n in (".", ".."):
                                    continue
                                sub_p = f"{p}/{n}"
                                if f.get("type") == "dir":
                                    _rm_r(sub_p)
                                else:
                                    ftp.delete(sub_p)
                            ftp.rmd(p)

                        try:
                            _rm_r(stage_dir)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Clean up all uploaded parts if delete_after is enabled
                if del_after:
                    for f_item in files_to_upload:
                        try:
                            ftp.delete(f"{unrar_base}/{f_item['name']}")
                        except Exception:
                            pass

                loc_label = extracted_final_path or extract_loc
                tid_label = f" ({extracted_title_id})" if extracted_title_id else ""
                payload_msg = f"Extracted to {loc_label}{tid_label} · Archive cleaned up (0 GB Mac disk)"
                job["detail"] = payload_msg
                report("status", payload_msg)
                save()
        else:
            payload_msg = "Archive uploaded to /data/unrar. Ready for payload injection (config.ini saved)"

        clean = True
        job["transferred"] = total_size or cumulative_transferred
        job["total"] = total_size or cumulative_transferred
        job["detail"] = payload_msg
        save()

        report("complete", {
            "size": total_size or cumulative_transferred,
            "verification": f"Archive streamed to /data/unrar (0 GB Mac disk). {payload_msg}"
        })
        return True

    finally:
        close_ftp(ftp, token, clean)


def transfer(job, settings, token, report, save):
    """One transfer attempt. Partial files are uniquely owned by the persisted job ID."""
    if job.get("kind") == "folder":
        return transfer_folder(job, settings, token, report, save)

    report("status", "Inspecting source")

    extract_mode = str(job.get("extract_mode") or settings.get("extract_mode", "ps5")).lower()
    is_decomp = job.get("decompress", True) and extract_mode != "none"
    is_arch = (
        job.get("is_archive")
        or is_archive_candidate(job.get("name", ""))
        or is_archive_candidate(job.get("source", ""))
    )

    # 1. Explicit Mac local staging extraction via unar tool
    if is_decomp and (extract_mode == "mac" or job.get("staged_extraction")) and find_unar_tool():
        return transfer_staged_archive(job, settings, token, report, save)

    # 2. PS5 on-console extraction via unrar-ps5 payload (0 GB Mac disk)
    if is_decomp and extract_mode == "ps5" and is_arch:
        if job.get("kind") != "multipart" and not job.get("archive_encrypted") and not job.get("archive_password"):
            try:
                source = probe_source(job["kind"], job["source"], token, decompress=True)
                if source.kind.startswith("zip_") or source.kind.startswith("archive_"):
                    # On-the-fly streaming directly supported by zip_streamer (0 GB Mac disk)!
                    pass
                elif find_unrar_ps5_payload():
                    return transfer_ps5_remote_archive(job, settings, token, report, save)
            except Exception:
                if find_unrar_ps5_payload():
                    return transfer_ps5_remote_archive(job, settings, token, report, save)
                elif find_unar_tool():
                    return transfer_staged_archive(job, settings, token, report, save)
                raise
        elif find_unrar_ps5_payload():
            return transfer_ps5_remote_archive(job, settings, token, report, save)
        elif find_unar_tool():
            return transfer_staged_archive(job, settings, token, report, save)

    if is_decomp and is_arch:
        try:
            if job.get("kind") == "multipart":
                source = probe_multipart_source(job.get("parts") or job["source"], token, target_filename=job.get("name"))
            else:
                source = probe_source(job["kind"], job["source"], token, decompress=True)
            if not (source.kind.startswith("zip_") or source.kind.startswith("archive_")):
                if extract_mode == "ps5" and find_unrar_ps5_payload():
                    return transfer_ps5_remote_archive(job, settings, token, report, save)
                elif find_unar_tool():
                    return transfer_staged_archive(job, settings, token, report, save)
        except Exception:
            if extract_mode == "ps5" and find_unrar_ps5_payload():
                return transfer_ps5_remote_archive(job, settings, token, report, save)
            elif find_unar_tool():
                return transfer_staged_archive(job, settings, token, report, save)
            raise
    else:
        if job.get("kind") == "multipart":
            source = probe_multipart_source(job.get("parts") or job["source"], token, target_filename=job.get("name"))
        else:
            source = probe_source(job["kind"], job["source"], token, decompress=False)

    if source.kind == "url" and source.location != job.get("source"):
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

    ftp = None
    clean = False
    meter = Meter()
    try:
        report("status", "Connecting to PS5")
        ftp = connect_ftp(settings, token)
        target_folder = valid_folder(job.get("folder") or settings["folder"])
        ensure_folder(ftp, target_folder)
        ftp.cwd(target_folder)
        name = valid_name(job["name"])
        free_space = check_ftp_storage(ftp, target_folder)
        if free_space is not None and source.size is not None:
            partial_size = remote_size(ftp, f"{name}.{job['id']}.ps5part") or 0
            needed = max(0, source.size - partial_size)
            if needed > free_space:
                raise TransferError(f"Insufficient PS5 disk space: needs {needed/1e9:.2f} GB, but only {free_space/1e9:.2f} GB is available.")

        def on_part_change(idx, total, part_name):
            report("status", f"Streaming Part {idx+1}/{total} ({part_name})")

        expected = stream_file_to_ftp(
            ftp, source, name, job, settings, token, report, save,
            meter=meter, on_part_change=on_part_change
        )
        job["transferred"] = expected
        job["total"] = expected
        clean = True
        report("complete", {"size": expected, "verification": "Remote file size verified; not a cryptographic checksum."})
    finally:
        close_ftp(ftp, token, clean)

