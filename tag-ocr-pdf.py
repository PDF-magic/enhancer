#!/usr/bin/env python3
"""Add heuristic paragraph and heading structure to an OCRmyPDF PDF.

Supports OCRmyPDF's fpdf2 text forms and untagged embedded page text using
ToUnicode or WinAnsi fonts. The script groups text objects into paragraphs,
labels likely section headings, and builds the PDF structure and parent trees
without rasterizing existing page content.
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

from pdf_metadata import apply_metadata, choose_source_url, local_user_name, source_details, visible_creator
from reading_order import (
    group_native_runs, reconstruct_reading_order,
    reorder_safe_text_units, order_changes, crosses_column_gutter,
)


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
    raw_text: str | None = None
    right: float | None = None
    bold: bool = False
    italic: bool = False
    native: bool = False


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
    parser.add_argument("--source-url", default="", help="original document URL to retain as pdfmagic:href")
    parser.add_argument("--retag", action="store_true", help="rebuild existing text tags and ActualText; preserve existing bookmarks and metadata")
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
    cmap = stream.read_bytes()
    result: dict[bytes, str] = {}

    def add(source: bytes, target: bytes) -> None:
        try:
            result[source] = target.decode("utf-16-be")
        except UnicodeDecodeError:
            pass

    for section in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for source, target in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section):
            add(bytes.fromhex(source.decode()), bytes.fromhex(target.decode()))
    for section in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for first, last, target, values in re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:<([0-9A-Fa-f]+)>|\[(.*?)\])",
            section, re.S,
        ):
            start, end = int(first, 16), int(last, 16)
            width = len(first) // 2
            if not 0 <= end - start < 65536:
                raise ValueError("unsupported Unicode range")
            if target:
                base = bytes.fromhex(target.decode())
                for offset, code in enumerate(range(start, end + 1)):
                    value = int.from_bytes(base, "big") + offset
                    add(code.to_bytes(width, "big"), value.to_bytes(len(base), "big"))
            else:
                targets = re.findall(rb"<([0-9A-Fa-f]+)>", values)
                for code, value in zip(range(start, end + 1), targets):
                    add(code.to_bytes(width, "big"), bytes.fromhex(value.decode()))
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
    mappings = {}
    for name, font in fonts.items():
        if font.get("/ToUnicode") is not None:
            mappings[str(name)] = unicode_map(font)
        elif font.get("/Subtype") in {Name("/Type1"), Name("/TrueType")} and font.get("/Encoding") == Name("/WinAnsiEncoding"):
            mappings[str(name)] = {bytes([code]): bytes([code]).decode("cp1252", errors="replace") for code in range(256)}
    bold_fonts = {str(name) for name, font in fonts.items() if "bold" in str(font.get("/BaseFont", "")).lower()}
    italic_fonts = {str(name) for name, font in fonts.items()
                    if any(style in str(font.get("/BaseFont", "")).lower()
                           for style in ("italic", "oblique"))}
    if not mappings:
        raise ValueError("OCR Form XObject has no fonts")

    def advance(font_name, operands, size):
        font = fonts[font_name]
        widths = font.get("/Widths")
        first = int(font.get("/FirstChar", 0))
        value = operands[0]
        items = value if isinstance(value, Array) else [value]
        result = 0.0
        for item in items:
            if isinstance(item, String):
                if widths is not None:
                    result += sum(float(widths[c-first]) if first <= c < first+len(widths) else 0
                                  for c in bytes(item)) * size / 1000
                else:
                    result += len(decode_pdf_string(item, mappings[font_name])) * size * .5
            else:
                result -= float(item) * size / 1000
        return result

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
                "advance": 0.0,
                "break": False,
            }
        elif current is not None and operator in {"Td", "Tm"}:
            if current["text"] and ((operator == "Td" and float(operands[1]) != 0)
                                   or operator == "Tm"):
                current["break"] = True
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
        elif current is not None and operator in {"T*", "TD"}:
            current["break"] = True
        elif current is not None and operator in {"Tj", "TJ", "'", '"'}:
            mapping = mappings.get(current_font)
            if mapping is None:
                raise ValueError(f"missing Unicode map for {current_font}")
            text_operands = operands if operator == "TJ" else [operands[-1]]
            if operator in {"'", '"'}:
                current["break"] = True
            decoded = decode_show_text(text_operands, mapping)
            if current["break"] and current["text"] and decoded:
                previous = "".join(current["text"])
                tail = previous.split()[-1] if previous.split() else ""
                url_continuation = re.search(r"(?:https?://|www\.|[\w.-]+\.[a-z]+/)", tail) and re.match(r"^[a-z0-9/?%._-]", decoded)
                if not url_continuation and not previous.endswith(" ") and not decoded.startswith(" "):
                    current["text"].append(" ")
            current["break"] = False
            current["text"].append(decoded)
            current["advance"] += advance(current_font, text_operands, float(current["size"]))
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
                        bold=current_font in bold_fonts,
                        italic=current_font in italic_fonts,
                        raw_text="".join(current["text"]),
                        right=float(current["x"]) + float(current["advance"]),
                    )
                )
            current = None
    return lines


def merge_native_fragments(lines: list[OcrLine]) -> list[OcrLine]:
    """Reassemble physical lines before treating font/style runs as paragraphs."""
    merged: list[OcrLine] = []
    for line in lines:
        if merged:
            previous = merged[-1]
            gap = line.x - (previous.right if previous.right is not None else previous.x)
            if abs(previous.y-line.y) < .25 and line.x >= previous.x and gap < min(20., max(previous.size, line.size)*1.6):
                left = previous.raw_text if previous.raw_text is not None else previous.text
                right = line.raw_text if line.raw_text is not None else line.text
                separator = " " if (not left.endswith(" ") and not right.startswith(" ")
                                    and (gap > max(previous.size, line.size)*.15
                                         or (previous.bold and line.bold and re.fullmatch(r"[IVXLCDM]+\.", previous.text)))) else ""
                previous.raw_text = left + separator + right
                previous.text = previous.raw_text.strip()
                previous.end = line.end
                previous.right = line.right
                previous.bold = previous.bold and line.bold
                previous.italic = previous.italic and line.italic
                continue
        merged.append(line)
    return merged


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
            output = (output if line.native else output[:-1]) + text
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


def build_blocks(lines: list[OcrLine], page_width: float | None = None) -> list[LogicalBlock]:
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
        if page_width is not None and crosses_column_gutter(previous, line, page_width):
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


def has_extractable_text(target: pikepdf.Object) -> bool:
    """Distinguish text layers from decorative vector Form XObjects."""
    fonts = target.get("/Resources", Dictionary()).get("/Font", Dictionary())
    if not any(font.get("/ToUnicode") is not None or (
        font.get("/Subtype") in {Name("/Type1"), Name("/TrueType")}
        and font.get("/Encoding") == Name("/WinAnsiEncoding")
    ) for font in fonts.values()):
        return False
    return any(str(item.operator) in {"Tj", "TJ", "'", '"'}
               for item in pikepdf.parse_content_stream(target))


def find_ocr_form(page: pikepdf.Page) -> pikepdf.Object | None:
    xobjects = page.obj.get("/Resources", Dictionary()).get("/XObject", Dictionary())
    forms = [
        item
        for item in xobjects.values()
        if item.get("/Subtype") == Name("/Form") and has_extractable_text(item)
    ]
    return forms[0] if forms else None


def text_target(pdf: pikepdf.Pdf, page: pikepdf.Page, retag_existing: bool = False) -> pikepdf.Object | None:
    """Use an OCR form, or the page's original text without rasterizing it."""
    if has_extractable_text(page.obj):
        if pdf.Root.get("/StructTreeRoot") is not None and not retag_existing:
            return None  # Keep an existing document structure intact.
        return page.obj
    return find_ocr_form(page)


