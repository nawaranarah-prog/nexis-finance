"""How Nexis Pulse reads evidence and turns it into a score. Deterministic and reproducible.

**Per item** (a headline, a filing, a market-data reading):

* sentiment ``s`` in [-1, 1]: for news, a finance word list (:func:`lexicon`) unless an AI classification was
  stored for the item, in which case that stored label is used (positive = +0.7, negative = -0.7); for market
  data, the size of the move (a 4% day or a 15% month counts as fully positive/negative); filings carry no tone;
* topics: keyword rules on the headline and summary (plus stored AI topics);
* weight ``w = source weight × 0.5 ** (age in days / 7)`` — news counts fully, market data 0.6, filings 0 (they
  shape themes and events, not tone); evidence older than 30 days is not used.

**Score**: ``50 + 49.5 × Σ(w·s) / (Σw + 2)``, rounded and kept within 1–100. The ``+ 2`` is two neutral
pseudo-items, so a handful of items can't produce an extreme score. Bands: 1–20 extremely unfavorable,
21–40 unfavorable, 41–60 neutral / mixed, 61–80 favorable, 81–100 extremely favorable.

**Coverage** counts discussion items (news and, once connected, social posts) in the window:
fewer than 3, or less than one fresh item's worth of weight → no score; under 6 items or one publisher →
*limited* ("early signal"); under 12 items or under 3 publishers → *developing*; otherwise *strong*.

The score measures the tone of recent market information about an asset. It is not a price prediction, a
probability of rising, an expected return or a recommendation.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services import pulse_score

WINDOW_DAYS = 30
HALF_LIFE_DAYS = 7.0
PRIOR_WEIGHT = 2.0
SOURCE_WEIGHT = {"news": 1.0, "social": 0.6, "market_data": 0.6, "filing": 0.0}
DISCUSSION_KINDS = ("news", "social")
MIN_ITEMS = 3
MIN_EFFECTIVE = 1.0
AI_STRENGTH = 0.7
LABEL_THRESHOLD = 0.2

BAND_KEYS = {
    "Extremely unfavorable": "extremely_unfavorable", "Unfavorable": "unfavorable", "Neutral / mixed": "mixed",
    "Favorable": "favorable", "Extremely favorable": "extremely_favorable",
}  # fmt: skip
COVERAGE_TEXT = {
    "none": "Not enough recent market discussion to calculate a reliable Pulse score.",
    "limited": "Early signal — based on limited recent evidence.",
    "developing": "Based on a developing set of recent market information.",
    "strong": "Based on a broad set of recent market information.",
}
DISCLAIMER = "Nexis Pulse measures the tone of current market discussion. It is not a price prediction."

# ------------------------------------------------------------------ word lists

_POS = [
    (r"beat(?:s|ing)?(?! down)", 1), (r"tops? (?:estimates|expectations|forecasts?)", 1), (r"surg(?:e|es|ed|ing)", 1),
    (r"soar(?:s|ed|ing)?", 1), (r"jump(?:s|ed|ing)?", 1), (r"rall(?:y|ies|ied|ying)", 1), (r"gain(?:s|ed|ing)?", 0.8),
    (r"ris(?:e|es|ing)|rose", 0.8), (r"climb(?:s|ed|ing)?", 0.8), (r"record (?:high|revenue|profit|sales|deliveries|quarter|earnings)", 1.2),
    (r"all-time high", 1), (r"strong(?:er|est)?", 0.8), (r"upgrad(?:e|es|ed)", 1.2),
    (r"rais(?:e|es|ed|ing) (?:its |full-year |annual )?(?:guidance|forecast|outlook|dividend|price target|target)", 1.2),
    (r"price target (?:raised|hike[sd]?|increase[sd]?)", 1), (r"outperform(?:s|ed|ing)?", 0.8), (r"bullish", 1),
    (r"boost(?:s|ed|ing)?", 0.8), (r"rebound(?:s|ed|ing)?", 0.8), (r"recover(?:s|ed|y|ing)", 0.6), (r"exceed(?:s|ed|ing)?", 1),
    (r"accelerat(?:e|es|ed|ing)", 0.8), (r"approv(?:al|ed|es)", 0.8), (r"wins?|won", 0.6), (r"buybacks?|repurchase", 0.6),
    (r"higher", 0.5), (r"optimis(?:m|tic)", 0.8), (r"expan(?:d|ds|ded|ding|sion)", 0.5), (r"partnership", 0.4),
    (r"tailwinds?", 0.8), (r"upbeat", 1), (r"robust", 0.8), (r"buy rating", 1), (r"overweight", 0.6), (r"momentum", 0.4),
]  # fmt: skip
_NEG = [
    (r"miss(?:es|ed|ing)?", 1), (r"falls?|falling|fell", 0.8), (r"drop(?:s|ped|ping)?", 0.8), (r"plung(?:e|es|ed|ing)", 1),
    (r"slump(?:s|ed|ing)?", 1), (r"tumbl(?:e|es|ed|ing)", 1), (r"sink(?:s|ing)?|sank", 1), (r"slid(?:e|es|ing)?", 0.8),
    (r"declin(?:e|es|ed|ing)", 0.8), (r"(?:cut|cuts|cutting|lower(?:s|ed)?|slash(?:es|ed)?) (?:its |full-year |annual )?(?:guidance|forecast|outlook|dividend|price target|target|jobs)", 1.2),
    (r"price target (?:cut|lowered|reduced)", 1), (r"downgrad(?:e|es|ed)", 1.2), (r"lawsuits?|sued|sues", 0.8),
    (r"probes?|investigat(?:ion|ions|es|ed|ing)", 0.8), (r"recalls?|recalled", 0.8), (r"warn(?:s|ed|ing)?", 0.8),
    (r"weak(?:er|est|ness)?", 0.8), (r"loss(?:es)?", 0.6), (r"bearish", 1), (r"concerns?|worr(?:y|ies|ied)", 0.6),
    (r"fears?", 0.6), (r"layoffs?|job cuts", 0.8), (r"delay(?:s|ed)?", 0.6), (r"halt(?:s|ed)?", 0.8), (r"fined?|penalt(?:y|ies)", 0.8),
    (r"sell-?offs?", 1), (r"crash(?:es|ed)?", 1), (r"slowdown|slow(?:s|ed|ing)", 0.6), (r"pressures?", 0.4), (r"headwinds?", 0.8),
    (r"overvalued|bubble", 0.8), (r"short sellers?", 0.6), (r"default(?:s|ed)?", 1), (r"sanctions?", 0.6), (r"record low", 1.2),
    (r"underperform(?:s|ed|ing)?", 0.8), (r"disappoint(?:s|ed|ing|ment)?", 1), (r"tariffs?", 0.4), (r"sell rating", 1),
    (r"underweight", 0.6), (r"bankrupt\w*", 1.2), (r"scandal", 1), (r"resign(?:s|ed|ation)?", 0.5),
]  # fmt: skip
_NEGATE = r"(?:not|no|never|fails? to|failed to|without)\s+(?:\w+\s+)?"
_POS_RX = [(re.compile(rf"\b{p}\b", re.I), w) for p, w in _POS]
_NEG_RX = [(re.compile(rf"\b{p}\b", re.I), w) for p, w in _NEG]
_NEGATE_RX = re.compile(rf"\b{_NEGATE}$", re.I)


def lexicon(text: str) -> dict[str, Any]:
    """Tone of a headline from finance word lists. Returns the score, label and the terms that drove it."""
    pos = neg = 0.0
    terms: list[str] = []
    for rx_list, sign in ((_POS_RX, 1), (_NEG_RX, -1)):
        for rx, w in rx_list:
            for m in rx.finditer(text):
                flipped = _NEGATE_RX.search(text[: m.start()]) is not None
                if (sign > 0) != flipped:
                    pos += w
                else:
                    neg += w
                terms.append(("-" if flipped else "") + m.group(0).lower())
    s = (pos - neg) / (pos + neg + 0.5)
    return {"score": round(s, 3), "label": label_of(s), "terms": terms[:8], "method": "lexicon"}


def label_of(s: float) -> str:
    return "positive" if s > LABEL_THRESHOLD else "negative" if s < -LABEL_THRESHOLD else "neutral"


# ------------------------------------------------------------------ topics

EXTRA_THEMES = {"analysts": "Analyst views", "deliveries": "Deliveries", "price_action": "Price action", "filings": "Company filings"}
_EXTRA_RX = [
    ("analysts", re.compile(r"\b(analysts?|price targets?|upgrad\w*|downgrad\w*|ratings?|overweight|underweight)\b", re.I)),
    ("deliveries", re.compile(r"\bdeliver(?:y|ies|ed)\b", re.I)),
]


def theme_label(key: str) -> str:
    from app.services.pulse import TOPICS

    return TOPICS.get(key) or EXTRA_THEMES.get(key) or key.replace("_", " ").capitalize()


def topics_for(text: str) -> list[str]:
    from app.services.events import _TOPICS

    found = [t for t, rx in _TOPICS if re.search(rx, text.lower())]
    found += [k for k, rx in _EXTRA_RX if rx.search(text)]
    return list(dict.fromkeys(found))[:5]


# ------------------------------------------------------------------ items


def item_reading(kind: str, title: str, summary: str | None, facts: dict[str, Any], stored: dict[str, Any] | None) -> dict[str, Any]:
    """Sentiment and topics for one item. A stored AI reading wins over the word list for news and social items."""
    text = f"{title}. {summary or ''}"
    if kind == "market_data":
        pct = float(facts.get("change_pct") or 0)
        scale, quiet = (4.0, 0.5) if facts.get("metric") == "1d" else (15.0, 2.0)
        s = 0.0 if abs(pct) < quiet else max(-1.0, min(1.0, pct / scale))
        return {"score": round(s, 3), "label": label_of(s), "topics": ["price_action"], "method": "market_data"}
    if kind == "filing":
        topics = ["filings"] + (["earnings"] if any("earnings" in i for i in facts.get("items") or []) or facts.get("form") in ("10-Q", "10-K") else [])
        return {"score": 0.0, "label": "neutral", "topics": topics, "method": "filing"}
    if stored and stored.get("method") == "ai" and stored.get("label") in ("positive", "neutral", "negative"):
        s = {"positive": AI_STRENGTH, "neutral": 0.0, "negative": -AI_STRENGTH}[stored["label"]]
        topics = list(dict.fromkeys([*(stored.get("topics") or []), *topics_for(text)]))[:5]
        return {**stored, "score": s, "topics": topics}
    r = lexicon(title)  # the headline is what the source chose to say; summaries are often boilerplate
    return {**r, "topics": topics_for(text)}


def weight(kind: str, published_at: datetime, now: datetime) -> float:
    age = max(0.0, (now - published_at).total_seconds() / 86400)
    if age > WINDOW_DAYS:
        return 0.0
    return SOURCE_WEIGHT.get(kind, 0.0) * 0.5 ** (age / HALF_LIFE_DAYS)


# ------------------------------------------------------------------ score


def score(items: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """``items``: dicts with kind, published_at, publisher, score, topics, id/key. Adds weight and contribution to each."""
    for it in items:
        it["weight"] = round(weight(it["kind"], it["published_at"], now), 4)
        it["recency"] = round(0.5 ** (max(0.0, (now - it["published_at"]).total_seconds() / 86400) / HALF_LIFE_DAYS), 4)
    wsum = sum(it["weight"] for it in items)
    denom = wsum + PRIOR_WEIGHT
    for it in items:
        it["contribution"] = round(49.5 * it["weight"] * it["score"] / denom, 2)
    disc = [it for it in items if it["kind"] in DISCUSSION_KINDS and it["weight"] > 0]
    publishers = {(it.get("publisher") or "").strip().lower() for it in disc if it.get("publisher")}
    effective = sum(it["recency"] for it in disc)
    n = len(disc)
    if n < MIN_ITEMS or effective < MIN_EFFECTIVE:
        coverage = "none"
    elif n < 6 or len(publishers) < 2:
        coverage = "limited"
    elif n < 12 or len(publishers) < 3:
        coverage = "developing"
    else:
        coverage = "strong"
    dist = {k: sum(1 for it in disc if it["label"] == k) for k in ("positive", "neutral", "negative")}
    base = {"coverage": coverage, "coverage_text": COVERAGE_TEXT[coverage], "items": n, "publishers": len(publishers),
            "effective_items": round(effective, 2), "distribution": dist, "weight_total": round(wsum, 3),
            "window_days": WINDOW_DAYS, "half_life_days": HALF_LIFE_DAYS, "prior_weight": PRIOR_WEIGHT}  # fmt: skip
    if coverage == "none":
        return {**base, "available": False, "value": None, "label": None, "sentiment": "insufficient"}
    value = max(1, min(100, round(50 + 49.5 * sum(it["weight"] * it["score"] for it in items) / denom)))
    name = pulse_score.label(value)
    return {**base, "available": True, "value": value, "label": name, "sentiment": BAND_KEYS[name]}


def themes(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Topics across the evidence: how many items, how much weight, and their net push on the score (in points)."""
    acc: dict[str, dict[str, Any]] = {}
    for it in items:
        if it["kind"] == "market_data" and it["weight"] == 0:
            continue
        for t in it.get("topics") or []:
            a = acc.setdefault(t, {"key": t, "label": theme_label(t), "count": 0, "weight": 0.0, "net": 0.0,
                                   "positive": 0, "negative": 0, "evidence": []})  # fmt: skip
            a["count"] += 1
            a["weight"] += it["weight"]
            a["net"] += it["contribution"]
            a["positive"] += it["label"] == "positive"
            a["negative"] += it["label"] == "negative"
            if it.get("ref") is not None and len(a["evidence"]) < 6:
                a["evidence"].append(it["ref"])
    out = sorted(acc.values(), key=lambda a: (-a["count"], -a["weight"]))
    for a in out:
        a["weight"], a["net"] = round(a["weight"], 3), round(a["net"], 2)
    return out[:limit]
