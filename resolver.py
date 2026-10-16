"""Direct Stream for PlayStation 5 — Link Resolver Engine.

Provides automatic resolution of direct download links from common file hosting
services (Archive.org, BuzzHeavier, 1fichier, MediaFire, PixelDrain, Google Drive,
KrakenFiles, Qiwi, GoFile, Send.cm, etc.) and generic web pages, allowing users
to paste sharing/landing page links.
"""

from __future__ import annotations

import os
import re
import urllib.parse
import urllib.request
from pathlib import Path


SUPPORTED_EXTENSIONS = (
    "pkg", "ffpfsc", "exfat", "ufs", "bin", "iso", "img",
    "zip", "rar", "7z", "tar", "001"
)
MEDIA_EXT_PATTERN = r'(?:pkg|ffpfsc|exfat|ufs|bin|iso|img|tar|zip|rar|7z|001)'
PRIMARY_GAME_EXTS = (".ffpfsc", ".exfat", ".ufs", ".pkg", ".iso", ".bin")
# Executable/installer types that are never a console payload. File hosts routinely
# show decoy "Download" buttons that hand out adware installers instead of the file,
# so these are refused no matter which heuristic produced them.
# NOTE: .pkg is deliberately NOT here -- it is a PlayStation package format.
INSTALLER_EXTS = (
    ".exe", ".msi", ".msix", ".appx", ".dmg", ".apk", ".scr", ".bat", ".cmd",
    ".com", ".vbs", ".ps1", ".jar", ".deb", ".rpm", ".run", ".bin.exe",
)


VIKINGFILE_DOMAIN = re.compile(r"(?:^|\.)(vik(?:ing|1ng)file\.[a-z]{2,})$", re.I)


def vikingfile_root(domain: str) -> str:
    """Return the root VikingFile domain for a host, else "".

    CDN subdomains (s2.vikingfile.com) must send the root site as Referer, so the
    matched registrable domain is returned rather than the full host.
    """
    if not domain:
        return ""
    match = VIKINGFILE_DOMAIN.search(domain.split(":")[0].strip().lower())
    return match.group(1) if match else ""


def is_vikingfile_domain(domain: str) -> bool:
    """True for vikingfile.com and its mirror domains (e.g. vik1ngfile.site)."""
    return bool(vikingfile_root(domain))


def is_installer_url(url: str) -> bool:
    """True if the URL points at an executable/installer rather than a payload."""
    if not url or not isinstance(url, str):
        return False
    try:
        path = urllib.parse.urlsplit(url).path.lower()
    except Exception:
        return False
    return any(path.endswith(ext) for ext in INSTALLER_EXTS)


AKIRABOX_RESERVED = (
    "premium", "developers", "blog", "contact-us", "terms", "privacy",
    "dmca", "login", "register", "offer", "ui", "user", "api", "f", "file",
    "d", "download", "files", "about", "faq", "support", "pricing",
)
JSON_LINK_KEYS = ("url", "link", "download_url", "downloadUrl", "direct_link",
                  "directLink", "download", "file_url", "fileUrl", "href")
