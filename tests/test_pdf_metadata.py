import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pikepdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_metadata as metadata

spec = importlib.util.spec_from_file_location("metadata_tagger", Path(__file__).resolve().parents[1] / "tag-ocr-pdf.py")
tagger = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tagger
spec.loader.exec_module(tagger)


def line(text, y, page=1, size=12, x=72):
    return {"page": page, "text": text, "x": x, "y": y, "size": size, "height": 792}


class MetadataTests(unittest.TestCase):
    def test_labeled_keyword_date_moves_to_document_date(self):
        with pikepdf.Pdf.new() as pdf:
            pdf.add_blank_page()
            pdf.docinfo.Keywords = "transfer agents; Date: 2009-08-04"
            pdf.docinfo.CreationDate = "D:20090908180705"
            with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                xmp["pdf:Keywords"] = "Date: 2009-08-04"
                xmp["dc:subject"] = ["Date: 2009-08-04", "securities"]
            metadata.apply_metadata(pdf, "Document Author")
            self.assertEqual(str(pdf.docinfo.Keywords), "transfer agents, securities")
            self.assertEqual(str(pdf.docinfo.CreationDate), "D:20090908180705")
            with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                self.assertEqual(xmp["pdf:Keywords"], "transfer agents, securities")
                self.assertEqual(set(xmp["dc:subject"]), {"transfer agents", "securities"})
                self.assertEqual(xmp["dc:date"], ["2009-08-04"])

    def test_ordinary_and_invalid_date_keywords_are_preserved(self):
        for value in ("Date: 2009-02-30", "2009-08-04", "securities, transfer agents"):
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo.Keywords = value
                metadata.apply_metadata(pdf, "Document Author")
                self.assertEqual(str(pdf.docinfo.Keywords), value)
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    self.assertNotIn("dc:date", xmp)

    def test_date_normalization_retains_source_metadata_and_previous_dates(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "output.pdf"
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo.Author = "Original Author"
                pdf.docinfo.Creator = "Original Creator"
                pdf.docinfo.Producer = "Original Producer"
                pdf.docinfo.CreationDate = "D:20090908180705"
                pdf.docinfo.Keywords = "securities; Date: 2009-08-04"
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    xmp["dc:date"] = ["2001-01-01"]
                    xmp["dc:subject"] = ["existing keyword"]
                pdf.save(source)
            tagger.tag_pdf(source, output, Path(folder) / "report.json",
                           metadata_overrides={"keywords": "Date: 2009-08-04, updated"})
            with pikepdf.open(output) as pdf:
                self.assertEqual(str(pdf.docinfo.Creator), "Original Creator")
                self.assertEqual(str(pdf.docinfo.Producer), "Original Producer; PDF Magic Enhancer")
                self.assertEqual(str(pdf.docinfo.CreationDate), "D:20090908180705")
                self.assertEqual(str(pdf.docinfo.Keywords), "updated")
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    self.assertEqual(xmp["dc:date"], ["2001-01-01", "2009-08-04"])
                    self.assertEqual(set(xmp["dc:subject"]), {"updated"})
                    self.assertEqual(xmp["pdf:Keywords"], "updated")

    def test_signature_beats_recipient_and_letterhead(self):
        records = [line("Dr. Recipient Name", 700, size=28), line("Dear Dr. Recipient Name:", 600),
                   line("Body paragraph here.", 550), line("Respectfully submitted,", 200),
                   line("Jane", 160), line("Q. Example", 160, x=105)]
        self.assertEqual(metadata.visible_creator(records, 1), ("Jane Q. Example", "signature"))

    def test_signed_marker_and_unicode_names(self):
        records = [line("/s/ José García", 200, page=3)]
        self.assertEqual(metadata.visible_creator(records, 3), ("José García", "signature"))

    def test_letterhead_person_or_company(self):
        for name in ("Jane Example", "Example Partners LLC"):
            records = [line(name, 730, size=28), line("Ordinary body text.", 550)]
            self.assertEqual(metadata.visible_creator(records, 1), (name, "letterhead"))

    def test_recipient_and_report_title_are_not_creators(self):
        records = [line("Jane Recipient", 740, size=28), line("Annual Report", 710, size=28),
                   line("Dear Jane Recipient:", 600), line("Ordinary body text.", 550)]
        self.assertEqual(metadata.visible_creator(records, 1), (None, None))

    def test_old_quoted_signatures_outside_document_end_are_ignored(self):
        records = [line("Sincerely,", 200), line("Quoted Person", 180)]
        self.assertEqual(metadata.visible_creator(records, 20), (None, None))

    def test_reenhancement_keeps_original_web_href(self):
        self.assertEqual(metadata.choose_source_url("https://example.com/original.pdf", "file:///tmp/enhanced.pdf"), "https://example.com/original.pdf")
        self.assertEqual(metadata.choose_source_url("", "https://example.com/original.pdf"), "https://example.com/original.pdf")
        with self.assertRaisesRegex(ValueError, "source URL"):
            metadata.choose_source_url("", "javascript:alert(1)")

    def test_local_full_name_and_username_fallback(self):
        with patch.object(metadata.getpass, "getuser", return_value="example"), \
             patch.object(metadata.os, "name", "nt"), \
             patch.object(metadata, "windows_full_name", return_value="Example Person"):
            self.assertEqual(metadata.local_user_name(), "Example Person")
        with patch.object(metadata.getpass, "getuser", return_value="example"), \
             patch.object(metadata.os, "name", "nt"), \
             patch.object(metadata, "windows_full_name", return_value=""):
            self.assertEqual(metadata.local_user_name(), "example")

    def test_pdf_info_and_xmp_match_and_href_survives_tagging(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            href = "https://example.com/original.pdf?x=1&y=2"
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                metadata.apply_metadata(pdf, "Document Author", href)
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            self.assertEqual(report["creator"], "Document Author")
            self.assertEqual(report["creator_source"], "document metadata")
            self.assertEqual(report["href"], href)
            with pikepdf.open(output) as pdf:
                self.assertNotIn("/Creator", pdf.docinfo)
                self.assertEqual(str(pdf.docinfo.Author), "Document Author")
                self.assertEqual(str(pdf.docinfo.Producer), "PDF Magic Enhancer")
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    self.assertEqual(xmp["dc:creator"], ["Document Author"])
                    self.assertNotIn("xmp:CreatorTool", xmp)
                    self.assertEqual(xmp["pdf:Producer"], "PDF Magic Enhancer")
                    self.assertEqual(xmp["pdfmagic:href"], href)

    def test_preserve_original_software_and_map_dublin_core(self):
        with tempfile.TemporaryDirectory() as folder:
            source, processed, output = (Path(folder) / name for name in ("original.pdf", "ocr.pdf", "enhanced.pdf"))
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo["/Author"] = "Jane Author"
                pdf.docinfo["/Creator"] = "Google Docs"
                pdf.docinfo["/Title"] = "Original Title"
                pdf.docinfo["/Subject"] = "Original summary"
                pdf.docinfo["/Keywords"] = "finance, investing"
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    xmp["dc:publisher"] = ["Small Publisher"]
                    xmp["dc:rights"] = "CC BY-SA 4.0"
                    xmp["dc:identifier"] = "ISBN 123"
                    xmp["dc:language"] = ["en-US"]
                pdf.save(source)
            with pikepdf.open(source) as pdf:
                pdf.docinfo["/Creator"] = "OCRmyPDF 17 / Tesseract"
                pdf.docinfo["/Author"] = ""
                pdf.docinfo["/Title"] = "Untitled OCR"
                pdf.save(processed)
            tagger.tag_pdf(processed, output, Path(folder) / "report.json", reported_input=source)
            with pikepdf.open(output) as pdf:
                self.assertEqual(str(pdf.docinfo["/Creator"]), "Google Docs")
                self.assertEqual(str(pdf.docinfo["/Author"]), "Jane Author")
                self.assertEqual(str(pdf.docinfo["/Producer"]), "PDF Magic Enhancer")
                self.assertEqual(str(pdf.docinfo["/Title"]), "Original Title")
                self.assertEqual(str(pdf.docinfo["/Subject"]), "Original summary")
                self.assertEqual(str(pdf.docinfo["/Keywords"]), "finance, investing")
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    self.assertEqual(xmp["xmp:CreatorTool"], "Google Docs")
                    self.assertEqual(xmp["dc:creator"], ["Jane Author"])
                    self.assertEqual(xmp["dc:title"], "Original Title")
                    self.assertEqual(xmp["dc:description"], "Original summary")
                    self.assertEqual(xmp["dc:subject"], {"finance", "investing"})
                    self.assertEqual(xmp["dc:publisher"], {"Small Publisher"})
                    self.assertEqual(xmp["dc:rights"], "CC BY-SA 4.0")
                    self.assertEqual(xmp["dc:identifier"], "ISBN 123")
                    self.assertEqual(xmp["dc:language"], {"en-US"})

    def test_sec_petition_creator_and_producer_survive_ocr_intermediary(self):
        """Preserve eCopy and Acrobat provenance from the SEC petition PDF."""
        with tempfile.TemporaryDirectory() as folder:
            source, ocr, output = (Path(folder) / name for name in ("sta.pdf", "ocr.pdf", "enhanced.pdf"))
            original_producer = "Adobe Acrobat 8.16 Paper Capture Plug-in"
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo["/Author"] = "Dechert LLP"
                pdf.docinfo["/Creator"] = "eCopy, Inc"
                pdf.docinfo["/Producer"] = original_producer
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    xmp["xmp:CreatorTool"] = "eCopy, Inc"
                    xmp["pdf:Producer"] = original_producer
                pdf.save(source)
            with pikepdf.open(source) as pdf:
                pdf.docinfo["/Creator"] = "OCRmyPDF 17 / Tesseract"
                pdf.docinfo["/Producer"] = "OCRmyPDF"
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    xmp["xmp:CreatorTool"] = "OCRmyPDF"
                    xmp["pdf:Producer"] = "OCRmyPDF"
                pdf.save(ocr)

            tagger.tag_pdf(ocr, output, Path(folder) / "report.json", reported_input=source)
            with pikepdf.open(output) as pdf:
                self.assertEqual(str(pdf.docinfo["/Creator"]), "eCopy, Inc")
                self.assertEqual(str(pdf.docinfo["/Producer"]), original_producer + "; PDF Magic Enhancer")
                self.assertEqual(str(pdf.docinfo["/Author"]), "Dechert LLP")
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    self.assertEqual(xmp["xmp:CreatorTool"], "eCopy, Inc")
                    self.assertEqual(xmp["pdf:Producer"], original_producer + "; PDF Magic Enhancer")

    def test_distinct_info_and_xmp_provenance_and_reenhancement(self):
        """Preserve original Info/XMP disagreements and never double-append."""
        with tempfile.TemporaryDirectory() as folder:
            source, first, second = (Path(folder) / name for name in ("source.pdf", "first.pdf", "second.pdf"))
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo["/Creator"] = "Info Creator"
                pdf.docinfo["/Producer"] = "Info Producer"
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    xmp["xmp:CreatorTool"] = "XMP Creator"
                    xmp["pdf:Producer"] = "XMP Producer"
                pdf.save(source)
            tagger.tag_pdf(source, first, Path(folder) / "first.json")
            tagger.tag_pdf(first, second, Path(folder) / "second.json")
            for path in (first, second):
                with pikepdf.open(path) as pdf:
                    self.assertEqual(str(pdf.docinfo["/Creator"]), "Info Creator")
                    self.assertEqual(str(pdf.docinfo["/Producer"]), "Info Producer; PDF Magic Enhancer")
                    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                        self.assertEqual(xmp["xmp:CreatorTool"], "XMP Creator")
                        self.assertEqual(xmp["pdf:Producer"], "XMP Producer; PDF Magic Enhancer")

    def test_optional_metadata_and_keyword_tags_preserve_existing_creator(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo["/Author"] = "Real Author"
                pdf.docinfo["/Creator"] = "Real Author"  # Even a legacy creator value is original metadata.
                pdf.save(source)
            tagger.tag_pdf(source, output, Path(folder) / "report.json", metadata_overrides={
                "title": "New Title",
                "author": "Chosen Author",
                "description": "Reader-facing summary",
                "keywords": "Markets, stocks, markets, ETFs",
                "publisher": "Example Publisher",
                "rights": "CC BY-SA 4.0",
                "language": "en-US",
                "identifier": "978-1-2345",
            })
            with pikepdf.open(output) as pdf:
                self.assertEqual(str(pdf.docinfo["/Creator"]), "Real Author")
                self.assertEqual(str(pdf.docinfo["/Author"]), "Chosen Author")
                self.assertEqual(str(pdf.docinfo["/Title"]), "New Title")
                self.assertEqual(str(pdf.docinfo["/Subject"]), "Reader-facing summary")
                self.assertEqual(str(pdf.docinfo["/Keywords"]), "Markets, stocks, ETFs")
                self.assertEqual(str(pdf.Root["/Lang"]), "en-US")
                with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
                    self.assertEqual(xmp["xmp:CreatorTool"], "Real Author")
                    self.assertEqual(xmp["dc:creator"], ["Chosen Author"])
                    self.assertEqual(xmp["dc:subject"], {"Markets", "stocks", "ETFs"})
                    self.assertEqual(xmp["pdf:Keywords"], "Markets, stocks, ETFs")
                    self.assertEqual(xmp["dc:publisher"], {"Example Publisher"})
                    self.assertEqual(xmp["dc:rights"], "CC BY-SA 4.0")
                    self.assertEqual(xmp["dc:language"], ["en-US"])
                    self.assertEqual(xmp["dc:identifier"], "978-1-2345")

    def test_original_author_metadata_survives_ocr_creator_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            original, ocr, output = (Path(folder) / name for name in ("original.pdf", "ocr.pdf", "output.pdf"))
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo.Author = "Original Author"
                pdf.save(original)
                pdf.docinfo.Author = ""
                pdf.docinfo.Creator = "OCRmyPDF 17 / Tesseract"
                pdf.save(ocr)
            report = tagger.tag_pdf(ocr, output, Path(folder) / "report.json", reported_input=original)
            self.assertEqual(report["creator"], "Original Author")

    def test_no_author_uses_local_name_and_explicit_href(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("source.pdf", "output.pdf"))
            with pikepdf.Pdf.new() as pdf:
                pdf.add_blank_page()
                pdf.docinfo.Creator = "OCRmyPDF 17 / Tesseract"
                pdf.save(source)
            with patch.object(tagger, "local_user_name", return_value="Local User"):
                report = tagger.tag_pdf(source, output, Path(folder) / "report.json", source_url="https://example.com/input.pdf")
            self.assertEqual(report["creator"], "Local User")
            self.assertEqual(report["creator_source"], "local user")
            self.assertEqual(metadata.source_details(output)["href"], "https://example.com/input.pdf")

    def test_tagged_letter_records_its_signature_name(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = (Path(folder) / name for name in ("letter.pdf", "output.pdf"))
            with pikepdf.Pdf.new() as pdf:
                page = pdf.add_blank_page()
                font = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica, Encoding=pikepdf.Name.WinAnsiEncoding,
                ))
                page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
                page.obj.Contents = pdf.make_stream(
                    b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Dear Recipient Name,) Tj ET "
                    b"BT /F1 12 Tf 1 0 0 1 72 500 Tm (This is my letter.) Tj ET "
                    b"BT /F1 12 Tf 1 0 0 1 72 200 Tm (Sincerely,) Tj ET "
                    b"BT /F1 12 Tf 1 0 0 1 72 170 Tm (Jane Documentauthor) Tj ET"
                )
                pdf.docinfo.Author = "Recipient Name"
                pdf.save(source)
            report = tagger.tag_pdf(source, output, Path(folder) / "report.json")
            self.assertEqual(report["creator"], "Jane Documentauthor")
            self.assertEqual(report["creator_source"], "signature")
            with pikepdf.open(output) as pdf:
                self.assertNotIn("/Creator", pdf.docinfo)
                self.assertEqual(str(pdf.docinfo.Producer), "PDF Magic Enhancer")


if __name__ == "__main__":
    unittest.main()
