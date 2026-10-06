#!/usr/bin/env python3
"""PS5 Transfer 2 — private local dashboard and serial transfer queue."""
import argparse
import concurrent.futures
import copy
import ftplib
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import shutil
import sys
import tempfile
import threading
import socket
import ssl
import time
import urllib.error
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from transfer_core import (Cancelled, Meter, StopToken, TransferError, close_ftp,
    connect_ftp, make_reader, probe_source, safe_text, transfer, valid_folder,
    valid_name, valid_url, check_ftp_storage, validate_source_url, validate_multipart_source, MIB)
from resolver import pre_resolve_url, detect_multipart_sequence, parse_multipart_info

VERSION = "2.9.0"
BASE = Path(__file__).resolve().parent
DEFAULTS = {"host": "", "port": 1337, "folder": "/data/ShadowMount", "username": "anonymous",
            "streams": 16, "buffer_mb": 256, "chunk_mb": 8, "limit_mbps": 0, "retries": 3}
ACTIVE = {"starting", "running", "retrying", "pausing", "cancelling"}


def extract_url_filename(src):
    """Extract real filename from URL query parameters (e.g. ?filename=...) or URL path."""
    try:
        parsed = urllib.parse.urlsplit(src)
        q = urllib.parse.parse_qs(parsed.query)
        for param in ("filename", "file_name", "name", "file"):
            vals = q.get(param)
            if vals and vals[0]:
                clean = urllib.parse.unquote(vals[0]).strip().split("/")[-1].split("\\")[-1]
                if clean and "." in clean:
                    return clean
        raw = urllib.parse.unquote(Path(parsed.path).name)
        if raw:
            return raw
    except Exception:
        pass
    return "download.bin"