JSON_LINK_CONTAINERS = ("data", "result", "file", "response", "payload")


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

    # 5. AkiraBox: https://akirabox.to/<id>/file, /f/<id>, /file/<id>, /d/<id> or /<id>
    #    -> https://akirabox.to/api/files/<id>/download (query/fragment tolerated)
    if not re.search(r"akirabox\.[a-z]+/api/files/", url, re.I):
        m_akira = re.match(
            r"^https?://(?:www\.)?(?:akirabox\.to|akirabox\.com)/(?:(?:f|file|files|d|download)/)?([a-zA-Z0-9_-]+)(?:/(?:file|download))?/?(?:[?#].*)?$",
            url, re.I)
        if m_akira:
            file_id = m_akira.group(1)
            if file_id.lower() not in AKIRABOX_RESERVED:
                return f"https://akirabox.to/api/files/{file_id}/download"

    # 6. FileDitch: https://fileditchfiles.st/... -> https://new.fileditch.com/...
    m_fileditch = re.match(r"^https?://(?:www\.)?fileditchfiles\.st/(?:d/|file/)?([^?#]+)", url, re.I)
    if m_fileditch:
        subpath = m_fileditch.group(1)
        return f"https://new.fileditch.com/{subpath}"

    # 7. Rootz: canonicalize www.rootz.so -> rootz.so
    if "www.rootz.so" in url.lower():
        url = re.sub(r"^https?://www\.rootz\.so", "https://rootz.so", url, flags=re.I)

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

    # 10. Rootz (rootz.so): Auto-resolve via Next.js RSC token & download-by-short API
    if "rootz.so" in domain:
        m_dl = re.search(r'href=["\'](/api/files/proxy-download/[^"\']+)["\']', html_text)
        if m_dl:
            return urllib.parse.urljoin("https://rootz.so", m_dl.group(1))
        m_cdn = re.search(r'href=["\'](https?://[a-zA-Z0-9.-]*alcyone\.so/[^"\']+)["\']', html_text)
        if m_cdn:
            return m_cdn.group(1).replace("&amp;", "&")
        m_token = re.search(r'shortId["\\\':\s]+([a-zA-Z0-9_-]+).*?pageToken["\\\':\s]+([a-zA-Z0-9_.-]+)', html_text)
        if m_token:
            short_id, page_token = m_token.group(1), m_token.group(2)
            try:
                import json
                api_url = f"https://rootz.so/api/files/download-by-short?shortId={urllib.parse.quote(short_id)}"
                api_req = urllib.request.Request(api_url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    "X-Page-Token": page_token,
                    "Referer": page_url
                })
                with urllib.request.urlopen(api_req, timeout=10) as r:
                    res_data = json.loads(r.read().decode())
                    file_id = res_data.get("data", {}).get("fileId") or short_id
                    return f"https://rootz.so/api/files/proxy-download/{file_id}"
            except Exception:
                pass

    # 11. AkiraBox (akirabox.to, akirabox.com): Extract hotlink API download or CDN target
    if "akirabox.to" in domain or "akirabox.com" in domain:
        m_api = re.search(r'href=["\'](/api/files/[a-zA-Z0-9_-]+/download(?:\?[^"\']*)?)["\']', html_text)
        if m_api:
            return urllib.parse.urljoin(page_url, m_api.group(1))
        m_cdn = re.search(r'href=["\'](https?://(?:storage|cdn|files)[a-zA-Z0-9.-]*akirabox\.[a-z]+/[^"\']+)["\']', html_text)
        if m_cdn:
            return m_cdn.group(1).replace("&amp;", "&")
        # Embedded JSON payload (e.g. "download_url": "https:\/\/...") inside an inline script.
        # Unescape JSON solidus escapes first so the URL pattern still matches.
        m_json = re.search(r'["\'](?:download_url|downloadUrl|direct_link|directLink|link)["\']\s*:\s*["\'](https?://[^"\']+)["\']', html_text.replace(r"\/", "/"))
        if m_json:
            return m_json.group(1).replace("&amp;", "&")
        m_id = re.search(r'/(?:(?:f|file|files|d|download)/)?([a-zA-Z0-9_-]+)(?:/(?:file|download))?/?$', parsed.path)
        if m_id and m_id.group(1).lower() not in AKIRABOX_RESERVED:
            return f"https://akirabox.to/api/files/{m_id.group(1)}/download"

    # 12. DataNodes (datanodes.to): Extract direct storage/CDN link or file download
    if "datanodes.to" in domain:
        m_cdn = re.search(r'href=["\'](https?://[a-zA-Z0-9.-]*datanodes\.to/d/[^"\']+)["\']', html_text)
        if m_cdn:
            return m_cdn.group(1).replace("&amp;", "&")
        m_dl = re.search(rf'href=["\'](https?://[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        if m_dl:
            return m_dl.group(1).replace("&amp;", "&")

    # 13. VikingFile (vikingfile.com + mirrors): Extract link from script response or download button
    if is_vikingfile_domain(domain):
        m_link = re.search(r'["\']link["\']:\s*["\'](https?://[^"\']+)["\']', html_text)
        if m_link:
            return m_link.group(1).replace(r"\/", "/").replace("&amp;", "&")
        m_btn = re.search(r'id=["\']download-link["\'][^>]*href=["\'](https?://[^"\']+)["\']', html_text)
        if m_btn and not m_btn.group(1).endswith("#"):
            return m_btn.group(1).replace("&amp;", "&")

    # 14. FileDitch (fileditchfiles.st, fileditch.com, new.fileditch.com, files.fileditch.com)
    if "fileditch" in domain:
        m_fd = re.search(rf'href=["\'](https?://(?:files|new)\.fileditch\.com/[^"\']+\.{MEDIA_EXT_PATTERN}(?:\?[^"\']*)?)["\']', html_text, re.I)
        if m_fd:
            return m_fd.group(1).replace("&amp;", "&")
        m_fd_any = re.search(r'href=["\'](https?://(?:files|new)\.fileditch\.com/[^"\']+)["\']', html_text, re.I)
        if m_fd_any:
            return m_fd_any.group(1).replace("&amp;", "&")

    # 15. Generic Web Page Heuristics:
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

    game_links = [l for l in game_links if not is_installer_url(l)]

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
    for m_download in re.finditer(r'<a[^>]*download(?:\s*=\s*["\'][^"\']*["\'])?[^>]*href=["\'](https?://[^"\']+)["\']', html_text, re.I):
        candidate = urllib.parse.urljoin(page_url, m_download.group(1).replace("&amp;", "&"))
        if not is_installer_url(candidate):
            return candidate

    return None


def extract_download_link_from_json(page_url: str, body_text: str) -> str | None:
    """Inspect a JSON API response (e.g. AkiraBox /api/files/<id>/download) for a direct link."""
    if not body_text or not isinstance(body_text, str):
        return None

    def _pick(obj, depth: int = 0):
        if depth > 4:
            return None
        if isinstance(obj, str):
            return obj if obj.startswith(("http://", "https://", "/")) else None
        if isinstance(obj, list):
            for entry in obj:
                found = _pick(entry, depth + 1)
                if found:
                    return found
            return None
        if not isinstance(obj, dict):
            return None
        for key in JSON_LINK_KEYS:
            val = obj.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://", "/")):
                return val
        for key in JSON_LINK_CONTAINERS:
            if key in obj:
                found = _pick(obj[key], depth + 1)
                if found:
                    return found
        return None

    candidate = None
    try:
        import json
        candidate = _pick(json.loads(body_text))
    except Exception:
        candidate = None

    if not candidate:
        # Truncated or non-standard body: fall back to a direct key scan.
        # Unescape JSON solidus escapes first so "https:\/\/host" still matches.
        scan_text = body_text.replace(r"\/", "/")
        keys = "|".join(JSON_LINK_KEYS)
        m = re.search(rf'["\'](?:{keys})["\']\s*:\s*["\']((?:https?://|/)[^"\']+)["\']', scan_text)
        if m:
            candidate = m.group(1)

    if not candidate:
        return None
    candidate = candidate.replace(r"\/", "/").replace("&amp;", "&").strip()
    if not candidate:
        return None
    return urllib.parse.urljoin(page_url, candidate)


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
        parsed = urllib.parse.urlsplit(fallback_url)
        # 1. Check query parameters first (e.g. ?filename=... or ?name=...)
        query = urllib.parse.parse_qs(parsed.query)
        for qk in ("filename", "file_name", "name", "file"):
            if qk in query and query[qk][0]:
                qname = urllib.parse.unquote(query[qk][0]).strip()
                if qname:
                    return Path(qname).name
        # 2. Path fallback
        path = parsed.path
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
        "datanodes.to": (
            "DataNodes generates a direct link after a free countdown in your browser. "
            "To stream directly to PS5: click 'Free Download' -> 'Start Download' in your browser, "
            "right-click the download item in your browser's Downloads tab, choose 'Copy download link', "
            "and paste that direct link into Direct Stream."
        ),
        "vikingfile.com": (
            "VikingFile generates its direct link after solving Cloudflare Turnstile in your browser. "
            "To stream immediately to PS5: click Download on VikingFile in your browser, copy the direct download link "
            "from your browser's Downloads tab, and paste it into Direct Stream."
        ),
        "akirabox.to": (
            "AkiraBox landing page is protected by Cloudflare. "
            "Click Download once in your browser, copy the direct download link from your browser's Downloads tab, "
            "and paste it into Direct Stream."
        ),
        "akirabox.com": (
            "AkiraBox landing page is protected by Cloudflare. "
            "Click Download once in your browser, copy the direct download link from your browser's Downloads tab, "
            "and paste it into Direct Stream."
        ),
        "rootz.so": (
            "Rootz link may be password protected or rate-limited. "
            "Click Download in your browser, copy the download link from your browser's Downloads tab, "
            "and paste it into Direct Stream."
        ),
    }

    if is_vikingfile_domain(domain):
        return hints["vikingfile.com"]

    for host_domain, hint in hints.items():
        if host_domain in domain:
            return hint
    return None


