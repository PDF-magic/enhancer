#!/usr/bin/env bash
set -euo pipefail

# Install only Enhancer's Python dependencies; Homebrew manages the OCR binaries.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
venv_python="$script_dir/.venv/bin/python"
if [[ ! -x "$venv_python" ]]; then
    bootstrap_python="${PDF_MAGIC_BOOTSTRAP_PYTHON:-python3}"
    if ! command -v "$bootstrap_python" >/dev/null 2>&1; then
        printf 'Python 3 is required to create the Enhancer virtual environment.\n' >&2
        exit 1
    fi
    "$bootstrap_python" -m venv "$script_dir/.venv"
fi
if ! "$venv_python" -c 'import pikepdf, PIL' >/dev/null 2>&1; then
    "$venv_python" -m pip install --disable-pip-version-check pikepdf Pillow
fi
"$venv_python" -c 'import pikepdf, PIL'
printf 'PDF Enhancer Python environment ready: %s\n' "$venv_python"
