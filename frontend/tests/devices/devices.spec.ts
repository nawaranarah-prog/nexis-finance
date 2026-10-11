import { expect, test } from "@playwright/test";
import { signUp } from "../support/account";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";
import { PAGES } from "../support/pages";

/* Real device profiles (iPhone 14 / Safari engine, Pixel 7 / Chrome, iPad / Safari engine): touch input, device pixel
   ratio, mobile user agent and the device's own viewport. Every main page is laid out without overflow, clipped text,
   small tap targets or tiny text, and the core touch flows work. */

test.beforeEach(async ({ page }) => { await mockApi(page); });

for (const p of PAGES.filter((x) => x.name !== "about")) {
  test(`layout: ${p.name}`, async ({ page }) => {
    await page.goto(p.path, { waitUntil: "domcontentloaded" });
    await page.locator(p.ready).first().waitFor({ timeout: 20_000 }).catch(() => undefined);
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);
    const width = page.viewportSize()!.width;
    expect(problems(await measure(page), width)).toEqual([]);
  });
}

test("navigation opens by touch and takes you to a page", async ({ page }) => {
  await page.goto("/");
  const width = page.viewportSize()!.width;
  if (width > 1024) test.skip(true, "the sidebar is always visible on this device");
  await page.getByRole("button", { name: "Open navigation" }).tap();
  const nav = page.getByRole("complementary", { name: "Navigation" });
  await nav.getByRole("link", { name: "Time Machine" }).tap();
  await expect(page).toHaveURL(/\/time-machine$/);
  await expect(page.locator("main h1")).toHaveText("Time Machine");
});

test("a member runs a scenario by touch", async ({ page }) => {
  await signUp(page, "dev");
  await page.goto("/time-machine");
  await page.getByPlaceholder("Add a holding: stock, ETF, index, bond…").fill("Emaar");
  await page.getByRole("option", { name: /EMAAR\.AE/ }).first().tap();
  await page.getByText("Set your own move for a holding").tap();
  await page.getByLabel("Holding", { exact: true }).selectOption("EMAAR.AE");
  await page.getByRole("button", { name: "Add move" }).tap();
  await page.getByRole("button", { name: "Run scenario" }).tap();
  await expect(page.getByRole("region", { name: "Result per holding" })).toContainText("Your move");
  expect(problems(await measure(page), page.viewportSize()!.width)).toEqual([]);
});

test("a member opens the World Graph and reads a relationship by touch", async ({ page }) => {
  await signUp(page, "devg");
  await page.goto("/world-graph?q=Emaar");
  const choices = page.getByRole("group", { name: "Which one did you mean?" });
  const head = page.locator(".wg-center-head h2");
  await expect(choices.or(head)).toBeVisible();
  if (await choices.isVisible()) await choices.getByRole("button", { name: /^Emaar Properties/ }).tap();
  await expect(head).toHaveText("Emaar Properties");
  const phone = page.viewportSize()!.width <= 640;
  if (phone) {  // phones start on the readable list
    await page.locator(".wg-card", { hasText: "Burj Khalifa" }).tap();
  } else {
    await page.getByRole("button", { name: /^Burj Khalifa,/ }).tap();
    await page.getByRole("button", { name: "Burj Khalifa — owned by — Emaar Properties" }).tap();
  }
  await expect(page.getByRole("complementary", { name: "Details" }).getByRole("link", { name: /Open source/ })).toBeVisible();
  expect(problems(await measure(page), page.viewportSize()!.width)).toEqual([]);
});
