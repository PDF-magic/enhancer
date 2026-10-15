import { command, optionalFlag, requireValue, shellQuote } from "../lib/command.js";

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
    { name: "title", label: "Title", placeholder: "Preserve original if blank" },
    { name: "author", label: "Author", placeholder: "Preserve or detect if blank" },
    { name: "description", label: "Description", placeholder: "Optional subject or summary", wide: true },
    { name: "keywords", label: "Tags / keywords", placeholder: "Comma-separated tags", wide: true },
    { name: "publisher", label: "Publisher", placeholder: "Optional" },
    { name: "rights", label: "Rights / license", placeholder: "Optional" },
    { name: "language", label: "Language", placeholder: "en-US" },
    { name: "identifier", label: "Identifier", placeholder: "ISBN, DOI, etc." },
  ],
  build(values) {
    const input = requireValue(values, "input", "Source PDF");
    const parts = ["./ocr-scanned-pdf.sh", shellQuote(input), shellQuote(values.output)];
    for (const field of ["title", "author", "description", "keywords", "publisher", "rights", "language", "identifier"]) {
      optionalFlag(parts, `--${field}`, values[field]);
    }
    return command(parts);
  },
};
