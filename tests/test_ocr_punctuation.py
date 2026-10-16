import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ocr_punctuation import tilde_candidates, tilde_change_warning


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STA_TEXT = ("DTC is subject to regulations adopted by the Commission, but as a "
            "self~regulatory organization it also may adopt its own rules and regulations")


def decision(text=STA_TEXT):
    return {"id": "p0001-b0001", "type": "body", "confidence": 0.99,
            "text": text.replace("self~regulatory", "self-regulatory"),
            "tilde_corrections": [{"offset": text.index("~"), "confidence": 0.99,
                "visual_evidence": "The scan shows a short straight horizontal hyphen after self."}]}


class PunctuationTests(unittest.TestCase):
    def test_sta_petition_verified_hyphen(self):
        reviewed = decision()
        self.assertEqual(tilde_candidates(STA_TEXT)[0]["offset"], STA_TEXT.index("~"))
        self.assertIsNone(tilde_change_warning(STA_TEXT, reviewed["text"], reviewed))

    def test_unsupported_or_ambiguous_corrections_are_rejected(self):
        for value in (None, [], [{"offset": 0, "confidence": 1, "visual_evidence": "hyphen"}],
                      [{"offset": STA_TEXT.index("~"), "confidence": 0.97, "visual_evidence": "blurry"}],
                      [{"offset": STA_TEXT.index("~"), "confidence": 1, "visual_evidence": ""}],
                      [{"offset": STA_TEXT.index("~"), "confidence": float("nan"), "visual_evidence": "hyphen"}]):
            with self.subTest(value=value):
                reviewed = decision()
                reviewed["tilde_corrections"] = value
                self.assertIsNotNone(tilde_change_warning(STA_TEXT, reviewed["text"], reviewed))

    def test_low_block_confidence_and_non_hyphen_replacements_are_rejected(self):
        reviewed = decision()
        reviewed["confidence"] = 0.97
        self.assertIsNotNone(tilde_change_warning(STA_TEXT, reviewed["text"], reviewed))
        reviewed["confidence"] = 0.99
        for replacement in ("", " ", "x"):
            self.assertIsNotNone(tilde_change_warning(STA_TEXT, STA_TEXT.replace("~", replacement), reviewed))

    def test_legitimate_tildes_and_correct_hyphens_survive(self):
        source = STA_TEXT + " x~y; ~10; https://example.org/~user; self-regulatory"
        reviewed = decision(source)
        self.assertIsNone(tilde_change_warning(source, reviewed["text"], reviewed))
        self.assertIsNotNone(tilde_change_warning(source, reviewed["text"].replace("x~y", "x-y"), reviewed))
        self.assertIsNone(tilde_change_warning(source, source, {}))

    def test_review_prompt_includes_candidates_and_evidence_contract(self):
        reviewer = load_script("punctuation_reviewer", "ollama-review-ocr.py")
        block = {"id": "p0001-b0001", "type": "body", "text": STA_TEXT,
                 "protected_tokens": [], "position": {}}
        import json
        with patch.object(reviewer, "ollama_chat", return_value=json.dumps({"decisions": [decision()]})) as chat:
            reviewer.review_page(SimpleNamespace(endpoint="unused", review_model="test",
                extract_model="test", skip_extractor=True), b"page-image", 5, [block], "")
        self.assertIn('"tilde_candidates"', chat.call_args.args[2])
        self.assertIn("tilde_corrections", chat.call_args.args[4]["properties"]["decisions"]["items"]["required"])

    def test_tagger_writes_corrected_actualtext_without_changing_drawing(self):
        import pikepdf
        tagger = load_script("punctuation_tagger", "tag-ocr-pdf.py")
        with tempfile.TemporaryDirectory() as folder:
            source, output, report = (Path(folder) / name for name in ("source.pdf", "output.pdf", "report.json"))
            text = "DTC is a self~regulatory organization."
            content = f"BT /F1 12 Tf 1 0 0 1 72 700 Tm ({text}) Tj ET".encode()
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pikepdf.Dictionary(
                    Subtype=pikepdf.Name.Type1, Encoding=pikepdf.Name.WinAnsiEncoding,
                    BaseFont=pikepdf.Name.Helvetica)))
                page.Contents = pdf.make_stream(content)
                pdf.save(source)
            original_bytes = source.read_bytes()
            for evidence in (True, False):
                reviewed = decision(text)
                if not evidence:
                    reviewed.pop("tilde_corrections")
                result = tagger.tag_pdf(source, output, report, decisions={reviewed["id"]: reviewed})
                expected = reviewed["text"] if evidence else text
                self.assertEqual(result["blocks"][0]["text"], expected)
                self.assertEqual(bool(result["review_warnings"]), not evidence)
                with pikepdf.open(output) as pdf:
                    instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
                    actual = [str(i.operands[1]["/ActualText"]) for i in instructions
                              if str(i.operator) == "BDC" and "/ActualText" in i.operands[1]]
                    self.assertEqual(actual, [expected])
                    drawing = [i for i in instructions if str(i.operator) not in {"BDC", "BMC", "EMC"}]
                    original = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, content)))
                    self.assertEqual(pikepdf.unparse_content_stream(drawing),
                                     pikepdf.unparse_content_stream(original))
                self.assertEqual(source.read_bytes(), original_bytes)
                output.unlink()


if __name__ == "__main__":
    unittest.main()
