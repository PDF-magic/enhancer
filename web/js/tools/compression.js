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
    { name: "input", label: "Input PDF", type: "file", accept: ".pdf", required: true },
    { name: "output", label: "Output PDF", required: true, placeholder: "scan-compact.pdf" },
    { name: "review", label: "AI review JSONL", type: "file", accept: ".jsonl", help: "Optional input for the compress-and-tag workflow." },
    { name: "report", label: "Compression report", placeholder: "compression.json", help: "Used by compression-only mode." },
    { name: "description", label: "Description", placeholder: "Optional; for tagged PDF" },
    { name: "keywords", label: "Tags / keywords", placeholder: "Comma-separated; for tagged PDF" },
  ],
  build(values) {
    const input = shellQuote(requireValue(values, "input", "Input PDF"));
    const output = shellQuote(requireValue(values, "output", "Output PDF"));
    if (values.mode === "compression-only") {
      const parts = ["python3", "compress-pdf-images.py", input, output];
      optionalFlag(parts, "--report", values.report);
      return command(parts);
    }
    const parts = ["./compress-and-tag-ocr-pdf.sh", input, output, shellQuote(values.review)];
    optionalFlag(parts, "--description", values.description);
    optionalFlag(parts, "--tags", values.keywords);
    return command(parts);
  },
};
