# Nexis Finance: overhaul and intelligence platform, final report

Branch `nexis-overhaul`.
* **Deployed:** PR #16 (merged 2026-10-10): test tooling, Step 1, Phase 1 accounts, Phase 2 data foundation, World
  Graph.
* **Open:** PR #17: everything since. It needs the owner's merge.
* **Details:** step-by-step logs and per-step test results are in [PROGRESS.md](PROGRESS.md).

## 1. What was implemented

**Research tools (master brief)**

* **World Graph** (`/world-graph`): evidence-backed company relationships from Wikidata.
  * Ownership, subsidiaries, listings, industry and country.
  * Every link carries its evidence and an honest status: referenced / unreferenced / ended.
  * Explicit disambiguation (it never guesses), expansion, filters, a list view, the evidence inspector, shared
    connections and paths between companies, and saved investigations with "data changed since saved".
* **Time Machine** (`/time-machine`): deterministic what-if scenarios.
  * Methods: your own move; measured factor sensitivity (weekly OLS with β ± 95%, R² and the dates); bond
    duration/convexity; a DCF check.
  * Flags extrapolation, unclear direction, weak fit, missing data and too little history.
  * Immutable saved runs, re-run and compare.
* **Devil's Advocate** (`/devils-advocate`): the AI argues against a thesis from stored evidence only.
  * Verbatim-quote citations are checked by code, and failing ones are dropped.
  * Claim statuses are computed by code; unverified figures are flagged.
  * When the AI fails, nothing is concluded.
  * Claims are tracked across runs.
* **Integration:** the `/my-research` hub; links between the tools that carry the company; a "Research tools" card on
  instrument pages; private PDF reports of saved runs.

**Overhaul steps (owner decisions)**

* **Accounts and access:**
  * Visitors read; AI, participation, reports and research need a free account, enforced server-side (401).
  * A join sheet that keeps what the visitor typed.
  * Private reports; password reset and email verification (SMTP-ready); login lockout.
* **Finstagram is a news feed:** only news pages post; members like, comment and follow; members' earlier posts are
  kept on their profiles.
* **Pulse:**
  * Contradiction checks against Nexis's own rates data. They hold the wrong FAB and "easing" stories for review on
  the next tick after deploy.
  * The outdated Reddit sourcing copy is fixed.
* **AI wealth advisor:**
  * An investor profile scored by code (with caps).
  * Profile- and holdings-aware *educational* guidance: no buy/sell calls or prescribed amounts.
  * An advice log with an automatic review; a member's advice history; 27 evaluation cases.
* **Stripe:** yearly AED plans supported by configuration, with no amounts invented; a monthly/yearly switch when
  available; `STRIPE-SETUP.md`.
* **Data layer:** every external source behind one provider registry; `/api/system/sources`; `DATA-SOURCES.md` with
  terms and limits checked.
* **Every device:** 44px tap targets, 14px phone text, contrast tokens; iPhone, Pixel and iPad profiles in the test
  suite.
* **SEO/SSR:** a proposal only, as the brief asked ([docs/SSR-PROPOSAL.md](docs/SSR-PROPOSAL.md)).

## 2. Phases: complete or not

