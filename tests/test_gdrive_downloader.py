# -*- coding: utf-8 -*-
"""Verification suite for .agents/skills/gdrive-downloader/scripts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / ".agents" / "skills" / "gdrive-downloader" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from gdrive_auth import load_cookies, normalize_expiry
from gdrive_common import classify_html_block, parse_drive_url, sanitize_filename
from gdrive_files import parse_virus_scan_form
from gdrive_media import classify_stream_url, strip_stream_params, verify_media
from gdrive_workspace import (
    build_xlsx_from_sheets,
    clean_gdoc_html_to_markdown,
    extract_docs_model_chunks,
    markdown_to_docx,
    parse_htmlview_to_sheets,
)


class TestGDriveDownloader(unittest.TestCase):
    def test_url_parsing_all_variants(self):
        doc = parse_drive_url("https://docs.google.com/document/d/1a5Ghxl2x8P-wDPWECHbVDrOfSUXLW6PKNanpm4YQLCw/edit?tab=t.0")
        self.assertEqual(doc.kind, "gdoc")
        self.assertEqual(doc.file_id, "1a5Ghxl2x8P-wDPWECHbVDrOfSUXLW6PKNanpm4YQLCw")
        self.assertEqual(doc.tab, "t.0")

        sheet = parse_drive_url("https://docs.google.com/spreadsheets/u/1/d/1h_R9Dv4ZVgDfst4uh58uNNdsuDyulY2N_vunflix1-E/edit#gid=920989393")
        self.assertEqual(sheet.kind, "gsheet")
        self.assertEqual(sheet.authuser, "1")
        self.assertEqual(sheet.gid, "920989393")

        slide = parse_drive_url("https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit")
        self.assertEqual(slide.kind, "gslide")

        folder = parse_drive_url("https://drive.google.com/drive/folders/1FoldErIdAbCdEfGhIjKlMnOpQr?resourcekey=0-xyz")
        self.assertEqual(folder.kind, "folder")
        self.assertEqual(folder.resourcekey, "0-xyz")

        vid = parse_drive_url("https://drive.google.com/file/d/1Vq0KqG8Gtq9EEZtJFWgkqewAY1Ars6ro/view?ts=6ab5de70")
        self.assertEqual(vid.kind, "drive_file")
        self.assertEqual(vid.file_id, "1Vq0KqG8Gtq9EEZtJFWgkqewAY1Ars6ro")

        uc = parse_drive_url("https://drive.google.com/uc?id=13vJQg-xOzJKtmkB5ukgqD5y7DnP3AP03&export=download")
        self.assertEqual(uc.kind, "drive_file")
        self.assertEqual(uc.file_id, "13vJQg-xOzJKtmkB5ukgqD5y7DnP3AP03")

        raw = parse_drive_url("13vJQg-xOzJKtmkB5ukgqD5y7DnP3AP03")
        self.assertEqual(raw.kind, "drive_file")

    def test_cookie_expiry_normalization(self):
        self.assertIsNone(normalize_expiry(0))
        self.assertIsNone(normalize_expiry(None))
        self.assertEqual(normalize_expiry(1790000000), 1790000000)
        self.assertEqual(normalize_expiry(1790000000123), 1790000000)
        self.assertEqual(normalize_expiry(1790000000123456), 1790000000)

    def test_dash_stream_params_and_priority(self):
        raw_v = (
            "https://rr2---sn-42u.googlevideo.com/videoplayback?"
            "itag=136&mime=video%2Fmp4&clen=195273883&range=0-1023&rn=1&rbuf=0&ump=1&srfvp=1"
        )
        clean, clen = strip_stream_params(raw_v)
        self.assertEqual(clen, 195273883)
        for bad in ("range=", "rn=", "rbuf=", "ump=", "srfvp="):
            self.assertNotIn(bad, clean)

        v136 = classify_stream_url(raw_v)
        v134 = classify_stream_url("https://googlevideo.com/videoplayback?itag=134&mime=video/mp4")
        a140 = classify_stream_url("https://googlevideo.com/videoplayback?itag=140&mime=audio/mp4")
        self.assertEqual(v136[0], "video")
        self.assertLess(v136[1], v134[1])  # 720p (136) prioritized over 360p (134)
        self.assertEqual(a140[0], "audio")

    def test_gdoc_html_strips_js_css_and_preserves_table(self):
        sample_html = """
        <html><head><title>BẢNG VIẾT AE333 - Google Tài liệu</title>
        <style>body { counter-increment: list1; }</style>
        <script>DOCS_initDocsMobileWeb(); navigator.sendBeacon('/log');</script>
        </head><body>
        <h1>Lesson 1</h1>
        <p>Xem <a href="https://www.google.com/url?q=https://aptiseasy.edu.vn&sa=D">Aptis Easy</a></p>
        <table><tr><th>Part</th><th>Limit</th></tr><tr><td>Part 1</td><td>1-5 words</td></tr></table>
        <script>enterCoreJsErrorDialog();</script>
        </body></html>
        """
        title, md = clean_gdoc_html_to_markdown(sample_html)
        self.assertIn("BẢNG VIẾT AE333", title)
        self.assertIn("# Lesson 1", md)
        self.assertIn("[Aptis Easy](https://aptiseasy.edu.vn)", md)
        self.assertIn("| Part | Limit |", md)
        for leak in ("DOCS_initDocsMobileWeb", "navigator.sendBeacon", "counter-increment", "enterCoreJsErrorDialog"):
            self.assertNotIn(leak, md)

    def test_docs_model_chunk_extraction(self):
        html = '<script>DOCS_modelChunk = [{"ty":"is","ibi":1,"s":"Hello Aptis\\nPart 2"}];</script>'
        self.assertEqual(extract_docs_model_chunks(html), "Hello Aptis\nPart 2")

    def test_markdown_to_docx_has_page_numbering(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_docx = Path(tmp) / "test_doc.docx"
            markdown_to_docx("# Title\n\n| Col1 | Col2 |\n|---|---|\n| A | B |\n", out_docx, title="Test Doc")
            self.assertTrue(out_docx.exists())
            with ZipFile(out_docx, "r") as zf:
                footer_xmls = [zf.read(n).decode("utf-8") for n in zf.namelist() if "footer" in n]
                doc_xml = zf.read("word/document.xml").decode("utf-8")
            self.assertTrue(any('w:instr="PAGE"' in x for x in footer_xmls), "Missing PAGE field in footer")
            self.assertIn("w:titlePg", doc_xml, "First page suppression (different_first_page_header_footer) missing")

    def test_htmlview_gsheet_rebuild_xlsx(self):
        hv = """
        <html><body>
        <ul id="sheet-menu">
          <li id="sheet-button-111"><a>LISTENING</a></li>
          <li id="sheet-button-222"><a>READING</a></li>
        </ul>
        <div id="111"><table class="waffle">
          <tr><th class="row-header-shim">1</th><td>Q1</td><td>Answer A</td></tr>
          <tr><th class="row-header-shim">2</th><td colspan="2">Merged Note</td></tr>
        </table></div>
        <div id="222"><table class="waffle">
          <tr><th class="row-header-shim">1</th><td>Part 4</td><td>Heading</td></tr>
        </table></div>
        </body></html>
        """
        sheets = parse_htmlview_to_sheets(hv)
        self.assertEqual(list(sheets.keys()), ["LISTENING", "READING"])
        self.assertEqual(sheets["LISTENING"][0], ["Q1", "Answer A"])
        self.assertEqual(sheets["LISTENING"][1], ["Merged Note", ""])

        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / "out.xlsx"
            build_xlsx_from_sheets(sheets, xlsx_path)
            import openpyxl
            wb = openpyxl.load_workbook(xlsx_path)
            self.assertEqual(wb.sheetnames, ["LISTENING", "READING"])
            self.assertEqual(wb["LISTENING"]["A1"].value, "Q1")
            wb.close()

    def test_virus_scan_form_and_block_classification(self):
        html = """
        <html><body>
        <p>Google Drive can't scan this file for viruses.</p>
        <form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
          <input type="hidden" name="id" value="FILE123">
          <input type="hidden" name="export" value="download">
          <input type="hidden" name="confirm" value="t">
          <input type="hidden" name="uuid" value="abc-123">
        </form>
        </body></html>
        """
        self.assertEqual(classify_html_block(html.encode("utf-8"), "text/html"), "virus_scan_warning")
        url = parse_virus_scan_form(html)
        self.assertIsNotNone(url)
        self.assertIn("id=FILE123", url)
        self.assertIn("confirm=t", url)
        self.assertIn("uuid=abc-123", url)

        self.assertEqual(classify_html_block(b"<html>Quota exceeded</html>", "text/html"), "quota_exceeded")
        self.assertIsNone(classify_html_block(b"PK\x03\x04binary", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))

    def test_verify_media_edge_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.mp4"
            empty.write_bytes(b"")
            res = verify_media(empty)
            self.assertFalse(res["verified"])

    def test_sanitize_filename_strips_google_suffixes(self):
        self.assertEqual(sanitize_filename("Buoi 8: Video? - Google Drive"), "Buoi 8_ Video_")

    def test_local_firefox_cookie_discovery_count_only(self):
        cookies = load_cookies()
        self.assertIsInstance(cookies, list)
        self.assertGreater(len(cookies), 0, "Expected Google/YouTube cookies from local Firefox profile")


if __name__ == "__main__":
    unittest.main(verbosity=2)
