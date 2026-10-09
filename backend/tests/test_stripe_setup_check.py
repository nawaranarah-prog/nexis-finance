"""``stripe_setup --check``: a read-only live-readiness report (never creates or changes anything)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import stripe

from app.cli import stripe_setup
from app.core import plans


def test_check_reports_without_changing_anything(monkeypatch, capsys):
    def forbidden(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("--check must not create or modify Stripe objects")

    for obj, name in (
        (stripe.Product, "create"),
        (stripe.Price, "create"),
        (stripe.WebhookEndpoint, "create"),
        (stripe.billing_portal.Configuration, "create"),
    ):
        monkeypatch.setattr(obj, name, forbidden)
    acct = {
        "country": "AE",
        "default_currency": "aed",
        "details_submitted": True,
        "charges_enabled": True,
        "payouts_enabled": True,
        "requirements": {},
    }
    monkeypatch.setattr(stripe.Account, "retrieve", lambda **k: NS(to_dict=lambda: acct))
    prices = [NS(id=f"price_{k}", lookup_key=v["lookup_key"], unit_amount=v["amount_cents"], currency=v["currency"], recurring=NS(interval=v["interval"]),
                 active=True, livemode=True) for k, v in plans.PRICES.items()]  # fmt: skip
    monkeypatch.setattr(stripe.Price, "list", lambda **k: NS(data=prices))
    hook = NS(id="we_1", url=stripe_setup.WEBHOOK_URL, status="enabled", enabled_events=sorted(stripe_setup.HANDLED))
    monkeypatch.setattr(stripe.WebhookEndpoint, "list", lambda **k: NS(data=[hook]))
    feat = NS(
        subscription_cancel=NS(enabled=True, mode="at_period_end"),
        payment_method_update=NS(enabled=True),
        subscription_update=NS(enabled=False),
    )
    monkeypatch.setattr(stripe.billing_portal.Configuration, "list", lambda **k: NS(data=[NS(is_default=True, features=feat)]))
    assert stripe_setup.check("LIVE", stripe_setup.WEBHOOK_URL) == 0
    # a test-mode price in live mode, a wrong amount, a missing event and portal plan switching are all flagged
    prices[0].livemode = False
    prices[1].unit_amount = 7900
    hook.enabled_events = hook.enabled_events[1:]
    feat.subscription_update.enabled = True
    acct["charges_enabled"] = False
    assert stripe_setup.check("LIVE", stripe_setup.WEBHOOK_URL) == 5
    out = capsys.readouterr().out
    assert "sk_" not in out and "whsec_" not in out
