import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signUp } from "../support/account";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";

/* Time Machine against the real local API. The test backend has no internet, so factor shocks honestly report that
   no price history was available; explicit moves work without data. (The arithmetic itself is covered offline with
   known sensitivities in backend/tests/test_time_machine.py.) */

test.beforeEach(async ({ page }) => { await mockApi(page); });

async function buildAndRun(page: Page) {
  await page.goto("/time-machine");
  await page.getByPlaceholder("Add a holding: stock, ETF, index, bond…").fill("Emaar");
  await page.getByRole("option", { name: /EMAAR\.AE/ }).first().click();
  await expect(page.locator(".tm-sym", { hasText: "EMAAR.AE" })).toBeVisible();
  await page.getByRole("button", { name: "Oil falls 30%" }).click();
  await expect(page.getByLabel(/Brent crude oil/)).toHaveValue("-30");
  await page.getByText("Set your own move for a holding").click();
  await page.getByLabel("Holding", { exact: true }).selectOption("EMAAR.AE");
  await page.getByRole("button", { name: "Add move" }).click();
  await page.getByLabel("Move for EMAAR.AE in percent").fill("-15");
  await page.getByRole("button", { name: "Run scenario" }).click();
  const result = page.getByRole("region", { name: "Result per holding" });
  await expect(result).toContainText("Your move");
  return result;
}

test("visitors see what the Time Machine is and are asked to join", async ({ page }) => {
  await page.goto("/time-machine");
  await expect(page.locator("main h1")).toHaveText("Time Machine");
  await page.getByRole("button", { name: "Create a free account to start" }).click();
  await expect(page.getByRole("dialog")).toContainText("Create a free account to use the Time Machine");
  expect((await page.request.post("/api/time-machine/run", { data: {} })).status()).toBe(401);
});

test("a member runs a scenario, saves it, runs it again and compares", async ({ page }) => {
  await signUp(page, "tm");
  const result = await buildAndRun(page);
  await expect(result).toContainText("−15.0%");
  await expect(result).toContainText("−1,500");  // 10,000 × −15%
  // offline: the oil move can't be measured, and the page says so instead of inventing a number
  await expect(page.getByRole("status").filter({ hasText: "No price history was available for: Brent crude oil" })).toBeVisible();

  await page.getByLabel("Scenario name").fill("Emaar hit");
  await page.getByRole("button", { name: "Save scenario" }).click();
  await expect(page).toHaveURL(/\/time-machine\/\d+$/);
  await expect(page.getByRole("heading", { name: "Emaar hit" })).toBeVisible();
  await page.getByRole("button", { name: "Run again on today's data" }).click();
  await expect(page.getByText("2 runs")).toBeVisible();
  await page.getByRole("button", { name: "Compare first and latest run" }).click();
  await expect(page.getByRole("region", { name: "Run comparison" })).toContainText("Whole portfolio");

  await page.getByRole("button", { name: "Duplicate and edit" }).click();
  await expect(page).toHaveURL(/\/time-machine$/);
  await expect(page.locator(".tm-sym", { hasText: "EMAAR.AE" }).first()).toBeVisible();
  await page.goto("/time-machine");
  await expect(page.locator(".tm-saved")).toContainText("Emaar hit");
});

for (const [w, h] of [[375, 812], [1440, 900]] as const) {
  test(`scenario result: layout and accessibility @ ${w}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await signUp(page, "tml");
    await buildAndRun(page);
    expect(problems(await measure(page), w)).toEqual([]);
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${scheme} ${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")})`);
      expect(bad, bad.join("\n")).toEqual([]);
    }
    await page.getByRole("region", { name: "Result per holding" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/time-machine-${w}.png`, fullPage: false });
  });
}
