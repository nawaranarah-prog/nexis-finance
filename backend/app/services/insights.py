"""Portfolio investigation workflows built on the reconstructed book.

* X-Ray              — allocation, industry (SEC SIC), headquarters country, currency, concentration, risk contribution
* Why is it moving?  — period return decomposed into per-asset and per-industry contributions
* Risk drill-down    — volatility → Euler contributions → correlation effect → industry risk → tail (CVaR) contributors
* Diagnostics        — transparent measurements per category with documented reference thresholds (no composite score)
"""

from __future__ import annotations

import contextlib
import math
from collections import defaultdict
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import metrics as M
from app.core.errors import ConfigurationError, InsufficientDataError
from app.models import (
    Account,
    CompanyProfile,
    Connection,
    Dataset,
    EconomicSeries,
    Experiment,
    Holding,
    ImportBatch,
    MarketData,
    SyncRun,
    Transaction,
)
from app.services import book as B
from app.services.market_data import load_panel


def xray(db: Session, scope: str = "all", method: str = "fifo") -> dict[str, Any]:
    a = B.analytics(db, scope, method)
    dec = (a.get("risk") or {}).get("decomposition") or {}
    by_sym = {x["symbol"]: x for x in dec.get("assets", [])}
    positions = []
    for p in a["positions"]:
        rc = by_sym.get(p["symbol"], {})
        positions.append(
            {
                **p,
                "pct_volatility_contribution": rc.get("pct_contribution"),
                "standalone_volatility": rc.get("standalone_volatility"),
            }
        )
    tail = {}
    with contextlib.suppress(InsufficientDataError):
        tail = risk_drilldown(db, scope, method).get("tail_contributions", {})
    return {
        "scope": scope,
        "totals": a["totals"],
        "allocation": a["allocation"],
        "industry": a["industry"],
        "country": a["country"],
        "currency": a["currency"],
        "concentration": a["concentration"],
        "positions": positions,
        "cvar_contributions": tail,
        "warnings": a["warnings"],
        "classification_note": (
            "Industry is the issuer's SEC SIC major group (not GICS); country is the SEC business address "
            "(headquarters, not revenue exposure). Holdings without SEC metadata stay unclassified."
        ),
    }


def attribution(db: Session, scope: str, start: str | None, end: str | None, method: str = "fifo") -> dict[str, Any]:
    book = B.build_book(db, scope, method)
    ds = B.daily_series(db, book)
    r, contrib = ds["returns"], ds["contrib"]
    s = pd.Timestamp(start) if start else r.index[max(0, len(r) - 21)]
    e = pd.Timestamp(end) if end else r.index[-1]
    if s >= e:
        raise ConfigurationError("start must be before end")
    rr = r.loc[s:e]
    if len(rr) < 1:
        raise InsufficientDataError("no portfolio returns in the selected period")
    cc = contrib.loc[rr.index].sum()
    port_ret = float((1 + rr).prod() - 1)
    names = {p["symbol"]: p for p in book.positions}
    assets = []
    for sym, v in cc.items():
        if abs(v) < 1e-12:
            continue
        ar = ds["asset_returns"][sym].loc[rr.index]
        assets.append(
            {
                "symbol": sym,
                "name": (names.get(sym) or {}).get("name", sym),
                "industry": (names.get(sym) or {}).get("industry"),
                "contribution": float(v),
                "asset_return": float((1 + ar.fillna(0)).prod() - 1),
                "avg_weight": float(ds["weights"][sym].loc[rr.index].mean()),
            }
        )
    assets.sort(key=lambda x: x["contribution"])
    ind: dict[str, float] = defaultdict(float)
    for x in assets:
        ind[x["industry"] or "Unclassified"] += x["contribution"]
    bsym, bclose = B.benchmark_for(db, book)
    bret = None
    if bclose is not None:
        br = bclose.pct_change(fill_method=None).reindex(rr.index)
        bret = float((1 + br.fillna(0)).prod() - 1)
    summed = float(sum(x["contribution"] for x in assets))
    return {
        "scope": scope,
        "start": rr.index[0].date().isoformat(),
        "end": rr.index[-1].date().isoformat(),
        "days": len(rr),
        "portfolio_return": port_ret,
        "sum_of_contributions": summed,
        "compounding_residual": port_ret - summed,
        "negative_contributors": [x for x in assets if x["contribution"] < 0][:10],
        "positive_contributors": [x for x in reversed(assets) if x["contribution"] > 0][:10],
        "assets": assets,
        "industries": sorted([{"industry": k, "contribution": v} for k, v in ind.items()], key=lambda x: x["contribution"]),
        "benchmark": {"symbol": bsym, "return": bret},
        "active_return": port_ret - bret if bret is not None else None,
        "history_method": ds["method_label"],
        "methodology": (
            "Daily contribution = prior-close position value × price change ÷ prior-close portfolio value; contributions are "
            "summed over the period. The portfolio return compounds daily returns, so the small difference between "
            "the two is shown as the compounding residual. Only price moves of held positions are counted — "
            "trades, deposits and withdrawals are not performance."
        ),
    }


