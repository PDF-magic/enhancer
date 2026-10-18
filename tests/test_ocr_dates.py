"""Regressions for dates that survive normal OCR as Roman-looking glyphs."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr_dates import normalize_ocr_dates


class OcrDateTests(unittest.TestCase):
    def test_sec_release_date_with_capital_i(self):
        source = "See Exchange Act Release No. 34040 (Dec. I, 1994)."
        corrected, edits = normalize_ocr_dates(source)
        self.assertEqual(corrected, "See Exchange Act Release No. 34040 (Dec. 1, 1994).")
        self.assertEqual(edits, [{"offset": source.index("I, 1994"),
                                  "original": "Dec. I, 1994",
                                  "corrected": "Dec. 1, 1994"}])
        self.assertIn("34040", corrected)  # No guessed release-number change.

    def test_lowercase_l_and_bar_in_multidigit_days(self):
        source = "Jan. l5, 2002; Mar. Il, 1983; Sept. |3, 2016"
        corrected, edits = normalize_ocr_dates(source)
        self.assertEqual(corrected, "Jan. 15, 2002; Mar. 11, 1983; Sept. 13, 2016")
        self.assertEqual(len(edits), 3)
        self.assertEqual([e["offset"] for e in edits], [5, 20, 36])

    def test_no_roman_numeral_or_non_date_rewrites(self):
        for text in (
            "Chapter I and Part II. Act I, 1994.",
            "Dec. IV, 1994 is a Roman-numeral heading.",
            "Dec. I is missing its year.",
            "On December I 1994 a comma is missing.",
            "A report in Dec. 1, 1994 needs no change.",
            "An internalNameDec. I, 1994 is not a standalone date.",
            "Feb. 3I, 2001 is not a real day.",
            "Feb. 29, 2001 is not an OCR-one candidate.",
            "Feb. I9, 0000 cannot be a real date.",
        ):
            with self.subTest(text=text):
                self.assertEqual(normalize_ocr_dates(text), (text, []))

    def test_leap_year_and_idempotency(self):
        text = "Feb. 2I, 2024 and Feb. 2I, 2023."
        corrected, changes = normalize_ocr_dates(text)
        self.assertEqual(corrected, "Feb. 21, 2024 and Feb. 21, 2023.")
        self.assertEqual(len(changes), 2)
        self.assertEqual(normalize_ocr_dates(corrected), (corrected, []))

    @unittest.skipUnless(importlib.util.find_spec("pikepdf"), "requires pikepdf")
    def test_tagger_corrects_pdf_actualtext_and_audits_without_rewriting_source(self):
        import pikepdf
        spec = importlib.util.spec_from_file_location(
            "ocr_date_tagger", Path(__file__).resolve().parents[1] / "tag-ocr-pdf.py")
        tagger = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = tagger
        spec.loader.exec_module(tagger)
        with tempfile.TemporaryDirectory() as folder:
            source, output, report = (Path(folder) / name for name in
                                      ("source.pdf", "output.pdf", "report.json"))
            text = "See Exchange Act Release No. 34040 (Dec. I, 1994)."
            content = f"BT /F1 12 Tf 1 0 0 1 72 700 Tm ({text}) Tj ET".encode()
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                    F1=pikepdf.Dictionary(Subtype=pikepdf.Name.Type1,
                    Encoding=pikepdf.Name.WinAnsiEncoding,
                    BaseFont=pikepdf.Name.Helvetica)))
                page.Contents = pdf.make_stream(content)
                pdf.save(source)
            source_bytes = source.read_bytes()
            result = tagger.tag_pdf(source, output, report)
            expected = text.replace("Dec. I,", "Dec. 1,")
            self.assertEqual(result["blocks"][0]["text"], expected)
            self.assertEqual(result["ocr_date_corrections"][0]["original"], "Dec. I, 1994")
            self.assertEqual(result["ocr_date_corrections"][0]["corrected"], "Dec. 1, 1994")
            self.assertEqual(result["ocr_date_corrections"][0]["id"], "p0001-b0001")
            with pikepdf.open(output) as pdf:
                instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
                actual = [str(i.operands[1]["/ActualText"]) for i in instructions
                          if str(i.operator) == "BDC" and "/ActualText" in i.operands[1]]
                self.assertEqual(actual, [expected])
                drawing = [i for i in instructions if str(i.operator) not in {"BDC", "BMC", "EMC"}]
                original = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, content)))
                self.assertEqual(pikepdf.unparse_content_stream(drawing),
                                 pikepdf.unparse_content_stream(original))
            self.assertEqual(source.read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main()
