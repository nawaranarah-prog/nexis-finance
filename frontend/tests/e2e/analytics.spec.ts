import { expect, test } from "@playwright/test";
import { mockApi } from "../support/mockApi";

/* Vercel Web Analytics: one script for the whole app (no duplicates when moving between pages), served from the
   site itself, and URLs that carry secrets are cleaned before anything is sent. The script is Vercel's (it only
   exists on Vercel); locally it is stubbed so nothing leaves the machine. */

test("Vercel Analytics loads once for every page and never sends secret URL parameters", async ({ page }) => {
  const scripts: string[] = [];
  await page.route("**/_vercel/insights/**", async (route) => {
    scripts.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: 200, contentType: "text/javascript", body: "/* stub */" });
  });
  await mockApi(page);
  await page.goto("/");
  await expect(page.locator("main h1")).toBeVisible();
  await page.getByRole("link", { name: "UAE & Global Markets" }).first().click();  // client-side route change
  await expect(page).toHaveURL(/\/markets/);
  await page.goto("/pulse");
  await expect(page.locator('script[src="/_vercel/insights/script.js"]')).toHaveCount(1);
  expect(scripts.filter((p) => p === "/_vercel/insights/script.js").length).toBeGreaterThanOrEqual(1);

  // the beforeSend hook the app registers with the analytics queue
  const cleaned = await page.evaluate(() => {
    const q = (window as unknown as { vaq?: [string, (e: { type: string; url: string }) => { url: string }][] }).vaq ?? [];
    const hook = q.find(([name]) => name === "beforeSend")?.[1];
    if (!hook) return null;
    return [
      hook({ type: "pageview", url: "https://nexis-finance-five.vercel.app/reset-password?token=SECRET&next=/pulse" }).url,
      hook({ type: "pageview", url: "https://nexis-finance-five.vercel.app/verify-email?token=SECRET" }).url,
      hook({ type: "pageview", url: "https://nexis-finance-five.vercel.app/pro/welcome?session_id=cs_test_SECRET" }).url,
      hook({ type: "pageview", url: "https://nexis-finance-five.vercel.app/markets?list=uae_bonds" }).url,
    ];
  });
  expect(cleaned).toEqual([
    "https://nexis-finance-five.vercel.app/reset-password?next=%2Fpulse",
    "https://nexis-finance-five.vercel.app/verify-email",
    "https://nexis-finance-five.vercel.app/pro/welcome",
    "https://nexis-finance-five.vercel.app/markets?list=uae_bonds",  // ordinary parameters are kept
  ]);
});
