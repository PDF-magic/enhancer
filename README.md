# PDF Magic Exporter

Tools for rendering accessible books as PDFs and creating searchable,
structured derivatives of scanned PDFs.

## Web toolbench

The dependency-free [web interface](web/index.html) keeps Google Books export,
OCR, local AI review, and compression in separate reusable modules. Each panel
validates its own inputs and builds the corresponding local CLI command.

```sh
npm run serve
```

Open `http://localhost:4173` in a browser. Run the frontend command-builder
tests with `npm test`.

## Google Play Books export

`export-google-book.py` captures the visible pages available through a Google
Play Books reader session and combines them into a PDF. It uses a persistent
browser profile so the user can sign in and retain reader access.

Install the Python dependencies and Playwright browser:

```sh
python3 -m pip install img2pdf playwright
python3 -m playwright install chromium
```

Export a volume by its Google Books ID:

```sh
python3 export-google-book.py BOOK_ID
```

By default, captures are written to `BOOK_ID-pages/` and the combined document
to `BOOK_ID.pdf`. The exporter resumes from existing captures, advances until
the reader stops, and removes the final reader-completion capture before PDF
assembly.

## OCR, compression, and structure tagging

The lossless PDF workflow can create searchable OCR, identify heading levels,
apply PDF structure tags, recompress compatible image streams without changing
decoded pixels, and optionally incorporate a local Ollama review. See the
[OCR workflow](OCR.md) for requirements and commands.

Enhanced PDFs record Creator and Author from recognizable signatures or large
letterhead names, then existing author metadata, then the local user's full name
or username. Producer is `PDF Magic Enhancer`. Both document-info and XMP fields
are updated. Name detection uses the searchable text and is heuristic.

Pass `--source-url URL` to `ocr-scanned-pdf.sh` to store the original document
URL in the XMP `pdfmagic:href` property. Existing original URLs are retained
when processing an enhanced local copy again.

## SEC concept-release comment references

`download-sec-comments.py` downloads the PDF and raw HTML submissions listed
for an SEC file number. Public-comment PDFs, raw HTML comments, and meeting
memoranda are stored in separate directories.

Set an identifying SEC User-Agent locally and provide the file number:

```sh
export SEC_USER_AGENT='Your Name your@email.com'
python3 download-sec-comments.py S7-27-15
```

If `SEC_USER_AGENT` is unset, the tool prompts for it in an interactive shell.
Use `--dry-run` to inspect the files and classifications without downloading.

DO NOT USE FOR PRORESED RULE, CP ONLY

This tool is intended only to refresh local reference copies.
