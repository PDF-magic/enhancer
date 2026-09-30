#!/usr/bin/env python3
"""Download and classify submissions from an SEC comment file index."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


SEC_ORIGIN = "https://www.sec.gov"
DEFAULT_USER_AGENT = "Your Name your@email.com"
MEETING_TYPE = "Meeting with SEC Officials"


@dataclass(frozen=True)
class Submission:
    url: str
    letter_type: str

    @property
    def filename(self) -> str:
        return Path(urlsplit(self.url).path).name

    @property
    def category(self) -> str:
        if self.letter_type.casefold() == MEETING_TYPE.casefold():
            return "meeting-memoranda"
        if Path(self.filename).suffix.lower() == ".pdf":
            return "pdfs"
        return "raw-html"


class CommentTableParser(HTMLParser):
    def __init__(self, index_url: str) -> None:
        super().__init__()
        self.index_url = index_url
        self.submissions: list[Submission] = []
        self.in_row = False
        self.cell_headers = ""
        self.letter_type_parts: list[str] = []
        self.row_links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag.lower() == "tr":
            self.in_row = True
            self.cell_headers = ""
            self.letter_type_parts = []
            self.row_links = []
        elif self.in_row and tag.lower() == "td":
            self.cell_headers = attributes.get("headers") or ""
        elif (
            self.in_row
            and tag.lower() == "a"
            and "view-nothing-table-column" in self.cell_headers
            and attributes.get("href")
        ):
            self.row_links.append(urljoin(self.index_url, attributes["href"]))

    def handle_data(self, data: str) -> None:
        if self.in_row and "comment-letter-type" in self.cell_headers:
            self.letter_type_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "td":
            self.cell_headers = ""
        elif tag.lower() == "tr" and self.in_row:
            letter_type = " ".join("".join(self.letter_type_parts).split())
            for url in self.row_links:
                if document_url(url) is not None:
                    self.submissions.append(Submission(document_url(url), letter_type))
            self.in_row = False


def request_bytes(url: str, user_agent: str) -> bytes:
    command = [
        "curl",
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "3",
        "--retry-all-errors",
        "--max-time",
        "60",
        "--user-agent",
        user_agent,
        url,
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as error:
        raise RuntimeError("curl is required but was not found") from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {message}" if message else ""
        raise RuntimeError(f"request failed: {url}{detail}") from error
    return result.stdout


def normalize_file_number(value: str) -> str:
    file_number = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", file_number):
        raise argparse.ArgumentTypeError(
            "expected an SEC file number such as S7-27-15 or SR-OCC-2025-801"
        )
    return file_number


def inferred_index_urls(file_number: str) -> list[str]:
    basename = file_number.replace("-", "")
    base = f"{SEC_ORIGIN}/comments/{file_number}/{basename}"
    return [f"{base}.shtml", f"{base}.htm", f"{base}.html"]


def document_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.netloc.lower() not in {"sec.gov", "www.sec.gov"}:
        return None
    if Path(parsed.path).suffix.lower() not in {".pdf", ".htm", ".html"}:
        return None
    return f"{SEC_ORIGIN}{parsed.path}"


def natural_sort_key(submission: Submission) -> list[tuple[int, int | str]]:
    parts = re.split(r"(\d+)", submission.filename.lower())
    return [(0, int(part)) if part.isdigit() else (1, part) for part in parts]


def find_index_url(
    file_number: str, requested_url: str | None, user_agent: str
) -> tuple[str, bytes]:
    candidates = [requested_url] if requested_url else inferred_index_urls(file_number)
    errors: list[str] = []
    for candidate in candidates:
        try:
            return candidate, request_bytes(candidate, user_agent)
        except RuntimeError as error:
            errors.append(str(error))
    raise RuntimeError("could not find the SEC comment index\n" + "\n".join(errors))


def discover_submissions(
    index_url: str, first_page: bytes, user_agent: str
) -> list[Submission]:
    discovered: dict[str, Submission] = {}
    page = 0
    page_content = first_page

    while True:
        parser = CommentTableParser(index_url)
        parser.feed(page_content.decode("utf-8", errors="replace"))
        new_submissions = {
            submission.url: submission
            for submission in parser.submissions
            if submission.url not in discovered
        }
        if not new_submissions:
            break

        discovered.update(new_submissions)
        page += 1
        separator = "&" if "?" in index_url else "?"
        page_url = f"{index_url}{separator}page={page}"
        page_content = request_bytes(page_url, user_agent)

    return sorted(discovered.values(), key=natural_sort_key)


def valid_existing_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if path.suffix.lower() != ".pdf":
        return True
    with path.open("rb") as file:
        return file.read(5) == b"%PDF-"


def download_submission(
    submission: Submission, output_dir: Path, user_agent: str, force: bool
) -> str:
    category_dir = output_dir / submission.category
    destination = category_dir / submission.filename
    if not force and valid_existing_file(destination):
        return "skipped"

    content = request_bytes(submission.url, user_agent)
    if destination.suffix.lower() == ".pdf" and not content.startswith(b"%PDF-"):
        raise RuntimeError(f"SEC response was not a PDF: {submission.url}")

    category_dir.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return "downloaded"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and classify submissions from an SEC comment file."
    )
    parser.add_argument(
        "file_number",
        type=normalize_file_number,
        help="SEC file number, such as S7-27-15",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output directory (default: FILE-NUMBER-comments)",
    )
    parser.add_argument(
        "--index-url",
        help="override the SEC comment index URL when it cannot be inferred",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="redownload files that already exist and appear valid",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list matching submissions and categories without downloading",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="seconds to wait between downloads (default: 0.2)",
    )
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT),
        help=(
            "identifying User-Agent sent to sec.gov; may also be set with "
            "SEC_USER_AGENT (include your name or organization and email address)"
        ),
    )
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if not args.user_agent.strip() or args.user_agent == DEFAULT_USER_AGENT:
        if not sys.stdin.isatty():
            parser.error(
                "set SEC_USER_AGENT or pass --user-agent when running non-interactively"
            )
        print("SEC requests require an identifying User-Agent.")
        args.user_agent = input("Name or organization and email address: ").strip()
        if not args.user_agent or args.user_agent == DEFAULT_USER_AGENT:
            parser.error("a non-default User-Agent is required")
    if args.output is None:
        args.output = Path(f"{args.file_number}-comments")
    return args


def main() -> int:
    args = parse_args()

    try:
        index_url, first_page = find_index_url(
            args.file_number, args.index_url, args.user_agent
        )
        submissions = discover_submissions(index_url, first_page, args.user_agent)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not submissions:
        print("error: no PDF or HTML submissions found", file=sys.stderr)
        return 1

    counts = {
        category: sum(item.category == category for item in submissions)
        for category in ("pdfs", "raw-html", "meeting-memoranda")
    }
    print(
        f"Found {len(submissions)} submission(s): "
        f"{counts['pdfs']} PDFs, {counts['raw-html']} raw HTML, "
        f"{counts['meeting-memoranda']} meeting memoranda."
    )
    if args.dry_run:
        for submission in submissions:
            print(f"[{submission.category}] {submission.url}")
        return 0

    downloaded = 0
    skipped = 0
    failed = 0

    for index, submission in enumerate(submissions, start=1):
        try:
            result = download_submission(
                submission, args.output, args.user_agent, args.force
            )
            if result == "downloaded":
                downloaded += 1
            else:
                skipped += 1
            print(
                f"[{index}/{len(submissions)}] {result}: "
                f"{submission.category}/{submission.filename}"
            )
        except (OSError, RuntimeError) as error:
            failed += 1
            print(
                f"[{index}/{len(submissions)}] failed: "
                f"{submission.filename}: {error}",
                file=sys.stderr,
            )

        if index != len(submissions) and args.delay:
            time.sleep(args.delay)

    print(f"Downloaded: {downloaded}; skipped: {skipped}; failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
