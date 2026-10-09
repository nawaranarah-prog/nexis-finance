/* Lighthouse (mobile settings) on the home page, one ticker page and one Pulse story.
   LH_BASE picks the site (default: production). Uses Playwright's Chromium, so no Chrome install is needed.
   Writes test-results/lighthouse.json and prints the four scores per page.  Usage: npm run lighthouse */
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = (process.env.LH_BASE ?? "https://nexis-finance-five.vercel.app").replace(/\/$/, "");
const PAGES = { home: "/", "ticker-emaar": "/markets/EMAAR.AE", "pulse-story": "/pulse/d/n368jc46bg/us-stocks-how-much-good-news-is-already-priced-in" };
mkdirSync("test-results/lighthouse", { recursive: true });
const out = {};
for (const [name, path] of Object.entries(PAGES)) {
  const file = `test-results/lighthouse/${name}.json`;
  try {
    execFileSync(process.execPath, ["node_modules/lighthouse/cli/index.js", BASE + path, "--quiet", "--output=json", `--output-path=${file}`,
      "--only-categories=performance,accessibility,best-practices,seo", "--chrome-flags=--headless=new --no-sandbox"],
      { env: { ...process.env, CHROME_PATH: chromium.executablePath() }, stdio: "inherit", timeout: 240_000 });
    const r = JSON.parse(readFileSync(file, "utf-8"));
    out[name] = Object.fromEntries(Object.entries(r.categories).map(([k, v]) => [k, Math.round(v.score * 100)]));
  } catch (e) {
    out[name] = { error: String(e).slice(0, 200) };
  }
  console.log(name, JSON.stringify(out[name]));
}
writeFileSync("test-results/lighthouse.json", JSON.stringify({ base: BASE, at: new Date().toISOString(), scores: out }, null, 2));
