import { expect, test } from "@playwright/test";
import { signUp } from "../support/account";
import { mockApi } from "../support/mockApi";

/* The public-launch announcement on the homepage: honest copy (no date, counts or claims), says the product already
   works, a real sign-up link for visitors, and it stays dismissed once closed. */

test.beforeEach(async ({ page }) => { await mockApi(page); });

test("visitors see the launch announcement above the working homepage, and can dismiss it", async ({ page }) => {
  await page.goto("/");
  const band = page.getByRole("region", { name: "A new perspective on your financial world." });
  await expect(band).toContainText("Launching soon");
  await expect(band).toContainText("We're getting closer. Stay tuned.");
  await expect(band).toContainText("already open — explore them now");
  await expect(band.getByRole("link", { name: "Create a free account" })).toHaveAttribute("href", "/login?mode=signup&next=/");
  await expect(page.locator("main h1")).toHaveText("Understand the move before you make one.");  // the hero is unchanged
  await band.getByRole("button", { name: "Dismiss the launch announcement" }).click();
  await expect(band).toHaveCount(0);
  await page.reload();
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.locator(".hm-launch")).toHaveCount(0);  // remembered in this browser
});

test("signed-in members see the announcement without a sign-up button", async ({ page }) => {
  await signUp(page, "launch");
  await page.goto("/");
  const band = page.locator(".hm-launch");
  await expect(band).toContainText("A new perspective on your financial world.");
  await expect(band.getByRole("link", { name: "Create a free account" })).toHaveCount(0);
});
