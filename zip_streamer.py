"""
On-The-Fly Streaming Archive Decompression Engine for Direct Stream PS5.
Pure Python Standard Library (zipfile, zlib, struct, io, urllib). Zero external dependencies.

Enables streaming compressed ZIP archives directly over HTTP or local disk into PS5 FTP storage
in RAM without extracting onto local PC/phone disk.
"""

import io
import json
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass

class Cancelled(Exception):
    pass

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
            if resp.status == 206:
                return resp.read(end - start + 1)
            data = resp.read(end + 1)
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
    raw = urllib.parse.unquote(str(filename_or_url))
    path = urllib.parse.urlsplit(raw).path.lower()
    base = os.path.basename(path)
    if base.endswith(".zip") or base.endswith(".zip64"):
        return "zip"
    if base.endswith(".rar") or re.search(r"\.part\d+\.rar$|\.r\d{2}$|\.rar\.\d+$", base):
        return "rar"
    if base.endswith(".7z") or base.endswith(".7z.001"):
        return "7z"
    if any(base.endswith(ext) for ext in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    if base.endswith(".gz"):
        return "gz"
    if ".rar" in base:
        return "rar"
    if ".zip" in base:
        return "zip"
    if ".7z" in base:
        return "7z"
    return "archive"


def find_lsar_tool():
    """Find lsar archive inspector tool (bundled in app, local bin, or PATH)."""
    if sys.platform != "darwin":
        return shutil.which("lsar")
    res_dir = os.environ.get("RESOURCEPATH")
    if res_dir:
        p = os.path.join(res_dir, "bin", "lsar")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "bin", "lsar"),
        os.path.join(base, "PS5 Direct Streamer.app", "Contents", "Resources", "bin", "lsar"),
        os.path.join(os.path.dirname(base), "Resources", "bin", "lsar"),
        "/opt/homebrew/bin/lsar",
        "/usr/local/bin/lsar",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    found = shutil.which("lsar")
    if found and os.access(found, os.X_OK):
        return found
    return None


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


class EncryptedArchiveError(ValueError):
    """Raised when an archive is password-protected or encrypted."""
    def __init__(self, message, archive_type="archive", password_hint=None):
        super().__init__(message)
        self.archive_type = archive_type
        self.password_hint = password_hint


def extract_password_hint(text):
    """Extract password hint from bracketed scene names like [DLPSGAME.COM] or pass=xyz."""
    if not text:
        return None
    clean = urllib.parse.unquote(str(text))
    m = re.search(r"\[([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\]", clean)
    if m:
        return m.group(1)
    m2 = re.search(r"(?:password|pass)[=:\s_-]+([a-zA-Z0-9_-]+)", clean, re.IGNORECASE)
    if m2:
        return m2.group(1)
    return None


