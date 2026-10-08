import test from "node:test";
import assert from "node:assert/strict";

import { aiReview } from "../web/js/tools/ai-review.js";
import { compression } from "../web/js/tools/compression.js";
import { googleExport } from "../web/js/tools/google-export.js";
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

test("OCR preserves paths with spaces", () => {
  assert.equal(
    ocr.build({ input: "Source Scan.pdf", output: "OCR Copy.pdf" }),
    "./ocr-scanned-pdf.sh 'Source Scan.pdf' 'OCR Copy.pdf'",
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
});
