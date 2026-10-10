import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signUp } from "../support/account";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";

/* World Graph against the real local API; Wikidata is answered from responses recorded for Emaar Properties,
   Investment Corporation of Dubai, Emaar Development and Emirates NBD (NEXIS_WIKIDATA_FIXTURES_DIR). */

test.beforeEach(async ({ page }) => { await mockApi(page); });

async function openEmaar(page: Page) {
  await page.goto("/world-graph");
  await page.getByPlaceholder(/Company or ticker/).first().fill("Emaar");
  await page.getByRole("button", { name: "Search", exact: true }).first().click();
  // Several companies are called Emaar, so the first search asks which one. Once Emaar's relationships are loaded
  // (by any member), its listing ticker EMAAR — a name the source gives — resolves directly.
  const choices = page.getByRole("group", { name: "Which one did you mean?" });
  const head = page.locator(".wg-center-head h2");
  await expect(choices.or(head)).toBeVisible();
  if (await choices.isVisible()) {
    await expect(choices).toContainText("Emaar Development");
    await choices.getByRole("button", { name: /^Emaar Properties/ }).click();
  }
  await expect(page).toHaveURL(/\/world-graph\/\d+$/);
  await expect(page.locator(".wg-center-head h2")).toHaveText("Emaar Properties");
}

test("visitors see what the World Graph is and are asked to join", async ({ page }) => {
  await page.goto("/world-graph");
  await expect(page.locator("main h1")).toHaveText("World Graph");
  await page.getByRole("button", { name: "Create a free account to start" }).click();
  await expect(page.getByRole("dialog")).toContainText("Create a free account to use the World Graph");
  expect((await page.request.get("/api/world-graph/resolve?q=Emaar")).status()).toBe(401);
});

test("a member searches, picks the right company and inspects sourced links", async ({ page }) => {
  await signUp(page, "wg");
  await openEmaar(page);
  await expect(page.locator(".wg-svg .wg-node")).toHaveCount(17);
  // 1 statement cites a reference; the rest don't (the exact count grows if other companies' links join the view)
  await expect(page.getByText(/16 connected entities · \d+ relationships · 1 referenced, \d+ unreferenced/)).toBeVisible();

  await page.getByRole("button", { name: /^Burj Khalifa,/ }).click();
  const inspector = page.getByRole("complementary", { name: "Details" });
  await expect(inspector.getByRole("heading", { name: "Burj Khalifa" })).toBeVisible();
  await inspector.getByRole("button", { name: "Burj Khalifa — owned by — Emaar Properties" }).click();
  await expect(inspector.locator(".wg-badge")).toHaveText("Referenced");
  await expect(inspector.getByRole("link", { name: /Open source/ })).toHaveAttribute("href", "http://skyscrapercenter.com/building/burj-khalifa/3");

  // the listing has no reference on Wikidata, and the page says so
  await page.getByRole("radio", { name: "List" }).click();
  const row = page.locator(".wg-table tr", { hasText: "Dubai Financial Market" });
  await expect(row).toContainText("Unreferenced");
  await expect(row).toContainText("from 2000-01-01");

  // filters: only referenced links
  await page.getByRole("checkbox", { name: /^Unreferenced/ }).uncheck();
  await expect(page.locator(".wg-table tbody tr")).toHaveCount(1);
  await expect(page).toHaveURL(/status=verified/);
});

test("expanding a neighbour, keyboard selection, and a chain between two companies", async ({ page }) => {
  await signUp(page, "wgx");
  await openEmaar(page);
  const icd = page.getByRole("button", { name: /^Investment Corporation of Dubai,/ });
  await icd.focus();
  await page.keyboard.press("Enter");  // nodes are reachable and usable from the keyboard
  await page.getByRole("button", { name: "Show its connections" }).click();
  await expect(page.getByRole("button", { name: /^ENOC,/ })).toBeVisible();

  const between = page.locator(".wg-between");
  await between.getByPlaceholder(/Company or ticker/).fill("Emirates NBD");
  await between.getByRole("button", { name: "Search", exact: true }).click();
  // a choice the first time; once Emirates NBD is known its own name resolves directly
  const pickEnbd = between.getByRole("button", { name: /^Emirates NBD/ });
  const heading = between.getByRole("heading", { name: "Connections between Emaar Properties and Emirates NBD" });
  await expect(pickEnbd.or(heading)).toBeVisible();
  if (await pickEnbd.isVisible()) await pickEnbd.click();
  await expect(heading).toBeVisible();
  await expect(between).toContainText("No chain of up to 4 referenced links");
  await between.getByRole("checkbox", { name: "Include unreferenced statements" }).check();
  await expect(between.locator(".wg-path")).toContainText("United Arab Emirates");
});

test("an investigation is saved privately and listed", async ({ page }) => {
  await signUp(page, "wgs");
  await openEmaar(page);
  await page.getByRole("button", { name: "Save investigation" }).click();
  await page.locator("#wg-save-title, .wg-save input").first().fill("Who controls Emaar");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/saved=\d+/);
  await expect(page.getByRole("button", { name: "Update saved investigation" })).toBeVisible();
  await page.goto("/world-graph");
  await expect(page.locator(".wg-saved-list")).toContainText("Who controls Emaar");
});

for (const [w, h] of [[375, 812], [1440, 900]] as const) {
  test(`member graph view: layout and accessibility @ ${w}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await signUp(page, "wgl");
    await openEmaar(page);
    await page.waitForLoadState("networkidle").catch(() => undefined);
    expect(problems(await measure(page), w)).toEqual([]);
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${scheme} ${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")})`);
      expect(bad, bad.join("\n")).toEqual([]);
    }
    await page.screenshot({ path: `test-results/world-graph-${w}.png`, fullPage: false });
  });
}
