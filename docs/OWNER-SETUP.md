# Owner setup checklist

Things only the owner can do (accounts, secrets, payments). Nothing here should be pasted into a chat. Set secrets
directly in Vercel → `nexis-finance-api` → Settings → Environment Variables (Production), then redeploy.

## 1. Contact email and email sending (free)

**Recommendation:** one dedicated Gmail account, for example `nexisfinance.app@gmail.com`, used both as the public
contact address and to send verification and password-reset emails.
* It costs nothing, needs no domain, and Google sends the mail, so it arrives reliably.
* Limit: 500 emails/day.
* Later, with your own domain (about $10/year, optional), switch to a free domain-based service such as Resend or
  Brevo.

Steps:
1. Create the Gmail account. Turn on **2-Step Verification** (Google Account → Security).
2. Google Account → Security → **App passwords** → create one named "Nexis". Google shows a 16-character password.
3. In Vercel (`nexis-finance-api`, Production), add:
   | Variable | Value |
   |---|---|
   | `NEXIS_SMTP_HOST` | `smtp.gmail.com` |
   | `NEXIS_SMTP_PORT` | `465` |
   | `NEXIS_SMTP_USERNAME` | the Gmail address |
   | `NEXIS_SMTP_PASSWORD` | the app password (secret) |
   | `NEXIS_SMTP_FROM` | `Nexis Finance <the Gmail address>` |
4. Redeploy. Then test "Forgot password?" on the site with your own account.

Once you tell me the address (it isn't a secret), I set the data-provider identification:
* `NEXIS_WIKIDATA_USER_AGENT` = `NexisFinance/1.0 (https://nexis-finance-five.vercel.app; <address>)`
* `NEXIS_SEC_USER_AGENT` = `Nexis Finance <address>`

## 2. Annual billing: create the two annual Stripe prices (test mode)

**Official prices** (set in `backend/app/core/plans.py`): Free AED 0 · Nexis Plus AED 29/month or **AED 229/year** ·
Nexis Pro AED 69/month or **AED 599/year**. Annual is one upfront payment for 12 months.

**Status (checked 2026-10-13):** the annual plans are **not on sale yet**. In Vercel, `nexis-finance-api` has the
two monthly AED price ids but **not** `STRIPE_PLUS_YEARLY_AED_PRICE_ID` or `STRIPE_PRO_YEARLY_AED_PRICE_ID`, and the
live catalog lists monthly billing only. Until both are set and match the amounts above, the site shows monthly
prices only and never offers an annual checkout.

> `STRIPE_PRO_YEARLY_PRICE_ID` (no `AED`) is the old USD price. Leave it: it only recognises old subscriptions.
> Don't copy it into the new variables.

### A. Create the prices in the Stripe Dashboard (no terminal needed)

1. Open https://dashboard.stripe.com and turn on **Test mode** (the toggle at the top right must say test mode).
2. **Product catalog** → **Nexis Plus** → **Add another price**:
   * Pricing model: **Recurring**
   * Amount: **229.00**, currency **AED**
   * Billing period: **Yearly**
   * Under "Advanced" / "More options", **Lookup key**: `nexis_plus_yearly_aed_229`
   * Description/nickname (optional): `Nexis Plus Annual (AED 229)`
   * Save. Copy the new price id (it starts with `price_`).
3. **Product catalog** → **Nexis Pro** → **Add another price**: the same, with **599.00** AED, Yearly, lookup key
   `nexis_pro_yearly_aed_599`. Copy its price id.
4. If an older annual price exists on either product (for example AED 290 or AED 990), **don't use it**. Archive it
   once no subscription uses it.

**Terminal alternative:** from `backend`, in your own terminal (never in a chat):
`$env:STRIPE_SECRET_KEY="sk_test_…"; .venv\Scripts\python -m app.cli.stripe_setup --check` lists every price
first. If the monthly prices show the lookup keys `nexis_plus_monthly_aed` / `nexis_pro_monthly_aed`, then
`.venv\Scripts\python -m app.cli.stripe_setup` creates only the two annual prices and prints their ids. If the
monthly prices have no lookup keys, use the Dashboard steps above instead (the script would add duplicate monthly
prices).

### B. Set the two environment variables

Vercel → **nexis-finance-api** → Settings → Environment Variables → Add (environment: **Production**, and Preview if
you test on previews):

| Variable | Value |
|---|---|
| `STRIPE_PLUS_YEARLY_AED_PRICE_ID` | the Nexis Plus AED 229/year price id |
| `STRIPE_PRO_YEARLY_AED_PRICE_ID` | the Nexis Pro AED 599/year price id |

Then **Deployments** → the latest production deployment → **Redeploy** (variables apply only to new deployments).

### C. Check before calling it done

1. https://nexis-finance-api.vercel.app/api/billing/plans must show `"intervals":["month","year"]`, with Plus
   `"amount":229.0` and Pro `"amount":599.0` under `"year"`. If it still shows only `"month"`, the server rejected
   a price: run `--check` (above) and it names the mismatch (amount, currency, interval or test/live mode).
2. Sign in with an address listed in `NEXIS_BILLING_TEST_EMAILS`, open **/pricing**, choose **Annual**, and buy
   Nexis Plus with the test card `4242 4242 4242 4242` (any future date, any CVC). Stripe's page must show
   **AED 229.00 per year**.
3. After returning to Nexis, Settings → Plan must show Nexis Plus, "billed annually upfront". In Stripe (test mode)
   → Subscriptions, the subscription must be on the AED 229 yearly price.
4. Cancel that test subscription in Stripe afterwards.

Only after step 3 passes are the annual plans purchasable (in test mode). Live payments are a separate step (§3).

## 3. Live payments

**Not started.** Payments stay in Stripe test mode. Two things are open:
* **Hosting terms.** Vercel's Hobby plan forbids "any method of requesting or processing payment". You decided not
  to use Vercel Pro (2026-10-11), so live payments wait until you choose another way.
* **Stripe account activation** (business details, bank account): done in the Stripe dashboard.

Then:
1. `$env:STRIPE_SECRET_KEY="sk_live_…"; .venv\Scripts\python -m app.cli.stripe_setup --live --webhook https://nexis-finance-api.vercel.app/api/billing/webhook`
2. In Vercel **Production**, replace `STRIPE_SECRET_KEY`, the four `STRIPE_*_AED_PRICE_ID` values and
   `STRIPE_WEBHOOK_SECRET` with the live values printed. Redeploy.
3. Run `--check` with the live key: everything must be OK.
4. Make one real purchase with your own card, then refund it in the Stripe dashboard.

## 4. Decisions still open

See [LEGAL-REVIEW.md](LEGAL-REVIEW.md): price-data licence, publisher permissions, Terms/Privacy review and version
bump, the advisor framing, and the operator details.
