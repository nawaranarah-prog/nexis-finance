import { expect, test } from "@playwright/test";
import { mockApi } from "../support/mockApi";
import { POST, PULSE_STORY } from "../support/pages";

/* Visitors read freely; restricted actions open the "join" sheet (never a redirect), and after signing up they come
   back to the same page with anything they typed kept (not sent). The API refuses the same actions (test_access.py). */

test.beforeEach(async ({ page }) => { await mockApi(page); });

const sheet = (page: import("@playwright/test").Page) => page.getByRole("dialog");

test("a visitor reads a Pulse story without being asked to sign up", async ({ page }) => {
  await page.goto(PULSE_STORY);
  await expect(page.locator("main h1")).toBeVisible();
  await page.waitForTimeout(800);
  await expect(sheet(page)).toHaveCount(0);
});

test("following a Pulse story asks a visitor to join, and Continue reading closes it", async ({ page }) => {
  await page.goto(PULSE_STORY);
  await page.locator("main").getByRole("button", { name: "Follow", exact: true }).first().click();
  await expect(sheet(page)).toContainText("Join the Nexis community");
  await expect(page).toHaveURL(new RegExp(PULSE_STORY.replace(/[/.]/g, "\\$&")));  // no redirect
  await page.getByRole("button", { name: "Continue reading" }).click();
  await expect(sheet(page)).toHaveCount(0);
});

test("the AI: a visitor's question is kept through sign-up and offered again, not sent", async ({ page }) => {
  let asked = 0;
  page.on("request", (r) => { if (r.url().includes("/api/advisor/chat")) asked++; });
  await page.goto("/advisor");
  await expect(page.getByText("The AI advisor is for members")).toBeVisible();
  await page.locator(".chat-input textarea").fill("What is Emaar's dividend yield?");
  await page.locator(".chat-input button").click();
  await expect(sheet(page)).toContainText("Create a free account to ask the AI");
  await sheet(page).getByRole("link", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/login\?mode=signup&next=%2Fadvisor/);
  await page.waitForLoadState("networkidle");
  const mail = `gate${Date.now()}${Math.floor(Math.random() * 1e5)}@example.com`;
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `198.20.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}` });
  await page.fill("input[type=email]", mail);
  await page.fill("input[type=password]", "gate-pass-12345");
  await page.check(".login-terms input");
  await page.click("button.login-submit");
  await page.waitForURL((u) => u.pathname === "/advisor");
  await expect(page.locator(".chat-input textarea")).toHaveValue("What is Emaar's dividend yield?");
  expect(asked).toBe(0);
});

test("Help: articles are open, AI answers ask a visitor to join", async ({ page }) => {
  await page.goto("/markets");
  await page.getByRole("button", { name: "Help and support" }).click();
  await page.fill("#help-search", "cancel");
  await expect(page.locator(".help-list button").first()).toBeVisible();
  await page.fill("#help-q", "How do I cancel?");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /ask the AI/ })).toBeVisible();
});

test("reports ask a visitor to join", async ({ page }) => {
  await page.goto("/compare?s=FAB.AD,EMIRATESNBD.AE");
  await page.getByRole("button", { name: "Download PDF report" }).click();
  await expect(page.getByRole("dialog")).toContainText("Create a free account to make reports");
});

test("liking a Finstagram post asks a visitor to join", async ({ page }) => {
  await page.goto(POST);
  await page.getByRole("button", { name: "Like" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("like, comment and follow");
});

test("password reset says plainly when email isn't set up", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page).toHaveURL(/\/forgot-password/);
  await expect(page.getByRole("status")).toContainText("isn't available yet");
});

test("Finstagram is a news feed: a member can react but not post", async ({ page }) => {
  const { signUp } = await import("../support/account");
  await signUp(page, "fin");
  await page.goto("/finstagram");
  await expect(page.getByText("Finstagram is a news feed: news pages post here.")).toBeVisible();
  await expect(page.locator(".composer textarea")).toHaveCount(0);
  expect((await page.request.post("/api/social/posts", { multipart: { body: "my own post" } })).status()).toBe(403);
});
