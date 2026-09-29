"""Chronological, leakage-aware splitting for time-series research.

There is deliberately **no shuffle option** anywhere in this module. Standard k-fold with
shuffling would mix future observations into training folds and overstate forecast skill.

Purging: when a target looks ``horizon`` bars ahead (e.g. next-10-day volatility), the last
``horizon`` training rows have targets overlapping the following period. They are dropped so the
training labels contain no information from the validation/test window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.core.errors import ConfigurationError, InsufficientDataError


@dataclass(frozen=True)
class SplitMasks:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray
    train_dates: tuple[str, str]
    validation_dates: tuple[str, str] | None
    test_dates: tuple[str, str]
    purged_rows: int


def assert_chronological(dates: pd.Series | pd.DatetimeIndex) -> None:
    d = pd.DatetimeIndex(dates)
    if not d.is_monotonic_increasing:
        raise ConfigurationError("time-series data must be sorted chronologically before splitting (shuffling is not allowed)")


def purged_split(
    dates: pd.Series | pd.DatetimeIndex, train_end: str, validation_end: str | None, horizon: int, min_rows: int = 100
) -> SplitMasks:
    """Masks over rows (possibly many rows per date, e.g. a pooled panel sorted by date).

    Train:      dates ≤ train_end, minus the last ``horizon`` unique dates (purge)
    Validation: train_end < dates ≤ validation_end, minus its last ``horizon`` dates (purge)
    Test:       dates > validation_end (or > train_end when no validation period is given)
    """
    d = pd.DatetimeIndex(dates)
    assert_chronological(d)
    if horizon < 0:
        raise ConfigurationError("horizon must be non-negative")
    uniq = d.unique()
    te = pd.Timestamp(train_end)
    ve = pd.Timestamp(validation_end) if validation_end else None
    if ve is not None and ve <= te:
        raise ConfigurationError("validation_end must be after train_end")

    def purge_cut(end: pd.Timestamp) -> pd.Timestamp | None:
        inside = uniq[uniq <= end]
        if len(inside) <= horizon:
            return None
        return inside[len(inside) - horizon - 1] if horizon > 0 else inside[-1]

    tr_cut = purge_cut(te)
    if tr_cut is None:
        raise InsufficientDataError("training period is shorter than the target horizon")
    train = np.asarray(d <= tr_cut)
    purged = int(np.sum((d > tr_cut) & (d <= te)))
    if ve is not None:
        va_cut = purge_cut(ve)
        validation = np.asarray((d > te) & (d <= va_cut)) if va_cut is not None else np.zeros(len(d), bool)
        purged += int(np.sum((d > (va_cut or te)) & (d <= ve)))
        test = np.asarray(d > ve)
    else:
        validation = np.zeros(len(d), dtype=bool)
        test = np.asarray(d > te)

    for name, m in (("train", train), ("test", test)):
        if m.sum() < min_rows:
            raise InsufficientDataError(f"{name} split has {int(m.sum())} rows; at least {min_rows} required")

    def rng(m: np.ndarray) -> tuple[str, str]:
        x = d[m]
        return (x.min().date().isoformat(), x.max().date().isoformat())

    return SplitMasks(
        train=train,
        validation=validation,
        test=test,
        train_dates=rng(train),
        validation_dates=rng(validation) if validation.any() else None,
        test_dates=rng(test),
        purged_rows=purged,
    )
