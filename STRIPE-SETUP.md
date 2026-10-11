# Stripe setup (operator checklist)

How billing works is in [docs/BILLING.md](docs/BILLING.md). This page lists what is configured and what still needs
the owner.

## Status

| | |
|---|---|
| Plans | Free AED 0; Nexis Plus AED 29/month or **AED 229/year**; Nexis Pro AED 69/month or **AED 599/year** (`backend/app/core/plans.py`, the owner's final prices). Annual = one upfront payment for 12 months: Plus saves AED 119 (~34%), Pro AED 229 (~28%) vs 12 monthly payments |
| Mode | **Test mode.** Live mode needs Stripe activation, a live key, and **Vercel Pro**: Vercel's Hobby plan forbids processing payments ($20/user/month, awaiting the owner's OK). Steps: [docs/OWNER-SETUP.md](docs/OWNER-SETUP.md) |
| Annual plans | **In code; on sale only once the two annual Stripe prices (AED 229, AED 599) exist and verify.** Monthly keeps selling either way. Subscribers on any earlier price keep it (shown as "Your existing price (unchanged)"); nobody is repriced |
| Safety | Nexis sells a Stripe price only after confirming, in Stripe, that it has exactly the amount, currency and interval in `plans.PRICES`, and that it is in the same mode as the key. Otherwise the pricing page says plans are unavailable. |

## Environment variables (names only; set them in Vercel → `nexis-finance-api`)

| Variable | What it is |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe secret key (test `sk_test_…`, or live once approved). Never in the browser or in git. |
| `STRIPE_WEBHOOK_SECRET` | Signing secret of the webhook endpoint `https://nexis-finance-api.vercel.app/api/billing/webhook`. |
| `STRIPE_PLUS_MONTHLY_AED_PRICE_ID`, `STRIPE_PRO_MONTHLY_AED_PRICE_ID` | Monthly price ids (also accepted with a `NEXIS_` prefix). |
| `STRIPE_PLUS_YEARLY_AED_PRICE_ID`, `STRIPE_PRO_YEARLY_AED_PRICE_ID` | Yearly price ids. Only used once yearly plans exist in `plans.PRICES`. |
| `NEXIS_BILLING_TEST_EMAILS` | With a test key on the public site, only these accounts can buy. |

## Turning on annual plans (AED 229 / AED 599)

The amounts are already in `backend/app/core/plans.py`, with lookup keys `nexis_plus_yearly_aed_229` and
`nexis_pro_yearly_aed_599`. The amount is in the key, so a price created earlier for AED 290/990 is never matched or
reused.

1. **Inspect what exists** (read-only), in your own terminal from `backend`:
   `$env:STRIPE_SECRET_KEY="sk_test_…"; .venv\Scripts\python -m app.cli.stripe_setup --check`
   The "Price inventory" lists every price on Nexis Plus and Nexis Pro: id, amount, interval, active and lookup key,
   and which one each `STRIPE_*_AED_PRICE_ID` should be.
2. **Create the two annual prices**: `…\python -m app.cli.stripe_setup` (idempotent). It finds the monthly prices,
   creates Plus AED 229/year and Pro AED 599/year, and prints the four `STRIPE_*_AED_PRICE_ID` lines.
   * Dashboard alternative: Products → Nexis Plus → Add price → Recurring, Yearly, AED 229.00, lookup key
     `nexis_plus_yearly_aed_229`. The same for Nexis Pro: AED 599.00, lookup key `nexis_pro_yearly_aed_599`.
3. In Vercel (`nexis-finance-api`, Production), set `STRIPE_PLUS_YEARLY_AED_PRICE_ID` and
   `STRIPE_PRO_YEARLY_AED_PRICE_ID` to the printed ids, and redeploy.
4. **If an earlier AED 290 / AED 990 annual price exists:** don't reuse it. Archive it in Stripe (Products → price →
   Archive) once no subscription uses it. If test subscriptions use it, they keep it.
5. Run `--check` again: everything must be OK. The pricing page then shows Monthly / Annual.

## Going live (owner only)

1. Activate the Stripe account (business details, bank account).
2. `STRIPE_SECRET_KEY=sk_live_… python -m app.cli.stripe_setup --live --webhook https://nexis-finance-api.vercel.app/api/billing/webhook`
3. Put the printed live ids and the webhook secret into Vercel **Production** only, then redeploy.
4. `python -m app.cli.stripe_setup --check` with the live key: everything must say OK.
5. Make one real purchase with your own card and refund it from the Stripe dashboard. Nexis never makes a charge on
   your behalf.
