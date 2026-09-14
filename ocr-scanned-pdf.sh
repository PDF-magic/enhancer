#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./ocr-scanned-pdf.sh INPUT.pdf [OUTPUT.pdf]

Create a searchable, tagged PDF from an English-language scanned PDF. If
OUTPUT.pdf is omitted, the output is written beside the input as
INPUT-enhanced-ocr.pdf. A plain-text sidecar and JSON tagging report are
written beside the output for searching and review. OCR lines are grouped into
logical paragraphs, and likely section headings are tagged as H1 or H2.

Install the required tools on macOS with:
  brew install ocrmypdf
EOF
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
    usage >&2
    exit 2
fi

input=$1

if [[ ! -f "$input" ]]; then
    printf 'Input PDF not found: %s\n' "$input" >&2
    exit 1
fi

input_dir=$(dirname -- "$input")
input_name=$(basename -- "$input")
input_stem=${input_name%.*}
output=${2:-"$input_dir/$input_stem-enhanced-ocr.pdf"}
output_stem=${output%.*}
sidecar="$output_stem.txt"
tagging_report="$output_stem.tagging.json"

if [[ "$input" == "$output" ]]; then
    printf 'Input and output must be different files.\n' >&2
    exit 1
fi

for path in "$output" "$sidecar" "$tagging_report"; do
    if [[ -e "$path" ]]; then
        printf 'Refusing to overwrite existing file: %s\n' "$path" >&2
        exit 1
    fi
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
tagger="$script_dir/tag-ocr-pdf.py"

if [[ ! -f "$tagger" ]]; then
    printf 'PDF tagging script not found: %s\n' "$tagger" >&2
    exit 1
fi

tagger_python=python3
if ! "$tagger_python" -c 'import pikepdf' >/dev/null 2>&1; then
    ocrmypdf_shebang=$(head -n 1 "$(command -v ocrmypdf)")
    tagger_python=${ocrmypdf_shebang#\#!}
fi

if [[ ! -x "$tagger_python" ]] || \
    ! "$tagger_python" -c 'import pikepdf' >/dev/null 2>&1; then
    printf 'Unable to find the Python environment used by OCRmyPDF.\n' >&2
    exit 1
fi

for command_name in ocrmypdf qpdf; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        printf 'Install the OCR tools with: brew install ocrmypdf\n' >&2
        exit 1
    fi
done

input_pages=$(qpdf --show-npages "$input")

# Adaptive Otsu thresholding recovered more faint and broken text in this
# archive's old Google Books scans than the default thresholding pass. OCR is
# rendered at 300 DPI, while the source page images remain unchanged in the
# output. Optimization is disabled to avoid lossy image recompression.
ocrmypdf \
    --language eng \
    --output-type pdf \
    --optimize 0 \
    --oversample 300 \
    --tesseract-thresholding adaptive-otsu \
    --no-tesseract-downsample-large-images \
    --sidecar "$sidecar" \
    --no-overwrite \
    "$input" \
    "$output"

tagging_dir=$(mktemp -d "${TMPDIR:-/tmp}/ocr-pdf-tagging.XXXXXX")
trap 'rm -rf -- "$tagging_dir"' EXIT
tagged_output="$tagging_dir/tagged.pdf"

"$tagger_python" "$tagger" \
    "$output" \
    "$tagged_output" \
    --report "$tagging_report" \
    --reported-input "$input" \
    --reported-output "$output"

# The untagged file was created by this invocation and is replaced only after
# the complete tagged sibling has been written and structurally checked.
mv -f -- "$tagged_output" "$output"

qpdf --check "$output"
output_pages=$(qpdf --show-npages "$output")

if [[ "$input_pages" != "$output_pages" ]]; then
    printf 'Page-count mismatch: input=%s output=%s\n' \
        "$input_pages" "$output_pages" >&2
    exit 1
fi

word_count=$(wc -w < "$sidecar" | tr -d ' ')

printf 'Created searchable PDF: %s\n' "$output"
printf 'Created OCR text sidecar: %s\n' "$sidecar"
printf 'Created tagging report: %s\n' "$tagging_report"
printf 'Verified pages: %s\n' "$output_pages"
printf 'Recognized word tokens: %s\n' "$word_count"
printf '%s\n' \
    'OCR remains an unverified aid; confirm names, numbers, and tables against the scan.'
