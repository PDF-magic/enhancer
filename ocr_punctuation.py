"""Evidence gates for ambiguous punctuation in image-reviewed OCR."""

from difflib import SequenceMatcher
import math
import re


WORD_TILDE = re.compile(r"(?<=[^\W\d_])~(?=[^\W\d_])")


def tilde_candidates(text: str) -> list[dict[str, object]]:
    """Offsets refer to the unmodified block text, using Unicode characters."""
    return [
        {"offset": match.start(),
         "context": text[max(0, match.start() - 30):match.end() + 30]}
        for match in WORD_TILDE.finditer(text)
    ]


def high_confidence(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.98 <= number <= 1


def tilde_change_warning(original: str, reviewed: str, decision: dict) -> str | None:
    """Permit only individually evidenced word-internal tilde-to-hyphen edits.

    Unchanged mathematical, URL, code, and prose tildes need no evidence.
    Other text edits remain subject to the tagger's existing review rules.
    """
    if original == reviewed or ("~" not in original and "~" not in reviewed):
        return None
    candidates = {item["offset"] for item in tilde_candidates(original)}
    corrections = decision.get("tilde_corrections", [])
    approved = set()
    if isinstance(corrections, list):
        for item in corrections:
            if not isinstance(item, dict):
                continue
            offset = item.get("offset")
            evidence = item.get("visual_evidence")
            if (type(offset) is int and offset in candidates
                    and high_confidence(item.get("confidence"))
                    and isinstance(evidence, str) and evidence.strip()):
                approved.add(offset)

    # Replacing approved characters first keeps alignment stable even when
    # the reviewer also fixes adjacent OCR characters in the same word.
    expected = "".join("-" if index in approved else char
                       for index, char in enumerate(original))
    for kind, start, end, other_start, other_end in SequenceMatcher(
            None, expected, reviewed, autojunk=False).get_opcodes():
        if kind != "equal" and ("~" in expected[start:end]
                                or "~" in reviewed[other_start:other_end]
                                or any(start <= offset < end for offset in approved)):
            return "tilde text change rejected without per-character visual evidence at 0.98 confidence"
    if approved and not high_confidence(decision.get("confidence")):
        return "tilde text change rejected below 0.98 block confidence"
    return None
