# Financial connectivity & intelligence layer

Nexis connects (or imports) financial data from several sources, normalises it into one data model
with record-level lineage, reconstructs the user's portfolio automatically, and investigates it.

```text
                ┌── Brokerage (Alpaca API · CSV/XLSX/JSON statements)
                ├── Market data (Yahoo chart endpoint · synthetic universe · CSV)
User ───────────┼── Economic data (US Treasury yield curve · World Bank · FRED with key)
                ├── Regulatory data (SEC EDGAR profiles & XBRL facts)
                └── Files (holdings · transactions · market data)
                         ↓
                NEXIS NORMALISATION  (column detection → mapping → validation → canonical records)
                         ↓
                 UNIFIED DATA MODEL  (accounts · holdings · transactions · prices · series · profiles)
                         ↓            + source_records (verbatim rows) for lineage
        ┌────────────────┼────────────────┐
   Portfolio         Risk engine       ML engine (regimes on live data)
   reconstruction        │
        └──────────── Research ─────────┘
                         ↓
         Intelligence Graph · X-Ray · Attribution · Diagnostics · Assistant
                         ↓
               Reports · Public API · Webhooks · Audit log
```

## Provider abstraction

`app/connectivity/base.py` defines `FinancialConnectionProvider` with category mixins
(`BrokerageProvider`, `EconomicDataProvider`, `RegulatoryDataProvider`); each provider declares a
`ProviderSpec` (capabilities, auth type, credential fields, requirements, documentation, data class
and **how it was verified**). A connection is only marked *connected* after `verify()` makes a real
request that succeeds.

| Provider | Category | Auth | Verification in this project |
|---|---|---|---|
| Public market data (Yahoo chart endpoint) | markets | none | live |
| Synthetic research universe | markets | none | live (deterministic) |
| US Treasury daily par yield curve | economics | none | live |
| World Bank indicators | economics | none | live |
| FRED | economics | API key | mocked responses (needs your key) |
| SEC EDGAR submissions + company facts | regulatory | none (User-Agent) | live |
| Alpaca Trading API | accounts | API key pair | mocked responses (needs your account) |
| File import (CSV / JSON / XLSX) | files | file | live |
| Plaid, SnapTrade, IBKR, Schwab, Polygon | — | — | **Coming soon** (not simulated) |

## Security

* Credentials are encrypted at rest with Fernet (`NEXIS_SECRET_KEY`); outside development the app
  refuses to store credentials without an explicit key.
* The API never returns credentials — only a masked hint (`••••1234`). Disconnect wipes them.
* `redact()` scrubs secret-like keys from everything written to the audit log.
* Nexis never asks for brokerage passwords; brokerages without a secure API use statement import.
* Public API keys are shown once and stored as SHA-256 hashes; revoked keys are rejected.
* Webhook payloads are signed (`X-Nexis-Signature: sha256=HMAC(secret, body)`); URLs must be https
  and non-private outside development.

## Universal import

`preview → map → import`. Columns are matched against canonical fields with confidence scores
(exact 100%, synonym 90%, fuzzy ≤ 85%); anything below 90% is flagged for review and every field can
be re-mapped manually. Values are normalised (numbers with `$`, commas and `(negatives)`; ISO and
US/European dates; transaction-type synonyms; `BRK.B → BRK-B`; currency symbols → ISO codes).
Every row is stored verbatim in `source_records` with its outcome (imported / rejected / duplicate)
and the canonical ID it produced (e.g. `TX-000012`). Re-importing an identical file is refused by
content hash; identical transactions are detected per account.

## Portfolio reconstruction

* **Position source per account:** latest holdings snapshot, else positions derived from
  transactions with the selected cost-basis method. The same account from two sources is one row
  (institution + external id), so consolidation never double-counts; conflicts surface in
  reconciliation instead.
* **Valuation:** stored market data (real preferred over synthetic), then the imported price
  (flagged), else *unpriced* (excluded from totals). Non-USD holdings use stored `CCYUSD=X` rates.
* **History:** transaction accounts are reconstructed day by day (time-weighted: flows are not
  performance); snapshot-only accounts are a labelled **backcast** of current holdings.

## Investigation

* **Portfolio X-Ray:** allocation, SEC SIC industry, headquarters country, currency, concentration
  (top-5/10, HHI), per-holding volatility contribution.
* **Why is my portfolio moving?** Daily contribution = prior-close position value × price change ÷
  prior-close portfolio value, summed over the period; the compounding residual is reported.
* **Risk drill-down:** Euler volatility contributions, undiversified vs actual volatility
  (correlation effect), industry risk, tail (CVaR) contributors, drawdown contributors.
* **Diagnostics:** transparent measurements per category against documented reference thresholds —
  no composite score.
* **Transactions:** trade counts, fees, turnover, frequency, FIFO or average-cost realised and
  unrealised P&L with open lots; oversells are flagged, methods never mixed.
* **Reconciliation:** holdings disagreements between sources, snapshot vs transaction-implied
  quantities, near-duplicate transactions across sources — reported, never auto-corrected.
* **Intelligence Graph:** account → portfolio → asset → industry → factor (price-derived terciles)
  → risk → benchmark → regime, built only from stored relationships; every node is inspectable.
* **Research Assistant:** deterministic intent matching over stored metrics with an evidence table;
  unanswerable questions get an explicit "cannot verify" response.

## Sample data

`scripts/make_samples.py` writes three fictional statements (real tickers, invented quantities) in
different formats. *Load sample statements* on the Connections page imports them through the normal
pipeline; they are badged **SAMPLE FILE** and classed as user-imported data. Brokerage A's snapshot
deliberately disagrees with its transaction history (MSFT 100 vs 95) to demonstrate reconciliation.