def notify_user(title, message, sound="Glass"):
    try:
        clean_title = re.sub(r'["\\]', '', str(title))
        clean_msg = re.sub(r'["\\]', '', str(message))
        if sys.platform == "darwin":
            script = f'display notification "{clean_msg}" with title "{clean_title}"'
            if sound:
                script += f' sound name "{sound}"'
            subprocess.Popen(["/usr/bin/osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("termux-notification"):
            subprocess.Popen(["termux-notification", "--title", clean_title, "--content", clean_msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


notify_macos = notify_user



def open_browser(url):
    if shutil.which("termux-open-url"):
        try:
            subprocess.Popen(["termux-open-url", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    if shutil.which("xdg-open") and not sys.platform.startswith("darwin") and not sys.platform.startswith("win"):
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".ps5-", dir=str(path.parent))
    try:
        os.chmod(temp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validated_settings(raw):
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS or k == "password"}}
    host = safe_text(cfg["host"], "PS5 address", 253)
    if host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host):
                raise TransferError("Enter a PS5 IP address or hostname, without a URL or port.")
    cfg["host"] = host
    cfg["folder"] = valid_folder(cfg["folder"])
    cfg["username"] = safe_text(cfg["username"], "FTP username", 128) or "anonymous"
    cfg["password"] = safe_text(cfg.get("password", ""), "FTP password", 512)
    for key, low, high in [("port", 1, 65535), ("streams", 1, 32), ("buffer_mb", 16, 1024), ("chunk_mb", 1, 32), ("retries", 0, 5)]:
        try:
            value = float(cfg[key])
            if not math.isfinite(value) or value != int(value) or not low <= value <= high:
                raise ValueError()
            cfg[key] = int(value)
        except (TypeError, ValueError, OverflowError):
            raise TransferError(f"{key} must be a whole number between {low} and {high}.")
    try:
        cfg["limit_mbps"] = float(cfg["limit_mbps"])
        if not math.isfinite(cfg["limit_mbps"]) or not 0 <= cfg["limit_mbps"] <= 1000:
            raise ValueError()
    except (TypeError, ValueError):
        raise TransferError("Speed cap must be 0–1000 MB/s; 0 means unlimited.")
    if cfg["buffer_mb"] < cfg["chunk_mb"] * cfg["streams"]:
        raise TransferError("RAM buffer must be at least streams × chunk size (for example, 8 × 8 = 64 MiB).")
    return cfg


def friendly_error(e):
    if isinstance(e, TransferError):
        return str(e)
    if isinstance(e, ConnectionRefusedError):
        return "PS5 refused the connection. Check the FTP payload is running and the port is correct."
    if isinstance(e, TimeoutError):
        return "Connection timed out. Check PS5 power, network, IP address, and Mac Local Network permission."
    if isinstance(e, ftplib.error_perm):
        return "FTP refused the operation. Check login, folder permissions, and payload compatibility."
    # URLError wraps the real cause in .reason; unwrap it so the message says what actually failed.
    cause = e.reason if isinstance(e, urllib.error.URLError) and isinstance(e.reason, BaseException) else e
    if isinstance(e, urllib.error.HTTPError) or isinstance(cause, urllib.error.HTTPError):
        code = getattr(e, "code", getattr(cause, "code", None))
        if code in (401, 403):
            return f"Download link access denied (HTTP {code}). The link may have expired or is one-time use; click 'Update link' to paste a fresh one."
        if code in (404, 410):
            return f"File not found on server (HTTP {code}). The link expired or was deleted; click 'Update link' to paste a fresh one."
        if code == 429:
            return "Download rate limit exceeded (HTTP 429). The hosting site is limiting requests; wait a few minutes or use fewer streams."
        if code in (500, 502, 503, 504):
            return f"Hosting provider server error (HTTP {code}). The server is temporarily unavailable."
        return f"Download server returned HTTP {code}."
    if isinstance(cause, ssl.SSLCertVerificationError):
        return ("SSL certificate check failed on the download link. On macOS with python.org Python, open "
                "/Applications/Python 3.x and run 'Install Certificates.command', then retry.")
    if isinstance(cause, socket.gaierror):
        return "Could not resolve the download host name (DNS). Check the link and your internet/VPN/DNS."
    if isinstance(cause, (socket.timeout, TimeoutError)):
        where = "the download server" if isinstance(e, urllib.error.URLError) else "the PS5 or source"
        return f"Timed out waiting for {where}. The server may be slow to start, rate-limiting, or blocking repeated requests."
    if isinstance(cause, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return "Connection was reset mid-transfer (by the download server, your network, or the PS5)."
    # Do not put signed download URLs or login secrets into logs: redact anything URL-like.
    detail = re.sub(r"https?://\S+", "<url>", str(cause))[:160]
    return f"{type(cause).__name__}: {detail}. Check the source link, network, and PS5 FTP server."


BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def measure_source(source, cfg, token, seconds=12):
    """Download-only speed test. Counts bytes as they arrive from the network."""
    meter = Meter()
    reader = make_reader(source, 0, cfg, token, meter)
    parallel = type(reader).__name__ == "ParallelReader"
    try:
        def drain():
            try:
                while reader.read():
                    pass
            except Exception:
                pass
        threading.Thread(target=drain, daemon=True, name="diag-drain").start()
        t0 = time.monotonic()
        samples, first_byte = [], None
        while time.monotonic() - t0 < seconds:
            token.check()
            if getattr(reader, "error", None):
                raise reader.error
            now = time.monotonic() - t0
            got = meter.snapshot()["downloaded"]
            samples.append((now, got))
            if first_byte is None and got > 0:
                first_byte = now
            if got >= 768 * MIB or (source.size is not None and got >= source.size):
                break
            time.sleep(.1)
        total_s, got = samples[-1]
        avg = got / max(.001, total_s)
        steady = avg
        if first_byte is not None and total_s - first_byte > 2:
            cut = first_byte + (total_s - first_byte) / 2
            base = next((x for x in samples if x[0] >= cut), samples[0])
            steady = (got - base[1]) / max(.001, total_s - base[0])
        return {"steady": steady, "avg": avg, "first_byte": first_byte if first_byte is not None else total_s,
                "bytes": got, "seconds": total_s, "parallel": parallel}
    finally:
        reader.close()


class Manager:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "state.json"
        self.lock = threading.RLock()
        self.ftp_lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.settings = dict(DEFAULTS)
        self.password = ""
        self.jobs = []
        self.logs = []
        self.running = False
        self.current = None
        self.token = None
        self.diagnostic_token = None
        self.diagnostic = {"state": "idle"}
        self.connection = {"state": "unknown", "message": "Add your PS5 address to connect"}
        self.metrics = {}
        self.history = []
        self._load()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="transfer-queue")
        self.thread.start()

    def _load(self):
        try:
            state = json.loads(self.path.read_text())
            self.settings = validated_settings(state.get("settings", {}))
            self.settings.pop("password", None)
            self.jobs = state.get("jobs", [])
            for job in self.jobs:
                if job.get("kind") == "url" and job.get("source"):
                    name = job.get("name", "")
                    if re.match(r'^[a-f0-9-]{16,}$', name, re.IGNORECASE) or "." not in name:
                        recovered = extract_url_filename(job["source"])
                        if recovered and recovered not in ("download.bin", name) and "." in recovered:
                            job["name"] = recovered
                if job["state"] in ACTIVE or job["state"] == "queued":
                    job["state"] = "paused"
                    job["detail"] = "Restored after app restart. Resume when ready."
            self.save()
        except FileNotFoundError:
            try:
                old = json.loads((Path.home() / ".ps5_streamer.json").read_text())
                self.settings.update({"host": str(old.get("ip", "")), "port": int(old.get("port", 1337)),
                                      "folder": old.get("dir", DEFAULTS["folder"])})
                self.settings = validated_settings(self.settings)
                self.settings.pop("password", None)
            except Exception:
                self.settings = dict(DEFAULTS)
        except Exception:
            self.settings = dict(DEFAULTS)
            self.jobs = []
            self.log("warning", "Saved queue could not be loaded. The original state file is retained until you make changes.")

    def save(self):
        with self.lock:
            atomic_json(self.path, {"version": VERSION, "settings": self.settings, "jobs": self.jobs})

    def log(self, level, message):
        with self.lock:
            self.logs.append({"time": time.strftime("%H:%M:%S"), "level": level, "message": message})
            self.logs = self.logs[-300:]

    def snapshot(self):
        with self.lock:
            # ponytail: shallow copies under lock avoids heavy copy.deepcopy memoization on every poll
            return {
                "version": VERSION,
                "settings": dict(self.settings),
                "jobs": [dict(j) for j in self.jobs],
                "running": self.running,
                "active": self.current,
                "connection": dict(self.connection),
                "diagnostic": dict(self.diagnostic),
                "metrics": dict(self.metrics),
                "history": list(self.history),
                "logs": list(self.logs),
                "has_password": bool(self.password)
            }

    def configure(self, raw):
        cfg = validated_settings(raw)
        with self.lock:
            if self.current or self.diagnostic["state"] == "running":
                raise TransferError("Pause transfers and finish diagnostics before changing settings.")
            self.password = cfg.pop("password")
            self.settings = cfg
            self.connection = {"state": "unknown", "message": "Settings saved · test the connection"}
            self.save()
        return {"ok": True}

    def add_jobs(self, data):
        kind = data.get("kind", "url")
        if kind not in ("url", "local", "multipart", "folder"):
            raise TransferError("Choose a direct link, a local file, or a folder.")
        items = data.get("items", [])
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise TransferError("Add between 1 and 100 files at a time.")
        combine = data.get("combine_multipart")
        is_seq, detected_name, sorted_items = detect_multipart_sequence(items)
        if not is_seq and len(items) > 1 and kind in ("url", "multipart"):
            tok = StopToken()
            def _probe_item(it):
                src = it.get("source", "").strip()
                if not src:
                    return
                curr_name = it.get("name", "")
                if not curr_name or not parse_multipart_info(curr_name):
                    try:
                        resolved = pre_resolve_url(src)
                        info = probe_source("url", resolved, tok, decompress=False)
                        if info.filename:
                            it["name"] = info.filename
                    except Exception:
                        pass

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(items), 8)) as executor:
                list(executor.map(_probe_item, items))

            is_seq, detected_name, sorted_items = detect_multipart_sequence(items)

        if kind == "multipart" or (combine is not False and is_seq) or (combine is True and len(items) > 1):
            use_items = sorted_items if is_seq else items
            target_name = valid_name(data.get("name") or (items[0].get("name") if len(items) == 1 else "") or detected_name or "combined.bin")
            parts_list = []
            for it in use_items:
                src = it.get("source", "").strip()
                p_kind = it.get("kind", kind if kind in ("url", "local") else ("local" if os.path.exists(src) else "url"))
                if p_kind == "url":
                    src = pre_resolve_url(src)
                    valid_url(src)
                    default = extract_url_filename(src)
                else:
                    src = os.path.abspath(os.path.expanduser(safe_text(src, "local file path", 8192)))
                    if not os.path.isfile(src):
                        raise TransferError(f"Local file not found: {src}")
                    default = Path(src).name
                p_name = valid_name(it.get("name") or default or "part.bin")
                parts_list.append({"source": src, "kind": p_kind, "name": p_name})

            dest_folder = data.get("folder")
            if dest_folder:
                dest_folder = valid_folder(dest_folder)
            else:
                ext = Path(target_name).suffix.lower()
                if ext in (".ffpfsc", ".exfat", ".ufs"):
                    dest_folder = "/data/ShadowMount"
                elif ext == ".pkg" and self.settings.get("folder") == "/data/ShadowMount":
                    dest_folder = "/data/pkg"
                else:
                    dest_folder = self.settings.get("folder", "/data/ShadowMount")

            new = [{
                "id": secrets.token_hex(6),
                "name": target_name,
                "source": f"{len(parts_list)} parts: {target_name}",
                "parts": parts_list,
                "folder": dest_folder,
                "kind": "multipart",
                "state": "queued",
                "detail": f"Queued ({len(parts_list)} parts) · direct PS5 stitch",
                "total": None,
                "transferred": 0,
                "overwrite": bool(data.get("overwrite", False)),
                "decompress": bool(data.get("decompress", True)),
                "created": time.time(),
                "identity": None
            }]
        else:
            new = []
            for item in items:
                src = item.get("source", "").strip()
                if kind == "url":
                    src = pre_resolve_url(src)
                    valid_url(src)
                    default = extract_url_filename(src)
                    item_kind = "url"
                else:
                    src = os.path.abspath(os.path.expanduser(safe_text(src, "local file path", 8192)))
                    if os.path.isdir(src):
                        item_kind = "folder"
                        default = Path(src).name
                    elif os.path.isfile(src):
                        item_kind = "local"
                        default = Path(src).name
                    else:
                        raise TransferError("Local file or folder not found. Use Choose file or folder, or enter its full path.")
                name = valid_name(item.get("name") or (data.get("name") if len(items) == 1 else "") or default or "download.bin")
                dest_folder = item.get("folder") or data.get("folder")
                if dest_folder:
                    dest_base = valid_folder(dest_folder)
                else:
                    ext = Path(name).suffix.lower()
                    if ext in (".ffpfsc", ".exfat", ".ufs"):
                        dest_base = "/data/ShadowMount"
                    elif ext == ".pkg" and self.settings.get("folder") == "/data/ShadowMount":
                        dest_base = "/data/pkg"
                    else:
                        dest_base = self.settings.get("folder", "/data/ShadowMount")

                if item_kind == "folder":
                    f_count = 0
                    f_bytes = 0
                    for r, _, fnames in os.walk(src):
                        for f in fnames:
                            if f.startswith(".") or f == "Thumbs.db":
                                continue
                            f_count += 1
                            try:
                                f_bytes += os.path.getsize(os.path.join(r, f))
                            except OSError:
                                pass
                    if dest_base.rstrip("/").endswith("/" + name):
                        target_folder = dest_base.rstrip("/")
                    else:
                        target_folder = f"{dest_base.rstrip('/')}/{name}"
                    new.append({
                        "id": secrets.token_hex(6),
                        "name": name,
                        "source": src,
                        "kind": "folder",
                        "folder": target_folder,
                        "files_count": f_count,
                        "completed_files": [],
                        "state": "queued",
                        "detail": f"Queued folder ({f_count} files) · waiting to start",
                        "total": f_bytes,
                        "transferred": 0,
                        "overwrite": bool(data.get("overwrite", False)),
                        "decompress": False,
                        "created": time.time(),
                        "identity": None
                    })
                else:
                    new.append({
                        "id": secrets.token_hex(6),
                        "name": name,
                        "source": src,
                        "kind": item_kind,
                        "folder": dest_base,
                        "state": "queued",
                        "detail": "Waiting to start",
                        "total": None,
                        "transferred": 0,
                        "overwrite": bool(data.get("overwrite", False)),
                        "decompress": bool(data.get("decompress", True)),
                        "created": time.time(),
                        "identity": None
                    })
        with self.lock:
            if len(self.jobs) + len(new) > 500:
                raise TransferError("Queue is full. Clear completed jobs first.")
            occupied = {j["name"] for j in self.jobs if j["state"] not in ("completed", "cancelled")}
            for j in new:
                if j["name"] in occupied:
                    raise TransferError(f"A job named {j['name']} is already in the queue. Use a different destination name.")
                occupied.add(j["name"])
            self.jobs.extend(new)
            self.save()
        self.wake.set()
        return {"ok": True, "count": len(new)}

    def action(self, action, job_id=None, extra=None):
        extra = extra or {}
        with self.lock:
            if action == "start":
                if not self.settings["host"]:
                    raise TransferError("Save your PS5 IP address in Settings first.")
                if self.diagnostic["state"] == "running":
                    raise TransferError("Wait for the diagnostic to finish, or stop it first.")
                self.running = True
            elif action == "pause_all":
                self.running = False
                if self.current:
                    job = next(j for j in self.jobs if j["id"] == self.current)
                    job["state"] = "pausing"
                    job["detail"] = "Stopping network activity; partial file will be retained"
                    self.token.cancel()
            elif action == "clear_completed":
                self.jobs = [j for j in self.jobs if j["state"] != "completed"]
            elif action == "reorder":
                job = next((j for j in self.jobs if j["id"] == job_id), None)
                if not job:
                    raise TransferError("Job no longer exists.")
                if job["id"] == self.current:
                    raise TransferError("Cannot move the active transfer.")
                new_idx = int(extra.get("new_index", 0))
                new_idx = max(0, min(len(self.jobs) - 1, new_idx))
                old_idx = self.jobs.index(job)
                if old_idx != new_idx:
                    self.jobs.pop(old_idx)
                    self.jobs.insert(new_idx, job)
            elif action == "bulk_pause":
                ids = set(extra.get("ids", []))
                for j in self.jobs:
                    if j["id"] in ids and j["state"] not in ("completed", "cancelled"):
                        if j["id"] == self.current:
                            j["state"] = "pausing"
                            self.token.cancel()
                        else:
                            j["state"] = "paused"
                            j["detail"] = "Paused"
            elif action == "bulk_resume":
                ids = set(extra.get("ids", []))
                for j in self.jobs:
                    if j["id"] in ids and j["state"] in ("paused", "failed", "cancelled"):
                        j["state"] = "queued"
                        j["detail"] = "Queued to resume"
            elif action == "bulk_remove":
                ids = set(extra.get("ids", []))
                if self.current in ids:
                    raise TransferError("Pause the active transfer before removing it.")
                self.jobs = [j for j in self.jobs if j["id"] not in ids]
            elif action == "bulk_merge":
                ids = list(extra.get("ids", []))
                if len(ids) < 2:
                    raise TransferError("Select at least 2 jobs to merge.")
                if self.current and self.current in ids:
                    raise TransferError("Pause the active transfer before merging.")

                target_jobs = [j for j in self.jobs if j["id"] in ids]
                if len(target_jobs) < 2:
                    raise TransferError("At least 2 jobs must exist in the queue to merge.")
                for j in target_jobs:
                    if j["state"] == "completed":
                        raise TransferError("Cannot merge completed transfers.")

                all_parts = []
                for j in target_jobs:
                    if j.get("kind") == "multipart" and j.get("parts"):
                        for p in j["parts"]:
                            all_parts.append(dict(p))
                    else:
                        p_name = j.get("name") or extract_url_filename(j.get("source", "")) or "part.bin"
                        all_parts.append({
                            "source": j.get("source", ""),
                            "kind": j.get("kind", "url"),
                            "name": p_name
                        })

                is_seq, detected_name, sorted_parts = detect_multipart_sequence(all_parts)
                if not is_seq:
                    tok = StopToken()
                    for p in all_parts:
                        if p.get("kind") == "url" and not parse_multipart_info(p.get("name", "")):
                            try:
                                resolved = pre_resolve_url(p["source"])
                                info = probe_source("url", resolved, tok, decompress=False)
                                if info.filename:
                                    p["name"] = info.filename
                            except Exception:
                                pass
                    is_seq, detected_name, sorted_parts = detect_multipart_sequence(all_parts)

                use_parts = sorted_parts if is_seq else all_parts
                target_name = valid_name(extra.get("name") or detected_name or (use_parts[0]["name"] if is_seq else "merged_archive.bin"))

                dest_folder = target_jobs[0].get("folder") or self.settings.get("folder", "/data/ShadowMount")
                ext = Path(target_name).suffix.lower()
                if ext in (".ffpfsc", ".exfat", ".ufs"):
                    dest_folder = "/data/ShadowMount"
                elif ext == ".pkg" and self.settings.get("folder") == "/data/ShadowMount":
                    dest_folder = "/data/pkg"

                total_sizes = [j.get("total") for j in target_jobs if j.get("total") is not None]
                merged_total = sum(total_sizes) if len(total_sizes) == len(target_jobs) else None

                merged_job = {
                    "id": secrets.token_hex(6),
                    "name": target_name,
                    "source": f"{len(use_parts)} parts: {target_name}",
                    "parts": use_parts,
                    "folder": dest_folder,
                    "kind": "multipart",
                    "state": "queued",
                    "detail": f"Merged ({len(use_parts)} parts) · direct PS5 stitch",
                    "total": merged_total,
                    "transferred": 0,
                    "overwrite": any(bool(j.get("overwrite")) for j in target_jobs),
                    "decompress": any(bool(j.get("decompress", True)) for j in target_jobs),
                    "created": min(j.get("created", time.time()) for j in target_jobs),
                    "identity": None
                }

                first_idx = min(self.jobs.index(j) for j in target_jobs)
                self.jobs = [j for j in self.jobs if j["id"] not in ids]
                self.jobs.insert(first_idx, merged_job)
                self.save()
                self.wake.set()
                return {"ok": True, "merged_id": merged_job["id"], "name": target_name, "parts_count": len(use_parts)}
            elif action == "validate_links":
                threading.Thread(target=self._validate_queued_links, daemon=True, name="link-validator").start()
                return {"ok": True, "message": "Link validation started in background"}
            else:
                job = next((j for j in self.jobs if j["id"] == job_id), None)
                if not job:
                    raise TransferError("Job no longer exists.")
                if action in ("pause", "cancel"):
                    if job["id"] == self.current:
                        job["state"] = "pausing" if action == "pause" else "cancelling"
                        self.token.cancel()
                    elif job["state"] != "completed":
                        job["state"] = "paused" if action == "pause" else "cancelled"
                        job["detail"] = "Partial file retained on PS5, if any"
                elif action in ("resume", "restart"):
                    if job["id"] == self.current:
                        raise TransferError("Wait for the current operation to stop.")
                    if job["state"] == "completed":
                        raise TransferError("This job is already complete.")
                    if action == "restart":
                        # New partial path; never delete a potentially valuable old partial silently.
                        job["id"] = secrets.token_hex(6)
                        job["identity"] = None
                        job["stage_owned"] = False
                        job["transferred"] = 0
                        job["destination"] = None
                    job["state"] = "queued"
                    job["detail"] = "Queued to resume" if action == "resume" else "Queued to restart from zero; old partial retained"
                elif action == "remove":
                    if job["id"] == self.current:
                        raise TransferError("Pause the job before removing it.")
                    self.jobs.remove(job)
                elif action in ("up", "down"):
                    i = self.jobs.index(job)
                    ni = i + (-1 if action == "up" else 1)
                    if 0 <= ni < len(self.jobs) and job["id"] != self.current and self.jobs[ni]["id"] != self.current:
                        self.jobs[i], self.jobs[ni] = self.jobs[ni], self.jobs[i]
                elif action == "edit":
                    raise TransferError("Use the edit endpoint to change the source link.")
                elif action == "merge_next":
                    if job["id"] == self.current:
                        raise TransferError("Pause the transfer before merging.")
                    if job["state"] == "completed":
                        raise TransferError("Cannot merge completed transfers.")
                    idx = self.jobs.index(job)
                    if idx + 1 >= len(self.jobs):
                        raise TransferError("No subsequent transfer found to merge with.")
                    next_job = self.jobs[idx + 1]
                    if next_job["id"] == self.current:
                        raise TransferError("Pause the next transfer before merging.")
                    if next_job["state"] == "completed":
                        raise TransferError("Cannot merge with a completed transfer.")
                    return self.action("bulk_merge", extra={"ids": [job["id"], next_job["id"]]})
                elif action == "toggle_extract":
                    if job["id"] == self.current:
                        raise TransferError("Pause the transfer before changing extraction settings.")
                    if job["state"] == "completed":
                        raise TransferError("This transfer is already completed.")
                    curr = job.get("decompress", True)
                    job["decompress"] = not curr
                    job["identity"] = None
                    job["stage_owned"] = False
                    job["transferred"] = 0
                    if job["decompress"]:
                        job["detail"] = "Extraction enabled: will decompress directly on PS5"
                    else:
                        job["detail"] = "Extraction disabled: will stream raw archive as-is"
                else:
                    raise TransferError("Unknown queue action.")
            self.save()
            self.wake.set()
        return {"ok": True}

    def _validate_queued_links(self):
        tok = StopToken()
        with self.lock:
            pending = [j for j in self.jobs if j["kind"] in ("url", "multipart") and j["state"] in ("queued", "paused")]
        self.log("info", f"Validating {len(pending)} queued links…")
        for j in pending:
            try:
                tok.check()
                if j["kind"] == "multipart":
                    info = validate_multipart_source(j.get("parts") or j["source"], tok)
                    with self.lock:
                        target = next((item for item in self.jobs if item["id"] == j["id"]), None)
                        if target and target["id"] != self.current:
                            if info["valid"]:
                                if info["size"] is not None:
                                    target["total"] = info["size"]
                                target["detail"] = f"Verified {info['parts_count']} parts · direct PS5 stitch"
                            else:
                                target["detail"] = f"Part check failed: {info['error']}"
                else:
                    info = validate_source_url(j["source"], tok, decompress=j.get("decompress", True))
                    with self.lock:
                        target = next((item for item in self.jobs if item["id"] == j["id"]), None)
                        if target and target["id"] != self.current:
                            if info["valid"]:
                                if info["size"] is not None:
                                    target["total"] = info["size"]
                                if info.get("filename") and (target["name"] in ("download.bin", "file", "view", "uc", "", j.get("name")) or not Path(target["name"]).suffix):
                                    target["name"] = info.get("filename")
                                if info.get("is_zip"):
                                    target["detail"] = f"ZIP Archive · Streaming '{info.get('filename')}' directly to PS5"
                                else:
                                    status = "Parallel ranges OK" if info["ranges"] else "Single-stream only"
                                    target["detail"] = f"Link verified · {status}"
                            else:
                                target["detail"] = f"Link check failed: {info['error']}"
                self.save()
            except Exception:
                pass
            time.sleep(0.1)
        self.log("info", "Link validation finished.")

    def edit_job(self, data):
        with self.lock:
            job = next((j for j in self.jobs if j["id"] == data.get("id")), None)
            if not job or job["id"] == self.current or job["kind"] != "url" or job["state"] == "completed":
                raise TransferError("Pause a URL job before updating its link.")
            new_src = valid_url(data.get("source", ""))
            job["source"] = pre_resolve_url(new_src)
            job["link_updated"] = True
            job["detail"] = "Link updated. Ready to resume with fresh link."
            self.save()
        return {"ok": True}

    def verify_links(self, data):
        raw_urls = data.get("urls")
        if not raw_urls:
            single = data.get("url")
            raw_urls = [single] if single else []
        if isinstance(raw_urls, str):
            raw_urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
        if not raw_urls or not isinstance(raw_urls, list):
            raise TransferError("Please enter at least one URL to verify.")
        if len(raw_urls) > 50:
            raise TransferError("Cannot verify more than 50 links at once.")

        results = []
        token = StopToken()
        overall_ok = True

        for raw_url in raw_urls:
            src = str(raw_url).strip()
            if not src:
                continue
            try:
                resolved = pre_resolve_url(src)
                valid_url(resolved)
                info = probe_source("url", resolved, token, decompress=False)
                size_b = info.size
                if size_b is not None:
                    if size_b >= 1e9:
                        size_str = f"{size_b / 1e9:.2f} GB"
                    elif size_b >= 1e6:
                        size_str = f"{size_b / 1e6:.1f} MB"
                    else:
                        size_str = f"{size_b / 1e3:.0f} KB"
                else:
                    size_str = "Unknown size"

                results.append({
                    "url": src,
                    "resolved_url": resolved if resolved != src else None,
                    "ok": True,
                    "filename": info.filename or extract_url_filename(resolved),
                    "size": size_b,
                    "size_formatted": size_str,
                    "ranges": bool(info.ranges),
                    "resumable": bool(info.resumable()),
                })
            except Exception as e:
                overall_ok = False
                results.append({
                    "url": src,
                    "ok": False,
                    "error": str(e),
                })

        return {
            "ok": overall_ok,
            "results": results,
            "count": len(results),
            "verified_count": sum(1 for r in results if r.get("ok")),
        }

    def _report(self, job, event, value):
        with self.lock:
            if event == "status":
                job["detail"] = value
                self.log("info", f"{job['name']}: {value}")
            elif event == "source":
                self.log("info", f"Source: {'parallel ranges available' if value['ranges'] else 'single stream only'}; resume {'available' if value['resumable'] else 'unavailable without stable source metadata'}.")
            elif event == "progress":
                self.metrics = value
                self.history.append({"up": value["upload_bps"], "down": value["download_bps"]})
                self.history = self.history[-120:]
            elif event == "complete":
                job["state"] = "completed"
                job["detail"] = "Complete · PS5 file size verified"
                job["finished"] = time.time()
                self.log("success", f"{job['name']}: complete. {value['verification']}")
                self.save()
                size_str = f" ({value['size']/1e9:.2f} GB)" if value.get("size") else ""
                notify_macos("Direct Stream for PlayStation 5", f"{job['name']}{size_str} verified on PS5", sound="Glass")

    def _loop(self):
        while not self.stopping.is_set():
            self.wake.wait(.5)
            self.wake.clear()
            with self.lock:
                job = next((j for j in self.jobs if j["state"] == "queued"), None) if self.running else None
                if not job:
                    self.running = False
                    continue
                cfg = {**self.settings, "password": self.password}
                destination = {k: cfg[k] for k in ("host", "port", "folder", "username")}
                if job.get("destination") and job["destination"] != destination:
                    job["state"] = "failed"
                    job["detail"] = "PS5 destination changed. Restore the original settings to resume, or Restart for this destination."
                    self.running = False
                    self.save()
                    continue
                job["destination"] = destination
                self.current = job["id"]
                self.token = token = StopToken()
                job["state"] = "starting"
                self.metrics = {}
                self.history = []
                self.save()
            caffeinate = None
            try:
                if sys.platform == "darwin":
                    caffeinate = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
                with self.ftp_lock:
                    for attempt in range(cfg["retries"] + 1):
                        token.check()
                        with self.lock:
                            token.check()
                            job["state"] = "running"
                        try:
                            transfer(job, cfg, token, lambda e, v: self._report(job, e, v), self.save)
                            break
                        except (TransferError, Cancelled, ftplib.error_perm):
                            raise
                        except Exception as e:
                            token.check()
                            if attempt >= cfg["retries"]:
                                raise
                            delay = min(30, 5 * 2 ** attempt)
                            with self.lock:
                                job["state"] = "retrying"
                                job["detail"] = f"Retry {attempt + 1}/{cfg['retries']} in {delay}s · {friendly_error(e)}"
                                self.log("warning", job["detail"])
                            token.wait(delay)
            except Exception as e:
                with self.lock:
                    if token.event.is_set():
                        job["state"] = "cancelled" if job["state"] == "cancelling" else "paused"
                        job["detail"] = "Stopped · partial retained on PS5; resume checks its actual size"
                    else:
                        job["state"] = "failed"
                        job["detail"] = friendly_error(e)
                        self.running = False
                        self.log("error", f"{job['name']}: {job['detail']}")
                        notify_macos("Direct Stream for PlayStation 5", f"{job['name']}: {job['detail']}", sound="Basso")
            finally:
                token.cancel()
                if caffeinate:
                    caffeinate.terminate()
                    try:
                        caffeinate.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        caffeinate.kill()
                        caffeinate.wait()
                with self.lock:
                    self.current = None
                    self.token = None
                    self.metrics = {}
                    self.save()
                # Let modest FTP payloads release resources before the next session.
                self.stopping.wait(.5)
                self.wake.set()

    def begin_diagnostic(self, kind, data):
        with self.lock:
            if self.current or self.diagnostic["state"] == "running":
                raise TransferError("Pause the queue before running a diagnostic.")
            if kind not in ("connection", "source", "files"):
                raise TransferError("Unknown diagnostic.")
            if kind != "source" and not self.settings["host"]:
                raise TransferError("Save your PS5 address in Settings first.")
            self.running = False
            self.diagnostic_token = StopToken()
            self.diagnostic = {"state": "running", "kind": kind, "message": "Working…"}
            cfg = {**self.settings, "password": self.password}
        threading.Thread(target=self._diagnostic, args=(kind, data, cfg, self.diagnostic_token), daemon=True).start()
        return {"ok": True}

    def _diagnostic(self, kind, data, cfg, token):
        ftp = reader = None
        held_ftp_lock = False
        result = None
        try:
            if kind != "source":
                self.ftp_lock.acquire()
                held_ftp_lock = True
            if kind == "source" and data.get("matrix"):
                source = probe_source("url", data.get("source", ""), token)
                if not source.ranges or source.size is None:
                    r = measure_source(source, {**cfg, "streams": 1}, token, 8)
                    result = {"state": "done", "kind": kind, "bps": r["steady"],
                        "best_label": "Single stream (server has no range support)",
                        "best_config": {"streams": 1, "chunk_mb": 4, "buffer_mb": 32},
                        "message": "This link does not support byte ranges; only a single stream is possible: "
                                   f"{r['steady']/1e6:.1f} MB/s."}
                else:
                    is_full = bool(data.get("full") or data.get("matrix_mode") == "full")
                    if is_full:
                        variants = [
                            ("Low-Bandwidth / Wi-Fi Tier (2 streams × 2 MiB, 32 MiB RAM)", {"streams": 2, "chunk_mb": 2, "buffer_mb": 32}),
                            ("Standard Entry Broadband (4 streams × 4 MiB, 64 MiB RAM)", {"streams": 4, "chunk_mb": 4, "buffer_mb": 64}),
                            ("Stable Mid-Bandwidth (6 streams × 4 MiB, 96 MiB RAM)", {"streams": 6, "chunk_mb": 4, "buffer_mb": 96}),
                            ("Balanced Default (8 streams × 8 MiB, 128 MiB RAM)", {"streams": 8, "chunk_mb": 8, "buffer_mb": 128}),
                            ("High-Speed Fiber (12 streams × 8 MiB, 192 MiB RAM)", {"streams": 12, "chunk_mb": 8, "buffer_mb": 192}),
                            ("Fast Turbo (16 streams × 8 MiB, 256 MiB RAM)", {"streams": 16, "chunk_mb": 8, "buffer_mb": 256}),
                            ("Ultra-Wide Pipeline (16 streams × 16 MiB, 384 MiB RAM)", {"streams": 16, "chunk_mb": 16, "buffer_mb": 384}),
                            ("Max Saturation (16 streams × 32 MiB, 512 MiB RAM)", {"streams": 16, "chunk_mb": 32, "buffer_mb": 512}),
                        ]
                        duration_per = 8
                    else:
                        variants = [
                            ("Tier 1: Conservative / Wi-Fi (4 streams × 4 MiB, 64 MiB RAM)", {"streams": 4, "chunk_mb": 4, "buffer_mb": 64}),
                            ("Tier 2: Balanced Standard (8 streams × 8 MiB, 128 MiB RAM)", {"streams": 8, "chunk_mb": 8, "buffer_mb": 128}),
                            ("Tier 3: Gigabit Turbo (16 streams × 16 MiB, 384 MiB RAM)", {"streams": 16, "chunk_mb": 16, "buffer_mb": 384}),
                        ]
                        duration_per = 10
                    lines, best = [], (0, variants[0][0], variants[0][1])
                    tiers = []
                    for i, (label, over) in enumerate(variants):
                        token.check()
                        with self.lock:
                            self.diagnostic = {"state": "running", "kind": kind,
                                "message": f"Test {i + 1}/{len(variants)}: {label}…"}
                        r = measure_source(source, {**cfg, **over}, token, duration_per)
                        lines.append(f"{r['steady']/1e6:5.1f} MB/s · {label} (first data: {r['first_byte']*1000:.0f} ms)")
                        clean_label = label.split("(")[0].strip()
                        specs = label.split("(")[1].replace(")", "").strip() if "(" in label else ""
                        tiers.append({
                            "label": label,
                            "clean_label": clean_label,
                            "specs": specs,
                            "bps": r["steady"],
                            "mbps": round(r["steady"] / 1e6, 1),
                            "latency_ms": round(r["first_byte"] * 1000),
                            "config": over,
                        })
                        if r["steady"] > best[0]:
                            best = (r["steady"], label, over)
                        token.wait(0.5)
                    for t in tiers:
                        t["is_best"] = (t["bps"] == best[0])
                    result = {"state": "done", "kind": kind, "bps": best[0],
                        "best_label": best[1], "best_config": best[2],
                        "tiers": tiers,
                        "message": "Optimal config: " + best[1] + f" ({best[0]/1e6:.1f} MB/s)\n\n" + "\n".join(lines) +
                                   "\n\nEach tier tested against actual direct download chunks. Bytes discarded from RAM."}
            elif kind == "source":
                source = probe_source("url", data.get("source", ""), token)
                r = measure_source(source, cfg, token, 12)
                mode = (f"{cfg['streams']} streams × {cfg['chunk_mb']} MiB ranges" if r["parallel"]
                        else "single stream (server does not support ranges, or streams = 1)")
                result = {"state": "done", "kind": kind, "bps": r["steady"],
                    "bytes": r["bytes"], "seconds": r["seconds"], "ranges": source.ranges, "size": source.size,
                    "parallel": r["parallel"], "first_byte_ms": round(r["first_byte"] * 1000),
                    "mode_label": mode,
                    "message": (f"Steady {r['steady']/1e6:.1f} MB/s (whole test incl. startup {r['avg']/1e6:.1f}) · "
                                f"first data after {r['first_byte']*1000:.0f} ms · {mode}. "
                                "Bytes discarded from RAM; PS5 was not contacted.")}
            else:
                t0 = time.monotonic()
                ftp = connect_ftp(cfg, token)
                ms = round((time.monotonic() - t0) * 1000)
                folder = valid_folder(data.get("folder", cfg["folder"]))
                space_str = ""
                try:
                    free_b = check_ftp_storage(ftp, folder)
                    if free_b is not None:
                        space_str = f" · {free_b/1e9:.1f} GB free on PS5"
                except Exception:
                    pass
                try:
                    ftp.cwd(folder)
                except ftplib.error_perm:
                    if kind == "files":
                        raise TransferError("Cannot open this folder. Check that it exists and is readable.")
                    result = {"state": "done", "kind": kind, "message": f"Connected in {ms} ms{space_str}. Destination will be created when uploading."}
                else:
                    result = {"state": "done", "kind": kind, "message": f"Connected in {ms} ms · destination folder found{space_str}"}
                    if kind == "files":
                        files = []
                        try:
                            for name, facts in ftp.mlsd():
                                token.check()
                                if facts.get("type") in ("cdir", "pdir"):
                                    continue
                                files.append({"name": name, "type": facts.get("type", "file"), "size": facts.get("size", ""), "modified": facts.get("modify", "")})
                                if len(files) >= 2000:
                                    raise TransferError("Folder has over 2,000 entries. Open a smaller subfolder.")
                        except ftplib.error_perm:
                            files = []
                            def append_line(line):
                                token.check()
                                if len(files) >= 2000:
                                    raise TransferError("Folder listing is too large. Open a smaller subfolder.")
                                files.append({"name": line, "type": "listing", "size": "", "modified": ""})
                            ftp.retrlines("LIST", append_line)
                        result.update({"files": files, "folder": folder, "message": f"{len(files)} entries in {folder}"})
                if kind == "connection":
                    with self.lock:
                        self.connection = {"state": "connected", "message": result["message"]}
        except Exception as e:
            result = {"state": "error", "kind": kind, "message": "Diagnostic stopped" if token.event.is_set() else friendly_error(e)}
            if kind == "connection":
                with self.lock:
                    self.connection = {"state": "error", "message": result["message"]}
        finally:
            if reader:
                reader.close()
            close_ftp(ftp, token, True)
            token.cancel()
            if held_ftp_lock:
                self.ftp_lock.release()
            with self.lock:
                self.diagnostic = result or {"state": "error", "kind": kind, "message": "Diagnostic stopped"}

    def stop(self):
        self.stopping.set()
        with self.lock:
            self.running = False
            if self.token:
                self.token.cancel()
            if self.diagnostic_token:
                self.diagnostic_token.cancel()
        self.wake.set()
        self.thread.join(4)
        self.save()


class Handler(BaseHTTPRequestHandler):
    server_version = "PS5Transfer/2"

    def log_message(self, *args):
        pass

    def send(self, code, body, content_type="application/json", extra_headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if extra_headers:
            for hk, hv in extra_headers.items():
                self.send_header(hk, hv)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, auth=False):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            self.send(403, {"error": "Invalid host"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + expected:
            self.send(403, {"error": "Cross-origin requests are not allowed"})
            return False
        if auth:
            req_token = self.headers.get("X-Session-Token", "")
            if not req_token and "Cookie" in self.headers:
                cookie_str = self.headers.get("Cookie", "")
                for part in cookie_str.split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "ps5_session":
                        req_token = v
                        break
            if not secrets.compare_digest(req_token, self.server.token):
                self.send(401, {"error": "This app session expired. Reopen the app."})
                return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.allowed(path.startswith("/api/")):
            return
        if path == "/api/state":
            self.send(200, self.server.manager.snapshot())
            return
        assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/icon.svg": "icon.svg", "/manifest.json": "manifest.json"}
        if path not in assets:
            self.send(404, {"error": "Not found"})
            return
        file = BASE / "web" / assets[path]
        extra = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
        if path == "/":
            extra["Set-Cookie"] = f"ps5_session={self.server.token}; Path=/; SameSite=Lax"
        self.send(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] or "application/octet-stream", extra_headers=extra)

    def do_POST(self):
        if not self.allowed(True):
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise TransferError("Expected a JSON request.")
            n = int(self.headers.get("Content-Length", 0))
            if not 0 < n <= 512 * 1024:
                raise TransferError("Invalid request size.")
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict):
                raise TransferError("Invalid request.")
            m = self.server.manager
            route = urllib.parse.urlsplit(self.path).path
            if route == "/api/settings":
                result = m.configure(data)
            elif route == "/api/jobs":
                result = m.add_jobs(data)
            elif route == "/api/action":
                result = m.action(data.get("action"), data.get("id"), data)
            elif route == "/api/edit":
                result = m.edit_job(data)
            elif route == "/api/diagnostic":
                result = m.begin_diagnostic(data.get("kind"), data)
            elif route == "/api/stop-diagnostic":
                if m.diagnostic_token:
                    m.diagnostic_token.cancel()
                result = {"ok": True}
            elif route == "/api/pick":
                pick_type = data.get("type", "file")
                paths, folder_path = [], ""
                picker_unsupported = False
                if sys.platform == "darwin":
                    try:
                        if pick_type == "folder":
                            script = 'tell application (path to frontmost application as text) to POSIX path of (choose folder with prompt "Choose folder containing PS5 game images or packages")'
                            proc = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=120)
                            folder_path = proc.stdout.strip() if proc.returncode == 0 else ""
                        else:
                            script = 'tell application (path to frontmost application as text)\n' \
                                     '  set chosen to choose file with prompt "Choose game files or packages to send to PS5" with multiple selections allowed\n' \
                                     'end tell\n' \
                                     'set outPaths to ""\n' \
                                     'repeat with aFile in chosen\n' \
                                     '  set outPaths to outPaths & (POSIX path of aFile) & linefeed\n' \
                                     'end repeat\n' \
                                     'return outPaths'
                            proc = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=120)
                            paths = [p.strip() for p in proc.stdout.splitlines() if p.strip()] if proc.returncode == 0 else []
                    except Exception:
                        paths, folder_path = [], ""
                elif sys.platform == "win32":
                    try:
                        if pick_type == "folder":
                            ps_cmd = 'Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; $f.Description = "Choose folder containing PS5 game images or packages"; if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }'
                            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], capture_output=True, text=True, timeout=120)
                            folder_path = proc.stdout.strip() if proc.returncode == 0 else ""
                        else:
                            ps_cmd = 'Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.OpenFileDialog; $f.Title = "Choose game files or packages to send to PS5"; $f.Multiselect = $true; $f.Filter = "PlayStation Games (*.pkg;*.ffpfsc;*.exfat;*.ufs;*.iso;*.bin;*.zip)|*.pkg;*.ffpfsc;*.exfat;*.ufs;*.iso;*.bin;*.zip|All Files (*.*)|*.*"; if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $f.FileNames | ForEach-Object { Write-Output $_ } }'
                            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], capture_output=True, text=True, timeout=120)
                            paths = [p.strip() for p in proc.stdout.splitlines() if p.strip()] if proc.returncode == 0 else []
                    except Exception:
                        pass
                else:
                    if shutil.which("zenity"):
                        try:
                            if pick_type == "folder":
                                proc = subprocess.run(["zenity", "--file-selection", "--directory", "--title=Choose folder containing PS5 packages"], capture_output=True, text=True, timeout=120)
                                folder_path = proc.stdout.strip() if proc.returncode == 0 else ""
                            else:
                                proc = subprocess.run(["zenity", "--file-selection", "--multiple", "--separator=\n", "--title=Choose package files to send to PS5"], capture_output=True, text=True, timeout=120)
                                paths = [p.strip() for p in proc.stdout.splitlines() if p.strip()] if proc.returncode == 0 else []
                        except Exception:
                            pass
                    else:
                        picker_unsupported = True

                if pick_type == "folder":
                    total_size = 0
                    file_count = 0
                    folder_name = ""
                    if folder_path and os.path.isdir(folder_path):
                        folder_name = os.path.basename(folder_path.rstrip("/\\"))
                        for r, _, fnames in os.walk(folder_path):
                            for f in fnames:
                                if f.startswith(".") or f == "Thumbs.db":
                                    continue
                                file_count += 1
                                try:
                                    total_size += os.path.getsize(os.path.join(r, f))
                                except OSError:
                                    pass
                    result = {
                        "is_folder": True,
                        "path": folder_path,
                        "name": folder_name,
                        "files_count": file_count,
                        "total_size": total_size,
                        "paths": [folder_path] if folder_path else [],
                        "picker_unsupported": picker_unsupported and not folder_path
                    }
                else:
                    result = {"paths": paths, "path": paths[0] if paths else "", "picker_unsupported": picker_unsupported and not paths}
            elif route == "/api/verify":
                result = m.verify_links(data)
            elif route == "/api/shutdown":
                result = {"ok": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send(404, {"error": "Not found"})
                return
            self.send(200, result)
        except (TransferError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.send(400, {"error": str(e) if isinstance(e, TransferError) else "Invalid request. Check the fields and try again."})
        except Exception as e:
            self.send(500, {"error": friendly_error(e)})


def _lock_instance(directory: Path):
    """Acquire single-instance file lock across macOS, Linux, and Windows.
    Returns the open file object if lock acquired, or None if already running."""
    lock_path = directory / "instance.lock"
    lockfile = open(lock_path, "a+b")
    if os.name == "nt":
        try:
            import msvcrt
            if lockfile.tell() == 0:
                lockfile.write(b"\0")
                lockfile.flush()
            lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            return lockfile
        except (OSError, PermissionError):
            lockfile.close()
            return None
        except ImportError:
            return lockfile
    else:
        try:
            import fcntl
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lockfile
        except (BlockingIOError, OSError):
            lockfile.close()
            return None
        except ImportError:
            return lockfile


def main():
    parser = argparse.ArgumentParser(description="DIRECT STREAM FOR PLAYSTATION 5 — local dashboard")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind to (e.g. 127.0.0.1 or 0.0.0.0)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", default=str(Path.home() / ".ps5-transfer"))
    args = parser.parse_args()
    directory = Path(args.data_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    # One instance per profile. Reopening the app reuses its existing dashboard.
    lockfile = _lock_instance(directory)
    if lockfile is None:
        try:
            session = json.loads((directory / "session.json").read_text())
            if not args.no_browser:
                open_browser(session["url"])
            print("DIRECT STREAM FOR PLAYSTATION 5 is already running.", flush=True)
        except Exception:
            print("DIRECT STREAM FOR PLAYSTATION 5 is already starting. Try opening it again in a moment.", flush=True)
        return
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.token = secrets.token_urlsafe(32)
    server.manager = Manager(directory)
    
    local_url = f"http://127.0.0.1:{server.server_port}/#session={server.token}"
    if args.host == "0.0.0.0":
        lan_ip = get_lan_ip()
        network_url = f"http://{lan_ip}:{server.server_port}/#session={server.token}"
        url = local_url
        print(f"DIRECT STREAM FOR PLAYSTATION 5 {VERSION}\nLocal URL:   {local_url}\nNetwork URL: {network_url}\nUse Quit app in the dashboard to stop the background process.", flush=True)
    else:
        url = f"http://{args.host}:{server.server_port}/#session={server.token}"
        print(f"DIRECT STREAM FOR PLAYSTATION 5 {VERSION}\nOpen {url}\nUse Quit app in the dashboard to stop the background process.", flush=True)

    atomic_json(directory / "session.json", {"url": url, "pid": os.getpid()})
    if not args.no_browser:
        open_browser(url)
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.manager.stop()
        server.server_close()
        (directory / "session.json").unlink(missing_ok=True)
        lockfile.close()

if __name__ == "__main__":
    main()
