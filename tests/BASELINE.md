# Baseline test run (Step 0 — before any product change)

Run on 2026-10-10, branch `nexis-overhaul` at the PR #15 code (no product changes). Local production build of the
website (`vite build` + `vite preview`) against the local API on a fresh SQLite database, **offline**: the API's
outbound HTTP goes to a dead proxy; market/Pulse/social data are fixtures recorded from the production API's public
responses (`frontend/tests/fixtures/api.json`, 31 responses); AI answers are mocked. Nothing paid was called.

## Summary

| Check | Command | Result |
|---|---|---|
| Build | `npm run build` | **PASS** |
| Type check | `npm run typecheck` | **PASS** |
| Lint (new: oxlint) | `npm run lint` | **FAIL** — 90 errors, 26 warnings |
| Frontend unit tests (new: Vitest) | `npm test` | **PASS** — 6/6 |
| Backend tests (pytest) + lint (ruff) | `pytest`, `ruff check app tests` | **PASS** — 283/283, ruff clean |
| Functional e2e (Chromium, Firefox, WebKit) | `npm run test:e2e` | **27 / 30 passed** |
| Responsive (16 pages × 9 viewports + nav + forms) | `npm run test:responsive` | **73 / 153 passed** |
| Accessibility (axe, 16 pages × 1280 & 375) | `npm run test:a11y` | **0 / 32 passed** |
| Internal links (rendered-SPA crawl) | `npm run test:links` | **PASS** — 373 routes checked, 0 broken |
| Lighthouse (mobile, **production site**) | `npm run lighthouse` | see below |
| **Total Playwright** | `npm run test:all` | **101 passed, 115 failed** |

## Functional (27/30)

Passing in all three browsers: home + market summary, search "Emaar" → ticker page, ticker page (price, change,
chart, dividends, valuation link, chart range switch), bonds list + bond page (yield, maturity, coupon), compare,
Pulse list + story disclosure, Finstagram feed + post, sign-up/sign-out/sign-in, visitor AI cap (4th question → 401).

Failing (3): **"AI assistant answers (mocked) with the disclaimer"** in Chromium, Firefox and WebKit — at the
default 1280×720 window the Help Agent launcher sits on top of the Advisor's send button and intercepts the click
(a real bug, from PR #15).

## Responsive (73/153)

| Viewport | 320 | 375 | 390 | 412 | 768 | 1024 | 1280 | 1440 | 1920 |
|---|---|---|---|---|---|---|---|---|---|
| Pages passing (of 16) | 0 | 0 | 0 | 0 | 0 | 16 | 16 | 16 | 16 |

* Every page fails at ≤ 768px on **tap targets < 44px** (top bar: menu 38×30, search 104×32, notifications 32×32,
  Sign in 58×30; plus chips, tabs, table links) and, at ≤ 480px, on **text < 14px** (most UI text is 12–13px).
* **Valuation** additionally scrolls sideways by 60px at 320/375px (three tables wider than the screen, outside any
  scroll container).
* No other page scrolls sideways; nothing else extends past the right edge; no clipped text; charts fit.
* Phone/tablet navigation (5/5) and phone forms (4/4) pass.

## Accessibility (0/32)

| axe rule (serious) | Pages × sizes affected |
|---|---|
| color-contrast | 32 of 32 (muted grey text on light surfaces; up to 320 nodes on Markets) |
| link-in-text-block | 6 (bond, Pulse story, terms) |
| link-name | 4 (Finstagram feed and post: image links without text) |
| scrollable-region-focusable | 2 (ticker and compare at 375px) |
No "critical" violations.

## Lighthouse (mobile) — production site (`main`, not this branch)

| Page | Performance | Accessibility | Best practices | SEO |
|---|---|---|---|---|
| Home | 70 | 96 | 100 | 100 |
| Ticker (EMAAR.AE) | 28 | 96 | 100 | 100 |
| Pulse story | 88 | 89 | 100 | 100 |

SEO scores 100 because `index.html` has valid tags — but they are the homepage's title/description/canonical on
every route (see AUDIT.md §1), which Lighthouse doesn't detect. Ticker performance is the main gap (large Plotly
bundle on first load). Production was measured because the local stack serves fixture-less API calls offline, which
would distort performance; later runs will also measure the branch build.

## Notes on the test suite itself

While establishing the baseline I fixed five bugs **in the new tests** (not in the product), so the baseline measures
the site rather than the tests: off-screen elements of the closed mobile drawer were counted as tap targets/text;
"Valuation" link locator matched the sidebar too; the sign-up URL check matched `?next=/my-nexis`; chart range
buttons are radios, not buttons; the nav test re-resolved positional locators after expanding groups; the search
test clicked a quick-pick chip under the results dropdown. Full results: `frontend/test-results/` (not committed).
