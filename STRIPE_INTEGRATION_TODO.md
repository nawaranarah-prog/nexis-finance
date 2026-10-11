# Stripe integration: remaining steps

Nexis already had a working Stripe Checkout integration. Only the parameters of the existing Checkout Session call
were updated to the Checkout Studio configuration (no new files, routes or refactoring). Everything about billing is
in [STRIPE-SETUP.md](STRIPE-SETUP.md), [docs/BILLING.md](docs/BILLING.md) and [docs/OWNER-SETUP.md](docs/OWNER-SETUP.md).

## Values to Replace

**None.** Every `sample_only` parameter already has a real value in the code, so it was kept:

**Files containing these values:**
- [backend/app/services/billing_stripe.py](backend/app/services/billing_stripe.py) (`checkout_url`)

| Field | Current value | Notes |
|-------|---------------|-------|
| mode | `subscription` | Correct: Nexis Plus and Pro are recurring plans |
| success_url | `{site}/pro/welcome?session_id={CHECKOUT_SESSION_ID}` | Real page; the session id is confirmed server-side |
| cancel_url | `{site}/pro?checkout=canceled` | Real page |
| line_items[].price | The Stripe price id for the chosen plan, from the environment (`STRIPE_PLUS_MONTHLY_AED_PRICE_ID`, `STRIPE_PRO_MONTHLY_AED_PRICE_ID`, `STRIPE_PLUS_YEARLY_AED_PRICE_ID`, `STRIPE_PRO_YEARLY_AED_PRICE_ID`) | Never chosen by the browser. Each price is checked against `plans.PRICES` before it is sold. **The two yearly ids still need to be created and set** (see below) |

## Configured Parameters

These parameters were configured in Checkout Studio and are now set in the code. A test pins them
(`test_checkout_requires_account_valid_plan_and_server_prices`).

**Files containing these parameters:**
- [backend/app/services/billing_stripe.py](backend/app/services/billing_stripe.py)

| Parameter | Value |
|-----------|-------|
| ui_mode | `hosted_page`: the value the installed SDK (stripe-python 16.0.0, API `2026-09-30.endive`) defines for hosted Checkout |
| billing_address_collection | `auto` |
| phone_number_collection | `{"enabled": false}` |
| automatic_tax | `{"enabled": false}` |
| allow_promotion_codes | `false` |
| payment_method_collection | `always` (subscription mode) |
| submit_type | `auto` |
| integration_identifier | `hosted_web_0001` |
| origin_context | `web` |

**Kept on purpose:** `customer`, `client_reference_id`, `metadata` and `subscription_data` aren't Checkout Studio
settings. They link the payment to the Nexis member, so the plan is granted to the right account and a member can't
end up with two subscriptions. Removing them would break fulfilment, so they stay.

## Setup and next steps

**Environment variables** (Vercel → `nexis-finance-api` → Settings → Environment Variables; never in code, chat or
git):
* `STRIPE_SECRET_KEY`
* `STRIPE_WEBHOOK_SECRET`
* `STRIPE_PLUS_MONTHLY_AED_PRICE_ID`, `STRIPE_PRO_MONTHLY_AED_PRICE_ID`
* `STRIPE_PLUS_YEARLY_AED_PRICE_ID`, `STRIPE_PRO_YEARLY_AED_PRICE_ID`
* `NEXIS_BILLING_TEST_EMAILS` (with a test key on the public site, only these accounts can buy)

No publishable key is needed: hosted Checkout redirects to Stripe, and nothing Stripe-related runs in the browser.

**To do:**
1. **Verify the new parameters against Stripe.** They match the SDK's definitions and the tests, but no checkout has
   been created with them against Stripe yet (no key is available to me). After this change is deployed, sign in with
   a test-email account and start a checkout. If Stripe rejects a parameter, the page says "We couldn't start
   checkout", and the server log names it.
2. **Create the yearly prices** (AED 290 and AED 990), test mode first: in your own terminal, from `backend`, run
   `$env:STRIPE_SECRET_KEY="sk_test_…"; .venv\Scripts\python -m app.cli.stripe_setup`. Then add the two printed
   price ids to Vercel and redeploy.
3. **Before going live:** activate the Stripe account, and move Vercel to **Pro** (Hobby forbids taking payments;
   $20/user/month, your decision). Then follow "Live payments" in [docs/OWNER-SETUP.md](docs/OWNER-SETUP.md).
4. **Tax:** `automatic_tax` is off. Decide on UAE VAT handling with your accountant before charging customers (listed
   in [docs/LEGAL-REVIEW.md](docs/LEGAL-REVIEW.md)).

**How it works:**
1. The member chooses a plan.
2. `POST /api/billing/checkout {plan}` checks the plan is on sale and its Stripe price matches, then creates a hosted
   Checkout Session (`mode=subscription`) and redirects to Stripe.
3. After payment, Stripe returns the member to `/pro/welcome`, and the server confirms the session.
4. The signed webhook (`/api/billing/webhook`) grants or updates the plan. Every event is stored once and re-read
   from Stripe.
5. Plan changes, cancellation and payment-method updates go through Settings and the Stripe Customer Portal.

**Testing (test mode only):** use card `4242 4242 4242 4242` with any future expiry date and any CVC. Card
`4000 0000 0000 9995` is declined, and `4000 0025 0000 3155` requires authentication. See
https://docs.stripe.com/testing. Nexis never makes a real charge on your behalf.

**Resources:** https://support.stripe.com · https://docs.stripe.com/mcp
