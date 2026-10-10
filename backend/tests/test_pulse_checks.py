"""Pulse contradiction checks: editorial text about the direction of rates vs Nexis's own rates data."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.db.base import utcnow
from app.models import MarketCache, ModerationAction, PulseDiscussion
from app.services import markets, pulse, pulse_checks

# Real wording from the two stories that were wrong, and from two that are fine.
FAB = "Falling rates are a headwind for FAB's net interest margin. As rates decline, that benefit reverses."
EASING = "Interest rates: how much easing is the market expecting?"
CONDITIONAL = "Because of the dirham peg, Emirates NBD's margins follow US interest rates: rising rates help, falling rates hurt."
UNRELATED = "FAB's appeal is balance-sheet strength and dividends rather than fast growth."


def test_detects_claims_about_the_direction_of_rates() -> None:
    assert pulse_checks.claims_direction(FAB) == {"falling"}
    assert pulse_checks.claims_direction(EASING) == {"falling"}
    assert pulse_checks.claims_direction(CONDITIONAL) == {"falling", "rising"}
    assert pulse_checks.claims_direction(UNRELATED) == set()
    assert pulse_checks.claims_direction("Yields are rising again as rate hikes are coming.") == {"rising"}


def _history(start_close: float, end_close: float):  # type: ignore[no-untyped-def]
    days = [(utcnow() - timedelta(days=200 - i)).date().isoformat() for i in range(200)]
    step = (end_close - start_close) / 199
    return {"bars": [{"t": f"{d}T00:00:00Z", "close": start_close + i * step} for i, d in enumerate(days)]}


@pytest.fixture
def rising(client, monkeypatch):  # type: ignore[no-untyped-def]
    """10-year 4.30% → 5.24% and 13-week bill 3.60% → 4.06% over the window (as in the real data in October 2026)."""
    from app.db import session as db_session

    data = {"^TNX": _history(4.30, 5.24), "^IRX": _history(3.60, 4.06)}
    monkeypatch.setattr(markets, "history", lambda db, s, *a, **k: data[s])
    with db_session.SessionLocal() as db:
        db.execute(delete(MarketCache).where(MarketCache.key == "pulse-check:rates"))
        db.commit()
        yield db


def test_rates_direction_comes_from_both_rates(rising) -> None:  # type: ignore[no-untyped-def]
    t = pulse_checks.rates_trend(rising)
    assert t["direction"] == "rising" and 80 <= t["us10y_bp"] <= 94 and 35 <= t["us13w_bp"] <= 46


def _story(db, title: str, body: str, kind: str = "editorial") -> PulseDiscussion:  # type: ignore[no-untyped-def]
    now = utcnow()
    d = PulseDiscussion(public_id=pulse.new_public_id(db), kind=kind, title=title, body=body, status="visible",
                        created_at=now, last_activity_at=now, content_updated_at=now)  # fmt: skip
    db.add(d)
    db.commit()
    return d


def test_contradicted_stories_are_held_with_the_numbers_and_can_be_restored(rising) -> None:  # type: ignore[no-untyped-def]
    db = rising
    fab = _story(db, "Falling rates are a headwind for FAB's net interest margin", FAB)
    easing = _story(db, EASING, "Markets are pricing several cuts.")
    fine = _story(db, "Emirates NBD margins follow US rates", CONDITIONAL)
    other = _story(db, "FAB's appeal is balance-sheet strength", UNRELATED)
    member_post = _story(db, "My view: falling rates ahead", "As rates decline, banks suffer.", kind="community")

    held = {h["id"] for h in pulse_checks.review(db)}
    assert held >= {fab.public_id, easing.public_id}
    assert not held & {fine.public_id, other.public_id, member_post.public_id}  # members' opinions are never checked
    for d in (fab, easing, fine, other, member_post):
        db.refresh(d)
    assert (fab.status, easing.status, fine.status, other.status, member_post.status) == (
        "held",
        "held",
        "visible",
        "visible",
        "visible",
    )
    log = db.scalars(select(ModerationAction).where(ModerationAction.target_type == "discussion", ModerationAction.target_id == fab.id,
                                                    ModerationAction.action == "auto_hold").order_by(ModerationAction.id.desc())).first()  # fmt: skip
    assert log.action == "auto_hold" and log.actor_id is None and "10-year +" in log.reason and "rising" in log.reason
    assert pulse_checks.review(db) == []  # already held: nothing more to do


def test_no_data_means_no_action(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    def down(*a, **k):  # type: ignore[no-untyped-def]
        raise markets.ProviderError("no data")

    monkeypatch.setattr(markets, "history", down)
    with db_session.SessionLocal() as db:
        db.execute(delete(MarketCache).where(MarketCache.key == "pulse-check:rates"))
        db.commit()
        d = _story(db, "How much easing is coming?", EASING)
        assert pulse_checks.rates_trend(db) == {"direction": "unknown"} and pulse_checks.review(db) == []
        db.refresh(d)
        assert d.status == "visible"
