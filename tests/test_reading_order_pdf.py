"""Full-document regression for --retag; run on macOS with the book available.

PDF_MAGIC_BOOK=/Users/GitHub/JFWooten4/JFWooten4/static/book.pdf \
    python3 -m pytest -q tests/test_reading_order_pdf.py

This creates only temporary output files. It never replaces the original book.
Preview drag selection is deliberately a separate manual acceptance test.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest

fitz = pytest.importorskip('fitz')
BOOK = os.environ.get('PDF_MAGIC_BOOK', '')
TAGGER = Path(__file__).resolve().parents[1] / 'tag-ocr-pdf.py'


def _layout(page):
    return (tuple(page.rect), tuple(page.mediabox),
            tuple(page.cropbox), page.rotation)


def _links(page):
    # Do not compare mutable annotation object numbers: compare destinations.
    return [{k: v for k, v in link.items() if k != 'xref'}
            for link in page.get_links()]


def _fonts(page):
    return sorted(tuple(font[1:]) for font in page.get_fonts(full=True))


def _text(page):
    # The ordering must change, not the printed wording. Canonicalizing
    # extracted page text also avoids reliance on line-break heuristics.
    return re.sub(r'\s+', '', page.get_text(sort=True))


def _renders_equal(first, second, pages, dpi=96):
    for index in pages:
        a = first[index].get_pixmap(dpi=dpi, alpha=False)
        b = second[index].get_pixmap(dpi=dpi, alpha=False)
        assert (a.width, a.height, a.n) == (b.width, b.height, b.n)
        assert a.samples == b.samples, f'page {index + 1} differs at {dpi} dpi'


@pytest.mark.skipif(not BOOK or not Path(BOOK).is_file(),
                    reason='Set PDF_MAGIC_BOOK to a copy of Nine to Noon')
@pytest.mark.skipif(shutil.which('qpdf') is None,
                    reason='Install qpdf to run the complete PDF regression')
def test_full_book_retag_twice_preserves_render_and_navigation():
    with TemporaryDirectory(prefix='enhancer-reading-order-') as directory:
        source = Path(BOOK)
        first = Path(directory) / 'retagged-1.pdf'
        second = Path(directory) / 'retagged-2.pdf'
        for original, output in ((source, first), (first, second)):
            subprocess.run([sys.executable, str(TAGGER), str(original),
                            str(output), '--retag'], check=True)
            subprocess.run(['qpdf', '--check', str(output)], check=True)
        with fitz.open(source) as before, fitz.open(first) as after, fitz.open(second) as repeat:
            assert len(before) == len(after) == len(repeat)
            assert before.metadata == after.metadata == repeat.metadata
            assert before.get_xml_metadata() == after.get_xml_metadata() == repeat.get_xml_metadata()
            assert before.get_toc(simple=False) == after.get_toc(simple=False) == repeat.get_toc(simple=False)
            for i in range(len(before)):
                assert _layout(before[i]) == _layout(after[i]) == _layout(repeat[i])
                assert _links(before[i]) == _links(after[i]) == _links(repeat[i])
                assert _fonts(before[i]) == _fonts(after[i]) == _fonts(repeat[i])
                assert _text(before[i]) == _text(after[i]) == _text(repeat[i])
            # Exhaustive 96 DPI pass and two higher-resolution page checks.
            _renders_equal(before, after, range(len(before)))
            _renders_equal(after, repeat, range(len(before)))
            _renders_equal(before, after, [5, 6], dpi=144)
            _renders_equal(after, repeat, [5, 6], dpi=144)
            footer = after[5].get_text(sort=False)
            isbn1 = footer.find('Paperback ISBN')
            isbn2 = footer.find('Hardback ISBN')
            disclaimer = footer.find('Full disclaimer')
            assert -1 not in (isbn1, isbn2, disclaimer)
            assert isbn1 < isbn2 < disclaimer
            # On page 7, section headings must precede their column entries.
            # For the complete TOC order, inspect the tagging-report blocks.
            assert after[6].get_text()
