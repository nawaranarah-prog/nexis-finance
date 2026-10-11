"""The source registry: every provider sits behind it, unofficial ones are labelled, and unknown names fail loudly."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.markets import sources, tradingview
from app.markets.yahoo import YahooClient
from app.services import uae


def test_defaults_are_the_existing_providers_unchanged() -> None:
    assert isinstance(sources.price_source(), YahooClient)
    assert sources.uae_exchange() is tradingview and uae.tv is tradingview


def test_registry_labels_unofficial_sources_and_their_risk(client) -> None:  # type: ignore[no-untyped-def]
    rows = {s["key"]: s for s in client.get("/api/system/sources").json()["sources"]}
    assert (rows["yahoo"]["official"], rows["yahoo"]["terms_risk"], rows["yahoo"]["active"]) == (False, "high", True)
    assert (rows["tradingview"]["official"], rows["tradingview"]["terms_risk"]) == (False, "high")
    assert rows["wikidata"]["official"] and rows["wikidata"]["licence"] == "CC0"
    assert all(s["code"].startswith("app/") for s in rows.values())


def test_an_unknown_source_is_refused(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "price_source", "made_up")
    with pytest.raises(ValueError, match="unknown price source"):
        sources.price_source()
