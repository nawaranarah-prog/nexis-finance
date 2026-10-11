import { expect, test } from "@playwright/test";
import { mockApi } from "../support/mockApi";
import { PAGES } from "../support/pages";

/* Broken internal links. The site is a client-rendered SPA, so a static checker (linkinator) only sees index.html;
   this crawls the rendered pages instead: it collects every internal link on the main pages and the navigation,
   opens each distinct route once, and fails on any that shows the app's 404 page ("This page doesn't exist"). */
test("no internal link leads to the 404 page", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  await mockApi(page);
  const found = new Map<string, string>();  // a concrete URL to open -> the page it was first seen on
  const patterns = new Set<string>();  // route patterns already sampled (one URL each)
  for (const p of PAGES.filter((x) => x.name !== "about")) {
    await page.goto(p.path, { waitUntil: "domcontentloaded" });
    await page.locator(p.ready).first().waitFor({ timeout: 20_000 }).catch(() => undefined);
    await page.waitForTimeout(500);
    const hrefs = await page.$$eval("a[href]", (as) => as.map((a) => (a as HTMLAnchorElement).href));
    for (const h of hrefs) {
      const u = new URL(h);
      if (u.origin !== new URL(page.url()).origin || u.pathname.startsWith("/api/")) continue;
      const route = u.pathname.replace(/\/(p|d|u|s|t|asset|ticker)\/[^/]+.*$/, "/$1/:id");  // one sample per dynamic route pattern
      // (this used to test the pattern against concrete URLs, so it never deduplicated and opened every story)
      if (!patterns.has(route)) { patterns.add(route); found.set(u.pathname + u.search, p.path); }
    }
  }
  const broken: string[] = [];
  for (const [route, from] of found) {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(700);
    if (await page.getByText("This page doesn't exist").count()) broken.push(`${route} (linked from ${from})`);
  }
  test.info().annotations.push({ type: "links", description: JSON.stringify({ checked: found.size, broken }) });
  expect(broken, broken.join("\n")).toEqual([]);
});
