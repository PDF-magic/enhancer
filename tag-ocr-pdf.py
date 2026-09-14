#!/usr/bin/env python3
"""Add heuristic paragraph and heading structure to an OCRmyPDF PDF.

This targets OCRmyPDF's fpdf2 renderer, which places invisible OCR text in one
Form XObject per page and normally emits one BT/ET text object per visual line.
The script groups those lines into paragraphs, labels likely section headings,
and builds the PDF structure and parent trees needed for real tagged content.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pikepdf
from pikepdf import Array, Dictionary, Name, Operator, String


MAJOR_HEADING = re.compile(
    r"^(?:chapter\s+[ivxlcdm0-9]+|part\s+[ivxlcdm0-9]+|"
    r"appendix\s+[a-z0-9]+)"
    r"(?:\s*[-—:]\s*[^,.]{1,80})?$",
    re.IGNORECASE,
)
SECTION_HEADING = re.compile(
    r"^section\s+[a-z0-9]+(?:\s*[-—:]\s*[^,.]{1,80})?$", re.IGNORECASE
)
DEEP_HEADING_PATTERNS = (
    ("H6", re.compile(r"^\([ivxlcdm]{1,8}\)\s+[A-Z]")),
    ("H5", re.compile(r"^(?:[a-z][.)]|\([a-z]\))\s+[A-Z]")),
    ("H4", re.compile(r"^(?:\d{1,3}[.)]|\(\d{1,3}\))\s+[A-Z]")),
    ("H3", re.compile(r"^[A-Z]{1,4}[.)]\s+[A-Z]")),
)
PAGE_NUMBER = re.compile(r"^(?:[ivxlcdm]+|\d+[a-z]?)$", re.IGNORECASE)
GOOGLE_FOOTER = re.compile(r"^digitized\s+by\s+google$", re.IGNORECASE)
WORD_END = re.compile(r"[.!?][\"'’”)]*$")
PARAGRAPH_END = re.compile(r"[.,;:!?][\"'’”)]*$")
SENSITIVE_TOKEN = re.compile(
    r"(?:\$?\b\d[\d,]*(?:\.\d+)?%?\b|"
    r"\b(?:[A-Z]\.){1,4}|"
    r"\b[A-Z](?:\.|\s)+[A-Z][A-Za-z.'’-]+|"
    r"\b[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){1,3})"
)


@dataclass
class OcrLine:
    start: int
    end: int
    text: str
    x: float
    y: float
    size: float
    artifact: bool = False
    table: bool = False
    heading: str | None = None


@dataclass
class LogicalBlock:
    start: int
    end: int
    text: str
    tag: str | None
    kind: str
    lines: list[OcrLine]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tag OCR text as paragraphs and section headings."
    )
    parser.add_argument("input", type=Path, help="OCRmyPDF-generated input PDF")
    parser.add_argument("output", type=Path, help="new tagged PDF")
    parser.add_argument(
        "--report",
        type=Path,
        help="optional JSON report (default: OUTPUT.tagging.json)",
    )
    parser.add_argument(
        "--review",
        type=Path,
        help="optional Ollama JSON/JSONL decisions produced by ollama-review-ocr.py",
    )
    parser.add_argument("--reported-input", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--reported-output", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def load_review(path: Path | None) -> dict[str, dict[str, object]]:
    if path is None:
        return {}
    raw = path.read_text().strip()
    try:
        parsed = json.loads(raw)
        records = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    decisions: dict[str, dict[str, object]] = {}
    for record in records:
        for decision in record.get("decisions", []):
            block_id = decision.get("id")
            if isinstance(block_id, str):
                decisions[block_id] = decision
    return decisions


def unicode_map(font: pikepdf.Object) -> dict[bytes, str]:
    stream = font.get("/ToUnicode")
    if stream is None:
        raise ValueError("OCR font has no /ToUnicode map")
    pairs = re.findall(
        rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", stream.read_bytes()
    )
    result: dict[bytes, str] = {}
    for source, target in pairs:
        try:
            result[bytes.fromhex(source.decode())] = bytes.fromhex(
                target.decode()
            ).decode("utf-16-be")
        except (UnicodeDecodeError, ValueError):
            continue
    return result


def decode_pdf_string(value: pikepdf.Object, mapping: dict[bytes, str]) -> str:
    raw = bytes(value)
    widths = sorted({len(key) for key in mapping}, reverse=True)
    output: list[str] = []
    offset = 0
    while offset < len(raw):
        for width in widths:
            token = raw[offset : offset + width]
            if token in mapping:
                output.append(mapping[token])
                offset += width
                break
        else:
            output.append("�")
            offset += widths[-1] if widths else 1
    return "".join(output)


def decode_show_text(
    operands: pikepdf.Object,
    mapping: dict[bytes, str],
) -> str:
    value = operands[0]
    if isinstance(value, pikepdf.Array):
        return "".join(
            decode_pdf_string(item, mapping)
            for item in value
            if isinstance(item, pikepdf.String)
        )
    return decode_pdf_string(value, mapping)


def multiply(
    outer: tuple[float, float, float, float, float, float],
    inner: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = outer
    g, h, i, j, k, l = inner
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    )


def transform(
    matrix: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def extract_lines(
    form: pikepdf.Object, instructions: list[pikepdf.ContentStreamInstruction]
) -> list[OcrLine]:
    resources = form.get("/Resources", Dictionary())
    fonts = resources.get("/Font", Dictionary())
    mappings = {str(name): unicode_map(font) for name, font in fonts.items()}
    if not mappings:
        raise ValueError("OCR Form XObject has no fonts")

    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm = identity
    stack: list[tuple[float, float, float, float, float, float]] = []
    current: dict[str, object] | None = None
    current_font = next(iter(mappings))
    lines: list[OcrLine] = []

    for index, instruction in enumerate(instructions):
        operator = str(instruction.operator)
        operands = instruction.operands
        if operator == "q":
            stack.append(ctm)
        elif operator == "Q":
            ctm = stack.pop() if stack else identity
        elif operator == "cm":
            local = tuple(float(value) for value in operands)
            ctm = multiply(ctm, local)  # type: ignore[arg-type]
        elif operator == "BT":
            current = {
                "start": index,
                "text": [],
                "x": 0.0,
                "y": 0.0,
                "size": 0.0,
                "positioned": False,
            }
        elif current is not None and operator in {"Td", "Tm"}:
            if not current["positioned"]:
                if operator == "Td":
                    local_x, local_y = float(operands[0]), float(operands[1])
                else:
                    local_x, local_y = float(operands[4]), float(operands[5])
                current["x"], current["y"] = transform(ctm, local_x, local_y)
                current["positioned"] = True
        elif current is not None and operator == "Tf":
            current_font = str(operands[0])
            current["size"] = float(operands[1])
        elif current is not None and operator in {"Tj", "TJ"}:
            mapping = mappings.get(current_font)
            if mapping is None:
                raise ValueError(f"missing Unicode map for {current_font}")
            current["text"].append(decode_show_text(operands, mapping))
        elif operator == "ET" and current is not None:
            text = "".join(current["text"]).strip()
            if text:
                lines.append(
                    OcrLine(
                        start=int(current["start"]),
                        end=index,
                        text=text,
                        x=float(current["x"]),
                        y=float(current["y"]),
                        size=float(current["size"]),
                    )
                )
            current = None
    return lines


def letter_stats(text: str) -> tuple[int, float]:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return 0, 0.0
    uppercase = sum(character.isupper() for character in letters)
    return len(letters), uppercase / len(letters)


def deep_heading_level(text: str) -> str | None:
    letters, uppercase_ratio = letter_stats(text)
    words = text.split()
    if (
        not 5 <= letters <= 85
        or not 2 <= len(words) <= 11
        or uppercase_ratio < 0.72
        or WORD_END.search(text)
    ):
        return None
    for level, pattern in DEEP_HEADING_PATTERNS:
        if pattern.match(text):
            return level
    return None


def classify_lines(
    lines: list[OcrLine],
    _page_width: float,
    enabled_deep_headings: set[str] | None = None,
) -> None:
    normalized = [re.sub(r"\s+", " ", line.text.strip().upper()) for line in lines]
    repeated_labels = Counter(text for text in normalized if 2 <= len(text.split()) <= 5)
    table_markers = [
        index
        for index, text in enumerate(normalized)
        if text.startswith("TABLE ")
        or text.startswith("APPENDIX TABLE ")
        or " STOCKHOLDER RANK" in text
        or "TRUST DEPARTMENT RANK" in text
    ]
    numeric_lines = sum(
        line.text.count(".") >= 4
        or sum(character.isdigit() for character in line.text)
        > max(5, sum(character.isalpha() for character in line.text) // 2)
        for line in lines
    )
    table_heavy = (
        bool(table_markers)
        or numeric_lines >= max(6, len(lines) // 6)
        or max(repeated_labels.values(), default=0) >= 3
    )
    table_start = min(table_markers) if table_markers else 0

    for index, line in enumerate(lines):
        stripped = line.text.strip()
        letters, uppercase_ratio = letter_stats(stripped)
        words = stripped.split()
        unique_letters = {character.upper() for character in stripped if character.isalpha()}
        noise_like = letters > 20 and len(unique_letters) / letters < 0.11
        if (
            GOOGLE_FOOTER.fullmatch(stripped)
            or PAGE_NUMBER.fullmatch(stripped)
            or letters < 2
            or noise_like
            or (len(stripped) <= 5 and sum(char.isalnum() for char in stripped) <= 2)
        ):
            line.artifact = True
            continue

        strong_prefix = bool(MAJOR_HEADING.fullmatch(stripped))
        section_prefix = bool(SECTION_HEADING.fullmatch(stripped))
        deep_level = deep_heading_level(stripped)
        if deep_level not in (enabled_deep_headings or set()):
            deep_level = None
        previous_is_table_title = index > 0 and lines[index - 1].text.lower().startswith(
            "table "
        )
        table_like = (
            stripped.lower().startswith("table ")
            or stripped.count(".") >= 4
            or sum(character.isdigit() for character in stripped) > max(5, letters // 2)
        )
        line.table = table_like or (table_heavy and index >= table_start)
        all_caps = (
            5 <= letters <= 85
            and 1 <= len(words) <= 11
            and uppercase_ratio >= 0.90
            and not WORD_END.search(stripped)
            and not previous_is_table_title
            and not (table_heavy and index >= table_start)
        )

        if not table_like and (strong_prefix or section_prefix or deep_level or all_caps):
            if strong_prefix:
                line.heading = "H1"
            elif deep_level:
                line.heading = deep_level
            else:
                line.heading = "H2"
            line.table = False


def typical_line_gap(lines: list[OcrLine]) -> float:
    gaps: list[float] = []
    for previous, current in zip(lines, lines[1:]):
        gap = previous.y - current.y
        if 3 <= gap <= 80:
            gaps.append(gap)
    return statistics.median(gaps) if gaps else 24.0


def join_lines(lines: Iterable[OcrLine]) -> str:
    output = ""
    for line in lines:
        text = re.sub(r"\s+", " ", line.text).strip()
        if not output:
            output = text
        elif output.endswith("-") and text and text[0].islower():
            output = output[:-1] + text
        else:
            output += " " + text
    return output


def protected_tokens(text: str) -> list[str]:
    """Return tokens an AI review must change only with visible evidence."""
    return list(
        dict.fromkeys(
            match.group(0).strip() for match in SENSITIVE_TOKEN.finditer(text)
        )
    )


def apply_review(
    block: LogicalBlock,
    block_id: str,
    decisions: dict[str, dict[str, object]],
) -> tuple[bool, str | None]:
    decision = decisions.get(block_id)
    if not decision:
        return False, None
    try:
        confidence = float(decision.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.75:
        return False, "decision below 0.75 confidence"

    type_to_tag = {
        "body": "P",
        "heading1": "H1",
        "heading2": "H2",
        "heading3": "H3",
        "heading4": "H4",
        "heading5": "H5",
        "heading6": "H6",
        "table": "Div",
        "artifact": None,
    }
    reviewed_type = decision.get("type")
    if reviewed_type in type_to_tag:
        block.kind = str(reviewed_type)
        block.tag = type_to_tag[str(reviewed_type)]

    reviewed_text = decision.get("text")
    if (
        isinstance(reviewed_text, str)
        and reviewed_text.strip()
        and reviewed_text != block.text
    ):
        missing_sensitive = [
            token for token in protected_tokens(block.text) if token not in reviewed_text
        ]
        evidence = str(decision.get("sensitive_change_evidence", "")).strip()
        if missing_sensitive and (confidence < 0.98 or not evidence):
            return (
                True,
                "sensitive text change rejected without 0.98 confidence and visual evidence",
            )
        block.text = re.sub(r"\s+", " ", reviewed_text).strip()
    return True, None


def starts_new_paragraph(
    previous: OcrLine,
    current: OcrLine,
    gap: float,
) -> bool:
    vertical_gap = previous.y - current.y
    if current.y > previous.y + gap:
        return True
    if vertical_gap > gap * 1.55:
        return True
    if current.x > previous.x + 15 and (
        PARAGRAPH_END.search(previous.text) or len(previous.text) <= 28
    ):
        return True
    return False


def build_blocks(lines: list[OcrLine]) -> list[LogicalBlock]:
    if not lines:
        return []
    gap = typical_line_gap(lines)
    blocks: list[LogicalBlock] = []
    active: list[OcrLine] = []

    def flush() -> None:
        if not active:
            return
        if active[0].artifact:
            tag = None
            kind = "artifact"
        elif active[0].heading:
            tag = active[0].heading
            kind = {
                "H1": "heading1",
                "H2": "heading2",
                "H3": "heading3",
                "H4": "heading4",
                "H5": "heading5",
                "H6": "heading6",
            }[tag]
        elif active[0].table:
            tag = "Div"
            kind = "table"
        else:
            tag = "P"
            kind = "body"
        blocks.append(
            LogicalBlock(
                start=active[0].start,
                end=active[-1].end,
                text=join_lines(active),
                tag=tag,
                kind=kind,
                lines=list(active),
            )
        )
        active.clear()

    for line in lines:
        if not active:
            active.append(line)
            continue
        previous = active[-1]
        category_changed = (
            line.artifact != previous.artifact
            or line.table != previous.table
            or line.heading != previous.heading
        )
        if line.heading is not None and previous.heading == line.heading:
            vertical_gap = previous.y - line.y
            if line.y > previous.y + gap or vertical_gap > gap * 1.30:
                category_changed = True
        if category_changed or (
            not line.artifact
            and line.heading is None
            and starts_new_paragraph(previous, line, gap)
        ):
            flush()
        active.append(line)
    flush()
    return blocks


def marked_instruction(tag: str, mcid: int, text: str) -> pikepdf.ContentStreamInstruction:
    properties = Dictionary(MCID=mcid, ActualText=String(text))
    return pikepdf.ContentStreamInstruction(
        [Name("/" + tag), properties], Operator("BDC")
    )


def simple_instruction(operator: str, operands: list[pikepdf.Object] | None = None):
    return pikepdf.ContentStreamInstruction(operands or [], Operator(operator))


def rewrite_form(
    form: pikepdf.Object,
    instructions: list[pikepdf.ContentStreamInstruction],
    blocks: list[LogicalBlock],
) -> None:
    openings: dict[int, list[pikepdf.ContentStreamInstruction]] = {}
    closings: dict[int, list[pikepdf.ContentStreamInstruction]] = {}
    mcid = 0
    for block in blocks:
        if block.tag is None:
            opening = simple_instruction("BMC", [Name("/Artifact")])
        else:
            opening = marked_instruction(block.tag, mcid, block.text)
            mcid += 1
        openings.setdefault(block.start, []).append(opening)
        closings.setdefault(block.end, []).append(simple_instruction("EMC"))

    rewritten: list[pikepdf.ContentStreamInstruction] = []
    for index, instruction in enumerate(instructions):
        rewritten.extend(openings.get(index, []))
        rewritten.append(instruction)
        rewritten.extend(closings.get(index, []))
    form.write(pikepdf.unparse_content_stream(rewritten))


def mark_page_images_as_artifacts(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
    image_names = {
        str(name)
        for name, xobject in page.obj.get("/Resources", Dictionary())
        .get("/XObject", Dictionary())
        .items()
        if xobject.get("/Subtype") == Name("/Image")
    }
    if not image_names:
        return
    instructions = pikepdf.parse_content_stream(page.obj)
    rewritten: list[pikepdf.ContentStreamInstruction] = []
    changed = False
    for instruction in instructions:
        if (
            str(instruction.operator) == "Do"
            and str(instruction.operands[0]) in image_names
        ):
            rewritten.append(simple_instruction("BMC", [Name("/Artifact")]))
            rewritten.append(instruction)
            rewritten.append(simple_instruction("EMC"))
            changed = True
        else:
            rewritten.append(instruction)
    if changed:
        page.obj["/Contents"] = pdf.make_stream(
            pikepdf.unparse_content_stream(rewritten)
        )


def find_ocr_form(page: pikepdf.Page) -> pikepdf.Object | None:
    xobjects = page.obj.get("/Resources", Dictionary()).get("/XObject", Dictionary())
    forms = [
        item
        for item in xobjects.values()
        if item.get("/Subtype") == Name("/Form") and item.get("/Resources")
    ]
    return forms[0] if forms else None


def tag_pdf(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    decisions: dict[str, dict[str, object]] | None = None,
    reported_input: Path | None = None,
    reported_output: Path | None = None,
) -> dict[str, object]:
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must be different files")
    if output_path.exists() or report_path.exists():
        raise FileExistsError("refusing to overwrite output or report")

    report: dict[str, object] = {
        "input": str(reported_input or input_path),
        "output": str(reported_output or output_path),
        "pages": 0,
        "tagged_pages": 0,
        "paragraphs": 0,
        "headings": [],
        "artifact_blocks": 0,
        "block_counts": {
            "body": 0,
            "heading1": 0,
            "heading2": 0,
            "heading3": 0,
            "heading4": 0,
            "heading5": 0,
            "heading6": 0,
            "table": 0,
            "artifact": 0,
        },
        "blocks": [],
        "reviewed_blocks": 0,
        "review_warnings": [],
        "deep_heading_profile": {},
    }

    with pikepdf.open(input_path) as pdf:
        report["pages"] = len(pdf.pages)
        deep_heading_pages = {level: set() for level in ("H3", "H4", "H5", "H6")}
        for page_number, page in enumerate(pdf.pages, start=1):
            form = find_ocr_form(page)
            if form is None:
                continue
            instructions = list(pikepdf.parse_content_stream(form))
            for line in extract_lines(form, instructions):
                level = deep_heading_level(line.text)
                if level:
                    deep_heading_pages[level].add(page_number)
        enabled_deep_headings = {
            level for level, pages in deep_heading_pages.items() if len(pages) >= 2
        }
        report["deep_heading_profile"] = {
            level: {
                "pages": len(deep_heading_pages[level]),
                "enabled": level in enabled_deep_headings,
            }
            for level in ("H3", "H4", "H5", "H6")
        }
        structure_root = pdf.make_indirect(Dictionary(Type=Name("/StructTreeRoot")))
        document = pdf.make_indirect(
            Dictionary(Type=Name("/StructElem"), S=Name("/Document"), P=structure_root)
        )
        document["/K"] = Array()
        structure_root["/K"] = Array([document])
        parent_numbers = Array()
        parent_key = 0

        for page_number, page in enumerate(pdf.pages, start=1):
            mark_page_images_as_artifacts(pdf, page)
            form = find_ocr_form(page)
            if form is None:
                continue
            instructions = list(pikepdf.parse_content_stream(form))
            lines = extract_lines(form, instructions)
            bbox = form.get("/BBox", Array([0, 0, 1, 1]))
            page_width = float(bbox[2]) - float(bbox[0])
            classify_lines(lines, page_width, enabled_deep_headings)
            blocks = build_blocks(lines)

            for block_number, block in enumerate(blocks, start=1):
                block_id = f"p{page_number:04d}-b{block_number:04d}"
                reviewed, warning = apply_review(block, block_id, decisions or {})
                if reviewed:
                    report["reviewed_blocks"] = int(report["reviewed_blocks"]) + 1
                if warning:
                    report["review_warnings"].append({"id": block_id, "warning": warning})
                report["block_counts"][block.kind] += 1
                report["blocks"].append(
                    {
                        "id": block_id,
                        "page": page_number,
                        "type": block.kind,
                        "text": block.text,
                        "protected_tokens": protected_tokens(block.text),
                        "position": {
                            "x": round(min(line.x for line in block.lines), 2),
                            "top_y": round(max(line.y for line in block.lines), 2),
                            "bottom_y": round(min(line.y for line in block.lines), 2),
                        },
                    }
                )
            rewrite_form(form, instructions, blocks)

            form["/StructParents"] = parent_key
            parents = Array()
            mcid = 0
            for block in blocks:
                if block.tag is None:
                    report["artifact_blocks"] = int(report["artifact_blocks"]) + 1
                    continue
                content_reference = Dictionary(
                    Type=Name("/MCR"), Pg=page.obj, Stm=form, MCID=mcid
                )
                element = pdf.make_indirect(
                    Dictionary(
                        Type=Name("/StructElem"),
                        S=Name("/" + block.tag),
                        P=document,
                        Pg=page.obj,
                        K=content_reference,
                    )
                )
                if block.tag in {"H1", "H2", "H3", "H4", "H5", "H6"}:
                    element["/T"] = String(block.text)
                document["/K"].append(element)
                parents.append(element)
                if block.tag == "P":
                    report["paragraphs"] = int(report["paragraphs"]) + 1
                elif block.tag in {"H1", "H2", "H3", "H4", "H5", "H6"}:
                    report["headings"].append(
                        {"page": page_number, "level": block.tag, "text": block.text}
                    )
                mcid += 1

            parent_numbers.extend([parent_key, pdf.make_indirect(parents)])
            parent_key += 1
            page.obj["/Tabs"] = Name("/S")
            report["tagged_pages"] = int(report["tagged_pages"]) + 1

        parent_tree = pdf.make_indirect(Dictionary(Nums=parent_numbers))
        structure_root["/ParentTree"] = parent_tree
        structure_root["/ParentTreeNextKey"] = parent_key
        pdf.Root["/StructTreeRoot"] = structure_root
        pdf.Root["/MarkInfo"] = Dictionary(Marked=True)
        pdf.Root["/Lang"] = String("en-US")
        pdf.save(output_path)

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def validate_output(path: Path, expected_pages: int) -> None:
    with pikepdf.open(path) as pdf:
        if len(pdf.pages) != expected_pages:
            raise ValueError("tagged PDF page count changed")
        if pdf.Root.get("/StructTreeRoot") is None:
            raise ValueError("tagged PDF has no /StructTreeRoot")
        mark_info = pdf.Root.get("/MarkInfo", Dictionary())
        if not bool(mark_info.get("/Marked", False)):
            raise ValueError("tagged PDF is not marked as tagged")
        parent_tree = pdf.Root.StructTreeRoot.get("/ParentTree")
        if parent_tree is None or not parent_tree.get("/Nums"):
            raise ValueError("tagged PDF has no structure parent tree")


def main() -> int:
    args = parse_args()
    report_path = args.report or args.output.with_suffix(".tagging.json")
    if not args.input.is_file():
        print(f"Input PDF not found: {args.input}", file=sys.stderr)
        return 1
    try:
        with pikepdf.open(args.input) as source:
            expected_pages = len(source.pages)
        decisions = load_review(args.review)
        report = tag_pdf(
            args.input,
            args.output,
            report_path,
            decisions,
            args.reported_input,
            args.reported_output,
        )
        validate_output(args.output, expected_pages)
    except Exception as error:
        print(f"Tagging failed: {error}", file=sys.stderr)
        return 1

    print(f"Created tagged PDF: {args.output}")
    print(f"Created tagging report: {report_path}")
    print(f"Tagged OCR pages: {report['tagged_pages']}/{report['pages']}")
    print(f"Paragraphs: {report['paragraphs']}")
    print(f"Headings: {len(report['headings'])}")
    print("Heading detection is heuristic; review the JSON report against the scan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
