#!/usr/bin/env python3
"""Review OCR blocks against page images with local Ollama vision models."""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path


DEFAULT_REVIEW_MODEL = "sec-ocr-review:latest"
DEFAULT_EXTRACT_MODEL = "deepseek-ocr:latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="OCR PDF whose scan is authoritative")
    parser.add_argument("tagging_report", type=Path, help="initial block report JSON")
    parser.add_argument("output", type=Path, help="append-safe review JSONL")
    parser.add_argument("--review-model", default=DEFAULT_REVIEW_MODEL)
    parser.add_argument("--extract-model", default=DEFAULT_EXTRACT_MODEL)
    parser.add_argument("--skip-extractor", action="store_true")
    parser.add_argument("--pages", help="page selection such as 1-10,14,20")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434/api/chat")
    parser.add_argument("--dpi", type=int, default=180)
    return parser.parse_args()


def selected_pages(specification: str | None, maximum: int) -> list[int]:
    if not specification:
        return list(range(1, maximum + 1))
    pages: set[int] = set()
    for part in specification.split(","):
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    invalid = sorted(page for page in pages if not 1 <= page <= maximum)
    if invalid:
        raise ValueError(f"page selection outside 1-{maximum}: {invalid}")
    return sorted(pages)


def ollama_chat(
    endpoint: str,
    model: str,
    prompt: str,
    image: bytes,
    schema: dict[str, object] | str,
) -> str:
    payload = {
        "model": model,
        "think": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image).decode("ascii")],
            }
        ],
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0,
            "num_ctx": 8192 if model.startswith("deepseek-ocr") else 32768,
            "num_predict": 8192 if model.startswith("deepseek-ocr") else 16384,
        },
    }
    if schema:
        payload["format"] = schema
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            result = json.load(response)
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ollama request failed for {model}: {error}") from error
    return result["message"]["content"]


def render_page(pdf: Path, page: int, output: Path, dpi: int) -> bytes:
    subprocess.run(
        [
            "gs",
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=pnggray",
            f"-r{dpi}",
            f"-dFirstPage={page}",
            f"-dLastPage={page}",
            f"-sOutputFile={output}",
            str(pdf),
        ],
        check=True,
    )
    return output.read_bytes()


def extract_visual_text(
    args: argparse.Namespace, image: bytes, page: int
) -> str:
    if args.skip_extractor:
        return ""
    prompt = (
        "Transcribe this historical scanned page exactly. Preserve names, initials, "
        "punctuation, numbers, currency, percentages, table rows, and reading order. "
        "Do not modernize spelling and do not infer obscured characters. Mark an "
        "unreadable character with [?]. Return plain text only."
    )
    result = ollama_chat(args.endpoint, args.extract_model, prompt, image, "")
    return re.sub(r"<\|[^>]+\|>", "", result).strip()


def decision_schema(block_ids: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": len(block_ids),
                "maxItems": len(block_ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": block_ids},
                        "type": {
                            "type": "string",
                            "enum": [
                                "body",
                                "heading1",
                                "heading2",
                                "heading3",
                                "heading4",
                                "heading5",
                                "heading6",
                                "table",
                                "artifact",
                            ],
                        },
                        "text": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "sensitive_change_evidence": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "type",
                        "text",
                        "confidence",
                        "sensitive_change_evidence",
                        "notes",
                    ],
                },
            }
        },
        "required": ["decisions"],
    }


