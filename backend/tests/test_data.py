"""Data pipeline: synthetic generator, validation rules, data-quality scoring, providers."""

from __future__ import annotations

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from app.core.errors import ConfigurationError, ProviderError
from app.data.providers import CSVMarketDataProvider, PublicMarketDataProvider, SyntheticMarketDataProvider
from app.data.quality import assess_quality
from app.data.synthetic import SyntheticConfig, generate_universe
from app.data.validation import validate_bars
from tests.conftest import TEST_CONFIG


def _bars(rows: list[dict]) -> pd.DataFrame:
    base = {"symbol": "AAA", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "adj_close": 10.5, "volume": 1000}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_synthetic_generator_is_reproducible():
    a = generate_universe(SyntheticConfig(seed=99, start="2020-01-01", end="2020-12-31"))
    b = generate_universe(SyntheticConfig(seed=99, start="2020-01-01", end="2020-12-31"))
    c = generate_universe(SyntheticConfig(seed=100, start="2020-01-01", end="2020-12-31"))
    pd.testing.assert_frame_equal(a.bars, b.bars)
    assert not a.bars["close"].equals(c.bars["close"])


def test_synthetic_clean_bars_satisfy_ohlc(universe):
    clean = validate_bars(universe.bars).clean
    assert (clean["low"] <= clean[["open", "close"]].min(axis=1) + 1e-9).all()
    assert (clean["high"] >= clean[["open", "close"]].max(axis=1) - 1e-9).all()
    assert clean.duplicated(["symbol", "date"]).sum() == 0


def test_validation_catches_every_injected_defect(universe):
    rep = validate_bars(universe.bars)
    kinds = {d["type"] for d in universe.manifest["data_defects"]}
    checks = {i["check"] for i in rep.issues}
    assert {"ohlc_inconsistent", "negative_price", "negative_volume", "duplicate", "missing_volume"} <= kinds
    assert {"low_above_high", "non_positive_price", "negative_volume", "duplicate_symbol_date", "missing_volume"} <= checks
    # Bad ticks are consistent bars; they are flagged as potential anomalies, not deleted.
    bad = {(d["symbol"], d["date"]) for d in universe.manifest["data_defects"] if d["type"] == "bad_tick"}
    flagged = {(i["symbol"], i["date"].isoformat()) for i in rep.issues if i["check"] == "large_price_move"}
    assert bad <= flagged
    kept = {(r.symbol, r.date.date().isoformat()) for r in rep.clean.itertuples()}
    assert bad <= kept


def test_validation_rules():
    df = _bars(
        [
            {"date": "2024-01-02"},
            {"date": "2024-01-03", "low": 12.0},  # low > high → rejected
            {"date": "2024-01-04", "close": -1.0},  # negative price → rejected
            {"date": "not-a-date"},  # invalid date → rejected
            {"date": "2024-01-05", "volume": None},  # missing volume → stored with warning
            {"date": "2024-01-05", "close": 10.6},  # duplicate symbol+date → deduplicated
            {"date": "2024-01-08", "close": 12.0},  # close > high → rejected
            {"date": "2099-01-01"},  # future → rejected
            {"date": "2024-01-09", "volume": -5},  # negative volume → rejected
            {"date": "2024-01-10", "close": "abc"},  # non-numeric close → rejected
        ]
    )
    rep = validate_bars(df, today=date(2025, 1, 1))
    assert rep.rejected == 7 and rep.duplicates == 1
    assert len(rep.clean) == 2
    assert rep.clean["date"].is_monotonic_increasing
    checks = [i["check"] for i in rep.issues]
    for c in (
        "low_above_high",
        "non_positive_price",
        "invalid_date",
        "ohlc_out_of_range",
        "future_date",
        "negative_volume",
        "missing_close",
        "duplicate_symbol_date",
        "missing_volume",
    ):
        assert c in checks, c


