import test from "node:test";
import assert from "node:assert/strict";

import { aiReview } from "../web/js/tools/ai-review.js";
import { compression } from "../web/js/tools/compression.js";
import { googleExport } from "../web/js/tools/google-export.js";
import { archiveExport } from "../web/js/tools/archive-export.js";
import { ocr } from "../web/js/tools/ocr.js";

test("Google export builds isolated output arguments", () => {
  assert.equal(
    googleExport.build({ bookId: "abc_123", outputPdf: "My Book.pdf", workers: "2" }),
    "python3 export-google-book.py abc_123 --output-pdf 'My Book.pdf' --workers 2",
  );
});

test("Google export rejects path-like IDs", () => {
  assert.throws(() => googleExport.build({ bookId: "../book" }), /Book ID may contain/);
});

test("Internet Archive accepts the linked two-page URL and scan range", () => {
  assert.equal(archiveExport.build({
    book: "https://archive.org/details/transformationof00seli/mode/2up",
    start: "2", end: "10", outputPdf: "Wall Street.pdf",
  }), "python3 export-archive-book.py transformationof00seli --output-pdf 'Wall Street.pdf' --start 2 --end 10");
});

test("Internet Archive rejects unsafe sources and reversed ranges", () => {
  for (const book of ["../book", "--help", "https://example.org/details/book", "https://archive.org.evil.test/details/book", "https://user@archive.org/details/book"]) {
    assert.throws(() => archiveExport.build({ book }));
  }
  assert.throws(() => archiveExport.build({ book: "book", start: "10", end: "2" }), /Last scan/);
});

test("OCR preserves paths with spaces", () => {
  assert.equal(
    ocr.build({ input: "Source Scan.pdf", output: "OCR Copy.pdf" }),
    "./ocr-scanned-pdf.sh 'Source Scan.pdf' 'OCR Copy.pdf'",
  );
});

test("OCR forwards optional descriptive metadata and tags", () => {
  assert.equal(
    ocr.build({ input: "scan.pdf", title: "Nine to Noon", description: "An investing guide", keywords: "stocks, ETFs" }),
    "./ocr-scanned-pdf.sh scan.pdf --title 'Nine to Noon' --description 'An investing guide' --keywords 'stocks, ETFs'",
  );
});

test("AI review exposes resumable page selection", () => {
  assert.equal(
    aiReview.build({
      pdf: "ocr.pdf",
      report: "ocr.tagging.json",
      output: "review.jsonl",
      pages: "1-10,14",
      resume: true,
      skipExtractor: false,
    }),
    "python3 ollama-review-ocr.py ocr.pdf ocr.tagging.json review.jsonl --pages 1-10,14 --resume",
  );
});

test("Compression modes remain independent", () => {
  assert.equal(
    compression.build({ mode: "compression-only", input: "in.pdf", output: "out.pdf", report: "report.json" }),
    "python3 compress-pdf-images.py in.pdf out.pdf --report report.json",
  );
  assert.equal(
    compression.build({ mode: "tagged", input: "in.pdf", output: "out.pdf", review: "review.jsonl" }),
    "./compress-and-tag-ocr-pdf.sh in.pdf out.pdf review.jsonl",
  );
  assert.equal(
    compression.build({ mode: "tagged", input: "in.pdf", output: "out.pdf", description: "Summary", keywords: "finance, books" }),
    "./compress-and-tag-ocr-pdf.sh in.pdf out.pdf --description Summary --tags 'finance, books'",
  );
});
