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

## 2. Annual billing: create the yearly Stripe prices (test mode first)

The code sells Plus at AED 229/year and Pro at AED 599/year (one upfront payment for 12 months) only once matching
Stripe prices exist. First run `--check` to see the price inventory; full steps in [STRIPE-SETUP.md](../STRIPE-SETUP.md).
1. In a terminal on your computer (not in a chat), from the `backend` folder:
   `$env:STRIPE_SECRET_KEY="sk_test_…"; .venv\Scripts\python -m app.cli.stripe_setup`
   This is idempotent: it finds the existing monthly prices and creates the two yearly ones. It prints
   `STRIPE_PLUS_YEARLY_AED_PRICE_ID` and `STRIPE_PRO_YEARLY_AED_PRICE_ID`.
2. Add those two variables in Vercel (Production) and redeploy.
3. Check: `…\python -m app.cli.stripe_setup --check` must report everything OK. The pricing page then shows Monthly
   / Yearly.

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
