# Nexis intelligence platform — implementation plan (Phase 0)

Branch `nexis-overhaul` (based on PR #15). Step 1 of the overhaul (touch/contrast/layout) is done and committed.
This plan merges the "Master product transformation" brief (World Graph, Time Machine, Devil's Advocate, account-gated
experience) with the open steps of the earlier overhaul. Where they overlap, they're one phase (e.g. accounts).

## 1. What exists today (audited)

| Area | What's there | Reuse |
|---|---|---|
| Stack | React 19 SPA (Vite) + FastAPI + SQLAlchemy/Alembic (Neon Postgres in production, SQLite in tests), Vercel serverless | as is |
| Auth | Email/phone + password, Google/Reddit/X OAuth, sessions (hashed tokens, HTTP-only cookies), CSRF origin check, `auth.require_user` / `optional_user`, roles (member/moderator/admin), legal acceptance gate. **No email delivery** (`email_provider` unset), so no email verification or password reset yet. | extend |
| Plans | `plans.py` + `entitlements.py` (Free/Plus/Pro, monthly AI allowance, capabilities, server-checked) | extend (new capabilities/limits) |
| Rate limits | `ratelimit.hit` buckets (DB-backed) | reuse |
| Evidence-like data | `source_events` (news/filings/price moves: title, url, publisher, published_at, excerpt facts) linked to assets (`source_event_assets`); Pulse cites them | reuse as one evidence source |
| Market data | `markets.py` (quotes, history, fundamentals, analyst consensus) via **unofficial** Yahoo/TradingView endpoints (AUDIT.md §2, flagged) | reuse behind an adapter, labelled delayed/unofficial |
| Macro | US Treasury, World Bank, FRED (needs key) connectors in `connectivity/providers/economic.py`, SEC EDGAR in `sec.py` | reuse |
| Valuation | DCF + comparables + sensitivity (`valuation.py`) | Time Machine valuation scenarios |
| Stress tests | Portfolio stress presets with linear beta (`stress.py`, research workspace datasets) | method reference |
| Graph | `/graph` = workspace graph of the user's research datasets (Plotly) — not a company relationship graph | keep; World Graph is separate |
| AI | `llm.py` (Vercel AI Gateway in production), tool-using advisor, help agent | reuse with structured outputs |
| Reports | PDF reports (compare, valuation, research workspace jobs) | extend to new tools |

## 2. Free, official data for relationships (checked on the providers' sites, Oct 2026)

| Source | Gives | Terms / limits |
|---|---|---|
| **Wikidata** (wbsearchentities API + SPARQL at query.wikidata.org) | entity resolution (name ↔ QID ↔ ticker/exchange/ISIN where recorded), parent organisation (P749), subsidiary (P355), owned by (P127), owner of (P1830), industry (P452), country (P17), stock exchange+ticker (P414/P249), founded/dissolved dates, and per-statement references | Data **CC0** (public domain, commercial use OK, no attribution required). Query service: 60 s processing per 60 s per client, 5 parallel queries per IP, 60 s timeout, descriptive User-Agent required, back off on 429. |
| **SEC EDGAR** (data.sec.gov) | US issuers: CIK, filings index, XBRL facts; Exhibit 21 subsidiary lists (later phase) | Free; max 10 requests/s; declared User-Agent with contact required. |
| Existing news store (`source_events`) | dated, linked headlines/excerpts per asset | as collected (publisher RSS; see AUDIT.md) |
| Existing market data | prices/returns for regressions and baselines | unofficial — labelled; swap when a licensed source exists |

**Not available for free from a structured, citable source:** supplier/customer relationships, commodity exposure
percentages, revenue by customer. These will **not** be shown as facts. "Exposed to commodity" and "depends on economic
factor" will only appear as **measured historical sensitivity** (regression of the company's returns on the factor,
with R² and sample size) and labelled as such — never as a reported business relationship.

## 3. Data model (one migration, `0017_intelligence`)

* `fin_entities` — company/asset/sector/industry/commodity/country/indicator; `wikidata_qid` (unique), `symbol`, `cik`, country.
* `fin_entity_aliases` — normalised names/tickers/ISINs → entity (for resolution and ambiguity detection).
* `evidence_records` — source kind (wikidata, sec, news, market_data, user), url/identifier, title, publisher,
  published_at, retrieved_at, short excerpt/claim (bounded), content hash; optional link to `source_events`.
