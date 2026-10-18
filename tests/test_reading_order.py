"""Layout-only tests that run without a PDF interpreter dependency."""
from __future__ import annotations
import sys
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reading_order import (
    reconstruct_reading_order, group_native_runs, reorder_safe_text_units, crosses_column_gutter,
)


@dataclass
class Line:
    text: str
    x: float
    y: float
    right: float
    size: float = 12
    heading: str | None = None
    start: int = 0
    end: int = 0


@dataclass
class Instruction:
    operator: str


class ReadingOrderTests(unittest.TestCase):
    def test_footer_follows_printed_order(self):
        # Deliberately wrong content-stream order, exactly as PDF page 6.
        lines = [
            Line('John F. Wooten, IV - CC BY-SA 4.0', 136, 136, 314),
            Line('Full disclaimer at end of book.', 160, 40, 308),
            Line('Paperback ISBN: 978-1-7945-6685-9', 144, 82, 323),
            Line('Hardback ISBN: 979-8-4829-2018-3', 144, 68, 321),
        ]
        expected = [lines[0], lines[2], lines[3], lines[1]]
        actual = reconstruct_reading_order(lines, 432, 648)
        self.assertEqual([id(x) for x in actual], [id(x) for x in expected])
        self.assertEqual([id(x) for x in reconstruct_reading_order(actual, 432, 648)],
                         [id(x) for x in expected])

    def test_two_column_contents_with_spanning_section_headings(self):
        content = [
            Line('Table of Contents', 157, 582, 270, heading='H1'),
            Line('Fundamental Analysis', 153, 550, 266, heading='H2'),
        ]
        left = [Line(f'{n}. Left {n}', 36, 535 - n*14, 181)
                for n in range(1, 7)]
        right = [Line(f'{n}. Right {n}', 225, 535 - (n-6)*14, 371)
                 for n in range(7, 13)]
        second_heading = Line('Technical Analysis', 161, 267, 262, heading='H2')
        lower_left = [Line(f'{n}. Second left', 36, 244 - n*14, 181)
                      for n in range(1, 5)]
        lower_right = [Line(f'{n}. Second right', 225, 244 - n*14, 371)
                       for n in range(1, 5)]
        # Simulate an interleaved stream that alternates physical columns.
        original = content + [x for pair in zip(left, right) for x in pair]
        original += [second_heading]
        original += [x for pair in zip(lower_left, lower_right) for x in pair]
        ordered = reconstruct_reading_order(original, 432, 648)
        self.assertEqual(ordered,
                         content + left + right + [second_heading]
                         + lower_left + lower_right)

    def test_single_column_paragraphs_not_split_by_center_of_page(self):
        lines = [Line(f'A paragraph across center {n}', 52, 510 - n*13, 380)
                 for n in range(10)]
        # A small right-side number should not manufacture a second column.
        lines += [Line('1', 350, 373, 359)]
        ordered = reconstruct_reading_order(list(reversed(lines)), 432, 648)
        self.assertEqual(ordered, lines)

    def test_mixed_font_fragments_same_baseline_are_one_line(self):
        # Font/style changes with separate BT/ET operators.
        runs = [
            Line('Bright', 40, 410, 76, start=0, end=5),
            Line(' ', 76, 410, 79, start=6, end=11),
            Line('Heading', 79, 410, 133, start=12, end=17),
            Line('body', 40, 390, 68, start=18, end=23),
        ]
        physical = group_native_runs(runs, 432)
        self.assertEqual(len(physical), 2)
        self.assertEqual(physical[0].text, 'Bright Heading')
        self.assertEqual((physical[0].start, physical[0].end), (0, 17))

    def test_column_gutter_separates_interleaved_paragraph_runs(self):
        left = Line('1. The Day (8)', 36, 530, 120)
        right = Line('13. News and Principles (90)', 225, 530, 360)
        continuation = Line('2. Starting to Get There', 36, 516, 184)
        self.assertTrue(crosses_column_gutter(left, right, 432))
        self.assertTrue(crosses_column_gutter(right, continuation, 432))
        self.assertFalse(crosses_column_gutter(left, continuation, 432))

    def test_q_wrapped_text_moved_without_touching_clip_or_transform(self):
        # Each scope is self-contained, with clipping, text, and a font.
        ops = ['q','re','W*','n','BT','Tf','Tm','Tj','ET','Q']
        stream = [Instruction(o) for o in ops * 3]
        raw = [Line('Author', 40, 130, 150, start=4, end=8),
               Line('Disclaimer', 40, 40, 150, start=14, end=18),
               Line('ISBN', 40, 80, 150, start=24, end=28)]
        ordered = [raw[0],raw[2],raw[1]]
        new, moved = reorder_safe_text_units(stream,raw,ordered)
        self.assertEqual(moved, 2)
        self.assertEqual([id(x) for x in new[10:20]],
                         [id(x) for x in stream[20:30]])
        self.assertEqual([id(x) for x in new[20:30]],
                         [id(x) for x in stream[10:20]])
        self.assertEqual([str(x.operator) for x in new], [str(x.operator) for x in stream])

    def test_do_form_is_hard_boundary(self):
        ops = [Instruction(o) for o in [
            'q','BT','Tf','Tm','Tj','ET','Q', 'q','Do','Q',
            'q','BT','Tf','Tm','Tj','ET','Q']]
        raw = [Line('After', 50, 20, 120, start=1, end=5),
               Line('Before', 50, 40, 120, start=11, end=15)]
        new, moved = reorder_safe_text_units(ops, raw, [raw[1], raw[0]])
        self.assertEqual(moved,0)
        self.assertTrue(all(x is y for x,y in zip(new,ops)))

    def test_repeat_reconstruction_idempotent(self):
        a=Line('A', 35, 100, 100, start=0, end=3)
        b=Line('B', 240, 100, 300, start=4, end=7)
        c=Line('C', 35, 86, 100, start=8, end=11)
        d=Line('D', 240, 86, 300, start=12, end=15)
        original=[b,a,d,c]
        first=reconstruct_reading_order(original,432,648)
        second=reconstruct_reading_order(first,432,648)
        self.assertEqual(first,second)

if __name__ == '__main__':
    unittest.main()
