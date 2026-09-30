# Markets, AI advisor, valuation and InstaFin

These features work on any instrument the market-data provider covers, fetched live on demand and
cached in the database (`market_cache`), so they need no seeding.

## Coverage

| Area | What is available | Source |
|---|---|---|
| Stocks & ETFs | Most global exchanges, including the **Dubai Financial Market** (`.AE`, prices in AED) and Saudi Tadawul (`.SR`) | Yahoo Finance public endpoints (unofficial, no SLA) |
| Indices, rates | S&P 500, FTSE, DFM General Index (`DFMGI.AE`), Tadawul (`^TASI.SR`), US Treasury yields (`^IRX ^FVX ^TNX ^TYX`) | same |
| Bonds | Treasury yields and bond ETFs (AGG, BND, TLT, IEF, SHY, LQD, HYG, EMB); individual bonds are not quoted by the free provider | same |
| Commodities, FX, crypto | Futures (`GC=F`, `CL=F` …), currencies (`USDAED=X` …), crypto (`BTC-USD` …) | same |
| Fundamentals | Key statistics, TTM financials, four fiscal years of statements, analyst consensus and targets, company profile | same (`quoteSummary`, fundamentals time series) |
| News | Headlines with publisher, time and link — Yahoo Finance plus Google News (covers Gulf outlets such as Khaleej Times, Arabian Business, The National) | RSS / JSON feeds; article bodies are not scraped |

Not available: **Abu Dhabi Securities Exchange (ADX)** listings (the free provider does not carry them), intraday data older than ~2 years, and individual bond prices.

## Global Markets & instrument pages

`/markets` lists live quotes by region/asset class; the search box resolves names and misspellings ("emmar" → `EMAAR.AE`).
`/markets/:symbol` shows the price chart (line or candles; 1D … Max with hourly/daily/weekly/monthly bars), key
statistics, TTM financials, annual statements, analyst consensus with the buy/hold/sell distribution, profile, news and
InstaFin posts that mention the ticker.

## Compare & Reports

`POST /api/markets/compare` compares 1–8 instruments over a preset or custom window at `1h | 1d | 1wk | 1mo` bars,
with periodic returns by `hour | day | week | month | quarter | year`:

* total and annualised return, volatility, Sharpe/Sortino (omitted for windows under three months), maximum drawdown,
  best/worst bar, correlation and beta to the first instrument; returns in each instrument's own currency;
* fundamentals and analyst consensus side by side;
* a **scorecard** that ranks the group 0–100 in momentum, risk-adjusted return, drawdown resilience, valuation (P/E),
  analyst view and income; ties share a score, and categories missing for any instrument are dropped and re-weighted.

`POST /api/markets/compare/report` renders the PDF: summary, charts, tables, periodic returns, correlation, fundamentals,
scorecard, recent headlines and a **Recommendation** section with key risks, methodology and disclaimer.

## Valuation (investment banking)

`GET|POST /api/valuation/:symbol` — every assumption is returned with its source and can be overridden:

* **DCF (FCFF, two-stage).** Base FCF = average of up to the last three fiscal years; starting growth = revenue CAGR
  clipped to −5…12 %, fading linearly to terminal growth; WACC from CAPM (US 10-year yield, Blume-adjusted beta, 5 % ERP,
  country risk premium) and market-value weights; sensitivity grid (WACC ±1 %, g ±1 %).
* **Reverse DCF**: the starting growth the current price implies.
* **Trading comparables** from curated GCC sector groups (banks, real estate, utilities, telecoms) and US mega-cap tech,
  otherwise provider suggestions filtered to the same sector; users can edit the peer list. Banks and other financials use
  P/E and P/B only (EV multiples and FCF DCFs are not meaningful for them).
* **Football field** of all ranges against the price, a blended fair value (median of method midpoints; analyst targets
  shown for reference only) and a view that is marked *low confidence* when warnings apply and *inconclusive* when methods
  disagree by more than 2.5×.

`POST /api/valuation/:symbol/report` renders the valuation PDF with the same recommendation section.

## AI advisor

`POST /api/advisor/chat` takes the conversation and answers with tools the model must use for every figure:
`search_instruments`, `get_instrument`, `get_news`, `get_price_stats`, `compare_instruments`, `run_valuation`,
`position_calculator` (cost of *N* shares or an amount, values at the 52-week low/high and analyst targets, dividends,
share of daily volume). The tool trace is returned and shown under each answer.

The language model is reached through an OpenAI-compatible endpoint, in this order: `NEXIS_LLM_API_KEY` (+
`NEXIS_LLM_BASE_URL`, `NEXIS_LLM_MODEL`), `ANTHROPIC_API_KEY`, or the **Vercel AI Gateway** using `AI_GATEWAY_API_KEY` or
the deployment's OIDC token (no key needed on Vercel once the account's AI Gateway is activated). Without a working
model the advisor returns a **live data briefing** built from the same tools and says so; `GET /api/advisor/status`
reports whether the model actually answers (a one-token probe, cached for 30 minutes). Requests are rate-limited per IP.

Comparison and valuation PDFs use the model only to write prose from the computed figures; without it they include a
transparent rule-based recommendation instead.

## InstaFin (social)

Accounts (`/api/auth/*`): scrypt password hashes, opaque session tokens (only their SHA-256 is stored) in an HTTP-only,
`SameSite=Lax`, `Secure` cookie; sign-up and login are rate-limited.

Feed (`/api/social/*`): posts up to 2,200 characters with an optional photo, `$CASHTAG` and `#hashtag` extraction with
live price cards, likes, comments, follows, "For you / Following / Trending" feeds, per-ticker and per-tag feeds,
profiles with a photo grid and links to the person's Instagram, X, LinkedIn, TikTok, YouTube and website, share
shortcuts (native share sheet, Instagram, WhatsApp, X, LinkedIn, Telegram, Facebook, email, copy link) and
reporting (a post reported by three people is hidden).

Images are downscaled in the browser (≤1440 px) to fit the hosting request limit, then decoded and re-encoded as JPEG
on the server, which also strips EXIF metadata such as GPS location.
