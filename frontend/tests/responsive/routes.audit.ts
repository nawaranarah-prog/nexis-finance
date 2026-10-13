import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { signUp } from "../support/account";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";
import { LAUNCH_WIDTHS, POST, PULSE_STORY } from "../support/pages";

/* Full responsive audit: every route × the 11 launch widths, signed out and signed in. Fails on any layout problem
   (see support/layout.ts), an uncaught page error, or desktop and phone navigation shown together, and writes one JSON
   line per page and width to test-results/responsive-audit.jsonl. Opt-in: npx playwright test --project=audit. */

const OUT = join(import.meta.dirname, "..", "..", "test-results");
mkdirSync(OUT, { recursive: true });
const FILE = join(OUT, "responsive-audit.jsonl");


const PUBLIC: [string, string][] = [
  ["home", "/"], ["markets", "/markets"], ["bonds", "/markets?list=uae_bonds"], ["ticker", "/markets/EMAAR.AE"], ["bond", "/markets/UAE0734USD.BOND"],
  ["valuation", "/valuation/EMAAR.AE"], ["compare", "/compare"], ["world-graph", "/world-graph"], ["time-machine", "/time-machine"],
  ["devils-advocate", "/devils-advocate"], ["my-research", "/my-research"], ["advisor", "/advisor"], ["pulse", "/pulse"], ["pulse-story", PULSE_STORY],
  ["pulse-latest", "/pulse/latest"], ["pulse-trending", "/pulse/trending"], ["pulse-topics", "/pulse/topics"], ["pulse-asset", "/pulse/asset/EMAAR.AE"],
  ["pulse-search", "/pulse/search?q=emaar"], ["finstagram", "/finstagram"], ["post", POST], ["fin-symbol", "/finstagram/s/EMAAR.AE"],
  ["fin-tag", "/finstagram/t/markets"], ["pricing", "/pricing"], ["login", "/login"], ["signup", "/login?mode=signup"],
  ["forgot-password", "/forgot-password"], ["reset-password", "/reset-password?token=x"], ["verify-email", "/verify-email"], ["terms", "/terms"],
  ["privacy", "/privacy"], ["disclaimer", "/disclaimer"], ["guidelines", "/community-guidelines"], ["ai-disclosure", "/ai-disclosure"],
  ["not-found", "/no-such-page"], ["research", "/research"], ["asset-research", "/asset-research"], ["portfolio-lab", "/portfolio-lab"],
  ["risk", "/risk"], ["stress", "/stress-testing"], ["factors", "/factors"], ["strategies", "/strategies"], ["backtesting", "/backtesting"],
  ["ml", "/machine-learning"], ["regimes", "/regimes"], ["anomalies", "/anomalies"], ["experiments", "/experiments"], ["reports", "/reports"],
  ["market-data", "/market-data"], ["data-quality", "/data-quality"], ["system", "/system"], ["connections", "/connections"],
  ["intelligence", "/intelligence"], ["xray", "/xray"], ["transactions", "/transactions"], ["graph", "/graph"], ["reconciliation", "/reconciliation"],
  ["lineage", "/lineage"], ["economic", "/economic"], ["audit", "/audit"], ["developer", "/developer"],
];
const MEMBER: [string, string][] = [
  ["m-home", "/"], ["m-my-nexis", "/my-nexis"], ["m-investments", "/my-nexis/investments"], ["m-watchlist", "/my-nexis/watchlist"],
  ["m-alerts", "/my-nexis/alerts"], ["m-activity", "/my-nexis/activity"], ["m-settings", "/settings"], ["m-notifications", "/notifications"],
  ["m-advice-history", "/advisor/history"], ["m-advisor", "/advisor"], ["m-pricing", "/pricing"], ["m-welcome", "/pro/welcome"],
  ["m-following", "/pulse/following"], ["m-saved", "/pulse/saved"], ["m-portfolio", "/portfolio"],
];

for (const [group, routes] of [["public", PUBLIC], ["member", MEMBER]] as const) {
  for (const [name, path] of routes) {
    for (const [w, h] of LAUNCH_WIDTHS) {
      test(`audit ${name} @ ${w}`, async ({ page }) => {
        const errors: string[] = [];
        page.on("pageerror", (e) => errors.push(e.message.slice(0, 160)));
        await page.setViewportSize({ width: w, height: h });
        await mockApi(page);
        if (group === "member") await signUp(page, "audit");
        await page.goto(path, { waitUntil: "domcontentloaded" });
        await page.locator("main h1, main h2, form").first().waitFor({ timeout: 15_000 }).catch(() => undefined);
        await page.waitForLoadState("networkidle", { timeout: 2_500 }).catch(() => undefined);
        await page.waitForTimeout(300);
        const report = await measure(page);
        const navs = await page.evaluate(() => {
          const shown = (sel: string) => [...document.querySelectorAll(sel)].some((el) => {
            const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth && s.display !== "none" && s.visibility !== "hidden";
          });
          return { sidebar: shown("aside.sidebar"), tabbar: shown(".tabbar"), menuButton: shown("button[aria-label='Open navigation']") };
        });
        const h1 = await page.locator("main h1").first().textContent({ timeout: 1000 }).catch(() => null);
        appendFileSync(FILE, JSON.stringify({ group, name, path, w, h1, found: problems(report, w), navs, errors, report }) + "\n");
        expect(navs.sidebar && navs.tabbar, "desktop and phone navigation shown together").toBe(false);
        expect.soft(errors, "uncaught page errors").toEqual([]);
        expect(problems(report, w), problems(report, w).join("\n")).toEqual([]);
      });
    }
  }
}
