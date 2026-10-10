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

## Phase 2 — shared data and evidence foundation

**What changed**
* Migration **0018** (additive only: seven new tables, nothing existing altered; downgrade drops exactly those):
  `fin_entities`, `fin_entity_aliases`, `evidence_records`, `fin_relationships`, `relationship_evidence`,
  `research_items`, `research_runs` (`app/models/knowledge.py`). Tested up/down/up on SQLite. `alembic check`
  reports no drift from the models. **Not applied to production.** It applies on the next API deploy, because the app
  runs migrations at start-up.
* Wikidata provider (`app/connectivity/providers/wikidata.py`): official, keyless, CC0. Entity search plus one query
  per company for parent/subsidiary, ownership, listing (with ticker), industry and country, including start/end dates
  and cited references. It sends a descriptive User-Agent, makes one request at a time at least 1 s apart, reports 429
  without retrying, and makes no request when external data is off. The first query design timed out on the Query
  Service. It was rewritten as explicit per-property patterns with a fixed join order, and now takes about 1 s.
* Entity and evidence service (`app/services/knowledge.py`):
  * Names are normalised (case, accents, punctuation and legal forms like PJSC/Inc are ignored).
  * Local aliases are checked first. Anything else comes back as **choices**: never auto-picked.
  * A member's confirmed choice is remembered.
  * Listing tickers link Nexis symbols (EMAAR → `EMAAR.AE`) for DFM, ADX, NYSE and Nasdaq only.
  * Evidence records are deduplicated and bounded (600-character excerpt; only http(s) links are stored).
  * No edge without evidence.
  * Each edge has a status:
    * `verified`: the statement cites a reference.
    * `uncertain`: the statement exists but cites nothing.
    * `historical`: the statement has ended.
    * `inferred`: reserved for numbers Nexis computes.
  * Relationships are cached for 7 days and search results for 1 day, with a stale fallback.
