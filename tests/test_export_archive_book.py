"""Internet Archive parsing, scan selection, and PDF assembly regressions."""
import argparse
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location(
    "archive_exporter", Path(__file__).resolve().parents[1] / "export-archive-book.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class InputTests(unittest.TestCase):
    def test_linked_url_and_id(self):
        for source in ["transformationof00seli",
                       "https://archive.org/details/transformationof00seli/mode/2up"]:
            self.assertEqual(exporter.book_id(source), "transformationof00seli")

    def test_rejects_unsafe_sources(self):
        for source in ["../book", "--help", "https://example.org/details/book",
                       "https://user@archive.org/details/book", "https://archive.org/details/"]:
            with self.subTest(source=source), self.assertRaises(argparse.ArgumentTypeError):
                exporter.book_id(source)

    def test_invalid_ranges(self):
        for args in [["book", "--start", "0"], ["book", "--start", "10", "--end", "2"]]:
            with self.assertRaises(SystemExit):
                exporter.parse_args(args)


class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.page = self.browser.new_page()
        self.page.set_content('''<script>
          window.br = {
            mode: 2, index: 0, book: {getNumLeafs: () => 3},
            switchMode(mode) { this.mode = mode; },
            currentIndex() { return this.index; },
            jumpToIndex(index) {
              this.index = index;
              setTimeout(() => {
                const canvas = document.createElement('canvas');
                canvas.width = 300; canvas.height = 400;
                const context = canvas.getContext('2d');
                context.fillStyle = index === 0 ? 'navy' : 'white';
                context.fillRect(0, 0, 300, 400);
                const container = document.createElement('div');
                container.className = 'BRpagecontainer';
                container.dataset.index = index;
                const image = new Image(); image.className = 'BRpageimage';
                image.src = canvas.toDataURL(); container.append(image);
                document.body.replaceChildren(container);
              }, 100);
            }
          };
        </script><img class="BRpageimage">''')

    def tearDown(self):
        self.page.close()
        self.directory.cleanup()

    def test_delayed_scans_preserve_identical_blank_pages_and_resume_holes(self):
        captures = self.root / "pages"
        captures.mkdir()
        Image.new("RGB", (300, 400), "white").save(captures / "0003.png")
        pdf = self.root / "book.pdf"
        exporter.export_pages(self.page, captures, pdf)
        self.assertEqual(self.page.evaluate("br.mode"), 1)
        self.assertEqual(len(list(captures.glob("*.png"))), 3)
        with Image.open(captures / "0001.png") as first, Image.open(captures / "0002.png") as second:
            self.assertEqual(first.getpixel((10, 10))[:3], (0, 0, 128))
            self.assertEqual(second.getpixel((10, 10))[:3], (255, 255, 255))
        import pikepdf
        with pikepdf.open(pdf) as document:
            self.assertEqual(len(document.pages), 3)

    def test_unavailable_scan_preserves_existing_pdf(self):
        pdf = self.root / "book.pdf"
        pdf.write_bytes(b"existing document")
        with patch.object(exporter, "capture_scan", side_effect=RuntimeError("unavailable")):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                exporter.export_pages(self.page, self.root / "pages", pdf)
        self.assertEqual(pdf.read_bytes(), b"existing document")

    def test_final_scan_exports_when_viewport_counter_stays_on_previous_scan(self):
        self.page.evaluate("() => { br.currentIndex = () => 1; }")
        pdf = self.root / "book.pdf"
        exporter.export_pages(self.page, self.root / "pages", pdf)
        import pikepdf
        with pikepdf.open(pdf) as document:
            self.assertEqual(len(document.pages), 3)

    def test_range_beyond_reader_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "3 scans"):
            exporter.export_pages(self.page, self.root / "pages", self.root / "book.pdf", end=4)


if __name__ == "__main__":
    unittest.main()
