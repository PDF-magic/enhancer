#!/usr/bin/env python3

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import re
import time
from urllib.parse import urlencode

import img2pdf
from playwright.sync_api import sync_playwright


READER_URL = "https://play.google.com/books/reader"
BOOK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
PROFILE = Path(".google-books-browser")

WRITE_WORKERS = 4

# Prefer the reader iframe navigation controls; use ArrowRight as a fallback.
# Observe the rendered page before capturing or issuing another navigation.
NAV_CHANGE_TIMEOUT = 12.0
NAV_OBSERVE_ROUNDS = 5
NAV_KEY_ATTEMPTS = 4
RENDER_SETTLE_SECONDS = 1.2
START_JUMP_ATTEMPTS = 6

FIND_PAGE_CANDIDATE = r"""
() => {
    const vw = window.innerWidth ||
               document.documentElement.clientWidth;
    const vh = window.innerHeight ||
               document.documentElement.clientHeight;

    const candidates = document.querySelectorAll(
        "img, canvas, svg, div, section"
    );

    function isVisual(el) {
        const tag = el.tagName;

        if (
            tag === "IMG" ||
            tag === "CANVAS" ||
            tag === "SVG"
        ) {
            return true;
        }

        const style = getComputedStyle(el);

        return (
            style.backgroundImage &&
            style.backgroundImage !== "none"
        );
    }

    let best = null;

    for (const el of candidates) {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);

        if (
            style.display === "none" ||
            style.visibility === "hidden" ||
            Number(style.opacity || 1) === 0
        ) {
            continue;
        }

        // Must overlap the current viewport.
        if (
            rect.right <= 0 ||
            rect.bottom <= 0 ||
            rect.left >= vw ||
            rect.top >= vh
        ) {
            continue;
        }

        const visibleWidth =
            Math.min(rect.right, vw) -
            Math.max(rect.left, 0);

        const visibleHeight =
            Math.min(rect.bottom, vh) -
            Math.max(rect.top, 0);

        if (
            visibleWidth < 200 ||
            visibleHeight < 300
        ) {
            continue;
        }

        const ratio = rect.width / rect.height;

        // A scanned book page should be approximately portrait.
        if (ratio < 0.30 || ratio > 1.20) {
            continue;
        }

        const ownVisual = isVisual(el);

        // Google sometimes tiles a page underneath a wrapper.
        let directVisualChild = false;

        for (const child of el.children) {
            if (isVisual(child)) {
                directVisualChild = true;
                break;
            }
        }

        if (!ownVisual && !directVisualChild) {
            continue;
        }

        const area = visibleWidth * visibleHeight;

        // Prefer elements centered in the reader.
        const x =
            rect.left + rect.width / 2;
        const y =
            rect.top + rect.height / 2;

        const dx =
            Math.abs(x - vw / 2) / vw;
        const dy =
            Math.abs(y - vh / 2) / vh;

        const centerBonus =
            1 / (1 + dx * 3 + dy * 2);

        // Ordinary portrait pages get a bonus.
        const portraitBonus =
            ratio >= 0.45 && ratio <= 0.90
                ? 2.0
                : 1.0;

        // Avoid accidentally choosing the entire reader application.
        const fullscreenPenalty =
            rect.width > vw * 0.94 &&
            rect.height > vh * 0.94
                ? 0.05
                : 1.0;

        // Actual page surfaces generally do not contain controls.
        const controls =
            el.querySelectorAll(
                "button, input, [role='button']"
            ).length;

        const controlPenalty =
            controls > 0 ? 0.2 : 1.0;

        const score =
            area *
            centerBonus *
            portraitBonus *
            fullscreenPenalty *
            controlPenalty;

        if (!best || score > best.score) {
            best = {
                el,
                score,
                width: rect.width,
                height: rect.height,
                tag: el.tagName,
                ownVisual,
                directVisualChild
            };
        }
    }

    if (!best) {
        return null;
    }

    const token =
        "gbook-" +
        Math.random().toString(36).slice(2) +
        Date.now().toString(36);

    best.el.setAttribute(
        "data-gbook-capture-target",
        token
    );

    return {
        token,
        score: best.score,
        width: best.width,
        height: best.height,
        tag: best.tag,
        ownVisual: best.ownVisual,
        directVisualChild: best.directVisualChild
    };
}
"""


