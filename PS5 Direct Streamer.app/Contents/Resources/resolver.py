"""Direct Stream for PlayStation 5 — Link Resolver Engine.

Provides automatic resolution of direct download links from common file hosting
services (MediaFire, PixelDrain, Google Drive, Internet Archive, GoFile, etc.)
and generic web pages, allowing users to paste sharing/landing page links.
"""

import os
import re
import urllib.parse
from pathlib import Path


def pre_resolve_url(url: str) -> str:
    """Pre-resolve known URL patterns before sending network requests."""
    if not url or not isinstance(url, str):
        return url
    url = url.strip()

    # 1. PixelDrain: https://pixeldrain.com/u/<id> -> https://pixeldrain.com/api/file/<id>
    m_pixel = re.match(r"^https?://(?:www\.)?pixeldrain\.com/u/([a-zA-Z0-9_-]+)", url, re.I)
    if m_pixel:
        return f"https://pixeldrain.com/api/file/{m_pixel.group(1)}"

    # 2. Google Drive: /file/d/<id>/view -> /uc?export=download&id=<id>
    m_gdrive = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([a-zA-Z0-9_-]+)", url, re.I)
    if m_gdrive:
        file_id = m_gdrive.group(1)
        if "export=download" not in url:
            return f"https://drive.google.com/uc?export=download&id={file_id}"

    # 3. Internet Archive: /details/<id>/<filename> -> /download/<id>/<filename>
    m_archive = re.match(r"^https?://(?:www\.)?archive\.org/details/([^/]+)/([^?#]+)", url, re.I)
    if m_archive:
        return f"https://archive.org/download/{m_archive.group(1)}/{m_archive.group(2)}"

    return url


