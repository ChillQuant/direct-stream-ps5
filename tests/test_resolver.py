import unittest
from resolver import (
    pre_resolve_url,
    extract_download_link_from_html,
    parse_filename_from_headers,
    get_captcha_hint_if_applicable,
)


class TestResolver(unittest.TestCase):
    def test_pixeldrain_pre_resolve(self):
        url = "https://pixeldrain.com/u/abc123XYZ"
        self.assertEqual(pre_resolve_url(url), "https://pixeldrain.com/api/file/abc123XYZ")

        url_www = "https://www.pixeldrain.com/u/abc123XYZ"
        self.assertEqual(pre_resolve_url(url_www), "https://pixeldrain.com/api/file/abc123XYZ")

    def test_gdrive_pre_resolve(self):
        url = "https://drive.google.com/file/d/1A2B3C4D5E/view?usp=sharing"
        self.assertEqual(pre_resolve_url(url), "https://drive.google.com/uc?export=download&id=1A2B3C4D5E")

        url_open = "https://drive.google.com/open?id=1A2B3C4D5E"
        self.assertEqual(pre_resolve_url(url_open), "https://drive.google.com/uc?export=download&id=1A2B3C4D5E")

    def test_archive_org_pre_resolve(self):
        url = "https://archive.org/details/ps5_sample_repo/update_v105.pkg"
        self.assertEqual(pre_resolve_url(url), "https://archive.org/download/ps5_sample_repo/update_v105.pkg")

    def test_mediafire_html_extract(self):
        html = '''
        <!DOCTYPE html>
        <html>
        <body>
            <div class="download_link">
                <a class="input popsok" aria-label="Download file" id="downloadButton" href="https://download1584.mediafire.com/xyz123/game_update.pkg">Download (4.2 GB)</a>
            </div>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://www.mediafire.com/file/xyz123/game_update.pkg/file", html)
        self.assertEqual(res, "https://download1584.mediafire.com/xyz123/game_update.pkg")

    def test_gdrive_html_confirm_extract(self):
        html = '''
        <html>
        <body>
            <a id="uc-download-link" class="goog-inline-block jfk-button" href="https://drive.usercontent.google.com/download?id=1A2B&amp;export=download&amp;confirm=t&amp;uuid=xyz">Download anyway</a>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://drive.google.com/uc?export=download&id=1A2B", html)
        self.assertEqual(res, "https://drive.usercontent.google.com/download?id=1A2B&export=download&confirm=t&uuid=xyz")

    def test_generic_pkg_link_extract(self):
        html = '''
        <html>
        <body>
            <h1>Release v1.2</h1>
            <p>Download the PlayStation 5 package below:</p>
            <a href="https://releases.example.com/builds/v1.2/Package_Patch_102.pkg">Download Package</a>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://releases.example.com/release.html", html)
        self.assertEqual(res, "https://releases.example.com/builds/v1.2/Package_Patch_102.pkg")

    def test_generic_no_link_returns_none(self):
        html = "<html><body><h1>Welcome to our home page</h1><p>No downloads here</p></body></html>"
        res = extract_download_link_from_html("https://example.com/about", html)
        self.assertIsNone(res)

    def test_content_disposition_filename(self):
        headers = {"Content-Disposition": 'attachment; filename="Bloodborne_Patch_60fps.pkg"'}
        self.assertEqual(parse_filename_from_headers(headers), "Bloodborne_Patch_60fps.pkg")

        headers_utf8 = {"Content-Disposition": "attachment; filename*=UTF-8''Elden%20Ring%20v1.12.pkg"}
        self.assertEqual(parse_filename_from_headers(headers_utf8), "Elden Ring v1.12.pkg")

    def test_captcha_hint(self):
        hint = get_captcha_hint_if_applicable("https://1fichier.com/?abc123xyz")
        self.assertIsNotNone(hint)
        self.assertIn("1fichier", hint)
        self.assertIn("CAPTCHA", hint)

        no_hint = get_captcha_hint_if_applicable("https://mediafire.com/file/abc")
        self.assertIsNone(no_hint)


if __name__ == "__main__":
    unittest.main()