def find_page_element(page):
    """Return the best page-like element from any frame."""
    best = None

    for frame_number, frame in enumerate(page.frames):
        try:
            candidate = frame.evaluate(FIND_PAGE_CANDIDATE)
        except Exception:
            continue

        if not candidate:
            continue

        candidate["frame_number"] = frame_number
        candidate["frame_url"] = frame.url

        if best is None or candidate["score"] > best["info"]["score"]:
            best = {"frame": frame, "info": candidate}

    if best is None:
        return None, None

    token = best["info"]["token"]
    locator = best["frame"].locator(
        f'[data-gbook-capture-target="{token}"]'
    )
    return locator, best["info"]


def capture_page(page, verbose=False):
    """Capture the currently rendered page element and return its PNG + digest."""
    locator, info = find_page_element(page)

    if locator is None:
        print("\nFrames visible to Playwright:")
        for i, frame in enumerate(page.frames):
            print(f"  [{i}] {frame.url or '(no URL)'}")
        raise RuntimeError(
            "Could not locate the rendered Google Books page in any frame."
        )

    locator.wait_for(state="visible", timeout=5000)
    box = locator.bounding_box()
    png = locator.screenshot(
        type="png",
        animations="disabled",
        caret="hide",
    )
    tag = locator.evaluate("(el) => el.tagName")
    digest = hashlib.sha256(png).hexdigest()

    if verbose:
        print(
            "    detected "
            f"{tag} {info['width']:.0f}×{info['height']:.0f} "
            f"in frame {info['frame_number']}"
        )

    return png, digest, tag, box


def _clamp_clip_to_viewport(page, box):
    """Clamp a page bounding box to the visible top-level viewport."""
    if not box:
        return None

    viewport = page.viewport_size or {"width": 1800, "height": 2200}
    vw = float(viewport["width"])
    vh = float(viewport["height"])

    x = max(0.0, float(box["x"]))
    y = max(0.0, float(box["y"]))
    right = min(vw, float(box["x"]) + float(box["width"]))
    bottom = min(vh, float(box["y"]) + float(box["height"]))

    width = right - x
    height = bottom - y

    if width < 50 or height < 50:
        return None

    # Stay a pixel inside the viewport to avoid Playwright clip rounding errors.
    return {
        "x": x,
        "y": y,
        "width": max(1.0, width - 1.0),
        "height": max(1.0, height - 1.0),
    }


def visible_page_digest(page, box):
    """Hash the pixels visibly occupying the current page rectangle.

    This intentionally screenshots a FIXED viewport rectangle instead of
    re-running find_page_element(). Google may briefly leave the old page in the
    DOM during a turn, which made the old detector follow a stale element even
    though the reader visibly advanced.
    """
    clip = _clamp_clip_to_viewport(page, box)
    if clip is None:
        return None

    png = page.screenshot(
        type="png",
        clip=clip,
        animations="disabled",
        caret="hide",
    )
    return hashlib.sha256(png).hexdigest()


def _parse_int(text):
    if text is None:
        return None
    text = str(text).strip().replace(",", "")
    if text.isdigit():
        return int(text)
    return None


def page_number_controls(page):
    """Yield visible controls that look like Google Books page-number inputs."""
    selectors = [
        'input[type="range"][aria-valuetext*="page" i]',
        'input[aria-label*="page" i]',
        'input[title*="page" i]',
        'input[placeholder*="page" i]',
        '[contenteditable="true"][aria-label*="page" i]',
        '[contenteditable="true"][title*="page" i]',
    ]

    seen = set()
    for frame in page.frames:
        for selector in selectors:
            try:
                loc = frame.locator(selector)
                count = loc.count()
            except Exception:
                continue

            for i in range(count):
                item = loc.nth(i)
                try:
                    if not item.is_visible():
                        continue
                    key = (frame.url, selector, i)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield item
                except Exception:
                    continue


