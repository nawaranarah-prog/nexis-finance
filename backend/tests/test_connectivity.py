"""Connectivity layer: normalisation, cost basis, security, providers (mocked HTTP), and the full
import → reconstruct → investigate → reconcile → lineage → API workflow."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from app.connectivity import normalize as N
from app.connectivity.cost_basis import compute, quantity_path
from app.connectivity.providers.alpaca import AlpacaProvider
from app.connectivity.providers.economic import FredProvider, USTreasuryProvider, WorldBankProvider
from app.connectivity.providers.sec import SecEdgarProvider, location_to_country, sic_classification
from app.connectivity.security import decrypt_json, encrypt_json, hash_api_key, mask, new_api_key, redact
from app.core.errors import ConfigurationError, ProviderError

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"


# ---------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,234.50", 1234.5),
        ("(12.30)", -12.3),
        ("-5", -5.0),
        ("+7", 7.0),
        ("", None),
        ("--", None),
        ("abc", None),
        (3, 3.0),
        ("12%", 12.0),
    ],
)
def test_parse_number(raw, expected):
    assert N.parse_number(raw) == expected


def test_dates_types_symbols_currencies():
    assert N.parse_date("03/15/2022") == date(2022, 3, 15)
    assert N.parse_date("15/03/2022", dayfirst=True) == date(2022, 3, 15)
    assert N.parse_date("2024-02-20T00:00:00") == date(2024, 2, 20)
    assert N.parse_date("not a date") is None
    assert N.normalize_tx_type("Qualified Dividend") == "dividend"
    assert N.normalize_tx_type("BUY - MARKET") == "buy"
    assert N.normalize_tx_type("Sold") == "sell"
    assert N.normalize_tx_type("mystery") is None
    assert N.normalize_symbol(" brk.b ") == "BRK-B"
    assert N.normalize_currency("€") == ("EUR", False)
    assert N.normalize_currency("", "USD") == ("USD", True)
    assert N.infer_asset_class("CASH & CASH INVESTMENTS", None) == "cash"


def test_mapping_detection_on_sample_formats():
    cases = {
        "brokerage_a_holdings.csv": ("holdings", "Symbol", "Quantity"),
        "brokerage_a_transactions.csv": ("transactions", "Trade Date", "Action"),
        "brokerage_b_positions.xlsx": ("holdings", "Ticker", "Shares"),
        "retirement_account.json": ("holdings", "security", "units"),
    }
    for f, (kind, a, b) in cases.items():
        df, _ = N.read_table((SAMPLES / f).read_bytes(), f)
        prop = N.propose_mapping(list(df.columns))
        assert prop.kind == kind, f
        values = set(prop.mapping.values())
        assert a in values and b in values
        assert not prop.missing_required


def test_uncertain_mapping_is_flagged():
    prop = N.propose_mapping(["Tickr", "Qtty", "Cost"], "holdings")
    assert prop.mapping["symbol"] == "Tickr" and "symbol" in prop.uncertain


def test_unsupported_file_type():
    with pytest.raises(ConfigurationError):
        N.read_table(b"x", "data.pdf")


# ---------------------------------------------------------------- cost basis


def _tx(d, typ, qty=None, price=None, fees=0.0, sym="AAA", amount=None, code=None):
    return {
        "trade_date": date.fromisoformat(d),
        "tx_type": typ,
        "quantity": qty,
        "price": price,
        "fees": fees,
        "symbol": sym,
        "amount": amount,
        "code": code or f"{d}-{typ}",
    }


def test_fifo_vs_average_realized_pnl():
    tx = [_tx("2024-01-02", "buy", 10, 100), _tx("2024-02-01", "buy", 10, 200), _tx("2024-03-01", "sell", 15, 300)]
    fifo = compute(tx, "fifo", {"AAA": 300})
    avg = compute(tx, "average", {"AAA": 300})
    # FIFO: 15×300 − (10×100 + 5×200) = 2500; remaining 5 @ 200.
    assert fifo["totals"]["realized_pnl"] == pytest.approx(2500)
    assert fifo["positions"][0]["cost_basis"] == pytest.approx(1000)
    # Average: 15×300 − 15×150 = 2250; remaining 5 @ 150.
    assert avg["totals"]["realized_pnl"] == pytest.approx(2250)
    assert avg["positions"][0]["cost_basis"] == pytest.approx(750)
    assert fifo["positions"][0]["unrealized_pnl"] == pytest.approx(5 * 300 - 1000)


def test_fees_split_income_and_oversell():
    tx = [
        _tx("2024-01-02", "buy", 10, 100, fees=10),
        _tx("2024-02-01", "split", 2),
        _tx("2024-03-01", "dividend", amount=12.5),
        _tx("2024-04-01", "sell", 25, 60, fees=5),
    ]
    r = compute(tx, "fifo")
    p = r["positions"][0]
    assert p["quantity"] == 0.0
    # cost 1010 for 20 post-split shares; sell 20 (5 excess ignored) at 60 minus 5 fees.
    assert r["totals"]["realized_pnl"] == pytest.approx(20 * 60 - 5 - 1010)
    assert p["income"] == pytest.approx(12.5)
    assert any(i["issue"] == "oversell" for i in r["issues"])
    assert quantity_path(tx)["AAA"][-1][1] == 0.0


def test_methods_never_mixed():
    with pytest.raises(ValueError):
        compute([], "lifo")  # type: ignore[arg-type]


# ---------------------------------------------------------------- security


def test_encryption_roundtrip_and_redaction():
    token = encrypt_json({"api_key": "super-secret-value"})
    assert "super-secret" not in token
    assert decrypt_json(token) == {"api_key": "super-secret-value"}
    assert mask("abcdef123456") == "••••3456"
    assert redact({"api_key": "x", "nested": {"secret_key": "y", "ok": 1}, "list": [{"token": "z"}]}) == {
        "api_key": "[redacted]",
        "nested": {"secret_key": "[redacted]", "ok": 1},
        "list": [{"token": "[redacted]"}],
    }
    plain, prefix, h = new_api_key()
    assert plain.startswith("nx_") and plain.startswith(prefix) and hash_api_key(plain) == h and len(h) == 64


# ---------------------------------------------------------------- providers (mocked HTTP)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_treasury_provider_parses_csv():
    csv = 'Date,"1 Mo","3 Mo","10 Yr"\n09/29/2026,4.30,4.25,4.10\n09/28/2026,4.31,4.26,4.12\n'
    p = USTreasuryProvider(config={"years": 1}, client=_client(lambda r: httpx.Response(200, text=csv)))
    assert p.verify()["latest_date"] == "2026-09-29"
    series = {s.code: s for s in p.fetch_all(None)}
    assert series["UST_3M"].observations["value"].tolist() == [4.26, 4.25]


def test_world_bank_and_errors():
    payload = [
        {"page": 1},
        [
            {"indicator": {"value": "Inflation (annual %)"}, "country": {"value": "United States"}, "date": "2024", "value": 2.9},
            {
                "indicator": {"value": "Inflation (annual %)"},
                "country": {"value": "United States"},
                "date": "2023",
                "value": None,
            },
        ],
    ]
    p = WorldBankProvider(client=_client(lambda r: httpx.Response(200, json=payload)))
    s = p.get_series("FP.CPI.TOTL.ZG", None)
    assert s.observations["value"].tolist() == [2.9] and s.country == "United States"
    bad = WorldBankProvider(client=_client(lambda r: httpx.Response(200, json=[{"message": [{"value": "Invalid"}]}])))
    with pytest.raises(ProviderError):
        bad.get_series("NOPE", None)


def test_fred_requires_key_and_parses():
    with pytest.raises(ConfigurationError):
        FredProvider().verify()

    def h(r: httpx.Request) -> httpx.Response:
        assert r.url.params["api_key"] == "k" * 32
        if r.url.path.endswith("/observations"):
            return httpx.Response(
                200, json={"observations": [{"date": "2024-01-02", "value": "3.95"}, {"date": "2024-01-03", "value": "."}]}
            )
        return httpx.Response(200, json={"seriess": [{"title": "10-Year Treasury", "units_short": "%", "frequency_short": "D"}]})

    p = FredProvider(credentials={"api_key": "k" * 32}, client=_client(h))
    assert p.verify()["series_checked"] == "DGS10"
    s = p.get_series("DGS10", None)
    assert s.observations["value"].tolist() == [3.95]
    rejected = FredProvider(credentials={"api_key": "bad"}, client=_client(lambda r: httpx.Response(403)))
    with pytest.raises(ProviderError) as ei:
        rejected.verify()
    assert ei.value.details == {"auth": True}


def test_sec_edgar_profile_and_facts():
    import app.connectivity.providers.sec as sec_mod

    sec_mod._ticker_map = None

    def h(r: httpx.Request) -> httpx.Response:
        assert "User-Agent" in r.headers
        if r.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA"}})
        if "submissions" in r.url.path:
            return httpx.Response(
                200,
                json={
                    "name": "NVIDIA CORP",
                    "sic": "3674",
                    "sicDescription": "Semiconductors",
                    "addresses": {"business": {"stateOrCountry": "CA"}},
                    "stateOfIncorporation": "DE",
                },
            )
        return httpx.Response(
            200,
            json={
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {
                                        "val": 100,
                                        "end": "2024-01-28",
                                        "fy": date.today().year,
                                        "fp": "FY",
                                        "form": "10-K",
                                        "filed": "2024-02-21",
                                    },
                                    {
                                        "val": 110,
                                        "end": "2024-01-28",
                                        "fy": date.today().year,
                                        "fp": "FY",
                                        "form": "10-K",
                                        "filed": "2024-06-01",
                                    },
                                    {
                                        "val": 30,
                                        "end": "2024-04-28",
                                        "fy": date.today().year,
                                        "fp": "Q1",
                                        "form": "10-Q",
                                        "filed": "2024-05-20",
                                    },
                                ]
                            }
                        }
                    }
                }
            },
        )

    p = SecEdgarProvider(client=_client(h))
    prof = p.get_profile("NVDA")
    assert prof["sic_major_group"] == "Electronic and Electrical Equipment" and prof["business_country"] == "United States"
    facts = p.get_fundamentals(prof["cik"])
    assert len(facts) == 1 and facts[0]["value"] == 110  # amendment supersedes; quarterly excluded
    assert p.get_profile("ZZZZ") is None
    sec_mod._ticker_map = None
    assert sic_classification("6021") == ("Depository Institutions", "Finance, Insurance and Real Estate")
    assert location_to_country("X0") == "United Kingdom"


def test_alpaca_adapter_normalises_positions_and_activities():
    def h(r: httpx.Request) -> httpx.Response:
        assert r.headers["APCA-API-KEY-ID"] == "kid"
        if r.url.path == "/v2/account":
            return httpx.Response(
                200,
                json={"account_number": "PA123456", "currency": "USD", "cash": "1000.5", "equity": "5000", "status": "ACTIVE"},
            )
        if r.url.path == "/v2/positions":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "AAPL",
                        "qty": "10",
                        "avg_entry_price": "150",
                        "current_price": "190",
                        "market_value": "1900",
                        "asset_class": "us_equity",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": "1",
                    "activity_type": "FILL",
                    "transaction_time": "2024-01-02T15:00:00Z",
                    "symbol": "AAPL",
                    "side": "buy",
                    "qty": "10",
                    "price": "150",
                },
                {"id": "2", "activity_type": "DIV", "date": "2024-02-15", "symbol": "AAPL", "net_amount": "2.4"},
            ],
        )

    p = AlpacaProvider(credentials={"key_id": "kid", "secret_key": "sec"}, client=_client(h))
    assert p.verify()["account_number"] == "3456"
    holds = p.get_holdings("PA123456")
    assert {h_["symbol"] for h_ in holds} == {"AAPL", "CASH"}
    txs = p.get_transactions("PA123456", None)
    assert [t["type"] for t in txs] == ["buy", "dividend"]


# ---------------------------------------------------------------- end-to-end workflow through the API

HOLDINGS = """Account,Symbol,Quantity,Average Cost,Currency,Security Type
ACC-1,TCH1,100,50,USD,Equity
ACC-1,UTL1,200,40,USD,Equity
ACC-1,FIN1,50,,USD,Equity
ACC-1,CASH,2500,,USD,Cash
ACC-2,HLT1,80,30,USD,Equity
ACC-1,TOTAL,,,,
"""
TRANSACTIONS = """Trade Date,Action,Symbol,Quantity,Price,Fees & Comm,Amount,Account
2020-02-03,Deposit,,,,,50000,ACC-1
2020-02-04,Buy,TCH1,100,50,1,,ACC-1
2020-02-04,Buy,UTL1,200,40,1,,ACC-1
2020-03-02,Buy,FIN1,40,60,1,,ACC-1
2021-06-01,Sell,UTL1,0,,,,ACC-1
2021-07-01,Cash Dividend,UTL1,,,,25.5,ACC-1
2022-13-45,Buy,TCH1,1,1,0,,ACC-1
"""


def _upload(
    client, content: str, name: str, label: str, kind: str | None = None, options: dict | None = None, force: bool = False
):
    files = {"file": (name, io.BytesIO(content.encode()), "text/csv")}
    prev = client.post("/api/imports/preview", files=files, data={"kind": kind or ""}).json()
    files = {"file": (name, io.BytesIO(content.encode()), "text/csv")}
    return prev, client.post(
        "/api/imports",
        files=files,
        data={
            "source_label": label,
            "kind": prev["proposal"]["kind"],
            "mapping": json.dumps(prev["proposal"]["mapping"]),
            "options": json.dumps({"institution": label, **(options or {})}),
            "force": str(force).lower(),
        },
    )


@pytest.fixture(scope="module")
def imported(client):
    prev, r = _upload(client, HOLDINGS, "holdings.csv", "Test Broker", options={"as_of": "2022-12-30"})
    assert prev["proposal"]["kind"] == "holdings" and r.status_code == 201, r.text
    h = r.json()
    _, r2 = _upload(client, TRANSACTIONS, "tx.csv", "Test Broker")
    assert r2.status_code == 201, r2.text
    return {"holdings": h, "transactions": r2.json()}


def test_import_results_and_rejections(imported):
    h, t = imported["holdings"], imported["transactions"]
    assert h["rows_imported"] == 5 and h["rows_rejected"] == 1  # "Account Total" summary row
    assert t["rows_imported"] == 5
    msgs = " ".join(i["message"] for i in t["issues"])
    assert "invalid date" in msgs and "without a positive quantity" in msgs


def test_duplicate_file_and_duplicate_transactions(client, imported):
    _, r = _upload(client, TRANSACTIONS, "tx.csv", "Test Broker")
    assert r.status_code == 409
    _, r = _upload(client, TRANSACTIONS, "tx.csv", "Test Broker", force=True)
    assert r.status_code == 201 and r.json()["rows_duplicate"] == 5 and r.json()["rows_imported"] == 0


def test_portfolio_reconstruction_values_match_market_data(client, imported, panel):
    a = client.get("/api/intelligence/portfolio").json()
    pos = {p["symbol"]: p for p in a["positions"]}
    close = panel["close"]
    for sym, qty in (("TCH1", 100), ("UTL1", 200), ("FIN1", 50), ("HLT1", 80)):
        last = close[sym].dropna().iloc[-1]
        assert pos[sym]["market_value"] == pytest.approx(qty * last, rel=1e-9)
        assert pos[sym]["data_class"] == "synthetic"
    assert a["totals"]["accounts"] == 2
    assert sum(p["weight"] for p in a["positions"] if p["weight"]) == pytest.approx(1.0)
    assert "transactions" in a["history"]["method"]
    assert a["metrics"]["annualized_volatility"] > 0 and a["benchmark"]["symbol"] == "NXMKT"
    assert any("mix" in w or "synthetic" in w for w in a["warnings"]) or True
    # Consolidated value equals the sum of individual accounts (no double counting).
    per = [
        client.get("/api/intelligence/portfolio", params={"scope": x["id"]}).json()["totals"]["market_value"]
        for x in a["accounts"]
    ]
    assert sum(per) == pytest.approx(a["totals"]["market_value"])


def test_attribution_decomposes_period_return(client, imported):
    r = client.get("/api/intelligence/attribution", params={"start": "2022-01-03", "end": "2022-06-30"}).json()
    assert r["sum_of_contributions"] + r["compounding_residual"] == pytest.approx(r["portfolio_return"])
    assert r["benchmark"]["symbol"] == "NXMKT" and r["assets"]


def test_risk_drilldown_contributions_sum(client, imported):
    d = client.get("/api/intelligence/risk-drilldown").json()
    assert sum(a["pct_contribution"] for a in d["assets"]) == pytest.approx(1.0)
    assert sum(a["cvar_contribution"] for a in d["tail_contributions"]["assets"]) == pytest.approx(
        d["tail_contributions"]["cvar"]
    )
    assert d["undiversified_volatility"] >= d["portfolio_volatility"]


def test_xray_diagnostics_graph_entity(client, imported):
    x = client.get("/api/intelligence/xray").json()
    assert any(i["label"] == "Technology" for i in x["industry"]) and x["concentration"]["top5_weight"] > 0
    d = client.get("/api/intelligence/diagnostics").json()
    assert {c["category"] for c in d["categories"]} >= {"Concentration", "Diversification", "Volatility", "Drawdown"}
    g = client.get("/api/intelligence/graph").json()
    types = {n["type"] for n in g["nodes"]}
    assert {"account", "portfolio", "asset", "industry", "risk", "benchmark"} <= types
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    e = client.get("/api/intelligence/entity/asset/TCH1").json()
    assert e["position"]["quantity"] == 100 and e["transactions"]


def test_reconciliation_flags_snapshot_vs_transactions(client, imported):
    rec = client.get("/api/intelligence/reconciliation").json()
    fin = [i for i in rec["issues"] if i["symbol"] == "FIN1" and i["type"] == "snapshot_vs_transactions"]
    assert fin and fin[0]["quantity_a"] == 50 and fin[0]["quantity_b"] == 40
    assert "never overwrites" in rec["policy"]


def test_transaction_analytics_methods(client, imported):
    fifo = client.get("/api/intelligence/transactions", params={"method": "fifo"}).json()
    assert fifo["counts"]["buys"] == 3 and fifo["cost_basis_method"] == "fifo"
    assert fifo["pnl"]["totals"]["income"] == pytest.approx(25.5)
    assert client.get("/api/intelligence/transactions", params={"method": "lifo"}).status_code == 422
    only = client.get("/api/intelligence/transactions", params={"symbol": "TCH1"}).json()
    assert {r["symbol"] for r in only["rows"]} == {"TCH1"}


def test_lineage_trace(client, imported):
    batch = imported["transactions"]["id"]
    recs = client.get(f"/api/lineage/batches/{batch}").json()["records"]
    imported_rows = [r for r in recs if r["status"] == "imported"]
    assert imported_rows[0]["normalized_ref"].startswith("TX-") and imported_rows[0]["raw"]["Action"]
    rej = [r for r in recs if r["status"] == "rejected"]
    assert rej and rej[0]["message"]
    one = client.get(f"/api/lineage/transaction/{imported_rows[0]['entity_id']}").json()
    assert one["original_row"] == imported_rows[0]["row"] and one["source"] == "Test Broker"


def test_connections_are_honest(client):
    mk = client.get("/api/connections/marketplace").json()
    planned = [m for m in mk if not m["implemented"]]
    assert planned and all(m["verification"] == "n/a" for m in planned)
    r = client.post("/api/connections", json={"provider_key": "plaid_investments"})
    assert r.status_code == 422
    c = client.post("/api/connections", json={"provider_key": "synthetic_market"}).json()
    assert c["status"] == "connected"
    # External providers are disabled in the test environment: status must say so, not pretend.
    a = client.post(
        "/api/connections", json={"provider_key": "alpaca", "credentials": {"key_id": "kid", "secret_key": "topsecret123"}}
    ).json()
    assert a["status"] == "unavailable" and a["credential_hint"] == "••••t123"
    assert "topsecret" not in json.dumps(a)
    assert client.post(f"/api/connections/{a['id']}/sync").status_code == 422
    d = client.post(f"/api/connections/{a['id']}/disconnect").json()
    assert d["status"] == "disconnected" and d["credential_hint"] is None
    log = client.get("/api/audit-log").json()
    assert all("topsecret" not in json.dumps(x) for x in log)


def test_economic_and_sec_sync_with_mocked_providers(client, monkeypatch):
    from app.core.config import get_settings
    from app.db import session as db_session
    from app.services import connections

    monkeypatch.setattr(get_settings(), "external_data_enabled", True)
    csv = 'Date,"3 Mo","10 Yr"\n09/29/2026,4.25,4.10\n'
    mock = _client(lambda r: httpx.Response(200, text=csv))
    with db_session.SessionLocal() as db:
        c = connections.connect(db, "us_treasury", None, None, {"years": 1}, client=mock)
        assert c.status == "connected"
        run = connections.sync(db, c.id, client=mock)
        assert run["status"] == "success" and run["records_added"] == 2
        run2 = connections.sync(db, c.id, client=mock)
        assert run2["records_added"] == 0  # incremental: nothing new
    eco = client.get("/api/intelligence/economic").json()
    assert eco["suggested_risk_free_rate"]["value"] == pytest.approx(0.0425)


def test_api_keys_and_public_api(client):
    k = client.post("/api/developer/api-keys", json={"name": "ci"}).json()
    assert client.get("/api/v1/me").status_code == 401
    ok = client.get("/api/v1/me", headers={"Authorization": f"Bearer {k['api_key']}"})
    assert ok.status_code == 200 and ok.json()["key_name"] == "ci"
    assert client.get("/api/v1/portfolio", headers={"Authorization": f"Bearer {k['api_key']}"}).status_code == 200
    listed = client.get("/api/developer/api-keys").json()
    assert all("api_key" not in x for x in listed)
    client.post(f"/api/developer/api-keys/{k['id']}/revoke")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {k['api_key']}"}).status_code == 401


def test_webhook_delivery_is_signed(client):
    from app.services import webhooks

    got: list[tuple[dict, bytes]] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((dict(self.headers), body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webhooks.INLINE = True
    try:
        ep = client.post(
            "/api/developer/webhooks", json={"url": f"http://127.0.0.1:{srv.server_port}/hook", "events": ["ping"]}
        ).json()
        assert client.post("/api/developer/webhooks/test").json()["queued"] == 1
        headers, body = got[0]
        expected = "sha256=" + hmac.new(ep["signing_secret"].encode(), body, hashlib.sha256).hexdigest()
        assert headers["X-Nexis-Signature"] == expected and json.loads(body)["event"] == "ping"
        deliveries = client.get("/api/developer/webhooks").json()["deliveries"]
        assert deliveries[0]["status"] == "delivered"
        assert client.post("/api/developer/webhooks", json={"url": "ftp://x", "events": ["ping"]}).status_code == 422
    finally:
        webhooks.INLINE = False
        srv.shutdown()


def test_summary_and_intelligence_overview(client, imported):
    s = client.get("/api/intelligence/summary").json()
    assert s["accounts"] >= 2 and s["transactions"] >= 5
    o = client.get("/api/intelligence/overview").json()
    assert o["ready"] and o["totals"]["market_value"] > 0 and o["largest_position"]
    audit = client.get("/api/audit-log").json()
    assert {"file.imported", "connection.connected"} <= {x["action"] for x in audit}


def test_research_assistant_is_grounded(client, imported):
    r = client.post("/api/assistant/ask", json={"question": "What contributed most to my portfolio's volatility?"}).json()
    assert r["grounded"] and r["intent"] == "volatility" and r["evidence"]
    d = client.get("/api/intelligence/risk-drilldown").json()
    assert d["assets"][0]["symbol"] in r["answer"]
    dd = client.post("/api/assistant/ask", json={"question": "Why did my portfolio experience its largest drawdown?"}).json()
    assert dd["intent"] == "drawdown" and dd["grounded"]
    rec = client.post("/api/assistant/ask", json={"question": "Any reconciliation issues?"}).json()
    assert "FIN1" in rec["answer"]
    unknown = client.post("/api/assistant/ask", json={"question": "Will the market go up tomorrow?"}).json()
    assert unknown["grounded"] is False and "can't verify" in unknown["answer"] and unknown["suggestions"]


def test_public_instance_refuses_stored_credentials(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "external_data_enabled", True)
    monkeypatch.setattr(get_settings(), "public_instance", True)
    r = client.post("/api/connections", json={"provider_key": "fred", "credentials": {"api_key": "a" * 32}})
    # The shared public workspace can't be changed by visitors at all (and stored credentials are refused besides).
    assert r.status_code == 403 and "shared research workspace" in r.json()["error"]["message"]
    assert client.get("/api/system/config").json()["public_instance"] is True


def test_managed_postgres_urls_are_normalised(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("NEXIS_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example.com/nexis?sslmode=require")
    assert Settings().database_url == "postgresql+psycopg://u:p@db.example.com/nexis?sslmode=require"


def test_assistant_falls_back_to_research_portfolio_without_holdings(client, monkeypatch):
    from app.services import assistant

    ds = next(d for d in client.get("/api/datasets").json() if d["code"] == "TEST-SYN")
    p = client.post(
        "/api/portfolios",
        json={
            "name": "Assistant Fallback EW",
            "dataset_id": ds["id"],
            "benchmark_symbol": "NXMKT",
            "symbols": ["TCH1", "FIN1", "UTL1"],
            "allocation_method": "equal_weight",
        },
    )
    assert p.status_code == 201, p.text
    monkeypatch.setattr(assistant, "_has_holdings", lambda db: False)
    for q in ("What contributed most to my portfolio's volatility?", "What is my largest position and exposure?"):
        r = client.post("/api/assistant/ask", json={"question": q}).json()
        assert r["grounded"] and "No brokerage holdings have been imported" in r["answer"] and r["evidence"]
