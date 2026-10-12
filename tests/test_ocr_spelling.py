import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocr_spelling import preserve_source_spellings

spec = importlib.util.spec_from_file_location("spelling_tagger", ROOT / "tag-ocr-pdf.py")
tagger = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tagger
spec.loader.exec_module(tagger)


class SourceSpellingTests(unittest.TestCase):
    def test_real_printed_typos_survive_claimed_high_confidence_corrections(self):
        for source, corrected in [("btgin", "begin"), ("mdember", "member"),
                                  ("adition", "addition"), ("fullfill", "fulfill"),
                                  ("hugh", "huge")]:
            with self.subTest(source=source):
                block = tagger.LogicalBlock(0, 1, f"They {source} operations.", "P", "body", [])
                accepted, warning = tagger.apply_review(block, "b1", {"b1": {
                    "type": "body", "text": f"They {corrected} operations.",
                    "confidence": 1.0, "sensitive_change_evidence": "Visibly legible.",
                }})
                self.assertTrue(accepted)
                self.assertIn(source, block.text)
                self.assertIn("source spellings preserved", warning)

    def test_spelling_preservation_keeps_layout_and_larger_recognition_repairs(self):
        source = "~ In adition, mdember banks have inter- rupted SRS operations."
        proposed = "In addition, member banks have interrupted DTC operations."
        text, changes = preserve_source_spellings(source, proposed)
        self.assertEqual(text, "In adition, mdember banks have interrupted DTC operations.")
        self.assertEqual({item["source"] for item in changes}, {"adition", "mdember"})

    def test_multiple_small_edits_in_a_run_and_word_insertions(self):
        text, _ = preserve_source_spellings("could btgin operations", "could soon begin operations")
        self.assertEqual(text, "could soon btgin operations")

    def test_case_punctuation_and_unchanged_text_are_not_spelling_changes(self):
        self.assertEqual(preserve_source_spellings("word, next", "Word; next"), ("Word; next", []))
        self.assertEqual(preserve_source_spellings("btgin", "btgin"), ("btgin", []))

    def test_small_genuine_recognition_errors_are_conservatively_retained(self):
        self.assertEqual(preserve_source_spellings("rednction", "reduction")[0], "rednction")

    def test_sensitive_changes_still_require_evidence(self):
        block = tagger.LogicalBlock(0, 1, "Paid $650,000.", "P", "body", [])
        _, warning = tagger.apply_review(block, "b1", {"b1": {
            "type": "body", "text": "Paid $850,000.", "confidence": 0.99,
            "sensitive_change_evidence": "",
        }})
        self.assertEqual(block.text, "Paid $650,000.")
        self.assertIn("sensitive text change rejected", warning)


if __name__ == "__main__":
    unittest.main()
