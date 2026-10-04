"""Automatic checks on Pulse posts for the financial harms the Community Guidelines prohibit.

These checks never decide on their own that an opinion is wrong. Strong, negative or controversial views pass
untouched. What they look for is the *shape* of known abuse: guaranteed returns, claimed inside information,
coordinated buying or selling, impersonation, and off-platform solicitation.

Each rule has a severity:

* ``review`` — the post is published and queued for a moderator (flag → review → action).
* ``hold``   — the post is kept out of public view until a moderator approves it. Only used for patterns that are
  almost never legitimate in a discussion (soliciting money or contact off-platform together with return promises,
  sharing a crypto wallet address, coordinating a pump).

The author is told when their post is waiting for review. Nothing is deleted automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    severity: str  # review | hold
    pattern: re.Pattern[str]


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.I)


RULES: tuple[Rule, ...] = (
    Rule("guaranteed_returns", "Claims guaranteed returns", "review", _rx(
        r"\bguarantee[ds]?\b.{0,40}\b(returns?|profits?|gains?|income|\d+\s*%|\d+x)\b",
        r"\b(risk[- ]?free|can'?t lose|cannot lose|zero risk|no risk)\b.{0,40}\b(returns?|profits?|gains?|trade|investment|money)\b",
        r"\b(100|1000)\s*%\s*(sure|certain|guaranteed)\b",
        r"\bdouble your (money|investment|capital)\b",
    )),
    Rule("insider_claim", "Claims inside information", "review", _rx(
        r"\b(insider|inside) (info|information|tip|knowledge|source)\b",
        r"\bmy (friend|cousin|brother|contact|source)s? (at|in|inside) .{0,40}\b(told|says|said|confirmed)\b",
        r"\bbefore (it'?s|it is) (announced|public)\b",
        r"\bnon[- ]public information\b",
    )),
    Rule("coordination", "Coordinating trades", "hold", _rx(
        r"\b(let'?s|we all|everyone|everybody) (all )?(buy|sell|dump|short|pump)\b.{0,40}\b(at|on|together|now|tomorrow|same time)\b",
        r"\bpump (it|this|and dump)\b",
        r"\bpump\s*(&|and)\s*dump\b.{0,30}\b(join|now|today|tomorrow)\b",
        r"\b(coordinated|mass) (buy|sell|buying|selling)\b",
    )),
    Rule("impersonation", "May impersonate a company or professional", "review", _rx(
        r"\b(i am|i'm|this is) (the )?(ceo|cfo|founder|chairman|an? (official )?(spokesperson|representative)) (of|at|for)\b",
        r"\b(official|verified) (account|statement|announcement) (of|from)\b",
        r"\bas an? (analyst|portfolio manager|trader|banker) at (goldman|morgan|jp ?morgan|citi|ubs|blackrock|fidelity|hsbc|barclays)\b",
    )),
    Rule("solicitation", "Solicits contact or money off Nexis", "hold", _rx(
        r"\b(whats\s?app|telegram|signal group|discord\.gg|t\.me/)\b",
        r"\b(dm|message|contact|text) me\b.{0,40}\b(signals?|tips?|returns?|profits?|group|invest|portfolio|account)\b",
        r"\b(join|subscribe to) my (vip|premium|paid|private) (group|channel|signals?)\b",
        r"\b(send|deposit|transfer) (me )?(usdt|btc|eth|crypto|money|funds)\b",
        r"\b(0x[a-f0-9]{40}|bc1[a-z0-9]{25,60}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b",
    )),
    Rule("promotion", "Suspicious promotion", "review", _rx(
        r"\b(next|the next) (100|1000)x\b",
        r"\b(moon|mooning) (soon|guaranteed|tomorrow)\b",
        r"\b(bit\.ly|tinyurl\.com|goo\.gl|cutt\.ly|rb\.gy|shorturl\.at)/",
        r"\b(referral|promo|invite) (code|link)\b",
    )),
)  # fmt: skip

LABELS = {r.key: r.label for r in RULES}


def check(text: str) -> list[dict[str, str]]:
    """The rules this text matches, most severe first. Empty when nothing matched."""
    hits = [{"key": r.key, "label": r.label, "severity": r.severity} for r in RULES if r.pattern.search(text or "")]
    return sorted(hits, key=lambda h: h["severity"] != "hold")


def verdict(flags: list[dict[str, str]]) -> str:
    """``visible`` (maybe flagged for review) or ``held``."""
    return "held" if any(f["severity"] == "hold" for f in flags) else "visible"
