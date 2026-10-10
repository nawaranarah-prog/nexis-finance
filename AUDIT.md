# Nexis Finance — audit (Step 0)

Branch `nexis-overhaul`, based on `release-tiers-help-agent` (PR #15: Free/Plus/Pro billing, Help Agent, Reddit
sharing — not yet merged or deployed). "Production" below means the live site, which still runs `main`.
Nothing in the product was changed for this audit.

## 1. Framework, routing and rendering

| Part | What it is |
|---|---|
| Website | React 19 + Vite 8 **client-only SPA** (`frontend/`), React Router 7, TanStack Query. Vercel project `nexis-finance`. |
| API | FastAPI (Python 3.12 on Vercel serverless, `api/index.py` → `backend/app`), SQLAlchemy 2 + Alembic, Neon Postgres. Vercel project `nexis-finance-api`. The website proxies `/api/*` to it. |
| Rendering | Every route returns the same `index.html`. **Verified on production:** `/markets/EMAAR.AE`, `/valuation/EMAAR.AE`, `/disclaimer`, `/pulse` all return the homepage `<title>` ("…AI financial advisor and Finstagram") and `canonical=/`, and crawlers without JS see "Nexis Finance needs JavaScript…". Titles are set client-side after load (`AppLayout.tsx` `useEffect`). |
| Existing server rendering | **Pulse discussion pages only**: `/pulse/d/*` is rewritten to `/api/seo/render/pulse/d/*` (`backend/app/api/routes/seo.py`), which returns real HTML with its own title/description/canonical. Also `/sitemap.xml`. |

## 2. External data sources

