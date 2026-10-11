# Data sources

Every external source Nexis reads: what it is used for, its terms and free limits as checked (October 2026), and how
Nexis stays within them.

* **One registry.** Every source sits behind `backend/app/markets/sources.py` (the provider interface).
  `GET /api/system/sources` lists them live, with licence and risk.
* **No paid sources.** The rule is official, documented, free sources only.
* **No new unofficial endpoints were added.** The existing unofficial ones are unchanged, as instructed, and are
  marked below. Replacing them is the owner's licensing decision.

## Official, documented sources

| Source | Used for | Key | Terms / licence | Free limits (checked) | How Nexis complies |
|---|---|---|---|---|---|
| **Wikidata** (wbsearchentities API, Query Service SPARQL) | World Graph: entity search; parent/subsidiary, ownership, listing (with ticker), industry and country, with dates and cited references | None | CC0. Wikimedia User-Agent policy: a descriptive User-Agent with contact details is required | Query Service: 60 s of processing per 60 s, 5 parallel queries per IP, 60 s per query; HTTP 429 beyond that | One request at a time, at least 1 s apart. `NEXIS_WIKIDATA_USER_AGENT`. One cheap query per company (~1 s). Search cached 1 day, relationships 7 days. On errors the last cached copy is served and marked stale. A 429 is reported, never retried in a loop |
| **SEC EDGAR** (submissions, XBRL company facts) | US issuer profiles (SIC industry, address) and reported financial facts | None | Public data; SEC fair-access policy | 10 requests/s; a declared User-Agent with contact details is required | Serialised, at most ~7/s. `NEXIS_SEC_USER_AGENT` (a real contact address is still needed in production) |
| **US Treasury Fiscal Data** | Treasury yields and rates (macro series in the research workspace) | None | Public domain; free for commercial use | No published fixed limit; HTTP 429 when throttled | Cached; few requests; backs off on errors |
| **World Bank Indicators API** | Macro indicators by country | None | CC BY 4.0 (attribution), plus World Bank terms: no excessive or abusive use | No quota; "reasonable request volume" | Cached; attributed in the UI and here |
| **FRED** (St. Louis Fed) | US macro series (optional) | Free API key (`NEXIS_FRED_API_KEY`) | Free, no commercial restriction on the API; some series carry their source's copyright, so check before republishing a whole series | 120 requests/min per key; HTTP 429 beyond | Off until a key is set; cached; series shown with their source |
| **Hacker News** (Algolia search API) | "Around the web" public threads in Pulse | None | Public API | 10,000 requests/hour per IP | A handful of queries per Pulse tick; usernames removed; every quote links to the original |
| **YouTube** channel feeds and the official embed | Market videos on Finstagram | None | YouTube embed terms | — | Official embed only; nothing downloaded |
| **Reddit** | Only when a member shares a link: Reddit's official embed, loaded by the reader's browser on request | None | Developer terms: monetised use of the API needs written approval; robots.txt disallows crawling | — | **Automated collection is off** (since PR #15). Nexis never fetches or stores Reddit content |
| **Stripe** | Billing | Secret key | Stripe terms; per-transaction fees only | — | See [STRIPE-SETUP.md](STRIPE-SETUP.md) |
| **AI: Vercel AI Gateway** (OIDC in production) | Advisor, Help, Devil's Advocate, Pulse narratives | OIDC / optional provider keys | Provider terms | Per gateway plan | Members only; per-member daily and monthly limits; every Advisor answer logged |

## News republishing policy (since 2026-10-11, `backend/app/markets/policy.py`)

Owner decision: zero cost, and no use or redistribution that a source's terms don't permit.

| Policy | Sources | Effect |
|---|---|---|
| **Blocked** | Investing.com (terms prohibit reuse); Yahoo Finance news and RSS; Google News (personal, non-commercial use only); chart posts drawn from Yahoo/TradingView prices | Not fetched or posted. Earlier posts are **hidden** (the reversible `hidden` flag), not deleted |
| **Restricted** | Other publisher RSS: Khaleej Times, Gulf News, The National, Arabian Business, MarketWatch, CNBC (incl. its Tech, Energy and Economy feeds), Cointelegraph | Headline, publisher and link to the original only: no excerpt, no hotlinked image. Earlier posts were trimmed the same way |
| **Off** | StockTwits | `NEXIS_PULSE_PUBLIC_STOCKTWITS` now defaults to `false` |
| **Permitted** | YouTube official embeds and the official APIs above | Unchanged |

* Company headlines on instrument pages, the advisor's news tool, Devil's Advocate evidence and Pulse evidence now
  come from the restricted publisher feeds, matched to the company. Google News and Yahoo are not used.
* `NEXIS_NEWS_POLICY=licensed` lifts these rules. Set it only once the licences are held.
* SEO pages never render prices or provider text.

## Existing sources that are not official APIs (unchanged, behind the registry)

| Source | Used for | Risk | Why it matters | What would replace it |
|---|---|---|---|---|
| **Yahoo Finance** (`query1/query2` endpoints, cookie and "crumb") | Quotes, history, fundamentals, analyst consensus, company news, search, peers. The main price source | **High** | No public API licence; Yahoo's terms prohibit automated access and commercial reuse | A licensed market-data feed (owner decision; no paid feed without discussion). Add an adapter to `sources.py` and set `NEXIS_PRICE_SOURCE`. Meanwhile prices are displayed on the site only: chart posts are blocked and SEO pages carry no prices |
| **TradingView** (scanner and chart endpoints) | ADX shares, UAE bonds and sukuk, UAE indices | **High** | Internal endpoints behind TradingView's widgets; data use outside the widgets needs a licence | A licensed UAE feed (ADX/DFM data vendors). Set `NEXIS_UAE_SOURCE` |
| **Google News RSS** | ~~Regional headlines per company~~ **Blocked since 2026-10-11** | Medium | Feeds are offered for personal, non-commercial reading | Replaced by publisher feeds matched to the company |
| **Publisher RSS** (Khaleej Times, Gulf News, The National, Arabian Business, MarketWatch, CNBC, Cointelegraph) | Finstagram news pages; company headlines | Medium | RSS is published for reading, but commercial republishing permission isn't verified | **Headline, publisher and link only** (no excerpt, no image). Investing.com and Yahoo RSS are **blocked**. Ask the publishers before showing more |
| **StockTwits** public symbol streams | Pulse "around the web" | Medium | Its public terms let it publish public content through APIs, but commercial reuse by third parties **could not be confirmed** from its public pages | **Switched off since 2026-10-11** (default). Re-enable only with StockTwits' permission |

## What these sources cannot tell us

* **Suppliers and customers.** No free structured source publishes them reliably. Nexis does not show them as facts.
* **Wikidata statements without a reference.** Many are true (Emaar is listed on the Dubai Financial Market), but
  nothing is cited, so they are shown as **unreferenced**, never as verified.
* **Owners of a company.** Wikidata ownership lists are often incomplete and undated. They are shown as reported
  statements with their dates, not as a shareholder register.
* **Article text.** Only headlines are stored. Devil's Advocate cites headlines as headlines.
* **Real-time prices.** All prices are delayed and labelled with their time.

## References (checked 2026-10-10 / 2026-10-11)

* Wikidata Query Service limits: <https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/query_limits>
* SEC fair access: <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data>
* FRED limits: <https://apis.io/rate-limits/fred/fred-rate-limits/>
* World Bank dataset terms: <https://www.worldbank.org/ext/en/legal/terms-conditions/datasets>
* Treasury Fiscal Data: <https://providers.apievangelist.com/providers/fiscaldata/>
* Hacker News Algolia limits: <https://npmjs.com/package/hacker-news-api>
* StockTwits terms: <https://stocktwits.com/terms>