| Phase / step | State |
|---|---|
| Phase 0 audit and plan; Step 0 baseline; Step 1 blocking bugs | Complete |
| Phase 1 accounts and access (Step 2) | Complete, deployed |
| Phase 2 data and evidence foundation | Complete, deployed |
| Phase 3 World Graph | Complete, deployed |
| Phase 4 Time Machine; Phase 5 Devil's Advocate; Phase 6 integration | Complete, in PR #17 |
| Step 3 data layer; Step 4 Finstagram; Step 6 advisor; Step 7 Pulse; Step 8 devices | Complete, in PR #17 |
| Step 5 Stripe | Complete except the yearly **amounts** (owner decision) and **live mode** (owner only) |
| Step 9 SSR | Proposal written; **not built** (it waits for the owner's choice, as instructed) |
| Phase 7 verification | Done locally. On production: World Graph verified; Devil's Advocate with the real AI still to verify after the PR #17 merge (preview is behind Vercel login) |

## 3. Files (main ones)

* **Backend services:**
  * `services/knowledge.py`, `world_graph.py`, `saved_research.py`;
  * `time_machine.py`, `devils_advocate.py`, `research_reports.py`;
  * `pulse_checks.py`, `investor_profile.py`, `advice_log.py`;
  * `account.py`, `email.py`.
* **Backend models, sources and routes:**
  * models: `models/knowledge.py`, `models/advisor.py`;
  * sources: `connectivity/providers/wikidata.py`, `markets/sources.py`;
  * routes: `api/routes/world_graph.py`, `time_machine.py`, `devils_advocate.py`.
* **Frontend pages:** `WorldGraph.tsx`, `TimeMachine.tsx`, `DevilsAdvocate.tsx`, `MyResearch.tsx`,
  `AdviceHistory.tsx`, `AccountEmail.tsx`.
* **Frontend components and styles:** `account.tsx`, `research.tsx`, `investor.tsx`; `styles-graph.css`,
  `styles-tm.css`, `styles-da.css`.
* **Tests:** `backend/tests/test_{access,knowledge,world_graph,time_machine,devils_advocate,research_reports,pulse_checks,advisor_profile,sources}.py`;
  `frontend/tests/e2e/{access,world-graph,time-machine,devils-advocate,research,pricing,advisor-profile}.spec.ts`;
  `frontend/tests/devices/devices.spec.ts`; `tests/ai-cases.md`.
* **Docs:** `AUDIT.md`, `PROGRESS.md`, `DATA-SOURCES.md`, `STRIPE-SETUP.md`, `docs/INTELLIGENCE-PLAN.md`,
  `docs/SSR-PROPOSAL.md`, `docs/BILLING.md` (updated), this file.

## 4. Migrations

| Migration | Contents | Reversible | Applied to production? |
|---|---|---|---|
| 0017 | `users.email_verified_at`, `reports.owner_id` | Yes (tested up/down/up) | **Yes**: verified 2026-10-10 |
| 0018 | 7 tables: entities, aliases, evidence, relationships, relationship evidence, research items and runs | Yes (tested) | **Yes**: verified 2026-10-10 |
| 0019 | `investor_profiles`, `advice_log` | Yes (tested; `alembic check`: no drift) | **No.** It applies on the first request after PR #17 is merged. Vercel Preview shares the production database, so opening PR #17's preview would apply it too. It is additive and safe for the running code |

## 5. Providers

* **Official:** Wikidata, SEC EDGAR, US Treasury, World Bank, FRED (needs a key), Hacker News (Algolia), YouTube
  embeds, Reddit embeds (member-shared only), Stripe (test mode), the Vercel AI Gateway.
* **Unofficial and unchanged** (now behind `markets/sources.py`): Yahoo Finance and TradingView (high risk), Google
  News RSS, publisher RSS and StockTwits (medium).
* Replacing the high-risk two is a **licensing decision for the owner**. Details: [DATA-SOURCES.md](DATA-SOURCES.md).

## 6. Environment variables (names only)

* **Email:** `NEXIS_SMTP_HOST`, `NEXIS_SMTP_PORT`, `NEXIS_SMTP_USERNAME`, `NEXIS_SMTP_PASSWORD`, `NEXIS_SMTP_FROM`
* **Identification:** `NEXIS_WIKIDATA_USER_AGENT`, `NEXIS_SEC_USER_AGENT`
* **Data:** `NEXIS_FRED_API_KEY`, `NEXIS_PRICE_SOURCE`, `NEXIS_UAE_SOURCE`, `NEXIS_PULSE_PUBLIC_STOCKTWITS`
* **Stripe:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PLUS_MONTHLY_AED_PRICE_ID`,
  `STRIPE_PRO_MONTHLY_AED_PRICE_ID`, `STRIPE_PLUS_YEARLY_AED_PRICE_ID`, `STRIPE_PRO_YEARLY_AED_PRICE_ID`,
  `NEXIS_BILLING_TEST_EMAILS`
* **Tests only (never in production):** `NEXIS_WIKIDATA_FIXTURES_DIR`

## 7. Credentials and decisions needed from the owner

1. An **SMTP sender**. Until one is set, password reset and verification say "not available yet".
2. A **contact** (URL or email address) for the Wikidata and SEC User-Agents.
3. **Yearly AED amounts**, if yearly plans are wanted (see STRIPE-SETUP.md).
4. **Stripe live**: account activation and a live key, set by the owner. Nexis never charges on the owner's behalf.
5. A **FRED key** (optional).
6. A **licensed price feed** to replace Yahoo and TradingView (business decision).
7. **SSR**: approve option A, or choose another.
8. **Confirm the advisor's educational framing** (it no longer gives buy/sell calls) and have counsel review the
   updated Terms and Privacy text. Then decide whether to **bump the legal version**, which makes members re-accept.

## 8. Tests: actual results

| Suite | Result |
|---|---|
| Backend (pytest) | **398 / 398** passing |
| Playwright full run (Chromium, Firefox, WebKit e2e; responsive at 9 sizes; axe in light and dark; iPhone, Pixel, iPad; links) | **479 passed, 0 failed.** The link checker was run separately after a session restart interrupted it |
| After the last frontend change (Step 6) | Profile/history e2e 3/3; core and access 54/54 in three browsers |
| Unit tests (Vitest) | 6 / 6 |
| Build and typecheck | Pass |
| Lint, files touched | 0 errors |
| Lint, project-wide | Frontend: 27 errors left in untouched files (baseline 40). Backend: 1 import-order issue in `alembic/env.py` (pre-existing) |
| Intermittent | One run of the existing "AI assistant answers" test failed in Chromium under heavy parallel load; 12 repeats across three browsers passed |
| Lighthouse | Run at baseline (production 70/28/88 performance). **Not re-run after these changes**: do it after the PR #17 merge |

## 9. Security checks

* **Access:** every new route requires sign-in (401 tests for each). Another member's items, runs, reports and
  advice log return 404 (tested). Request bodies reject unknown fields. Risk bands and plan prices are computed on the
  server, never accepted from the client.
* **Data sharing:** shared aliases come only from source names, so one member can't redirect another's search. This
  was a bug found and fixed during testing.
* **Concurrency:** concurrent inserts are race-safe (savepoints).
* **Injection and links:** Wikidata queries accept only validated IDs. Only `http(s)` evidence links are stored, and
  the Markdown renderer injects no HTML and only links `http(s)`. PDF text is escaped. There are no user-supplied
  outbound hosts (no SSRF).
* **Rate limits:** Wikidata lookups, scenario runs (40/h), Devil's Advocate (10/day), reports (20/h) and profile
  saves.
* **Secrets:** none in code or git. The Stripe test key was used only as an environment variable and never stored.
  Live mode was never used.

## 10. Known limitations

* **Wikidata coverage:** most statements are unreferenced (Emaar: 1 of 16), and the page says so.
* **Not available from free sources:** supplier and customer relationships.
* **Time Machine:** factor sensitivity is historical co-movement, applied linearly. It says so and flags
  extrapolation. Results depend on the delayed, unofficial price source.
* **Devil's Advocate:** cites headlines, not articles. Not yet verified with the real AI.
* **Pulse checks:** only the direction of rates is checked so far. Other contradiction types are future work.
* **PDF reports:** characters outside Windows-1252 (e.g. Arabic) print as "?".
* **Not built:** SSR/SEO beyond Pulse discussions (proposal only). Yearly plans have no amounts. Email isn't sent
  until SMTP is configured.

## 11. Manual steps for the owner

1. Merge **PR #17**. Then:
   * open `/pulse` once, which runs the tick that holds the two wrong stories;
   * try a Devil's Advocate challenge;
   * run the cases in `tests/ai-cases.md`.
2. Set the SMTP and User-Agent contact variables in Vercel (`nexis-finance-api`).
3. Decide items 3–8 in section 7.
4. Run Lighthouse on production and compare with `tests/BASELINE.md`.

## 12. Readiness

* **Production-ready (tested, and deployed or deployable):** accounts and access, World Graph, Time Machine,
  Finstagram as a news feed, the Pulse checks, the device and accessibility work, and billing in test mode.
* **Ready once verified on production:** Devil's Advocate and the personalised advisor. Their logic is fully tested,
  but the real-AI behaviour can only be checked with the production AI connection.
* **Not ready for paying customers yet:** Stripe live mode (owner activation), and the licensing of the main price
  source (Yahoo/TradingView). The latter is the main business risk, recorded since the audit.
