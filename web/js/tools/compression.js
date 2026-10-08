import { command, optionalFlag, requireValue, shellQuote } from "../lib/command.js";

export const compression = {
  id: "compression",
  step: "04",
  accent: "amber",
  eyebrow: "Lossless optimization",
  title: "PDF compression",
  summary: "Recompress compatible image streams, with optional structure tagging and review application.",
  fields: [
    {
      name: "mode",
      label: "Workflow",
      type: "select",
      value: "tagged",
      wide: true,
      options: [
        { value: "tagged", label: "Compress and tag" },
        { value: "compression-only", label: "Compression only" },
      ],
    },
    { name: "input", label: "Input PDF", required: true, placeholder: "scan-ocr.pdf" },
    { name: "output", label: "Output PDF", required: true, placeholder: "scan-compact.pdf" },
    { name: "review", label: "AI review JSONL", placeholder: "review-v1.jsonl", help: "Used by the compress-and-tag workflow." },
    { name: "report", label: "Compression report", placeholder: "compression.json", help: "Used by compression-only mode." },
  ],
  build(values) {
    const input = shellQuote(requireValue(values, "input", "Input PDF"));
    const output = shellQuote(requireValue(values, "output", "Output PDF"));
    if (values.mode === "compression-only") {
      const parts = ["python3", "compress-pdf-images.py", input, output];
      optionalFlag(parts, "--report", values.report);
      return command(parts);
    }
    return command(["./compress-and-tag-ocr-pdf.sh", input, output, shellQuote(values.review)]);
  },
};