def risk_drilldown(db: Session, scope: str = "all", method: str = "fifo", confidence: float = 0.95) -> dict[str, Any]:
    book = B.build_book(db, scope, method)
    ds = B.daily_series(db, book)
    held = [
        p for p in book.positions if p["symbol"] != "CASH" and p["market_value"] and p["symbol"] in ds["asset_returns"].columns
    ]
    if not held:
        raise InsufficientDataError("no priced security holdings")
    w = pd.Series({p["symbol"]: p["market_value"] for p in held})
    w = w / w.sum()
    ar = ds["asset_returns"][list(w.index)].iloc[-756:].dropna()
    if len(ar) < 60:
        raise InsufficientDataError("fewer than 60 overlapping daily returns for the current holdings")
    cov = ar.cov().to_numpy() * 252
    wv = w.to_numpy()
    sigma = math.sqrt(float(wv @ cov @ wv))
    sd = np.sqrt(np.diag(cov))
    undiversified = float(wv @ sd)
    pct = (wv * (cov @ wv)) / sigma**2 if sigma > 0 else np.zeros_like(wv)
    info = {p["symbol"]: p for p in held}
    assets = sorted(
        [
            {
                "symbol": s,
                "name": info[s]["name"],
                "industry": info[s]["industry"],
                "weight": float(wv[i]),
                "standalone_volatility": float(sd[i]),
                "pct_contribution": float(pct[i]),
                "contribution": float(pct[i] * sigma),
                "risk_to_weight": float(pct[i] / wv[i]) if wv[i] > 0 else None,
            }
            for i, s in enumerate(w.index)
        ],
        key=lambda x: -x["pct_contribution"],
    )
    ind: dict[str, dict[str, float]] = defaultdict(lambda: {"weight": 0.0, "pct_risk": 0.0})
    for x in assets:
        k = x["industry"] or "Unclassified"
        ind[k]["weight"] += x["weight"]
        ind[k]["pct_risk"] += x["pct_contribution"]
    port = ar.to_numpy() @ wv
    q = np.quantile(port, 1 - confidence)
    tail = port <= q
    comp = {s: float(-(wv[i] * ar.iloc[:, i].to_numpy())[tail].mean()) for i, s in enumerate(w.index)}
    cvar = float(-port[tail].mean())
    # Drawdown attribution over the actual reconstructed history.
    r = ds["returns"]
    dd = M.drawdown_series(r)
    trough = dd.idxmin()
    peak = (1 + r).cumprod().loc[:trough].idxmax()
    dd_contrib = ds["contrib"].loc[peak:trough].sum().sort_values()
    avg_corr = float(ar.corr().to_numpy()[~np.eye(len(w), dtype=bool)].mean()) if len(w) > 1 else None
    return {
        "scope": scope,
        "portfolio_volatility": sigma,
        "undiversified_volatility": undiversified,
        "diversification_benefit": undiversified - sigma,
        "average_pairwise_correlation": avg_corr,
        "correlation_effect_note": (
            "Undiversified volatility is the weighted average of standalone volatilities (all correlations = 1). "
            "The gap to actual volatility is the diversification benefit from imperfect correlation."
        ),
        "assets": assets,
        "industries": sorted([{"industry": k, **v} for k, v in ind.items()], key=lambda x: -x["pct_risk"]),
        "tail_contributions": {
            "confidence": confidence,
            "cvar": cvar,
            "observations_in_tail": int(tail.sum()),
            "assets": sorted(
                [{"symbol": s, "cvar_contribution": v, "share": v / cvar if cvar else None} for s, v in comp.items()],
                key=lambda x: -x["cvar_contribution"],
            ),
        },
        "drawdown": {
            "max_drawdown": float(dd.min()),
            "peak": peak.date().isoformat(),
            "trough": trough.date().isoformat(),
            "contributions": [{"symbol": s, "contribution": float(v)} for s, v in dd_contrib.items() if abs(v) > 1e-9],
        },
        "observations": len(ar),
        "window": [ar.index[0].date().isoformat(), ar.index[-1].date().isoformat()],
        "methodology": (
            "Volatility contributions use the Euler decomposition wᵢ(Σw)ᵢ/σ² on the last ≤756 days of daily returns with "
            "current weights. Tail contributions: each holding's average weighted return on the days in the worst "
            f"{(1 - confidence):.0%} of portfolio returns (they sum to historical CVaR)."
        ),
    }


