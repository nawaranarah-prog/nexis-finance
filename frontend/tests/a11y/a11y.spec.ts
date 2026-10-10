import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "../support/mockApi";
import { PAGES } from "../support/pages";

/* axe-core on every main page (WCAG 2.1 A/AA rules), light and dark mode: no "serious" or "critical" violations. */
for (const p of PAGES) {
  for (const [w, h] of [[1280, 800], [375, 812]] as const) for (const scheme of ["light", "dark"] as const) {
    test(`axe: ${p.name} @ ${w} ${scheme}`, async ({ page }, info) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width: w, height: h });
      await mockApi(page);
      await page.goto(p.path, { waitUntil: "domcontentloaded" });
      await page.locator(p.ready).first().waitFor({ timeout: 20_000 }).catch(() => undefined);
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => undefined);
      const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      const summary = bad.map((v) => `${v.impact} ${v.id} (${v.nodes.length}): ${v.help}`);
      info.annotations.push({ type: "axe", description: JSON.stringify({ page: p.name, w, scheme, summary }) });
      expect(summary, summary.join("\n")).toEqual([]);
    });
  }
}
