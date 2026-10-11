import { expect, test } from "@playwright/test";
import { signUp } from "../support/account";
import { mockApi } from "../support/mockApi";

/* Yearly plans appear only when the server lists them (plans.PRICES); the amounts always come from the server. */

test.beforeEach(async ({ page }) => { await mockApi(page); });

test("monthly only: no billing switch", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.getByRole("radio", { name: "Yearly" })).toHaveCount(0);
});

test("with yearly plans configured, the switch shows the server's yearly prices and checks out the yearly plan", async ({ page }) => {
  await signUp(page, "yr");
  await page.route("**/api/billing/plans", async (route) => {
    const r = await route.fetch();
    const body = await r.json();
    body.intervals = ["month", "year"];
    body.available = true;
    for (const t of body.tiers) {
      if (t.id === "plus") t.prices = { month: { key: "plus_monthly", amount: 29, currency: "AED" }, year: { key: "plus_yearly", amount: 111, currency: "AED" } };
      if (t.id === "pro") t.prices = { month: { key: "pro_monthly", amount: 69, currency: "AED" }, year: { key: "pro_yearly", amount: 222, currency: "AED" } };
    }
    await route.fulfill({ response: r, json: body });
  });
  let sent: unknown = null;
  await page.route("**/api/billing/checkout", async (route) => { sent = route.request().postDataJSON(); await route.fulfill({ json: { url: "/pricing?checkout=canceled" } }); });
  await page.goto("/pricing");
  await page.getByRole("radio", { name: "Yearly" }).click();
  await expect(page.locator(".pro-col.plus .pro-amount")).toContainText("/year");
  await expect(page.locator(".pro-col.plus .pro-amount")).toContainText("111");
  await page.locator(".pro-col.plus").getByRole("button", { name: /Get Nexis Plus/ }).click();
  await expect.poll(() => sent).toEqual({ plan: "plus_yearly" });
});

test("a yearly subscriber's plan change offers every plan on sale with its own price", async ({ page }) => {
  await signUp(page, "yrs");
  const tiers = [
    { id: "free", name: "Free", limits: { investments: 1, advisor: 20, watchlist: 50 }, price: { key: null, amount: 0, currency: "AED", interval: "month" }, prices: {}, capabilities: [] },
    { id: "plus", name: "Nexis Plus", limits: { investments: 10, advisor: 200, watchlist: 50 }, price: { key: "plus_monthly", amount: 29, currency: "AED", interval: "month" },
      prices: { month: { key: "plus_monthly", amount: 29, currency: "AED" }, year: { key: "plus_yearly", amount: 290, currency: "AED" } }, capabilities: ["portfolio_insights"] },
    { id: "pro", name: "Nexis Pro", limits: { investments: 20, advisor: 600, watchlist: 50 }, price: { key: "pro_monthly", amount: 69, currency: "AED", interval: "month" },
      prices: { month: { key: "pro_monthly", amount: 69, currency: "AED" }, year: { key: "pro_yearly", amount: 990, currency: "AED" } }, capabilities: ["portfolio_insights", "portfolio_risk"] },
  ];
  await page.route("**/api/billing/plans", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), tiers, intervals: ["month", "year"], available: true } });
  });
  await page.route("**/api/billing/status", async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: { ...(await r.json()), paid: true, plan: "plus", plan_name: "Nexis Plus", billing: "AED 290/year", interval: "year",
      price_key: "plus_yearly", can_change_plan: true, can_manage: true, renews_at: "2027-10-11T00:00:00Z" } });
  });
  let sent: unknown = null;
  await page.route("**/api/billing/change-plan", async (route) => { sent = route.request().postDataJSON(); await route.fulfill({ status: 503, json: { error: { message: "mock" } } }); });
  await page.goto("/settings#plan");
  await page.getByRole("button", { name: "Change plan" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("You're on Nexis Plus (AED 290/year)");
  await expect(dialog.getByRole("radio")).toHaveCount(3);  // Pro yearly, Plus monthly, Pro monthly
  await expect(dialog.getByRole("radio").first()).toBeChecked();
  await expect(dialog).toContainText("Nexis Pro, billed yearly · AED 990/year");
  await dialog.getByLabel(/Nexis Plus · AED 29\/month/).check();
  await expect(dialog).toContainText("credited to your next invoice");
  await dialog.getByRole("button", { name: /Switch to Nexis Plus, billed monthly/ }).click();
  await expect.poll(() => sent).toEqual({ plan: "plus_monthly" });
});