* [DATA-SOURCES.md](DATA-SOURCES.md) started (Wikidata, SEC EDGAR, and what free sources can't tell us). Step 3
  completes it.

**Results:** backend 327/327 (14 new, offline from Wikidata responses recorded for Emaar Properties); ruff clean.
Playwright Chromium functional 17/17 against the migrated stack (no frontend files changed in this phase). On the
real data, 17 Emaar statements came back: 1 cites a reference (Burj Khalifa → skyscrapercenter.com) and 16 cite none,
so most edges will honestly show as "unreferenced".

**Needs you:** a contact for `NEXIS_WIKIDATA_USER_AGENT` / `NEXIS_SEC_USER_AGENT` in production (a URL or email
address in the User-Agent string; both services ask for one).

## Phase 3 — World Graph

**What changed**
* `/world-graph`: members search a company and pick it from explicit choices (several companies are called "Emaar").
  The page then shows a deterministic radial graph of the relationships Wikidata reports, each with its evidence.
  * Line style shows evidence status (solid = referenced, dashed = unreferenced, dotted = ended), and every badge
    says it in words.
  * Graph controls: zoom, pan and reset, a legend, and filters by evidence status and relationship type (kept in the
    URL). "Direct links" or "two steps". Any neighbour can be expanded progressively.
  * Nodes are keyboard-operable buttons. A List view shows the same data as a table, or as sentences on phones
    (phones default to the list).
  * The inspector shows each entity and each link with its sources (excerpt, publisher, retrieval date, link).
  * "Connections between…" shows shared direct connections and the shortest chain. It uses referenced links only
    unless you tick "include unreferenced".
  * Saved investigations (private) show a "data changed since you saved" notice.
  * The graph is capped at 40 nodes; when it cuts nodes it says how many, and keeps better-sourced links first.
* API (all members only, 401 for visitors, rate-limited per member):
  * `/api/world-graph/resolve`, `/entities` (confirm), `/entities/{id}/graph`, `/edges/{id}`, `/shared`, `/path`.
  * `/api/saved-research` CRUD: owner-only, and another member's item returns 404. The same store will hold
    Time Machine scenarios and Devil's Advocate theses.
* Visitors see what the World Graph is and a "Create a free account" button that opens the join sheet. A "World
  Graph" link was added to the Research navigation.
* Bugs found and fixed while testing:
  * **Shared-alias poisoning.** One member's search text was stored as a global alias, so it could make a name
    resolve to the wrong company for everyone. Only source names (label, listing ticker, Nexis symbol) are aliases now.
  * **A race condition.** Two members opening the same company at once got "a database error". Inserts now use
    savepoints and adopt the other request's row.
  * **Expanding was blocked.** "Show its connections" was hidden whenever anyone had ever loaded that company.
  * **Shared/path found nothing.** Results for a company whose relationships weren't loaded yet came back empty;
    both sides now load first.
  * **Filter checkboxes flicked back** for a moment in WebKit.
  * **Unreadable phone list.** The list view needed sideways scrolling on phones.
  * **Pre-existing, outside the World Graph:** the header avatar letter was 11.8px on phones (only visible when
    signed in), and there were two lint errors in the instrument search box.
* Tests run against recorded Wikidata answers. `NEXIS_WIKIDATA_FIXTURES_DIR` is set only in the Playwright config,
  never in production. Recorded: Emaar Properties, Emaar Development, Investment Corporation of Dubai, Emirates NBD.

**Results**
* Backend: 345/345, ruff clean on touched files.
* Playwright, full run: 323 passed. The only 3 failures were World Graph tests in Firefox and WebKit; they were bugs
  2 and 4 in the list above, now fixed, and the World Graph spec then passed 18/18 across all three browsers.
* Responsive and axe checks for the visitor page are in the shared suites. The member graph view at 375 and 1440
  (light and dark) is checked inside the World Graph spec.
* Unit tests 6/6; build and typecheck pass; lint on touched files 0 errors.
* Screenshots at 375 and 1440 reviewed (label collisions and clipping fixed after review).

**Not deployed.** Vercel's Preview environment uses the same Postgres database as Production, so any deploy of this
branch would run migrations 0017 and 0018 on the live database. That needs your go-ahead.

## Deployed: Phases 1–3 (PR #16, merged 2026-10-10)

Production was checked after the merge (both Vercel projects were Ready):
* As a visitor, the World Graph API and the AI chat return 401.
* A temporary member account searched "Emaar" against live Wikidata and opened its graph: 17 entities, 1 referenced
  link, 15 unreferenced, linked to `EMAAR.AE`. This confirms migrations 0017 and 0018 ran.
* It saved and deleted an investigation, and the account was then deleted.

## Phase 4 — Time Machine

**What changed**
* `/time-machine` (members only; visitors see what it is and the join sheet). Holdings come from "My investments"
  (USD, latest delayed prices) or a list the member enters.
* The member sets moves for oil, the S&P 500, the DFM index, gold, EUR/USD and the US 10-year yield (with presets),
  their own move for any single holding, and the history window (1, 2 or 3 years).
* Four deterministic methods, each named on every result line:
  1. **Your move:** applied as given.
  2. **Measured sensitivity:** OLS of weekly (Friday) returns on the shocked factors, giving β ± 95%, R², the number
     of weeks and the dates, and an impact range from the betas' covariance.
  3. **Bond duration:** modified duration and convexity, for bonds with known coupon, maturity and yield.
  4. **DCF check:** the existing valuation model re-run with growth, discount-rate and long-run-growth changes.
* Flags:
  * The move is larger than any 3-month move in the data, so the result extrapolates.
  * The 95% range includes zero: the data can't tell the direction.
  * Weak relationship (R² below 0.2).
  * Not enough history (fewer than 26 weeks): no number is given.
  * A factor has no data: the move isn't applied, and the page says so.
* No probabilities, Monte Carlo or AI. The same inputs and the same data give the same output.
* Saved scenarios are research items. Each run is immutable (inputs, data snapshot, model version, output). "Run again
  on today's data" adds a run; runs can be compared (first against latest) and a scenario duplicated and edited.
  Private: another member gets 404.
* API: `/api/time-machine/{factors,run,scenarios,scenarios/{id},scenarios/{id}/rerun,runs/{id},compare}`. All are
  401 for visitors, and runs are rate-limited (40 per hour).
* Checked on live data (locally):
  * Oil −30% with the S&P 500 −10%: XOM −12.4% (range −16.5 to −8.2) and AAPL −13.4%.
  * Emaar +7.4%, but its range (−0.3 to +15.0) includes zero and R² is 0.14. The page says the direction is unclear
    and the relationship is weak.
  * Rates +100bp: TLT −13.5% (R² 0.89); the UAE 2034 bond −6.0% (duration 6.2).
  * Two rule changes came from this check. Moves are now compared with 3-month moves, because the old single-week
    comparison flagged every preset. Ranges that cross zero are now called out.
* No database migration: scenarios and runs use the Phase 2 tables.
* Also fixed: the link checker's deduplication. It compared route patterns with concrete URLs, so it never
  deduplicated and opened every story. The fix keeps it inside its 10-minute limit.

**Results**
* Backend: 364/364 (19 new Time Machine tests). These use synthetic series with known sensitivities and check that
  the regression recovers them; bond duration and convexity are checked against full repricing. Ruff clean.
* Playwright, full run: 350 passed. The only failure was the link checker's time limit; with the fix it passes on
  its own (8.1 min, no broken links).
* The Time Machine spec passes 12/12 in Chromium, Firefox and WebKit. It includes layout and axe checks at 375 and
  1440 in light and dark.
* Unit tests 6/6; build and typecheck pass; lint on touched files 0 errors.
* Screenshots at 375 and 1440 reviewed. Fixed after review: full-width selects, a stretched history-window control,
  and the remove button wrapping on phones.

## Phase 5 — Devil's Advocate

**What changed**
* `/devils-advocate` (members only). A member saves a thesis (company, text of 30–3,000 characters, optional
  horizon); "Challenge this thesis" runs one analysis:
  1. **Evidence** (code) is stored as bounded evidence records labelled E1…En:
     * market data: price and range, multiples, profitability and growth, dividend, analyst consensus — each a
       separate item, dated;
     * recent headlines (headline text only; articles aren't read) and Nexis source events;
     * the company's World Graph links.
  2. **One AI call** splits the thesis into claims (first run only; they are then fixed, so their status can be
     tracked) and argues against it. Every argument must cite `{evidence, quote}`.
  3. **The validator** (code):
     * drops a citation whose quote isn't verbatim in that item (case, whitespace and quote style are ignored;
       minimum 8 characters);
     * drops counter-arguments and risks left without a valid citation;
     * computes each claim's status from the surviving citations (supported, contradicted, mixed or untested);
     * flags figures that appear in neither the evidence nor the thesis (years aren't counted).
  4. If the AI is unavailable, the answer can't be parsed, or there's too little evidence, the run is stored as
     **failed** and nothing is shown as a result.
* The page shows sections A–H:
  * A: claims
  * B and C: evidence for and against, as quotes linked to their sources
  * D: the strongest case against
  * E: assumption check, with the change since the last challenge
  * F: risks
  * G: what would change the view
  * H: gaps, the validator's counts, the AI model, and the full evidence list
* Runs are immutable and kept, failed ones included. Rewriting the thesis resets its claims. Each thesis has a status
  (active, under review, archived). 10 challenges per member per day.
* API: `/api/devils-advocate/theses` (create), `/theses/{id}` (get, patch), `/theses/{id}/analyse`, `/runs/{id}`.
  Visitors get 401; another member gets 404.
* No AI key is configured locally or in Vercel env vars. Production reaches the AI through the Vercel AI Gateway
  using the deployment's OIDC token, so the real end-to-end check happens on production after the merge.

## Phase 6 — integration

* `/my-research` hub: the three tools, plus everything the member saved, grouped by tool. It is first in the Research
  navigation.
* The company travels between tools:
  * World Graph → Stress-test / Challenge a thesis;
  * each Time Machine result line → Connections / Challenge;
  * Devil's Advocate → Connections / Stress-test;
  * every instrument page has a "Research tools" card.
* **PDF reports of saved runs** (Time Machine scenarios and completed challenges). Each report prints the stored run
  exactly, without recomputing or calling the AI again. Reports are private, through the existing owner-only download.
  The standard PDF fonts cover Windows-1252 only, so other characters (e.g. Arabic) print as "?" (a known limitation).
* Accessibility fixes found by the new specs:
  * text-style buttons use the higher-contrast link colour (dark mode was below 4.5:1);
  * evidence links are underlined;
  * the Edit link is a 44px target;
  * the assumption table stacks into cards on phones.

**Results (Phases 5 + 6)**
* Backend: 378/378. New tests cover Devil's Advocate (12: verbatim-quote validation, dropped arguments, computed
  statuses, figure flags, fixed claims, AI-down / unparseable / no-evidence failures, privacy, daily limit) and
  reports (2).
* Playwright, full run on the Phase 5 code: 376 passed.
* After Phase 6: the tool and integration specs passed 77/78 in Chromium, Firefox and WebKit. The one failure was the
  existing "AI assistant answers" test in Chromium, which didn't reproduce in 12 repeats across three browsers
  (intermittent under heavy parallel load; noted, not hidden). Responsive and axe checks for the new and changed
  pages pass 65/65.
* Unit tests 6/6; build and typecheck pass; lint on touched files 0 errors.
* Screenshots reviewed at 375 and 1440.

## Overhaul Step 7 — Pulse: contradiction checks; the wrong FAB and "easing" stories

* **The problem, from Nexis's own data:** the US 10-year yield rose from 4.05% to 5.24% over the past year, and the
  13-week bill from 3.53% to 4.06% since December. Two Nexis editorial stories nevertheless presented falling rates
  as the current situation:
  * "Falling rates are a headwind for FAB's net interest margin" (cites no sources);
  * "Interest rates: how much easing is the market expecting?"
* **New `services/pulse_checks.py`, run on every Pulse engine tick:**
  * It reads the direction of US rates from Nexis market data: both the 10-year and the 13-week bill moving ±20bp or
    more over 26 weeks, cached for 6 hours. If the data is missing, nothing is cached and nothing is held.
  * It holds for review (**not deleted**) any visible editorial story that presents the opposite direction as current.
  * Each hold writes a moderation-log entry with the numbers. One "approve" from a moderator restores a story.
  * Members' posts are never checked.
* **Run over the 20+ live editorial stories (offline copy):** it flags exactly those two. Stories that discuss both
  directions conditionally (Emirates NBD margins, NASDAQ) and the other FAB story are left alone.
* **Takes effect on production on the first Pulse visit after deploy** (the engine tick runs then).
* **Outdated copy fixed:** Pulse no longer says it collects from Reddit (collection has been off since #15). Reddit
  posts appear only when a member shares one, through Reddit's embed. A pre-existing lint error in the Pulse search
  box was also fixed.

## Overhaul Step 8 — device profiles

* New Playwright projects `iphone` (iPhone 14, Safari engine), `pixel` (Pixel 7, Chrome) and `ipad` (iPad 7th gen,
  Safari engine). Each uses touch, a mobile user agent and device pixel ratio.
* `tests/devices/devices.spec.ts` checks every main page's layout on the device's own viewport, plus three touch
  flows: navigation drawer → Time Machine; a scenario run; World Graph → evidence.
* Result: 72/72.

## Overhaul Step 4 — Finstagram is a news feed

* **The server enforces it.** Only news pages (`kind = page`) post, plus admins for corrections. A member gets 403
  with a plain explanation.
* **Feeds are news-only.** "For you", following, trending, and symbol and hashtag pages show only posts by news pages
  and admins (the same rule as posting).
* **Members' earlier posts are kept, not deleted.** They stay on the member's profile and at their own address.
  Members can still like, comment, save and follow.
* **UI.** Members see "Finstagram is a news feed…" instead of a composer. Visitors get the join sheet. The instrument
  page's "Post" button and "Be the first" link are gone.
* **Tests.** The three older backend tests that posted as members now post with the admin role. A news-page account
  couldn't be used for them because those accounts can't sign in. A new test covers news-only feeds, kept member
  posts and the 403. A new e2e test checks the member view.
* **Results.**
  * Backend: 383/383.
  * e2e (core, access, research): 57/57 in Chromium, Firefox and WebKit, plus the new check in Chromium and WebKit.
  * Responsive and axe on Finstagram, post, instrument and Pulse pages: 65/65.
