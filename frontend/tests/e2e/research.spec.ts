import { expect, test } from "@playwright/test";
import { signUp } from "../support/account";
import { mockApi } from "../support/mockApi";

/* The research tools work together: an instrument page links into each one, the company travels with you, and the
   hub lists everything a member saved. */

test.beforeEach(async ({ page }) => { await mockApi(page); });

test("an instrument page carries the company into each research tool", async ({ page }) => {
  await signUp(page, "rs");
  await page.goto("/markets/EMAAR.AE");
  const tools = page.locator(".card", { has: page.getByText("Research tools") });
  await expect(tools.getByRole("link", { name: "Connections" })).toHaveAttribute("href", "/world-graph?q=EMAAR.AE");

  await tools.getByRole("link", { name: "Stress-test" }).click();
  await expect(page).toHaveURL(/\/time-machine$/);
  await expect(page.locator(".tm-sym", { hasText: "EMAAR.AE" })).toBeVisible();

  await page.goto("/markets/EMAAR.AE");
  await page.locator(".card", { has: page.getByText("Research tools") }).getByRole("link", { name: "Challenge a thesis" }).click();
  await expect(page).toHaveURL(/\/devils-advocate\?symbol=EMAAR\.AE$/);
  await expect(page.locator(".da-form")).toContainText("EMAAR.AE");
  await page.getByLabel(/Why you think/).fill("Emaar will keep rising because Dubai property sales stay strong for years.");
  await page.getByRole("button", { name: "Save thesis" }).click();
  await expect(page).toHaveURL(/\/devils-advocate\/\d+$/);
  await expect(page.getByRole("main").getByRole("link", { name: "Connections" })).toBeVisible();  // the sidebar has its own "Connections" (data connections)

  await page.goto("/my-research");
  await expect(page.locator("main h1")).toHaveText("My research");
  await expect(page.locator(".card", { hasText: "Devil's Advocate theses" })).toContainText("EMAAR.AE");
});

test("visitors see the hub and are invited to join", async ({ page }) => {
  await page.goto("/my-research");
  await expect(page.getByRole("link", { name: /World Graph/ }).first()).toBeVisible();
  await page.getByRole("button", { name: "Create a free account" }).click();
  await expect(page.getByRole("dialog")).toContainText("save research");
});
