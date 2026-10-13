import { execFileSync } from "node:child_process";
import { appendFileSync, closeSync, mkdirSync, openSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { measure, problems } from "../support/layout";
import { mockApi } from "../support/mockApi";
import { LAUNCH_WIDTHS } from "../support/pages";

/* Responsive audit for the research tools (fails on any layout problem), which need a loaded research dataset. The test backend starts
   empty, so the offline demo seeder (scripts/seed_demo.py --minimal: synthetic data and portfolios, no network) fills
   it once. Results go to test-results/responsive-audit-research.jsonl. */

const ROOT = join(import.meta.dirname, "..", "..", "..");
const OUT = join(ROOT, "frontend", "test-results");
const DB = join(OUT, "e2e.db").replace(/\\/g, "/");
const PY = join(ROOT, "backend", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const FILE = join(OUT, "responsive-audit-research.jsonl");
mkdirSync(OUT, { recursive: true });

const RESEARCH: [string, string][] = [
  ["research", "/research"], ["asset-research", "/asset-research"], ["portfolio-lab", "/portfolio-lab"], ["risk", "/risk"],
  ["stress", "/stress-testing"], ["factors", "/factors"], ["strategies", "/strategies"], ["backtesting", "/backtesting"],
  ["ml", "/machine-learning"], ["regimes", "/regimes"], ["anomalies", "/anomalies"], ["data-quality", "/data-quality"],
  ["experiments", "/experiments"], ["reports", "/reports"], ["xray", "/xray"], ["graph", "/graph"], ["transactions", "/transactions"],
];

test.beforeAll(async ({ request }) => {
  test.setTimeout(300_000);
  const has = async () => ((await (await request.get("http://127.0.0.1:8010/api/datasets")).json()) as unknown[]).length > 0;
  if (await has()) return;
  let lock: number | null = null;
  try { lock = openSync(join(OUT, "seed.lock"), "wx"); } catch { /* another worker is seeding */ }
  if (lock !== null) {
    execFileSync(PY, [join(ROOT, "scripts", "seed_demo.py"), "--minimal"], { cwd: ROOT, env: { ...process.env, NEXIS_DATABASE_URL: `sqlite:///${DB}` }, stdio: "ignore" });
    closeSync(lock);
  }
  for (let i = 0; i < 240 && !(await has()); i++) await new Promise((r) => setTimeout(r, 1000));
});

for (const [name, path] of RESEARCH) {
  for (const [w, h] of LAUNCH_WIDTHS) {
    test(`research audit ${name} @ ${w}`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message.slice(0, 160)));
      await page.setViewportSize({ width: w, height: h });
      await mockApi(page);
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await page.locator("main h1, main h2").first().waitFor({ timeout: 15_000 }).catch(() => undefined);
      await page.waitForLoadState("networkidle", { timeout: 4_000 }).catch(() => undefined);
      await page.waitForTimeout(500);
      const report = await measure(page);
      const empty = await page.getByText("No dataset loaded yet").count();
      const heading = await page.locator("main h1, main h2").first().textContent({ timeout: 1000 }).catch(() => null);
      appendFileSync(FILE, JSON.stringify({ name, path, w, heading, empty, found: problems(report, w), errors, report }) + "\n");
      expect(empty, "the research dataset wasn't loaded, so the page wasn't really tested").toBe(0);
      expect.soft(errors, "uncaught page errors").toEqual([]);
      expect(problems(report, w), problems(report, w).join("\n")).toEqual([]);
    });
  }
}
