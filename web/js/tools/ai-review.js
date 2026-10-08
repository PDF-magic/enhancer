import { command, optionalFlag, positiveInteger, requireValue, shellQuote } from "../lib/command.js";

export const aiReview = {
  id: "ai-review",
  step: "03",
  accent: "plum",
  eyebrow: "Local vision review",
  title: "AI OCR review",
  summary: "Review OCR blocks against page images with local Ollama models.",
  fields: [
    { name: "pdf", label: "OCR PDF", required: true, placeholder: "scan-ocr.pdf" },
    { name: "report", label: "Tagging report", required: true, placeholder: "scan-ocr.tagging.json" },
    { name: "output", label: "Review output", required: true, placeholder: "review-v1.jsonl", wide: true },
    { name: "pages", label: "Pages", placeholder: "1-10,14,20", help: "Leave blank to review every page." },
    { name: "dpi", label: "Render DPI", type: "number", min: 1, placeholder: "180" },
    { name: "resume", label: "Resume existing review", type: "checkbox" },
    { name: "skipExtractor", label: "Skip extractor model", type: "checkbox" },
  ],
  build(values) {
    const parts = [
      "python3",
      "ollama-review-ocr.py",
      shellQuote(requireValue(values, "pdf", "OCR PDF")),
      shellQuote(requireValue(values, "report", "Tagging report")),
      shellQuote(requireValue(values, "output", "Review output")),
    ];
    optionalFlag(parts, "--pages", values.pages);
    const dpi = positiveInteger(values.dpi, "Render DPI");
    if (dpi != null) parts.push("--dpi", String(dpi));
    if (values.resume) parts.push("--resume");
    if (values.skipExtractor) parts.push("--skip-extractor");
    return command(parts);
  },
};
