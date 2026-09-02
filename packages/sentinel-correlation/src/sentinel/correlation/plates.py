"""Confusion-weighted plate matching.

OCR makes predictable mistakes on Indian plates: 0 for D or Q, 1 for I, 8 for
B, 5 for S, 2 for Z, and back the other way. A plain edit distance treats
'GJ01DB1234' and 'GJ01OB1234' as one character apart, which is right, and
'GJ01DB1234' and 'GJ01DB1235' as one character apart too, which is badly
wrong -- the first is the same plate misread, the second is a different
vehicle.

So substitutions between known-confusable characters cost a fraction of a
normal substitution. A misread plate still surfaces the right vehicle; a
genuinely different plate does not.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: Symmetric confusion classes. Characters within a class are cheap to swap.
CONFUSION_CLASSES = [
    {"0", "O", "D", "Q"},
    {"1", "I", "L", "7"},
    {"8", "B"},
    {"5", "S"},
    {"2", "Z"},
    {"6", "G"},
    {"4", "A"},
    {"U", "V"},
    {"M", "N"},
]

CONFUSION_COST = 0.25      # a confusable swap
SUBSTITUTION_COST = 1.0    # any other swap
INDEL_COST = 1.0


@lru_cache(maxsize=1)
def _confusion_map() -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for group in CONFUSION_CLASSES:
        for char in group:
            mapping.setdefault(char, set()).update(group - {char})
    return mapping


def normalise(plate: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", plate.upper())


def substitution_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return CONFUSION_COST if b in _confusion_map().get(a, ()) else SUBSTITUTION_COST


def distance(a: str, b: str) -> float:
    """Weighted Levenshtein distance between two normalised plates."""
    a, b = normalise(a), normalise(b)
    if not a:
        return len(b) * INDEL_COST
    if not b:
        return len(a) * INDEL_COST

    previous = [j * INDEL_COST for j in range(len(b) + 1)]
    for i, ca in enumerate(a, start=1):
        current = [i * INDEL_COST]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + INDEL_COST,
                    current[j - 1] + INDEL_COST,
                    previous[j - 1] + substitution_cost(ca, cb),
                )
            )
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """1.0 for identical, 0.0 for nothing in common."""
    a, b = normalise(a), normalise(b)
    longest = max(len(a), len(b))
    return max(0.0, 1.0 - distance(a, b) / longest) if longest else 0.0


def matches(target: str, candidate: str, threshold: float = 0.82) -> bool:
    return similarity(target, candidate) >= threshold


def trigram_prefilter(plate: str) -> str:
    """A LIKE pattern narrow enough for the trigram index to help.

    The state code and the first digit are the characters OCR gets right most
    often. Anchoring on them cuts the candidate set hard before the weighted
    distance runs, which is expensive.
    """
    cleaned = normalise(plate)
    return f"%{cleaned[:2]}%{cleaned[-4:]}%" if len(cleaned) >= 6 else f"%{cleaned}%"
