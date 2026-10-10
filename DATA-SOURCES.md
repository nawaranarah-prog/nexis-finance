# Data sources

Every external source Nexis uses: what it provides, its terms and free limits as checked, and how Nexis stays inside
them. Only official, documented, free sources are added. The unofficial endpoints already in the app (Yahoo Finance,
TradingView, publisher RSS, Google News, StockTwits) are listed with their risk in [AUDIT.md](AUDIT.md). They are
left unchanged until the data-layer step (overhaul Step 3) moves each one behind the provider interface. That step
completes this file.

| Source | Used for | Key | Terms / licence | Free limits | How Nexis complies |
|---|---|---|---|---|---|
| **Wikidata** (wbsearchentities API, Query Service SPARQL) | Entity search; company relationships for the World Graph: parent/subsidiary (P749/P355), ownership (P127/P1830), stock-exchange listing with ticker (P414 + P249), industry (P452), country (P17), with start/end dates and the references editors cited | None | Data is CC0 (no attribution required; Nexis still links every statement). Wikimedia User-Agent policy: clients must send a descriptive User-Agent with contact details | Query Service: 60 s of processing per 60 s and 5 parallel queries per IP; 60 s per query. HTTP 429 when exceeded (checked 2026-10-10) | One request at a time, at least 1 s apart. Descriptive User-Agent (`NEXIS_WIKIDATA_USER_AGENT`). One cheap query per company (≈1 s; explicit per-property patterns). Search results cached 1 day; relationships cached 7 days; on errors the last cached copy is served and marked stale. A 429 is reported, not retried in a loop |
| **SEC EDGAR** (submissions, XBRL company facts) | US issuer profiles (SIC industry, business address) and reported financial facts | None | Public data. SEC fair-access policy | 10 requests/s; a declared User-Agent with contact details is required | ≤ ~7 requests/s, serialised. User-Agent from `NEXIS_SEC_USER_AGENT`. A real contact address still needs to be set in production |

## What these sources cannot tell us

* **Suppliers and customers.** No free structured source publishes them reliably. Nexis does not show them as facts.
  Supply-chain links will appear only where a cited document (e.g. an SEC filing passage) states them, marked with
  that evidence.
* **Wikidata statements without a reference.** Many are true (Emaar is listed on the Dubai Financial Market), but
  Wikidata cites nothing for them. They are shown as **unreferenced**, never as verified.
* **Owners of a company.** Wikidata ownership lists are incomplete and undated more often than not. Nexis shows
  them as reported statements with their dates (or "no date"), not as a current shareholder register.
