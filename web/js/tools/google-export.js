import { command, optionalFlag, positiveInteger, requireValue, shellQuote } from "../lib/command.js";

export const googleExport = {
  id: "google-export",
  step: "01",
  accent: "blue",
  eyebrow: "Reader capture",
  title: "Google Books export",
  summary: "Capture accessible reader pages and assemble them into one PDF.",
  fields: [
    { name: "bookId", label: "Book ID", required: true, placeholder: "ctmJB8IRXQcC", wide: true },
    { name: "outputDir", label: "Capture directory", placeholder: "book-pages" },
    { name: "outputPdf", label: "Output PDF", placeholder: "book.pdf" },
    { name: "start", label: "Start number", type: "number", min: 1, placeholder: "1" },
    { name: "readerPage", label: "Reader page", type: "number", min: 1, placeholder: "1" },
    { name: "maxPages", label: "Last capture number", type: "number", min: 1, placeholder: "No limit" },
    { name: "workers", label: "Write workers", type: "number", min: 1, placeholder: "4" },
  ],
  build(values) {
    const bookId = requireValue(values, "bookId", "Book ID");
    if (!/^[A-Za-z0-9_-]+$/.test(bookId)) {
      throw new Error("Book ID may contain only letters, numbers, underscores, or hyphens.");
    }
    const parts = ["python3", "export-google-book.py", shellQuote(bookId)];
    optionalFlag(parts, "--output-dir", values.outputDir);
    optionalFlag(parts, "--output-pdf", values.outputPdf);
    for (const [name, flag, label] of [
      ["start", "--start", "Start number"],
      ["readerPage", "--reader-page", "Reader page"],
      ["maxPages", "--max-pages", "Last capture number"],
      ["workers", "--workers", "Write workers"],
    ]) {
      const value = positiveInteger(values[name], label);
      if (value != null) parts.push(flag, String(value));
    }
    return command(parts);
  },
};
