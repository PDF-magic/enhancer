#!/usr/bin/env python3
"""Capture pages rendered by Internet Archive's BookReader into a PDF."""

import argparse
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import img2pdf
from playwright.sync_api import sync_playwright


PROFILE = Path(".internet-archive-browser")


def book_id(value):
    value = value.strip()
    if "://" in value:
        url = urlsplit(value)
        parts = url.path.split("/")
        if (url.scheme not in ("http", "https")
                or url.netloc.lower() not in ("archive.org", "www.archive.org")
                or len(parts) < 3 or parts[1] != "details"):
            raise argparse.ArgumentTypeError("Use an archive.org/details book URL.")
        value = parts[2]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise argparse.ArgumentTypeError("Use an Internet Archive item ID or details URL.")
    return value


def positive_integer(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a positive whole number.") from None
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive whole number.")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=book_id, help="Item ID or archive.org/details URL.")
    parser.add_argument("--output-dir", type=Path, help="Default: <item-id>-archive-pages.")
    parser.add_argument("--output-pdf", type=Path, help="Default: <item-id>.pdf.")
    parser.add_argument("--start", type=positive_integer, default=1,
                        help="First scan position, including front matter (one-based).")
    parser.add_argument("--end", type=positive_integer,
                        help="Last scan position, inclusive (default: end of book).")
    args = parser.parse_args(argv)
    if args.end is not None and args.end < args.start:
        parser.error("--end must be at least --start")
    return args


def wait_until(page, predicate, timeout=30):
    # Poll from Python: BookReader's CSP can block wait_for_function's eval.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        page.wait_for_timeout(200)
    raise RuntimeError("Reader page unavailable or not fully loaded. Check reader access and retry.")


def reader_count(page):
    wait_until(page, lambda: page.evaluate(
        """() => Boolean(window.br?.book?.getNumLeafs()
            && [1, 2, 3].includes(window.br.mode)
            && document.querySelector('.BRpageimage'))"""))
    return page.evaluate("() => window.br.book.getNumLeafs()")


def capture_scan(page, index):
    """Capture only the requested scan, never a neighboring or loading page."""
    page.evaluate("() => { if (window.br.mode !== 1) window.br.switchMode(1); }")
    page.evaluate("index => window.br.jumpToIndex(index)", index)
    image = page.locator(f'.BRpagecontainer[data-index="{index}"] .BRpageimage')

    def ready():
        # currentIndex() tracks the top of the scroll viewport. At the end of
        # the book it can remain on an earlier scan even with this image loaded.
        # The container's data-index identifies the requested scan directly.
        return (image.count() == 1
                and image.evaluate("img => img.complete && img.naturalWidth > 200 && img.naturalHeight > 200"))

    wait_until(page, ready)
    # Capture the rendered image element, without reader chrome or OCR overlays.
    return image.screenshot(type="png", timeout=30000)


def assemble_pdf(paths, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with temporary.open("wb") as stream:
            img2pdf.convert([str(path) for path in paths], outputstream=stream)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_pages(page, output_dir, output_pdf, start=1, end=None):
    total = reader_count(page)
    end = total if end is None else end
    if start > total or end > total:
        raise ValueError(f"Scan range exceeds the reader's {total} scans.")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for number in range(start, end + 1):
        path = output_dir / f"{number:04d}.png"
        # Keep scan numbering stable on resume, including identical blank pages.
        if not path.exists():
            png = capture_scan(page, number - 1)
            temporary = path.with_suffix(".png.tmp")
            temporary.write_bytes(png)
            temporary.replace(path)
            print(f"Captured scan {number}/{total}: {path}", flush=True)
        else:
            print(f"Using existing scan {number}: {path}", flush=True)
        paths.append(path)
    assemble_pdf(paths, output_pdf)
    print(f"Created: {output_pdf} ({len(paths)} pages, scans {start}–{end})")


def main():
    args = parse_args()
    output_dir = args.output_dir or Path(f"{args.book}-archive-pages")
    output_pdf = args.output_pdf or Path(f"{args.book}.pdf")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE), headless=False,
            viewport={"width": 1800, "height": 2200}, device_scale_factor=2)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"https://archive.org/details/{args.book}/mode/1up",
                      wait_until="domcontentloaded", timeout=60000)
            print("Internet Archive is open in a visible browser window controlled by Playwright.")
            print("1. In that browser, sign in and borrow the book if required.")
            print("2. Open the reader and wait until the book's pages are visible.")
            print("3. Return to this Terminal window and press Enter to start exporting.")
            print("The exporter waits here until you press Enter. Your login is saved for future runs.")
            print("Only pages available in this reader session can be exported.")
            input("Press Enter here in Terminal when you are ready to export... ")
            export_pages(page, output_dir, output_pdf, args.start, args.end)
        finally:
            context.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Export stopped: {exc} Captures are retained; rerun to resume.") from None
