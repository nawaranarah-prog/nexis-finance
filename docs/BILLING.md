# Nexis Pro — billing setup and operations

Nexis is free to use. Nexis Pro raises the allowances for the three features that cost money per use: the AI
Advisor, PDF research reports (comparison and valuation) and AI briefs. Everything else stays free.

## How it works

* **Entitlement** — `backend/app/services/entitlements.py` is the only place that decides Pro access and limits.
  Pro = Stripe status `active`, `trialing` or `past_due` (Stripe is retrying a failed renewal; the member sees a
  "fix your payment" notice). Not Pro: `incomplete`, `incomplete_expired`, `unpaid`, `canceled`, `paused`, or a
  period that ended more than 2 days ago. Cancel-at-period-end keeps Pro until `current_period_end`.
* **Usage** — counted server-side in `usage_events`; reserved before the expensive call and refunded if it fails,
  so parallel tabs can't exceed a limit. Free resets monthly (UTC), Pro each billing period. Visitors without an
  account get `ANONYMOUS_ADVISOR_PER_DAY` Advisor questions, then are asked to create a free account.
* **Payments** — Stripe Checkout (hosted) + Stripe Billing + Customer Portal. No card data touches Nexis.
  Subscription state is written only from signed Stripe webhooks or server-side Stripe API reads; every event
  re-reads the subscription from Stripe (out-of-order safe) and event ids are stored (duplicates ignored).
* **Provider-neutral** — `billing.py` is provider-agnostic; `billing_stripe.py` is Stripe. A second provider
  (e.g. Tabby) adds its own module and `subscriptions.provider` value.

## Stripe setup (TEST MODE first)

1. Stripe Dashboard → switch to **Test mode**.
2. Product catalogue → add product **Nexis Pro** with two recurring prices (monthly, yearly) in AED.
   Copy both price ids (`price_...`).
3. Settings → Payment methods: enable Cards, Apple Pay and Google Pay. Checkout is hosted by Stripe, so no domain
   verification is needed.
4. Settings → Billing → Customer portal: allow updating payment methods, viewing invoices and cancelling
   (choose "cancel at end of billing period").
5. Settings → Billing → Subscriptions and emails: smart retries on; after all retries fail → "mark the
   subscription as unpaid" or "cancel"; send emails for failed payments.
6. Developers → Webhooks → add endpoint **`https://nexis-finance-api.vercel.app/api/billing/webhook`** with events:
   `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`. Copy the signing secret (`whsec_...`).
7. Vercel → project **nexis-finance-api** → Settings → Environment Variables (Production):
   `STRIPE_SECRET_KEY` (sk_test_…), `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_MONTHLY_PRICE_ID`,
   `STRIPE_PRO_YEARLY_PRICE_ID`. Redeploy. No publishable key is needed (Checkout is a redirect).

## Testing in test mode

* Open `/pro`, start checkout, pay with `4242 4242 4242 4242` (any future date, any CVC) → you return to
  `/pro/welcome`, which shows Pro once Stripe confirms; Settings → Plan shows renewal date and usage.
* Failed renewal: card `4000 0000 0000 0341` (attaches, then fails) or in the Dashboard use a test clock.
* Cancel via Manage subscription → Settings shows "Access until …", Pro continues until then.
* Webhook deliveries and retries are visible under Developers → Webhooks.

## Going live (founder only)

Create the same product/prices in **live** mode, add a live webhook endpoint (same URL and events), then replace
the four variables in Vercel with live values and redeploy. Live keys never go in the repository.