def remove_structure_markers(instructions):
    """Remove old text tags while preserving optional-content visibility scopes."""
    retained = []
    scopes = []
    for item in instructions:
        operator = str(item.operator)
        if operator in {"BDC", "BMC"}:
            properties = item.operands[1] if operator == "BDC" else None
            remove = (str(item.operands[0]) == "/Artifact"
                      or isinstance(properties, Dictionary) and "/MCID" in properties)
            scopes.append(remove)
            if remove:
                continue
        elif operator == "EMC" and scopes:
            if scopes.pop():
                continue
        retained.append(item)
    return retained


def merge_heading_markers(lines: list[OcrLine]) -> list[OcrLine]:
    merged = []
    for line in lines:
        if merged:
            previous = merged[-1]
            if (previous.bold and line.bold and abs(previous.y - line.y) < 1
                and abs(previous.size - line.size) < .1
                and line.x > previous.x):
                separator = "" if line.text[:1].islower() and not previous.text.endswith((" ", "\u00a0")) else " "
                previous.text += separator + line.text
                previous.end = line.end
                continue
        merged.append(line)
    return merged


def classify_existing_lines(lines: list[OcrLine], heading_sizes: list[float], repeated: set[str], page_width: float, enabled: set[str]) -> None:
    """Use recurring printed styles, excluding running headers and bold prose."""
    for line in lines:
        text = re.sub(r"\s+", " ", line.text).strip()
        line.heading = None
        line.table = False
        # Native text fragments include pronouns and footnote references;
        # OCR noise/page-number rules must not hide these from extraction.
        line.artifact = False
        if text in repeated or re.fullmatch(r"Page \d+ of \d+", text, re.I):
            line.artifact = True
            continue
        if line.artifact:
            continue
        if line.x > page_width * .55 or text.startswith(("—", "...", "[")) or re.match(r"^\d{1,2} [A-Z][a-z]{2} \d{4}$", text):
            continue
        words = [word for word in text.split() if word[0].isalpha()]
        title_case = sum(word[0].isupper() for word in words) / max(1, len(words)) >= .6
        size = round(line.size, 2)
        if (line.bold and size in heading_sizes and 4 <= len(text) <= 180
            and 1 <= len(words) <= 14 and title_case and not WORD_END.search(text)):
            line.heading = "H" + str(min(6, heading_sizes.index(size) + 1))
            for level, pattern in DEEP_HEADING_PATTERNS:
                if level in enabled and pattern.match(text):
                    # Printed size determines the top levels. Recurring
                    # lowercase markers can distinguish smaller nested levels.
                    if level == "H5":
                        line.heading = "H" + str(max(int(line.heading[1]), 4))
                    elif level == "H6":
                        line.heading = "H" + str(max(int(line.heading[1]), 5))
                    break


