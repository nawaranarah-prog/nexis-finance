"""The Nexis Pulse score: how favourable recent discussions about an asset are, from 1 to 100.

It summarises the sentiment people expressed. It is not a probability that the price rises, not a price
prediction and not a recommendation. Swap :func:`score` for a better model later; callers only rely on
the returned shape.
"""

from __future__ import annotations

from typing import Any

SENTIMENTS = ("bullish", "neutral", "bearish")
# Fewer classified discussions than this and no score is shown.
MIN_DISCUSSIONS = 3
_WEIGHT = {"bullish": 1.0, "neutral": 0.5, "bearish": 0.0}
BANDS = (
    (20, "Extremely unfavorable"),
    (40, "Unfavorable"),
    (60, "Neutral / mixed"),
    (80, "Favorable"),
    (100, "Extremely favorable"),
)


def label(value: int) -> str:
    return next(name for top, name in BANDS if value <= top)


def score(counts: dict[str, int]) -> dict[str, Any]:
    """``counts`` maps bullish/neutral/bearish to how many discussions expressed it.

    Each discussion contributes 1 (bullish), 0.5 (neutral) or 0 (bearish); the mean is mapped onto 1–100.
    """
    n = sum(counts.get(s, 0) for s in SENTIMENTS)
    if n < MIN_DISCUSSIONS:
        return {"available": False, "value": None, "label": None, "basis": n, "minimum": MIN_DISCUSSIONS}
    mean = sum(_WEIGHT[s] * counts.get(s, 0) for s in SENTIMENTS) / n
    value = max(1, min(100, round(1 + 99 * mean)))
    return {"available": True, "value": value, "label": label(value), "basis": n, "minimum": MIN_DISCUSSIONS}
