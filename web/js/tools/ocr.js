import { command, requireValue, shellQuote } from "../lib/command.js";

export const ocr = {
  id: "ocr",
  step: "02",
  accent: "green",
  eyebrow: "Text recognition",
  title: "Searchable OCR",
  summary: "Create a searchable, structured derivative while preserving the source scan.",
  fields: [
    { name: "input", label: "Source PDF", required: true, placeholder: "scan.pdf", wide: true },
    { name: "output", label: "Output PDF", placeholder: "scan-enhanced-ocr.pdf", wide: true, help: "Optional; the CLI derives a sibling filename when omitted." },
  ],
  build(values) {
    const input = requireValue(values, "input", "Source PDF");
    return command(["./ocr-scanned-pdf.sh", shellQuote(input), shellQuote(values.output)]);
  },
};