def merge_chapter_titles(lines: list[OcrLine], heading_sizes: list[float], page_width: float = 612) -> list[OcrLine]:
    """Join confidently formatted chapter labels and titles, not nearby prose."""
    named_label = re.compile(
        r"(?:chapter|chap\.?|part|appendix)\s+(?:\d{1,3}|[ivxlcdm]{1,8}|[a-z]|"
        r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
        r"(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)",
        re.I,
    )
    bare_label = re.compile(r"(?:\d{1,3}|[ivxlcdm]{1,8})[.)]?", re.I)
    merged = []
    index = 0
    while index < len(lines):
        label = lines[index]
        if index + 1 < len(lines) and not label.artifact:
            named = bool(named_label.fullmatch(label.text.strip()))
            bare = bool(bare_label.fullmatch(label.text.strip()))
            if named or bare:
                title = lines[index + 1]
                title_text = title.text.strip()
                words = [w for w in title_text.split() if any(c.isalpha() for c in w)]
                capitals = sum(w.lstrip('"“‘([—')[:1].isupper() for w in words)
                title_like = bool(words) and (capitals / len(words) >= .6 or title_text.endswith(('?', '!')))
                known_size = round(title.size, 2) in heading_sizes
                larger = title.size >= label.size * 1.18
                distinct_style = title.bold or title.italic
                if (
                    not title.artifact and not title.table
                    and 1 <= len(title_text.split()) <= 14 and len(title_text) <= 120
                    and not title_text.endswith('.') and title_like
                    and 0 < label.y - title.y <= 3 * title.size
                    and abs(label.x - title.x) <= max(96, page_width * .25)
                    and title.size >= label.size
                    and (
                        (named and (
                            (larger and (distinct_style or title.size >= label.size * 1.4))
                            or (label.bold and title.bold and known_size and title.heading is not None)
                        ))
                        or (bare and label.bold and title.bold and known_size
                            and (larger or title.heading is not None))
                    )
                ):
                    label.text = label.text.strip() + " " + title_text
                    label.end = title.end
                    label.size = title.size
                    level = 1 if named else min(6, heading_sizes.index(round(title.size, 2)) + 1)
                    label.heading = f"H{level}"
                    label.table = False
                    index += 1
        merged.append(label)
        index += 1
    return merged


