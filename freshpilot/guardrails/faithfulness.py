"""GRD-3: numeric faithfulness — every number in a narrative or reason must match
a value from the run's tool outputs / calculations (0.5% tolerance) or be flagged.
"""

from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"([-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)(\s*%)?")
_DEFAULT_TOLERANCE = 0.005
_WORDS = dict(zip(
    ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
     "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety").split(),
    [*range(20), *range(20, 100, 10)], strict=True,
))
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}
_WORD_RE = re.compile(r"\b(?:" + "|".join([*_WORDS, *_SCALES]) + r")\b", re.IGNORECASE)


def normalize_number_words(text: str) -> str:
    """Normalize English integer phrases before applying the same numeric checks."""
    matches = list(_WORD_RE.finditer(text))
    groups: list[list[re.Match[str]]] = []
    for match in matches:
        if groups and re.fullmatch(r"[\s-]+(?:and\s+)?", text[groups[-1][-1].end():match.start()]):
            groups[-1].append(match)
        else:
            groups.append([match])
    for group in reversed(groups):
        total = current = 0
        for match in group:
            word = match.group().lower()
            if word in _WORDS:
                current += _WORDS[word]
            elif word == "hundred":
                current = max(1, current) * 100
            else:
                total += max(1, current) * _SCALES[word]
                current = 0
        text = text[:group[0].start()] + str(total + current) + text[group[-1].end():]
    return text


def extract_numbers(text: str) -> list[tuple[float, bool]]:
    """Every numeric literal in free text, as (value, was_followed_by_percent_sign).

    Tolerant of the unicode minus sign (an LLM writing "−18 to −24%" is common),
    ranges, units ("48 lb"), and rates ("15.2 lb/day").
    """
    normalized = normalize_number_words(text).replace("−", "-")
    out = []
    for match in _NUMBER_RE.finditer(normalized):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        out.append((value, match.group(2) is not None))
    return out


def _close(a: float, b: float, tolerance: float) -> bool:
    if b == 0:
        return abs(a - b) < 1e-9
    return abs(a - b) / abs(b) <= tolerance


def is_faithful(
    text: str, known_values: list[float], tolerance: float = _DEFAULT_TOLERANCE
) -> tuple[bool, list[float]]:
    """A percent-suffixed number is checked both as written and divided by 100 --
    text may say "26%" while the underlying value is stored as the fraction 0.26,
    or vice versa; either representation matching counts as faithful.
    """
    unmatched = []
    for value, is_percent in extract_numbers(text):
        candidates = [value, value / 100] if is_percent else [value]
        if not any(_close(c, k, tolerance) for c in candidates for k in known_values):
            unmatched.append(value)
    return len(unmatched) == 0, unmatched
