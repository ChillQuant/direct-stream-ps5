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
import socket
import struct
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


def is_zip_candidate(filename_or_url):
    """Check if target name or URL path looks like a ZIP file."""
    path = urllib.parse.urlsplit(str(filename_or_url)).path.lower()
    return path.endswith(".zip") or path.endswith(".zip64")


def select_best_member(infolist):
    """
    Select the primary game/package file from a ZIP archive:
    1. Prioritize files ending in .pkg (case-insensitive).
    2. Fallback to largest file by uncompressed size.
    3. Exclude directories, macOS metadata (__MACOSX), and hidden files.
    """
    candidates = []
    for info in infolist:
        name = info.filename
        if name.endswith("/") or name.endswith("\\"):
            continue
        parts = re.split(r"[/\\]", name)
        basename = parts[-1]
        if not basename or basename.startswith(".") or "__MACOSX" in parts:
            continue
        candidates.append(info)

    if not candidates:
        return None

    # First look for primary game formats (.ffpfsc, .exfat, .ufs, .pkg, .iso, .bin)
    primary_exts = (".ffpfsc", ".exfat", ".ufs", ".pkg", ".iso", ".bin")
    game_files = [c for c in candidates if any(c.filename.lower().endswith(ext) for ext in primary_exts)]
    if game_files:
        game_files.sort(key=lambda x: x.file_size, reverse=True)
        return game_files[0]

    # Fallback to largest payload
    candidates.sort(key=lambda x: x.file_size, reverse=True)
    return candidates[0]


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
