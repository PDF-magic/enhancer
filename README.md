# PDF Magic Exporter

Tools for rendering accessible books as PDFs and creating searchable,
structured derivatives of scanned PDFs.

## Web toolbench

The dependency-free [web interface](web/index.html) keeps Google Books export,
OCR, local AI review, and compression in separate reusable modules. Each panel
validates its own inputs and builds the corresponding local CLI command.

**Choose input files by clicking the drop zone or dragging them into it.**
Supported inputs include PDF scans, JSON tagging reports, and JSONL AI reviews.
The localhost Python web server streams an unmodified copy into
`.pdfmagic-uploads/` and substitutes the staged path into the built command.
No manual file path is needed for inputs. You can delete this ignored staging
directory when the imports are no longer needed.

The upload endpoint is bound to `127.0.0.1` and rejects cross-origin requests.
The selected documents stay on your Mac. A static deployment (for example,
Netlify) can display the toolbench but cannot provide this local upload
endpoint. The interface still builds commands for Terminal; it does not
execute PDF processing or Google Books captures itself.

```sh
npm run serve
```

Open `http://localhost:4173` in a browser. On macOS, **double-click
[`Launch PDF Magic.command`](Launch%20PDF%20Magic.command)** in Finder to start
the local server and automatically open the site in your default browser.
Alternatively, run `npm run open` from the repository root. If the upload-capable server is already listening on port 4173, the
launcher opens that instance instead of starting another. If an older static
server occupies the port, stop it with Control-C before relaunching. The launcher binds its new server to localhost;
leave its Terminal window open to keep the server running.

The interface follows the system appearance by default. Use the **Dark mode /
Light mode** button in the top-right corner to override it; the preference is
saved in your browser. The interface still only **builds commands**. It does not
run Google Books exports or PDF enhancement jobs itself.

Run the frontend tests with `npm test`.

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

Enhanced PDFs record **Author** (`/Author`, `dc:creator`) from recognizable
signatures or large letterhead names, then existing author metadata, then the
local user's full name or username. **Creator** (`/Creator`, `xmp:CreatorTool`) retains the original PDF Info and
XMP values separately, even if they disagree or resemble an author name.
**Producer** (`/Producer`, `pdf:Producer`) preserves the original value for
each metadata format and appends `; PDF Magic Enhancer` once; if missing,
it is `PDF Magic Enhancer`. Name detection uses the searchable text and
is heuristic.

The OCR and compress-and-tag enhancement workflows accept optional `--title`,
`--author`, `--description`, `--keywords` (alias `--tags`), `--publisher`,
`--rights`, `--language`, and `--identifier` values. Unspecified descriptive
fields retain source metadata rather than being invented. PDF Info Title,
Subject, and Keywords map to XMP `dc:title`, `dc:description`, and
`dc:subject`/`pdf:Keywords`, respectively. Publisher, rights, language, and
identifier are mapped into Dublin Core XMP, and language also sets PDF `/Lang`.
The web toolbench offers editable metadata fields.

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
