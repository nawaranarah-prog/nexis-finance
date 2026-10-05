"""Create (or find) the Nexis Pro product and its two prices in Stripe, from ``app/core/plans.py``.

    STRIPE_SECRET_KEY=sk_test_... python -m app.cli.stripe_setup
    STRIPE_SECRET_KEY=sk_test_... python -m app.cli.stripe_setup --webhook https://nexis-finance-api.vercel.app/api/billing/webhook

* Idempotent: the product has the fixed id ``nexis_pro`` and the prices the lookup keys ``nexis_pro_monthly`` and
  ``nexis_pro_yearly``; a second run finds them instead of creating duplicates. An existing price with a different
  amount is never edited (Stripe prices are immutable) — the script stops and says so.
* Test mode only unless ``--live`` is passed explicitly. It never creates customers, subscriptions or charges.
* ``--webhook URL`` also creates the webhook endpoint with the six events Nexis handles and prints its signing
  secret (Stripe shows that secret only once), and sets up the Customer Portal (update card, invoices, cancel at
  period end).

Prints the environment variables to set on the API.
"""

from __future__ import annotations

import argparse
import os
import sys

import stripe

from app.core import plans
from app.services.billing_stripe import HANDLED

PORTAL_FEATURES = {
    "payment_method_update": {"enabled": True},
    "invoice_history": {"enabled": True},
    "customer_update": {"enabled": True, "allowed_updates": ["email", "address", "name", "tax_id"]},
    "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--webhook", help="create the webhook endpoint for this URL")
    ap.add_argument("--live", action="store_true", help="allow a live secret key (creates LIVE objects)")
    a = ap.parse_args()
    key = os.environ.get("STRIPE_SECRET_KEY") or os.environ.get("NEXIS_STRIPE_SECRET_KEY")
    if not key:
        sys.exit("Set STRIPE_SECRET_KEY to your Stripe TEST secret key (sk_test_...).")
    if key.startswith(("sk_live_", "rk_live_")) and not a.live:
        sys.exit("Refusing to use a LIVE key. This script is for test mode; pass --live only when you mean it.")
    stripe.api_key = key
    mode = "LIVE" if key.startswith(("sk_live_", "rk_live_")) else "TEST"

    try:
        product = stripe.Product.retrieve(plans.PRODUCT["id"])
        print(f"[{mode}] found product {product.id} ({product.name})")
    except stripe.InvalidRequestError:
        product = stripe.Product.create(id=plans.PRODUCT["id"], name=plans.PRODUCT["name"],
                                        description="Higher fair-use limits for the AI Advisor, My Nexis and Nexis Pulse.")  # fmt: skip
        print(f"[{mode}] created product {product.id}")

    found = {p.lookup_key: p for p in stripe.Price.list(lookup_keys=[v["lookup_key"] for v in plans.PRICES.values()], limit=10, expand=["data.product"]).data}
    ids: dict[str, str] = {}
    for plan, want in plans.PRICES.items():
        p = found.get(want["lookup_key"])
        if p is None:
            p = stripe.Price.create(product=product.id, unit_amount=want["amount_cents"], currency=want["currency"],
                                    recurring={"interval": want["interval"]}, lookup_key=want["lookup_key"], nickname=want["nickname"])  # fmt: skip
            print(f"[{mode}] created {plan}: {p.id}  {want['amount_cents'] / 100:.2f} {want['currency'].upper()}/{want['interval']}")
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
            w = stripe.WebhookEndpoint.create(url=a.webhook, enabled_events=sorted(HANDLED), description="Nexis Pro billing")
            secret = w.secret
            print(f"[{mode}] created webhook endpoint {w.id} for {a.webhook}")
        configs = stripe.billing_portal.Configuration.list(limit=10).data
        if not any(c.is_default for c in configs):
            stripe.billing_portal.Configuration.create(features=PORTAL_FEATURES, business_profile={"headline": "Nexis Pro billing"})
            print(f"[{mode}] created Customer Portal configuration")
        else:
            print(f"[{mode}] Customer Portal already configured (check it allows cancel at period end)")

    print("\nSet these on the API (Vercel > nexis-finance-api > Settings > Environment Variables):")
    print("  STRIPE_SECRET_KEY=<the key you used>")
    print(f"  STRIPE_PRO_MONTHLY_PRICE_ID={ids['pro_monthly']}")
    print(f"  STRIPE_PRO_YEARLY_PRICE_ID={ids['pro_yearly']}")
    print(f"  STRIPE_WEBHOOK_SECRET={secret or '<signing secret of the webhook endpoint>'}")


if __name__ == "__main__":
    main()
