import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signUp } from "../support/account";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";

/* Devil's Advocate against the real local API. The test backend has no AI provider, so a real challenge must fail
   honestly ("could not be completed", nothing concluded). The rendering of a completed challenge is checked with a
   server answer in the exact shape the API returns (validator logic itself: backend/tests/test_devils_advocate.py). */

test.beforeEach(async ({ page }) => { await mockApi(page); });

const THESIS = "Emaar will keep rising because Dubai property sales stay strong and its dividend is safe.";

async function createThesis(page: Page) {
  await page.goto("/devils-advocate");
  await page.getByPlaceholder("Search a company, e.g. Emaar").fill("Emaar");
  await page.getByRole("option", { name: /EMAAR\.AE/ }).first().click();
  await page.getByLabel(/Why you think/).fill(THESIS);
  await page.getByLabel("Time horizon (optional)").selectOption("1–3 years");
  await page.getByRole("button", { name: "Save thesis" }).click();
  await expect(page).toHaveURL(/\/devils-advocate\/\d+$/);
  await expect(page.locator(".da-thesis")).toContainText("Dubai property sales stay strong");
}

const EVIDENCE = [
  { id: 901, label: "E1", source_kind: "market_data", title: "Emaar Properties: Dividend (as of 2026-10-09)", excerpt: "Dividend yield 5.20%; Payout ratio 48.0%.", publisher: "Nexis market data (delayed, from the data provider)", url: null, published_at: null, retrieved_at: "2026-10-10T08:00:00Z" },
  { id: 902, label: "E2", source_kind: "news", title: "Analysts warn of oversupply as Dubai launches surge", excerpt: "Headline: Analysts warn of oversupply as Dubai launches surge", publisher: "Reuters", url: "https://example.org/b", published_at: "2026-10-01T00:00:00Z", retrieved_at: "2026-10-10T08:00:00Z" },
];
const completed = (id: number) => ({
  item: { id, title: "EMAAR.AE: Emaar will keep rising", status: "active", updated_at: "2026-10-10T08:00:00Z", state: { symbol: "EMAAR.AE", thesis: THESIS, horizon: "1–3 years", claims: [{ id: "C1", text: "Dubai property sales stay strong", kind: "assumption" }, { id: "C2", text: "The dividend is safe", kind: "prediction" }] } },
  runs: [{ id: 77, status: "completed", created_at: "2026-10-10T08:00:00Z", model_version: "da-1.0" }],
  latest: {
    id: 77, status: "completed", created_at: "2026-10-10T08:00:00Z", model_version: "da-1.0", snapshot: { evidence: EVIDENCE, computed_at: "2026-10-10T08:00:00Z" },
    output: {
      claims: [
        { id: "C1", text: "Dubai property sales stay strong", kind: "assumption", status: "contradicted", for: [], against: [{ evidence_id: 902, label: "E2", quote: "Analysts warn of oversupply" }], comment: "Supply risk is flagged.", unverified_numbers: [] },
        { id: "C2", text: "The dividend is safe", kind: "prediction", status: "supported", for: [{ evidence_id: 901, label: "E1", quote: "Payout ratio 48.0%" }], against: [], comment: "Payout below half of earnings.", unverified_numbers: [] },
      ],
      counter_case: [{ text: "New supply could cap prices and slow sales.", citations: [{ evidence_id: 902, label: "E2", quote: "warn of oversupply as Dubai launches surge" }], unverified_numbers: [] }],
      risks: [{ text: "A 23% fall in launches would hit revenue.", citations: [{ evidence_id: 902, label: "E2", quote: "Dubai launches surge" }], unverified_numbers: ["23"] }],
      signals: [{ text: "Transaction volumes falling for three months", direction: "weakens", unverified_numbers: [] }],
      gaps: ["Headlines only — the articles themselves were not available."],
      validation: { citations: 6, dropped: 2, points_dropped: 1 }, ai: { provider: "gateway", model: "openai/gpt-5.1" },
    },
  },
  changes_since_previous: null, daily_limit: 10,
});

test("visitors see what Devil's Advocate is and are asked to join", async ({ page }) => {
  await page.goto("/devils-advocate");
  await expect(page.locator("main h1")).toHaveText("Devil's Advocate");
  await page.getByRole("button", { name: "Create a free account to start" }).click();
  await expect(page.getByRole("dialog")).toContainText("Create a free account to use Devil's Advocate");
  expect((await page.request.post("/api/devils-advocate/theses", { data: {} })).status()).toBe(401);
});

test("without an AI provider, a challenge fails honestly and concludes nothing", async ({ page }) => {
  await signUp(page, "da");
  await createThesis(page);
  await page.getByRole("button", { name: "Challenge this thesis" }).click();
  // the test backend has no AI provider (and no market data either): either way, nothing is presented as a result
  await expect(page.getByRole("status").filter({ hasText: /could not be completed|Not enough evidence/ })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("No result is shown for this run.")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Evidence for/ })).toHaveCount(0);
  await page.goto("/devils-advocate");
  await expect(page.locator(".tm-saved")).toContainText("EMAAR.AE");
});

for (const [w, h] of [[375, 812], [1440, 900]] as const) {
  test(`a completed challenge shows checked quotes in sections A–H @ ${w}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await signUp(page, "dar");
    await createThesis(page);
    const id = Number(page.url().split("/").pop());
    await page.route(`**/api/devils-advocate/theses/${id}/analyse`, (r) => r.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(completed(id)) }));
    await page.getByRole("button", { name: "Challenge this thesis" }).click();
    for (const s of ["Your thesis, as claims", "Evidence for", "Evidence against", "The strongest case against", "Assumption check", "Risks", "What would change the view", "Gaps, limits and sources"]) {
      await expect(page.getByRole("heading", { name: s })).toBeVisible();
    }
    await expect(page.getByRole("region", { name: "Assumption status" })).toContainText("Contradicted");
    await expect(page.getByText("Figure not found in the evidence: 23")).toBeVisible();
    await expect(page.getByText(/6 citations from the AI, 2 removed/)).toBeVisible();
    await page.getByRole("link", { name: "Evidence E2" }).first().click();
    await expect(page).toHaveURL(/#ev-902$/);
    expect(problems(await measure(page), w)).toEqual([]);
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${scheme} ${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")})`);
      expect(bad, bad.join("\n")).toEqual([]);
    }
    await page.getByRole("heading", { name: "The strongest case against" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/devils-advocate-${w}.png` });
  });
}
