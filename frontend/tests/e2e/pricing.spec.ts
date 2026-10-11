import { expect, test, type Page } from "@playwright/test";
import { signUp } from "../support/account";
import { mockApi } from "../support/mockApi";

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

test("a yearly subscriber's plan change offers every plan on sale with its own price and honest terms", async ({ page }) => {
  await signUp(page, "yrs");
  await withAnnual(page);
  await page.route("**/api/billing/status", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), paid: true, plan: "plus", plan_name: "Nexis Plus", billing: "AED 229/year", interval: "year",
      price_key: "plus_yearly", can_change_plan: true, can_manage: true, renews_at: "2027-10-11T00:00:00Z" } });
  });
  let sent: unknown = null;
  await page.route("**/api/billing/change-plan", async (route) => { sent = route.request().postDataJSON(); await route.fulfill({ status: 503, json: { error: { message: "mock" } } }); });
  await page.goto("/settings#plan");
  await expect(page.locator("#plan")).toContainText("AED 229/year · billed annually upfront");
  await page.getByRole("button", { name: "Change plan" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("You're on Nexis Plus (AED 229/year)");
  await expect(dialog.getByRole("radio")).toHaveCount(3);  // Pro annual, Plus monthly, Pro monthly
  await expect(dialog.getByRole("radio").first()).toBeChecked();
  await expect(dialog).toContainText("Nexis Pro, billed annually · AED 599/year");
  await expect(dialog.getByTestId("cp-terms")).toContainText("Your renewal date stays");  // same period: date unchanged
  await dialog.getByLabel(/Nexis Plus · AED 29\/month/).check();
  await expect(dialog.getByTestId("cp-terms")).toContainText("renewal date changes to today");
  await expect(dialog.getByTestId("cp-terms")).toContainText("it isn't refunded");
  await dialog.getByRole("button", { name: /Switch to Nexis Plus, billed monthly/ }).click();
  await expect.poll(() => sent).toEqual({ plan: "plus_monthly" });
});

test("a monthly subscriber moving to annual is told what is charged today", async ({ page }) => {
  await signUp(page, "mtoy");
  await withAnnual(page);
  await page.route("**/api/billing/status", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), paid: true, plan: "pro", plan_name: "Nexis Pro", billing: "AED 69/month", interval: "month",
      price_key: "pro_monthly", can_change_plan: true, can_manage: true, renews_at: "2026-11-11T00:00:00Z" } });
  });
  await page.goto("/settings#plan");
  await page.getByRole("button", { name: "Change plan" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Nexis Pro · AED 599\/year/).check();
  await expect(dialog.getByTestId("cp-terms")).toContainText("You'll be charged AED 599 today for 12 months");
  await expect(dialog.getByTestId("cp-terms")).toContainText("renews one year from today");
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