def detect_archive_encryption(sample_bytes, stderr_text="", name_or_url=""):
    """
    Check if archive header or decompressor stderr indicates encryption/password protection.
    Works for RAR5, RAR4, 7Z, and libarchive/bsdtar/7zz stderr outputs.
    """
    err_lower = (stderr_text or "").lower()
    enc_phrases = (
        "encryption is not supported",
        "cannot open encrypted archive",
        "passphrase required",
        "password required",
        "wrong password",
        "encrypted file",
        "unsupported feature: encryption",
        "bad password",
        "encrypted = +",
        "header is encrypted",
    )
    if any(p in err_lower for p in enc_phrases):
        return True

    if sample_bytes and len(sample_bytes) >= 16:
        # RAR 5.0
        if sample_bytes.startswith(b"Rar!\x1a\x07\x01\x00"):
            idx = 12
            def _read_vint(buf, off):
                val = 0
                shift = 0
                for i in range(10):
                    if off + i >= len(buf):
                        break
                    b = buf[off + i]
                    val |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        return val, off + i + 1
                return val, off
            hdr_size, idx = _read_vint(sample_bytes, idx)
            hdr_type, idx = _read_vint(sample_bytes, idx)
            if hdr_type == 4:  # HEAD_ENCRYPT: entire archive header is encrypted
                return True
        # RAR 4.x
        elif sample_bytes.startswith(b"Rar!\x1a\x07\x00"):
            if len(sample_bytes) > 11 and sample_bytes[9] == 0x73 and (sample_bytes[10] & 0x80):
                return True
            if len(sample_bytes) > 11 and sample_bytes[9] == 0x74 and (sample_bytes[10] & 0x04):
                return True

    return False


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

    has_package = any(c[1].lower().endswith((".pkg", ".ffpfsc", ".exfat", ".ufs", ".iso", ".nsp", ".xci")) for c in candidates)
    if not has_package and len(candidates) > 1 and any(os.path.basename(c[1]).lower() in ("eboot.bin", "param.sfo", "param.json") for c in candidates):
        raise ValueError("Loose game folder requires full extraction, not single-member streaming.")

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
        is_encrypted = False
        entries = []
        for info in members:
            if info.filename.endswith("/") or "__MACOSX" in info.filename:
                continue
            if info.flag_bits & 0x1:
                is_encrypted = True
            entries.append({
                "filename": info.filename,
                "uncompressed_size": info.file_size,
                "compressed_size": info.compress_size,
                "compress_type": info.compress_type,
                "header_offset": info.header_offset,
                "crc": info.CRC,
            })

        if is_encrypted:
            pwd_hint = extract_password_hint(location)
            hint_str = f" (password: {pwd_hint} required)" if pwd_hint else " (password required)"
            raise EncryptedArchiveError(
                f"This ZIP archive is password-protected or encrypted{hint_str}. "
                "On-the-fly streaming cannot decompress encrypted archives without unpacking. "
                f"Choose 'PS5 on-console extraction' or 'Local staging extraction'{f' with password {pwd_hint}' if pwd_hint else ''}, "
                "or transfer the raw archive directly.",
                archive_type="zip",
                password_hint=pwd_hint,
            )

        selected = select_best_member(members)
        if not selected:
            raise ValueError("ZIP archive contains no usable payload files.")

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
            if resp.status == 206:
                hdr_bytes = resp.read(30)
            else:
                data = resp.read(header_offset + 30)
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
                    if self.payload_offset > 16 * 1024 * 1024:
                        resp.close()
                        raise OSError("Server returned HTTP 200 without Range support; cannot stream ZIP payload from large offset.")
                    discarded = 0
                    while discarded < self.payload_offset:
                        skip = resp.read(min(BLOCK, self.payload_offset - discarded))
                        if not skip:
                            break
                        discarded += len(skip)
                self.resource = resp

            r = self.resource
            remaining = self.compressed_size
            output_size = 0
            crc = 0
            decompressor = zlib.decompressobj(-15) if self.compress_type == 8 else None
            if self.compress_type not in (0, 8):
                raise ValueError(f"Unsupported ZIP compression type: {self.compress_type}. Supported: 0 (Stored), 8 (Deflate)")

            while remaining:
                self.token.check()
                if self.closed.is_set():
                    return
                compressed = r.read(min(256 * 1024, remaining))
                if not compressed:
                    raise ValueError("Truncated ZIP compressed stream.")
                remaining -= len(compressed)
                self.meter.add(downloaded=len(compressed))
                pending = compressed
                while pending:
                    self.token.check()
                    if self.closed.is_set():
                        return
                    output = decompressor.decompress(pending, BLOCK) if decompressor else pending
                    pending = decompressor.unconsumed_tail if decompressor else b""
                    if output:
                        output_size += len(output)
                        if output_size > self.uncompressed_size:
                            raise ValueError("ZIP payload exceeded declared size.")
                        crc = zlib.crc32(output, crc)
                        self.meter.add(buffered=len(output))
                        self._put(output)

            if decompressor and (not decompressor.eof or decompressor.unused_data):
                raise ValueError("Invalid or incomplete ZIP deflate stream.")
            expected_crc = self.source.archive_info.get("crc") if hasattr(self.source, "archive_info") and self.source.archive_info else None
            if output_size != self.uncompressed_size or (expected_crc is not None and (crc & 0xffffffff) != expected_crc):
                raise ValueError("ZIP payload size or CRC mismatch.")
            self._put(None)
        except Exception as ex:
            self.error = ex
            try:
                self._put(None)
            except Exception:
                pass
        finally:
            if self.resource:
                try:
                    self.resource.close()
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
        raise Cancelled()

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


