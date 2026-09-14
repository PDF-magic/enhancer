#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
    printf 'Usage: ./compress-and-tag-ocr-pdf.sh INPUT.pdf OUTPUT.pdf [REVIEW.jsonl]\n' >&2
    exit 2
fi

input=$1
output=$2
review=${3:-}
output_stem=${output%.*}
compression_report="$output_stem.compression.json"
tagging_report="$output_stem.tagging.json"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [[ ! -f "$input" ]]; then
    printf 'Input PDF not found: %s\n' "$input" >&2
    exit 1
fi

for path in "$output" "$compression_report" "$tagging_report"; do
    if [[ -e "$path" ]]; then
        printf 'Refusing to overwrite existing file: %s\n' "$path" >&2
        exit 1
    fi
done

for command_name in ocrmypdf qpdf; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        exit 1
    fi
done

tagger_python=python3
if ! "$tagger_python" -c 'import pikepdf, PIL' >/dev/null 2>&1; then
    ocrmypdf_shebang=$(head -n 1 "$(command -v ocrmypdf)")
    tagger_python=${ocrmypdf_shebang#\#!}
fi

if [[ ! -x "$tagger_python" ]] || \
    ! "$tagger_python" -c 'import pikepdf, PIL' >/dev/null 2>&1; then
    printf 'Unable to find the Python environment used by OCRmyPDF.\n' >&2
    exit 1
fi

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/compress-tag-pdf.XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT
compressed="$work_dir/compressed.pdf"

"$tagger_python" "$script_dir/compress-pdf-images.py" \
    "$input" "$compressed" \
    --report "$compression_report" \
    --reported-output "$output"
tagger_args=(
    "$compressed"
    "$output"
    --report "$tagging_report"
    --reported-input "$input"
    --reported-output "$output"
)
if [[ -n "$review" ]]; then
    if [[ ! -f "$review" ]]; then
        printf 'Review file not found: %s\n' "$review" >&2
        exit 1
    fi
    tagger_args+=(--review "$review")
fi
"$tagger_python" "$script_dir/tag-ocr-pdf.py" "${tagger_args[@]}"

qpdf --check "$output"
input_pages=$(qpdf --show-npages "$input")
output_pages=$(qpdf --show-npages "$output")
if [[ "$input_pages" != "$output_pages" ]]; then
    printf 'Page-count mismatch: input=%s output=%s\n' \
        "$input_pages" "$output_pages" >&2
    exit 1
fi

printf 'Created compressed tagged PDF: %s\n' "$output"
printf 'Verified pages: %s\n' "$output_pages"
