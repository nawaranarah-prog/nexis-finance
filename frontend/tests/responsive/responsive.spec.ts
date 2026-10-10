import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";
import { PAGES, VIEWPORTS } from "../support/pages";

/* Every page × every viewport: no sideways scrolling, nothing past the right edge, no clipped text, 44px tap targets
   (≤ 768px), 14px body text (phones), tables/charts contained. Full-page screenshots go to <repo>/tests/screenshots
   for the human review the brief requires. */

const SHOTS = join(import.meta.dirname, "..", "..", "..", "tests", "screenshots");
mkdirSync(SHOTS, { recursive: true });

for (const p of PAGES) {
  for (const [w, h] of VIEWPORTS) {
    test(`${p.name} @ ${w}x${h}`, async ({ page }, info) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.setViewportSize({ width: w, height: h });
      await mockApi(page);
      await page.goto(p.path, { waitUntil: "domcontentloaded" });
      await page.locator(p.ready).first().waitFor({ timeout: 20_000 }).catch(() => undefined);
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);
      await page.waitForTimeout(400);
      await page.screenshot({ path: join(SHOTS, `${p.name}-${w}.png`), fullPage: true });
      const report = await measure(page);
      const found = problems(report, w);
      info.annotations.push({ type: "layout", description: JSON.stringify({ page: p.name, w, h, found, report }) });
      expect.soft(errors, "uncaught page errors").toEqual([]);
      expect(found, found.join("\n")).toEqual([]);
    });
  }
}

for (const [w, h] of VIEWPORTS.filter(([w]) => w <= 768)) {
  test(`phone/tablet navigation opens, reaches every link and closes @ ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await mockApi(page);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const menu = page.getByRole("button", { name: "Open navigation" });
    await expect(menu).toBeVisible();
    await menu.click();
    const drawer = page.locator("aside.sidebar, aside").first();
    await expect(drawer).toBeInViewport();
    // open every folded group (as a person would), then every link must be scrollable into view inside the drawer
    const folded = drawer.locator("button.nav-group.toggle[aria-expanded=false]:not([disabled])");
    for (let i = 0; i < 12 && (await folded.count()) > 0; i++) await folded.first().click();
    await page.waitForTimeout(400);
    const unreachable = await drawer.evaluate((d) => [...d.querySelectorAll("a[href]")].filter((a) => {
      a.scrollIntoView({ block: "center" });
      const r = a.getBoundingClientRect();
      return !(r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth);
    }).map((a) => a.textContent?.trim()));
    expect(unreachable, "nav links that can't be scrolled into view").toEqual([]);
    await page.keyboard.press("Escape");
    await page.locator(".scrim.open").click({ position: { x: w - 5, y: 5 } }).catch(() => undefined);
    await expect(drawer).not.toBeInViewport({ ratio: 0.5 });
  });
}

for (const [w, h] of VIEWPORTS.filter(([w]) => w <= 480)) {
  test(`forms stay usable on phones @ ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await mockApi(page);
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.locator("button.login-submit").scrollIntoViewIfNeeded();  // a sign-in form may be taller than a small phone
    await expect(page.locator("button.login-submit")).toBeInViewport();
    await page.goto("/advisor", { waitUntil: "domcontentloaded" });
    const input = page.locator(".chat-input textarea");
    await input.focus();
    await expect(input).toBeInViewport();
    await expect(page.locator(".chat-input button")).toBeInViewport();
  });
}
