"""Keeps Nexis Pulse current: collect events, send personal alerts, refresh stale market analyses.

Called by the daily cron (``/api/social/news/refresh``) and nudged by Pulse page views (``/api/pulse/engine/tick``,
throttled to once per ``pulse_tick_minutes``). Each run refreshes a few assets whose analysis is missing or old,
from the public universe only: the core list of widely followed assets plus assets someone has opened in Pulse.
Private holdings and watchlists never decide what is analysed publicly.

Nexis no longer generates discussions written by fictional investors; this module used to, and that code is gone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import MarketCache, MarketDiscussion
from app.services import events, llm, market_pulse, pulse_connectors

STATE_KEY = "pulse-engine:state"
STALE_AFTER = timedelta(hours=3)


def _state(db: Session) -> dict[str, Any]:
    row = db.get(MarketCache, STATE_KEY)
    return dict(row.payload.get("v", {})) if row else {}


def _save_state(db: Session, **kw: Any) -> None:
    row = db.get(MarketCache, STATE_KEY)
    v = {**(_state(db)), **kw}
    if row is None:
        db.add(MarketCache(key=STATE_KEY, payload={"v": v}, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = {"v": v}, utcnow()
    db.commit()


def status(db: Session) -> dict[str, Any]:
    s = get_settings()
    st = _state(db)
    rows = list(db.execute(select(MarketDiscussion.symbol, MarketDiscussion.computed_at, MarketDiscussion.confidence)))
    return {
        "model_configured": bool(llm.status().get("configured")),
        "ai_calls_last_24h": len(market_pulse._ai_calls_today(db)),
        "ai_daily_limit": s.pulse_ai_daily_limit,
        "analysed_assets": len(rows),
        "with_score": sum(1 for r in rows if r.confidence != "none"),
        "last_run": st.get("last_run"),
        "last_result": st.get("last_result"),
        "last_error": st.get("last_error"),
        "connectors": pulse_connectors.describe(),
    }


def due(db: Session, limit: int) -> list[str]:
    """Public assets with no analysis first, then the oldest analyses past ``STALE_AFTER``."""
    have = {sym: at for sym, at in db.execute(select(MarketDiscussion.symbol, MarketDiscussion.computed_at))}
    missing = [s for s in events.CORE_UNIVERSE if s not in have]
    stale = sorted((at, s) for s, at in have.items() if utcnow() - at >= STALE_AFTER)
    return (missing + [s for _, s in stale])[:limit]


def tick(db: Session, max_assets: int = 2, force: bool = False) -> dict[str, Any]:
    s = get_settings()
    last = _state(db).get("last_run")
    if not force and last and datetime.fromisoformat(last) > utcnow() - timedelta(minutes=s.pulse_tick_minutes):
        return {"skipped": True, "reason": "ran recently", "last_run": last}
    _save_state(db, last_run=utcnow().isoformat())
    result: dict[str, Any] = {"events": events.ingest(db)}
    from app.services import alerts

    result["alerts"] = alerts.dispatch(db)
    refreshed, errors = [], []
    for sym in due(db, max_assets):
        try:
            md = market_pulse.compute(db, sym)
            refreshed.append({"symbol": sym, "score": md.pulse_score, "coverage": md.confidence, "method": md.method})
        except NexisError as exc:
            db.rollback()
            errors.append(f"{sym}: {exc.message}")
    result["refreshed"] = refreshed
    # Nexis editorial discussions follow the refreshed analyses, plus one market-wide theme per run.
    from app.services import pulse_editorial

    editorial = []
    budget = s.pulse_editorial_per_tick
    for job in [*(("asset", r["symbol"]) for r in refreshed), *(("theme", k) for k in pulse_editorial.due_themes(db, 1))][:budget]:
        try:
            out = pulse_editorial.sync_asset(db, job[1]) if job[0] == "asset" else pulse_editorial.sync_theme(db, job[1])
            editorial.append(out)
        except NexisError as exc:
            db.rollback()
            errors.append(f"editorial {job[1]}: {exc.message}")
    result["editorial"] = editorial
    result["digests"] = alerts.summarise_digests(db)
    _save_state(db, last_result=result, last_error="; ".join(errors)[:500] or None)
    return result
