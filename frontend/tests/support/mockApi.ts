import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { Page, Route } from "@playwright/test";

/* Tests never touch the internet or a paid service:
   - public read endpoints (markets, Pulse, social, intelligence) are answered from fixtures recorded once from the
     production API's public responses (tests/fixtures/api.json, made by tests/scripts/record-fixtures.ts);
   - the AI endpoints (Advisor stream, Help answers) are answered by canned fixtures in the real wire format;
   - everything else (sign-in, writes, billing status, usage caps) goes to the local backend, which runs with outbound
     HTTP blocked (see playwright.config.ts), so it can't reach providers either. */

type Fixture = { status: number; body: unknown };
const FILE = join(import.meta.dirname, "..", "fixtures", "api.json");
const fixtures: Record<string, Fixture> = existsSync(FILE) ? JSON.parse(readFileSync(FILE, "utf-8")) : {};

/** Read endpoints that may be replayed. Anything private or stateful is never replayed. */
export const REPLAY = [/^\/api\/markets\//, /^\/api\/pulse\/(feed|discussions\/[^/]+$|meta|engine$|topics|asset\/|search|overview|trending)/,
  /^\/api\/social\//, /^\/api\/intelligence\//, /^\/api\/home/, /^\/api\/research\/market/, /^\/api\/valuation\//, /^\/api\/compare/,
  /^\/api\/help\/articles/, /^\/api\/billing\/plans/];
/** Read-only computations the pages run with POST (valuation, comparison); replayed by path. */
export const POST_REPLAY = [/^\/api\/valuation\/[^/]+$/, /^\/api\/markets\/compare$/];
const NEVER = [/^\/api\/auth\//, /^\/api\/me\//, /^\/api\/billing\/(status|checkout|portal|cancel|resume|confirm|change-plan|webhook)/, /^\/api\/legal\//];

export function keyFor(url: URL): string {
  const q = [...url.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("&");
  return `${url.pathname}${q ? `?${q}` : ""}`;
}

function byPath(path: string): Fixture | undefined {
  const hit = Object.keys(fixtures).find((k) => k.split("?")[0] === path);
  return hit ? fixtures[hit] : undefined;
}

export const AI_ANSWER = [
  "Emaar's recent results show strong development sales, but the shares also carry real-estate cycle risk.",
  "",
  "**Factors to weigh:** revenue growth, the dividend record, valuation against peers, and how sensitive Dubai property is to rates.",
  "",
  "*Educational information only, not investment advice.*",
].join("\n");

export function sse(events: unknown[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

export async function mockApi(page: Page, opts: { ai?: "ok" | "error" } = {}) {
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (req.method() === "POST" && path === "/api/advisor/chat/stream") {
      if (opts.ai === "error") return route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([{ type: "error", message: "The AI couldn't answer (mock)." }]) });
      return route.fulfill({
        status: 200, contentType: "text/event-stream",
        body: sse([{ type: "status", text: "Checking Emaar's latest data" }, { type: "delta", text: AI_ANSWER },
          { type: "done", meta: { mode: "ai", tools: [], symbols: ["EMAAR.AE"], news: [], disclaimer: "Educational analysis from public data — not personalised investment advice." } }]),
      });
    }
    if (req.method() === "POST" && path === "/api/help/ask") {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ answer: "Open Settings → Billing. (mock)", sources: [], links: [], ai: true }) });
    }
    if (req.method() === "POST" && path === "/api/pulse/engine/tick") return route.fulfill({ status: 200, contentType: "application/json", body: '{"skipped":true,"editorial":0}' });
    if (req.method() === "POST" && POST_REPLAY.some((r) => r.test(path))) {
      const f = fixtures[`POST ${path}`];
      if (f) return route.fulfill({ status: f.status, contentType: "application/json", body: JSON.stringify(f.body) });
    }
    if (req.method() === "GET" && REPLAY.some((r) => r.test(path)) && !NEVER.some((r) => r.test(path))) {
      const f = fixtures[keyFor(url)] ?? byPath(path);
      if (f) return route.fulfill({ status: f.status, contentType: "application/json", body: JSON.stringify(f.body) });
    }
    return route.continue();
  });
}