def review_page(
    args: argparse.Namespace,
    image: bytes,
    page: int,
    blocks: list[dict[str, object]],
    visual_text: str,
) -> dict[str, object]:
    compact_blocks = [
        {
            "id": block["id"],
            "initial_type": block["type"],
            "text": block["text"],
            "protected_tokens": block["protected_tokens"],
            "position": block["position"],
        }
        for block in blocks
    ]
    prompt = f"""You are reviewing page {page} of a low-resolution 1974 government scan.

Return exactly one decision for every supplied block ID, in the same order.
Classify each block as body, heading1 through heading6, table, or artifact.

Rules:
- The page image is authoritative. Existing OCR and the specialist transcript are clues.
- Body prose must become one logical line per paragraph. Join wrapped lines and remove a
  line-end hyphen only when the image clearly shows a split word.
- A table title or all-caps cell is not automatically a section heading. Classify table
  captions, headers, rows, numeric columns, and dot leaders as table.
- Preserve historical spelling. Correct an obvious OCR error only when visible evidence
  supports the exact replacement.
- Names, corporate names, personal initials, dates, section numbers, page references,
  quantities, dollar amounts, percentages, and table values are protected evidence.
  Never normalize or guess them. If any protected token changes, confidence must be at
  least 0.98 and sensitive_change_evidence must briefly state what is visibly legible.
- Use heading1 only for document parts, chapters, and appendices. Use heading2 for
  primary sections, heading3 for lettered subsections, heading4 for numbered
  subsections, heading5 for lowercase clauses, and heading6 for parenthesized Roman
  subclauses. Assign a deep level only when the hierarchy is consistent in the
  document. Running headers, page numbers, scan marks, and Google footers are artifacts.
- If uncertain, retain the supplied text and explain the uncertainty in notes.

Specialist visual transcript (may also contain errors):
{visual_text}

Blocks:
{json.dumps(compact_blocks, ensure_ascii=False, separators=(',', ':'))}
"""
    expected = [str(block["id"]) for block in blocks]
    schema = decision_schema(expected)
    result: dict[str, object] | None = None
    for attempt in range(2):
        attempt_prompt = prompt
        if attempt:
            attempt_prompt += (
                "\nYour previous response omitted or duplicated block IDs. Return each "
                "listed ID exactly once."
            )
        raw = ollama_chat(
            args.endpoint,
            args.review_model,
            attempt_prompt,
            image,
            schema,
        )
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError:
            if attempt == 0:
                continue
            raise ValueError(f"page {page}: model returned malformed JSON twice")
        received = [
            decision.get("id") for decision in candidate.get("decisions", [])
        ]
        if len(received) == len(expected) and set(received) == set(expected):
            by_id = {decision["id"]: decision for decision in candidate["decisions"]}
            candidate["decisions"] = [by_id[block_id] for block_id in expected]
            result = candidate
            break
    if result is None:
        raise ValueError(f"page {page}: model returned incomplete or duplicate block IDs")
    return {
        "page": page,
        "extract_model": None if args.skip_extractor else args.extract_model,
        "review_model": args.review_model,
        "decisions": result["decisions"],
    }


def completed_pages(path: Path) -> set[int]:
    if not path.exists():
        return set()
    return {
        int(json.loads(line)["page"])
        for line in path.read_text().splitlines()
        if line.strip()
    }


def main() -> int:
    args = parse_args()
    if not args.pdf.is_file() or not args.tagging_report.is_file():
        raise SystemExit("PDF or tagging report not found")
    if args.output.exists() and not args.resume:
        raise SystemExit(f"Refusing to overwrite existing review: {args.output}")

    report = json.loads(args.tagging_report.read_text())
    by_page: dict[int, list[dict[str, object]]] = defaultdict(list)
    for block in report["blocks"]:
        by_page[int(block["page"])].append(block)
    maximum_page = max(by_page, default=int(report["pages"]))
    pages = selected_pages(args.pages, maximum_page)
    done = completed_pages(args.output) if args.resume else set()

    with tempfile.TemporaryDirectory(prefix="ollama-ocr-review-") as temporary:
        image_path = Path(temporary) / "page.png"
        with args.output.open("a", encoding="utf-8") as output:
            for page in pages:
                if page in done or not by_page.get(page):
                    continue
                image = render_page(args.pdf, page, image_path, args.dpi)
                visual_text = extract_visual_text(args, image, page)
                record = review_page(args, image, page, by_page[page], visual_text)
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                print(f"Reviewed page {page}/{report['pages']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