def inspect_system_archive(kind, location, headers=None, total_size=None, password=None):
    """
    Inspect local or remote system archive (RAR, 7Z, TAR, etc.) using lsar/bsdtar/tar.
    Returns metadata dict with selected member info.
    """
    effective_pwd = password or extract_password_hint(location)
    entries = []
    raw_output = ""
    stderr_output = ""
    sample = b""
    arch_type = get_archive_type(location)

    # 1. Try lsar if local file to accurately inspect with password decryption
    if kind == "local":
        path = os.path.abspath(os.path.expanduser(location))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Local file not found: {path}")
        total_size = os.path.getsize(path)
        try:
            with open(path, "rb") as f:
                sample = f.read(65536)
        except Exception:
            pass

        lsar = find_lsar_tool()
        if lsar:
            cmd = [lsar, "-j"]
            if effective_pwd:
                cmd.extend(["-p", effective_pwd])
            cmd.append(path)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if proc.returncode == 0 and proc.stdout:
                    data = json.loads(proc.stdout)
                    lsar_entries = data.get("lsarContents", [])
                    is_encrypted = any(item.get("XADIsEncrypted") for item in lsar_entries)
                    for item in lsar_entries:
                        name = item.get("XADFileName", "").strip()
                        if not name or name.endswith("/") or name.endswith("\\") or "__MACOSX" in name:
                            continue
                        size = item.get("XADFileSize", 0)
                        entries.append({
                            "filename": name,
                            "uncompressed_size": size,
                        })
                    if entries:
                        selected = select_best_member(entries)
                        if selected:
                            return {
                                "is_archive": True,
                                "archive_type": arch_type,
                                "total_archive_size": total_size,
                                "entries_count": len(entries),
                                "is_encrypted": is_encrypted,
                                "password_hint": effective_pwd,
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
            except Exception:
                pass

    tool = find_archive_tool()
    if not tool:
        raise RuntimeError("No system archive decompressor (lsar/bsdtar/tar) found on host machine.")

    headers = headers or HEADERS

    if kind == "local":
        path = os.path.abspath(os.path.expanduser(location))
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

    if detect_archive_encryption(sample, stderr_output, location):
        pwd_hint = effective_pwd
        hint_str = f" (password: {pwd_hint} required)" if pwd_hint else " (password required)"
        raise EncryptedArchiveError(
            f"This {arch_type.upper()} archive is password-protected or encrypted{hint_str}. "
            "On-the-fly streaming cannot decompress encrypted archives without unpacking. "
            f"Choose 'PS5 on-console extraction' or 'Local staging extraction'{f' with password {pwd_hint}' if pwd_hint else ''}, "
            "or transfer the raw archive directly.",
            archive_type=arch_type,
            password_hint=pwd_hint,
        )

    pattern_gnu = re.compile(r"^([-drwxst]+)\s+\S+/\S+\s+(\d+)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?\s+(.+)$")
    pattern_numeric = re.compile(r"^([drwxst-]{10})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([A-Za-z]{3}\s+\d+\s+[\d:]+)\s+(.+)$")
    pattern_fallback = re.compile(r"^([drwxst-]{10})\s+.*?(\d+)\s+([A-Za-z]{3}\s+\d+\s+[\d:]+)\s+(.+)$")

    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        gnu = pattern_gnu.match(line)
        if gnu:
            perms, size, name = gnu.groups()
            if perms.startswith("-"):
                entries.append({"filename": name.strip(), "uncompressed_size": int(size)})
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
        if detect_archive_encryption(sample, stderr_output, location):
            pwd_hint = effective_pwd
            hint_str = f" (password: {pwd_hint} required)" if pwd_hint else " (password required)"
            raise EncryptedArchiveError(
                f"This {arch_type.upper()} archive is password-protected or encrypted{hint_str}. "
                "On-the-fly streaming cannot decompress encrypted archives without unpacking. "
                f"Choose 'PS5 on-console extraction' or 'Local staging extraction'{f' with password {pwd_hint}' if pwd_hint else ''}, "
                "or transfer the raw archive directly.",
                archive_type=arch_type,
                password_hint=pwd_hint,
            )
        err_hint = stderr_output.strip()[:150] or "No files found in archive listing."
        raise ValueError(f"Archive inspection failed: {err_hint}")

    # Check if archive is a loose directory structure / folder dump (e.g. Balatro loose files, eboot.bin, etc.)
    has_pkg_or_disc = any(any(e["filename"].lower().endswith(ext) for ext in (".ffpfsc", ".exfat", ".ufs", ".pkg", ".iso", ".nsp", ".xci")) for e in entries)
    if not has_pkg_or_disc and len(entries) > 1:
        if any("eboot.bin" in e["filename"].lower() or "param.sfo" in e["filename"].lower() or "sce_sys" in e["filename"].lower() for e in entries):
            raise ValueError(
                f"This archive contains a loose game folder structure ({len(entries)} files) rather than a single package file (.pkg/.ffpfsc). "
                "Single-file streaming to PS5 cannot reconstruct the folder hierarchy. Extract the archive on your computer first, "
                "then use 'Upload Folder' to transfer the entire game directory directly to your PS5."
            )

    selected = select_best_member(entries)
    if not selected:
        raise ValueError("Archive contains no usable payload files.")

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


def inspect_archive(kind, location, headers=None, total_size=None, password=None):
    """
    Unified archive inspector for ZIP, RAR, 7Z, TAR, etc.
    Uses native pure-Python zipfile engine for standard ZIP archives,
    and system libarchive / lsar for RAR, 7Z, TAR, etc.
    """
    if is_zip_candidate(location):
        try:
            res = inspect_zip_archive(kind, location, headers=headers, total_size=total_size)
            res["archive_type"] = "zip"
            return res
        except Exception:
            # Fallback to system decompressor if python zipfile fails
            pass

    return inspect_system_archive(kind, location, headers=headers, total_size=total_size, password=password)


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
        raise Cancelled()

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
