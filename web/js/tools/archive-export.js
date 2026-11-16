import { command, optionalFlag, positiveInteger, requireValue, shellQuote } from "../lib/command.js";

export function archiveBookId(value) {
  let id = value.trim();
  if (id.includes("://")) {
    let url;
    try { url = new URL(id); } catch { throw new Error("Use an archive.org/details book URL."); }
    const parts = url.pathname.split("/");
    if (!["http:", "https:"].includes(url.protocol) ||
        !["archive.org", "www.archive.org"].includes(url.host) ||
        url.username || url.password || parts[1] !== "details" || !parts[2]) {
      throw new Error("Use an archive.org/details book URL.");
    }
    id = parts[2];
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(id)) {
    throw new Error("Use an Internet Archive item ID or details URL.");
  }
  return id;
}

export const archiveExport = {
  id: "archive-export",
  step: "01",
  accent: "blue",
  eyebrow: "Reader capture",
  title: "Internet Archive export",
  summary: "Assemble accessible reader pages into a PDF. Sign in or borrow in the opened browser if required.",
  fields: [
    { name: "book", label: "Book URL or item ID", required: true, wide: true,
      placeholder: "https://archive.org/details/transformationof00seli/mode/2up" },
    { name: "outputDir", label: "Capture directory", placeholder: "transformationof00seli-archive-pages" },
    { name: "outputPdf", label: "Output PDF", placeholder: "transformationof00seli.pdf" },
    { name: "start", label: "First scan", type: "number", min: 1, placeholder: "1", help: "Scan positions include covers and front matter." },
    { name: "end", label: "Last scan", type: "number", min: 1, placeholder: "End of book" },
  ],
  build(values) {
    const id = archiveBookId(requireValue(values, "book", "Book URL or item ID"));
    const start = positiveInteger(values.start, "First scan");
    const end = positiveInteger(values.end, "Last scan");
    if (end != null && end < (start ?? 1)) throw new Error("Last scan must be at least the first scan.");
    const parts = ["python3", "export-archive-book.py", shellQuote(id)];
    optionalFlag(parts, "--output-dir", values.outputDir);
    optionalFlag(parts, "--output-pdf", values.outputPdf);
    if (start != null) parts.push("--start", String(start));
    if (end != null) parts.push("--end", String(end));
    return command(parts);
  },
};
