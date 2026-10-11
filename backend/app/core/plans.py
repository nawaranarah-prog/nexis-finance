"""Nexis plans: the ONE place tiers, prices, limits and paid capabilities are defined.

Everything else reads from here — entitlement checks, the pricing page (served by ``GET /api/billing/plans``),
Settings → Billing, the Help Agent's account answers, and the check that the Stripe prices match. Change a number
here and it changes everywhere.

* ``investments`` — distinct assets held in My Nexis (a capacity, not a counter). Archived investments don't count.
* ``watchlist`` — followed tickers. The same anti-abuse cap on every plan; it isn't a paid feature.
* ``advisor`` — AI Advisor messages per calendar month (UTC), reset on the 1st for every plan.
* Pulse (reading, posting, replying, voting) is free and unmetered on every plan; only anti-spam rate limits apply.
"""

from __future__ import annotations

from typing import Any

TIERS: tuple[str, ...] = ("free", "plus", "pro")  # lowest to highest
PAID_TIERS: tuple[str, ...] = ("plus", "pro")

# What a visitor without an account may do (per IP address, rolling 24 hours).
ANONYMOUS: dict[str, int] = {"advisor_daily": 0, "help_ai_daily": 0}  # visitors: no AI; a free account unlocks it

LIMITS: dict[str, dict[str, int]] = {
    "free": {"investments": 1, "watchlist": 50, "advisor": 20},
    "plus": {"investments": 10, "watchlist": 50, "advisor": 200},
    "pro": {"investments": 20, "watchlist": 50, "advisor": 600},
}

# Help Agent AI answers per signed-in account per day (product support; FAQ search itself is never limited).
HELP_AI_DAILY = 40

# How each limit is described to members. ``unit`` reads after the number ("20 AI Advisor messages").
FEATURES: dict[str, dict[str, Any]] = {
    "investments": {"label": "Investments", "unit": "active investments", "monthly": False,
                    "action": "add another investment", "value": "Track more of your portfolio in My Nexis."},
    "watchlist": {"label": "Watchlist", "unit": "watchlist tickers", "monthly": False,
                  "action": "follow another ticker", "value": "Follow more tickers."},
    "advisor": {"label": "AI Advisor", "unit": "AI Advisor messages", "monthly": True,
                "action": "ask the AI Advisor", "value": "The Advisor checks live prices, news, analyst ratings and valuations before every answer."},
}  # fmt: skip

# Paid capabilities beyond limits. Each one is a real, server-enforced feature (see ``entitlements.require``).
CAPABILITIES: dict[str, dict[str, Any]] = {
    "portfolio_insights": {"tiers": ("plus", "pro"), "label": "Portfolio insights",
                           "summary": "Concentration, currency exposure, diversification and the positions driving your gains and losses."},
    "portfolio_risk": {"tiers": ("pro",), "label": "Portfolio risk analytics",
                       "summary": "One-year volatility, drawdown, beta against the S&P 500 and correlations between your holdings."},
}  # fmt: skip

# Monthly prices in UAE dirhams. Stripe holds the real Price objects (ids from STRIPE_PLUS_MONTHLY_PRICE_ID and
# STRIPE_PRO_MONTHLY_PRICE_ID); the billing code refuses to sell a Stripe price whose amount, currency or interval
# differs from these.
PRICES: dict[str, dict[str, Any]] = {
    "plus_monthly": {"tier": "plus", "amount_cents": 2900, "currency": "aed", "interval": "month",
                     "lookup_key": "nexis_plus_monthly_aed", "nickname": "Nexis Plus Monthly (AED)"},
    "pro_monthly": {"tier": "pro", "amount_cents": 6900, "currency": "aed", "interval": "month",
                    "lookup_key": "nexis_pro_monthly_aed", "nickname": "Nexis Pro Monthly (AED)"},
    # Annual prices (owner's final prices, 2026-10-11): one upfront payment for 12 months. They go on sale only once their
    # Stripe price ids are set and verified (STRIPE_PLUS_YEARLY_AED_PRICE_ID / STRIPE_PRO_YEARLY_AED_PRICE_ID).
    # The lookup keys carry the amount, so a Stripe price made for an earlier amount (AED 290 / 990) is never reused.
    "plus_yearly": {"tier": "plus", "amount_cents": 22900, "currency": "aed", "interval": "year",
                    "lookup_key": "nexis_plus_yearly_aed_229", "nickname": "Nexis Plus Annual (AED 229)"},
    "pro_yearly": {"tier": "pro", "amount_cents": 59900, "currency": "aed", "interval": "year",
                   "lookup_key": "nexis_pro_yearly_aed_599", "nickname": "Nexis Pro Annual (AED 599)"},
}  # fmt: skip
PRODUCTS: dict[str, dict[str, str]] = {
    "plus": {"id": "nexis_plus", "name": "Nexis Plus"},
    "pro": {"id": "nexis_pro", "name": "Nexis Pro"},
}
NAMES = {"free": "Free", "plus": "Nexis Plus", "pro": "Nexis Pro"}


def rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else 0


def tier_of(price_key: str | None) -> str | None:
    """The tier a subscription's price key sells. Subscriptions created before the AED plans (``pro_monthly`` in
    USD, ``pro_yearly``) were all Nexis Pro."""
    if not price_key:
        return None
    if price_key in PRICES:
        return PRICES[price_key]["tier"]
    head = price_key.split("_", 1)[0]
    return head if head in PAID_TIERS else None


def price_key_for(tier: str, interval: str = "month") -> str:
    """The price key selling ``tier`` billed every ``interval`` (falls back to the tier's first price)."""
    return next((k for k, v in PRICES.items() if v["tier"] == tier and v["interval"] == interval),
                next(k for k, v in PRICES.items() if v["tier"] == tier))  # fmt: skip


def annual_offer(tier: str) -> dict[str, Any] | None:
    """What annual billing means for ``tier`` versus 12 monthly payments: the upfront amount, its monthly equivalent and
    the saving. Computed from PRICES, never typed in anywhere else."""
    month = next((v for v in PRICES.values() if v["tier"] == tier and v["interval"] == "month"), None)
    year = next((v for v in PRICES.values() if v["tier"] == tier and v["interval"] == "year"), None)
    if not month or not year:
        return None
    twelve = month["amount_cents"] * 12
    saving = twelve - year["amount_cents"]
    return {
        "upfront": year["amount_cents"] / 100,
        "per_month": round(year["amount_cents"] / 1200, 2),
        "twelve_monthly": twelve / 100,
        "saving": saving / 100,
        "saving_pct": round(saving * 100 / twelve),
    }


def price_facts() -> str:
    """One sentence with every plan price, from PRICES (used by the Help agent so it can't drift from checkout)."""
    parts = ["Free AED 0"]
    for t in PAID_TIERS:
        m = PRICES.get(price_key_for(t, "month"))
        y = next((v for v in PRICES.values() if v["tier"] == t and v["interval"] == "year"), None)
        text = f"{NAMES[t]} AED {m['amount_cents'] / 100:,.0f}/month" if m else NAMES[t]
        if y:
            o = annual_offer(t)
            text += (
                (
                    f" or AED {y['amount_cents'] / 100:,.0f}/year paid upfront for 12 months (about AED {o['per_month']:,.2f} a month; "
                    f"saves AED {o['saving']:,.0f}, about {o['saving_pct']}%, vs 12 monthly payments)"
                )
                if o
                else ""
            )
        parts.append(text)
    return "; ".join(parts) + ". Annual plans are only on sale when the pricing page shows them."


def intervals() -> list[str]:
    """Billing intervals on sale, monthly first."""
    return sorted({v["interval"] for v in PRICES.values()}, key=lambda i: (i != "month", i))


def allows(tier: str, capability: str) -> bool:
    return tier in CAPABILITIES[capability]["tiers"]


def price_label(price_key: str | None) -> str | None:
    p = PRICES.get(price_key or "")
    if not p:
        return "Your existing price (unchanged)" if (price_key or "").endswith("_legacy") else None
    amount = p["amount_cents"] / 100
    return f"AED {amount:,.2f}/{p['interval']}" if amount % 1 else f"AED {amount:,.0f}/{p['interval']}"


def public() -> dict[str, Any]:
    """Everything the browser needs to describe the plans (no secrets, no Stripe ids)."""
    return {
        "tiers": [
            {"id": t, "name": NAMES[t], "limits": dict(LIMITS[t]),
             "price": ({"key": price_key_for(t), "amount": PRICES[price_key_for(t)]["amount_cents"] / 100,
                        "currency": PRICES[price_key_for(t)]["currency"].upper(), "interval": PRICES[price_key_for(t)]["interval"]}
                       if t in PAID_TIERS else {"key": None, "amount": 0, "currency": "AED", "interval": "month"}),
             "prices": ({v["interval"]: {"key": k, "amount": v["amount_cents"] / 100, "currency": v["currency"].upper(),
                                         **({"annual": annual_offer(t)} if v["interval"] == "year" and annual_offer(t) else {})}
                         for k, v in PRICES.items() if v["tier"] == t} if t in PAID_TIERS else {}),
             "capabilities": [c for c, v in CAPABILITIES.items() if t in v["tiers"]]}
            for t in TIERS
        ],
        "intervals": intervals(),
        "anonymous": dict(ANONYMOUS),
        "limits": {k: dict(v) for k, v in LIMITS.items()},
        "features": {k: {kk: vv for kk, vv in v.items() if kk in ("label", "unit", "monthly")} for k, v in FEATURES.items()},
        "capabilities": {k: {"label": v["label"], "summary": v["summary"], "tiers": list(v["tiers"])} for k, v in CAPABILITIES.items()},
        "prices": {k: {"tier": v["tier"], "amount": v["amount_cents"] / 100, "currency": v["currency"].upper(), "interval": v["interval"]}
                   for k, v in PRICES.items()},
    }  # fmt: skip
