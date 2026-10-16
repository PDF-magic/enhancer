"""Conservative OCR cleanup for numeric days in fully specified written dates.

Never infer dates from a free-standing Roman numeral. A correction must have
an explicit month, comma, four-digit year and a valid resulting calendar day.
"""

from datetime import date
import re


MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

MONTH_DAY_YEAR = re.compile(
    r"(?<![A-Za-z])(?P<month>"
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?[ \t]+(?P<day>[0-9Il|]{1,2}),[ \t]*(?P<year>[0-9]{4})(?![0-9A-Za-z])",
    re.IGNORECASE,
)

OCR_ONE = str.maketrans({"I": "1", "l": "1", "|": "1"})


def normalize_ocr_dates(text: str) -> tuple[str, list[dict[str, object]]]:
    """Correct I/l/| used as a calendar day digit; return an audit of edits.

    Months and years must already be legible, the day must be one or two
    characters, and its corrected value must describe a real calendar date.
    The offsets are Unicode offsets in the *original* paragraph.
    """
    edits: list[dict[str, object]] = []
    replacements: list[tuple[int, int, str]] = []
    for match in MONTH_DAY_YEAR.finditer(text):
        seen = match.group("day")
        if not any(char in "Il|" for char in seen):
            continue
        corrected = seen.translate(OCR_ONE)
        try:
            date(int(match.group("year")),
                 MONTH_NUMBERS[match.group("month")[:3].lower()],
                 int(corrected))
        except ValueError:
            continue
        replacements.append((match.start("day"), match.end("day"), corrected))
        edits.append({
            "offset": match.start("day"),
            "original": match.group(),
            "corrected": (match.group()[:match.start("day") - match.start()]
                          + corrected + match.group()[match.end("day") - match.start():]),
        })
    if not edits:
        return text, []
    output = text
    for start, end, day in reversed(replacements):
        output = output[:start] + day + output[end:]
    return output, edits
