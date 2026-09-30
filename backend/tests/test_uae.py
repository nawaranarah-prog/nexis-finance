"""UAE market layer: ADX/DFM universe, sectors, bonds priced from yields, routing — TradingView mocked."""

from __future__ import annotations

import pytest

from app.services import uae


def test_bond_price_from_yield_matches_market_quote():
    # UAE 4.857% 2034: market price 95.145 at a 5.636% yield with ~7.75 years to maturity.
    assert uae.price_from_yield(0.05636, 0.04857, 7.75) == pytest.approx(95.15, abs=0.1)
    assert uae.price_from_yield(0.05, 0.05, 5) == pytest.approx(100.0, abs=1e-6)  # priced at par when yield = coupon


def test_bond_description_parsing_and_symbols():
    assert uae._parse_bond("Government of United Arab Emirates 4.857% 02-JUL-2034") == (
        4.857,
        __import__("datetime").date(2034, 7, 2),
    )
    assert uae._parse_bond("Emirates NBD Bank (P.J.S.C) 6.25% PERP") is None
    assert uae.to_symbol("ADX:FAB") == "FAB.AD" and uae.to_symbol("DFM:EMAAR") == "EMAAR.AE"
    assert uae.to_symbol("NASDAQDUBAI:UAE0734USD") == "UAE0734USD.BOND"
    assert uae.is_uae_native("FAB.AD") and uae.is_uae_native("UAE0734USD.BOND") and uae.is_uae_native("DFMGI.AE")
    assert not uae.is_uae_native("EMAAR.AE")


def test_sector_mapping():
    assert uae.sector_of("FAB", "Major Banks") == "Banks"
    assert uae.sector_of("ALDAR", "Real Estate Development") == "Real estate"
    assert uae.sector_of("IHC", "Medical/Nursing Services") == "Holdings & conglomerates"  # holding company override
    assert uae.sector_of("X", "Something new") == "Other"


def _scan_rows():  # type: ignore[no-untyped-def]
    base = dict.fromkeys(uae.tv.STOCK_COLUMNS)
    return [
        {**base, "tv": "ADX:FAB", "description": "First Abu Dhabi Bank", "exchange": "ADX", "close": 19.8, "change": -1.5, "change_abs": -0.3,
         "market_cap_basic": 2.2e11, "industry": "Major Banks", "currency": "AED", "price_earnings_ttm": 10.7, "recommendation_mark": 1.34,
         "recommendation_buy": 12, "recommendation_hold": 4, "recommendation_sell": 0, "recommendation_total": 19, "price_target_average": 21.7},
        {**base, "tv": "ADX:ADCB", "description": "Abu Dhabi Commercial Bank", "exchange": "ADX", "close": 16.76, "change": -1.4, "change_abs": -0.24,
         "market_cap_basic": 1.3e11, "industry": "Major Banks", "currency": "AED"},
        {**base, "tv": "DFM:EMAAR", "description": "Emaar Properties", "exchange": "DFM", "close": 11.58, "change": -0.7, "change_abs": -0.08,
         "market_cap_basic": 1.0e11, "industry": "Real Estate Development", "currency": "AED"},
    ]  # fmt: skip


def test_universe_details_peers_and_search(client, monkeypatch):
    from app.db import session as db_session
    from app.models import MarketCache
    from app.services import markets

    monkeypatch.setattr(uae.tv, "scan", lambda market, columns, tickers=None, limit=600: _scan_rows())
    monkeypatch.setattr(
        markets.YahooClient,
        "search",
        lambda self, q, quotes=10, news=0: {"quotes": [{"symbol": "FAB", "name": "US ETF"}], "news": []},
    )
    with db_session.SessionLocal() as db:
        db.query(MarketCache).filter(
            MarketCache.key.like("uae:%") | MarketCache.key.like("search:%") | MarketCache.key.like("details:%")
        ).delete(synchronize_session=False)
        db.commit()
        assert [r["symbol"] for r in uae.universe(db)] == ["FAB.AD", "ADCB.AD", "EMAAR.AE"]
        d = markets.details(db, "FAB.AD")
        assert d["exchange"] == "Abu Dhabi (ADX)" and d["quote"]["price"] == 19.8 and d["valuation"]["pe_trailing"] == 10.7
        assert d["analysts"]["recommendation"] == "strong_buy" and d["analysts"]["count"] == 19
        assert d["analysts"]["upside_to_mean_target"] == pytest.approx(21.7 / 19.8 - 1)
        assert markets.peers(db, "FAB.AD") == ["ADCB.AD"]
        hits = markets.search(db, "first abu dhabi")
        assert hits[0]["symbol"] == "FAB.AD"  # UAE listings rank ahead of foreign tickers
        q = markets.quotes(db, ["ADCB.AD"])
        assert q[0]["price"] == 16.76 and q[0]["sector"] == "Banks"


def test_bond_history_converts_yields_to_price_and_total_return(monkeypatch):
    bond = {
        "symbol": "UAE0734USD.BOND",
        "tv": "NASDAQDUBAI:UAE0734USD",
        "currency": "USD",
        "coupon": 0.04857,
        "maturity": "2034-07-02",
    }
    monkeypatch.setattr(uae, "bond", lambda db, s: bond)
    bars = [
        {
            "t": f"2026-09-{d:02d}T00:00:00+00:00",
            "open": 5.6,
            "high": 5.6,
            "low": 5.6,
            "close": 5.6,
            "adj_close": 5.6,
            "volume": None,
        }
        for d in (1, 2, 3)
    ]
    monkeypatch.setattr(uae.tv, "history", lambda sym, iv, n: {"info": {}, "bars": bars})
    h = uae.history(None, "UAE0734USD.BOND", "1d", None, None)  # type: ignore[arg-type]
    prices = [b["close"] for b in h["bars"]]
    assert 94 < prices[0] < 97 and h["bars"][0]["yield"] == pytest.approx(0.056)
    # Unchanged yield: the price pulls to par slightly and the total return also earns the coupon.
    assert h["bars"][-1]["adj_close"] > h["bars"][0]["adj_close"]