def read_reader_page_number(page):
    """Best-effort read of the visible reader page-number control."""
    for control in page_number_controls(page):
        try:
            tag = control.evaluate('(el) => el.tagName')
            if tag == 'INPUT':
                value = control.input_value()
            else:
                value = control.text_content()
            number = _parse_int(value)
            if number is not None:
                if control.get_attribute("type") == "range":
                    return number - int(control.get_attribute("min") or 0) + 1
                return number
        except Exception:
            continue
    return None


def jump_to_reader_page(page, target, attempts=START_JUMP_ATTEMPTS):
    """Automatically move Google Play Books to a requested reader page.

    This uses a visible page slider or page-number control. It does not guess by firing a
    large number of ArrowRight events, so restarting cannot accidentally skip
    through the book.
    """
    if target < 1:
        raise ValueError("Reader page must be positive")
    if read_reader_page_number(page) == target:
        return True

    for attempt in range(1, attempts + 1):
        controls = list(page_number_controls(page))
        if not controls:
            print(f"    restart jump attempt {attempt}/{attempts}: page box not found")
            time.sleep(1.0)
            continue

        for control in controls:
            try:
                _, old_digest, _, old_box = capture_page(page)
                old_visual = visible_page_digest(page, old_box)
                if control.get_attribute("type") == "range":
                    minimum = int(control.get_attribute("min") or 0)
                    value = minimum + target - 1
                    maximum = control.get_attribute("max")
                    if maximum is not None and value > int(maximum):
                        return False
                    control.evaluate(
                        """(el, value) => {
                            el.value = String(value);
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""",
                        value,
                    )
                else:
                    control.fill(str(target))
                    control.press('Enter')
                print(
                    f"    restart jump attempt {attempt}/{attempts}: "
                    f"requested reader page {target}"
                )

                # A typed value is not confirmation: require a settled page
                # image as well as the requested reader number after submission.
                changed, _ = wait_for_page_change_once(
                    page, old_box, old_visual, old_digest, None,
                    timeout=NAV_CHANGE_TIMEOUT,
                )
                if changed and read_reader_page_number(page) == target:
                    blur_reader_focus(page)
                    return True

            except Exception:
                continue

        time.sleep(1.0)

    return False


def wait_for_page_change_once(
    page,
    old_box,
    old_visual_digest,
    old_page_digest,
    old_reader_number,
    timeout=NAV_CHANGE_TIMEOUT,
):
    """Wait for changed page pixels to remain stable through rendering.

    A counter can update before the image. It is supporting evidence only;
    capturing immediately on a counter change can save the previous page.
    """
    deadline = time.monotonic() + timeout
    stable_digest = None
    stable_since = None

    while time.monotonic() < deadline:
        try:
            visual = visible_page_digest(page, old_box)
            _, digest, _, _ = capture_page(page, verbose=False)
            current = read_reader_page_number(page)
            changed = (
                digest != old_page_digest
                and visual is not None
                and (old_visual_digest is None or visual != old_visual_digest)
            )
            counter_ready = (
                old_reader_number is None
                or current is None
                or current != old_reader_number
            )
            if changed and counter_ready:
                if digest != stable_digest:
                    stable_digest = digest
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= RENDER_SETTLE_SECONDS:
                    return True, "settled page image"
            else:
                stable_digest = stable_since = None
        except Exception:
            stable_digest = stable_since = None

        time.sleep(0.30)

    return False, None


def blur_reader_focus(page):
    """Blur text-entry controls so ArrowRight reaches the reader."""
    for frame in page.frames:
        try:
            frame.evaluate(
                """() => {
                    const el = document.activeElement;
                    if (el && el.blur) el.blur();
                }"""
            )
        except Exception:
            pass


def send_next_page(page):
    """Use the actual reader button without relying on top-level key focus."""
    for frame in page.frames:
        button = frame.get_by_role("button", name="Next Page", exact=True)
        if button.count() and button.first.is_visible():
            if not button.first.is_enabled():
                return None
            button.first.click()
            return "Next Page button"
    page.keyboard.press("ArrowRight")
    return "ArrowRight"


