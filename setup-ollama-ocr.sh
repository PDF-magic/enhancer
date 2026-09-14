#!/usr/bin/env bash

set -euo pipefail

if ! command -v ollama >/dev/null 2>&1; then
    printf 'Ollama is not installed. Install it before running this setup.\n' >&2
    exit 1
fi

# DeepSeek-OCR supplies a specialist transcription candidate. Qwen3-VL 32B
# performs the final image-grounded block classification and correction. Its
# 21 GB Q4 build leaves practical headroom on a 48 GB Apple Silicon Mac; the
# 36 GB Q8 build does not leave enough for vision and context memory.
ollama pull deepseek-ocr:latest
ollama pull qwen3-vl:32b-instruct-q4_K_M
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ollama create sec-ocr-review -f "$script_dir/ollama/Modelfile.sec-ocr-review"

printf '%s\n' 'Installed the local OCR review models:'
ollama list | grep -E '^(deepseek-ocr|qwen3-vl:32b-instruct-q4_K_M|sec-ocr-review)' || true
