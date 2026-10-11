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
