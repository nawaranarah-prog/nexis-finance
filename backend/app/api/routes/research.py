"""Asset research, correlation, factors, backtests, ML experiments and the experiment registry."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError
from app.db.session import get_db
from app.ml.features import ANOMALY_FEATURES, REGIME_FEATURES, VOL_FEATURES
from app.ml.volatility import MODEL_NAMES
from app.schemas.requests import (
    AnomalyExperimentRequest,
    BacktestRequest,
    NotesUpdate,
    RegimeExperimentRequest,
    VolatilityExperimentRequest,
    WalkForwardRequest,
)
from app.services import backtests as bt_svc
from app.services import experiments as exp_svc
from app.services import jobs
from app.services import ml as ml_svc
from app.services import research as research_svc
from app.strategies.registry import list_strategies

router = APIRouter(tags=["research"])


# --- Asset research / correlation / factors ---------------------------------------------------


@router.get("/research/assets/{symbol}")
def asset_research(
    symbol: str,
    dataset_id: int,
    benchmark: str | None = None,
    start: date | None = None,
    end: date | None = None,
    window: int = 60,
    frequency: str = "daily",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return research_svc.asset_research(db, dataset_id, symbol.upper(), benchmark, start, end, window, frequency)


@router.get("/research/compare-assets")
def compare_assets(
    dataset_id: int,
    symbols: str = Query(..., description="comma-separated"),
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not 1 <= len(syms) <= 30:
        raise ConfigurationError("select between 1 and 30 symbols")
    return research_svc.asset_comparison(db, dataset_id, syms, start, end)


@router.get("/research/correlation")
def correlation(
    dataset_id: int,
    symbols: str | None = None,
    start: date | None = None,
    end: date | None = None,
    method: str = "pearson",
    threshold: float = Query(default=0.8, ge=0, le=1),
    rolling_window: int = 60,
    pair: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    pr = None
    if pair:
        parts = [p.strip().upper() for p in pair.split(",")]
        if len(parts) != 2:
            raise ConfigurationError("pair must be two comma-separated symbols")
        pr = (parts[0], parts[1])
    return research_svc.correlation(db, dataset_id, syms, start, end, method, threshold, rolling_window, pr)


@router.get("/research/factors")
def factors(
    dataset_id: int,
    portfolio_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    rolling_window: int = Query(default=126, ge=40, le=504),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return research_svc.factor_analytics(db, dataset_id, portfolio_id, start, end, rolling_window)


# --- Strategies & backtests -----------------------------------------------------------------


@router.get("/strategies")
def strategies() -> list[dict[str, Any]]:
    return list_strategies()


@router.post("/backtests", status_code=202)
def create_backtest(req: BacktestRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = req.model_dump(mode="json")
    bt_svc.validate_config(db, cfg)  # fail fast with a 422 before queueing
    job = jobs.submit(db, "backtest", cfg, lambda s, p, prog: bt_svc.execute_backtest(s, p, prog))
    return jobs.serialize(job)


@router.post("/backtests/walk-forward", status_code=202)
def create_walk_forward(req: WalkForwardRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = req.model_dump(mode="json")
    bt_svc.validate_config(db, {**cfg, "mode": "walk_forward"})
    job = jobs.submit(db, "walk_forward", cfg, lambda s, p, prog: bt_svc.execute_walk_forward(s, p, prog))
    return jobs.serialize(job)


@router.get("/backtests")
def list_backtests(limit: int = Query(default=100, le=500), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return bt_svc.list_backtests(db, limit)


@router.get("/backtests/{bt_id}")
def get_backtest(bt_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    bt = bt_svc.get_backtest(db, bt_id)
    return bt_svc.serialize(bt, exp_svc.get_experiment(db, bt.experiment_id))


@router.get("/backtests/{bt_id}/trades")
def backtest_trades(
    bt_id: int,
    symbol: str | None = None,
    limit: int = Query(default=5000, le=50000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return bt_svc.trades(db, bt_id, symbol.upper() if symbol else None, limit, offset)


@router.get("/backtests/{bt_id}/diagnostics/{symbol}")
def backtest_diagnostics(bt_id: int, symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return bt_svc.symbol_diagnostics(db, bt_id, symbol.upper())


# --- Machine learning ------------------------------------------------------------------------


@router.get("/ml/catalog")
def ml_catalog() -> dict[str, Any]:
    return {
        "volatility_features": VOL_FEATURES,
        "volatility_models": MODEL_NAMES,
        "regime_features": REGIME_FEATURES,
        "anomaly_features": ANOMALY_FEATURES,
    }


@router.post("/ml/experiments/volatility", status_code=202)
def ml_volatility(req: VolatilityExperimentRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = req.model_dump(mode="json")
    job = jobs.submit(db, "volatility_forecast", cfg, lambda s, p, prog: ml_svc.run_volatility(s, p, prog))
    return jobs.serialize(job)


@router.post("/ml/experiments/regime", status_code=202)
def ml_regime(req: RegimeExperimentRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = req.model_dump(mode="json")
    job = jobs.submit(db, "regime", cfg, lambda s, p, prog: ml_svc.run_regime(s, p, prog))
    return jobs.serialize(job)


@router.post("/ml/experiments/anomaly", status_code=202)
def ml_anomaly(req: AnomalyExperimentRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = req.model_dump(mode="json")
    job = jobs.submit(db, "anomaly", cfg, lambda s, p, prog: ml_svc.run_anomaly(s, p, prog))
    return jobs.serialize(job)


@router.get("/ml/experiments/{exp_id}/predictions")
def ml_predictions(
    exp_id: int, model: str | None = None, symbol: str | None = None, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    exp_svc.get_experiment(db, exp_id)
    return ml_svc.predictions(db, exp_id, model, symbol.upper() if symbol else None)


@router.get("/ml/experiments/{exp_id}/anomalies")
def ml_anomalies(
    exp_id: int,
    method: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    symbol: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    exp_svc.get_experiment(db, exp_id)
    return ml_svc.anomalies(db, exp_id, method, category, severity, symbol.upper() if symbol else None)


# --- Experiment registry -------------------------------------------------------------------------


@router.get("/experiments")
def list_experiments(
    experiment_type: str | None = None,
    dataset_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(default=200, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return [exp_svc.serialize(e) for e in exp_svc.list_experiments(db, experiment_type, dataset_id, status, q, limit)]


@router.get("/experiments/compare")
def compare_experiments(a: int, b: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return exp_svc.compare(db, a, b)


@router.get("/experiments/{exp_id}")
def get_experiment(exp_id: int, artifacts: bool = True, db: Session = Depends(get_db)) -> dict[str, Any]:
    e = exp_svc.get_experiment(db, exp_id)
    out = exp_svc.serialize(e, include_artifacts=artifacts)
    if e.experiment_type == "backtest" and e.summary:
        out["backtest_id"] = e.summary.get("backtest_id")
    return out


@router.patch("/experiments/{exp_id}")
def update_notes(exp_id: int, req: NotesUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return exp_svc.serialize(exp_svc.update_notes(db, exp_id, req.notes))


@router.delete("/experiments/{exp_id}", status_code=204)
def delete_experiment(exp_id: int, db: Session = Depends(get_db)) -> Response:
    exp_svc.delete_experiment(db, exp_id)
    return Response(status_code=204)


@router.post("/experiments/{exp_id}/reproduce", status_code=202)
def reproduce(exp_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    exp = exp_svc.get_experiment(db, exp_id)
    if exp.status != "completed":
        raise ConfigurationError("only completed experiments can be reproduced")
    job = jobs.submit(
        db, "reproduce", {"experiment_id": exp_id}, lambda s, p, prog: exp_svc.reproduce(s, p["experiment_id"], prog)
    )
    return jobs.serialize(job)
