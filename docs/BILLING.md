# Nexis billing — Free, Nexis Plus, Nexis Pro

## The plans (single source: `backend/app/core/plans.py`)

| | Free | Nexis Plus | Nexis Pro |
|---|---|---|---|
| Price | AED 0 | AED 29 / month | AED 69 / month |
| Active investments (My Nexis holdings, distinct assets) | 1 | 10 | 20 |
| AI Advisor messages / month (reset on the 1st, UTC) | 20 | 200 | 600 |
| Portfolio insights (`/api/me/portfolio/insights`) | — | ✓ | ✓ |
| Portfolio risk analytics (`/api/me/portfolio/risk`) | — | — | ✓ |
| Nexis Pulse (read, post, reply, vote) | unlimited | unlimited | unlimited |
| Watchlist tickers (anti-abuse cap, not a paid feature) | 50 | 50 | 50 |
| Help Center articles / AI help answers per day | unlimited / 40 | unlimited / 40 | unlimited / 40 |

Visitors without an account: 3 AI Advisor messages and 10 AI help answers per day (per IP). Monthly billing only;
no yearly plan. Pulse is never metered by plan; anti-spam rate limits (`pulse_discussions_per_day`,
`pulse_comments_per_hour`, no repeated posts) apply to everyone equally.

## How it works

