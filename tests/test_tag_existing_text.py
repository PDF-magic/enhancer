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
    def test_retag_keeps_optional_content_scopes(self):
        with pikepdf.Pdf.new() as pdf:
            content = b'/OC /Layer BDC /P <</MCID 0>> BDC BT (visible) Tj ET EMC EMC /Artifact BMC 0 0 10 10 re f EMC'
            instructions = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, content)))
            retained = tagger.remove_structure_markers(instructions)
            self.assertEqual([str(i.operator) for i in retained], ['BDC', 'BT', 'Tj', 'ET', 'EMC', 're', 'f'])
            self.assertEqual(str(retained[0].operands[0]), '/OC')

    def test_decorative_form_does_not_hide_native_or_ocr_text(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            fonts = pikepdf.Dictionary(F1=pikepdf.Dictionary(
                Subtype=pikepdf.Name.Type1, Encoding=pikepdf.Name.WinAnsiEncoding,
                BaseFont=pikepdf.Name.Helvetica))
            artwork = pdf.make_stream(b"0 0 10 10 re f")
            artwork.Subtype = pikepdf.Name.Form
            artwork.Resources = pikepdf.Dictionary()
            ocr = pdf.make_stream(b"BT /F1 12 Tf (Actual text) Tj ET")
            ocr.Subtype = pikepdf.Name.Form
            ocr.Resources = pikepdf.Dictionary(Font=fonts)
            page.obj.Resources = pikepdf.Dictionary(
                Font=fonts, XObject=pikepdf.Dictionary(Artwork=artwork, OCR=ocr))
            page.obj.Contents = pdf.make_stream(b"/Artwork Do /OCR Do")
            self.assertEqual(tagger.text_target(pdf, page), ocr)
            page.obj.Contents = pdf.make_stream(b"BT /F1 12 Tf (Native text) Tj ET /Artwork Do")
            self.assertEqual(tagger.text_target(pdf, page), page.obj)
            pdf.Root.StructTreeRoot = pikepdf.Dictionary()
            self.assertIsNone(tagger.text_target(pdf, page))
            self.assertEqual(tagger.text_target(pdf, page, True), page.obj)

    def test_native_runs_and_wrapped_hyphens_preserve_words(self):
        lines = [tagger.OcrLine(0, 1, 'It i', 72, 700, 12, raw_text='It i', right=90, native=True),
                 tagger.OcrLine(2, 3, 's nine', 90, 700, 12, raw_text='s nine', right=125, native=True),
                 tagger.OcrLine(4, 5, 'head-and-', 72, 680, 12, native=True),
                 tagger.OcrLine(6, 7, 'shoulders', 72, 666, 12, native=True)]
        merged = tagger.merge_native_fragments(lines)
        self.assertEqual(merged[0].text, 'It is nine')
        self.assertEqual(tagger.join_lines(merged[1:]), 'head-and-shoulders')
        self.assertEqual(tagger.join_lines([
            tagger.OcrLine(0, 1, 'wrap-', 72, 700, 12),
            tagger.OcrLine(2, 3, 'ping', 72, 686, 12)]), 'wrapping')

    def test_retag_preserves_metadata_outline_and_rendering_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output, again = (Path(folder) / name for name in ('source.pdf', 'output.pdf', 'again.pdf'))
            content = b'/P <</MCID 0>> BDC BT /F1 12 Tf 1 0 0 1 72 700 Tm (First printed line) Tj 0 -14 Td (continues here.) Tj ET EMC'
            metadata = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                    F1=pikepdf.Dictionary(Subtype=pikepdf.Name.Type1,
                        Encoding=pikepdf.Name.WinAnsiEncoding, BaseFont=pikepdf.Name.Helvetica)))
                page.obj.Contents = pdf.make_stream(content)
                pdf.Root.StructTreeRoot = pikepdf.Dictionary()
                pdf.docinfo.Author = 'John Wooten'
                pdf.docinfo.Creator = 'John Wooten'
                pdf.Root.Metadata = pdf.make_stream(metadata)
                with pdf.open_outline() as outline:
                    outline.root.append(pikepdf.OutlineItem('Custom chapter: title', 0))
                pdf.save(source, fix_metadata_version=False)
            for before, after in ((source, output), (output, again)):
                report = tagger.tag_pdf(before, after, after.with_suffix('.json'), retag_existing=True)
                self.assertEqual(report['blocks'][0]['text'], 'First printed line continues here.')
                with pikepdf.open(after) as pdf:
                    self.assertEqual(str(pdf.docinfo.Author), 'John Wooten')
                    self.assertEqual(str(pdf.docinfo.Creator), 'John Wooten')
                    self.assertEqual(pdf.Root.Metadata.read_bytes(), metadata)
                    with pdf.open_outline() as outline:
                        self.assertEqual(outline.root[0].title, 'Custom chapter: title')
                    instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
                    self.assertEqual(sum(str(i.operator) == 'BDC' for i in instructions), 1)
                    visible = [i for i in instructions if str(i.operator) not in {'BDC', 'BMC', 'EMC'}]
                    original = [i for i in pikepdf.parse_content_stream(pikepdf.Stream(pdf, content))
                                if str(i.operator) not in {'BDC', 'BMC', 'EMC'}]
                    self.assertEqual(pikepdf.unparse_content_stream(visible), pikepdf.unparse_content_stream(original))

    def test_chapter_label_and_question_title_produce_one_outline_entry(self):
        for label in ("Chapter 3", "3", "Part Two"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
                content = (
                    f"BT /F1 10 Tf 1 0 0 1 190 700 Tm ({label}) Tj ET "
                    "BT /F1 14 Tf 1 0 0 1 140 683 Tm (Why Should I Trade Stocks?) Tj ET"
                ).encode()
                with pikepdf.Pdf.new() as pdf:
                    page = pdf.add_blank_page()
                    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                        F1=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                             BaseFont=pikepdf.Name('/Helvetica-Bold'),
                                             Encoding=pikepdf.Name.WinAnsiEncoding),
                    ))
                    page.obj.Contents = pdf.make_stream(content)
                    pdf.save(source)
                report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
                title = label + " Why Should I Trade Stocks?"
                self.assertEqual(report['headings'], [{'page': 1, 'level': 'H1', 'text': title}])
                with pikepdf.open(output) as pdf:
                    with pdf.open_outline() as outline:
                        self.assertEqual([item.title for item in outline.root], [title])
                    instructions = [item for item in pikepdf.parse_content_stream(pdf.pages[0])
                                    if str(item.operator) not in {'BDC', 'BMC', 'EMC'}]
                    original = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, content)))
                    self.assertEqual(pikepdf.unparse_content_stream(instructions),
                                     pikepdf.unparse_content_stream(original))

    def test_chapter_labels_allow_alternate_numbering_and_print_styles(self):
        cases = (
            ("Chapter Eleven", True, False, True, 14),
            ("Chap. 12", False, True, False, 14),
            ("Part Twenty-One", False, True, False, 14),
            ("Appendix B", False, False, True, 14),
            ("Chapter 8", False, False, False, 17),
            ("Chapter III", False, True, False, 14),
        )
        for label, label_bold, title_bold, title_italic, title_size in cases:
            with self.subTest(label=label):
                lines = [
                    tagger.OcrLine(0, 1, label, 190, 700, 10, bold=label_bold),
                    tagger.OcrLine(2, 3, "An Interesting Subject", 140, 683, title_size,
                                   bold=title_bold, italic=title_italic),
                ]
                merged = tagger.merge_chapter_titles(lines, [], 612)
                self.assertEqual(len(merged), 1)
                self.assertEqual(merged[0].heading, "H1")
                self.assertEqual(merged[0].text, label + " An Interesting Subject")

    def test_chapter_merging_is_conservative_about_ordinary_text(self):
        cases = (
            ("Chapter 3", "An unrelated paragraph", 11, False, 140, 683),
            ("Chapter 3", "An unrelated paragraph.", 17, False, 140, 683),
            ("Chapter 3", "An unrelated paragraph", 17, False, 140, 683),
            ("Chapter summary", "An Interesting Subject", 14, True, 140, 683),
            ("Chapter 3", "An Interesting Subject", 14, True, 420, 683),
            ("Chapter 3", "An Interesting Subject", 14, True, 140, 600),
            ("3", "An Interesting Subject", 17, False, 140, 683),
            ("Chapter 3", "An Interesting Subject", 9, True, 140, 683),
        )
        for label, title, size, bold, x, y in cases:
            with self.subTest(label=label, title=title, size=size, x=x, y=y):
                lines = [
                    tagger.OcrLine(0, 1, label, 190, 700, 10, bold=True),
                    tagger.OcrLine(2, 3, title, x, y, size, bold=bold),
                ]
                self.assertEqual(len(tagger.merge_chapter_titles(lines, [14, 10], 612)), 2)
                self.assertEqual(lines[0].text, label)

    def test_italic_chapter_title_retains_original_drawing_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            content = (
                b"BT /F1 10 Tf 1 0 0 1 190 700 Tm (Chapter Eleven) Tj ET "
                b"BT /F2 14 Tf 1 0 0 1 140 683 Tm (An Interesting Subject) Tj ET"
            )
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                    F1=pikepdf.Dictionary(Subtype=pikepdf.Name.Type1,
                                          BaseFont=pikepdf.Name.Helvetica,
                                          Encoding=pikepdf.Name.WinAnsiEncoding),
                    F2=pikepdf.Dictionary(Subtype=pikepdf.Name.Type1,
                                          BaseFont=pikepdf.Name("/Times-Italic"),
                                          Encoding=pikepdf.Name.WinAnsiEncoding),
                ))
                page.obj.Contents = pdf.make_stream(content)
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            title = "Chapter Eleven An Interesting Subject"
            self.assertEqual(report["headings"], [{"page": 1, "level": "H1", "text": title}])
            with pikepdf.open(output) as pdf:
                with pdf.open_outline() as outline:
                    self.assertEqual([item.title for item in outline.root], [title])
                instructions = [item for item in pikepdf.parse_content_stream(pdf.pages[0])
                                if str(item.operator) not in {"BDC", "BMC", "EMC"}]
                original = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, content)))
                self.assertEqual(pikepdf.unparse_content_stream(instructions),
                                 pikepdf.unparse_content_stream(original))

    def test_chapter_label_does_not_absorb_body_or_distant_heading(self):
        for size, bold, y in ((11, False, 683), (14, True, 600)):
            with self.subTest(size=size, bold=bold, y=y):
                lines = [tagger.OcrLine(0, 1, 'Chapter 3', 190, 700, 10, bold=True),
                         tagger.OcrLine(2, 3, 'An unrelated paragraph', 72, y, size, bold=bold)]
                self.assertEqual(len(tagger.merge_chapter_titles(lines, [14, 10])), 2)
                self.assertEqual(lines[0].text, 'Chapter 3')

    def test_truetype_winansi_only_page_is_tagged_without_changing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            original = b"BT /F2 12 Tf 1 0 0 1 72 700 Tm (Investor\x92s caf\xe9) Tj ET"
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                font = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType,
                    BaseFont=pikepdf.Name("/TimesNewRomanPSMT"),
                    Encoding=pikepdf.Name.WinAnsiEncoding,
                ))
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F2=font))
                page.obj.Contents = pdf.make_stream(original)
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            self.assertEqual(report["tagged_pages"], 1)
            self.assertEqual(report["blocks"][0]["text"], "Investor’s café")
            tagger.validate_output(output, 1)
            with pikepdf.open(output) as pdf:
                instructions = [item for item in pikepdf.parse_content_stream(pdf.pages[0])
                                if str(item.operator) not in {"BDC", "BMC", "EMC"}]
                before = list(pikepdf.parse_content_stream(pikepdf.Stream(pdf, original)))
                self.assertEqual(pikepdf.unparse_content_stream(instructions),
                                 pikepdf.unparse_content_stream(before))

    def test_mixed_unicode_and_truetype_fonts_decode_without_missing_map(self):
        with pikepdf.Pdf.new() as pdf:
            page = pdf.add_blank_page()
            unicode_font = pikepdf.Dictionary(
                Subtype=pikepdf.Name.TrueType,
                Encoding=pikepdf.Name.WinAnsiEncoding,
                ToUnicode=pdf.make_stream(b"1 beginbfchar <41> <005A> endbfchar"),
            )
            winansi_font = pikepdf.Dictionary(
                Subtype=pikepdf.Name.TrueType, Encoding=pikepdf.Name.WinAnsiEncoding,
            )
            page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                F1=unicode_font, F2=winansi_font,
            ))
            page.obj.Contents = pdf.make_stream(
                b"BT /F1 12 Tf (A) Tj ET BT /F2 12 Tf (caf\xe9) Tj ET"
            )
            lines = tagger.extract_lines(page.obj, list(pikepdf.parse_content_stream(page)))
            self.assertEqual([line.text for line in lines], ["Z", "café"])

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

    def test_existing_tags_gain_outline_without_rewriting_page(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            content = (b"BT /F1 12 Tf 1 0 0 1 276 600 Tm (Overview) Tj "
                       b"1 0 0 1 72 570 Tm (This is ordinary body prose filling the full measure across the page to its right margin.) Tj ET")
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page(page_size=(612, 792))
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                    F1=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name("/Times-Roman"), Encoding=pikepdf.Name.WinAnsiEncoding)))
                page.obj.Contents = pdf.make_stream(content)
                pdf.Root.StructTreeRoot = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.StructTreeRoot, TestMarker="Keep original tags"))
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            self.assertEqual(report["tagged_pages"], 0)
            self.assertEqual(report["outline_entries"], 1)
            with pikepdf.open(output) as pdf:
                self.assertEqual(pdf.pages[0].Contents.read_bytes(), content)
                self.assertEqual(str(pdf.Root.StructTreeRoot.TestMarker), "Keep original tags")
                with pdf.open_outline() as outline:
                    self.assertEqual(outline.root[0].title, "Overview")
                    self.assertEqual(outline.root[0].destination[0].objgen, pdf.pages[0].obj.objgen)

    def test_mixed_italic_heading_becomes_h3_with_inline_style_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            content = (b"BT /F1 12 Tf /P << /MCID 0 >> BDC "
                       b"1 0 0 1 276 650 Tm (Overview) Tj EMC "
                       b"/P << /MCID 1 >> BDC /F2 12 Tf "
                       b"1 0 0 1 258 600 Tm (The Rule Filing) Tj EMC "
                       b"/Span << /MCID 2 >> BDC /F1 12 Tf ( 1975) Tj EMC "
                       b"/P << /MCID 3 >> BDC 1 0 0 1 72 570 Tm "
                       b"(This is ordinary body prose filling the full measure across the page to its right margin.) Tj EMC ET")
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page(page_size=(612, 792))
                def font(name):
                    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                        BaseFont=pikepdf.Name('/' + name), Encoding=pikepdf.Name.WinAnsiEncoding)
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
                    F1=font("Times-Roman"), F2=font("Times-Italic")))
                page.obj.Contents = pdf.make_stream(content)
                style = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.Span, K=2))
                title = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, Pg=page.obj,
                    K=pikepdf.Array([1, style])))
                pdf.Root.StructTreeRoot = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.StructTreeRoot, K=pikepdf.Array([
                        pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.P, Pg=page.obj, K=0)),
                        title])))
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            self.assertEqual([(h["text"], h["level"]) for h in report["headings"]],
                             [("Overview", "H2"), ("The Rule Filing 1975", "H3")])
            self.assertEqual(report["promoted_heading_tags"], 2)
            with pikepdf.open(output) as pdf:
                self.assertEqual(pdf.pages[0].Contents.read_bytes(), content)
                title = pdf.Root.StructTreeRoot.K[1]
                self.assertEqual(title.S, pikepdf.Name.H3)
                self.assertEqual(title.K[1].S, pikepdf.Name.Span)
                self.assertEqual(title.K[1].K, 2)
                with pdf.open_outline() as outline:
                    self.assertEqual(outline.root[0].children[0].title, "The Rule Filing 1975")

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
