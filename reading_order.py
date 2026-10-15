"""Conservative geometric reading-order reconstruction for PDF text blocks.

The input objects need only x/y/text/size and optionally right/heading/lines.
PDF user-space y increases toward the top of the page. All ordering is stable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar
import re

T = TypeVar("T")


@dataclass(frozen=True)
class Region:
    x0: float
    x1: float
    y: float
    size: float
    heading: bool
    original: int


def _region(item: object, index: int, page_width: float) -> Region:
    lines = getattr(item, "lines", None)
    if lines is not None:
        if not lines:
            raise ValueError("cannot order an empty text block")
        parts = [_region(line, index, page_width) for line in lines]
        return Region(
            min(p.x0 for p in parts), max(p.x1 for p in parts),
            max(p.y for p in parts), max(p.size for p in parts),
            any(p.heading for p in parts), index,
        )
    x = float(getattr(item, "x"))
    size = max(1.0, float(getattr(item, "size", 10)))
    text = str(getattr(item, "text", ""))
    right = getattr(item, "right", None)
    if right is None or float(right) <= x:
        right = x + max(size, len(text) * size * .48)
    return Region(x, min(page_width, float(right)), float(getattr(item, "y")),
                  size, bool(getattr(item, "heading", False)), index)


def _two_columns(items: list[tuple[T, Region]], width: float) -> bool:
    """Require recurrent, vertically overlapping text on both sides of a gutter."""
    middle = width * .50
    gap = max(5., width * .015)
    left = [r for _, r in items if r.x1 < middle - gap and
            r.x0 < width * .40]
    right = [r for _, r in items if r.x0 > middle + gap and
             r.x1 > width * .60]
    if len(left) < 3 or len(right) < 3:
        return False
    common_top = min(max(r.y for r in left), max(r.y for r in right))
    common_bottom = max(min(r.y for r in left), min(r.y for r in right))
    if common_top <= common_bottom:
        return False
    # One isolated sidebar/note is not sufficient evidence of two columns.
    span = common_top - common_bottom
    return (sum(common_bottom <= r.y <= common_top for r in left) >= 3
            and sum(common_bottom <= r.y <= common_top for r in right) >= 3
            and span >= min(35., width * .08))


def _spanning(r: Region, width: float, page_height: float | None) -> bool:
    center = width / 2
    centered = abs((r.x0 + r.x1) / 2 - center) <= width * .17
    crosses_gutter = r.x0 < center - width * .015 < r.x1
    # Heading or isolated central section divider, not arbitrary long prose.
    separator = (crosses_gutter and centered and
                 (r.heading or (r.x1 - r.x0 >= width * .18
                                and r.x0 > width * .18
                                and r.x1 < width * .82)))
    page_number = (page_height is not None and r.y < page_height * .055
                   and centered and (r.x1 - r.x0) < width * .16)
    return separator or page_number


def reconstruct_reading_order(
    items: list[T], page_width: float, page_height: float | None = None,
) -> list[T]:
    """Order a page as heading/band, then left column, then right column.

    Retains original stream order for near-identical positions and ambiguous
    column patterns. Does not move or alter any PDF drawing instructions.
    """
    if len(items) < 2:
        return list(items)
    items_with_boxes = [(item, _region(item, i, page_width))
                        for i, item in enumerate(items)]
    if not _two_columns(items_with_boxes, page_width):
        return [item for item, r in sorted(
            items_with_boxes, key=lambda pair: (-pair[1].y, pair[1].x0,
                                                pair[1].original))]
    anchors = [(item, r) for item, r in items_with_boxes
               if _spanning(r, page_width, page_height)]
    anchors.sort(key=lambda pair: (-pair[1].y, pair[1].original))
    result: list[T] = []
    remaining = [(item, r) for item, r in items_with_boxes
                 if not _spanning(r, page_width, page_height)]
    ceiling = float("inf")

    def append_band(floor: float) -> None:
        nonlocal remaining
        band = [(item, r) for item, r in remaining if floor < r.y <= ceiling]
        remaining = [(item, r) for item, r in remaining
                     if not floor < r.y <= ceiling]
        if _two_columns(band, page_width):
            left = [p for p in band if (p[1].x0 + p[1].x1) / 2 < page_width / 2]
            right = [p for p in band if p not in left]
            for column in (left, right):
                result.extend(item for item, _ in sorted(
                    column, key=lambda p: (-p[1].y, p[1].x0, p[1].original)))
        else:
            result.extend(item for item, _ in sorted(
                band, key=lambda p: (-p[1].y, p[1].x0, p[1].original)))

    for item, region in anchors:
        append_band(region.y)
        result.append(item)
        ceiling = region.y
    append_band(float("-inf"))
    return result


def order_changes(items: list[T], ordered: list[T]) -> bool:
    """Identity-based check, not equality (duplicate printed strings exist)."""
    return any(a is not b for a, b in zip(items, ordered))


# Reordering is permitted only for independent, balanced q ... Q scopes.
# Moving a bare BT ... ET is unsafe: a preceding gs, cm, clipping path, or
# inherited text state may be essential to its appearance.
LOCAL_TEXT_OPERATORS = frozenset({
    "q", "Q", "re", "W", "W*", "n", "BT", "ET",
    "Tf", "Tm", "Td", "TD", "T*", "Tc", "Tw", "Tz", "TL",
    "Tr", "Ts", "Tj", "TJ", "'", '"',
    "gs", "g", "G", "rg", "RG", "k", "K", "cs", "CS",
    "sc", "SC", "scn", "SCN", "cm",
})


def reorder_safe_text_units(
    instructions: list[object],
    raw_lines: list[object],
    ordered_lines: list[object],
) -> tuple[list[object], int]:
    """Reorder safe self-contained text drawing scopes, never arbitrary BT/ET.

    Return (new_instructions, moved_units); 0 means no physical changes.
    `ordered_lines` are the logical line groups returned by the layout
    reconstructor; raw_lines retain the original BT..ET instruction indices.
    """
    if not raw_lines or not ordered_lines:
        return list(instructions), 0
    ops = [str(i.operator) for i in instructions]
    stack: list[tuple[int, int | None]] = []
    scopes: list[tuple[int, int, int | None]] = []
    for index, op in enumerate(ops):
        if op == "q":
            stack.append((index, stack[-1][0] if stack else None))
        elif op == "Q":
            if not stack:
                return list(instructions), 0
            start, parent = stack.pop()
            scopes.append((start, index + 1, parent))
    if stack:
        return list(instructions), 0

    # Locate the physical line to which each independently state-scoped
    # drawing unit belongs. Whitespace-only units are left in their slots.
    units: list[tuple[int, int, int | None, int | None, float]] = []
    for start, end, parent in scopes:
        segment = ops[start:end]
        if (segment.count("q") != 1 or segment.count("Q") != 1
                or segment.count("BT") != 1 or segment.count("ET") != 1
                or not set(segment).issubset(LOCAL_TEXT_OPERATORS)):
            continue
        fragments = [r for r in raw_lines
                     if start <= int(r.start) <= int(r.end) < end]
        if len(fragments) > 1:
            continue  # A single q scope must own at most one text object.
        rank = None
        x = 0.
        if fragments:
            fragment = fragments[0]
            x = float(fragment.x)
            for i, line in enumerate(ordered_lines):
                if (int(line.start) <= int(fragment.start) <= int(fragment.end)
                        <= int(line.end)
                        and abs(float(fragment.y) - float(line.y))
                        < max(2.5, float(fragment.size) * .4)):
                    rank = i
                    break
        units.append((start, end, parent, rank, x))

    if not units:
        return list(instructions), 0
    units.sort()
    # Do not cross unbalanced graphics state, text-free drawing, optional
    # content, marked content, or external Form/Image operations.
    islands: list[list[tuple[int, int, int | None, int | None, float]]] = []
    active: list[tuple[int, int, int | None, int | None, float]] = []
    for unit in units:
        if (active and
                (unit[2] != active[-1][2] or unit[0] != active[-1][1])):
            if len(active) > 1:
                islands.append(active)
            active = []
        active.append(unit)
    if len(active) > 1:
        islands.append(active)

    swaps: dict[int, list[object]] = {}
    moved = 0
    for island in islands:
        visible = [u for u in island if u[3] is not None]
        if len(visible) < 2:
            continue
        sorted_visible = sorted(visible, key=lambda u: (u[3], u[4], u[0]))
        if all(a[0] == b[0] for a, b in zip(visible, sorted_visible)):
            continue
        # Fail closed for text on top of other text: reordering translucent
        # glyphs could change compositing. Same-line fragments retain x order.
        ranks = {u[3] for u in visible}
        if len(ranks) < 2:
            continue
        relevant_lines = [ordered_lines[i] for i in ranks]
        ambiguous_overlap = False
        for i, a in enumerate(relevant_lines):
            for b in relevant_lines[i + 1:]:
                if abs(float(a.y) - float(b.y)) > max(
                        float(a.size), float(b.size)) * .85:
                    continue
                a_right = float(getattr(a, "right", None) or a.x)
                b_right = float(getattr(b, "right", None) or b.x)
                if max(float(a.x), float(b.x)) < min(a_right, b_right) - 1:
                    ambiguous_overlap = True
                    break
            if ambiguous_overlap:
                break
        if ambiguous_overlap:
            continue
        for slot, source in zip(visible, sorted_visible):
            if slot[0] != source[0]:
                swaps[slot[0]] = instructions[source[0]:source[1]]
                moved += 1

    if not swaps:
        return list(instructions), 0
    ranges = {start: end for start, end, _, _, _ in units}
    rewritten: list[object] = []
    index = 0
    while index < len(instructions):
        end = ranges.get(index)
        if end is not None:
            rewritten.extend(swaps.get(index, instructions[index:end]))
            index = end
        else:
            rewritten.append(instructions[index])
            index += 1
    return rewritten, moved



@dataclass
class PhysicalLine:
    """A layout line assembled from independent PDF text objects."""
    start: int
    end: int
    text: str
    x: float
    y: float
    right: float
    size: float
    heading: str | None = None


def group_native_runs(raw_lines: list[object], page_width: float) -> list[PhysicalLine]:
    """Group positioned font/style fragments before inferring page columns.

    Avoid treating each single-letter native PDF object as an entire column.
    The groups are used for *ordering*, not as permission to move their bare
    BT/ET instructions without the surrounding graphics-state scope.
    """
    rows: list[list[object]] = []
    for run in sorted(raw_lines, key=lambda r: (-float(r.y), float(r.x),
                                                 int(r.start))):
        for row in rows[-3:]:
            if abs(float(row[0].y) - float(run.y)) <= min(
                    1.5, max(0.3, float(run.size) * .13)):
                row.append(run)
                break
        else:
            rows.append([run])
    grouped: list[PhysicalLine] = []
    for row in rows:
        group: list[object] = []

        def flush() -> None:
            if not group:
                return
            first = group[0]
            text = ""
            prior_right: float | None = None
            for fragment in group:
                value = str(getattr(fragment, "raw_text", None) or fragment.text)
                if (text and prior_right is not None
                        and float(fragment.x) - prior_right
                        > max(1.8, float(fragment.size) * .18)
                        and not text.endswith(" ") and not value.startswith(" ")):
                    text += " "
                text += value
                prior_right = float(getattr(fragment, "right", None) or (
                    float(fragment.x) + len(value) * float(fragment.size) * .48))
            grouped.append(PhysicalLine(
                min(int(r.start) for r in group),
                max(int(r.end) for r in group),
                text.strip(),
                min(float(r.x) for r in group),
                float(first.y),
                max(float(getattr(r, "right", None) or (
                    float(r.x) + len(str(r.text)) * float(r.size) * .48))
                    for r in group),
                max(float(r.size) for r in group),
            ))
            group.clear()

        for run in sorted(row, key=lambda r: (float(r.x), int(r.start))):
            if group:
                previous = group[-1]
                right = float(getattr(previous, "right", None) or (
                    float(previous.x) + len(str(previous.text)) *
                    float(previous.size) * .48))
                gap = float(run.x) - right
                limit = max(10., min(20., max(float(previous.size),
                                              float(run.size)) * 1.6))
                # Preserve a genuine column gutter even if adjacent font runs
                # would otherwise satisfy a broad horizontal-merge heuristic.
                if gap > limit or gap < -max(float(run.size) * 2, 25):
                    flush()
            group.append(run)
        flush()
    return grouped


def crosses_column_gutter(previous: object, current: object, page_width: float) -> bool:
    """Do not merge interleaved left/right-column rows into one paragraph."""
    middle = page_width * .5
    a, b = float(previous.x), float(current.x)
    return (min(a, b) < middle * .90
            and max(a, b) > middle * 1.02
            and abs(a - b) > page_width * .23)
