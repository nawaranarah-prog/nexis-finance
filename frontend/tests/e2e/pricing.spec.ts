import { expect, test, type Page } from "@playwright/test";
import { signUp } from "../support/account";
import { mockApi } from "../support/mockApi";
import type { AnyObj } from "../../src/types/api";

/* Prices come from the server (backend/app/core/plans.py). Annual plans appear only when the server lists them; they
   are always described as one upfront payment for 12 months, with the real saving against 12 monthly payments. */

test.beforeEach(async ({ page }) => { await mockApi(page); });

// The catalog exactly as the server builds it once the annual Stripe prices are configured (see test_billing.py).
const PLUS_YEAR = { key: "plus_yearly", amount: 229, currency: "AED", annual: { upfront: 229, per_month: 19.08, twelve_monthly: 348, saving: 119, saving_pct: 34 } };
const PRO_YEAR = { key: "pro_yearly", amount: 599, currency: "AED", annual: { upfront: 599, per_month: 49.92, twelve_monthly: 828, saving: 229, saving_pct: 28 } };
const TIERS = [
  { id: "free", name: "Free", limits: { investments: 1, advisor: 20, watchlist: 50 }, price: { key: null, amount: 0, currency: "AED", interval: "month" }, prices: {}, capabilities: [] },
  { id: "plus", name: "Nexis Plus", limits: { investments: 10, advisor: 200, watchlist: 50 }, price: { key: "plus_monthly", amount: 29, currency: "AED", interval: "month" },
    prices: { month: { key: "plus_monthly", amount: 29, currency: "AED" }, year: PLUS_YEAR }, capabilities: ["portfolio_insights"] },
  { id: "pro", name: "Nexis Pro", limits: { investments: 20, advisor: 600, watchlist: 50 }, price: { key: "pro_monthly", amount: 69, currency: "AED", interval: "month" },
    prices: { month: { key: "pro_monthly", amount: 69, currency: "AED" }, year: PRO_YEAR }, capabilities: ["portfolio_insights", "portfolio_risk"] },
];

async function withAnnual(page: Page) {
  await page.route("**/api/billing/plans", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), tiers: TIERS, intervals: ["month", "year"], available: true } });
  });
}

test("monthly only: no billing switch", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.getByRole("radio", { name: /Annual/ })).toHaveCount(0);
  await expect(page.locator(".pro-col.plus .pro-amount")).toHaveText("AED 29/month");
  await expect(page.locator(".pro-col.pro .pro-amount")).toHaveText("AED 69/month");
});

test("annual billing: upfront price, monthly equivalent and real saving; checkout gets the annual plan", async ({ page }) => {
  await signUp(page, "yr");
  await withAnnual(page);
  let sent: unknown = null;
  await page.route("**/api/billing/checkout", async (route) => { sent = route.request().postDataJSON(); await route.fulfill({ json: { url: "/pricing?checkout=canceled" } }); });
  await page.goto("/pricing");
  await expect(page.locator(".pro-col.plus")).toContainText("Or AED 229/year billed annually upfront.");  // monthly view hints at annual
  await page.getByRole("radio", { name: "Annual · save up to 34%" }).click();
  const plus = page.locator(".pro-col.plus");
  await expect(plus.locator(".pro-amount")).toHaveText("AED 229/year");
  await expect(plus).toContainText("about AED 19.08 a month, billed annually upfront (AED 229 for 12 months). Save AED 119 — about 34% — vs 12 monthly payments.");
  const pro = page.locator(".pro-col.pro");
  await expect(pro.locator(".pro-amount")).toHaveText("AED 599/year");
  await expect(pro).toContainText("about AED 49.92 a month, billed annually upfront (AED 599 for 12 months). Save AED 229 — about 28% — vs 12 monthly payments.");
  await expect(page.locator(".pro-table")).toContainText("AED 229/year (billed annually upfront)");
  await plus.getByRole("button", { name: "Get Nexis Plus annually" }).click();
  await expect.poll(() => sent).toEqual({ plan: "plus_yearly" });
});

async function asSubscriber(page: Page, extra: AnyObj) {
  await page.route("**/api/billing/status", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), paid: true, can_change_plan: true, can_manage: true, ...extra } });
  });
}

// Stripe's figures for each target plan, as POST /api/billing/change-plan/preview returns them (billing_stripe._describe).
const QUOTES: Record<string, AnyObj> = {
  pro_yearly: { plan: "pro_yearly", plan_name: "Nexis Pro", billing: "AED 599/year", amount_due: 370, amount_due_minor: 37000, credit: 0, currency: "AED",
    effective: "after_payment", renews_at: "2027-10-11T00:00:00Z", proration_date: 1791900000,
    summary: "You'll pay AED 370 now. Nexis Pro (AED 599/year) starts as soon as that payment succeeds; until then your current plan stays exactly as it is." },
  plus_monthly: { plan: "plus_monthly", plan_name: "Nexis Plus", billing: "AED 29/month", amount_due: 0, amount_due_minor: 0, credit: 160.5, currency: "AED",
    effective: "immediately", renews_at: "2026-11-13T00:00:00Z", proration_date: 1791900000,
    summary: "Nothing to pay today. Nexis Plus (AED 29/month) starts immediately. The unused part of your current plan becomes a credit of AED 160.50, used on your next invoices (not refunded). Your billing period changes to monthly." },
};

