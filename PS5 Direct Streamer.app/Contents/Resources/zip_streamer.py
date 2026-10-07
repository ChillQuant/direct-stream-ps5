"""
On-The-Fly Streaming Archive Decompression Engine for Direct Stream PS5.
Pure Python Standard Library (zipfile, zlib, struct, io, urllib). Zero external dependencies.

Enables streaming compressed ZIP archives directly over HTTP or local disk into PS5 FTP storage
in RAM without extracting onto local PC/phone disk.
"""

import io
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass

BLOCK = 1024 * 1024  # 1 MiB chunks
HEADERS = {"User-Agent": "DirectStreamPS5/2.9.0"}

ARCHIVE_EXTENSIONS = (
    ".zip", ".zip64",
    ".rar",
    ".7z",
    ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz",
)

_ARCHIVE_TOOL = None


class HttpRangeStream(io.RawIOBase):
    """
    Virtual seekable read-only stream over HTTP using Range requests.
    Caches 64 KiB chunks in memory so zipfile directory inspection
    completes in ~2-3 requests without downloading archive data.
    """
    def __init__(self, url, total_size, headers=None, chunk_size=65536, max_cache=32):
        self.url = url
        self.total_size = total_size
        self.headers = headers or HEADERS
        self.chunk_size = chunk_size
        self.max_cache = max_cache
        self.pos = 0
        self.cache = {}  # chunk_index -> bytes
        self.cache_order = []

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        elif whence == io.SEEK_END:
            self.pos = self.total_size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        return self.pos

    def _fetch_range(self, start, end):
        req = urllib.request.Request(self.url, headers={
            **self.headers,
            "Range": f"bytes={start}-{end}"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if resp.status == 206:
                return data
            return data[start : end + 1]

    def _get_chunk(self, chunk_idx):
        if chunk_idx in self.cache:
            return self.cache[chunk_idx]
        start = chunk_idx * self.chunk_size
        if start >= self.total_size:
            return b""
        end = min(self.total_size - 1, start + self.chunk_size - 1)
        data = self._fetch_range(start, end)
        if len(self.cache_order) >= self.max_cache:
            oldest = self.cache_order.pop(0)
            self.cache.pop(oldest, None)
        self.cache[chunk_idx] = data
        self.cache_order.append(chunk_idx)
        return data

    def readinto(self, b):
        if self.pos >= self.total_size:
            return 0
        chunk_idx = self.pos // self.chunk_size
        chunk_offset = self.pos % self.chunk_size
        chunk = self._get_chunk(chunk_idx)
        if not chunk or chunk_offset >= len(chunk):
            return 0
        available = len(chunk) - chunk_offset
        to_read = min(len(b), available)
        b[:to_read] = chunk[chunk_offset : chunk_offset + to_read]
        self.pos += to_read
        return to_read


def is_archive_candidate(filename_or_url):
    """Check if target name or URL path looks like a supported compressed archive."""
    path = urllib.parse.urlsplit(str(filename_or_url)).path.lower()
    return any(path.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


def is_zip_candidate(filename_or_url):
    """Check if target name or URL path looks like a ZIP file."""
    path = urllib.parse.urlsplit(str(filename_or_url)).path.lower()
    return path.endswith(".zip") or path.endswith(".zip64")


def get_archive_type(filename_or_url):
    """Returns detected archive format: 'zip', 'rar', '7z', 'tar', etc."""
    path = urllib.parse.urlsplit(str(filename_or_url)).path.lower()
    if path.endswith(".zip") or path.endswith(".zip64"):
        return "zip"
    if path.endswith(".rar"):
        return "rar"
    if path.endswith(".7z"):
        return "7z"
    if any(path.endswith(ext) for ext in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    if path.endswith(".gz"):
        return "gz"
    return "archive"


def find_archive_tool():
    """Find system archive extraction tool (bsdtar/tar). Cached."""
    global _ARCHIVE_TOOL
    if _ARCHIVE_TOOL is not None:
        return _ARCHIVE_TOOL or None

    candidates = ["bsdtar", "/usr/bin/bsdtar", "tar", "/usr/bin/tar"]
    if os.name == "nt":
        candidates.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe"))

    for c in candidates:
        if os.path.isabs(c):
            if os.path.isfile(c) and os.access(c, os.X_OK):
                _ARCHIVE_TOOL = c
                return _ARCHIVE_TOOL
        else:
            found = shutil.which(c)
            if found:
                _ARCHIVE_TOOL = found
                return _ARCHIVE_TOOL

    _ARCHIVE_TOOL = ""
    return None


def escape_bsdtar_pattern(name):
    """Escape glob pattern characters for bsdtar/tar member extraction."""
    return re.sub(r"([*?\[\]])", r"\\\1", name)


def select_best_member(infolist):
    """
    Select the primary game/package file from an archive:
    1. Prioritize files ending in game formats (.pkg, .ffpfsc, .exfat, .ufs, .iso, .bin, .nsp, .xci).
    2. Fallback to largest file by uncompressed size.
    3. Exclude directories, macOS metadata (__MACOSX), and hidden files.
    """
    candidates = []
    for info in infolist:
        name = info.filename if hasattr(info, "filename") else info["filename"]
        size = info.file_size if hasattr(info, "file_size") else info.get("uncompressed_size", 0)
        if name.endswith("/") or name.endswith("\\"):
            continue
        parts = re.split(r"[/\\]", name)
        basename = parts[-1]
        if not basename or basename.startswith(".") or "__MACOSX" in parts:
            continue
        candidates.append((info, name, size))

    if not candidates:
        return None

    primary_exts = (".ffpfsc", ".exfat", ".ufs", ".pkg", ".iso", ".bin", ".nsp", ".xci")
    game_files = [c for c in candidates if any(c[1].lower().endswith(ext) for ext in primary_exts)]
    if game_files:
        game_files.sort(key=lambda x: x[2], reverse=True)
        return game_files[0][0]

    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[0][0]


def inspect_zip_archive(kind, location, headers=None, total_size=None):
    """
    Inspect remote or local ZIP file directory using pure Python zipfile.
    Returns metadata dict with selected member info.
    """
    headers = headers or HEADERS
    if kind == "local":
        path = os.path.abspath(os.path.expanduser(location))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Local file not found: {path}")
        total_size = os.path.getsize(path)
        zf = zipfile.ZipFile(path, "r")
    else:
        if total_size is None:
            req = urllib.request.Request(location, headers={**headers, "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                cr = resp.headers.get("Content-Range", "")
                m = re.search(r"/(\d+)$", cr)
                if m:
                    total_size = int(m.group(1))
                elif resp.headers.get("Content-Length"):
                    total_size = int(resp.headers["Content-Length"])
                else:
                    raise ValueError("Cannot determine remote ZIP archive size.")
        stream = HttpRangeStream(location, total_size, headers=headers)
        zf = zipfile.ZipFile(stream, "r")

    try:
        members = zf.infolist()
        selected = select_best_member(members)
        if not selected:
            raise ValueError("ZIP archive contains no usable payload files.")

        entries = []
        for info in members:
            if info.filename.endswith("/") or "__MACOSX" in info.filename:
                continue
            entries.append({
                "filename": info.filename,
                "uncompressed_size": info.file_size,
                "compressed_size": info.compress_size,
                "compress_type": info.compress_type,
                "header_offset": info.header_offset,
                "crc": info.CRC,
            })

        return {
            "is_zip": True,
            "total_archive_size": total_size,
            "entries_count": len(entries),
            "selected": {
                "filename": selected.filename,
                "basename": os.path.basename(selected.filename.replace("\\", "/")),
                "uncompressed_size": selected.file_size,
                "compressed_size": selected.compress_size,
                "compress_type": selected.compress_type,  # 0=Stored, 8=Deflated
                "header_offset": selected.header_offset,
                "crc": selected.CRC,
            }
        }
    finally:
        zf.close()


def get_zip_payload_offset(kind, location, header_offset, headers=None):
    """
    Read the 30-byte Local File Header at header_offset to compute exact payload start.
    payload_start = header_offset + 30 + filename_len + extra_len
    """
    headers = headers or HEADERS
    if kind == "local":
        with open(location, "rb") as f:
            f.seek(header_offset)
            hdr_bytes = f.read(30)
    else:
        req = urllib.request.Request(location, headers={
            **headers,
            "Range": f"bytes={header_offset}-{header_offset + 29}"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if resp.status == 206:
                hdr_bytes = data[:30]
            else:
                hdr_bytes = data[header_offset : header_offset + 30]

    if len(hdr_bytes) < 30:
        raise ValueError("Truncated ZIP local file header.")

    sig, ver, ver_need, flags, comp, mtime, mdate, crc, csize, usize, fn_len, ex_len = struct.unpack(
        zipfile.structFileHeader, hdr_bytes
    )
    if sig != b"PK\x03\x04":
        raise ValueError(f"Invalid ZIP local file header signature: {sig!r}")

    return header_offset + 30 + fn_len + ex_len


class ZipStreamingReader:
    """
    Streaming decompressing reader.
    Streams compressed bytes from local file or HTTP URL via Range request,
    decompresses on the fly in RAM using zlib.decompressobj(-15),
    and exposes standard read(size) interface backed by a bounded queue.
    Zero local disk writes.
    """
    def __init__(self, source, buffer_mb, token, meter, headers=None):
        self.source = source
        self.token = token
        self.meter = meter
        self.headers = headers or HEADERS
        self.closed = threading.Event()
        self.q = queue.Queue(maxsize=max(2, buffer_mb - 2))
        self.resource = None
        self.error = None

        archive = source.archive_info
        self.kind = source.kind
        self.location = source.location
        self.payload_offset = archive["payload_offset"]
        self.compressed_size = archive["compressed_size"]
        self.uncompressed_size = archive["uncompressed_size"]
        self.compress_type = archive["compress_type"]

        self.thread = threading.Thread(target=self._fill, daemon=True, name="zip-decompressor")
        self.thread.start()

    def _put(self, block):
        while not self.closed.is_set():
            self.token.check()
            try:
                self.q.put(block, timeout=0.1)
                return
            except queue.Full:
                pass

    def _fill(self):
        try:
            if self.kind in ("local", "zip_local"):
                f = open(self.location, "rb")
                f.seek(self.payload_offset)
                self.resource = f
            else:
                end_byte = self.payload_offset + self.compressed_size - 1
                req = urllib.request.Request(self.location, headers={
                    **self.headers,
                    "Range": f"bytes={self.payload_offset}-{end_byte}"
                })
                resp = urllib.request.urlopen(req, timeout=20)
                if resp.status not in (200, 206):
                    resp.close()
                    raise OSError(f"Unexpected HTTP {resp.status} for ZIP payload stream.")
                if resp.status == 200 and self.payload_offset > 0:
                    discarded = 0
                    while discarded < self.payload_offset:
                        skip = resp.read(min(BLOCK, self.payload_offset - discarded))
                        if not skip:
                            break
                        discarded += len(skip)
                self.resource = resp

            r = self.resource
            rem_compressed = self.compressed_size
            read_chunk = 256 * 1024  # 256 KiB compressed read

            if self.compress_type == 0:
                # Stored / Uncompressed
                while not self.closed.is_set() and rem_compressed > 0:
                    self.token.check()
                    take = min(BLOCK, rem_compressed)
                    chunk = r.read(take)
                    if not chunk:
                        break
                    rem_compressed -= len(chunk)
                    self.meter.add(downloaded=len(chunk), buffered=len(chunk))
                    self._put(chunk)
            elif self.compress_type == 8:
                # Deflated (raw deflate window bits -15)
                dobj = zlib.decompressobj(-15)
                while not self.closed.is_set() and rem_compressed > 0:
                    self.token.check()
                    take = min(read_chunk, rem_compressed)
                    compressed_chunk = r.read(take)
                    if not compressed_chunk:
                        break
                    rem_compressed -= len(compressed_chunk)
                    self.meter.add(downloaded=len(compressed_chunk))

                    # Decompress in chunks
                    decompressed = dobj.decompress(compressed_chunk)
                    if decompressed:
                        self.meter.add(buffered=len(decompressed))
                        self._put(decompressed)

                # Flush any remaining buffer in decompressor
                final = dobj.flush()
                if final:
                    self.meter.add(buffered=len(final))
                    self._put(final)
            else:
                raise ValueError(f"Unsupported ZIP compression type: {self.compress_type}. Supported: 0 (Stored), 8 (Deflate)")

            self._put(None)
        except Exception as ex:
            self.error = ex
            try:
                self._put(None)
            except Exception:
                pass

    def read(self, size=BLOCK):
        while not self.closed.is_set():
            self.token.check()
            try:
                block = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            if block is None:
                if self.error:
                    raise self.error
                return b""
            self.meter.add(buffered=-len(block))
            return block
        raise Exception("Stream closed or cancelled.")

    def close(self):
        self.closed.set()
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
        self.thread.join(timeout=1.0)
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break


def inspect_system_archive(kind, location, headers=None, total_size=None):
    """
    Inspect local or remote system archive (RAR, 7Z, TAR, etc.) using bsdtar/tar.
    Returns metadata dict with selected member info.
    """
    tool = find_archive_tool()
    if not tool:
        raise RuntimeError("No system archive decompressor (bsdtar/tar) found on host machine.")

    headers = headers or HEADERS
    entries = []
    raw_output = ""
    stderr_output = ""

    if kind == "local":
        path = os.path.abspath(os.path.expanduser(location))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Local file not found: {path}")
        total_size = os.path.getsize(path)
        try:
            proc = subprocess.Popen(
                [tool, "--numeric-owner", "-tvf", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace"
            )
            raw_output, stderr_output = proc.communicate(timeout=15)
        except Exception as ex:
            raise ValueError(f"Failed to inspect local archive: {ex}") from ex
    else:
        # Remote URL: sample first 2 MiB to read archive header
        req = urllib.request.Request(location, headers={**headers, "Range": "bytes=0-2097151"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                sample = resp.read()
                if total_size is None:
                    cr = resp.headers.get("Content-Range", "")
                    m = re.search(r"/(\d+)$", cr)
                    if m:
                        total_size = int(m.group(1))
                    elif resp.headers.get("Content-Length"):
                        total_size = int(resp.headers["Content-Length"])
        except Exception as ex:
            raise ValueError(f"Failed to fetch remote archive header sample: {ex}") from ex

        try:
            proc = subprocess.Popen(
                [tool, "--numeric-owner", "-tvf", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = proc.communicate(input=sample, timeout=15)
            raw_output = stdout_bytes.decode("utf-8", errors="replace")
            stderr_output = stderr_bytes.decode("utf-8", errors="replace")
        except Exception as ex:
            raise ValueError(f"Failed to inspect remote archive stream: {ex}") from ex

    pattern_numeric = re.compile(r"^([drwxst-]{10})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([A-Za-z]{3}\s+\d+\s+[\d:]+)\s+(.+)$")
    pattern_fallback = re.compile(r"^([drwxst-]{10})\s+.*?(\d+)\s+([A-Za-z]{3}\s+\d+\s+[\d:]+)\s+(.+)$")

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern_numeric.match(line)
        if m:
            perms, links, uid, gid, size_str, mtime, name = m.groups()
            size = int(size_str)
        else:
            m = pattern_fallback.match(line)
            if m:
                perms, size_str, mtime, name = m.groups()
                size = int(size_str)
            else:
                continue

        name = name.strip()
        if perms.startswith("d") or name.endswith("/") or name.endswith("\\"):
            continue
        entries.append({
            "filename": name,
            "uncompressed_size": size,
        })

    if not entries:
        err_hint = stderr_output.strip()[:150] or "No files found in archive listing."
        raise ValueError(f"Archive inspection failed: {err_hint}")

    selected = select_best_member(entries)
    if not selected:
        raise ValueError("Archive contains no usable payload files.")

    arch_type = get_archive_type(location)
    return {
        "is_archive": True,
        "archive_type": arch_type,
        "total_archive_size": total_size,
        "entries_count": len(entries),
        "selected": {
            "filename": selected["filename"],
            "basename": os.path.basename(selected["filename"].replace("\\", "/")),
            "uncompressed_size": selected["uncompressed_size"],
            "compressed_size": total_size,
            "compress_type": "archive",
            "header_offset": 0,
            "crc": 0,
        }
    }


def inspect_archive(kind, location, headers=None, total_size=None):
    """
    Unified archive inspector for ZIP, RAR, 7Z, TAR, etc.
    Uses native pure-Python zipfile engine for standard ZIP archives,
    and system libarchive (bsdtar/tar) for RAR, 7Z, TAR, etc.
    """
    if is_zip_candidate(location):
        try:
            res = inspect_zip_archive(kind, location, headers=headers, total_size=total_size)
            res["archive_type"] = "zip"
            return res
        except Exception:
            # Fallback to system decompressor if python zipfile fails
            pass

    return inspect_system_archive(kind, location, headers=headers, total_size=total_size)


class ArchiveStreamingReader:
    """
    Streaming decompressing reader for RAR, 7Z, TAR, and system archives.
    Runs system decompressor (bsdtar/tar) in a subprocess, streaming extracted bytes
    directly into RAM and FTP socket with 0 disk writes.
    Supports both local disk archives and remote HTTP URLs via stdin pipe.
    """
    def __init__(self, source, offset, buffer_mb, token, meter, headers=None):
        self.source = source
        self.offset = offset or 0
        self.token = token
        self.meter = meter
        self.headers = headers or HEADERS
        self.closed = threading.Event()
        self.q = queue.Queue(maxsize=max(2, buffer_mb - 2))
        self.error = None
        self.proc = None
        self.http_resp = None
        self.feeder_thread = None
        self.reader_thread = None

        archive = source.archive_info
        self.member_name = archive["filename"]
        self.uncompressed_size = archive.get("uncompressed_size")
        self.tool = find_archive_tool()
        if not self.tool:
            raise RuntimeError("System archive tool (bsdtar/tar) not found.")

        esc_pattern = escape_bsdtar_pattern(self.member_name)
        if self.source.kind in ("local", "archive_local"):
            cmd = [self.tool, "-xOf", self.source.location, esc_pattern]
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1024 * 1024,
            )
            self.reader_thread = threading.Thread(target=self._read_stdout, daemon=True, name="archive-stdout-reader")
            self.reader_thread.start()
        else:
            cmd = [self.tool, "-xOf", "-", esc_pattern]
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1024 * 1024,
            )
            self.feeder_thread = threading.Thread(target=self._feed_stdin, daemon=True, name="archive-stdin-feeder")
            self.reader_thread = threading.Thread(target=self._read_stdout, daemon=True, name="archive-stdout-reader")
            self.feeder_thread.start()
            self.reader_thread.start()

    def _put(self, block):
        while not self.closed.is_set():
            self.token.check()
            try:
                self.q.put(block, timeout=0.1)
                return
            except queue.Full:
                pass

    def _feed_stdin(self):
        try:
            req = urllib.request.Request(self.source.location, headers=self.headers)
            resp = urllib.request.urlopen(req, timeout=20)
            self.http_resp = resp
            read_chunk = 256 * 1024
            while not self.closed.is_set():
                self.token.check()
                data = resp.read(read_chunk)
                if not data:
                    break
                self.meter.add(downloaded=len(data))
                try:
                    self.proc.stdin.write(data)
                except (BrokenPipeError, OSError):
                    break
        except Exception as ex:
            if not self.closed.is_set():
                self.error = ex
        finally:
            try:
                if self.proc and self.proc.stdin:
                    self.proc.stdin.close()
            except Exception:
                pass

    def _read_stdout(self):
        try:
            discarded = 0
            target_offset = self.offset
            while not self.closed.is_set() and discarded < target_offset:
                self.token.check()
                take = min(BLOCK, target_offset - discarded)
                skipped = self.proc.stdout.read(take)
                if not skipped:
                    break
                discarded += len(skipped)
                if self.source.kind in ("local", "archive_local"):
                    self.meter.add(downloaded=len(skipped))

            while not self.closed.is_set():
                self.token.check()
                chunk = self.proc.stdout.read(BLOCK)
                if not chunk:
                    break
                if self.source.kind in ("local", "archive_local"):
                    self.meter.add(downloaded=len(chunk), buffered=len(chunk))
                else:
                    self.meter.add(buffered=len(chunk))
                self._put(chunk)

            stderr_out = b""
            if self.proc:
                try:
                    stderr_out = self.proc.stderr.read()
                except Exception:
                    pass
                self.proc.wait()
                if self.proc.returncode not in (0, None) and not self.closed.is_set():
                    err_msg = stderr_out.decode("utf-8", errors="replace").strip()
                    if err_msg and "Broken pipe" not in err_msg:
                        self.error = OSError(f"Archive extraction failed (exit {self.proc.returncode}): {err_msg}")

            self._put(None)
        except Exception as ex:
            self.error = ex
            try:
                self._put(None)
            except Exception:
                pass

    def read(self, size=BLOCK):
        while not self.closed.is_set():
            self.token.check()
            try:
                block = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            if block is None:
                if self.error:
                    raise self.error
                return b""
            self.meter.add(buffered=-len(block))
            return block
        raise Exception("Stream closed or cancelled.")

    def close(self):
        self.closed.set()
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=1.0)
            except Exception:
                pass
        if self.http_resp:
            try:
                sock = self.http_resp.fp.raw._sock
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.http_resp.close()
            except Exception:
                pass
        if self.feeder_thread and self.feeder_thread.is_alive():
            self.feeder_thread.join(timeout=1.0)
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