def advance_page(page, old_box, old_page_digest):
    """Advance one page, with many checks but no blind double-skip retries.

    Send one navigation action, then run several observation rounds.
    Another action is sent only if a readable page-number control positively
    confirms that the reader is STILL on the old page.
    """
    blur_reader_focus(page)
    old_visual_digest = visible_page_digest(page, old_box)
    if old_visual_digest is None:
        print("    could not establish the visible page rectangle")
        return False

    old_reader_number = read_reader_page_number(page)

    for key_attempt in range(1, NAV_KEY_ATTEMPTS + 1):
        blur_reader_focus(page)

        try:
            action = send_next_page(page)
            if action is None:
                print("    Next Page button is disabled")
                return False
        except Exception as exc:
            print(f"    Next-page navigation failed: {exc}")
            return False

        print(
            f"    {action} attempt {key_attempt}/{NAV_KEY_ATTEMPTS}; "
            f"checking for the new page..."
        )

        for observe_round in range(1, NAV_OBSERVE_ROUNDS + 1):
            changed, reason = wait_for_page_change_once(
                page,
                old_box,
                old_visual_digest,
                old_page_digest,
                old_reader_number,
                timeout=NAV_CHANGE_TIMEOUT,
            )
            if changed:
                print(
                    f"    advanced with {action} "
                    f"(confirmed by {reason}, check {observe_round}/{NAV_OBSERVE_ROUNDS})"
                )
                return True

            print(
                f"    no change confirmed yet "
                f"(check {observe_round}/{NAV_OBSERVE_ROUNDS})"
            )

        # Never blindly press ArrowRight again. Only retry the key if Google's
        # own readable page counter proves that we are still on the same page.
        if old_reader_number is None:
            print(
                "    could not safely verify the reader stayed on the old page; "
                "not sending another navigation action"
            )
            return False

        current = read_reader_page_number(page)
        if current is None:
            print(
                "    page counter became unreadable; stopping to avoid skipping a page"
            )
            return False

        if current != old_reader_number:
            print(
                f"    reader counter moved from {old_reader_number} to {current}; "
                "but the new image did not settle; stopping capture"
            )
            return False

        if key_attempt < NAV_KEY_ATTEMPTS:
            print(
                f"    reader counter still says {current}; retrying next-page navigation"
            )
            time.sleep(0.75)

    print(
        f"    reader counter stayed at {old_reader_number} after "
        f"{NAV_KEY_ATTEMPTS} navigation attempts"
    )
    return False

def write_png(path, data):
    path.write_bytes(data)
    return path


def existing_page_images(output_dir):
    """Return existing PNGs in filename order.

    Resume numbering deliberately uses the *count* of PNG files, matching the
    original script's logic: len(existing) + 1.
    """
    return sorted(output_dir.glob("*.png"))


def numbered_existing_pages(output_dir):
    """Return numeric PNG filenames in numeric order for PDF assembly."""
    pages = []
    for path in output_dir.glob("*.png"):
        try:
            number = int(path.stem)
        except ValueError:
            continue
        pages.append((number, path))
    return sorted(pages)


def remove_last_page(output_dir):
    """Discard the trailing reader-completion screen."""
    pages = numbered_existing_pages(output_dir)
    if not pages:
        return False

    last_page = pages[-1][1]
    last_page.unlink()
    print(f"Removed final capture: {last_page}")
    return True


def book_id(value):
    """Validate a Google Books volume ID for use in URLs and output names."""
    value = value.strip()
    if not BOOK_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "book ID must contain only letters, numbers, underscores, or hyphens"
        )
    return value


def reader_url(book):
    """Build the Google Play Books reader URL for a volume ID."""
    return f"{READER_URL}?{urlencode({'id': book})}"


def output_paths(book, output_dir=None, output_pdf=None):
    """Return isolated default output paths for a book capture."""
    return (
        output_dir or Path(f"{book}-pages"),
        output_pdf or Path(f"{book}.pdf"),
    )


