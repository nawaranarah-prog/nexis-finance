/* Records the public API responses the test pages need from the production site into tests/fixtures/api.json.
   Opt-in (it uses the internet):  RECORD_FIXTURES=1 npx playwright test --project=record
   Only GET responses of public read endpoints are kept (REPLAY in support/mockApi.ts) — nothing private, no AI. */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "@playwright/test";
import { keyFor, POST_REPLAY, REPLAY } from "../support/mockApi";
import { PAGES } from "../support/pages";

const SITE = process.env.RECORD_FROM ?? "https://nexis-finance-five.vercel.app";

test("record public API fixtures", async ({ browser }) => {
  test.skip(!process.env.RECORD_FIXTURES, "set RECORD_FIXTURES=1 to re-record fixtures from production");
  test.setTimeout(15 * 60_000);
  const out: Record<string, { status: number; body: unknown }> = {};
  const extra = ["/pulse?sort=latest", "/markets?list=uae", "/finstagram/s/EMAAR.AE", "/compare"];
  for (const path of [...PAGES.map((p) => p.path), ...extra]) {
    const page = await browser.newPage();
    const pending: Promise<void>[] = [];
    page.on("response", (res) => {
      const url = new URL(res.url());
      const method = res.request().method();
      const post = method === "POST" && POST_REPLAY.some((r) => r.test(url.pathname));
      if (!post && (method !== "GET" || !REPLAY.some((r) => r.test(url.pathname)))) return;
      pending.push(res.json().then((body) => { out[post ? `POST ${url.pathname}` : keyFor(url)] = { status: res.status(), body }; }, () => undefined));
    });
    await page.goto(SITE + path, { waitUntil: "domcontentloaded", timeout: 120_000 });
    await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => undefined);
    await page.waitForTimeout(2000);
    if (path === "/markets") {  // the search the functional tests use
      await page.locator("main input").first().fill("Emaar");
      await page.waitForTimeout(3000);
    }
    await Promise.all(pending);
    await page.close();
  }
  mkdirSync(join(import.meta.dirname, "..", "fixtures"), { recursive: true });
  writeFileSync(join(import.meta.dirname, "..", "fixtures", "api.json"), JSON.stringify(out));
  console.log(`saved ${Object.keys(out).length} responses`);
});
