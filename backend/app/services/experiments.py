"""Experiment registry, comparison and reproducibility."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date
from threading import Lock
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConfigurationError, NotFoundError
from app.db.base import utcnow
from app.models import Dataset, Experiment, ExperimentMetric

PREFIX = {"backtest": "BT", "walk_forward": "WF", "volatility_forecast": "VOL", "regime": "REG", "anomaly": "ANOM"}
EXPERIMENT_TYPES = tuple(PREFIX)
_code_lock = Lock()

# Runner registry: experiment_type -> fn(db, config, progress, parent_id) -> dict with "experiment_id".
Runner = Callable[..., dict[str, Any]]
RUNNERS: dict[str, Runner] = {}


def register_runner(experiment_type: str, fn: Runner) -> None:
    RUNNERS[experiment_type] = fn


def _next_code(db: Session, experiment_type: str) -> str:
    prefix = PREFIX[experiment_type]
    year = utcnow().year
    pattern = f"{prefix}-{year}-%"
    existing = db.scalars(select(Experiment.code).where(Experiment.code.like(pattern))).all()
    seq = max((int(c.rsplit("-", 1)[1]) for c in existing), default=0) + 1
    return f"{prefix}-{year}-{seq:03d}"


def start_experiment(
    db: Session,
    experiment_type: str,
    name: str,
    dataset: Dataset,
    config: dict[str, Any],
    seed: int | None = None,
    model: str | None = None,
    features: list[str] | None = None,
    train: tuple[Any, Any] | None = None,
    test: tuple[Any, Any] | None = None,
    notes: str | None = None,
    parent_id: int | None = None,
) -> Experiment:
    if experiment_type not in PREFIX:
        raise ConfigurationError(f"unknown experiment type {experiment_type}")
    with _code_lock:
        exp = Experiment(
            code=_next_code(db, experiment_type),
            experiment_type=experiment_type,
            name=name[:200],
            dataset_id=dataset.id,
            dataset_version=dataset.version_label,
            dataset_hash=dataset.content_hash,
            config=_jsonable(config),
            seed=seed,
            model=model,
            features=features,
            notes=notes,
            parent_id=parent_id,
            status="running",
            train_start=_d(train[0]) if train else None,
            train_end=_d(train[1]) if train else None,
            test_start=_d(test[0]) if test else None,
            test_end=_d(test[1]) if test else None,
        )
        db.add(exp)
        db.commit()
    return exp


def complete_experiment(
    db: Session,
    exp: Experiment,
    metric_rows: list[tuple[str, str | None, str, Any]],
    summary: dict[str, Any],
    artifacts: dict[str, Any] | None,
    duration: float,
    model: str | None = None,
) -> None:
    for split, model_name, metric, value in metric_rows:
        v = _num(value)
        exp.metrics.append(ExperimentMetric(split=split, model=model_name, metric=metric, value=v))
    exp.summary = _jsonable(summary)
    exp.artifacts = _jsonable(artifacts) if artifacts is not None else None
    exp.duration_seconds = duration
    exp.status = "completed"
    if model:
        exp.model = model
    db.commit()


def fail_experiment(db: Session, exp_id: int, message: str) -> None:
    exp = db.get(Experiment, exp_id)
    if exp:
        exp.status = "failed"
        exp.error = message
        db.commit()


def metric_rows_from(split: str, metrics: dict[str, Any], model: str | None = None) -> list[tuple[str, str | None, str, Any]]:
    return [(split, model, k, v) for k, v in metrics.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]


def get_experiment(db: Session, exp_id: int) -> Experiment:
    exp = db.get(Experiment, exp_id)
    if exp is None:
        raise NotFoundError(f"experiment {exp_id} not found")
    return exp


def list_experiments(
    db: Session,
    experiment_type: str | None = None,
    dataset_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[Experiment]:
    stmt = select(Experiment).order_by(Experiment.created_at.desc(), Experiment.id.desc()).limit(min(limit, 500))
    if experiment_type:
        stmt = stmt.where(Experiment.experiment_type == experiment_type)
    if dataset_id:
        stmt = stmt.where(Experiment.dataset_id == dataset_id)
    if status:
        stmt = stmt.where(Experiment.status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Experiment.code.ilike(like),
                Experiment.name.ilike(like),
                Experiment.notes.ilike(like),
                Experiment.model.ilike(like),
            )
        )
    return list(db.scalars(stmt.options(selectinload(Experiment.metrics))))


def update_notes(db: Session, exp_id: int, notes: str | None) -> Experiment:
    exp = get_experiment(db, exp_id)
    exp.notes = notes
    db.commit()
    return exp


def delete_experiment(db: Session, exp_id: int) -> None:
    db.delete(get_experiment(db, exp_id))
    db.commit()


def counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(Experiment.experiment_type, func.count()).group_by(Experiment.experiment_type)).all()
    return {t: int(n) for t, n in rows}


def serialize(exp: Experiment, include_artifacts: bool = False) -> dict[str, Any]:
    out = {
        "id": exp.id,
        "code": exp.code,
        "experiment_type": exp.experiment_type,
        "name": exp.name,
        "dataset_id": exp.dataset_id,
        "dataset_version": exp.dataset_version,
        "dataset_hash": exp.dataset_hash,
        "config": exp.config,
        "seed": exp.seed,
        "model": exp.model,
        "features": exp.features,
        "train_start": _iso(exp.train_start),
        "train_end": _iso(exp.train_end),
        "test_start": _iso(exp.test_start),
        "test_end": _iso(exp.test_end),
        "status": exp.status,
        "duration_seconds": exp.duration_seconds,
        "error": exp.error,
        "notes": exp.notes,
        "parent_id": exp.parent_id,
        "reproducibility": exp.reproducibility,
        "summary": exp.summary,
        "created_at": exp.created_at.isoformat(),
        "metrics": [{"split": m.split, "model": m.model, "metric": m.metric, "value": m.value} for m in exp.metrics],
    }
    if include_artifacts:
        out["artifacts"] = exp.artifacts
    return out


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    else:
        out[prefix.rstrip(".")] = d
    return out


def compare(db: Session, a_id: int, b_id: int) -> dict[str, Any]:
    a, b = get_experiment(db, a_id), get_experiment(db, b_id)
    ma = {(m.split, m.model or "", m.metric): m.value for m in a.metrics}
    mb = {(m.split, m.model or "", m.metric): m.value for m in b.metrics}
    keys = sorted(set(ma) | set(mb))
    rows = [
        {
            "split": k[0],
            "model": k[1] or None,
            "metric": k[2],
            "a": ma.get(k),
            "b": mb.get(k),
            "difference": (mb[k] - ma[k]) if (k in ma and k in mb and ma[k] is not None and mb[k] is not None) else None,
        }
        for k in keys
    ]
    fa, fb = _flatten(a.config), _flatten(b.config)
    diffs = [{"key": k, "a": fa.get(k), "b": fb.get(k)} for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]
    return {
        "a": serialize(a),
        "b": serialize(b),
        "same_type": a.experiment_type == b.experiment_type,
        "same_dataset": a.dataset_hash == b.dataset_hash,
        "metrics": rows,
        "config_differences": diffs,
        "note": "Numerical comparison of recorded research outputs; not a ranking or recommendation.",
    }


def reproducibility_check(original: Experiment, new: Experiment, tolerance: float = 1e-9) -> dict[str, Any]:
    mo = {(m.split, m.model or "", m.metric): m.value for m in original.metrics}
    mn = {(m.split, m.model or "", m.metric): m.value for m in new.metrics}
    common = [k for k in mo if k in mn]
    diffs = []
    for k in common:
        va, vb = mo[k], mn[k]
        if va is None and vb is None:
            continue
        if va is None or vb is None:
            diffs.append((k, math.inf))
            continue
        diffs.append((k, abs(va - vb)))
    max_diff = max((d for _, d in diffs), default=0.0)
    worst = [
        {"split": k[0], "model": k[1] or None, "metric": k[2], "abs_diff": d}
        for k, d in sorted(diffs, key=lambda x: x[1], reverse=True)[:5]
        if d > tolerance
    ]
    return {
        "original_experiment": original.code,
        "metrics_compared": len(common),
        "missing_metrics": len(set(mo) ^ set(mn)),
        "max_abs_difference": max_diff if math.isfinite(max_diff) else None,
        "tolerance": tolerance,
        "matches": max_diff <= tolerance and len(common) > 0,
        "dataset_unchanged": original.dataset_hash == new.dataset_hash,
        "original_dataset_version": original.dataset_version,
        "current_dataset_version": new.dataset_version,
        "largest_differences": worst,
    }


def reproduce(db: Session, exp_id: int, progress: Callable[..., None] | None = None) -> dict[str, Any]:
    original = get_experiment(db, exp_id)
    if original.status != "completed":
        raise ConfigurationError("only completed experiments can be reproduced")
    runner = RUNNERS.get(original.experiment_type)
    if runner is None:
        raise ConfigurationError(f"no runner registered for {original.experiment_type}")
    result = runner(db, dict(original.config), progress, parent_id=original.id)
    new = get_experiment(db, result["experiment_id"])
    db.refresh(original)
    check = reproducibility_check(original, new)
    new.reproducibility = check
    if not new.notes:
        new.notes = f"Reproduction of {original.code}"
    db.commit()
    return {**result, "reproducibility": check}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _jsonable(v: Any) -> Any:
    import numpy as np
    import pandas as pd

    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (np.floating, float)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp, date)):
        return v.isoformat()[:10]
    return v


def _d(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    import pandas as pd

    return pd.Timestamp(v).date()


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None
