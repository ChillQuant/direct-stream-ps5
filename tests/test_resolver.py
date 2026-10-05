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

    def test_archive_org_html_details_extract(self):
        html = '''
        <html>
        <body>
            <h1>PlayStation Archive</h1>
            <a href="/download/ps5_collection/readme.txt">Readme</a>
            <a href="/download/ps5_collection/CUSA00123_00.pkg">Download PKG</a>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://archive.org/details/ps5_collection", html)
        self.assertEqual(res, "https://archive.org/download/ps5_collection/CUSA00123_00.pkg")

    def test_buzzheavier_pre_resolve_and_extract(self):
        # Test pre-resolve patterns
        url1 = "https://buzzheavier.com/abc12345"
        self.assertEqual(pre_resolve_url(url1), "https://buzzheavier.com/abc12345/download")

        url2 = "https://bzzhr.to/f/xyz7890"
        self.assertEqual(pre_resolve_url(url2), "https://bzzhr.to/xyz7890/download")

        # Test HTML extraction
        html = '''
        <html>
        <body>
            <a class="download-button" href="https://w.buzzheavier.com/s/direct/CUSA99999.pkg">Download file</a>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://buzzheavier.com/abc12345", html)
        self.assertEqual(res, "https://w.buzzheavier.com/s/direct/CUSA99999.pkg")

    def test_1fichier_extract(self):
        html = '''
        <html>
        <body>
            <div id="dl_link">
                <a class="ok btn-general btn-orange" href="https://a-01.1fichier.com/c123456789">Click here to download the file</a>
            </div>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://1fichier.com/?abc123", html)
        self.assertEqual(res, "https://a-01.1fichier.com/c123456789")

    def test_krakenfiles_extract(self):
        html = '''
        <html>
        <body>
            <a id="downloadButton" href="https://krakenfiles.com/download/file12345/game.pkg">Download</a>
        </body>
        </html>
        '''
        res = extract_download_link_from_html("https://krakenfiles.com/view/file12345/file.html", html)
        self.assertEqual(res, "https://krakenfiles.com/download/file12345/game.pkg")

    def test_qiwi_and_sendcm_extract(self):
        html_qiwi = '<html><body><a href="https://eu.qiwi.gg/files/CUSA11111.pkg">Download</a></body></html>'
        res_qiwi = extract_download_link_from_html("https://qiwi.gg/file/abc", html_qiwi)
        self.assertEqual(res_qiwi, "https://eu.qiwi.gg/files/CUSA11111.pkg")

        html_send = '<html><body><a href="https://s1.send.cm/files/CUSA22222.pkg">Download</a></body></html>'
        res_send = extract_download_link_from_html("https://send.cm/file/xyz", html_send)
        self.assertEqual(res_send, "https://s1.send.cm/files/CUSA22222.pkg")

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

    def test_native_ps5_formats_extract(self):
        # Native PS5 compressed disk image (.ffpfsc)
        html_ffpfsc = '''
        <html><body>
            <a href="https://releases.example.com/readme.txt">Readme</a>
            <a href="https://releases.example.com/assets.zip">Assets ZIP</a>
            <a href="https://releases.example.com/Demons_Souls_PPSA01411.ffpfsc">Download PS5 PFS</a>
        </body></html>
        '''
        res_ffpfsc = extract_download_link_from_html("https://releases.example.com/demons-souls", html_ffpfsc)
        self.assertEqual(res_ffpfsc, "https://releases.example.com/Demons_Souls_PPSA01411.ffpfsc")

        # Native PS5 raw exFAT disk image (.exfat)
        html_exfat = '''
        <html><body>
            <a href="https://archive.org/download/spiderman2_ps5/Spider_Man_2_PPSA08338.exfat">Direct Virtual Disk</a>
        </body></html>
        '''
        res_exfat = extract_download_link_from_html("https://archive.org/details/spiderman2_ps5", html_exfat)
        self.assertEqual(res_exfat, "https://archive.org/download/spiderman2_ps5/Spider_Man_2_PPSA08338.exfat")

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

        hint_buzz = get_captcha_hint_if_applicable("https://buzzheavier.com/abc123xyz")
        self.assertIsNotNone(hint_buzz)
        self.assertIn("BuzzHeavier", hint_buzz)

        no_hint = get_captcha_hint_if_applicable("https://mediafire.com/file/abc")
        self.assertIsNone(no_hint)


if __name__ == "__main__":
    unittest.main()
