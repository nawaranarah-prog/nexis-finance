"""Portfolios, risk analytics and stress tests."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.risk.stress import SCENARIO_TYPES
from app.schemas.requests import PortfolioCreate, RiskAnalyzeRequest, StressTestRequest
from app.services import auth
from app.services import portfolios as svc
from app.services import stress as stress_svc

router = APIRouter(tags=["portfolios & risk"])


@router.get("/portfolios")
def list_portfolios(dataset_id: int | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [svc.serialize(p) for p in svc.list_portfolios(db, dataset_id)]


@router.post("/portfolios", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def create_portfolio(req: PortfolioCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.serialize(svc.create_portfolio(db, req.model_dump()))


@router.post("/portfolios/preview-allocation", dependencies=[Depends(auth.compute_limit)])
def preview_allocation(req: PortfolioCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Compute weights for an allocation method without saving the portfolio."""
    from app.services.market_data import load_panel

    panel = load_panel(db, req.dataset_id)
    weights, details = svc.build_weights(panel, req.model_dump())
    return {"weights": weights, "details": details}


@router.get("/portfolios/compare")
def compare(a: int, b: int, start: date | None = None, end: date | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.compare(db, a, b, start, end)


@router.get("/portfolios/{pid}")
def get_portfolio(pid: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.serialize(svc.get_portfolio(db, pid))


@router.put("/portfolios/{pid}", dependencies=[Depends(auth.workspace_editor)])
def update_portfolio(pid: int, req: PortfolioCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.serialize(svc.update_portfolio(db, pid, req.model_dump()))


@router.delete("/portfolios/{pid}", status_code=204, dependencies=[Depends(auth.workspace_editor)])
def delete_portfolio(pid: int, db: Session = Depends(get_db)) -> Response:
    svc.delete_portfolio(db, pid)
    return Response(status_code=204)


@router.get("/portfolios/{pid}/analytics")
def analytics(
    pid: int,
    start: date | None = None,
    end: date | None = None,
    risk_free_rate: float | None = Query(default=None, ge=-0.05, le=0.25),
    rolling_window: int = Query(default=63, ge=5, le=504),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return svc.analytics(db, pid, start, end, risk_free_rate, rolling_window)


@router.post("/risk/analyze", dependencies=[Depends(auth.compute_limit)])
def risk_analyze(req: RiskAnalyzeRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return svc.risk_analysis(db, req.portfolio_id, req.confidences, req.lookback_days, req.horizon_days, req.backtest_window)


@router.get("/risk/history/{pid}")
def risk_history(pid: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    svc.get_portfolio(db, pid)
    return svc.risk_history(db, pid)


@router.get("/stress-tests/presets")
def presets() -> dict[str, Any]:
    return {"presets": stress_svc.PRESETS, "scenario_types": SCENARIO_TYPES}


@router.post("/stress-tests", status_code=201, dependencies=[Depends(auth.workspace_editor)])
def run_stress(req: StressTestRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return stress_svc.run(
        db, req.portfolio_id, req.name, req.scenario_type, req.parameters, req.lookback_days, req.confidence, req.save
    )


@router.get("/stress-tests")
def list_stress(portfolio_id: int | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return stress_svc.list_tests(db, portfolio_id)


@router.get("/stress-tests/{test_id}")
def get_stress(test_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return stress_svc.get_test(db, test_id)
