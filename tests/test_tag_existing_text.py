import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pikepdf


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

spec = importlib.util.spec_from_file_location(
    "tag_ocr_pdf", Path(__file__).resolve().parents[1] / "tag-ocr-pdf.py"
)
tagger = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tagger
spec.loader.exec_module(tagger)


class ExistingTextTests(unittest.TestCase):
    def test_heading_outline_preserves_hierarchy_and_page_destinations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sections.pdf"
            with pikepdf.Pdf.new() as pdf:
                for _ in range(3):
                    pdf.add_blank_page()
                count = tagger.write_heading_outline(pdf, [
                    {"level": "H1", "text": "I. Introduction", "page": 1},
                    {"level": "H3", "text": "A. Background", "page": 2},
                    {"level": "H4", "text": "1. Detail", "page": 2},
                    {"level": "H2", "text": "B. Findings", "page": 3},
                    {"level": "H1", "text": "II. Conclusion", "page": 3},
                    {"level": "H2", "text": "Invalid page", "page": 4},
                ])
                self.assertEqual(count, 5)
                pdf.save(path)
            with pikepdf.open(path) as pdf:
                with pdf.open_outline() as outline:
                    first, last = outline.root
                    self.assertEqual(first.title, "I. Introduction")
                    self.assertEqual(last.title, "II. Conclusion")
                    self.assertEqual([item.title for item in first.children], ["A. Background", "B. Findings"])
                    self.assertEqual(first.children[0].children[0].title, "1. Detail")
                    self.assertEqual(first.destination[0].objgen, pdf.pages[0].obj.objgen)
                    self.assertEqual(last.destination[0].objgen, pdf.pages[2].obj.objgen)

    def test_no_recognized_headings_preserves_existing_outline(self):
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page()
            with pdf.open_outline() as outline:
                outline.root.append(pikepdf.OutlineItem("Existing section", destination=0))
            self.assertEqual(tagger.write_heading_outline(pdf, []), 0)
            with pdf.open_outline() as outline:
                self.assertEqual(outline.root[0].title, "Existing section")

    def test_no_new_ocr_preserves_existing_content_and_structure(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "output.pdf"
            report_path = Path(folder) / "output.tagging.json"
            content = b"BT /F1 12 Tf (Existing text) Tj ET"
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                page.obj.Contents = pdf.make_stream(content)
                pdf.Root.StructTreeRoot = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.StructTreeRoot,
                    TestMarker="Preserve original structure",
                ))
                pdf.save(source)
            report = tagger.tag_pdf(source, output, report_path)
            self.assertEqual(report["tagged_pages"], 0)
            self.assertTrue(report["preserved_existing_text"])
            self.assertEqual(json.loads(report_path.read_text()), report)
            tagger.validate_output(output, 1, require_tags=False)
            with pikepdf.open(output) as pdf:
                self.assertEqual(pdf.pages[0].obj.Contents.read_bytes(), content)
                self.assertEqual(str(pdf.Root.StructTreeRoot.TestMarker), "Preserve original structure")
                self.assertNotIn("/MarkInfo", pdf.Root)

    def test_unicode_ranges_and_array_destinations(self):
        with pikepdf.Pdf.new() as pdf:
            font = pikepdf.Dictionary(ToUnicode=pdf.make_stream(
                b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
                b"1 beginbfchar <0020> <0020> endbfchar "
                b"2 beginbfrange <0001> <0003> <0041> "
                b"<0010> <0011> [<0066 0069> <0062>] endbfrange"
                .replace(b"0066 0069", b"00660069")
            ))
            mapping = tagger.unicode_map(font)
            self.assertNotIn(b"\x00\x00", mapping)
            self.assertEqual(mapping[b"\x00\x03"], "C")
            self.assertEqual(mapping[b"\x00\x10"], "fi")
            self.assertEqual(mapping[b"\x00\x11"], "b")

    def test_embedded_text_builds_headings_without_ocr_or_pixel_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                font = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name("/Helvetica-Bold"), Encoding=pikepdf.Name.WinAnsiEncoding,
                ))
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
                original = b"BT /F1 24 Tf 1 0 0 1 72 700 Tm (I.) Tj ET BT /F1 24 Tf 1 0 0 1 95 700 Tm (Introduction) Tj ET"
                page.obj.Contents = pdf.make_stream(original)
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            self.assertEqual(report["tagged_pages"], 1)
            self.assertEqual(report["headings"], [{"page": 1, "level": "H1", "text": "I. Introduction"}])
            tagger.validate_output(output, 1)
            with pikepdf.open(output) as pdf:
                instructions = [item for item in pikepdf.parse_content_stream(pdf.pages[0]) if str(item.operator) not in {"BDC", "BMC", "EMC"}]
                before = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, original)))
                self.assertEqual(pikepdf.unparse_content_stream(instructions), pikepdf.unparse_content_stream(before))
                self.assertIn("/StructParents", pdf.pages[0].obj)
                reference = pdf.Root.StructTreeRoot.K[0].K[0].K
                self.assertNotIn("/Stm", reference)
                with pdf.open_outline() as outline:
                    self.assertEqual(outline.root[0].title, "I. Introduction")

    def test_native_fragments_remain_text_and_recurring_markers_set_depth(self):
        lines = [
            tagger.OcrLine(0, 1, "I", 72, 700, 16, artifact=True),
            tagger.OcrLine(2, 3, "a) Nested Section", 108, 650, 16, bold=True),
            tagger.OcrLine(4, 5, "17 Dec 2024", 108, 620, 16, bold=True),
            tagger.OcrLine(6, 7, "Running Header", 150, 750, 16, bold=True),
        ]
        tagger.classify_existing_lines(lines, [24, 18, 16], {"Running Header"}, 612, {"H5"})
        self.assertFalse(lines[0].artifact)
        self.assertEqual(lines[1].heading, "H4")
        self.assertIsNone(lines[2].heading)
        self.assertTrue(lines[3].artifact)

    def test_untagged_copy_still_requires_matching_page_count(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "output.pdf"
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.save(output)
            tagger.validate_output(output, 1, require_tags=False)
            with self.assertRaisesRegex(ValueError, "page count changed"):
                tagger.validate_output(output, 2, require_tags=False)
            with self.assertRaisesRegex(ValueError, "no /StructTreeRoot"):
                tagger.validate_output(output, 1)


if __name__ == "__main__":
    unittest.main()