async function withQuotes(page: Page, onChange: (body: AnyObj) => AnyObj | Promise<AnyObj>) {
  await page.route("**/api/billing/change-plan/preview", async (route) => {
    const plan = route.request().postDataJSON().plan;
    await route.fulfill({ json: QUOTES[plan] ?? { ...QUOTES.pro_yearly, plan } });
  });
  await page.route("**/api/billing/change-plan", async (route) => { await route.fulfill({ json: await onChange(route.request().postDataJSON()) }); });
}

test("changing plan shows Stripe's figures and confirms that exact amount", async ({ page }) => {
  await signUp(page, "yrs");
  await withAnnual(page);
  await asSubscriber(page, { plan: "plus", plan_name: "Nexis Plus", billing: "AED 229/year", interval: "year", price_key: "plus_yearly", status: "active",
    renews_at: "2027-10-11T00:00:00Z" });
  const sent: AnyObj[] = [];
  await withQuotes(page, (b) => { sent.push(b); return { plan: "plus", plan_name: "Nexis Plus", paid: true, change: { result: "changed", message: "You're now on Nexis Plus (AED 29/month)." } }; });
  await page.goto("/settings#plan");
  await expect(page.locator("#plan")).toContainText("AED 229/year · billed annually upfront");
  await expect(page.locator("#plan")).toContainText("Active");
  await page.getByRole("button", { name: "Change plan" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("You're on Nexis Plus (AED 229/year)");
  await expect(dialog.getByRole("radio")).toHaveCount(3);  // Pro annual, Plus monthly, Pro monthly
  const terms = dialog.getByTestId("cp-terms");
  await expect(terms).toContainText("Charged todayAED 370");
  await expect(terms).toContainText("As soon as the payment succeeds");
  await expect(dialog.getByRole("button", { name: "Pay AED 370 and switch" })).toBeEnabled();
  await dialog.getByLabel(/Nexis Plus · AED 29\/month/).check();
  await expect(terms).toContainText("Charged todayNothing");
  await expect(terms).toContainText("credit AED 160.50 for the unused part of your current plan");
  await expect(terms).toContainText("Immediately");
  await dialog.getByRole("button", { name: /Switch to Nexis Plus, billed monthly/ }).click();
  await expect.poll(() => sent[0]).toEqual({ plan: "plus_monthly", expected_amount: 0, proration_date: 1791900000 });
});

test("a change that needs payment goes to Stripe's page, and the pending change can be paid or cancelled", async ({ page }) => {
  await signUp(page, "pend");
  await withAnnual(page);
  await asSubscriber(page, { plan: "plus", plan_name: "Nexis Plus", billing: "AED 229/year", interval: "year", price_key: "plus_yearly", status: "active",
    test_subscription: true, renews_at: "2027-10-11T00:00:00Z" });
  await withQuotes(page, () => ({ plan: "plus", plan_name: "Nexis Plus", paid: true,
    change: { result: "payment_required", pay_url: "/pricing?paid=stripe", message: "Your payment of AED 370 needs your attention." } }));
  await page.goto("/settings#plan");
  await expect(page.getByTestId("test-subscription")).toContainText("No real money was charged");
  await page.getByRole("button", { name: "Change plan" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Pay AED 370 and switch" }).click();
  await expect(page).toHaveURL(/paid=stripe/);  // handed to Stripe's hosted invoice page

  await page.unroute("**/api/billing/status");
  await asSubscriber(page, { plan: "plus", plan_name: "Nexis Plus", billing: "AED 229/year", interval: "year", price_key: "plus_yearly", status: "active",
    can_change_plan: false, pending_change: { plan: "pro_yearly", plan_name: "Nexis Pro", billing: "AED 599/year", expires_at: "2026-10-14T18:00:00Z" } });
  let cancelled = false;
  await page.route("**/api/billing/pending/cancel", async (route) => {
    cancelled = true;
    await route.fulfill({ json: { plan: "plus", plan_name: "Nexis Plus", paid: true, pending_change: null, usage: {} } });
  });
  await page.goto("/settings#plan");
  const note = page.getByTestId("pending-change");
  await expect(note).toContainText("Your change to Nexis Pro (AED 599/year) is waiting for payment.");
  await expect(note).toContainText("Until it's paid you stay on Nexis Plus");
  await expect(page.getByRole("button", { name: "Change plan" })).toHaveCount(0);
  await note.getByRole("button", { name: "Cancel the change" }).click();
  await expect.poll(() => cancelled).toBe(true);
  await expect(page.getByTestId("pending-change")).toHaveCount(0);
});

test("the pricing cards say what is charged today and how it renews", async ({ page }) => {
  await withAnnual(page);
  await page.goto("/pricing");
  await expect(page.getByTestId("terms-plus")).toHaveText("Charged today: AED 29. Renews every month at AED 29 until you cancel.");
  await page.getByRole("radio", { name: /Annual/ }).click();
  await expect(page.getByTestId("terms-pro")).toHaveText("Charged today: AED 599 for 12 months. Renews every year at AED 599 until you cancel.");
});

test("coming back from checkout: the page reflects what Stripe says, never the URL", async ({ page }) => {
  await signUp(page, "back");
  let answer: AnyObj = { plan: "free", paid: false, checkout: { status: "open", payment_status: "unpaid", subscription_status: null } };
  await page.route("**/api/billing/confirm", async (route) => { await route.fulfill({ json: answer }); });
  await page.goto("/pro/welcome?session_id=cs_test_abandoned");
  await expect(page.locator("h1")).toHaveText("Checkout wasn't completed.");
  await expect(page.getByText("Nothing was charged and your plan hasn't changed.")).toBeVisible();
  answer = { plan: "free", paid: false, checkout: { status: "complete", payment_status: "unpaid", subscription_status: "incomplete" } };
  await page.goto("/pro/welcome?session_id=cs_test_processing");
  await expect(page.locator("h1")).toHaveText("Your payment is being confirmed.");
  answer = { plan: "pro", plan_name: "Nexis Pro", paid: true, billing: "AED 69/month", interval: "month", test_subscription: true,
    checkout: { status: "complete", payment_status: "paid" } };
  await expect(page.locator("h1")).toHaveText("Welcome to Nexis Pro.", { timeout: 15000 });
  await expect(page.getByText("This was a Stripe test-mode (Sandbox) payment: no real money was charged.")).toBeVisible();
});

for (const [w, h] of [[375, 812], [1440, 900]] as const) {
  test(`monthly and annual are easy to compare @ ${w}`, async ({ page }) => {
    const { measure, problems } = await import("../support/layout");
    await page.setViewportSize({ width: w, height: h });
    await withAnnual(page);
    await page.goto("/pricing");
    await page.getByRole("radio", { name: /Annual/ }).click();
    await expect(page.locator(".pro-col.plus .pro-amount")).toHaveText("AED 229/year");
    expect(problems(await measure(page), w)).toEqual([]);
    await page.screenshot({ path: `test-results/pricing-annual-${w}.png`, fullPage: true });
  });
}

async function asPlan(page: Page, plan: string, name: string) {
  await page.route("**/api/billing/status", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), paid: plan !== "free", plan, plan_name: name } });
  });
}

