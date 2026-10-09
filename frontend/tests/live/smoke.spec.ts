import { expect, test } from "@playwright/test";

/* Optional live smoke test against the real site (uses the internet; no purchases, no AI questions).
   Skipped unless LIVE_SMOKE=1:  LIVE_SMOKE=1 npx playwright test --project=live-smoke */
const SITE = process.env.LIVE_SITE ?? "https://nexis-finance-five.vercel.app";

test("production is up and serves real data", async ({ page, request }) => {
  test.skip(!process.env.LIVE_SMOKE, "set LIVE_SMOKE=1 to run against production");
  expect((await request.get(`${SITE}/api/health`)).ok()).toBe(true);
  const plans = await (await request.get(`${SITE}/api/billing/plans`)).json();
  expect(plans.tiers?.length ?? Object.keys(plans.prices).length).toBeGreaterThan(0);
  await page.goto(SITE);
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator(".strip-cell").first()).toContainText(/\d/);
});