REFERENCE = {
    "max_weight": 0.20,
    "top5_weight": 0.60,
    "effective_n": 10,
    "avg_corr": 0.6,
    "vol_ratio": 1.2,
    "max_drawdown": -0.30,
    "days_to_liquidate": 5,
    "tracking_error": 0.08,
    "currency_non_base": 0.30,
}


def diagnostics(db: Session, scope: str = "all", method: str = "fifo") -> dict[str, Any]:
    a = B.analytics(db, scope, method)
    c = a.get("concentration") or {}
    m = a.get("metrics") or {}
    bm = (a.get("benchmark") or {}).get("summary") or {}
    risk = a.get("risk") or {}
    dec = risk.get("decomposition") or {}
    cats: list[dict[str, Any]] = []

    def flag(value: float | None, ref: float, higher_is_worse: bool = True) -> str | None:
        if value is None:
            return None
        return "above reference" if (value > ref if higher_is_worse else value < ref) else "within reference"

    cats.append(
        {
            "category": "Concentration",
            "measurements": [
                {
                    "name": "Largest position weight",
                    "value": c.get("largest_weight"),
                    "unit": "pct",
                    "detail": c.get("largest_position"),
                    "reference": REFERENCE["max_weight"],
                    "status": flag(c.get("largest_weight"), REFERENCE["max_weight"]),
                },
                {
                    "name": "Top-5 weight",
                    "value": c.get("top5_weight"),
                    "unit": "pct",
                    "reference": REFERENCE["top5_weight"],
                    "status": flag(c.get("top5_weight"), REFERENCE["top5_weight"]),
                },
                {"name": "Herfindahl index", "value": c.get("herfindahl_index"), "unit": "num"},
            ],
        }
    )
    corr = risk.get("correlation") or {}
    cats.append(
        {
            "category": "Diversification",
            "measurements": [
                {
                    "name": "Effective number of holdings",
                    "value": c.get("effective_number_of_assets"),
                    "unit": "num",
                    "reference": REFERENCE["effective_n"],
                    "status": flag(c.get("effective_number_of_assets"), REFERENCE["effective_n"], False),
                },
                {"name": "Diversification ratio", "value": dec.get("diversification_ratio"), "unit": "num"},
                {
                    "name": "Average pairwise correlation",
                    "value": corr.get("average_pairwise_correlation"),
                    "unit": "num",
                    "reference": REFERENCE["avg_corr"],
                    "status": flag(corr.get("average_pairwise_correlation"), REFERENCE["avg_corr"]),
                },
            ],
        }
    )
    vol, bvol = m.get("annualized_volatility"), bm.get("annualized_volatility")
    ratio = vol / bvol if vol and bvol else None
    cats.append(
        {
            "category": "Volatility",
            "measurements": [
                {"name": "Annualised volatility", "value": vol, "unit": "pct"},
                {"name": f"Benchmark volatility ({(a.get('benchmark') or {}).get('symbol')})", "value": bvol, "unit": "pct"},
                {
                    "name": "Volatility ratio to benchmark",
                    "value": ratio,
                    "unit": "num",
                    "reference": REFERENCE["vol_ratio"],
                    "status": flag(ratio, REFERENCE["vol_ratio"]),
                },
            ],
        }
    )
    hist = a.get("history") or {}
    cur_dd = (hist.get("drawdown") or [None])[-1] if hist.get("drawdown") else None
    cats.append(
        {
            "category": "Drawdown",
            "measurements": [
                {
                    "name": "Maximum drawdown",
                    "value": m.get("max_drawdown"),
                    "unit": "pct",
                    "reference": REFERENCE["max_drawdown"],
                    "status": flag(m.get("max_drawdown"), REFERENCE["max_drawdown"], False),
                },
                {"name": "Current drawdown", "value": cur_dd, "unit": "pct"},
            ],
        }
    )
    cats.append({"category": "Liquidity proxy", "measurements": _liquidity(db, a)})
    cats.append(
        {
            "category": "Benchmark deviation",
            "measurements": [
                {"name": "Beta", "value": m.get("beta"), "unit": "num"},
                {"name": "Correlation", "value": m.get("correlation"), "unit": "num"},
                {
                    "name": "Tracking error",
                    "value": m.get("tracking_error"),
                    "unit": "pct",
                    "reference": REFERENCE["tracking_error"],
                    "status": flag(m.get("tracking_error"), REFERENCE["tracking_error"]),
                },
            ],
        }
    )
    ind = a.get("industry") or []
    cats.append(
        {
            "category": "Industry concentration",
            "measurements": [
                {
                    "name": "Largest industry",
                    "value": ind[0]["weight"] if ind else None,
                    "unit": "pct",
                    "detail": ind[0]["label"] if ind else None,
                },
                {
                    "name": "Industries represented",
                    "value": len([i for i in ind if not i["label"].startswith("Unclassified")]),
                    "unit": "int",
                },
            ],
        }
    )
    ccy = a.get("currency") or []
    non_usd = sum(x["weight"] or 0 for x in ccy if x["label"] != "USD")
    cats.append(
        {
            "category": "Currency concentration",
            "measurements": [
                {
                    "name": "Non-USD share",
                    "value": non_usd,
                    "unit": "pct",
                    "reference": REFERENCE["currency_non_base"],
                    "status": flag(non_usd, REFERENCE["currency_non_base"]),
                },
                {"name": "Currencies", "value": len(ccy), "unit": "int"},
            ],
        }
    )
    return {
        "scope": scope,
        "categories": cats,
        "reference_thresholds": REFERENCE,
        "warnings": a["warnings"],
        "note": (
            "Measurements are computed from the reconstructed holdings. Reference thresholds are fixed, documented "
            "conventions for highlighting — not recommendations — and no composite score is calculated."
        ),
    }


