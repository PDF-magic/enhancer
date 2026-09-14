"""Browser regressions; run with .venv/bin/python -m unittest discover -s tests."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location(
    "exporter", Path(__file__).resolve().parents[1] / "export-google-book.py"
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ReaderNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_content('''
            <input aria-label="Page" value="46">
            <canvas width="400" height="500"></canvas>
            <script>
            const input = document.querySelector('input');
            const canvas = document.querySelector('canvas');
            const ctx = canvas.getContext('2d');
            function render(n) {
                ctx.fillStyle = n % 2 ? 'navy' : 'maroon';
                ctx.fillRect(0, 0, 400, 500);
            }
            render(46);
            </script>
        ''')
        self.timeout = patch.object(exporter, 'NAV_CHANGE_TIMEOUT', 2)
        self.settle = patch.object(exporter, 'RENDER_SETTLE_SECONDS', 0.3)
        self.timeout.start()
        self.settle.start()

    def tearDown(self):
        self.timeout.stop()
        self.settle.stop()
        self.page.close()

    def test_rejected_jump_is_not_success(self):
        self.page.evaluate('''() => input.onkeydown = e => {
            if (e.key === 'Enter') setTimeout(() => input.value = '46', 100);
        }''')
        self.assertFalse(exporter.jump_to_reader_page(self.page, 47, attempts=1))

    def test_page_one_is_actually_requested(self):
        self.page.evaluate('''() => input.onkeydown = e => {
            if (e.key === 'Enter') setTimeout(() => render(Number(input.value)), 100);
        }''')
        self.assertTrue(exporter.jump_to_reader_page(self.page, 1, attempts=1))
        self.assertEqual(exporter.read_reader_page_number(self.page), 1)

    def test_hidden_control_does_not_confirm_jump(self):
        self.page.evaluate('''() => input.onkeydown = e => {
            if (e.key === 'Enter') input.style.display = 'none';
        }''')
        self.assertFalse(exporter.jump_to_reader_page(self.page, 47, attempts=1))

    def test_counter_alone_does_not_confirm_next_page(self):
        _, digest, _, box = exporter.capture_page(self.page)
        visual = exporter.visible_page_digest(self.page, box)
        self.page.evaluate("input.value = '47'")
        changed, _ = exporter.wait_for_page_change_once(
            self.page, box, visual, digest, 46, timeout=1,
        )
        self.assertFalse(changed)

    def test_next_page_waits_for_delayed_render(self):
        _, digest, _, box = exporter.capture_page(self.page)
        self.page.evaluate('''() => {
            window.turns = 0;
            document.onkeydown = e => {
                if (e.key === 'ArrowRight') {
                    window.turns++;
                    input.value = '47';
                    setTimeout(() => render(47), 500);
                }
            };
        }''')
        self.assertTrue(exporter.advance_page(self.page, box, digest))
        self.assertEqual(self.page.evaluate('window.turns'), 1)
        self.assertNotEqual(exporter.capture_page(self.page)[1], digest)

    def test_iframe_slider_jump_and_next_button(self):
        self.page.set_content('<iframe style="width:600px;height:650px"></iframe>')
        frame = self.page.frames[1]
        frame.set_content('''<canvas width="400" height="500"></canvas>
            <input type="range" min="0" max="442" value="45"
                   aria-valuetext="Page 30">
            <button aria-label="Next Page">Next</button>
            <script>
            const slider = document.querySelector('input');
            const ctx = document.querySelector('canvas').getContext('2d');
            function render() {
                ctx.fillStyle = Number(slider.value) % 2 ? 'maroon' : 'navy';
                ctx.fillRect(0, 0, 400, 500);
            }
            render();
            slider.onchange = () => setTimeout(render, 100);
            document.querySelector('button').onclick = () => {
                slider.value = Number(slider.value) + 1;
                setTimeout(render, 100);
            };
            </script>''')
        self.assertEqual(exporter.read_reader_page_number(self.page), 46)
        self.assertTrue(exporter.jump_to_reader_page(self.page, 47, attempts=1))
        _, digest, _, box = exporter.capture_page(self.page)
        self.assertTrue(exporter.advance_page(self.page, box, digest))
        self.assertEqual(exporter.read_reader_page_number(self.page), 48)


class CommandLineTests(unittest.TestCase):
    def test_book_id_selects_isolated_default_outputs(self):
        args = exporter.parse_args(["ctmJB8IRXQcC"])
        output_dir, output_pdf = exporter.output_paths(args.book_id)

        self.assertEqual(args.book_id, "ctmJB8IRXQcC")
        self.assertIsNone(args.max_pages)
        self.assertEqual(output_dir, Path("ctmJB8IRXQcC-pages"))
        self.assertEqual(output_pdf, Path("ctmJB8IRXQcC.pdf"))
        self.assertEqual(
            exporter.reader_url(args.book_id),
            "https://play.google.com/books/reader?id=ctmJB8IRXQcC",
        )

    def test_custom_output_paths_are_preserved(self):
        args = exporter.parse_args([
            "ctmJB8IRXQcC",
            "--output-dir", "captures/book",
            "--output-pdf", "captures/book.pdf",
            "--max-pages", "860",
        ])

        self.assertEqual(args.max_pages, 860)
        self.assertEqual(
            exporter.output_paths(
                args.book_id, args.output_dir, args.output_pdf,
            ),
            (Path("captures/book"), Path("captures/book.pdf")),
        )

    def test_path_like_book_id_is_rejected(self):
        with self.assertRaises(exporter.argparse.ArgumentTypeError):
            exporter.book_id("../another-book")

    def test_verified_initial_page_skips_prompt(self):
        prompt = Mock()
        with (
            patch.object(exporter, "jump_to_reader_page", return_value=True),
            patch.object(exporter, "capture_page", return_value=(b"png", "hash", "CANVAS", {})),
        ):
            exporter.prepare_reader_for_capture(Mock(), 1, prompt=prompt)

        prompt.assert_not_called()

    def test_unverified_initial_page_retains_manual_prompt(self):
        prompt = Mock()
        with patch.object(exporter, "jump_to_reader_page", return_value=False):
            exporter.prepare_reader_for_capture(Mock(), 1, prompt=prompt)

        prompt.assert_called_once_with("Press Enter when ready to start capture... ")

    def test_final_capture_is_removed_automatically(self):
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            first = output_dir / "0001.png"
            last = output_dir / "0002.png"
            first.write_bytes(b"book page")
            last.write_bytes(b"reading complete")

            removed = exporter.remove_last_page(output_dir)

            self.assertTrue(removed)
            self.assertTrue(first.exists())
            self.assertFalse(last.exists())

    def test_no_final_capture_is_a_no_op(self):
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            removed = exporter.remove_last_page(output_dir)

            self.assertFalse(removed)


if __name__ == '__main__':
    unittest.main()
