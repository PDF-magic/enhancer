"""Conservatively retain source spellings when an AI proposes small word edits."""

from difflib import SequenceMatcher
import re


WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def spelling_distance(left: str, right: str) -> int:
    """Levenshtein distance, capped at three for this preservation policy."""
    if abs(len(left) - len(right)) > 2:
        return 3
    row = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        following = [i]
        for j, other in enumerate(right, 1):
            following.append(min(following[-1] + 1, row[j] + 1, row[j - 1] + (char != other)))
        row = following
    return min(row[-1], 3)


def preserve_source_spellings(source: str, proposed: str) -> tuple[str, list[dict[str, str]]]:
    """Restore small spelling substitutions without trusting model confidence.

    This deliberately also retains small genuine recognition errors. Without
    independent evidence, a near-spelling change cannot distinguish those from
    a printed typo. Punctuation, case, hyphen joining, and larger repairs remain
    available to the reviewer.
    """
    original = list(WORD.finditer(source))
    reviewed = list(WORD.finditer(proposed))
    before = [word.group().casefold() for word in original]
    after = [word.group().casefold() for word in reviewed]
    edits = []
    for kind, start, end, new_start, new_end in SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        if kind != "replace":
            continue
        # Match close spellings inside a changed run even when the model also
        # removes a scan mark or adds a missing word in that same run.
        used = set()
        for index in range(start, end):
            word = before[index]
            if len(word) < 3:
                continue
            limit = 1 if len(word) == 3 else 2
            candidates = []
            for other in range(new_start, new_end):
                if other in used or len(after[other]) < 3:
                    continue
                distance = spelling_distance(word, after[other])
                if 0 < distance <= limit:
                    candidates.append((distance, other))
            if not candidates:
                continue
            _, other = min(candidates)
            used.add(other)
            edits.append((reviewed[other].start(), reviewed[other].end(), original[index].group(), reviewed[other].group()))
    result = proposed
    for start, end, replacement, _ in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result, [{"source": source, "proposed": target} for _, _, source, target in edits]