def extract_download_link_from_html(page_url: str, html_text: str) -> str | None:
    """Inspect HTML page content to extract direct binary download links."""
    if not html_text or not isinstance(html_text, str):
        return None

    parsed = urllib.parse.urlsplit(page_url)
    domain = parsed.netloc.lower()

    # 1. MediaFire: Extract downloadButton anchor or direct storage link
    if "mediafire.com" in domain:
        patterns = [
            r'id=["\']downloadButton["\'][^>]*href=["\'](https?://[^"\']+)["\']',
            r'aria-label=["\']Download file["\'][^>]*href=["\'](https?://[^"\']+)["\']',
            r'href=["\'](https?://download[^"\']+\.mediafire\.com/[^"\']+)["\']',
            r'href=["\'](https?://[^"\']+)["\'][^>]*id=["\']downloadButton["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html_text, re.I)
            if m:
                return m.group(1).replace("&amp;", "&")

    # 2. Google Drive: Extract "Download anyway" virus confirmation link/form
    if "google.com" in domain or "googleusercontent.com" in domain:
        # Check direct confirmation link (<a id="uc-download-link" href="...">)
        m_link = re.search(r'id=["\']uc-download-link["\'][^>]*href=["\']([^"\']+)["\']', html_text, re.I)
        if m_link:
            return urllib.parse.urljoin(page_url, m_link.group(1).replace("&amp;", "&"))

        # Check confirmation form (<form id="download-form" action="...">)
        m_form = re.search(r'<form[^>]*action=["\']([^"\']+)["\'][^>]*id=["\']download-form["\']', html_text, re.I)
        if not m_form:
            m_form = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html_text, re.I)
        if m_form:
            action = urllib.parse.urljoin(page_url, m_form.group(1).replace("&amp;", "&"))
            inputs = dict(re.findall(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', html_text, re.I))
            if "confirm" in inputs or "id" in inputs:
                query = urllib.parse.urlencode(inputs)
                sep = "&" if "?" in action else "?"
                return f"{action}{sep}{query}"

    # 3. GoFile: JSON response or direct link in page
    if "gofile.io" in domain:
        m = re.search(r'"link":\s*"(https?://store[^"]+)"', html_text)
        if m:
            return m.group(1).replace(r"\/", "/")

    # 4. Generic Web Page Heuristics:
    # A) Meta refresh redirect (<meta http-equiv="refresh" content="...;url=(...)">)
    m_refresh = re.search(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*url=([^"\'>\s]+)', html_text, re.I)
    if m_refresh:
        target = m_refresh.group(1).strip().strip("'\"")
        if target.startswith("http") or target.startswith("/"):
            return urllib.parse.urljoin(page_url, target)

    # B) Specific package file links (.pkg, .bin, .iso, .tar, .zip, .rar, .7z)
    pkg_links = re.findall(r'<a[^>]*href=["\'](https?://[^"\']+\.(?:pkg|bin|iso|tar|zip|rar|7z)(?:\?[^"\']*)?)["\']', html_text, re.I)
    if not pkg_links:
        pkg_links = re.findall(r'<a[^>]*href=["\'](/[^"\']+\.(?:pkg|bin|iso|tar|zip|rar|7z)(?:\?[^"\']*)?)["\']', html_text, re.I)
        pkg_links = [urllib.parse.urljoin(page_url, l) for l in pkg_links]

    if len(pkg_links) == 1:
        return pkg_links[0]
    elif len(pkg_links) > 1:
        pkg_only = [l for l in pkg_links if ".pkg" in l.lower()]
        if len(pkg_only) == 1:
            return pkg_only[0]

    # C) Anchor with explicit download attribute
    m_download = re.search(r'<a[^>]*download(?:\s*=\s*["\'][^"\']*["\'])?[^>]*href=["\'](https?://[^"\']+)["\']', html_text, re.I)
    if m_download:
        return urllib.parse.urljoin(page_url, m_download.group(1).replace("&amp;", "&"))

    return None


def parse_filename_from_headers(headers, fallback_url: str = "") -> str:
    """Extract a clean filename from Content-Disposition header, with fallback to URL path."""
    cd = headers.get("Content-Disposition", "") if hasattr(headers, "get") else ""
    if cd:
        # Check RFC 5987 encoded filename: filename*=UTF-8''...
        m_rfc = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;\s]+)", cd, re.I)
        if m_rfc:
            name = urllib.parse.unquote(m_rfc.group(1).strip('"\''))
            if name:
                return Path(name).name

        # Standard filename parameter: filename="..."
        m_std = re.search(r'filename\s*=\s*["\']?([^;"\']+)["\']?', cd, re.I)
        if m_std:
            name = urllib.parse.unquote(m_std.group(1).strip())
            if name:
                return Path(name).name

    if fallback_url:
        path = urllib.parse.urlsplit(fallback_url).path
        name = Path(path).name
        if name and name not in ("file", "download", "uc", "view", "index.html", "index.php"):
            return urllib.parse.unquote(name)

    return ""


def get_captcha_hint_if_applicable(url: str) -> str | None:
    """Return an actionable hint if the URL belongs to a host requiring browser CAPTCHA."""
    parsed = urllib.parse.urlsplit(url)
    domain = parsed.netloc.lower()

    captcha_hosts = {
        "1fichier.com": "1fichier",
        "rapidgator.net": "Rapidgator",
        "ddownload.com": "DDownload",
        "mega.nz": "MEGA",
        "katfile.com": "Katfile",
        "nitroflare.com": "Nitroflare",
        "uploadhaven.com": "UploadHaven",
    }

    for host_domain, name in captcha_hosts.items():
        if host_domain in domain:
            return (
                f"{name} requires solving an interactive CAPTCHA in your browser. "
                "Click Download on the site in Chrome, right-click the download item in Chrome's Downloads tab, "
                "and select 'Copy download link'."
            )
    return None


def parse_multipart_info(filename: str) -> tuple[str, int] | None:
    """If filename matches a multi-part split pattern, returns (base_name, part_number)."""
    if not filename or not isinstance(filename, str):
        return None
    name = Path(filename).name

    # 1. Numbered split extension: e.g. Game.pkg.001, Game.pkg.002, Game.pkg.1
    m = re.match(r"^(.*?\.pkg)\.(\d{1,4})$", name, re.I)
    if m:
        return m.group(1), int(m.group(2))

    # 2. Raw numbered extension: e.g. Game.001, Game.002 -> Game.pkg
    m = re.match(r"^(.*?)\.(\d{2,4})$", name, re.I)
    if m:
        base = m.group(1)
        if not base.lower().endswith(".pkg"):
            base += ".pkg"
        return base, int(m.group(2))

    # 3. Part in name before .pkg: e.g. Game.part01.pkg, Game_part1.pkg, Game-part02.pkg
    m = re.match(r"^(.*?)[._-]part(\d{1,4})\.pkg$", name, re.I)
    if m:
        return m.group(1) + ".pkg", int(m.group(2))

    # 4. Part after .pkg: e.g. Game.pkg.part1, Game.pkg_part02
    m = re.match(r"^(.*?\.pkg)[._-]part(\d{1,4})$", name, re.I)
    if m:
        return m.group(1), int(m.group(2))

    # 5. Numerical suffix before .pkg: e.g. Game_1.pkg, Game_2.pkg
    m = re.match(r"^(.*?)[_.](\d{1,3})\.pkg$", name, re.I)
    if m:
        return m.group(1) + ".pkg", int(m.group(2))

    return None


def detect_multipart_sequence(items: list[dict]) -> tuple[bool, str, list[dict]]:
    """If items form a valid multi-part sequence, returns (True, merged_name, sorted_items)."""
    if not items or len(items) <= 1:
        return False, "", items

    parsed = []
    base_names = set()
    for item in items:
        raw_name = item.get("name") or ""
        if not raw_name:
            src = item.get("source", "")
            raw_name = urllib.parse.unquote(Path(urllib.parse.urlsplit(src).path).name)
        info = parse_multipart_info(raw_name)
        if not info:
            return False, "", items
        base, num = info
        base_names.add(base.lower())
        parsed.append((num, item, base))

    if len(base_names) != 1:
        return False, "", items

    parsed.sort(key=lambda x: x[0])
    merged_name = parsed[0][2]
    sorted_items = [p[1] for p in parsed]
    return True, merged_name, sorted_items