def write_heading_outline(pdf: pikepdf.Pdf, headings: list[dict[str, object]]) -> int:
    """Expose recognized heading tags as navigable PDF sections."""
    roots = []
    ancestors = []
    count = 0
    for heading in headings:
        level_name = str(heading.get("level", ""))
        title = str(heading.get("text", "")).strip()
        page = heading.get("page")
        if level_name not in {"H1", "H2", "H3", "H4", "H5", "H6"} or not title:
            continue
        if type(page) is not int or not 1 <= page <= len(pdf.pages):
            continue
        level = int(level_name[1])
        item = pikepdf.OutlineItem(title, destination=page - 1)
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        if ancestors:
            ancestors[-1][1].children.append(item)
        else:
            roots.append(item)
        ancestors.append((level, item))
        count += 1
    if roots:
        with pdf.open_outline() as outline:
            outline.root = roots
    return count


def tag_pdf(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    decisions: dict[str, dict[str, object]] | None = None,
    reported_input: Path | None = None,
    reported_output: Path | None = None,
    source_url: str = "",
    retag_existing: bool = False,
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
        "physical_reordered_units": 0,
        "layout_reordered_pages": 0,
    }

    original_path = reported_input if reported_input and reported_input.is_file() else input_path
    original = source_details(original_path)
    reference_url = choose_source_url(original["href"], source_url)
    with pikepdf.open(input_path) as pdf:
        report["pages"] = len(pdf.pages)
        existing_outline = pdf.Root.get("/Outlines") is not None
        targets = [text_target(pdf, page, retag_existing) for page in pdf.pages]
        extracted = []
        for page, target in zip(pdf.pages, targets):
            if target is None:
                extracted.append(([], []))
                continue
            instructions = list(pikepdf.parse_content_stream(target))
            if retag_existing:
                instructions = remove_structure_markers(instructions)
            lines = extract_lines(target, instructions)
            if retag_existing and target.get("/Type") == Name("/Page") and lines:
                page_width = float(page.mediabox[2]) - float(page.mediabox[0])
                page_height = float(page.mediabox[3]) - float(page.mediabox[1])
                physical_lines = group_native_runs(lines, page_width)
                ordered = reconstruct_reading_order(
                    physical_lines, page_width, page_height
                )
                instructions, moved = reorder_safe_text_units(
                    instructions, lines, ordered
                )
                if moved:
                    report["physical_reordered_units"] += moved
                    # All source indices change when complete q ... Q drawing
                    # scopes move. Never reuse the old line index ranges.
                    lines = extract_lines(target, instructions)
            extracted.append((instructions, lines))
        name_lines = []
        for page_number, (page, (_, lines)) in enumerate(zip(pdf.pages, extracted), start=1):
            if page_number <= 2 or page_number >= max(1, len(pdf.pages) - 4):
                for line in lines:
                    name_lines.append({"page": page_number, "text": line.text,
                                       "x": line.x, "y": line.y, "size": line.size,
                                       "height": float(page.mediabox[3]) - float(page.mediabox[1])})
        creator, creator_source = visible_creator(name_lines, len(pdf.pages))
        if not creator:
            creator = original["author"] or local_user_name()
            creator_source = "document metadata" if original["author"] else "local user"
        report["creator"] = creator
        report["creator_source"] = creator_source
        report["href"] = reference_url or None
        if not any(lines for _, lines in extracted):
            report["preserved_existing_text"] = True
            if not retag_existing:
                apply_metadata(pdf, creator, reference_url)
            pdf.save(output_path, fix_metadata_version=not retag_existing)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            return report
        deep_heading_pages = {level: set() for level in ("H3", "H4", "H5", "H6")}
        margins = Counter()
        styles = {}
        existing_prefixes = {level: set() for level, _ in DEEP_HEADING_PATTERNS}
        for page_number, (page, target, (_, lines)) in enumerate(zip(pdf.pages, targets, extracted), start=1):
            page_height = float(page.mediabox[3]) - float(page.mediabox[1])
            margins.update({re.sub(r"\s+", " ", line.text).strip() for line in lines if line.y > page_height * .90 or line.y < page_height * .08})
            for line in lines:
                if target is not None and target.get("/Type") == Name("/Page"):
                    if line.bold:
                        styles.setdefault(round(line.size, 2), set()).add(page_number)
                        for level, pattern in DEEP_HEADING_PATTERNS:
                            # Markers may be separate text objects.
                            if pattern.match(line.text + " Heading"):
                                existing_prefixes[level].add(page_number)
                level = deep_heading_level(line.text)
                if level:
                    deep_heading_pages[level].add(page_number)
        heading_sizes = sorted((size for size, pages in styles.items() if len(pages) >= 2 or len(pdf.pages) == 1), reverse=True)
        existing_deep = {level for level, pages in existing_prefixes.items() if len(pages) >= 2}
        repeated = {text for text, count in margins.items() if count >= max(3, len(pdf.pages) * .3)}
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

        for page_number, (page, form, (instructions, lines)) in enumerate(zip(pdf.pages, targets, extracted), start=1):
            if form is None or not lines:
                continue
            is_page = form.get("/Type") == Name("/Page")
            if not is_page:
                mark_page_images_as_artifacts(pdf, page)
            bbox = page.mediabox if is_page else form.get("/BBox", Array([0, 0, 1, 1]))
            page_width = float(bbox[2]) - float(bbox[0])
            page_height = float(bbox[3]) - float(bbox[1])
            if is_page:
                for line in lines:
                    line.native = True
                lines = merge_native_fragments(lines)
                lines = merge_heading_markers(lines)
            classify_lines(lines, page_width, enabled_deep_headings)
            if is_page:
                classify_existing_lines(lines, heading_sizes, repeated, page_width, existing_deep)
                lines = merge_chapter_titles(lines, heading_sizes, page_width)
            # Build contiguous content-stream spans first, so a visually
            # adjacent but physically interleaved column cannot cause an
            # ActualText tag to swallow unrelated drawing instructions.
            blocks = build_blocks(lines, page_width)
            ordered_blocks = reconstruct_reading_order(blocks, page_width, page_height)
            if order_changes(blocks, ordered_blocks):
                report["layout_reordered_pages"] += 1
            blocks = ordered_blocks

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
            if is_page:
                stream = pdf.make_stream(pikepdf.unparse_content_stream(instructions))
                rewrite_form(stream, instructions, blocks)
                page.obj["/Contents"] = stream
            else:
                rewrite_form(form, instructions, blocks)

            form["/StructParents"] = parent_key
            parents = Array()
            mcid = 0
            for block in blocks:
                if block.tag is None:
                    report["artifact_blocks"] = int(report["artifact_blocks"]) + 1
                    continue
                content_reference = Dictionary(
                    Type=Name("/MCR"), Pg=page.obj, MCID=mcid
                )
                if not is_page:
                    content_reference["/Stm"] = form
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
        if retag_existing and existing_outline:
            report["preserved_existing_outline"] = True
        else:
            report["outline_entries"] = write_heading_outline(pdf, report["headings"])
        if not retag_existing:
            apply_metadata(pdf, creator, reference_url)
        pdf.save(output_path, fix_metadata_version=not retag_existing)

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def validate_output(path: Path, expected_pages: int, require_tags: bool = True) -> None:
    with pikepdf.open(path) as pdf:
        if len(pdf.pages) != expected_pages:
            raise ValueError("tagged PDF page count changed")
        if not require_tags:
            return
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
            args.source_url,
            args.retag,
        )
        validate_output(args.output, expected_pages, require_tags=report["tagged_pages"] > 0)
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
