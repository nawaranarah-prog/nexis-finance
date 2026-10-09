# Nexis overhaul — progress

## Step 0 — audit and test setup (no product changes)

**What changed**
* `AUDIT.md` — framework, data sources (2 high-risk unofficial sources), AI prompt (gives buy/sell/position-size
  advice today), Pulse/Finstagram generation, wording inventory, routes, responsive problems, decisions needed.
* Test tooling (all free, `frontend/` devDependencies): Playwright (Chromium/Firefox/WebKit), @axe-core/playwright,
  Vitest, Lighthouse, linkinator (for the server-rendered pages in Step 4), oxlint (typescript-eslint doesn't support
  TypeScript 7 yet).
* Suites: `frontend/tests/e2e` (functional), `responsive` (16 pages × 9 viewports + nav + forms, screenshots to
  `tests/screenshots/`), `a11y` (axe), `links` (rendered-SPA crawl), `live` (opt-in smoke, `LIVE_SMOKE=1`),
  `record` (opt-in fixture recorder, `RECORD_FIXTURES=1`), `lighthouse/run.mjs`; Vitest unit tests in `src/**/*.test.ts`.
* Offline test stack (`frontend/playwright.config.ts`): production build + local API on throwaway SQLite with
  outbound HTTP blocked; fixtures for market/Pulse/social data (`tests/support/mockApi.ts`); AI mocked.
* npm scripts: `lint`, `test`, `test:e2e`, `test:responsive`, `test:a11y`, `test:links`, `test:all`, `test:live`,
  `fixtures:record`, `lighthouse`.

**Results** — see `tests/BASELINE.md`: build ✓, typecheck ✓, lint 90 errors, unit 6/6, backend 283/283,
e2e 27/30, responsive 73/153, axe 0/32, links ✓ (373 routes). Lighthouse (production, mobile): home 70/96/100/100,
ticker 28/96/100/100, Pulse story 88/89/100/100.

**Open**
* Step 0 changes no product code, so the Definition of Done items about passing suites apply from Step 1 on.
* Decisions needed: AUDIT.md §9 (route `/research` is taken; Step 6 vs Stripe billing; AI cap; data licensing;
  hiding bot posts; SSR approach; CMA wording; company details).