def get_request_headers_for_url(url: str, base_headers: dict | None = None) -> dict:
    """Build optimized request headers for streaming from known hosts, including proper Referer and User-Agent."""
    headers = dict(base_headers) if base_headers else {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if not url or not isinstance(url, str):
        return headers
    try:
        parsed = urllib.parse.urlsplit(url)
        domain = parsed.netloc.lower()

        if "datanodes.to" in domain:
            headers["Referer"] = "https://datanodes.to/"
        elif "rootz.so" in domain or "alcyone.so" in domain:
            headers["Referer"] = "https://rootz.so/"
        elif vikingfile_root(domain):
            headers["Referer"] = f"https://{vikingfile_root(domain)}/"
        elif "akirabox.to" in domain or "akirabox.com" in domain:
            headers["Referer"] = "https://akirabox.to/"
        elif "fileditch" in domain:
            headers["Referer"] = "https://new.fileditch.com/"
        elif "mediafire.com" in domain:
            headers["Referer"] = "https://www.mediafire.com/"
        elif "1fichier.com" in domain:
            headers["Referer"] = "https://1fichier.com/"
        elif "torbox" in domain or "tb-cdn" in domain:
            headers["Referer"] = "https://torbox.app/"
        elif "real-debrid.com" in domain or "rdb.gg" in domain:
            headers["Referer"] = "https://real-debrid.com/"
        elif "alldebrid" in domain:
            headers["Referer"] = "https://alldebrid.com/"
        elif "Referer" not in headers and parsed.scheme and parsed.netloc:
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        pass

    return headers


def is_single_connection_host(url: str) -> bool:
    """Return True if host is known to limit free users to 1 concurrent connection, has single-use tokens, or rate-limits multi-stream connections (including TorBox and debrid services)."""
    if not url or not isinstance(url, str):
        return False
    try:
        domain = urllib.parse.urlsplit(url).netloc.lower()
        if any(k in domain for k in ("torbox", "tb-cdn", "real-debrid", "alldebrid", "debrid-link", "premiumize", "pikpak")):
            return True
        if is_vikingfile_domain(domain):
            return True
        single_hosts = (
            "datanodes.to",
            "vikingfile.com",
            "akirabox.to",
            "akirabox.com",
            "fileditchfiles.st",
            "fileditch.com",
            "new.fileditch.com",
            "1fichier.com",
            "rapidgator.net",
            "ddownload.com",
            "katfile.com",
            "nitroflare.com",
            "torbox.app",
            "torbox.online",
            "real-debrid.com",
            "rdb.gg",
            "alldebrid.com",
            "alldebrid.fr",
            "debrid-link.fr",
            "premiumize.me",
            "pikpak.me",
            "mypikpak.com",
        )
        return any(h in domain for h in single_hosts)
    except Exception:
        return False


def parse_multipart_info(filename: str) -> tuple[str, int] | None:
    """If filename matches a multi-part split pattern, returns (base_name, part_number)."""
    if not filename or not isinstance(filename, str):
        return None
    name = Path(filename).name

    # 1. Numbered split extension: e.g. Game.pkg.001, Game.ffpfsc.001, Game.iso.001, Game.rar.001
    m = re.match(r"^(.*?\.(?:pkg|ffpfsc|exfat|ufs|iso|bin|img|zip|rar|7z|tar|[a-z0-9]{2,6}))\b\.(\d{1,4})$", name, re.I)
    if m:
        return m.group(1), int(m.group(2))

    # 2. Part pattern before extension: e.g. Game.part01.rar, Game.part1.pkg, Game_part02.ffpfsc
    m = re.match(r"^(.*?)[._-]part(\d{1,4})\.([a-z0-9]{2,6})$", name, re.I)
    if m:
        return f"{m.group(1)}.{m.group(3)}", int(m.group(2))

    # 3. Part pattern after extension: e.g. Game.pkg.part1, Game.rar.part02
    m = re.match(r"^(.*?\.[a-z0-9]{2,6})[._-]part(\d{1,4})$", name, re.I)
    if m:
        return m.group(1), int(m.group(2))

    # 4. Raw numbered extension: e.g. Game.001, Game.002
    m = re.match(r"^(.*?)\.(\d{2,4})$", name, re.I)
    if m:
        return m.group(1), int(m.group(2))

    # 5. Numerical suffix before extension: e.g. Game_1.pkg, Game_2.ffpfsc, Game.1.rar
    m = re.match(r"^(.*?)[_.](\d{1,3})\.([a-z0-9]{2,6})$", name, re.I)
    if m:
        return f"{m.group(1)}.{m.group(3)}", int(m.group(2))

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
            src = str(item.get("source", "")).strip()
            if src.startswith(("http://", "https://")):
                raw_name = urllib.parse.unquote(Path(urllib.parse.urlsplit(src).path).name)
            else:
                raw_name = os.path.basename(src.replace("\\", "/"))
        info = parse_multipart_info(raw_name)
        if not info:
            return False, "", items
        base, num = info
        base_names.add(base.lower())
        upd_item = dict(item)
        if not upd_item.get("name"):
            upd_item["name"] = raw_name
        parsed.append((num, upd_item, base))

    if len(base_names) != 1:
        return False, "", items

    parsed.sort(key=lambda x: x[0])
    numbers = [entry[0] for entry in parsed]
    min_num = numbers[0] if numbers else 1
    if min_num not in (0, 1) or numbers != list(range(min_num, min_num + len(numbers))):
        return False, "", items
    merged_name = parsed[0][2]
    sorted_items = [p[1] for p in parsed]
    return True, merged_name, sorted_items