test("upgrade prompts: Free members see what Plus and Pro add where the limits apply", async ({ page }) => {
  await signUp(page, "nudge");
  await withAnnual(page);
  await page.goto("/my-nexis/investments");
  const n = page.getByTestId("upgrade-nudge-investments");
  await expect(n).toContainText("Want to track more investments? Your plan includes 1.");
  await expect(n).toContainText("Nexis Plus: 10 active investments (AED 29/month) · Nexis Pro: 20 active investments (AED 69/month).");
  await expect(n).toContainText("Save up to 34% with annual billing.");
  await expect(n.getByRole("link", { name: "Upgrade to Plus or Pro" })).toHaveAttribute("href", "/pricing");
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Upgrade to Plus or Pro" }).click();
  await expect(page).toHaveURL(/\/(pricing|pro)$/);  // /pricing shows the plans page at /pro
});

test("upgrade prompts: Plus members are offered Pro only; Pro members see none", async ({ page }) => {
  await signUp(page, "nudge2");
  await withAnnual(page);
  await asPlan(page, "plus", "Nexis Plus");
  await page.goto("/my-nexis/investments");
  const n = page.getByTestId("upgrade-nudge-investments");
  await expect(n).toContainText("Your plan includes 10. Nexis Pro: 20 active investments (AED 69/month).");
  await expect(n).not.toContainText("Nexis Plus:");
  await expect(n.getByRole("link", { name: "Upgrade to Pro" })).toHaveAttribute("href", "/settings#plan");
  await page.unroute("**/api/billing/status");
  await asPlan(page, "pro", "Nexis Pro");
  await page.reload();
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator("#plan-allowance")).toBeVisible();
  await expect(page.getByTestId("upgrade-nudge-investments")).toHaveCount(0);
  await page.getByRole("button", { name: "Account menu" }).click();
  await expect(page.getByRole("menuitem", { name: /Upgrade/ })).toHaveCount(0);
});
