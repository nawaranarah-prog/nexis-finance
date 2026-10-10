import { expect, type Page, test } from "@playwright/test";
import { mockApi } from "../support/mockApi";
import { POST, PULSE_STORY } from "../support/pages";

/* Core flows that exist today, in Chromium, Firefox and WebKit. Market data and AI answers come from fixtures;
   accounts, sessions and usage caps are real (local backend). Flows the brief adds later get their tests in the
   step that builds them. */

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`); });
  return errors;
}

test.beforeEach(async ({ page }) => { await mockApi(page); });

test("home loads with the market summary", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.getByText("DFM General").first()).toBeVisible();
  await expect(page.locator(".strip-cell").first()).toContainText(/\d/);
  expect(errors).toEqual([]);
});

test("search finds Emaar and opens the ticker page", async ({ page }) => {
  await page.goto("/markets");
  await page.locator("main input").first().fill("Emaar");
  const hit = page.locator(".sym-search").getByText("EMAAR.AE").first();  // the search result, not the quick-pick chip under it
  await expect(hit).toBeVisible();
  await hit.click();
  await expect(page).toHaveURL(/\/markets\/EMAAR\.AE/);
  await expect(page.locator("main h1")).toContainText(/Emaar/i);
});

test("ticker page: price, change, chart, dividends and valuation link; chart range switches", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/markets/EMAAR.AE");
  await expect(page.locator("main h1")).toContainText(/Emaar/i);
  await expect(page.getByText(/AED/).first()).toBeVisible();
  await expect(page.locator(".js-plotly-plot, svg").first()).toBeVisible();
  await expect(page.getByText(/dividend/i).first()).toBeVisible();
  await expect(page.locator("main").getByRole("link", { name: "Valuation" })).toBeVisible();
  const range = page.locator("main").getByRole("radio", { name: "5Y", exact: true });
  await range.click();
  await expect(range).toHaveAttribute("aria-checked", "true");
  expect(errors).toEqual([]);
});

test("UAE bonds list loads and a bond page shows yield, maturity and coupon", async ({ page }) => {
  await page.goto("/markets?list=uae_bonds");
  await expect(page.locator("main table")).toBeVisible();
  await page.goto("/markets/UAE0734USD.BOND");
  for (const word of [/yield/i, /maturity/i, /coupon/i]) await expect(page.getByText(word).first()).toBeVisible();
});

test("compare shows the selected stocks side by side", async ({ page }) => {
  await page.goto("/compare?s=FAB.AD,EMIRATESNBD.AE");
  await expect(page.getByText("FAB.AD").first()).toBeVisible();
  await expect(page.getByText("EMIRATESNBD.AE").first()).toBeVisible();
});

test("AI assistant answers (mocked) with the disclaimer", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/advisor");
  await page.locator(".chat-input textarea").fill("Should I buy Emaar?");
  await page.locator(".chat-input button").click();
  await expect(page.getByText("Factors to weigh").first()).toBeVisible();
  await expect(page.getByText(/not (personalised )?investment advice|not personalised financial advice/i).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("Pulse lists discussions and a story shows its label and disclosure", async ({ page }) => {
  await page.goto("/pulse");
  await expect(page.locator(".np-row, article").first()).toBeVisible();
  await page.goto(PULSE_STORY);
  await expect(page.locator("main h1")).toBeVisible();
  await expect(page.getByText(/not advice/i).first()).toBeVisible();
});

test("Finstagram feed and a post load", async ({ page }) => {
  await page.goto("/finstagram");
  await expect(page.locator("main")).toContainText(/\w{20,}/);
  await page.goto(POST);
  await expect(page.locator("main")).toContainText(/\w{10,}/);
});

test("sign up, sign out and sign in again", async ({ page }) => {
  const mail = `e2e${Date.now()}${Math.floor(Math.random() * 1e4)}@example.com`;
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `198.18.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}` });
  await page.goto("/login?mode=signup&next=/my-nexis");
  await page.waitForLoadState("networkidle");  // the login page re-renders once the session check returns
  await page.fill("input[type=email]", mail);
  await page.fill("input[type=password]", "e2e-pass-12345");
  await page.check(".login-terms input");
  await expect(page.locator(".login-terms input")).toBeChecked();
  await page.click("button.login-submit");
  await page.waitForURL((u) => u.pathname === "/my-nexis");  // the path, not the ?next= in the login URL
  expect((await (await page.request.get("/api/auth/me")).json()).user.email).toBe(mail);
  await page.request.post("/api/auth/logout");
  await page.goto("/login?next=/my-nexis");
  await page.waitForLoadState("networkidle");
  await page.fill("input[autocomplete=username]", mail);
  await page.fill("input[type=password]", "e2e-pass-12345");
  await page.click("button.login-submit");
  await page.waitForURL((u) => u.pathname === "/my-nexis");
});

test("visitors hit the AI usage cap after 3 questions (real cap, local backend)", async ({ page }) => {
  await page.unroute("**/api/**");  // let the stream reach the backend so it counts; no AI provider is configured there
  const ip = `198.19.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}`;  // its own bucket per run and browser
  const ask = () => page.request.post("/api/advisor/chat/stream", { data: { messages: [{ role: "user", content: "What is a sukuk?" }] },
    headers: { "x-forwarded-for": ip } });
  const codes = [];
  for (let i = 0; i < 4; i++) codes.push((await ask()).status());
  expect(codes.slice(0, 3).every((c) => c !== 401)).toBe(true);
  expect(codes[3]).toBe(401);
});
