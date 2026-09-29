"""Reproducible synthetic market-data generator (DEMO / SYNTHETIC DATA MODE).

The data produced here is *entirely artificial*. It is designed to exercise the analytics with
realistic statistical features, and every injected event is recorded in a ground-truth manifest
so detection methods can be evaluated honestly.

Return model (daily, per asset i, day t, regime s_t)::

    r_it = mu_it + beta_i * m_t + gamma_i * f_{k(i),t} + (x_it - x_i,t-1) + e_it + J_it

* ``s_t``   hidden 4-state Markov regime (calm / normal / stressed / crisis) plus two scheduled
            episodes (a crisis block and a stressed block) so every seed contains a drawdown.
* ``m_t``   market factor: regime-dependent drift and volatility, Student-t(5) innovations.
* ``f_kt``  sector factor with regime-scaled volatility.
* ``mu_it`` slowly varying expected return, AR(1) with phi=0.998 (creates momentum persistence).
* ``x_it``  Ornstein-Uhlenbeck price deviation (creates short-horizon mean reversion).
* ``e_it``  GARCH(1,1) idiosyncratic noise (volatility clustering).
* ``J_it``  rare jumps (fat tails).

Prices, OHLC and volume are derived from the returns so that OHLC relationships hold by
construction; data defects are then injected into the *raw* output on purpose to exercise
the validation pipeline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

REGIMES = ("calm", "normal", "stressed", "crisis")
_REGIME_MU = np.array([0.00065, 0.00030, -0.00060, -0.00300])
_REGIME_VOL = np.array([0.0065, 0.0100, 0.0180, 0.0350])
_REGIME_SECTOR_MULT = np.array([0.8, 1.0, 1.4, 2.0])
_REGIME_IDIO_MULT = np.array([0.9, 1.0, 1.2, 1.5])
_TRANSITIONS = np.array(
    [
        [0.9920, 0.0070, 0.0010, 0.0000],
        [0.0060, 0.9880, 0.0055, 0.0005],
        [0.0010, 0.0150, 0.9780, 0.0060],
        [0.0000, 0.0050, 0.0350, 0.9600],
    ]
)

SECTORS: dict[str, dict[str, Any]] = {
    "Technology": {"prefix": "TCH", "beta": (1.15, 1.45), "idio": (0.011, 0.018), "gamma": 1.0},
    "Financials": {"prefix": "FIN", "beta": (1.00, 1.30), "idio": (0.009, 0.014), "gamma": 1.0},
    "Healthcare": {"prefix": "HLT", "beta": (0.70, 0.95), "idio": (0.009, 0.015), "gamma": 0.9},
    "Energy": {"prefix": "NRG", "beta": (0.90, 1.20), "idio": (0.012, 0.018), "gamma": 1.3},
    "Industrials": {"prefix": "IND", "beta": (0.95, 1.15), "idio": (0.006, 0.009), "gamma": 0.9},
    "Consumer Staples": {"prefix": "STP", "beta": (0.50, 0.75), "idio": (0.006, 0.009), "gamma": 0.7},
    "Consumer Discretionary": {"prefix": "DSC", "beta": (1.05, 1.30), "idio": (0.010, 0.015), "gamma": 1.0},
    "Utilities": {"prefix": "UTL", "beta": (0.40, 0.65), "idio": (0.006, 0.008), "gamma": 0.8},
}


@dataclass(frozen=True)
class SyntheticConfig:
    seed: int = 42
    start: str = "2018-01-02"
    end: str = "2026-06-30"
    assets_per_sector: int = 5
    inject_market_events: bool = True
    inject_data_defects: bool = True
    # Scheduled episodes expressed as fractions of the sample length (start, length in days).
    crisis_episode: tuple[float, int] = (0.27, 28)
    stressed_episode: tuple[float, int] = (0.52, 150)
    late_listing_fraction: float = 0.30
    delisting_fraction: float = 0.63

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class SyntheticUniverse:
    assets: list[dict[str, Any]]
    bars: pd.DataFrame  # raw bars including injected defects
    manifest: dict[str, Any] = field(default_factory=dict)


def _ou_and_garch(
    rng: np.random.Generator, n_days: int, n: int, idio_vol: np.ndarray, regime_idx: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    alpha, beta_g = 0.08, 0.90
    omega = idio_vol**2 * (1 - alpha - beta_g)
    sig2 = idio_vol**2
    e = np.zeros((n_days, n))
    z = rng.standard_normal((n_days, n))
    for t in range(n_days):
        mult = _REGIME_IDIO_MULT[regime_idx[t]]
        e[t] = np.sqrt(sig2) * z[t] * mult
        sig2 = omega + alpha * (e[t] / mult) ** 2 + beta_g * sig2
    # Ornstein-Uhlenbeck deviation in log-price space (kappa=0.12/day, half-life ~5.4 days).
    kappa = 0.12
    eta = rng.normal(0.0, 0.0038, (n_days, n))
    x = np.zeros((n_days, n))
    for t in range(1, n_days):
        x[t] = (1 - kappa) * x[t - 1] + eta[t]
    ou_ret = np.diff(np.vstack([np.zeros((1, n)), x]), axis=0)
    return e, ou_ret


def _regime_path(rng: np.random.Generator, n_days: int, cfg: SyntheticConfig) -> np.ndarray:
    s = np.empty(n_days, dtype=int)
    s[0] = 1
    u = rng.random(n_days)
    cum = np.cumsum(_TRANSITIONS, axis=1)
    for t in range(1, n_days):
        s[t] = int(np.searchsorted(cum[s[t - 1]], u[t]))
    for (frac, length), state in ((cfg.crisis_episode, 3), (cfg.stressed_episode, 2)):
        a = int(frac * n_days)
        s[a : a + length] = state
        if state == 3:
            s[a + length : a + length + 40] = 2  # crises decay through a stressed phase
    return s


def generate_universe(cfg: SyntheticConfig = SyntheticConfig()) -> SyntheticUniverse:
    return _generate_cached(cfg)


@lru_cache(maxsize=4)
def _generate_cached(cfg: SyntheticConfig) -> SyntheticUniverse:
    rng = np.random.default_rng(cfg.seed)
    dates = pd.bdate_range(cfg.start, cfg.end)  # business days; exchange holidays are not modelled
    n_days = len(dates)

    # --- Asset definitions -------------------------------------------------------------
    assets: list[dict[str, Any]] = []
    betas, gammas, idio, sector_idx = [], [], [], []
    for k, (sector, spec) in enumerate(SECTORS.items()):
        for j in range(cfg.assets_per_sector):
            symbol = f"{spec['prefix']}{j + 1}"
            b = rng.uniform(*spec["beta"])
            assets.append(
                {
                    "symbol": symbol,
                    "name": f"Synthetic {sector} {j + 1:02d}",
                    "asset_type": "equity",
                    "sector": sector,
                    "is_benchmark": False,
                    "attributes": {"synthetic": True, "true_beta": round(b, 4)},
                }
            )
            betas.append(b)
            gammas.append(spec["gamma"] * rng.uniform(0.8, 1.2))
            idio.append(rng.uniform(*spec["idio"]))
            sector_idx.append(k)
    # Commodity-like assets: low equity beta, their own factor exposure.
    for sym, name, b, vol in (("CMD1", "Synthetic Precious Metal", 0.05, 0.010), ("CMD2", "Synthetic Crude Energy", 0.45, 0.020)):
        assets.append(
            {
                "symbol": sym,
                "name": name,
                "asset_type": "commodity",
                "sector": "Commodities",
                "is_benchmark": False,
                "attributes": {"synthetic": True, "true_beta": b},
            }
        )
        betas.append(b)
        gammas.append(0.0 if sym == "CMD1" else 0.9)
        idio.append(vol)
        sector_idx.append(3 if sym == "CMD2" else -1)  # CMD2 loads on the Energy sector factor

    n = len(assets)
    betas_a, gammas_a, idio_a = np.array(betas), np.array(gammas), np.array(idio)
    sector_a = np.array(sector_idx)

    # --- Factors ---------------------------------------------------------------------
    regime = _regime_path(rng, n_days, cfg)
    t5 = rng.standard_t(5, n_days) / np.sqrt(5 / 3)  # unit-variance Student-t
    market = _REGIME_MU[regime] + _REGIME_VOL[regime] * t5
    n_sectors = len(SECTORS)
    sector_f = rng.normal(0, 0.0055, (n_days, n_sectors)) * _REGIME_SECTOR_MULT[regime][:, None]

    crisis_start = int(cfg.crisis_episode[0] * n_days)
    market[crisis_start] = -0.072  # scheduled market-wide shock day
    market[crisis_start + 3] = -0.055
    market[crisis_start + 6] = 0.061  # sharp relief rally inside the crisis

    # Flight-to-quality bond index and gold-like commodity respond negatively to market stress.
    bond_r = 0.00012 + 0.0028 * rng.standard_normal(n_days) - 0.07 * market * (regime >= 2)
    gold_extra = -0.10 * market * (regime >= 2)

    # --- Asset returns ---------------------------------------------------------------
    mu = np.zeros((n_days, n))
    mu_t = rng.normal(0.0002, 0.00035, n)
    shocks = rng.normal(0, 0.00035 * np.sqrt(1 - 0.998**2), (n_days, n))
    for t in range(n_days):
        mu_t = 0.998 * mu_t + 0.002 * 0.0002 + shocks[t]
        mu[t] = mu_t
    e, ou = _ou_and_garch(rng, n_days, n, idio_a, regime)
    sect = np.zeros((n_days, n))
    for i in range(n):
        if sector_a[i] >= 0:
            sect[:, i] = sector_f[:, sector_a[i]] * gammas_a[i]
    jumps = (rng.random((n_days, n)) < 0.0018) * rng.standard_t(3, (n_days, n)) * 0.035
    r = mu + market[:, None] * betas_a[None, :] + sect + ou + e + jumps
    gold_i = next(i for i, a in enumerate(assets) if a["symbol"] == "CMD1")
    r[:, gold_i] += gold_extra
    r = np.clip(r, -0.6, 1.5)

    # --- Controlled market-behaviour events --------------------------------------------------
    events: list[dict[str, Any]] = []
    vol_mult = np.ones((n_days, n))
    if cfg.inject_market_events:
        eq_idx = [i for i, a in enumerate(assets) if a["asset_type"] == "equity"]
        lo = min(300, n_days // 4)
        cand_days = rng.choice(np.arange(lo, n_days - 30), size=12, replace=False)
        cand_assets = rng.choice(eq_idx, size=12, replace=True)
        for q, (d, i) in enumerate(zip(sorted(cand_days), cand_assets, strict=True)):
            if q < 8:  # price shock + volume surge (e.g. an earnings-like surprise)
                move = float(rng.choice([-1, 1]) * rng.uniform(0.11, 0.19))
                r[d, i] = move
                vol_mult[d, i] = rng.uniform(7, 14)
                kind = "price_volume_shock"
            else:  # volume surge without a large price move (e.g. a block trade)
                vol_mult[d, i] = rng.uniform(9, 16)
                kind = "volume_surge"
                move = float(r[d, i])
            events.append(
                {
                    "symbol": assets[i]["symbol"],
                    "date": dates[d].date().isoformat(),
                    "type": kind,
                    "return": round(move, 4),
                    "volume_multiplier": round(float(vol_mult[d, i]), 2),
                    "category": "market_behaviour",
                }
            )

    log_r = np.log1p(r)
    start_px = rng.uniform(20, 250, n)
    close = start_px * np.exp(np.cumsum(log_r, axis=0))

    # --- OHLC and volume ----------------------------------------------------------------
    cond_vol = np.sqrt(e.var(axis=0) + (betas_a * _REGIME_VOL[regime][:, None]) ** 2)
    overnight = 0.3 * log_r + rng.normal(0, 0.15, (n_days, n)) * cond_vol
    prev_close = np.vstack([start_px[None, :], close[:-1]])
    open_ = prev_close * np.exp(overnight)
    hi_ext = np.abs(rng.normal(0, 0.45, (n_days, n))) * cond_vol
    lo_ext = np.abs(rng.normal(0, 0.45, (n_days, n))) * cond_vol
    high = np.maximum(open_, close) * np.exp(hi_ext)
    low = np.minimum(open_, close) * np.exp(-lo_ext)

    base_vol = np.exp(rng.uniform(np.log(2e5), np.log(6e6), n))
    ar = np.zeros((n_days, n))
    noise = rng.normal(0, 0.22, (n_days, n))
    for t in range(1, n_days):
        ar[t] = 0.7 * ar[t - 1] + noise[t]
    trend = np.linspace(0, rng.uniform(-0.3, 0.5), n_days)[:, None]
    surprise = np.abs(r) / (idio_a[None, :] + 1e-9)
    volume = base_vol * np.exp(ar + trend + 0.18 * np.minimum(surprise, 6) + 0.35 * (regime[:, None] >= 2)) * vol_mult
    volume = np.round(volume).astype(np.int64)

    # --- Market index (equal-weighted constituents) and bond index ------------------------------
    eq_cols = [i for i, a in enumerate(assets) if a["asset_type"] == "equity"]
    frame_cols = {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    records = []
    sym_arr = [a["symbol"] for a in assets]

    # Listing/delisting windows (survivorship realism).
    listing = {s: (0, n_days) for s in sym_arr}
    late_sym, delist_sym = "TCH5", "FIN5"
    if late_sym in listing:
        listing[late_sym] = (int(cfg.late_listing_fraction * n_days), n_days)
    if delist_sym in listing:
        d_end = int(cfg.delisting_fraction * n_days)
        di = sym_arr.index(delist_sym)
        # Deteriorating fundamentals before delisting: forced drawdown over 120 days.
        decay = np.linspace(0, np.log(0.35), 120)
        adj = np.zeros(n_days)
        adj[d_end - 120 : d_end] = decay
        adj[d_end:] = decay[-1]
        for arr in (open_, high, low, close):
            arr[:, di] *= np.exp(adj)
        listing[delist_sym] = (0, d_end)

    # Index built from listed constituents only, equal-weighted daily.
    listed_mask = np.zeros((n_days, n), dtype=bool)
    for i, s in enumerate(sym_arr):
        a0, a1 = listing[s]
        listed_mask[a0:a1, i] = True
    close_ret = np.vstack([np.zeros((1, n)), close[1:] / close[:-1] - 1])
    eq_mask = listed_mask[:, eq_cols]
    idx_ret = np.where(eq_mask, close_ret[:, eq_cols], 0).sum(axis=1) / np.maximum(eq_mask.sum(axis=1), 1)
    idx_ret[0] = 0
    idx_close = 1000 * np.cumprod(1 + idx_ret)
    idx_open = np.concatenate([[1000], idx_close[:-1]]) * np.exp(0.3 * np.log1p(idx_ret))
    idx_high = np.maximum(idx_open, idx_close) * (1 + np.abs(rng.normal(0, 0.003, n_days)) * (1 + (regime >= 2)))
    idx_low = np.minimum(idx_open, idx_close) * (1 - np.abs(rng.normal(0, 0.003, n_days)) * (1 + (regime >= 2)))
    idx_vol = np.round(np.where(eq_mask, volume[:, eq_cols], 0).sum(axis=1)).astype(np.int64)

    bond_close = 100 * np.cumprod(1 + bond_r)
    bond_open = np.concatenate([[100], bond_close[:-1]])
    bond_high = np.maximum(bond_open, bond_close) * (1 + np.abs(rng.normal(0, 0.0008, n_days)))
    bond_low = np.minimum(bond_open, bond_close) * (1 - np.abs(rng.normal(0, 0.0008, n_days)))
    bond_vol = np.round(np.exp(rng.normal(np.log(8e5), 0.25, n_days))).astype(np.int64)

    for i, s in enumerate(sym_arr):
        a0, a1 = listing[s]
        sl = slice(a0, a1)
        records.append(
            pd.DataFrame(
                {
                    "symbol": s,
                    "date": dates[sl],
                    **{k: v[sl, i] for k, v in frame_cols.items()},
                }
            )
        )
    records.append(
        pd.DataFrame(
            {
                "symbol": "NXMKT",
                "date": dates,
                "open": idx_open,
                "high": idx_high,
                "low": idx_low,
                "close": idx_close,
                "volume": idx_vol,
            }
        )
    )
    records.append(
        pd.DataFrame(
            {
                "symbol": "NXBND",
                "date": dates,
                "open": bond_open,
                "high": bond_high,
                "low": bond_low,
                "close": bond_close,
                "volume": bond_vol,
            }
        )
    )
    assets.append(
        {
            "symbol": "NXMKT",
            "name": "Nexis Synthetic Equal-Weight Market Index",
            "asset_type": "index",
            "sector": None,
            "is_benchmark": True,
            "attributes": {"synthetic": True, "construction": "equal-weight listed equities, daily rebalanced"},
        }
    )
    assets.append(
        {
            "symbol": "NXBND",
            "name": "Nexis Synthetic Aggregate Bond Index",
            "asset_type": "bond_index",
            "sector": "Fixed Income",
            "is_benchmark": True,
            "attributes": {"synthetic": True},
        }
    )
    for a in assets:
        if a["symbol"] == late_sym:
            a["attributes"]["listed_from"] = dates[listing[late_sym][0]].date().isoformat()
        if a["symbol"] == delist_sym:
            a["attributes"]["delisted_after"] = dates[listing[delist_sym][1] - 1].date().isoformat()

    bars = pd.concat(records, ignore_index=True)
    bars["adj_close"] = bars["close"]
    bars["date"] = pd.to_datetime(bars["date"])
    bars = bars[["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]]

    defects: list[dict[str, Any]] = []
    if cfg.inject_data_defects:
        bars, defects = _inject_defects(bars, rng)

    regime_runs = []
    start_i = 0
    for t in range(1, n_days + 1):
        if t == n_days or regime[t] != regime[start_i]:
            regime_runs.append(
                {
                    "start": dates[start_i].date().isoformat(),
                    "end": dates[t - 1].date().isoformat(),
                    "regime": REGIMES[regime[start_i]],
                }
            )
            start_i = t

    manifest = {
        "generator": "SyntheticMarketDataProvider",
        "config": cfg.to_dict(),
        "fingerprint": cfg.fingerprint(),
        "disclaimer": "All observations are synthetic and do not represent any real security or market.",
        "calendar": "Business days (Mon-Fri); exchange holidays are not modelled.",
        "market_events": events,
        "data_defects": defects,
        "regime_runs": regime_runs,
        "listing_events": [
            {"symbol": late_sym, "type": "late_listing", "date": dates[listing[late_sym][0]].date().isoformat()},
            {"symbol": delist_sym, "type": "delisting", "date": dates[listing[delist_sym][1] - 1].date().isoformat()},
        ],
    }
    return SyntheticUniverse(assets=assets, bars=bars, manifest=manifest)


def _inject_defects(bars: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Inject realistic data-quality problems. Each is logged so validation can be verified."""
    df = bars.copy()
    defects: list[dict[str, Any]] = []
    eq = df[~df["symbol"].isin(["NXMKT", "NXBND"])]
    # Avoid the first/last 300 rows per symbol to keep defects in the analysable body.
    margin = min(300, int(eq.groupby("symbol").size().min()) // 4)
    body = eq.groupby("symbol", group_keys=False).apply(lambda g: g.iloc[margin:-60])
    picks = body.sample(n=24, random_state=int(rng.integers(0, 2**31 - 1))).index.to_list()

    def log(idx: int, kind: str, detail: str) -> None:
        row = df.loc[idx]
        defects.append(
            {
                "symbol": row["symbol"],
                "date": pd.Timestamp(row["date"]).date().isoformat(),
                "type": kind,
                "detail": detail,
                "category": "data_quality",
            }
        )

    it = iter(picks)
    for _ in range(3):  # impossible OHLC: high below low
        i = next(it)
        h, lo = df.at[i, "high"], df.at[i, "low"]
        df.at[i, "high"], df.at[i, "low"] = lo, h
        log(i, "ohlc_inconsistent", "high and low swapped")
    for _ in range(2):  # non-positive price
        i = next(it)
        df.at[i, "close"] = -abs(df.at[i, "close"])
        log(i, "negative_price", "close sign flipped")
    for _ in range(2):  # negative volume
        i = next(it)
        df.at[i, "volume"] = -int(df.at[i, "volume"])
        log(i, "negative_volume", "volume sign flipped")
    for _ in range(3):  # missing volume
        i = next(it)
        df.at[i, "volume"] = pd.NA
        log(i, "missing_volume", "volume set to null")
    for _ in range(2):  # bad tick: whole bar scaled for one day, then reverts
        i = next(it)
        for c in ("open", "high", "low", "close", "adj_close"):
            df.at[i, c] = df.at[i, c] * 1.38
        log(i, "bad_tick", "entire bar scaled by 1.38 for one day (reverts next day)")
    drop_idx = []
    for _ in range(8):  # missing observations
        i = next(it)
        log(i, "missing_observation", "row removed")
        drop_idx.append(i)
    dup_rows = []
    for _ in range(4):  # duplicate symbol+date records
        i = next(it)
        dup = df.loc[[i]].copy()
        dup["close"] = dup["close"] * 1.0005  # near-identical conflicting duplicate
        dup_rows.append(dup)
        log(i, "duplicate", "near-identical duplicate record appended")
    df = df.drop(index=drop_idx)
    df = pd.concat([df, *dup_rows], ignore_index=True)
    df["volume"] = df["volume"].astype("Int64")
    return df, defects
