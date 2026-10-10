"""Time Machine: the arithmetic (offline, synthetic series with known sensitivities) and the member-only routes."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.core.errors import ValidationFailed
from app.services import time_machine as tm
from app.services import valuation
from tests.helpers import member

DAYS = pd.bdate_range("2024-06-03", periods=560)  # ~2.2 years of weekdays


def _walk(seed: int, vol: float = 0.012) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, vol, len(DAYS))


def _prices(returns: np.ndarray, start: float = 100.0) -> pd.Series:
    return pd.Series(start * np.cumprod(1 + returns), index=DAYS)


OIL_R = _walk(1, 0.02)
SPX_R = _walk(2, 0.01)
# An "oil-sensitive" holding: 0.8 × oil + 0.5 × S&P 500 + its own noise. A holding with no link to either.
SERIES = {
    "BZ=F": _prices(OIL_R, 80),
    "^GSPC": _prices(SPX_R, 5000),
    "^TNX": pd.Series(4.0 + np.cumsum(_walk(3, 0.03)), index=DAYS),
    "OILCO": _prices(0.8 * OIL_R + 0.5 * SPX_R + _walk(4, 0.004)),
    "RANDOM": _prices(_walk(5, 0.015)),
    "SHORT": _prices(_walk(6))[-60:],  # only ~12 weeks of history
}


def closes(symbol: str, weeks: int) -> pd.Series | None:
    return SERIES.get(symbol)


def _scenario(**kw):  # type: ignore[no-untyped-def]
    base = {"holdings": "custom", "positions": [{"symbol": "OILCO", "amount": 10_000}], "factor_shocks": {}, "window_weeks": 104}
    return {**base, **kw}


# ---------------------------------------------------------------------------------------------- arithmetic


def test_regression_recovers_known_sensitivities_with_honest_uncertainty() -> None:
    x = pd.DataFrame({"oil": tm.weekly_moves(SERIES["BZ=F"], "pct"), "market_us": tm.weekly_moves(SERIES["^GSPC"], "pct")})
    reg = tm.regress(tm.weekly_moves(SERIES["OILCO"], "pct"), x)
    assert reg is not None and reg["n"] > 100 and reg["r2"] > 0.9
    for k, true in (("oil", 0.8), ("market_us", 0.5)):
        assert abs(reg["beta"][k] - true) < 3 * reg["se"][k], (k, reg["beta"][k], reg["se"][k])
    imp = tm.factor_impact(reg, {"oil": -20, "market_us": 0})
    assert imp["low"] < imp["pct"] < imp["high"] and -20 < imp["pct"] < -12  # ≈ 0.8 × −20%


def test_extrapolation_and_weak_relationships_are_flagged() -> None:
    x = pd.DataFrame({"oil": tm.weekly_moves(SERIES["BZ=F"], "pct")})
    strong = tm.regress(tm.weekly_moves(SERIES["OILCO"], "pct"), x)
    biggest = strong["max_abs_move"]["oil"] * 100
    assert tm.factor_impact(strong, {"oil": -(biggest * 0.5)})["flags"] == []
    assert "extrapolates" in tm.factor_impact(strong, {"oil": -(biggest * 2)})["flags"][0]
    weak = tm.regress(tm.weekly_moves(SERIES["RANDOM"], "pct"), x)
    assert any("Weak relationship" in f for f in tm.factor_impact(weak, {"oil": -5})["flags"])
    assert tm.regress(tm.weekly_moves(SERIES["SHORT"], "pct"), x) is None  # too little history: no number at all


def test_rates_are_measured_in_basis_points() -> None:
    w = tm.weekly_moves(SERIES["^TNX"], "bp")
    weekly = SERIES["^TNX"].resample("W-FRI").last()
    assert w.iloc[0] == pytest.approx((weekly.iloc[1] - weekly.iloc[0]) * 100)


def _price(coupon: float, y: float, years: float, freq: int = 2) -> float:
    n = math.ceil(years * freq - 1e-9)
    times = [years - (n - i) / freq for i in range(1, n + 1)]
    flows = [coupon / freq] * (n - 1) + [coupon / freq + 1]
    return sum(f / (1 + y / freq) ** (t * freq) for t, f in zip(times, flows, strict=True))


@pytest.mark.parametrize(("coupon", "ytm", "years"), [(0.05, 0.05, 10), (0.03, 0.045, 7.3), (0.06, 0.04, 2)])
def test_bond_duration_and_convexity_match_repricing(coupon: float, ytm: float, years: float) -> None:
    mod, conv = tm.duration_convexity(coupon, ytm, years)
    h = 1e-4
    p0, up, dn = _price(coupon, ytm, years), _price(coupon, ytm + h, years), _price(coupon, ytm - h, years)
    assert mod == pytest.approx(-(up - dn) / (2 * h * p0), rel=1e-4)
    assert conv == pytest.approx((up + dn - 2 * p0) / (h * h * p0), rel=1e-3)
    # +100bp: the approximation is within a few basis points of fully repricing the bond
    exact = (_price(coupon, ytm + 0.01, years) / p0 - 1) * 100
    assert tm.bond_impact(coupon, ytm, years, 100)["pct"] == pytest.approx(exact, abs=0.05)


def test_inputs_are_validated() -> None:
    for bad in (
        _scenario(),  # nothing to apply
        _scenario(factor_shocks={"bitcoin": 10}),
        _scenario(factor_shocks={"oil": -95}),
        _scenario(factor_shocks={"rates_us10y": 900}),
        _scenario(factor_shocks={"oil": -10}, window_weeks=30),
        _scenario(positions=[], factor_shocks={"oil": -10}),
        _scenario(asset_shocks={"OILCO": -120}),
    ):
        with pytest.raises(ValidationFailed):
            tm.validate(bad)


# ---------------------------------------------------------------------------------------------- a full run


def test_a_run_explains_every_line_and_is_reproducible(client) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session
    from app.models import User

    member(client, "tm")
    with db_session.SessionLocal() as db:
        user = db.query(User).order_by(User.id.desc()).first()
        inputs = _scenario(
            positions=[{"symbol": "OILCO", "amount": 10_000}, {"symbol": "RANDOM", "amount": 5_000},
                       {"symbol": "SHORT", "amount": 1_000}, {"symbol": "EMAAR.AE", "amount": 2_000}],
            factor_shocks={"oil": -20, "market_us": None},
            asset_shocks={"EMAAR.AE": -15},
        )  # fmt: skip
        r1 = tm.run(db, user, inputs, closes)
        r2 = tm.run(db, user, inputs, closes)
    lines = {ln["symbol"]: ln for ln in r1["output"]["lines"]}
    assert lines["OILCO"]["method"] == "factor" and -20 < lines["OILCO"]["pct"] < -12
    assert lines["OILCO"]["stats"]["weeks"] >= 100 and set(lines["OILCO"]["stats"]["beta"]) == {"oil"}
    assert lines["EMAAR.AE"]["method"] == "explicit" and lines["EMAAR.AE"]["change"] == pytest.approx(-300)
    assert (
        lines["SHORT"]["method"] == "none"
        and lines["SHORT"]["pct"] is None
        and "Not enough price history" in lines["SHORT"]["flags"][0]
    )
    assert any("Weak relationship" in f for f in lines["RANDOM"]["flags"])
    p = r1["output"]["portfolio"]
    assert p["covered_value"] == 17_000 and p["wide_low"] <= p["change"] <= p["wide_high"]
    assert r1["model_version"] == tm.MODEL_VERSION and r1["snapshot"]["series"]["BZ=F"]["days"] > 400
    assert r1["inputs"]["factor_shocks"] == {"oil": -20.0}  # empty shocks are dropped, not stored as zero
    assert r1["output"] == r2["output"]  # deterministic: same inputs + same data = same answer


def test_valuation_sensitivity_reruns_the_dcf(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    a = {"base_fcf": 1000.0, "years": 5, "growth_start": 0.06, "terminal_growth": 0.025, "risk_free_rate": 0.042, "beta": 1.0,
         "equity_risk_premium": 0.05, "country_risk_premium": 0.007, "pre_tax_cost_of_debt": 0.057, "tax_rate": 0.09,
         "cash": 500.0, "debt": 800.0, "shares_outstanding": 100.0, "market_cap": 9000.0}  # fmt: skip
    base = {"symbol": "TEST.AE", "name": "Test Co", "currency": "AED", "financial_currency": "AED", "price": 90.0, "assumptions": a,
            "details": {"profile": {"sector": "Real Estate", "industry": "Development"}}}  # fmt: skip
    monkeypatch.setattr(valuation, "defaults", lambda db, s: base)
    with db_session.SessionLocal() as db:
        up = tm.valuation_change(db, {"symbol": "TEST.AE", "growth_change": 0, "wacc_change": 1, "terminal_growth_change": 0})
        w0 = valuation.wacc(a)["wacc"]
        assert up["before"] == pytest.approx(valuation.dcf(a, w0, 0.025)["value_per_share"])
        assert up["after"] == pytest.approx(valuation.dcf(a, w0 + 0.01, 0.025)["value_per_share"])
        assert up["change_pct"] < 0  # a higher discount rate lowers the value
        base["assumptions"] = {**a, "beta": 0.2}  # a low-risk company: discount rate ≈ 5.7%
        with pytest.raises(ValidationFailed, match="above terminal growth"):
            tm.valuation_change(db, {"symbol": "TEST.AE", "growth_change": 0, "wacc_change": -2, "terminal_growth_change": 2})


# ---------------------------------------------------------------------------------------------- routes


@pytest.fixture
def offline(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tm, "market_closes", lambda db: closes)


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/time-machine/factors"), ("POST", "/api/time-machine/run"), ("POST", "/api/time-machine/scenarios"),
     ("GET", "/api/time-machine/scenarios/1"), ("POST", "/api/time-machine/scenarios/1/rerun"),
     ("GET", "/api/time-machine/runs/1"), ("GET", "/api/time-machine/compare?a=1&b=2")],
)  # fmt: skip
def test_visitors_get_401(client, method, path):  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.request(method, path, json=_scenario(factor_shocks={"oil": -10})).status_code == 401


def test_saved_scenarios_keep_every_run_and_are_private(client, offline):  # type: ignore[no-untyped-def]
    member(client, "tms")
    body = {"title": "Oil −20%", "inputs": _scenario(factor_shocks={"oil": -20})}
    saved = client.post("/api/time-machine/scenarios", json=body)
    assert saved.status_code == 201, saved.text
    item, first = saved.json()["item"], saved.json()["run"]
    assert item["kind"] == "scenario" and first["model_version"] == tm.MODEL_VERSION

    again = client.post(f"/api/time-machine/scenarios/{item['id']}/rerun").json()
    assert again["id"] != first["id"]
    view = client.get(f"/api/time-machine/scenarios/{item['id']}").json()
    assert [r["id"] for r in view["runs"]] == [again["id"], first["id"]] and view["latest"]["id"] == again["id"]
    assert client.get(f"/api/time-machine/runs/{first['id']}").json()["output"] == first["output"]  # never rewritten
    cmp = client.get("/api/time-machine/compare", params={"a": first["id"], "b": again["id"]}).json()
    assert cmp["diff"]["portfolio"]["diff"] == pytest.approx(0)

    assert client.post("/api/time-machine/run", json=_scenario(factor_shocks={"oil": -20}, extra=1)).status_code == 422
    member(client, "tms2")
    assert client.get(f"/api/time-machine/scenarios/{item['id']}").status_code == 404
    assert client.get(f"/api/time-machine/runs/{first['id']}").status_code == 404
    assert client.post(f"/api/time-machine/scenarios/{item['id']}/rerun").status_code == 404
    assert client.get("/api/time-machine/compare", params={"a": first["id"], "b": again["id"]}).status_code == 404


def test_my_investments_are_used_when_asked(client, offline, monkeypatch):  # type: ignore[no-untyped-def]
    from app.services import portfolio_analytics

    member(client, "tmm")
    empty = client.post("/api/time-machine/run", json={"holdings": "mine", "factor_shocks": {"oil": -10}})
    assert empty.status_code == 422 and "no priced investments" in empty.json()["error"]["message"]
    pos = [{"symbol": "OILCO", "name": "Oil Co", "priced": True, "value_usd": 4000.0},
           {"symbol": "NOPRICE", "name": "No price", "priced": False, "value_usd": 0.0}]  # fmt: skip
    monkeypatch.setattr(portfolio_analytics, "_positions", lambda db, user: (pos, {}))
    out = client.post("/api/time-machine/run", json={"holdings": "mine", "factor_shocks": {"oil": -10}}).json()["output"]
    assert out["currency"] == "USD" and out["unpriced"] == ["NOPRICE"] and [ln["symbol"] for ln in out["lines"]] == ["OILCO"]


def test_scenario_moves_are_compared_with_three_month_moves_and_unclear_directions_are_said() -> None:
    oil = SERIES["BZ=F"]
    weekly = oil.resample("W-FRI").last()
    expect = float((weekly / weekly.shift(13) - 1).abs().max())
    assert tm.largest_move(oil, "pct") == pytest.approx(expect)
    x = pd.DataFrame({"oil": tm.weekly_moves(oil, "pct")})
    reg = tm.regress(tm.weekly_moves(SERIES["OILCO"], "pct"), x)
    big_week = reg["max_abs_move"]["oil"] * 100
    within_quarter = min(expect * 100 * 0.9, 89)
    assert within_quarter > big_week  # a move too big for one week but normal over a quarter…
    assert not any("extrapolates" in f for f in tm.factor_impact(reg, {"oil": -within_quarter}, {"oil": expect})["flags"])
    beyond = tm.factor_impact(reg, {"oil": -(expect * 100 * 1.5)}, {"oil": expect})
    assert any("larger than any 3-month move" in f for f in beyond["flags"])

    weak = tm.regress(tm.weekly_moves(SERIES["RANDOM"], "pct"), x)
    unclear = tm.factor_impact(weak, {"oil": -10}, {"oil": expect})
    assert unclear["low"] < 0 < unclear["high"]
    assert any("can't tell whether" in f for f in unclear["flags"]) and any("explain only" in f for f in unclear["flags"])