def _liquidity(db: Session, a: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in a["positions"]:
        if p["symbol"] == "CASH" or not p["market_value"]:
            continue
        pi = B.latest_prices(db, [p["symbol"]]).get(p["symbol"])
        if not pi:
            continue
        panel = load_panel(db, pi.dataset_id)
        if p["symbol"] not in panel.volume.columns:
            continue
        adv = float((panel.volume[p["symbol"]] * panel.close[p["symbol"]]).iloc[-63:].mean())
        if adv > 0 and math.isfinite(adv):
            rows.append((p["symbol"], p["market_value"] / (0.10 * adv)))
    if not rows:
        return [
            {"name": "Days to liquidate at 10% of ADV", "value": None, "unit": "num", "detail": "no volume data for holdings"}
        ]
    worst = max(rows, key=lambda x: x[1])
    return [
        {
            "name": "Max days to liquidate at 10% of 63-day ADV",
            "value": worst[1],
            "unit": "num",
            "detail": worst[0],
            "reference": REFERENCE["days_to_liquidate"],
            "status": "above reference" if worst[1] > REFERENCE["days_to_liquidate"] else "within reference",
        },
        {"name": "Holdings with volume data", "value": len(rows), "unit": "int"},
    ]


def data_summary(db: Session) -> dict[str, Any]:
    """'Your Financial Data' counters — each straight from the database."""
    last_sync = db.scalar(select(func.max(SyncRun.finished_at)))
    last_import = db.scalar(select(func.max(ImportBatch.created_at)))
    last = max([d for d in (last_sync, last_import) if d], default=None)
    return {
        "connected_sources": int(
            db.scalar(select(func.count()).select_from(Connection).where(Connection.status == "connected")) or 0
        ),
        "connections_total": int(db.scalar(select(func.count()).select_from(Connection)) or 0),
        "accounts": int(db.scalar(select(func.count()).select_from(Account)) or 0),
        "assets_held": int(db.scalar(select(func.count(func.distinct(Holding.symbol)))) or 0),
        "transactions": int(db.scalar(select(func.count()).select_from(Transaction)) or 0),
        "import_batches": int(db.scalar(select(func.count()).select_from(ImportBatch)) or 0),
        "market_data_bars": int(db.scalar(select(func.count()).select_from(MarketData)) or 0),
        "datasets": int(db.scalar(select(func.count()).select_from(Dataset)) or 0),
        "economic_series": int(db.scalar(select(func.count()).select_from(EconomicSeries)) or 0),
        "company_profiles": int(db.scalar(select(func.count()).select_from(CompanyProfile)) or 0),
        "last_sync": last.isoformat() if last else None,
    }


def financial_intelligence(db: Session, scope: str = "all", method: str = "fifo") -> dict[str, Any]:
    """The onboarding summary. Every value is computed; missing inputs produce explicit gaps."""
    summary = data_summary(db)
    if summary["accounts"] == 0:
        return {"ready": False, "data": summary, "steps": _onboarding_steps(db, summary)}
    a = B.analytics(db, scope, method)
    m = a.get("metrics") or {}
    dec = (a.get("risk") or {}).get("decomposition") or {}
    top_risk = max(dec.get("assets", []), key=lambda x: x["pct_contribution"], default=None)
    ind = [i for i in a["industry"] if not i["label"].startswith("Unclassified")]
    regime = _regime_for(db, a)
    return {
        "ready": True,
        "data": summary,
        "scope": scope,
        "totals": a["totals"],
        "largest_exposure": ind[0] if ind else None,
        "largest_position": (a.get("concentration") or {}).get("largest_position"),
        "largest_position_weight": (a.get("concentration") or {}).get("largest_weight"),
        "annualized_volatility": m.get("annualized_volatility"),
        "max_drawdown": m.get("max_drawdown"),
        "sharpe_ratio": m.get("sharpe_ratio"),
        "beta": m.get("beta"),
        "correlation": m.get("correlation"),
        "benchmark": (a.get("benchmark") or {}).get("symbol"),
        "var_95": ((a.get("risk") or {}).get("95") or {}).get("historical", {}).get("var"),
        "top_risk_contributor": top_risk,
        "history_method": (a.get("history") or {}).get("method_label"),
        "regime": regime,
        "warnings": a["warnings"],
        "steps": _onboarding_steps(db, summary),
    }


def _regime_for(db: Session, a: dict[str, Any]) -> dict[str, Any] | None:
    real = any(p["data_class"] == "real_external" for p in a["positions"])
    exps = db.scalars(
        select(Experiment)
        .where(Experiment.experiment_type == "regime", Experiment.status == "completed")
        .order_by(Experiment.created_at.desc())
    ).all()
    for e in exps:
        ds = db.get(Dataset, e.dataset_id)
        if ds and (not ds.is_synthetic) == real and e.summary:
            return {
                "experiment_id": e.id,
                "code": e.code,
                "dataset": ds.code,
                **(e.summary.get("latest") or {}),
                "note": "Descriptive cluster label from an unsupervised model; not a forecast.",
            }
    return None


def _onboarding_steps(db: Session, s: dict[str, Any]) -> list[dict[str, Any]]:
    market = (
        db.scalar(
            select(func.count()).select_from(Connection).where(Connection.category == "markets", Connection.status == "connected")
        )
        or 0
    )
    return [
        {"step": "Connect a market-data source", "done": bool(market), "link": "/connections"},
        {"step": "Import or connect holdings / transactions", "done": s["accounts"] > 0, "link": "/connections"},
        {
            "step": "Sync market data for your holdings",
            "done": s["market_data_bars"] > 0 and s["accounts"] > 0,
            "link": "/connections",
        },
        {"step": "Add SEC company metadata (industry, headquarters)", "done": s["company_profiles"] > 0, "link": "/connections"},
    ]


def economic_overview(db: Session) -> dict[str, Any]:
    from app.models import EconomicObservation

    series = db.scalars(select(EconomicSeries).order_by(EconomicSeries.source, EconomicSeries.series_code)).all()
    out = []
    for s in series:
        obs = db.execute(
            select(EconomicObservation.date, EconomicObservation.value)
            .where(EconomicObservation.series_id == s.id)
            .order_by(EconomicObservation.date)
        ).all()
        out.append(
            {
                "id": s.id,
                "source": s.source,
                "code": s.series_code,
                "title": s.title,
                "units": s.units,
                "frequency": s.frequency,
                "country": s.country,
                "last_retrieved": s.last_retrieved.isoformat() if s.last_retrieved else None,
                "coverage": [
                    s.coverage_start.isoformat() if s.coverage_start else None,
                    s.coverage_end.isoformat() if s.coverage_end else None,
                ],
                "dates": [d.isoformat() for d, _ in obs],
                "values": [v for _, v in obs],
                "latest": {"date": obs[-1][0].isoformat(), "value": obs[-1][1]} if obs else None,
            }
        )
    curve = None
    tenors = ["UST_1M", "UST_3M", "UST_6M", "UST_1Y", "UST_2Y", "UST_5Y", "UST_10Y", "UST_20Y", "UST_30Y"]
    ust = {x["code"]: x for x in out if x["source"] == "us_treasury"}
    if ust:
        latest_date = max(x["latest"]["date"] for x in ust.values() if x["latest"])
        curve = {
            "date": latest_date,
            "tenors": [t for t in tenors if t in ust],
            "yields": [dict(zip(ust[t]["dates"], ust[t]["values"], strict=True)).get(latest_date) for t in tenors if t in ust],
        }
    rf = ust.get("UST_3M", {}).get("latest")
    return {
        "series": out,
        "yield_curve": curve,
        "suggested_risk_free_rate": {"value": rf["value"] / 100, "date": rf["date"], "source": "US Treasury 3-month par yield"}
        if rf
        else None,
    }


def fundamentals(db: Session, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    from app.models import Fundamental

    q = select(CompanyProfile).order_by(CompanyProfile.symbol)
    if symbols:
        q = q.where(CompanyProfile.symbol.in_(symbols))
    out = []
    for p in db.scalars(q):
        facts = db.scalars(select(Fundamental).where(Fundamental.profile_id == p.id).order_by(Fundamental.period_end)).all()
        latest: dict[str, Any] = {}
        for f in facts:
            if f.fp == "FY":
                latest[f.label] = {"value": f.value, "unit": f.unit, "period_end": f.period_end.isoformat(), "form": f.form}
        out.append(
            {
                "symbol": p.symbol,
                "cik": p.cik,
                "name": p.name,
                "sic": p.sic,
                "sic_description": p.sic_description,
                "sic_major_group": p.sic_major_group,
                "sic_division": p.sic_division,
                "business_country": p.business_country,
                "business_state_or_country": p.business_state_or_country,
                "state_of_incorporation": p.state_of_incorporation,
                "exchanges": p.exchanges,
                "fetched_at": p.fetched_at.isoformat(),
                "latest_annual": latest,
                "facts": len(facts),
                "edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={p.cik}",
            }
        )
    return out


def _today() -> date:
    return date.today()
