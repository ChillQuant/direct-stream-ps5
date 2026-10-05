"""Direct Stream for PlayStation 5 — Link Resolver Engine.

Provides automatic resolution of direct download links from common file hosting
services (Archive.org, BuzzHeavier, 1fichier, MediaFire, PixelDrain, Google Drive,
KrakenFiles, Qiwi, GoFile, Send.cm, etc.) and generic web pages, allowing users
to paste sharing/landing page links.
"""

import os
import re
import urllib.parse
from pathlib import Path


SUPPORTED_EXTENSIONS = (
    "pkg", "ffpfsc", "exfat", "ufs", "bin", "iso", "img",
    "zip", "rar", "7z", "tar", "001"
)
MEDIA_EXT_PATTERN = r'(?:pkg|ffpfsc|exfat|ufs|bin|iso|img|tar|zip|rar|7z|001)'
PRIMARY_GAME_EXTS = (".ffpfsc", ".exfat", ".ufs", ".pkg", ".iso", ".bin")


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

    # 4. BuzzHeavier / Bzzhr: https://buzzheavier.com/<id> or /f/<id> -> <url>/download
    m_buzz = re.match(r"^https?://(?:www\.)?(buzzheavier\.com|bzzhr\.to)/(?:f/)?([a-zA-Z0-9_-]+)/?$", url, re.I)
    if m_buzz:
        host, file_id = m_buzz.group(1), m_buzz.group(2)
        return f"https://{host}/{file_id}/download"

    # 5. Qiwi.gg / Qiwi.to: https://qiwi.gg/file/<id> -> https://qiwi.gg/file/<id>
    # (Resolved in HTML or left for direct API probe)

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

    # 2. Internet Archive (archive.org): Extract package/game file links from details/download directory
    if "archive.org" in domain:
        # Search for game/package links under /download/
        game_links = re.findall(rf'href=["\']((?:https?://(?:www\.)?archive\.org)?/download/[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        if not game_links:
            # Check relative links inside /download/<identifier>/ directory
            game_links = re.findall(rf'<a[^>]*href=["\']([^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        if game_links:
            # Prioritize primary game disk/package files (.ffpfsc, .exfat, .ufs, .pkg, .iso) over archives/metadata
            primary_only = [l for l in game_links if any(l.lower().split("?")[0].endswith(ext) for ext in PRIMARY_GAME_EXTS)]
            chosen = primary_only[0] if primary_only else game_links[0]
            return urllib.parse.urljoin(page_url, chosen.replace("&amp;", "&"))

    # 3. BuzzHeavier / Bzzhr: Extract direct CDN download link or hx-redirect target
    if "buzzheavier.com" in domain or "bzzhr.to" in domain:
        # Check direct CDN storage endpoints (e.g. w.buzzheavier.com, s*.buzzheavier.com)
        m_cdn = re.search(r'href=["\'](https?://[a-zA-Z0-9.-]*buzzheavier\.com/[^"\']+)["\']', html_text)
        if m_cdn:
            return m_cdn.group(1).replace("&amp;", "&")
        # Check download button or hx-get/hx-post attribute
        m_hx = re.search(r'(?:hx-get|hx-post|href)=["\']([^"\']*/download[^"\']*)["\']', html_text)
        if m_hx:
            return urllib.parse.urljoin(page_url, m_hx.group(1).replace("&amp;", "&"))

    # 4. 1fichier: Extract direct CDN link or download button
    if "1fichier.com" in domain:
        # Check direct CDN link on download landing page
        m_cdn = re.search(r'href=["\'](https?://[a-zA-Z0-9.-]+\.1fichier\.com/[^"\']+)["\']', html_text)
        if m_cdn:
            return m_cdn.group(1).replace("&amp;", "&")
        # Check standard orange download button (<a class="...btn-orange..." href="...">)
        m_btn = re.search(r'<a[^>]*class=["\'][^"\']*btn-orange[^"\']*["\'][^>]*href=["\'](https?://[^"\']+)["\']', html_text)
        if m_btn:
            return m_btn.group(1).replace("&amp;", "&")

    # 5. KrakenFiles: Extract download button
    if "krakenfiles.com" in domain:
        m_btn = re.search(r'<a[^>]*id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html_text, re.I)
        if not m_btn:
            m_btn = re.search(r'href=["\'](https?://[a-zA-Z0-9.-]*krakenfiles\.com/download/[^"\']+)["\']', html_text, re.I)
        if m_btn:
            return urllib.parse.urljoin(page_url, m_btn.group(1).replace("&amp;", "&"))

    # 6. Qiwi (qiwi.gg, qiwi.to, qiwi.lol)
    if "qiwi." in domain:
        m_qiwi = re.search(rf'href=["\'](https?://[a-zA-Z0-9.-]*qiwi\.[a-z]+/[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        if m_qiwi:
            return m_qiwi.group(1).replace("&amp;", "&")

    # 7. Send.cm
    if "send.cm" in domain:
        m_send = re.search(rf'href=["\'](https?://[a-zA-Z0-9.-]*send\.cm/[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        if m_send:
            return m_send.group(1).replace("&amp;", "&")

    # 8. Google Drive: Extract "Download anyway" virus confirmation link/form
    if "google.com" in domain or "googleusercontent.com" in domain:
        m_link = re.search(r'id=["\']uc-download-link["\'][^>]*href=["\']([^"\']+)["\']', html_text, re.I)
        if m_link:
            return urllib.parse.urljoin(page_url, m_link.group(1).replace("&amp;", "&"))
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

    # 9. GoFile: JSON response or direct link in page
    if "gofile.io" in domain:
        m = re.search(r'"link":\s*"(https?://store[^"]+)"', html_text)
        if m:
            return m.group(1).replace(r"\/", "/")

    # 10. Generic Web Page Heuristics:
    # A) Meta refresh redirect (<meta http-equiv="refresh" content="...;url=(...)">)
    m_refresh = re.search(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*url=([^"\'>\s]+)', html_text, re.I)
    if m_refresh:
        target = m_refresh.group(1).strip().strip("'\"")
        if target.startswith("http") or target.startswith("/"):
            return urllib.parse.urljoin(page_url, target)

    # B) Specific game/disk/package links (.pkg, .ffpfsc, .exfat, .ufs, .iso, .zip, etc.)
    game_links = re.findall(rf'<a[^>]*href=["\'](https?://[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
    if not game_links:
        game_links = re.findall(rf'<a[^>]*href=["\'](/[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        game_links = [urllib.parse.urljoin(page_url, l) for l in game_links]

    if len(game_links) == 1:
        return game_links[0]
    elif len(game_links) > 1:
        primary_only = [l for l in game_links if any(l.lower().split("?")[0].endswith(ext) for ext in PRIMARY_GAME_EXTS)]
        if len(primary_only) == 1:
            return primary_only[0]
        elif primary_only:
            return primary_only[0]
        return game_links[0]

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
    """Return an actionable hint if the URL belongs to a host requiring browser CAPTCHA or wait timer."""
    parsed = urllib.parse.urlsplit(url)
    domain = parsed.netloc.lower()

    hints = {
        "1fichier.com": (
            "1fichier requires solving a free wait timer or using a direct link. "
            "To stream immediately: click Download once in your browser, then copy the download link "
            "from your browser's Downloads tab and paste it into Direct Stream."
        ),
        "buzzheavier.com": (
            "BuzzHeavier landing page is protected by Cloudflare. "
            "Click Download once in your browser, right-click the download item in your browser's Downloads tab, "
            "and select 'Copy download link'."
        ),
        "bzzhr.to": (
            "BuzzHeavier landing page is protected by Cloudflare. "
            "Click Download once in your browser, right-click the download item in your browser's Downloads tab, "
            "and select 'Copy download link'."
        ),
        "rapidgator.net": (
            "Rapidgator requires solving an interactive CAPTCHA in your browser. "
            "Click Download on the site in your browser, copy the direct download link from your browser's Downloads tab, "
            "and paste it into Direct Stream."
        ),
        "ddownload.com": "DDownload requires an interactive browser CAPTCHA or premium direct link.",
        "mega.nz": "MEGA uses client-side decryption which requires a browser or the MEGA API.",
        "katfile.com": "Katfile requires solving a CAPTCHA in your browser before starting.",
        "nitroflare.com": "Nitroflare requires solving a CAPTCHA in your browser.",
        "uploadhaven.com": "UploadHaven requires waiting 15 seconds in your browser before generating a link.",
    }

    for host_domain, hint in hints.items():
        if host_domain in domain:
            return hint
    return None