| Source | Used for | Called from | Free / paid / scraped | Terms risk |
|---|---|---|---|---|
| Yahoo Finance `query1/query2.finance.yahoo.com` (+ `fc.yahoo.com` cookie, "crumb") | Quotes, history, fundamentals, analyst consensus, news for most symbols incl. DFM `.AE` | `backend/app/markets/yahoo.py`, `backend/app/data/providers.py` | Free, **unofficial** endpoints, browser User-Agent spoofed | **HIGH.** Yahoo offers no public API licence; its terms prohibit automated access and commercial reuse. This is the main price source of a paid product. |
| TradingView `scanner.tradingview.com`, `symbol-search.tradingview.com`, `data.tradingview.com` websocket | ADX `.AD` shares, UAE bonds/sukuk (`.BOND`), DFM fallback, UAE indices | `backend/app/markets/tradingview.py` | Free, **internal endpoints behind TradingView's own screener and charts**, browser UA spoofed | **HIGH.** TradingView's terms forbid using its data/endpoints outside its widgets without a licence. |
| Publisher RSS (Khaleej Times, Gulf News, The National, Arabian Business, MarketWatch/Dow Jones, CNBC, Investing.com, Cointelegraph, Yahoo Finance) | Finstagram "news pages", Pulse evidence | `backend/app/services/newsfeed.py`, `backend/app/markets/feeds.py` | Free RSS | **MEDIUM.** RSS is published for reading, but republishing headline + excerpt + **hotlinked lead image** in a commercial feed may exceed what each publisher allows (Investing.com's terms prohibit it explicitly). Images are linked, not stored. |
| Google News RSS search | Regional news per company | `backend/app/markets/news.py`, `newsfeed.py` | Free RSS | **MEDIUM.** Google's terms limit automated/commercial use of News feeds. Only headline/source/link kept. |
| YouTube channel feeds + embedded player | Market videos in Finstagram | `newsfeed.py` | Free, official embeds | Low (official embed). |
| Hacker News (Algolia API) | Public discussions in Pulse | `backend/app/services/public_discussions.py` | Free, public API | Low. |
| StockTwits public symbol streams | Public discussions in Pulse | `public_discussions.py` | Free, unauthenticated | **MEDIUM.** StockTwits' terms restrict commercial use of its API content; not reviewed in detail. |
| Reddit | Pulse — automated collection **off** (PR #15); member-shared links shown via Reddit's official embed | `public_discussions.py`, `reddit_links.py` | — | Resolved in PR #15 (collection needs Reddit's written permission). |
| SEC EDGAR, US Treasury, World Bank, FRED | Filings and macro series (research workspace) | `backend/app/connectivity/providers/{sec,economic}.py` | Free, official public APIs | Low. |
| AI: Vercel AI Gateway (OIDC in production); optional keys for Anthropic, OpenAI, Gemini, Groq, OpenRouter, GitHub Models | AI Advisor, Help Agent, Pulse narratives, report text | `backend/app/services/llm.py` | Free tier/usage-based (gateway) | See §3. |
| Stripe (test mode) | Billing | `backend/app/services/billing_stripe.py` | Fee per transaction | n/a |

No new scraping will be added. The two HIGH items are a product-level licensing decision (§9).

## 3. The AI assistant

* **Provider/model:** `llm.py`, first configured target wins. Production uses the **Vercel AI Gateway** (Vercel OIDC token) with `llm_model = "openai/gpt-5.1,openai/gpt-5,openai/gpt-4.1"` (`backend/app/core/config.py`).
* **Cost per request:** not visible in code or config. Each question runs a tool loop (`advisor.ask`, up to `max_tokens=3500` per model call, several tool calls). Gateway pricing is per token; actual spend is only visible in the Vercel dashboard.
* **Limits today:** visitors 3/day per IP; Free 20, Plus 200, Pro 600 per month (`backend/app/core/plans.py`); burst limit 20/hour per IP (`advisor_requests_per_hour`).
* **Tools** (`backend/app/services/advisor.py` `TOOLS`): `search_instruments`, `get_instrument`, `get_news`, `get_price_stats`, `compare_instruments`, `run_valuation`, `position_calculator`.
* **System prompt — regulatory problem.** `advisor.py` `SYSTEM` currently says, verbatim:
  * "You are Nexis Advisor — the AI **financial advisor** inside the Nexis Finance app … like a … senior **wealth adviser**"
  * "Open with a direct answer … (a clear view — e.g. "**I'd buy some, but in stages**" / "I'd wait" / "I'd trim")"
  * "… the bull case and the bear case; and **your recommendation** with reasoning."
  * "Make it actionable: for a buy idea, **suggest a sensible position size** (as a share of the portfolio), an entry approach …"
  * It ends answers with "Educational guidance based on public data — not personalised financial advice", which contradicts the instructions above. This is personal buy/sell/position-size advice — the core of Step 1.
* Language: "Reply in the user's language" is already in the prompt.
* The Help Agent (`backend/app/services/help.py`) is separate and already refuses investment advice.

## 4. How Pulse stories are generated

* **Community discussions:** written by members (anonymous). **Public discussions:** collected from HN/StockTwits (Reddit off) as short attributed excerpts, labelled by source.
* **Editorial stories** (`pulse_editorial.py`, labelled Nexis): built by `market_pulse.compute` from "evidence" — news headlines **plus up to 240-character publisher summaries**, filings and price moves (`_synthesize` sends `[n] date · publisher · kind · title — summary` to the model). The model writes "what happened", the debate and bull/bear points, each citing evidence; numbers are checked against the sources. **So editorial stories are AI summaries of third-party news reporting** (not full-article rewrites, but paraphrase of publishers' headlines/excerpts) → Step 3 asks to write only from the app's own data and link to news instead.
* Topics include non-UAE themes: "AI infrastructure", "US equities", crypto assets (e.g. the US-stocks story used in tests).
* AI label today: a disclosure line ("Written by Nexis with AI assistance…"), not "AI-generated · data as of <date>".

## 5. How Finstagram posts are created

* Real members post; likes/comments/saves come only from real people.
* **Platform bot accounts** (`users.kind = "page"`, `newsfeed.PAGES`, e.g. `uae.markets`, `gulf.business` and several global/US/crypto pages) auto-post news headlines + excerpts + hotlinked publisher images, YouTube videos and generated charts (`chartposts.py`). Their bios say "Automated news page". **At 1440px the feed is almost entirely these bot posts, mostly US/crypto news** (screenshot `finstagram-1440.png`). Step 2's `is_seed` handling applies to these.
* No fake people or engagement were found.

## 6. "advisor", "advice", "recommend", "buy", "sell", "financial advisor"

Occurrences in the website source (case-insensitive): `i18n.tsx` 28, `Legal.tsx` 16, `Home.tsx` 13, `AppLayout.tsx` 12 (nav label "AI Advisor", page titles), `Advisor.tsx` 11, `Pro.tsx` 9, `Instrument.tsx` 7 ("Ask the advisor"), `ResearchSearch.tsx` 7, `Finstagram.tsx` 5, `Compare.tsx` 5 ("recommendation section" in the PDF report), `Backtesting.tsx` 5 (trading buy/sell signals — legitimate), `pro.tsx` 5, others 1–4.
Metadata (`frontend/index.html`, seen by every crawler and share card):
* `<title>Nexis Finance — UAE stocks, bonds, AI financial advisor and Finstagram</title>`
* description/keywords/OG/Twitter: "Ask an **AI financial advisor**…", "an AI advisor that researches live data", JSON-LD description "an AI financial advisor".
* `<noscript>`: "…an AI financial advisor…". Also "**Live** prices" — data is delayed (see §8).
Sitemap: `/advisor` (weekly, 0.9). Prompts: §3. Help articles mention "AI Advisor" (product name).
Compare's PDF has a "recommendation section" (`Compare.tsx` page description) — needs review in Step 1.

## 7. Routes and their main interactive elements

| Route | Main elements |
|---|---|
| `/` Home | Ask/search box (to Advisor), market strip with sparklines, market wire (news), Pulse preview, UAE movers |
| `/markets` (`?list=uae|uae_bonds|indices|us|bonds|commodities|…`) | Symbol search, list tabs, sector filters, exchange filter, sortable tables |
| `/markets/:symbol` (stocks, `.BOND` bonds, indices, FX, crypto) | Price chart + range (radio group), key stats, analysts, financials, statements table, news, profile, Finstagram preview; buttons Ask the advisor / Compare / Valuation / Post |
| `/valuation`, `/valuation/:symbol` | DCF assumptions form, recalculate, football-field chart, DCF/sensitivity/comps tables, peer editor, PDF report |
| `/compare` | Instrument picker, presets, period/interval controls, charts, ranking, tables, PDF report |
| `/advisor` (AI assistant), `/assistant` (research assistant, workspace) | Chat input, streaming answers, usage note |
| `/pulse`, `/pulse/latest|trending|following|saved|topics|search|overview`, `/pulse/topic/:t`, `/pulse/asset/:s`, `/pulse/d/:id/:slug` | Feed tabs, kind filter, composer (incl. Reddit link), reactions, follow/save/report, comments |
| `/finstagram`, `/finstagram/p/:id`, `/u/:username`, `/s/:symbol`, `/t/:tag`, `/social/*` | Feed, composer (images, video), likes/comments/saves, follow, report |
| `/my-nexis`, `/my-nexis/:section`, `/my-nexis/asset/:symbol` | Investments (add/edit/archive), watchlist, alerts, plan allowance picker, analytics |
| `/pro`, `/pricing`, `/pro/welcome` | Plan cards, checkout buttons (Stripe) |
| `/settings`, `/notifications` | Account forms, billing (portal/cancel/change plan), notification preferences |
| `/login` | Email/phone tabs, sign-in/sign-up, OAuth buttons |
| `/terms`, `/privacy`, `/disclaimer`, `/community-guidelines`, `/ai-disclosure` | Static legal text |
| Research workspace (`/research`, `/market-data`, `/portfolio-lab`, `/risk`, `/stress-testing`, `/factors`, `/strategies`, `/backtesting`, `/machine-learning`, `/regimes`, `/anomalies`, `/experiments`, `/reports`, `/system`, `/connections`, `/intelligence`, `/xray`, `/transactions`, `/graph`, `/reconciliation`, `/lineage`, `/economic`, `/audit`, `/developer`, `/data-quality`, `/asset-research`) | Quant tools on shared datasets (read-only for the public) |
| Everywhere | Top bar (menu, command palette, Pulse/My Nexis/AI Advisor links, notifications, account, theme), mobile tab bar, Help Agent launcher |

**Not present yet** (needed by later steps): `/about`, a waitlist, `/circles`, bond *list* route (it's `/markets?list=uae_bonds`), `/research` for the assistant (**`/research` is already the Research Overview page** — see §9).

## 8. Responsive problems (current code, local build, fixtures)

Automated (responsive suite, details in `tests/BASELINE.md`) and visual review of `tests/screenshots/*-{375,768,1440}.png`:

1. **Valuation (`/valuation/EMAAR.AE`) scrolls sideways at 320 and 375px** (60px): the DCF projection, sensitivity and trading-comparables tables are wider than the screen and not in a scroll container; the PV and P/B columns are cut off. The football-field chart squeezes its bars into a sliver next to long labels; KPI captions are cut mid-word ("Blended value = median of t…").
2. **Top bar on phones:** menu button 38×30, search 104×32, notifications 32×32, "Sign in" 58×30 — all under 44×44 on every page.
3. **Small text on phones:** most UI text is 12–13px (top bar, captions, table cells, chips, tabs). Every page fails the 14px check at ≤ 480px; the worst are Markets (349 elements at 320px), Compare (248), Ticker (214), Valuation (213).
4. **Small tap targets** across content: chips, tabs, sort headers, table links, Pulse/Finstagram action buttons (up to 187 per page on Markets).
5. **Compare at 375px:** wide tables scroll inside their cards (allowed by the rules) but read as broken — the second column is cut mid-number; a card layout would read better. Correlation heatmap labels are tiny.
6. **Help launcher covers the AI Advisor's send button at 1280×720** (desktop browser default size) in Chromium, Firefox and WebKit — the send click is intercepted. (Introduced in PR #15; the launcher is raised on /advisor but not enough for a 720px-tall window.) On Valuation at 375px it also sits over the warning box text.
7. Login at 320px: Email/Phone tabs 141×33, "Create an account" link 108×17.
8. Finstagram at 1440px: content-wise dominated by bot news posts (see §5); layout itself is fine.
9. Not broken: navigation drawer opens/closes and every link is reachable at all phone/tablet sizes; login and AI chat inputs stay visible on phones; no other page scrolls sideways; ticker page at 768px and home at 375px look clean.

Other code issues noticed: `Markets.tsx` reads `localStorage` without try/catch (throws in some private modes).

## 9. Decisions needed before / during Steps 1–7

1. **`/research` is taken** by the Research Overview workspace page. Options: move that page (e.g. `/research-overview`), or use another path for the assistant (e.g. `/ai-research`). Route change → needs your OK.
2. **Step 6 vs PR #15:** PR #15 sells Nexis Plus/Pro through Stripe. Step 6 asks for "Nexis Pro — coming soon" with a waitlist and no payment provider. Which applies? (Removing billing would be a large reversal.)
3. **AI cap:** Step 6 says e.g. 5 questions/day for logged-out and free users; today it's 3/day visitors and 20/month Free. Keep, or change?
4. **Data licensing (§2, HIGH):** Yahoo's and TradingView's unofficial endpoints are the price sources. Free licensed alternatives for ADX/DFM real-time or delayed data are limited; this is a business decision (the overhaul won't expand them).
5. **Finstagram bot accounts:** Step 2 says hide seeded/bot posts from public feeds — that removes most of the current feed content. Confirm.
6. **Step 4 SSR approach:** to be proposed before doing it, as you asked.
7. **CMA wording:** the notice "Nexis is not licensed by the UAE Capital Market Authority" is a factual claim only you can confirm.
8. **Company details for /about** (name, registration, founders, contact) — placeholders until you provide them.
