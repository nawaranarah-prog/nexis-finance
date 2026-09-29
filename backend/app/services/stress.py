"""Stress-test orchestration and persistence."""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError, NotFoundError
from app.models import StressTest
from app.risk.stress import run_scenario
from app.services.portfolios import get_portfolio, simulate

PRESETS: list[dict[str, Any]] = [
    {"name": "Market shock −5%", "scenario_type": "market_shock", "parameters": {"shock": -0.05}},
    {"name": "Market shock −10%", "scenario_type": "market_shock", "parameters": {"shock": -0.10}},
    {"name": "Market shock −20%", "scenario_type": "market_shock", "parameters": {"shock": -0.20}},
    {"name": "Volatility spike ×2", "scenario_type": "volatility_spike", "parameters": {"multiplier": 2.0}},
    {"name": "Correlation increase (50% toward 1)", "scenario_type": "correlation_increase", "parameters": {"intensity": 0.5}},
    {"name": "Worst historical 10-day window", "scenario_type": "historical_worst", "parameters": {"window_days": 10}},
]


def run(
    db: Session,
    portfolio_id: int,
    name: str,
    scenario_type: str,
    parameters: dict[str, Any],
    lookback_days: int = 504,
    confidence: float = 0.99,
    save: bool = True,
) -> dict[str, Any]:
    if not 60 <= lookback_days <= 5000:
        raise ConfigurationError("lookback_days must be between 60 and 5000")
    p = get_portfolio(db, portfolio_id)
    panel, sim, bench_r, asset_r = simulate(db, p, None, None)
    asset_r = asset_r.iloc[-lookback_days:]
    bench_r = bench_r.reindex(asset_r.index)
    # Scenario applied to current (drifted) holdings at the latest valuation.
    weights = sim.weights.iloc[-1]
    value = float(sim.values.iloc[-1])
    sectors = {s: panel.assets[s]["sector"] for s in panel.symbols}
    res = run_scenario(scenario_type, parameters, weights, asset_r, bench_r, sectors, value, confidence)
    for a in res["assets"]:
        a["name"] = panel.assets[a["symbol"]]["name"]
        a["sector"] = panel.assets[a["symbol"]]["sector"]
    res.update(
        {
            "portfolio": p.name,
            "as_of": asset_r.index[-1].date().isoformat(),
            "lookback_days": len(asset_r),
            "label": "Hypothetical scenario — not a forecast",
        }
    )
    out = {"name": name, "scenario_type": scenario_type, "parameters": parameters, "results": res}
    if save:
        st = StressTest(
            portfolio_id=p.id,
            name=name[:160],
            scenario_type=scenario_type,
            parameters=parameters,
            portfolio_impact_pct=res.get("portfolio_return"),
            results=_json(res),
        )
        db.add(st)
        db.commit()
        out["id"] = st.id
        out["created_at"] = st.created_at.isoformat()
    return out


def list_tests(db: Session, portfolio_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    q = select(StressTest).order_by(StressTest.created_at.desc()).limit(limit)
    if portfolio_id:
        q = q.where(StressTest.portfolio_id == portfolio_id)
    return [
        {
            "id": s.id,
            "portfolio_id": s.portfolio_id,
            "name": s.name,
            "scenario_type": s.scenario_type,
            "parameters": s.parameters,
            "portfolio_impact_pct": s.portfolio_impact_pct,
            "created_at": s.created_at.isoformat(),
        }
        for s in db.scalars(q)
    ]


def get_test(db: Session, test_id: int) -> dict[str, Any]:
    s = db.get(StressTest, test_id)
    if s is None:
        raise NotFoundError(f"stress test {test_id} not found")
    return {
        "id": s.id,
        "portfolio_id": s.portfolio_id,
        "name": s.name,
        "scenario_type": s.scenario_type,
        "parameters": s.parameters,
        "results": s.results,
        "created_at": s.created_at.isoformat(),
    }


def _json(v: Any) -> Any:
    from app.services.experiments import _jsonable

    if isinstance(v, pd.Series):
        v = v.to_dict()
    return _jsonable(v)