* `fin_relationships` — source, target, type, direction, status (verified/historical/inferred/uncertain),
  valid_from/to, first_seen, last_verified; unique (source, target, type); `relationship_evidence` join table.
* `research_items` — one table for everything a user saves (graph investigation, scenario, thesis, report), owner
  `user_id`, kind, title, entity, state JSON, notes, status, timestamps; room for a future `org_id`.
* `research_runs` — immutable executions (scenario run, thesis analysis, graph snapshot): item, model version,
  inputs, input snapshot (data + timestamps + evidence ids), outputs, created_at. Past runs are never rewritten.

## 4. Phases and acceptance criteria

**Phase 1 — accounts and access (= overhaul Step 2).** Visitors read Pulse, markets and Finstagram; every AI endpoint
(advisor, help answers, new tools), Pulse/Finstagram participation, saved research and report generation require a
session server-side (401), with a contextual sign-in sheet that returns the visitor to the same URL. Visitor AI
allowance → 0. Login/sign-up rate limits; email verification + password reset with a provider interface (SMTP via env
vars) — they work once a sender is configured and say so honestly until then. Tests: logged-out 401 matrix for every
restricted route, UI gate + return URL, User A/B isolation.

**Phase 2 — shared foundation.** Migration 0017; entity resolution (local aliases first, Wikidata search second,
ambiguity returned as choices, never auto-picked); evidence service; Wikidata/EDGAR adapters with rate limiting,
backoff, caching and status reporting; shared validation/error utilities. Tests with recorded fixtures (no network).

**Phase 3 — World Graph.** `/world-graph`: search → central entity → typed, evidence-backed edges (bounded depth 2,
≤ 40 nodes, progressive expansion, dedup); SVG graph with zoom/pan/reset, legend, filters (type, evidence status),
keyboard navigation, list/table alternative; evidence inspector; "shared connections" and "path between entities"
queries over stored verified edges only; saved investigations with "data changed since saved" indicator.

**Phase 4 — Time Machine.** `/time-machine`: deterministic models only — (a) explicit price/sector shocks on holdings,
(b) factor sensitivity (OLS beta of an asset on oil/index/FX/rates with R², standard error, sample window; impact =
beta × user shock, shown with the regression's uncertainty, explicitly not a forecast), (c) valuation sensitivity via
the existing DCF (growth/margin/WACC overrides), (d) bond rate shocks via duration/convexity where yield/coupon/maturity
are known. No Monte Carlo (no defensible probability model with the available data). Saved scenarios, immutable runs,
compare two scenarios, duplicate/rerun.

**Phase 5 — Devil's Advocate.** `/devils-advocate`: thesis CRUD; pipeline: validate → retrieve evidence (news store,
market data, valuation, Wikidata relationships, EDGAR for US issuers) → LLM extracts claims (structured JSON) →
LLM classifies evidence for/against **by evidence id only, with a verbatim quote that must appear in the stored
excerpt** → validator drops any citation that fails, numbers not present in evidence/inputs are flagged → sections
A–H. AI unavailable → "analysis could not be completed" (nothing shown as a result). Versioned analyses, comparison
with the previous run, assumption status tracking.

**Phase 6 — integration.** One "Research" navigation group (Overview, World Graph, Time Machine, Devil's Advocate,
Saved research, Reports) + cross-links that carry entity/assumption context; onboarding; responsive/axe on every new
page; reports from saved runs (PDF, owner-only download).

**Phase 7 — verification and handoff.** Full suites (unit, backend, Playwright ×3 browsers, responsive, axe, links,
Lighthouse), security review of new routes, docs, final report.

The remaining overhaul steps (data providers with official free sources, Finstagram as a news feed, Stripe yearly
plans, personalised advisor with onboarding, Pulse from own data, every-device pass, SEO/SSR) continue after these
phases, or interleave where they share work (e.g. provider interfaces in Phase 2 = overhaul Step 3 groundwork).

## 5. Decisions and external setup needed (none block Phase 1–3)

1. **Email sender** for verification/reset (SMTP of your choice, or approval of a free-tier service).
2. **FRED API key** (free) if macro scenarios should include FRED series; World Bank/Treasury work without keys.
3. **SEC User-Agent contact email** (EDGAR requires a real contact) — defaults to the configured support email.
4. Yearly AED prices for Plus/Pro (overhaul Step 5).
5. Licensing of the price source (AUDIT.md §2) remains a business decision.
