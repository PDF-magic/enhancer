# PDF Magic Exporter

Tools for rendering accessible books as PDFs and creating searchable,
structured derivatives of scanned PDFs.

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

## Provenance

This toolset was migrated from the following immutable source snapshot after
its development branch was squash-merged:

https://github.com/blocktransfer/SEC-publications/tree/45ef3a6bb1fc0f3269c8fb80579be4d7f67bacc2/tools