def test_large_moves_are_flagged_not_removed():
    rng = np.random.default_rng(0)
    closes = list(100 * np.cumprod(1 + rng.normal(0, 0.005, 60)))
    closes[40] = closes[39] * 1.5
    rows = [
        {"date": d, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "adj_close": c}
        for d, c in zip(pd.bdate_range("2024-01-01", periods=60), closes, strict=True)
    ]
    rep = validate_bars(_bars(rows))
    assert len(rep.clean) == 60
    assert any(i["check"] == "large_price_move" and i["action"] == "flagged" for i in rep.issues)


def test_quality_score_components():
    idx = pd.bdate_range("2024-01-01", periods=20)
    bars = pd.DataFrame({"symbol": "A", "date": idx, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 100.0})
    perfect = assess_quality(bars, {"received": 20}, is_synthetic=True)
    assert perfect["overall_score"] == pytest.approx(1.0) and perfect["status"] == "pass"
    gappy = bars.drop(index=[5, 6]).copy()
    other = bars.assign(symbol="B")
    res = assess_quality(pd.concat([gappy, other]), {"received": 40, "low_above_high": 2}, is_synthetic=True)
    assert res["components"]["completeness"] == pytest.approx(1 - 2 / 40)
    assert res["components"]["consistency"] == pytest.approx(1 - 2 / 40)
    assert res["overall_score"] < 1.0


def test_quality_empty_dataset_fails():
    empty = pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    assert assess_quality(empty, {}, True)["status"] == "fail"


def test_synthetic_provider_filters_range():
    p = SyntheticMarketDataProvider(TEST_CONFIG)
    df = p.fetch(["TCH1", "NXMKT"], date(2020, 1, 1), date(2020, 1, 31))
    assert set(df["symbol"]) == {"TCH1", "NXMKT"}
    assert df["date"].min() >= pd.Timestamp("2020-01-01") and df["date"].max() <= pd.Timestamp("2020-01-31")


def test_csv_provider_aliases_and_errors():
    csv = b"Date,Open,High,Low,Close,Adj Close,Volume\n2024-01-02,10,11,9,10.5,10.4,100\n"
    p = CSVMarketDataProvider(csv, default_symbol="xyz")
    df = p.fetch(["XYZ"], None, None)
    assert df.iloc[0]["adj_close"] == 10.4 and df.iloc[0]["symbol"] == "XYZ"
    with pytest.raises(ConfigurationError):
        CSVMarketDataProvider(b"date,close\n2024-01-02,10\n")  # no symbol column and no default


def _yahoo_payload() -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "longName": "Test Corp",
                        "instrumentType": "EQUITY",
                        "currency": "USD",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    "timestamp": [1704205800, 1704292200],
                    "indicators": {
                        "quote": [
                            {"open": [10, 11], "high": [11, 12], "low": [9, 10], "close": [10.5, 11.5], "volume": [100, 200]}
                        ],
                        "adjclose": [{"adjclose": [10.4, 11.4]}],
                    },
                }
            ],
            "error": None,
        }
    }


def test_public_provider_parses_response_with_mock_transport():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=_yahoo_payload()))
    p = PublicMarketDataProvider(client=httpx.Client(transport=transport))
    df = p.fetch(["TEST"], date(2024, 1, 1), date(2024, 1, 5))
    assert list(df["close"]) == [10.5, 11.5] and list(df["adj_close"]) == [10.4, 11.4]
    assert df["date"].iloc[0] == pd.Timestamp("2024-01-02")
    assert p.list_assets(["TEST"])[0].name == "Test Corp"


def test_public_provider_network_failure_is_structured():
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    p = PublicMarketDataProvider(client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(ProviderError, match="network error"):
        p.fetch(["TEST"], None, None)
    p404 = PublicMarketDataProvider(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    with pytest.raises(ProviderError, match="not found"):
        p404.fetch(["NOPE"], None, None)
