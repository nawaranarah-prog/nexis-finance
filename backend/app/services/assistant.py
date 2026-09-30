"""Research assistant grounded in the application's own stored data.

This is deliberately *not* a generative chatbot. A question is matched to a supported intent; the
answer is assembled from metrics retrieved (and, where needed, computed) from the database, and
every figure is returned with its evidence and source. Questions outside the supported intents get
an explicit "cannot verify from application data" response — the assistant never guesses.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.models import Anomaly, Backtest, Experiment, Holding, Portfolio, Transaction
from app.services import book as B
from app.services import experiments as exp_svc
from app.services import insights, intel
from app.services import portfolios as port_svc

SUGGESTIONS = [
    "Why did my portfolio experience its largest drawdown?",
    "What contributed most to my portfolio's volatility?",
    "Which assets had the highest anomaly scores?",
    "What changed between the last two backtests?",
    "What is the current market regime?",
    "What is my largest position and exposure?",
    "Are there any reconciliation issues?",
    "What is the Sharpe ratio of Diversified Risk Parity?",
]


def _pct(v: float | None, d: int = 2) -> str:
    return "n/a" if v is None else f"{v * 100:+.{d}f}%"


def _p(v: float | None, d: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{d}f}%"


def _research_portfolio(db: Session, q: str) -> Portfolio | None:
    ql = q.lower()
    best = None
    for p in db.scalars(select(Portfolio)):
        if p.name.lower() in ql and (best is None or len(p.name) > len(best.name)):
            best = p
    return best


def _mine(q: str) -> bool:
    return bool(re.search(r"\b(my|mine|consolidated|holdings|accounts?)\b", q.lower()))


def _has_holdings(db: Session) -> bool:
    return db.scalar(select(Holding.id).limit(1)) is not None or db.scalar(select(Transaction.id).limit(1)) is not None


def _target(db: Session, q: str) -> tuple[Portfolio | None, str]:
    """The research portfolio a question is about, and a note when it stands in for "my portfolio".

    Returns ``(None, "")`` when the question should be answered from the consolidated (imported) book.
    """
    p = _research_portfolio(db, q)
    if p is not None and not _mine(q):
        return p, ""
    if _has_holdings(db):
        return None, ""
    p = p or db.scalars(select(Portfolio).order_by(Portfolio.id)).first()
    if p is None:
        return None, ""
    return p, f"No brokerage holdings have been imported yet, so this answers for the research portfolio “{p.name}”. "


def _answer(intent: str, text: str, evidence: list[dict[str, Any]], links: list[dict[str, str]]) -> dict[str, Any]:
    return {"intent": intent, "grounded": True, "answer": text, "evidence": evidence, "links": links}


# ---------------------------------------------------------------- intents


def drawdown(db: Session, q: str) -> dict[str, Any]:
    p, note = _target(db, q)
    if p is not None:
        a = port_svc.analytics(db, p.id)
        d = a["max_drawdown_details"]
        _, sim, _, asset_r = port_svc.simulate(db, p, None, None)
        contrib = (sim.weights.shift(1) * asset_r).loc[d["peak_date"] : d["trough_date"]].sum().sort_values()
        top = [(s, float(v)) for s, v in contrib.head(3).items()]
        bench = a["benchmark"]["summary"] or {}
        text = note + (
            f"{p.name}'s maximum drawdown was {_pct(d['max_drawdown'])}, from a peak on {d['peak_date']} to a trough on "
            f"{d['trough_date']} ({'recovered ' + d['recovery_date'] if d['recovery_date'] else 'not yet recovered'}). "
            f"The largest negative contributors over that window were " + ", ".join(f"{s} ({_pct(v)})" for s, v in top) + ". "
            f"For reference, the benchmark {a['benchmark']['symbol']}'s maximum drawdown over the full period was {_pct(bench.get('max_drawdown'))}."
        )
        ev = [
            {"label": "Max drawdown", "value": _pct(d["max_drawdown"]), "source": f"portfolio {p.id} analytics"},
            {"label": "Peak → trough", "value": f"{d['peak_date']} → {d['trough_date']}", "source": "wealth index"},
            *[
                {"label": f"{s} contribution", "value": _pct(v), "source": "Σ prior-day weight × daily return, peak→trough"}
                for s, v in top
            ],
        ]
        return _answer("drawdown", text, ev, [{"label": "Portfolio Lab", "to": f"/portfolio-lab?id={p.id}"}])
    r = insights.risk_drilldown(db)
    dd = r["drawdown"]
    top = sorted(dd["contributions"], key=lambda x: x["contribution"])[:3]
    text = (
        f"Your consolidated portfolio's largest reconstructed drawdown was {_pct(dd['max_drawdown'])}, from {dd['peak']} to "
        f"{dd['trough']}. The biggest detractors over that window were "
        + ", ".join(f"{x['symbol']} ({_pct(x['contribution'])})" for x in top)
        + "."
    )
    ev = [
        {"label": "Max drawdown", "value": _pct(dd["max_drawdown"]), "source": "reconstructed portfolio history"},
        *[
            {"label": f"{x['symbol']} contribution", "value": _pct(x["contribution"]), "source": "daily position contributions"}
            for x in top
        ],
    ]
    return _answer("drawdown", text, ev, [{"label": "Risk drill-down", "to": "/xray?tab=risk"}])


def volatility(db: Session, q: str) -> dict[str, Any]:
    p, note = _target(db, q)
    if p is not None:
        dec = port_svc.analytics(db, p.id)["risk_decomposition"]
        top = sorted(dec.get("assets", []), key=lambda x: -x["pct_contribution"])[:3]
        text = note + (
            f"{p.name} has annualised volatility of {_p(dec.get('portfolio_volatility'))}. The largest contributors (Euler "
            f"decomposition) are "
            + ", ".join(f"{x['symbol']} with {_p(x['pct_contribution'])} of risk at {_p(x['weight'])} weight" for x in top)
            + "."
        )
        ev = [
            {"label": f"{x['symbol']}", "value": f"{_p(x['pct_contribution'])} of volatility", "source": "wᵢ(Σw)ᵢ/σ²"}
            for x in top
        ]
        return _answer("volatility", text, ev, [{"label": "Portfolio Lab", "to": f"/portfolio-lab?id={p.id}"}])
    r = insights.risk_drilldown(db)
    top = r["assets"][:3]
    text = (
        f"Your portfolio's volatility is {_p(r['portfolio_volatility'])} (undiversified {_p(r['undiversified_volatility'])}, so "
        f"diversification removes {_p(r['diversification_benefit'])}). The largest contributors are "
        + ", ".join(f"{x['symbol']} ({_p(x['pct_contribution'])} of risk vs {_p(x['weight'])} of value)" for x in top)
        + "."
    )
    ev = [
        {
            "label": x["symbol"],
            "value": f"{_p(x['pct_contribution'])} of volatility",
            "source": f"{r['observations']} daily returns",
        }
        for x in top
    ]
    return _answer("volatility", text, ev, [{"label": "Risk drill-down", "to": "/xray?tab=risk"}])


def anomalies(db: Session, q: str) -> dict[str, Any]:
    exp = db.scalars(
        select(Experiment)
        .where(Experiment.experiment_type == "anomaly", Experiment.status == "completed")
        .order_by(Experiment.created_at.desc())
    ).first()
    if exp is None:
        return _unverifiable("No anomaly scan has been run yet, so there are no stored anomaly scores.")
    rows = db.scalars(
        select(Anomaly)
        .where(Anomaly.experiment_id == exp.id, Anomaly.method == "isolation_forest")
        .order_by(Anomaly.score.desc())
        .limit(5)
    ).all()
    text = (
        f"In the latest scan ({exp.code}, Isolation Forest), the highest anomaly scores were: "
        + "; ".join(
            f"{a.symbol} on {a.date} (score {a.score:.3f}, {a.severity}, {a.category.replace('_', ' ')}, driven by {a.top_feature})"
            for a in rows
        )
        + ". Anomalies are unusual observations, not evidence of wrongdoing."
    )
    ev = [{"label": f"{a.symbol} {a.date}", "value": f"{a.score:.3f}", "source": f"{exp.code} · anomalies table"} for a in rows]
    return _answer("anomalies", text, ev, [{"label": "Anomaly Detection", "to": f"/anomalies?id={exp.id}"}])


def compare_backtests(db: Session, q: str) -> dict[str, Any]:
    codes = re.findall(r"BT-\d{4}-\d{3}", q.upper())
    if len(codes) >= 2:
        exps = [db.scalars(select(Experiment).where(Experiment.code == c)).first() for c in codes[:2]]
    else:
        exps = [
            e
            for _, e in db.execute(
                select(Backtest, Experiment)
                .join(Experiment, Experiment.id == Backtest.experiment_id)
                .order_by(Backtest.created_at.desc())
                .limit(2)
            ).all()
        ][::-1]
    if len(exps) < 2 or any(e is None for e in exps):
        return _unverifiable("Two stored backtests are needed for a comparison.")
    c = exp_svc.compare(db, exps[0].id, exps[1].id)
    full = {r["metric"]: r for r in c["metrics"] if r["split"] == "full"}
    keys = [
        k
        for k in (
            "cumulative_return",
            "annualized_return",
            "sharpe_ratio",
            "max_drawdown",
            "annualized_turnover",
            "total_transaction_costs",
        )
        if k in full
    ]
    diffs = c["config_differences"][:6]
    text = (
        f"Comparing {exps[0].code} (A) with {exps[1].code} (B): configuration differences are "
        + (", ".join(f"{d['key']} ({d['a']} → {d['b']})" for d in diffs) or "none")
        + ". Full-period metrics: "
        + "; ".join(
            f"{k.replace('_', ' ')} {full[k]['a']:.4g} → {full[k]['b']:.4g}"
            for k in keys
            if full[k]["a"] is not None and full[k]["b"] is not None
        )
        + ". This is a numerical comparison of historical simulations, not a ranking."
    )
    ev = [
        {"label": k.replace("_", " "), "value": f"{full[k]['a']:.4g} → {full[k]['b']:.4g}", "source": "experiment_metrics"}
        for k in keys
        if full[k]["a"] is not None and full[k]["b"] is not None
    ]
    return _answer("compare_backtests", text, ev, [{"label": "Research Experiments", "to": "/experiments"}])


def regime(db: Session, q: str) -> dict[str, Any]:
    out, ev = [], []
    for e in db.scalars(
        select(Experiment)
        .where(Experiment.experiment_type == "regime", Experiment.status == "completed")
        .order_by(Experiment.created_at.desc())
    ):
        lt = (e.summary or {}).get("latest") or {}
        if not lt:
            continue
        prob = max(lt.get("probabilities", {}).values(), default=None)
        out.append(f"{e.code} ({e.dataset_version}) classifies {lt['date']} as “{lt['regime']}” with posterior {_p(prob)}")
        ev.append({"label": e.code, "value": lt["regime"], "source": f"{e.dataset_version}, fitted to {e.train_end}"})
        if len(out) == 2:
            break
    if not out:
        return _unverifiable("No regime model has been trained yet.")
    return _answer(
        "regime",
        "; ".join(out) + ". These are descriptive labels from an unsupervised model, not forecasts.",
        ev,
        [{"label": "Regime Analysis", "to": "/regimes"}],
    )


def largest(db: Session, q: str) -> dict[str, Any]:
    p, note = _target(db, q)
    if p is not None:
        pos = sorted(p.positions, key=lambda x: -x.weight)
        top5 = sum(x.weight for x in pos[:5])
        hhi = sum(x.weight**2 for x in pos)
        text = note + (
            f"The largest position in {p.name} is {pos[0].symbol} at {_p(pos[0].weight)} target weight; the top five make up "
            f"{_p(top5)} across {len(pos)} positions (Herfindahl index {hhi:.3f})."
        )
        ev = [
            {"label": "Largest position", "value": f"{pos[0].symbol} {_p(pos[0].weight)}", "source": f"portfolio {p.id} weights"},
            {"label": "Herfindahl index", "value": f"{hhi:.3f}", "source": "Σ w²"},
        ]
        return _answer("largest", text, ev, [{"label": "Portfolio Lab", "to": f"/portfolio-lab?id={p.id}"}])
    a = B.analytics(db)
    c = a.get("concentration") or {}
    ind = [i for i in a["industry"] if not i["label"].startswith("Unclassified")]
    if not c:
        return _unverifiable("No priced holdings are stored yet.")
    text = (
        f"Your largest position is {c['largest_position']} at {_p(c['largest_weight'])} of securities value; the top five holdings "
        f"make up {_p(c['top5_weight'])}. The largest classified industry exposure is "
        + (f"{ind[0]['label']} ({_p(ind[0]['weight'])}, SEC SIC major group)." if ind else "unavailable (no SEC metadata).")
    )
    ev = [
        {
            "label": "Largest position",
            "value": f"{c['largest_position']} {_p(c['largest_weight'])}",
            "source": "reconstructed holdings",
        },
        {"label": "Herfindahl index", "value": f"{c['herfindahl_index']:.3f}", "source": "Σ w²"},
    ]
    return _answer("largest", text, ev, [{"label": "Portfolio X-Ray", "to": "/xray"}])


def reconciliation(db: Session, q: str) -> dict[str, Any]:
    r = intel.reconciliation(db)
    if not r["issues"]:
        return _answer(
            "reconciliation",
            "No inconsistencies were found across your sources.",
            [],
            [{"label": "Reconciliation", "to": "/reconciliation"}],
        )
    text = (
        f"There are {r['count']} potential reconciliation issue(s): "
        + "; ".join(
            f"{i['account']} {i['symbol']}: {i['source_a']} says {i['quantity_a']:g}, {i['source_b']} says {i['quantity_b']:g}"
            for i in r["issues"][:4]
        )
        + ". Nothing has been overwritten; inspect the source records to decide."
    )
    ev = [
        {
            "label": f"{i['symbol']} ({i['type'].replace('_', ' ')})",
            "value": f"{i['quantity_a']:g} vs {i['quantity_b']:g}",
            "source": "source records",
        }
        for i in r["issues"][:4]
    ]
    return _answer("reconciliation", text, ev, [{"label": "Reconciliation", "to": "/reconciliation"}])


def performance(db: Session, q: str) -> dict[str, Any]:
    p, note = _target(db, q)
    if p is not None:
        s = port_svc.analytics(db, p.id)["summary"]
        name, link = p.name, f"/portfolio-lab?id={p.id}"
    else:
        s = B.analytics(db).get("metrics") or {}
        if not s:
            return _unverifiable("No portfolio history is available to compute performance.")
        name, link = "Your consolidated portfolio", "/intelligence"
    text = note + (
        f"{name}: cumulative return {_pct(s.get('cumulative_return'))}, annualised return {_pct(s.get('annualized_return'))}, "
        f"annualised volatility {_p(s.get('annualized_volatility'))}, Sharpe ratio {s.get('sharpe_ratio') or float('nan'):.2f}, "
        f"maximum drawdown {_pct(s.get('max_drawdown'))} over {s.get('start_date')} → {s.get('end_date')}. Historical simulation, not a forecast."
    )
    ev = [
        {
            "label": k.replace("_", " "),
            "value": _pct(s.get(k)) if "ratio" not in k else f"{s.get(k) or 0:.3f}",
            "source": "performance_summary",
        }
        for k in ("annualized_return", "annualized_volatility", "sharpe_ratio", "max_drawdown")
    ]
    return _answer("performance", text, ev, [{"label": "Open", "to": link}])


INTENTS: list[tuple[str, Callable[[Session, str], dict[str, Any]]]] = [
    (r"reconcil|mismatch|discrepan|inconsisten", reconciliation),
    (r"anomal|unusual|outlier", anomalies),
    (r"(chang|differ|compar).*backtest|backtest.*(chang|differ|compar)|BT-\d{4}-\d{3}", compare_backtests),
    (r"drawdown|worst (period|loss)|biggest (drop|fall|loss)", drawdown),
    (r"contribut.*(volatil|risk)|(volatil|risk).*contribut|risk contributor", volatility),
    (r"regime", regime),
    (r"largest (position|holding|exposure)|biggest (position|holding)|concentrat", largest),
    (r"sharpe|return|volatility|performance", performance),
]


def _unverifiable(reason: str | None = None) -> dict[str, Any]:
    return {
        "intent": None,
        "grounded": False,
        "answer": (reason or "I can't verify that from the application's stored data.")
        + " I only answer questions I can ground in stored metrics — try one of the suggestions.",
        "evidence": [],
        "links": [],
        "suggestions": SUGGESTIONS,
    }


def ask(db: Session, question: str) -> dict[str, Any]:
    q = question.strip()
    for pattern, fn in INTENTS:
        if re.search(pattern, q, re.I):
            try:
                out = fn(db, q)
            except NexisError as exc:
                return _unverifiable(f"The data needed for that answer is not available ({exc.message}).")
            out["question"] = q
            out["method"] = "Deterministic intent matching over stored application data; no generative model is used."
            return out
    return {**_unverifiable(), "question": q}
