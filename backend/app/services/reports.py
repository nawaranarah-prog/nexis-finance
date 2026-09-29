"""Research report generation (PDF).

Language is deliberately descriptive: "historical simulation", "research result",
"hypothetical scenario", "model output". Reports never contain buy/sell recommendations.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import APP_VERSION, get_settings
from app.core.errors import ConfigurationError, NotFoundError
from app.models import Backtest, Experiment, Report, StressTest
from app.reporting.pdf import (
    PageBreak,
    Spacer,
    bar_chart,
    build_pdf,
    line_chart,
    mm,
    money,
    monthly_heatmap,
    num,
    para,
    pct,
    table,
)
from app.services import portfolios as port_svc
from app.services.experiments import get_experiment
from app.services.market_data import dataset_summary, get_dataset
from app.services.notifications import notify

LIMITATIONS = [
    "Results are historical simulations. Past performance of a simulated strategy or portfolio does not indicate future results.",
    "Transaction costs are modelled as fixed basis-point commission and slippage; market impact, borrow costs, "
    "taxes and financing are not modelled.",
    "Daily bars only; intraday path, auctions and exchange holidays (for synthetic data) are not modelled.",
    "Survivorship: the universe contains the assets present in the dataset. For live public data this is typically "
    "the set of currently listed instruments, which can bias results upward.",
    "Parameter choices and model selection carry estimation error; in-sample optimised allocations are unstable.",
    "VaR/CVaR are quantile-based estimates under stated assumptions; they are not maximum possible losses.",
    "Regime labels and anomaly flags are unsupervised model outputs and have no guaranteed economic meaning.",
]
DISCLAIMER = (
    "This document was produced by research software for educational and research purposes only. "
    "It is not investment advice, an offer, or a solicitation to buy or sell any security."
)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")[:60] or "report"


def generate(
    db: Session,
    title: str,
    portfolio_id: int | None,
    backtest_id: int | None,
    experiment_ids: list[int],
    stress_test_ids: list[int],
    notes: str | None = None,
) -> Report:
    if not any([portfolio_id, backtest_id, experiment_ids, stress_test_ids]):
        raise ConfigurationError("select at least one portfolio, backtest, experiment or stress test for the report")
    if len(experiment_ids) > 10 or len(stress_test_ids) > 10:
        raise ConfigurationError("at most 10 experiments and 10 stress tests per report")
    settings = get_settings()
    now = datetime.now(UTC)
    story: list[Any] = []
    sections: list[str] = []
    dataset_ids: set[int] = set()

    p = port_svc.get_portfolio(db, portfolio_id) if portfolio_id else None
    bt = db.get(Backtest, backtest_id) if backtest_id else None
    if backtest_id and bt is None:
        raise NotFoundError(f"backtest {backtest_id} not found")
    exps = [get_experiment(db, i) for i in experiment_ids]
    sts = []
    for sid in stress_test_ids:
        s = db.get(StressTest, sid)
        if s is None:
            raise NotFoundError(f"stress test {sid} not found")
        sts.append(s)
    for obj in [p, bt, *exps]:
        if obj is not None:
            dataset_ids.add(obj.dataset_id)

    story += [
        para(title, "title"),
        para(f"Research report · generated {now:%Y-%m-%d %H:%M} UTC · Nexis Finance v{APP_VERSION}", "subtitle"),
    ]
    ds_rows = [["Dataset", "Version", "Source", "Period", "Records", "SHA-256 (prefix)"]]
    synthetic = False
    for did in sorted(dataset_ids):
        ds = get_dataset(db, did)
        s = dataset_summary(db, ds)
        synthetic |= ds.is_synthetic
        ds_rows.append(
            [
                ds.code,
                s["version_label"],
                "synthetic" if ds.is_synthetic else ds.source,
                f"{s['start_date']} → {s['end_date']}",
                f"{s['record_count']:,}",
                (ds.content_hash or "")[:16],
            ]
        )
    if synthetic:
        story.append(
            para(
                "DEMO / SYNTHETIC DATA MODE — all market observations in this report are artificially "
                "generated and do not describe any real security or market.",
                "banner",
            )
        )
    story += [para("Dataset", "h1"), table(ds_rows, [30 * mm, 34 * mm, 18 * mm, 40 * mm, 18 * mm, 34 * mm], align_right_from=4)]
    if notes:
        story += [para("Analyst notes", "h2"), para(notes)]

    if p is not None:
        sections.append("portfolio")
        story += _portfolio_section(db, p, sts)
    if bt is not None:
        sections.append("backtest")
        story += _backtest_section(db, bt)
    for e in exps:
        sections.append(e.experiment_type)
        story += _experiment_section(e)
    if sts and p is None:
        sections.append("stress_tests")
        story += _stress_section(sts)

    story += [PageBreak(), para("Methodology", "h1")]
    story += [
        para(t)
        for t in (
            "<b>Returns.</b> Simple close-to-close returns on adjusted closes; annualised with 252 trading days. "
            "Annualised return is geometric (CAGR). Volatility is the sample standard deviation × √252.",
            "<b>Risk-adjusted ratios.</b> Sharpe = mean excess return / std of excess return × √252 using the stated risk-free "
            "rate. Sortino uses downside deviation below the risk-free rate. Calmar = annualised return / |maximum drawdown|.",
            "<b>VaR / CVaR.</b> Historical VaR is the empirical loss quantile of the lookback sample; parametric VaR assumes "
            "i.i.d. normal returns; CVaR is the mean loss beyond VaR. Reported as positive loss fractions.",
            "<b>Backtests.</b> Signals are formed at the close using only data available at that close and executed on the "
            "next bar (open or close as configured) with commission and slippage charged on traded notional.",
            "<b>Machine learning.</b> Chronological train/validation/test splits with purging of overlapping forward targets; "
            "no shuffling. Fitted models are compared with no-fit baselines.",
        )
    ]
    story += [para("Limitations", "h1")] + [para(f"• {t}") for t in LIMITATIONS]
    story += [para("Reproducibility", "h1")]
    rep_rows = [["Item", "Identifier", "Dataset version", "Seed", "Configuration hash"]]
    for label, e in ([("Backtest", db.get(Experiment, bt.experiment_id))] if bt else []) + [("Experiment", e) for e in exps]:
        if e is None:
            continue
        cfg_hash = hashlib.sha256(repr(sorted((e.config or {}).items())).encode()).hexdigest()[:12]
        rep_rows.append([label, e.code, e.dataset_version, str(e.seed if e.seed is not None else "—"), cfg_hash])
    if len(rep_rows) > 1:
        story.append(table(rep_rows, [25 * mm, 30 * mm, 45 * mm, 15 * mm, 35 * mm], align_right_from=5))
        story.append(
            para(
                "Each experiment can be re-run from its stored configuration via “Reproduce” in the "
                "Research Experiments page; the reproduction records a metric-level comparison.",
                "small",
            )
        )
    story += [Spacer(1, 6 * mm), para("Disclaimer", "h2"), para(DISCLAIMER, "small")]

    fname = f"{now:%Y%m%d-%H%M%S}-{_slug(title)}.pdf"
    path = Path(settings.reports_dir) / fname
    build_pdf(str(path), story, title, "Research output — not investment advice." + (" Synthetic data." if synthetic else ""))
    content = path.read_bytes()
    rep = Report(
        title=title[:200],
        report_type="research",
        file_name=fname,
        file_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        config={
            "portfolio_id": portfolio_id,
            "backtest_id": backtest_id,
            "experiment_ids": experiment_ids,
            "stress_test_ids": stress_test_ids,
            "notes": notes,
        },
        sections=sections,
    )
    db.add(rep)
    notify(db, "success", "report", "Research report generated", f"“{title}” ({len(content) / 1024:.0f} KB).", link="/reports")
    db.commit()
    return rep


def _portfolio_section(db: Session, p: Any, sts: list[Any]) -> list[Any]:
    a = port_svc.analytics(db, p.id)
    s, b = a["summary"], (a["benchmark"]["summary"] or {})
    out: list[Any] = [
        PageBreak(),
        para(f"Portfolio: {p.name}", "h1"),
        para(
            f"Historical simulation {a['period']['start']} → {a['period']['end']} "
            f"({a['period']['observations']} trading days). Allocation method: {p.allocation_method}; "
            f"rebalanced {p.rebalance_frequency}; initial capital {money(p.initial_capital)}; "
            f"benchmark {p.benchmark_symbol}; risk-free rate {a['risk_free_rate']:.2%}."
        ),
    ]
    rows = [["Metric", "Portfolio", "Benchmark"]]
    for label, k, f in (
        ("Cumulative return", "cumulative_return", pct),
        ("Annualised return", "annualized_return", pct),
        ("Annualised volatility", "annualized_volatility", lambda v: pct(v, signed=False)),
        ("Sharpe ratio", "sharpe_ratio", num),
        ("Sortino ratio", "sortino_ratio", num),
        ("Maximum drawdown", "max_drawdown", pct),
        ("Calmar ratio", "calmar_ratio", num),
    ):
        rows.append([label, f(s.get(k)), f(b.get(k))])
    rows += [
        ["Beta", num(s.get("beta")), "1.000"],
        ["Tracking error", pct(s.get("tracking_error"), signed=False), "—"],
        ["Information ratio", num(s.get("information_ratio")), "—"],
    ]
    out += [table(rows, [60 * mm, 40 * mm, 40 * mm])]
    ser = a["series"]
    out += [
        line_chart(
            ser["dates"],
            {"Portfolio": ser["value"], a["benchmark"]["symbol"]: ser["benchmark_value"]},
            "Portfolio value vs benchmark",
        ),
        line_chart(ser["dates"], {"Drawdown": ser["drawdown"]}, "Drawdown", percent=True, fill_negative=True),
    ]
    pos = [["Symbol", "Name", "Target weight", "Current weight", "Ann. vol", "% risk contribution"]]
    rc = {x["symbol"]: x for x in a["risk_decomposition"].get("assets", [])}
    for x in a["assets"]:
        pos.append(
            [
                x["symbol"],
                x["name"][:42],
                pct(x["target_weight"], signed=False),
                pct(x["current_weight"], signed=False),
                pct(x["annualized_volatility"], signed=False),
                pct(rc.get(x["symbol"], {}).get("pct_contribution"), signed=False),
            ]
        )
    out += [
        para("Positions and risk contribution", "h2"),
        table(pos, [16 * mm, 64 * mm, 22 * mm, 24 * mm, 18 * mm, 30 * mm], align_right_from=2),
    ]
    var_rows = [["Confidence", "Historical VaR", "Historical CVaR", "Parametric VaR", "Parametric CVaR", "Obs."]]
    for c, v in a["var"].items():
        if "error" in v:
            continue
        var_rows.append(
            [
                f"{c}%",
                pct(v["historical"]["var"], signed=False),
                pct(v["historical"]["cvar"], signed=False),
                pct(v["parametric_normal"]["var"], signed=False),
                pct(v["parametric_normal"]["cvar"], signed=False),
                str(v["observations"]),
            ]
        )
    out += [
        para("One-day Value-at-Risk (full sample)", "h2"),
        table(var_rows),
        para("VaR is a loss quantile of the historical/modelled distribution, not a maximum possible loss.", "small"),
    ]
    own = [s for s in sts if s.portfolio_id == p.id]
    if own:
        out += _stress_section(own)
    return out


def _stress_section(sts: list[Any]) -> list[Any]:
    rows = [["Scenario", "Type", "Portfolio impact", "Largest contributor"]]
    for s in sts:
        r = s.results
        lc = (r.get("largest_contributors") or [{}])[0]
        rows.append(
            [
                s.name[:40],
                s.scenario_type,
                pct(r.get("portfolio_return")),
                f"{lc.get('symbol', '—')} ({pct(lc.get('contribution'))})"
                if lc.get("contribution") is not None
                else lc.get("symbol", "—"),
            ]
        )
    return [
        para("Hypothetical stress scenarios", "h2"),
        table(rows, [60 * mm, 35 * mm, 30 * mm, 45 * mm]),
        para(
            "Stress results are hypothetical scenarios under linear/beta assumptions, not forecasts. For "
            "volatility and correlation scenarios the impact shown is the stressed one-day parametric CVaR.",
            "small",
        ),
    ]


def _backtest_section(db: Session, bt: Backtest) -> list[Any]:
    exp = db.get(Experiment, bt.experiment_id)
    cfg = exp.config if exp else {}
    m, ser = bt.metrics["full"], bt.series
    out: list[Any] = [
        PageBreak(),
        para(f"Backtest: {bt.name} ({exp.code if exp else ''})", "h1"),
        para(
            f"Historical simulation of strategy <b>{bt.strategy_key}</b> from {bt.start_date} to {bt.end_date}. "
            f"Execution: {bt.execution.replace('_', ' ')}. Costs: {cfg.get('commission_bps', 5)} bps commission + "
            f"{cfg.get('slippage_bps', 5)} bps slippage. Parameters: {bt.metrics.get('selected_params')}."
        ),
    ]
    rows = [
        ["Metric", "Net of costs", "Gross"],
        ["Cumulative return", pct(m.get("cumulative_return")), pct(m.get("gross_cumulative_return"))],
        ["Annualised return", pct(m.get("annualized_return")), pct(m.get("gross_annualized_return"))],
        ["Sharpe ratio", num(m.get("sharpe_ratio")), num(m.get("gross_sharpe_ratio"))],
        ["Annualised volatility", pct(m.get("annualized_volatility"), signed=False), "—"],
        ["Sortino ratio", num(m.get("sortino_ratio")), "—"],
        ["Maximum drawdown", pct(m.get("max_drawdown")), "—"],
        ["Calmar ratio", num(m.get("calmar_ratio")), "—"],
        ["Win rate (days)", pct(m.get("win_rate"), signed=False), "—"],
        ["Profit factor (days)", num(m.get("profit_factor")), "—"],
        ["Annualised turnover", num(m.get("annualized_turnover"), 2), "—"],
        ["Number of trades", str(m.get("number_of_trades")), "—"],
        ["Total transaction costs", money(m.get("total_transaction_costs")), "—"],
    ]
    out += [table(rows, [60 * mm, 40 * mm, 40 * mm])]
    series = {"Net equity": ser["equity"], "Gross equity": ser["gross_equity"]}
    if ser.get("benchmark_equity"):
        series[f"Benchmark ({bt.benchmark_symbol})"] = ser["benchmark_equity"]
    out += [
        line_chart(ser["dates"], series, "Equity curve"),
        line_chart(ser["dates"], {"Drawdown": ser["drawdown"]}, "Drawdown", percent=True, fill_negative=True),
    ]
    hm = monthly_heatmap(ser.get("monthly_returns", []), "Monthly net returns")
    if hm:
        out.append(hm)
    segs = bt.metrics.get("segments") or {}
    if segs:
        rows = [["Segment", "Cumulative", "Ann. return", "Sharpe", "Max DD", "Costs"]]
        for name, sm in segs.items():
            if "error" in sm:
                continue
            rows.append(
                [
                    f"{name} ({sm.get('start_date')} → {sm.get('end_date')})",
                    pct(sm.get("cumulative_return")),
                    pct(sm.get("annualized_return")),
                    num(sm.get("sharpe_ratio")),
                    pct(sm.get("max_drawdown")),
                    money(sm.get("transaction_costs")),
                ]
            )
        out += [
            para("Chronological segments", "h2"),
            table(rows, [62 * mm, 22 * mm, 22 * mm, 18 * mm, 20 * mm, 24 * mm]),
            para("Parameters were fixed (or selected on the in-sample segment only) before evaluating later segments.", "small"),
        ]
    return out


def _experiment_section(e: Experiment) -> list[Any]:
    a = e.artifacts or {}
    out: list[Any] = [
        PageBreak(),
        para(f"Experiment {e.code}: {e.name}", "h1"),
        para(f"Type: {e.experiment_type}. Dataset {e.dataset_version}. Seed {e.seed}. Status {e.status}."),
    ]
    if e.experiment_type == "volatility_forecast":
        sp = a.get("split", {})
        out.append(
            para(
                f"Target: annualised realised volatility over the next {sp.get('horizon')} trading days. "
                f"Train {_rng(sp.get('train'))}, validation {_rng(sp.get('validation'))}, test {_rng(sp.get('test'))}; "
                f"{sp.get('purged_rows')} rows purged at split boundaries."
            )
        )
        rows = [["Model", "Val. RMSE", "Val. MAE", "Test RMSE", "Test MAE", "Test R²"]]
        for model, mm_ in a.get("metrics", {}).items():
            rows.append(
                [
                    model,
                    num(mm_["validation"]["rmse"], 4),
                    num(mm_["validation"]["mae"], 4),
                    num(mm_["test"]["rmse"], 4),
                    num(mm_["test"]["mae"], 4),
                    num(mm_["test"]["r2"], 3),
                ]
            )
        out += [table(rows)]
        sel = a.get("selection", {})
        out.append(
            para(
                f"Model output: preferred model <b>{sel.get('preferred_model')}</b> "
                f"(validation RMSE improvement vs best baseline: {pct(sel.get('rmse_improvement_vs_baseline'))}; "
                f"rule: {sel.get('rule')})."
            )
        )
        best = sel.get("best_fitted_model")
        pim = (a.get("explainability", {}).get(best, {}) or {}).get("permutation_importance")
        if pim:
            items = sorted(pim.items(), key=lambda kv: kv[1]["mean"], reverse=True)[:10]
            out.append(
                bar_chart(
                    [k for k, _ in items],
                    [v["mean"] for _, v in items],
                    f"Permutation importance on test set ({best}, increase in RMSE)",
                )
            )
    elif e.experiment_type == "regime":
        rows = [["Regime (descriptive label)", "Frequency", "Avg. duration (days)", "Mean 20d vol", "Mean 60d momentum"]]
        for s in a.get("regime_stats", []):
            rows.append(
                [
                    s["regime"],
                    pct(s["frequency"], 1, False),
                    num(s["average_duration_days"], 1),
                    pct(s["feature_means"].get("rv_20"), 1, False),
                    pct(s["feature_means"].get("mom_60"), 1),
                ]
            )
        out += [table(rows, [65 * mm, 22 * mm, 30 * mm, 25 * mm, 28 * mm])]
        lt = a.get("latest", {})
        out.append(
            para(
                f"Latest classified regime ({lt.get('date')}): <b>{lt.get('regime')}</b>. Labels are derived "
                "from cluster feature means and are not forecasts."
            )
        )
        ev = a.get("evaluation_vs_synthetic_truth")
        if ev:
            out.append(para(f"Adjusted Rand index vs synthetic ground-truth regimes: {num(ev.get('adjusted_rand_index'))}."))
    elif e.experiment_type == "anomaly":
        s = a.get("summary", {})
        out.append(
            para(
                f"{s.get('observations', 0):,} observations across {s.get('symbols')} assets, {s.get('period')}. "
                f"Flags by method: {s.get('flagged_by_method')}; flagged by both: {s.get('flagged_by_both')}. "
                f"Data-quality flags: {s.get('data_quality_flags')}; market-behaviour flags: {s.get('market_behaviour_flags')}."
            )
        )
        ev = a.get("evaluation_vs_injected")
        if ev:
            rows = [["Method", "Flagged", "Injected events found", "Recall"]]
            for mname, mv in ev["methods"].items():
                rows.append(
                    [
                        mname,
                        str(mv["flagged"]),
                        f"{mv['true_positives']}/{ev['injected_events_in_sample']}",
                        pct(mv["recall"], 1, False),
                    ]
                )
            out += [table(rows), para(ev["note"], "small")]
    elif e.experiment_type == "walk_forward":
        rows = [["Fold", "Train", "Test", "Selected params", "IS objective", "OOS Sharpe", "OOS return"]]
        for f in a.get("folds", []):
            rows.append(
                [
                    str(f["fold"]),
                    f"{f['train_start']}→{f['train_end']}",
                    f"{f['test_start']}→{f['test_end']}",
                    str(f["selected_params"])[:40],
                    num(f.get("in_sample_objective"), 2),
                    num(f.get("test_sharpe_ratio"), 2),
                    pct(f.get("test_cumulative_return")),
                ]
            )
        out += [table(rows, [10 * mm, 38 * mm, 38 * mm, 40 * mm, 16 * mm, 16 * mm, 18 * mm])]
        agg = a.get("aggregate_out_of_sample", {})
        out.append(
            para(
                f"Stitched out-of-sample: cumulative {pct(agg.get('cumulative_return'))}, Sharpe {num(agg.get('sharpe_ratio'))}, "
                f"max drawdown {pct(agg.get('max_drawdown'))}."
            )
        )
        ser = a.get("series")
        if ser:
            out.append(line_chart(ser["dates"], {"Out-of-sample equity": ser["equity"]}, "Stitched walk-forward test equity"))
    elif e.experiment_type == "backtest":
        out.append(para(f"Headline: {e.summary}"))
    return out


def _rng(r: Any) -> str:
    return f"{r[0]} → {r[1]}" if r else "none"


def list_reports(db: Session) -> list[dict[str, Any]]:
    return [serialize(r) for r in db.scalars(select(Report).order_by(Report.created_at.desc()))]


def serialize(r: Report) -> dict[str, Any]:
    return {
        "id": r.id,
        "title": r.title,
        "report_type": r.report_type,
        "file_name": r.file_name,
        "file_size": r.file_size,
        "sha256": r.sha256,
        "config": r.config,
        "sections": r.sections,
        "created_at": r.created_at.isoformat(),
    }


def report_path(db: Session, report_id: int) -> tuple[Path, Report]:
    r = db.get(Report, report_id)
    if r is None:
        raise NotFoundError(f"report {report_id} not found")
    base = Path(get_settings().reports_dir).resolve()
    path = (base / r.file_name).resolve()
    if base not in path.parents or not path.is_file():  # guard against path traversal / missing files
        raise NotFoundError("report file is not available")
    return path, r
