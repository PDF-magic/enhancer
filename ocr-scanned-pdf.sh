#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./ocr-scanned-pdf.sh INPUT.pdf [OUTPUT.pdf] [--skip-text|--force-ocr] [--ai-review]

Create a searchable, tagged PDF from an English-language scanned PDF. If
OUTPUT.pdf is omitted, the output is written beside the input as
INPUT-enhanced-ocr.pdf. A plain-text sidecar and JSON tagging report are
written beside the output for searching and review. OCR lines are grouped into
logical paragraphs, and likely section headings are tagged as H1 or H2.
Use --skip-text for mixed documents to preserve pages that already have text.
Use --force-ocr to replace the text layer on every page, including existing OCR.
Use --ai-review to apply the local DeepSeek and SEC OCR review models afterward.

Install the required tools on macOS with:
  brew install ocrmypdf
EOF
}

if [[ $# -lt 1 || $# -gt 4 ]]; then
    usage >&2
    exit 2
fi

ocr_options=()
ocr_mode=""
ai_review=false
for option in "${@:3}"; do
    case "$option" in
        --skip-text|--force-ocr)
            if [[ -n "$ocr_mode" ]]; then
                printf 'Select only one OCR mode.\n' >&2
                exit 2
            fi
            ocr_mode=$option
            ocr_options+=("$option")
            ;;
        --ai-review) ai_review=true ;;
        *) usage >&2; exit 2 ;;
    esac
done

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
review_report="$output_stem.review.jsonl"

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
if "$ai_review" && [[ -e "$review_report" ]]; then
    printf 'Refusing to overwrite existing file: %s\n' "$review_report" >&2
    exit 1
fi

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

input_pages=$(qpdf --warning-exit-0 --show-npages "$input")

# Adaptive Otsu thresholding recovered more faint and broken text in this
# archive's old Google Books scans than the default thresholding pass. OCR is
# rendered at 300 DPI. --force-ocr rasterizes pages to replace all existing
# text; other modes preserve source images. Optimization remains disabled.
ocrmypdf \
    ${ocr_options[@]+"${ocr_options[@]}"} \
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
initial_report="$tagging_report"
if "$ai_review"; then
    initial_report="$tagging_dir/initial.tagging.json"
fi

"$tagger_python" "$tagger" \
    "$output" \
    "$tagged_output" \
    --report "$initial_report" \
    --reported-input "$input" \
    --reported-output "$output"

if "$ai_review"; then
    "$tagger_python" "$script_dir/ollama-review-ocr.py" \
        "$tagged_output" "$initial_report" "$review_report"
    reviewed_output="$tagging_dir/reviewed.pdf"
    "$tagger_python" "$tagger" \
        "$output" "$reviewed_output" \
        --review "$review_report" \
        --report "$tagging_report" \
        --reported-input "$input" \
        --reported-output "$output"
    tagged_output="$reviewed_output"
    "$tagger_python" - "$tagging_report" "$sidecar" <<'PY'
import json
from pathlib import Path
import sys

report = json.loads(Path(sys.argv[1]).read_text())
blocks = [block["text"] for block in report["blocks"] if block["type"] != "artifact"]
Path(sys.argv[2]).write_text("\n\n".join(blocks) + "\n")
PY
fi

# The untagged file was created by this invocation and is replaced only after
# the complete tagged sibling has been written and structurally checked.
mv -f -- "$tagged_output" "$output"

qpdf --warning-exit-0 --check "$output"
output_pages=$(qpdf --warning-exit-0 --show-npages "$output")

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
