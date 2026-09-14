# OCR and tagged PDF workflow

These tools create reproducible searchable, structured, and losslessly
compressed derivatives of scanned PDFs without replacing the source.

## Create searchable OCR

Run the OCR, paragraph grouping, table-block classification, and heading
tagging without replacing the source:

```sh
./ocr-scanned-pdf.sh SOURCE.pdf NEW-OCR.pdf
```

The command refuses to overwrite files. It writes a searchable PDF, a text
sidecar, and a JSON report containing every classified block. Body paragraphs
use `P`; document parts/chapters/appendices use `H1`; primary sections use `H2`;
lettered, numbered, lowercase, and parenthesized Roman levels may use `H3` through
`H6`; and table-like regions use `Div` so table labels are not promoted to
headings. A deeper prefix style is enabled only when it recurs on at least two
pages, preventing one-off list items from inventing a document hierarchy.

## Create a much smaller derivative

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
