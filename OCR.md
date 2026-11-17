# OCR and tagged PDF workflow

These tools create reproducible searchable, structured, and losslessly
compressed derivatives of scanned PDFs without replacing the source.

## Python environment

Install the Enhancer's isolated Python dependencies once:

```sh
./setup-python-env.sh
```

This creates `.venv/` beside the Enhancer scripts with `pikepdf` and Pillow.
The OCR and compression scripts prefer `.venv/bin/python` for tagging, local AI
review, and metadata helpers. Existing standalone installations without this
environment can still use Python with the required packages, including the
interpreter bundled with OCRmyPDF. An incomplete project venv produces an error
instead of silently using an unrelated global interpreter.

The browser viewer's native-host installer also initializes this environment
when an Enhancer checkout supplies `setup-python-env.sh`. OCRmyPDF and qpdf
remain separate system executables, typically installed using Homebrew.

## Create searchable OCR

Run the OCR, paragraph grouping, table-block classification, and heading
tagging without replacing the source:

```sh
./ocr-scanned-pdf.sh SOURCE.pdf NEW-OCR.pdf
```

To rebuild every page's text, including pages with existing OCR or digital text,
and apply the local AI review in one run:

```sh
./ocr-scanned-pdf.sh SOURCE.pdf NEW-AI-OCR.pdf --force-ocr --ai-review
```

This is the workflow used by the viewer's Enhance button. Force OCR rasterizes
pages and replaces existing text in the new copy; the source remains unchanged.
The AI pass reviews all pages with recognized blocks, applies reviewed text and
tags, and writes a `.review.jsonl` alongside the PDF, text, and tagging report.
Both viewer enhancement modes use this OCR and AI review workflow, including
for PDFs with existing text. Previously enhanced copies without AI review are
not reused from the viewer's enhancement cache.

Optionally add or override descriptive metadata while enhancing:

```sh
./ocr-scanned-pdf.sh SOURCE.pdf NEW-OCR.pdf \
  --title "Example Title" \
  --description "A summary of the document" \
  --tags "finance, investing" \
  --language en-US
```

The original authoring application is preserved as Creator where available;
the person or organization belongs in Author and `dc:creator`. Descriptions
map to PDF Subject and `dc:description`; tags to PDF Keywords,
`pdf:Keywords`, and Dublin Core `dc:subject`. Other optional fields include
`--author`, `--publisher`, `--rights`, and `--identifier`. Missing descriptive
values are not guessed. The compress-and-tag workflow accepts these options too.

The command refuses to overwrite files. It writes a searchable PDF, a text
sidecar, and a JSON report containing every classified block. Body paragraphs
use `P`; document parts/chapters/appendices use `H1`; primary sections use `H2`;
lettered, numbered, lowercase, and parenthesized Roman levels may use `H3` through
`H6`; and table-like regions use `Div` so table labels are not promoted to
headings. A deeper prefix style is enabled only when it recurs on at least two
pages, preventing one-off list items from inventing a document hierarchy.
Recognized headings also become nested PDF bookmarks with page destinations,
so the viewer can show its section navigator for the enhanced copy. The final
bookmarks use the AI-reviewed heading text and levels when AI review is enabled.

## Create a much smaller derivative

To rebuild paragraph copy text in an already tagged PDF, use:

```sh
python3 tag-ocr-pdf.py TAGGED.pdf RETAGGED.pdf --retag
```

This replaces text structure tags and paragraph `/ActualText`, preserves existing
bookmarks and metadata, and leaves drawing instructions intact. Decorative
vector Form objects are skipped when selecting a text layer. Native font runs
are joined before paragraph detection, preserving printed hard hyphens.
Clipboard behavior still depends on the PDF viewer honoring `/ActualText`.

Create a new losslessly optimized derivative, then apply the same structure
tags:

```sh
./compress-and-tag-ocr-pdf.sh EXISTING-OCR.pdf NEW-COMPACT.pdf
```

The source is never edited. A compression report records byte counts and the
tagging report records block decisions. Compression recompresses the existing
predicted Flate streams at zlib level 9 and generates object streams. It does not
convert color, resample images, or use JPEG. Every decoded image is SHA-256
checked before and after optimization, and the command fails if any image
differs. This scan is already losslessly compressed, so the remaining strict
lossless reduction is limited; materially smaller derivatives require lossy
image encoding.

## Set up and run the local AI review

Install the two Ollama models selected for a 48 GB Apple Silicon Mac:

```sh
./setup-ollama-ocr.sh
```

Setup also creates `sec-ocr-review`, a local custom model that embeds the
evidence-preservation rules without copying the underlying 32B weights.

Review all page images and initial blocks. The output is JSONL so an interrupted
long run can resume without losing completed pages:

```sh
./ollama-review-ocr.py \
  INITIAL-TAGGED.pdf \
  INITIAL-TAGGED.tagging.json \
  REVIEW-v1.jsonl
```

Resume an interrupted review with `--resume`. Test a page range first with
`--pages 45-50`.

Apply the review while creating another new compressed version:

```sh
./compress-and-tag-ocr-pdf.sh \
  EXISTING-UNTAGGED-OCR.pdf \
  NEW-AI-REVIEWED-COMPACT.pdf \
  REVIEW-v1.jsonl
```

The AI prompt treats names, initials, corporate names, dates, references,
quantities, currency, percentages, and table values as protected evidence. A
changed protected token is rejected unless the review supplies image-specific
evidence at confidence 0.98 or higher. The scan remains authoritative.

Date-shaped OCR errors are also corrected during tagging: an `I`, `l`, or
`|` in the day of an explicit written month/day/four-digit-year date (for
example `Dec. I, 1994`) is converted to `1` only if the resulting date is
calendar-valid. Standalone Roman numerals, dates without a numeric year, and
other numbers are not changed. Every substitution is recorded with its block
and page in `ocr_date_corrections` in the tagging report. Corrections appear
in the exported text and the derivative PDF's paragraph `/ActualText`;
scanned page images and source files are untouched. Because this narrow
normalization relies on syntax, readers should still check consequential dates
against the scan. No attempt is made to verify or alter release numbers.

Word-internal tildes such as `self~regulatory` are flagged for image review.
A tilde becomes a hyphen only with per-character visual evidence and both
character and block confidence of at least 0.98. Ambiguous marks and legitimate
tildes are retained; there is no global substitution. Rejected changes appear
in the tagging report's `review_warnings`. Older saved reviews that change a
tilde need to be regenerated to supply this evidence. Accepted corrections are
written to the derivative's paragraph `/ActualText`; page drawing instructions
and the source PDF remain unchanged.
