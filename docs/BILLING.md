# Nexis Pro — billing

## The plan (single source: `backend/app/core/plans.py`)

| | Visitor | Free | Nexis Pro |
|---|---|---|---|
| Price | — | $0 | **$9.99/month** or **$99/year** (USD) |
| AI Advisor | 3/day | 20/month | 600/month |
| My Nexis tracked assets (capacity) | — | 3 | 100 |
| Pulse discussions started | — | 3/month | 50/month |
| Pulse comments / replies | — | 10/month | 100/month |
| Reading Pulse, sentiment, discussions; search; basic asset pages | unlimited | unlimited | unlimited |

Yearly = $8.25/month; twelve monthly payments = $119.88; saving $20.88 (~17%). Computed from the prices, not typed.
Monthly allowances reset on the 1st of each calendar month (UTC) on every plan (a yearly subscriber gets 600 Advisor
messages every month). My Nexis is a capacity: removing an asset frees its slot; after a downgrade nothing is deleted,
only adding beyond the plan's capacity is refused. Fair-use limits apply; nothing numeric is called "unlimited".

## How it works

* `entitlements.py` — the only entitlement logic: `has_pro`, `get_user_entitlements`, `check_usage_limit`,
  `consume_usage` (+ `refund`), `check_capacity`, `anonymous_advisor`. Usage rows are written first and the total
  checked after, so parallel requests can't exceed a limit; failed Advisor answers / failed posts are refunded.
* Pro access by Stripe status: `active`, `trialing`, `past_due` (Stripe retrying; "Your payment needs attention.
  Please update your payment method.") → Pro. `canceled`, `unpaid`, `incomplete(_expired)`, `paused` → Free. A period
  that ended more than 2 days ago without a renewal → Free. Cancel = at period end; Pro until `current_period_end`.
* `billing.py` (provider-neutral) + `billing_stripe.py` (Stripe Checkout, Billing, Customer Portal, webhooks).
  Checkout only sells the two configured price ids, and only after confirming in Stripe that they are exactly
  $9.99/month and $99/year USD (otherwise Pro is shown as unavailable and the mismatch is logged).
  Duplicate protection: an open checkout session is reused; a live Stripe subscription → "You're already on Nexis Pro."
* Webhooks: signature verified (`STRIPE_WEBHOOK_SECRET`); each event id stored in `billing_events` with status
  `processed`/`ignored` and the object/customer/subscription ids; repeats are acknowledged and skipped; every event
  re-reads the subscription from Stripe so out-of-order delivery leaves the latest state. Failures return 500 so
  Stripe retries.

Endpoints: `GET /api/billing/plans` (public), `GET /api/billing/status`, `POST /api/billing/checkout {plan}`,
`POST /api/billing/confirm {session_id}`, `POST /api/billing/cancel`, `POST /api/billing/resume`,
`POST /api/billing/portal`, `POST /api/billing/webhook` (Stripe only).

## TEST environment (do this first)

1. Stripe Dashboard → **Test mode** → Developers → API keys → copy the **test** secret key (`sk_test_...`).
2. Create the product, prices, webhook and portal settings (idempotent; refuses live keys):
   ```
   cd backend
   STRIPE_SECRET_KEY=sk_test_... python -m app.cli.stripe_setup --webhook https://nexis-finance-api.vercel.app/api/billing/webhook
   ```
   It prints `STRIPE_PRO_MONTHLY_PRICE_ID`, `STRIPE_PRO_YEARLY_PRICE_ID` and `STRIPE_WEBHOOK_SECRET`.
   (Manual alternative: one product "Nexis Pro"; recurring prices 9.99 USD monthly and 99 USD yearly; webhook to the
   URL above with `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`; Customer Portal: payment method
   update, invoice history, customer update, cancel **at period end**.)
3. Settings → Payment methods (test mode): enable Cards, Apple Pay, Google Pay.
4. Settings → Billing → Subscriptions and emails: Smart Retries on; after the final retry → mark subscription
   **unpaid** (or cancel); send failed-payment emails.
5. Vercel → `nexis-finance-api` → Environment Variables: set the four `STRIPE_*` values (test) → redeploy.
6. Test (test cards only — never a real card):
   * monthly and yearly checkout: `4242 4242 4242 4242`, any future date, any CVC → `/pro/welcome` → Settings → Billing
   * failed renewal / past_due: subscribe with `4000 0000 0000 0341`, or use a **test clock** to advance to renewal
   * 3-D Secure: `4000 0027 6000 3184`
   * cancellation (Settings → Cancel subscription) → "Access until …"; advance a test clock past period end → Free
   * renewal: advance a test clock one period → new period end
   * webhook retries / duplicates: Developers → Webhooks → an event → **Resend**
   * existing Pro: start checkout again → "You're already on Nexis Pro."; billing portal: Manage billing → update card

## LIVE environment (only when the founder says so)

1. In **live** mode run the same script with a live key and `--live` (or create the same objects by hand).
2. Replace the four Vercel variables with the live values; redeploy. Live keys never go into the repository.
3. Do one real purchase with a founder card, then refund it in the Dashboard, to verify end to end.