* `entitlements.py` — the only plan logic: `tier_of` (free/plus/pro from the provider's subscription state),
  `require(capability)`, `get_user_entitlements`, `consume_usage` (+ `refund`), `check_capacity`. Usage rows are
  written first and the total checked after, so parallel requests can't exceed a limit. Adding an investment locks the
  account row and re-counts before committing.
* Paid access by Stripe status: `active`, `trialing`, `past_due` (Stripe retrying; the member is asked to update the
  card) → the subscribed tier. `canceled`, `unpaid`, `incomplete(_expired)`, `paused` → Free. A period that ended more
  than 2 days ago without renewal → Free. Subscriptions on the earlier USD prices are recognised as Nexis Pro.
* `investments.py` — downgrades never delete. Before a cancellation ends (or a move to a smaller plan) the member picks
  which investments stay active (`POST /api/me/investments/keep`). When Stripe confirms the lower plan, the rest are
  archived (read-only, not counted). No choice on file → nothing is archived; adding/editing waits until they choose
  (409 `investment_selection_required`), viewing stays open, and they get a notification. Resubscribing restores
  investments archived by a downgrade, up to the new allowance. Members can archive/restore their own too.
* `billing.py` (provider-neutral) + `billing_stripe.py` (Checkout, Billing, Customer Portal, plan changes, webhooks).
  Checkout sells only the two configured price ids, and only after confirming in Stripe that they are exactly
  AED 29/month and AED 69/month and in the same mode (test/live) as the key. One live subscription per account:
  a second checkout is refused; a second paid subscription (two tabs) is cancelled and refunded automatically.
* Plan changes (`POST /api/billing/change-plan`): same subscription, new price. Upgrades are invoiced immediately
  for the rest of the period and refused if that payment fails; downgrades credit the unused part. Plan changes are
  disabled in the Stripe portal so they always go through Nexis (which asks which investments to keep).
* Webhooks: signature verified; each event id stored once; every event re-reads the subscription from Stripe so
  late/out-of-order events can't downgrade; failures return 500 so Stripe retries; after each event the member's
  investments are reconciled with the confirmed plan.

Endpoints: `GET /api/billing/plans`, `GET /api/billing/status`, `POST /api/billing/checkout {plan}`,
`POST /api/billing/change-plan {plan, keep?}`, `POST /api/billing/confirm {session_id}`, `POST /api/billing/cancel`,
`POST /api/billing/resume`, `POST /api/billing/portal`, `POST /api/billing/webhook` (Stripe only);
`GET /api/me/investments`, `POST /api/me/investments/keep`, `POST /api/me/investments/{symbol}/archive|restore`.

## TEST environment

1. Create (or find) the products and AED prices — idempotent, refuses live keys:
   ```
   cd backend
   STRIPE_SECRET_KEY=sk_test_... python -m app.cli.stripe_setup
   ```
   Test-mode prices already created: `STRIPE_PLUS_MONTHLY_AED_PRICE_ID=price_1UOjNkBMkNzEbwkIWjbss0on`,
   `STRIPE_PRO_MONTHLY_AED_PRICE_ID=price_1UOjNlBMkNzEbwkIfPxyh7fe`. The webhook endpoint and portal configuration from
   the earlier setup are reused (same URL, same events).
2. Vercel → `nexis-finance-api` → Environment Variables (Production): add `STRIPE_PLUS_MONTHLY_AED_PRICE_ID` and
   `STRIPE_PRO_MONTHLY_AED_PRICE_ID`; keep `STRIPE_PRO_MONTHLY_PRICE_ID` / `STRIPE_PRO_YEARLY_PRICE_ID` (they now only
   identify earlier subscriptions); redeploy. Until the new variables are set, paid plans show as unavailable.
3. Test cards only: `4242 4242 4242 4242` (success), `4000 0000 0000 0002` (declined), `4000 0027 6000 3184` (3-D
   Secure). Test clocks for renewal, failed renewal (`pm_card_chargeCustomerFail`) and expiry.
4. While the key is a test key, production checkout is limited to `NEXIS_BILLING_TEST_EMAILS`.

## LIVE environment (only when the founder says so)

Check any time, read-only (works with a test or live key; changes nothing, prints no secrets):
```
STRIPE_SECRET_KEY=<key> python -m app.cli.stripe_setup --check
```

1. **Activate the Stripe account** (Dashboard → Activate payments): business details, identity verification and a
   bank account for payouts, until `--check` shows details submitted, charges enabled and payouts enabled. The
   account is in the UAE with AED as its default currency, which matches the AED prices.
2. **Create the live objects**, in a terminal on your own machine (the live key never goes into chat or the repo):
   ```
   STRIPE_SECRET_KEY=sk_live_... python -m app.cli.stripe_setup --live --webhook https://nexis-finance-api.vercel.app/api/billing/webhook
   ```
   It creates the `nexis_plus` / `nexis_pro` products, the AED 29 and AED 69 monthly prices, the webhook endpoint
   with the six events (`checkout.session.completed`, `customer.subscription.created|updated|deleted`, `invoice.paid`,
   `invoice.payment_failed`) and the Customer Portal (update card, invoices, cancel at period end, plan switching off).
   It prints the two price ids and the webhook signing secret once. If the live account already has a default portal
   configuration, set the same options in Dashboard → Settings → Billing → Customer portal.
3. **Vercel → nexis-finance-api → Settings → Environment Variables → Production only** (never Preview/Development):
   replace `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PLUS_MONTHLY_AED_PRICE_ID`, `STRIPE_PRO_MONTHLY_AED_PRICE_ID`
   with the live values; delete `STRIPE_PRO_MONTHLY_PRICE_ID` and `STRIPE_PRO_YEARLY_PRICE_ID` (test-mode ids) and
   `NEXIS_BILLING_TEST_EMAILS` (ignored with a live key anyway). Then redeploy — Vercel applies changed variables only
   to new deployments. With a live key, checkout opens to everyone; prices of the wrong mode are refused.
4. Billing → Subscriptions and emails: Smart Retries on; after the final retry cancel the subscription (or mark it
   unpaid); failed-payment emails on.
5. Wallets: enable Apple Pay / Google Pay in the live Dashboard, confirm they appear in a live checkout, then set
   `NEXIS_BILLING_WALLETS="Apple Pay,Google Pay"`. Until then only cards are listed. Tabby is a separate provider
   (not Stripe Checkout here) and needs its own merchant approval.
6. Terms of Use: add subscription, renewal, cancellation and refund terms before taking real payments.
7. Run `--check` with the live key until it reports 0 problems, then one real AED 29 purchase with the founder's own
   card (explicitly authorised), verified end to end, then cancelled and refunded in the Dashboard.
