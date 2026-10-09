"""Create (or find) the Nexis Plus and Nexis Pro products and their monthly AED prices in Stripe, from ``app/core/plans.py``.

    STRIPE_SECRET_KEY=sk_test_... python -m app.cli.stripe_setup
    STRIPE_SECRET_KEY=sk_test_... python -m app.cli.stripe_setup --webhook https://nexis-finance-api.vercel.app/api/billing/webhook

* Idempotent: the products have the fixed ids ``nexis_plus`` / ``nexis_pro`` and the prices the lookup keys in
  ``plans.PRICES``; a second run finds them instead of creating duplicates. Earlier prices are left untouched. An existing price with a different
  amount is never edited (Stripe prices are immutable) — the script stops and says so.
* Test mode only unless ``--live`` is passed explicitly. It never creates customers, subscriptions or charges.
* ``--webhook URL`` also creates the webhook endpoint with the six events Nexis handles and prints its signing
  secret (Stripe shows that secret only once), and sets up the Customer Portal (update card, invoices, cancel at
  period end).

Prints the environment variables to set on the API.

``--check`` changes nothing (safe with a live key, no ``--live`` needed): it reports whether the account can take
payments, whether both prices exist with the exact amounts, whether the webhook endpoint is registered for the six
events, and whether the Customer Portal is set up the way Nexis expects. Secrets are never printed.
"""

from __future__ import annotations

import argparse
import os
import sys

import stripe

from app.core import plans
from app.services.billing_stripe import HANDLED

DESCRIPTIONS = {
    "plus": "Ten active investments, a higher AI Advisor allowance and portfolio insights.",
    "pro": "Twenty active investments, the highest AI Advisor allowance, portfolio insights and risk analytics.",
}
PORTAL_FEATURES = {
    "payment_method_update": {"enabled": True},
    "invoice_history": {"enabled": True},
    "customer_update": {"enabled": True, "allowed_updates": ["email", "address", "name", "tax_id"]},
    "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
    "subscription_update": {"enabled": False},  # plan changes go through Nexis (it asks which investments to keep)
}


WEBHOOK_URL = "https://nexis-finance-api.vercel.app/api/billing/webhook"


