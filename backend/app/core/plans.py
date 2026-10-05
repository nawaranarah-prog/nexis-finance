"""Nexis plans: the ONE place prices and limits are defined.

Everything else reads from here — entitlement checks, the pricing page (served by ``GET /api/billing/plans``),
Settings → Billing, and the check that the Stripe prices match. Change a number here and it changes everywhere.

Monthly allowances reset on the 1st of each calendar month (UTC) for every plan — a yearly subscriber gets 600
Advisor messages each month, not 600 a year. ``my_nexis_assets`` is a capacity, not a counter: removing an asset
frees its slot.
"""

from __future__ import annotations

from typing import Any

# What a visitor without an account may do (per IP address, rolling 24 hours).
ANONYMOUS: dict[str, int] = {"advisor_daily": 3}

LIMITS: dict[str, dict[str, int]] = {
    "free": {"advisor": 20, "my_nexis_assets": 3, "pulse_discussions": 3, "pulse_comments": 10},
    "pro": {"advisor": 600, "my_nexis_assets": 100, "pulse_discussions": 50, "pulse_comments": 100},
}

# How each limit is described to members. ``unit`` reads after the number ("20 AI Advisor messages").
FEATURES: dict[str, dict[str, Any]] = {
    "advisor": {"label": "AI Advisor", "unit": "AI Advisor messages", "monthly": True,
                "action": "ask the AI Advisor", "value": "The Advisor checks live prices, news, analyst ratings and valuations before every answer."},
    "my_nexis_assets": {"label": "My Nexis", "unit": "tracked assets", "monthly": False,
                        "action": "track another asset in My Nexis", "value": "Nexis follows the developments and Pulse discussions for every asset you track."},
    "pulse_discussions": {"label": "Discussions", "unit": "Pulse discussions", "monthly": True,
                          "action": "start another Pulse discussion", "value": "Start debates about the assets and themes you follow, anonymously."},
    "pulse_comments": {"label": "Comments", "unit": "Pulse comments and replies", "monthly": True,
                       "action": "reply in Pulse", "value": "Take part in more debates, anonymously."},
}  # fmt: skip

# The two Nexis Pro prices. Stripe holds the real Price objects (ids come from STRIPE_PRO_*_PRICE_ID); the billing
# code refuses to sell a Stripe price whose amount, currency or interval differs from these.
PRICES: dict[str, dict[str, Any]] = {
    "pro_monthly": {"amount_cents": 999, "currency": "usd", "interval": "month", "lookup_key": "nexis_pro_monthly", "nickname": "Nexis Pro Monthly"},
    "pro_yearly": {"amount_cents": 9900, "currency": "usd", "interval": "year", "lookup_key": "nexis_pro_yearly", "nickname": "Nexis Pro Yearly"},
}  # fmt: skip
PRODUCT = {"id": "nexis_pro", "name": "Nexis Pro"}


def yearly_value() -> dict[str, Any]:
    """The yearly plan compared with twelve monthly payments, computed from the prices above (never typed in)."""
    m, y = PRICES["pro_monthly"]["amount_cents"], PRICES["pro_yearly"]["amount_cents"]
    twelve = m * 12
    return {
        "per_month_cents": round(y / 12),  # 825 → $8.25
        "twelve_monthly_cents": twelve,  # 11988 → $119.88
        "savings_cents": twelve - y,  # 2088 → $20.88
        "savings_pct": round(100 * (twelve - y) / twelve),  # 17
    }


def public() -> dict[str, Any]:
    """Everything the browser needs to describe the plans (no secrets, no Stripe ids)."""
    return {
        "anonymous": dict(ANONYMOUS),
        "limits": {k: dict(v) for k, v in LIMITS.items()},
        "features": {k: {kk: vv for kk, vv in v.items() if kk in ("label", "unit", "monthly")} for k, v in FEATURES.items()},
        "prices": {k: {"amount": v["amount_cents"] / 100, "currency": v["currency"].upper(), "interval": v["interval"]} for k, v in PRICES.items()},
        "yearly_value": yearly_value(),
    }
