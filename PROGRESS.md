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

## Step 1 — blocking bugs, touch sizes, text sizes, contrast

**What changed**
* Help launcher no longer covers the AI Send button: on the AI chat pages (`/advisor`, `/assistant`) the floating
  launcher is not shown at any size; a "Help" button in the page header opens the same panel (`openHelp` event).
* Valuation no longer scrolls sideways: its four tables (and every other `.table-wrap` on the site) scroll inside their
  own focusable, labelled region (also fixes axe `scrollable-region-focusable`).
* Phone text ≥ 14px everywhere: every CSS font size under 14px is now `max(<size>, var(--fs-floor))`; `--fs-floor` is
  14px at ≤ 480px and 0 elsewhere (desktop typography unchanged). Charts use the same floor (`charts.ts` `fs()`).
* Tap targets ≥ 44×44 at ≤ 768px via base rules in `styles.css` (min-height on all controls; min-width on icon
  controls and short-label controls, which don't shrink; segment rows scroll). Inline links in sentences are exempt
  (WCAG inline exception); inline hashtags get padding instead of a taller line.
* Contrast: design tokens fixed for light and dark (`--muted`, `--accent`, new `--accent-solid` for white-on-blue,
  `--warn`), rating-bar colours, faded price-change text; links inside prose are underlined; image links labelled.
* Lint: touched files are clean (oxlint). Config: `prefer-tag-over-role` off (ARIA roles on divs are valid; not in
  jsx-a11y's recommended set); `tabIndex` allowed on `role="region"` (axe requires focusable scroll regions);
  `control-has-associated-label` uses jsx-a11y's recommended options plus `td`. Real findings fixed: autofocus removed
  (Markets, Login), empty header cells labelled, menus focusable, modal semantics, effects that copied props into state
  replaced with React's render-time pattern, components defined during render replaced with render helpers.
* Tests corrected (test bugs, not product changes): off-screen/visually-hidden elements, checkbox measured by its
  label, sign-up waits for the page to settle. Dark-mode axe runs added.

**Results** (offline stack): responsive 153/153, axe 64/64 (16 pages × 2 sizes × light/dark), functional 30/30
(Chromium, Firefox, WebKit), links 1/1 (373 routes), unit 6/6, build ✓, typecheck ✓, lint on touched files 0 errors.
Before: responsive 73/153, axe 0/32, functional 27/30. Project-wide lint: 40 errors / 22 warnings remain in files not
yet touched (was 90/26).

**Reviewed screenshots** at 375 and 1440 (valuation, home, compare, ticker, pulse, markets). Found and fixed visually:
overlapping "Try" links on Home (a global min-width had let flex items shrink below their text), over-underlined
headline lists. Pulse intro still says it collects from Reddit — stale since PR #15; fixed with the Pulse work.

**Open:** none for Step 1.

## Phase 1 — accounts and access (= overhaul Step 2)

**What changed**
* Server: visitors get 401 from every AI endpoint (advisor chat/stream, help answers, AI brief, research assistant),
  report generation (compare/valuation PDFs), and all Pulse/Finstagram participation (already enforced; now tested).
  Visitor AI allowance is 0 (`plans.ANONYMOUS`). Public reading (markets, Pulse, Finstagram, help articles, pricing)
  stays open.
* Reports are private: `reports.owner_id` (migration **0017**); listing shows shared workspace reports + your own;
  anyone else's report returns 404 (was: anyone could list and download every report by id).
* Account emails: `services/email.py` (SMTP via `NEXIS_SMTP_*`; says "not available yet" when unset),
  `services/account.py` — signed, expiring tokens: password reset (single use, signs out every device) and email
  verification (`users.email_verified_at`, migration 0017; Google sign-ins count as verified; changing the address
  clears it). "Forgot password" answers the same whether or not the address has an account.
* Login lockout: 10 wrong passwords for one account in 15 minutes → wait (even with the right password). Existing
  per-IP limits on login (20/h) and sign-up (5/h) kept.
* UI: one "join Nexis" sheet (`components/account.tsx`) for every restricted action — Create account / Sign in /
  Continue reading — that returns to the same page; anything typed (AI or help question) is kept and offered again,
  never sent automatically. Replaces the old redirects in Pulse and Finstagram. Advisor shows visitors a members-only
  note; Help keeps article search for everyone. New pages: /forgot-password, /reset-password, /verify-email; Settings
  shows email confirmation status with a resend link. Login's return address hardened against `/\host` tricks.
* Lint fixes in touched files (effects copying props into state, autofocus, clickable divs → buttons).

**Results** (offline stack): backend 313/313 (29 new access tests), ruff clean; Playwright 295/295 — functional 51/51
in Chromium/Firefox/WebKit (7 new access flows), responsive 171/171 (incl. new pages), axe 72/72 light+dark, links
clean; unit 6/6; build/typecheck ✓; lint on touched files 0 errors. Migration 0017 tested up/down/up on SQLite.
Screenshots reviewed: join sheet at 320 and 1440, Advisor visitor view, forgot-password page.

**Open / needs you:** an SMTP sender (`NEXIS_SMTP_HOST`, `NEXIS_SMTP_PORT`, `NEXIS_SMTP_USERNAME`,
`NEXIS_SMTP_PASSWORD`, `NEXIS_SMTP_FROM`) for verification and reset emails. Email verification is shown but not
required to use features (requiring it before a sender exists would lock everyone out).