def check(mode: str, url: str) -> int:
    """Read-only readiness report. Returns the number of problems."""
    problems = 0

    def line(ok: bool | None, text: str) -> None:
        nonlocal problems
        problems += ok is False
        print(f"  [{'PASS' if ok else 'FAIL' if ok is False else 'INFO'}] {text}")

    print(f"Stripe {mode} readiness for Nexis")
    acct = stripe.Account.retrieve().to_dict()
    req = acct.get("requirements") or {}
    line(None, f"account country {acct.get('country')}, default currency {str(acct.get('default_currency')).upper()}")
    line(bool(acct.get("details_submitted")), "account details submitted (activation form completed)")
    line(bool(acct.get("charges_enabled")), "charges enabled (can take real payments)" if mode == "LIVE" else "charges enabled")
    line(bool(acct.get("payouts_enabled")), "payouts enabled (bank account connected and verified)")
    if req.get("currently_due"):
        line(False, f"{len(req['currently_due'])} verification item(s) currently due in the Dashboard")
    if req.get("disabled_reason"):
        line(False, f"account restricted: {req['disabled_reason']}")
    found = {
        p.lookup_key: p for p in stripe.Price.list(lookup_keys=[v["lookup_key"] for v in plans.PRICES.values()], limit=10).data
    }
    for key, want in plans.PRICES.items():
        p = found.get(want["lookup_key"])
        if p is None:
            line(False, f"{key}: no price with lookup key {want['lookup_key']} (run this script without --check to create it)")
            continue
        got = (p.unit_amount, p.currency, p.recurring.interval if p.recurring else None, p.active, p.livemode)
        line(
            got == (want["amount_cents"], want["currency"], want["interval"], True, mode == "LIVE"),
            f"{key}: {p.id} = {p.unit_amount / 100:.2f} {p.currency.upper()}/{got[2]}, active={p.active}, livemode={p.livemode}",
        )
    hooks = [w for w in stripe.WebhookEndpoint.list(limit=100).data if w.url == url]
    if not hooks:
        line(False, f"no webhook endpoint for {url}")
    for w in hooks:
        missing = sorted(set(HANDLED) - set(w.enabled_events)) if "*" not in w.enabled_events else []
        line(
            w.status == "enabled" and not missing,
            f"webhook {w.id} {w.status}" + (f", missing events: {', '.join(missing)}" if missing else ", all 6 events"),
        )
    cfgs = [c for c in stripe.billing_portal.Configuration.list(limit=10, active=True).data if c.is_default]
    if not cfgs:
        line(False, "no default Customer Portal configuration")
    for c in cfgs:
        f = c.features
        line(f.subscription_cancel.enabled and f.subscription_cancel.mode == "at_period_end", "portal: cancel at period end")
        line(f.payment_method_update.enabled, "portal: update payment method")
        line(
            not f.subscription_update.enabled,
            "portal: plan switching OFF (Nexis handles Plus/Pro changes and asks which investments to keep)",
        )
    print(f"{problems} problem(s)")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--webhook", help="create the webhook endpoint for this URL")
    ap.add_argument("--live", action="store_true", help="allow a live secret key (creates LIVE objects)")
    ap.add_argument("--check", action="store_true", help="report readiness only; changes nothing")
    a = ap.parse_args()
    key = os.environ.get("STRIPE_SECRET_KEY") or os.environ.get("NEXIS_STRIPE_SECRET_KEY")
    if not key:
        sys.exit("Set STRIPE_SECRET_KEY to your Stripe TEST secret key (sk_test_...).")
    if a.check:
        stripe.api_key = key
        sys.exit(1 if check("LIVE" if key.startswith(("sk_live_", "rk_live_")) else "TEST", a.webhook or WEBHOOK_URL) else 0)
    if key.startswith(("sk_live_", "rk_live_")) and not a.live:
        sys.exit("Refusing to use a LIVE key. This script is for test mode; pass --live only when you mean it.")
    stripe.api_key = key
    mode = "LIVE" if key.startswith(("sk_live_", "rk_live_")) else "TEST"

    products = {}
    for tier, prod in plans.PRODUCTS.items():
        try:
            products[tier] = stripe.Product.retrieve(prod["id"])
            print(f"[{mode}] found product {prod['id']} ({products[tier].name})")
        except stripe.InvalidRequestError:
            products[tier] = stripe.Product.create(id=prod["id"], name=prod["name"], description=DESCRIPTIONS[tier])
            print(f"[{mode}] created product {prod['id']}")

    found = {
        p.lookup_key: p for p in stripe.Price.list(lookup_keys=[v["lookup_key"] for v in plans.PRICES.values()], limit=10).data
    }
    ids: dict[str, str] = {}
    for plan, want in plans.PRICES.items():
        p = found.get(want["lookup_key"])
        if p is None:
            p = stripe.Price.create(product=products[want["tier"]].id, unit_amount=want["amount_cents"], currency=want["currency"],
                                    recurring={"interval": want["interval"]}, lookup_key=want["lookup_key"], nickname=want["nickname"])  # fmt: skip
            print(
                f"[{mode}] created {plan}: {p.id}  {want['amount_cents'] / 100:.2f} {want['currency'].upper()}/{want['interval']}"
            )
        else:
            got = (p.unit_amount, p.currency, p.recurring.interval if p.recurring else None)
            if got != (want["amount_cents"], want["currency"], want["interval"]):
                sys.exit(f"Price {p.id} ({want['lookup_key']}) is {got}, not {want}. Archive it in Stripe and run again.")
            print(f"[{mode}] found {plan}: {p.id}")
        ids[plan] = p.id

    secret = None
    if a.webhook:
        existing = [w for w in stripe.WebhookEndpoint.list(limit=100).data if w.url == a.webhook]
        if existing:
            print(f"[{mode}] webhook endpoint already exists: {existing[0].id} — use its signing secret from the Dashboard")
        else:
            w = stripe.WebhookEndpoint.create(url=a.webhook, enabled_events=sorted(HANDLED), description="Nexis billing")
            secret = w.secret
            print(f"[{mode}] created webhook endpoint {w.id} for {a.webhook}")
        configs = stripe.billing_portal.Configuration.list(limit=10).data
        if not any(c.is_default for c in configs):
            stripe.billing_portal.Configuration.create(features=PORTAL_FEATURES, business_profile={"headline": "Nexis billing"})
            print(f"[{mode}] created Customer Portal configuration")
        else:
            print(f"[{mode}] Customer Portal already configured (check it allows cancel at period end)")

    print("\nSet these on the API (Vercel > nexis-finance-api > Settings > Environment Variables):")
    print("  STRIPE_SECRET_KEY=<the key you used>")
    print(f"  STRIPE_PLUS_MONTHLY_AED_PRICE_ID={ids['plus_monthly']}")
    print(f"  STRIPE_PRO_MONTHLY_AED_PRICE_ID={ids['pro_monthly']}")
    print(f"  STRIPE_WEBHOOK_SECRET={secret or '<signing secret of the webhook endpoint>'}")


if __name__ == "__main__":
    main()