def prepare_reader_for_capture(page, target, prompt=input):
    """Position and verify the first page, prompting only for manual recovery."""
    print(f"Automatically jumping to reader page {target}...")
    if jump_to_reader_page(page, target):
        try:
            capture_page(page, verbose=False)
        except Exception as exc:
            print(f"Could not verify the rendered page: {exc}")
        else:
            print(f"Reader positioned and verified at page {target}.")
            return
    else:
        print(f"Could not automatically confirm reader page {target}.")

    print("Position it manually before continuing.")
    prompt("Press Enter when ready to start capture... ")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Capture accessible Google Play Books pages into a PDF.",
        epilog=(
            "Example: %(prog)s ctmJB8IRXQcC --max-pages 50"
        ),
    )
    parser.add_argument(
        "book_id",
        type=book_id,
        help="Google Books volume ID, such as ctmJB8IRXQcC.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="PNG directory (default: <book-id>-pages).",
    )
    parser.add_argument(
        "--output-pdf",
        type=Path,
        default=None,
        help="PDF path (default: <book-id>.pdf).",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help=(
            "Output/capture number to start at. If omitted, use the original "
            "resume logic: number of existing PNGs + 1."
        ),
    )
    parser.add_argument(
        "--reader-page",
        type=int,
        default=None,
        help=(
            "One-based scan position (including front matter) to jump to on startup. If omitted, "
            "use the same number as the resume/output start page."
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional highest output page number to capture (default: no limit).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=WRITE_WORKERS,
        help="Background image-write threads (browser automation stays serial).",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    output_dir, output_pdf = output_paths(
        args.book_id,
        args.output_dir,
        args.output_pdf,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    existing = existing_page_images(output_dir)

    if args.start is not None:
        start_number = max(1, args.start)
    else:
        start_number = len(existing) + 1

    if existing:
        print(
            f"Found {len(existing)} existing PNG(s) in {output_dir}; "
            f"capture numbering starts at {start_number}."
        )

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            viewport={"width": 1800, "height": 2200},
            device_scale_factor=2,
        )

        pages = context.pages
        page = pages[0] if pages else context.new_page()

        page.goto(
            reader_url(args.book_id),
            wait_until="domcontentloaded",
            timeout=60000,
        )

        time.sleep(2)

        reader_page = (
            max(1, args.reader_page)
            if args.reader_page is not None
            else start_number
        )

        print()
        print("Google Play Books is open.")
        print(f"Book ID: {args.book_id}")
        print("Use SINGLE-PAGE mode and make sure no menu covers the page.")
        print(
            f"The first captured file will be "
            f"{output_dir / f'{start_number:04d}.png'}"
        )

        prepare_reader_for_capture(page, reader_page)

        seen = set()
        # Include existing hashes so a resumed run can detect accidental overlap.
        for path in existing:
            try:
                seen.add(hashlib.sha256(path.read_bytes()).hexdigest())
            except Exception:
                pass

        pending_writes = []

        with ThreadPoolExecutor(
            max_workers=max(1, args.workers),
            thread_name_prefix="page-writer",
        ) as pool:
            n = start_number
            while args.max_pages is None or n <= args.max_pages:
                png, digest, tag, box = capture_page(page, verbose=False)

                if digest in seen:
                    print(
                        f"{n:04d}: current reader page was already captured; "
                        "advancing without consuming an output number."
                    )
                    if not advance_page(page, box, digest):
                        print()
                        print(
                            "Could not advance past the duplicate page. "
                            "Stopping without renumbering later pages."
                        )
                        break
                    continue

                filename = output_dir / f"{n:04d}.png"
                pending_writes.append(
                    pool.submit(write_png, filename, png)
                )
                seen.add(digest)

                if box:
                    print(
                        f"{n:04d}: {tag} "
                        f"{box['width']:.0f}×{box['height']:.0f} "
                        f"→ {filename}"
                    )
                else:
                    print(f"{n:04d}: → {filename}")

                if args.max_pages is not None and n >= args.max_pages:
                    break

                if not advance_page(page, box, digest):
                    print()
                    print(
                        f"Could not advance after capture {n}. "
                        "Stopping without assuming that this is the book's end."
                    )
                    break

                n += 1

            # Surface any background disk-write exception before PDF creation.
            for future in pending_writes:
                future.result()

        print()
        remove_last_page(output_dir)
        context.close()

    all_pages = [p for _, p in numbered_existing_pages(output_dir)]
    if not all_pages:
        raise RuntimeError("No page images were saved.")

    print(f"\nCombining {len(all_pages)} pages...")
    with output_pdf.open("wb") as f:
        f.write(img2pdf.convert([str(x) for x in all_pages]))

    print(f"Created: {output_pdf}")
    print(f"Pages:   {len(all_pages)}")


if __name__ == "__main__":
    main()
