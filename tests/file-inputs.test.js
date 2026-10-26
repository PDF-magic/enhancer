import test from "node:test";
import assert from "node:assert/strict";
import { ocr } from "../web/js/tools/ocr.js";
import { aiReview } from "../web/js/tools/ai-review.js";
import { compression } from "../web/js/tools/compression.js";

test("source file fields are genuine file-picker targets", () => {
  for (const [tool, name, accept] of [
    [ocr, "input", ".pdf"],
    [aiReview, "pdf", ".pdf"],
    [aiReview, "report", ".json"],
    [compression, "input", ".pdf"],
    [compression, "review", ".jsonl"],
  ]) {
    assert.deepEqual(
      { type: tool.fields.find(f => f.name === name)?.type,
        accept: tool.fields.find(f => f.name === name)?.accept },
      { type: "file", accept },
    );
  }
});

test("command builders accept staged paths with spaces and keep output editable", () => {
  assert.equal(
    ocr.build({ input: "/tmp/PDF Magic/scan.pdf", output: "enhanced.pdf" }),
    "./ocr-scanned-pdf.sh '/tmp/PDF Magic/scan.pdf' enhanced.pdf",
  );
  assert.equal(
    aiReview.build({ pdf: "/tmp/book.pdf", report: "/tmp/blocks.json", output: "review.jsonl" }),
    "python3 ollama-review-ocr.py /tmp/book.pdf /tmp/blocks.json review.jsonl",
  );
  assert.equal(
    compression.build({ input: "/tmp/book.pdf", output: "compressed.pdf", review: "/tmp/notes.jsonl", mode: "tagged" }),
    "./compress-and-tag-ocr-pdf.sh /tmp/book.pdf compressed.pdf /tmp/notes.jsonl",
  );
});
