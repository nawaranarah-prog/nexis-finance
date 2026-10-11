# Stripe setup (operator checklist)

How billing works is in [docs/BILLING.md](docs/BILLING.md). This page lists what is configured and what still needs
the owner.

## Status

| | |
|---|---|
| Plans on sale | Nexis Plus AED 29/month, Nexis Pro AED 69/month (`backend/app/core/plans.py`) |
| Mode | **Test mode.** Live mode needs the Stripe account activated and a live key, and the owner's explicit go-ahead. |
| Yearly plans | **Ready in code, not on sale.** The yearly AED amounts haven't been decided, and Nexis never guesses a price. |
| Safety | Nexis sells a Stripe price only after confirming, in Stripe, that it has exactly the amount, currency and interval in `plans.PRICES`, and that it is in the same mode as the key. Otherwise the pricing page says plans are unavailable. |

## Environment variables (names only; set them in Vercel → `nexis-finance-api`)

| Variable | What it is |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe secret key (test `sk_test_…`, or live once approved). Never in the browser or in git. |
| `STRIPE_WEBHOOK_SECRET` | Signing secret of the webhook endpoint `https://nexis-finance-api.vercel.app/api/billing/webhook`. |
| `STRIPE_PLUS_MONTHLY_AED_PRICE_ID`, `STRIPE_PRO_MONTHLY_AED_PRICE_ID` | Monthly price ids (also accepted with a `NEXIS_` prefix). |
| `STRIPE_PLUS_YEARLY_AED_PRICE_ID`, `STRIPE_PRO_YEARLY_AED_PRICE_ID` | Yearly price ids. Only used once yearly plans exist in `plans.PRICES`. |
| `NEXIS_BILLING_TEST_EMAILS` | With a test key on the public site, only these accounts can buy. |

## Turning on yearly plans

1. Decide the two yearly amounts in AED.
2. Add the `plus_yearly` and `pro_yearly` entries in `backend/app/core/plans.py`. The commented example there shows
   the shape; amounts are in fils (AED × 100).
3. Create the Stripe prices (idempotent; test mode unless `--live`):
   `cd backend && STRIPE_SECRET_KEY=sk_test_… python -m app.cli.stripe_setup`
   It prints the `STRIPE_*_AED_PRICE_ID` variables to set.
4. Set the two yearly price-id variables in Vercel and redeploy.
5. Check: `python -m app.cli.stripe_setup --check` (changes nothing). Then the pricing page shows a Monthly / Yearly
   switch, and checkout sells the yearly price.

Nothing else changes in the code: checkout, plan changes, webhooks, the "AED …/year" label and the price
verification already handle any plan in `plans.PRICES`. This is covered by the tests
`test_yearly_plans_are_off_until_configured`, `test_yearly_plans_sell_the_configured_stripe_price_and_grant_the_tier`
and `tests/e2e/pricing.spec.ts`.

## Going live (owner only)

1. Activate the Stripe account (business details, bank account).
2. `STRIPE_SECRET_KEY=sk_live_… python -m app.cli.stripe_setup --live --webhook https://nexis-finance-api.vercel.app/api/billing/webhook`
3. Put the printed live ids and the webhook secret into Vercel **Production** only, then redeploy.
4. `python -m app.cli.stripe_setup --check` with the live key: everything must say OK.
5. Make one real purchase with your own card and refund it from the Stripe dashboard. Nexis never makes a charge on
   your behalf.
