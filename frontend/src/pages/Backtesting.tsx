import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { Data, Shape } from "plotly.js";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Disclaimer, download, Field, InfoTip, JobStatus, PageHead, QueryView, StatusBadge, Tabs } from "../components/ui";
import { AssetChecklist, AssetSelect, defaultBenchmark } from "../components/pickers";
import { dateAxis, diverging, INK, seriesColor } from "../components/charts";
import { label, line, MetricCompare, MetricStrip, PCT_AXIS } from "../components/metrics";
import { useAssets, useExperiments, useJobRunner, useStrategies } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, buildUrl } from "../services/api";
import type { AnyObj, StrategyInfo } from "../types/api";
import { dt, int, money, num, pct, secs, spct, tone } from "../utils/format";

type Tab = "results" | "new" | "walk_forward";

export default function Backtesting() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(params.get("strategy") ? "new" : "results");
  const selectedId = params.get("id") ? Number(params.get("id")) : null;
  const list = useQuery({ queryKey: ["backtests"], queryFn: () => api.get<AnyObj[]>("/backtests") });
  const id = selectedId ?? list.data?.[0]?.id ?? null;
  return (
    <>
      <PageHead title="Backtesting" desc="Event-driven daily simulation: signals at the close, execution on the next bar, commission and slippage on every trade, chronological in-sample / validation / out-of-sample evaluation." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "results", label: "Results" }, { value: "new", label: "New backtest" }, { value: "walk_forward", label: "Walk-forward analysis" }]} />
      {tab === "results" && (
        <div className="stack">
          <Card title="Backtests" flush>
            <QueryView q={list}>
              {(rows) => (
                <DataTable<AnyObj> rows={rows} pageSize={6} onRowClick={(r) => setParams({ id: String(r.id) })} selected={(r) => r.id === id} columns={[
                  { key: "experiment_code", label: "Code", render: (r) => <b className="mono">{r.experiment_code}</b> },
                  { key: "name", label: "Name" }, { key: "strategy_key", label: "Strategy" },
                  { key: "period", label: "Period", value: (r) => `${r.start_date} → ${r.end_date}` },
                  { key: "cum", label: "Net cumulative", align: "right", value: (r) => r.headline.cumulative_return, render: (r) => <span className={tone(r.headline.cumulative_return)}>{spct(r.headline.cumulative_return)}</span> },
                  { key: "sharpe", label: "Sharpe", align: "right", value: (r) => r.headline.sharpe_ratio, render: (r) => num(r.headline.sharpe_ratio, 2) },
                  { key: "mdd", label: "Max DD", align: "right", value: (r) => r.headline.max_drawdown, render: (r) => spct(r.headline.max_drawdown) },
                  { key: "trade_count", label: "Trades", align: "right" },
                  { key: "created_at", label: "Run", render: (r) => dt(r.created_at) },
                ]} empty="No backtests yet." />
              )}
            </QueryView>
          </Card>
          {id && <Result id={id} />}
        </div>
      )}
      {tab === "new" && <Config mode="backtest" onDone={(r) => { setTab("results"); if (r?.backtest_id) setParams({ id: String(r.backtest_id) }); }} />}
      {tab === "walk_forward" && <WalkForward />}
    </>
  );
}

// ---------------------------------------------------------------- configuration

function Config({ mode, onDone }: { mode: "backtest" | "walk_forward"; onDone: (r: AnyObj | null) => void }) {
  const { datasetId, dataset } = useWorkspace();
  const [params] = useSearchParams();
  const strategies = useStrategies();
  const assets = useAssets(datasetId);
  const job = useJobRunner([["backtests"], ["experiments"]]);
  const [key, setKey] = useState(params.get("strategy") ?? "momentum");
  const strat: StrategyInfo | undefined = strategies.data?.find((s) => s.key === key);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [grid, setGrid] = useState<Record<string, string>>({});
  const [allUniverse, setAll] = useState(true);
  const [universe, setUniverse] = useState<string[]>([]);
  const bench = defaultBenchmark(assets.data);
  const [f, setF] = useState({
    name: "", benchmark: "", start: "", end: "", capital: 1_000_000, commission: 5, slippage: 5, execution: "next_open",
    train_end: "", validation_end: "", objective: "sharpe_ratio", notes: "", train_days: 504, test_days: 126, anchored: false,
  });
  useEffect(() => {
    if (!strat) return;
    setValues(Object.fromEntries(strat.params.map((p) => [p.name, p.default])));
    setGrid({});
  }, [strat]);
  useEffect(() => {
    if (dataset && !f.start) {
      const s = new Date(dataset.start_date!); s.setFullYear(s.getFullYear() + 1);
      setF((x) => ({ ...x, start: s.toISOString().slice(0, 10), end: dataset.end_date!, train_end: mode === "backtest" ? "" : "" }));
    }
  }, [dataset, f.start, mode]);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));

  const parseGrid = (): Record<string, unknown[]> => {
    const out: Record<string, unknown[]> = {};
    for (const [k, txt] of Object.entries(grid)) {
      if (!txt.trim()) continue;
      const p = strat?.params.find((x) => x.name === k);
      out[k] = txt.split(",").map((s) => s.trim()).filter(Boolean).map((s) => (p?.type === "int" ? parseInt(s, 10) : p?.type === "float" ? parseFloat(s) : p?.type === "bool" ? s === "true" : s));
    }
    return out;
  };
  const gridCombos = Object.values(parseGrid()).reduce((n, v) => n * v.length, 1);

  const submit = async () => {
    const g = parseGrid();
    const common = {
      name: f.name || undefined, dataset_id: datasetId, strategy: key, params: values, universe: allUniverse ? null : universe,
      benchmark_symbol: f.benchmark || bench || null, start_date: f.start, end_date: f.end, initial_capital: f.capital,
      commission_bps: f.commission, slippage_bps: f.slippage, execution: f.execution, objective: f.objective, notes: f.notes || undefined,
    };
    const body = mode === "backtest"
      ? { ...common, train_end: f.train_end || null, validation_end: f.validation_end || null, param_grid: Object.keys(g).length ? g : null }
      : { ...common, param_grid: g, train_days: f.train_days, test_days: f.test_days, anchored: f.anchored };
    const j = await job.run(mode === "backtest" ? "/backtests" : "/backtests/walk-forward", body);
    if (j?.status === "succeeded") onDone(j.result);
  };

  return (
    <div className="grid g2">
      <Card title="Strategy">
        <div className="stack">
          <Field label="Strategy">
            <select className="input" value={key} onChange={(e) => setKey(e.target.value)}>
              {(strategies.data ?? []).map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
            </select>
          </Field>
          {strat && <div className="small text2">{strat.description}</div>}
          <table className="dt">
            <thead><tr><th>Parameter</th><th>Value</th><th>Grid values <InfoTip text={mode === "backtest" ? "Comma-separated candidates. The best combination is chosen on the in-sample segment only (requires a train end date), then frozen for validation and out-of-sample." : "Comma-separated candidates re-selected on each fold's training window."} /></th></tr></thead>
            <tbody>
              {strat?.params.map((p) => (
                <tr key={p.name}>
                  <td><span className="mono">{p.name}</span> <InfoTip text={`${p.description}${p.minimum != null ? ` (range ${p.minimum}–${p.maximum})` : ""}`} /></td>
                  <td>
                    {p.choices ? (
                      <select className="input sm" value={String(values[p.name])} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}>{p.choices.map((c) => <option key={String(c)}>{String(c)}</option>)}</select>
                    ) : p.type === "bool" ? (
                      <input type="checkbox" checked={Boolean(values[p.name])} onChange={(e) => setValues({ ...values, [p.name]: e.target.checked })} />
                    ) : (
                      <input className="input sm num" type="number" step={p.type === "float" ? 0.05 : 1} min={p.minimum ?? undefined} max={p.maximum ?? undefined} value={String(values[p.name] ?? "")}
                        onChange={(e) => setValues({ ...values, [p.name]: p.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value) })} style={{ width: 110 }} />
                    )}
                  </td>
                  <td>{(p.type === "int" || p.type === "float") && <input className="input sm mono" placeholder="e.g. 63,126,252" value={grid[p.name] ?? ""} onChange={(e) => setGrid({ ...grid, [p.name]: e.target.value })} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {gridCombos > 1 && <div className="xs muted">{gridCombos} parameter combinations (maximum 36).</div>}
          <Field label="Universe">
            <label className="check"><input type="checkbox" checked={allUniverse} onChange={(e) => setAll(e.target.checked)} /> All tradable (non-benchmark) assets in the dataset</label>
          </Field>
          {!allUniverse && <AssetChecklist assets={assets.data ?? []} value={universe} onChange={setUniverse} includeBenchmarks={false} height={200} />}
        </div>
      </Card>
      <Card title={mode === "backtest" ? "Simulation" : "Walk-forward"}>
        <div className="stack">
          <div className="form-grid">
            <Field label="Name"><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="optional" /></Field>
            <Field label="Benchmark"><AssetSelect assets={assets.data ?? []} value={f.benchmark || bench} onChange={(v) => set("benchmark", v)} benchmarksOnly /></Field>
            <Field label="Start"><input className="input" type="date" value={f.start} onChange={(e) => set("start", e.target.value)} /></Field>
            <Field label="End"><input className="input" type="date" value={f.end} onChange={(e) => set("end", e.target.value)} /></Field>
            <Field label="Initial capital"><input className="input" type="number" value={f.capital} onChange={(e) => set("capital", Number(e.target.value))} /></Field>
            <Field label="Execution" hint="signals always use the prior close">
              <select className="input" value={f.execution} onChange={(e) => set("execution", e.target.value)}><option value="next_open">Next open</option><option value="next_close">Next close</option></select>
            </Field>
            <Field label="Commission (bps)" metric="transaction_costs"><input className="input" type="number" min={0} max={500} step={0.5} value={f.commission} onChange={(e) => set("commission", Number(e.target.value))} /></Field>
            <Field label="Slippage (bps)"><input className="input" type="number" min={0} max={500} step={0.5} value={f.slippage} onChange={(e) => set("slippage", Number(e.target.value))} /></Field>
            <Field label="Selection objective"><select className="input" value={f.objective} onChange={(e) => set("objective", e.target.value)}>{["sharpe_ratio", "annualized_return", "calmar_ratio", "sortino_ratio"].map((o) => <option key={o} value={o}>{label(o)}</option>)}</select></Field>
          </div>
          {mode === "backtest" ? (
            <>
              <div className="field-label">Chronological evaluation segments <InfoTip text="In-sample: start → train end. Validation: train end → validation end. Out-of-sample: validation end → end. Leave blank for a single full-period evaluation." /></div>
              <div className="form-grid">
                <Field label="Train (in-sample) end"><input className="input" type="date" value={f.train_end} onChange={(e) => set("train_end", e.target.value)} /></Field>
                <Field label="Validation end"><input className="input" type="date" value={f.validation_end} onChange={(e) => set("validation_end", e.target.value)} disabled={!f.train_end} /></Field>
              </div>
            </>
          ) : (
            <div className="form-grid">
              <Field label="Train window (days)"><input className="input" type="number" min={126} value={f.train_days} onChange={(e) => set("train_days", Number(e.target.value))} /></Field>
              <Field label="Test window (days)"><input className="input" type="number" min={21} value={f.test_days} onChange={(e) => set("test_days", Number(e.target.value))} /></Field>
              <Field label="Window type"><label className="check"><input type="checkbox" checked={f.anchored} onChange={(e) => set("anchored", e.target.checked)} /> Anchored (expanding)</label></Field>
            </div>
          )}
          <Field label="Notes"><textarea className="input" rows={2} value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
          <div className="form-actions">
            <button className="btn primary" onClick={submit} disabled={job.running || !strat || (mode === "walk_forward" && gridCombos < 2)}>
              {job.running ? "Running…" : mode === "backtest" ? "Run backtest" : "Run walk-forward"}
            </button>
            {mode === "walk_forward" && gridCombos < 2 && <span className="xs muted">Enter at least two grid values to re-select per fold.</span>}
          </div>
          <JobStatus job={job.job} error={job.error} running={job.running} />
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- results

function segmentShapes(segs: AnyObj[], theme: "light" | "dark"): Partial<Shape>[] {
  const fills: Record<string, string> = { validation: theme === "dark" ? "rgba(201,133,0,0.10)" : "rgba(237,161,0,0.08)", out_of_sample: theme === "dark" ? "rgba(25,158,112,0.10)" : "rgba(27,175,122,0.08)" };
  return segs.filter((s) => fills[s.name]).map((s) => ({ type: "rect", xref: "x", yref: "paper", x0: s.start, x1: s.end, y0: 0, y1: 1, fillcolor: fills[s.name], line: { width: 0 }, layer: "below" }));
}

function Result({ id }: { id: number }) {
  const theme = useResolvedTheme();
  const q = useQuery({ queryKey: ["backtest", id], queryFn: () => api.get<AnyObj>(`/backtests/${id}`), staleTime: Infinity });
  const trades = useQuery({ queryKey: ["backtest-trades", id], queryFn: () => api.get<AnyObj>(`/backtests/${id}/trades`, { limit: 50000 }), staleTime: Infinity });
  const repro = useJobRunner([["experiments"]]);
  return (
    <QueryView q={q} label="Loading backtest">
      {(b) => {
        const s = b.series, m = b.metrics.full, segs = b.metrics.segments ?? {};
        const shapes = segmentShapes(s.segments ?? [], theme);
        const equity: Data[] = [line(s.dates, s.equity, "Net equity", seriesColor(theme, 0)), line(s.dates, s.gross_equity, "Gross equity (before costs)", seriesColor(theme, 1), { line: { width: 1.5, dash: "dot", color: seriesColor(theme, 1) } })];
        if (s.benchmark_equity) equity.push(line(s.dates, s.benchmark_equity, `Benchmark ${b.benchmark_symbol}`, INK[theme].neutral));
        const cumCosts: number[] = [];
        s.costs.reduce((acc: number, v: number | null) => { const n = acc + (v ?? 0); cumCosts.push(n); return n; }, 0);
        const years = Array.from(new Set((s.monthly_returns as AnyObj[]).map((r) => r.year))).sort();
        const z = years.map((y) => Array.from({ length: 12 }, (_, i) => (s.monthly_returns as AnyObj[]).find((r) => r.year === y && r.month === i + 1)?.return ?? null));
        const lim = Math.max(0.05, ...z.flat().filter((v): v is number => v != null).map(Math.abs));
        return (
          <div className="stack">
            <Card title={<>{b.name} <span className="badge">{b.experiment_code}</span></>} sub={`${b.strategy_key} · ${b.start_date} → ${b.end_date} · ${b.dataset_version}`}
              actions={<>
                <button className="btn sm" onClick={() => repro.run(`/experiments/${b.experiment_id}/reproduce`, {})} disabled={repro.running}>{repro.running ? "Reproducing…" : "Reproduce"}</button>
                <button className="btn sm" onClick={() => download(buildUrl(`/exports/backtests/${id}/trades`))}>Trades CSV</button>
                <button className="btn sm" onClick={() => download(buildUrl(`/exports/backtests/${id}/results`))}>Daily CSV</button>
                <button className="btn sm" onClick={() => download(buildUrl(`/exports/backtests/${id}/results`, { format: "json" }))}>JSON</button>
              </>}>
              <div className="stack" style={{ gap: 6 }}>
                <div className="small"><b>Parameters used:</b> <code>{JSON.stringify(b.metrics.selected_params)}</code></div>
                <div className="small text2">Execution {b.execution.replace("_", " ")} · commission {b.config.commission_bps} bp + slippage {b.config.slippage_bps} bp · capital {money(b.config.initial_capital)} · universe {b.config.universe ? `${b.config.universe.length} assets` : "all tradable"}</div>
                {(s.notes ?? []).map((n: string) => <div key={n} className="xs muted">• {n}</div>)}
                {repro.job?.status === "succeeded" && repro.job.result && (
                  <div className={`banner ${repro.job.result.reproducibility.matches ? "info" : "warn"} small`}>
                    Reproduction {repro.job.result.experiment_code}: {repro.job.result.reproducibility.matches ? "all" : "not all"} {repro.job.result.reproducibility.metrics_compared} metrics match
                    (max |Δ| = {repro.job.result.reproducibility.max_abs_difference?.toExponential(2) ?? "n/a"}); dataset {repro.job.result.reproducibility.dataset_unchanged ? "unchanged" : "changed since original run"}.
                  </div>
                )}
                <JobStatus job={repro.job?.status === "succeeded" ? null : repro.job} error={repro.error} running={repro.running} />
              </div>
            </Card>
            <MetricStrip summary={m} keys={["cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "sortino_ratio", "max_drawdown", "calmar_ratio", "win_rate", "profit_factor", "annualized_turnover", "total_transaction_costs", "number_of_trades"]} />
            <div className="grid g2">
              <Card title="Net vs gross" sub="gross adds back each day's costs" flush>
                <MetricCompare columns={[{ name: "Net of costs", values: m }, { name: "Gross", values: { cumulative_return: m.gross_cumulative_return, annualized_return: m.gross_annualized_return, sharpe_ratio: m.gross_sharpe_ratio } }, { name: "Benchmark", values: { cumulative_return: m.benchmark_cumulative_return, annualized_return: m.benchmark_annualized_return, sharpe_ratio: m.benchmark_sharpe_ratio, max_drawdown: m.benchmark_max_drawdown } }]}
                  keys={["cumulative_return", "annualized_return", "sharpe_ratio", "max_drawdown"]} />
                <div className="small text2" style={{ padding: "8px 14px" }}>Annual cost drag {spct(-m.annual_cost_drag)} · costs {pct(m.transaction_costs_pct_initial)} of initial capital · average gross exposure {pct(m.average_gross_exposure)} · {m.rebalance_count} executions</div>
              </Card>
              {Object.keys(segs).length > 0 ? (
                <Card title="Chronological segments" sub="shaded on charts: validation (amber), out-of-sample (green)" flush>
                  <MetricCompare columns={Object.entries(segs).map(([k, v]) => ({ name: `${k.replace(/_/g, " ")} (${(v as AnyObj).start_date?.slice(0, 7)}→${(v as AnyObj).end_date?.slice(0, 7)})`, values: v as AnyObj }))}
                    keys={["cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "max_drawdown", "benchmark_cumulative_return", "transaction_costs"]} />
                </Card>
              ) : <Card title="Chronological segments"><div className="small muted">Single full-period evaluation (no train/validation split configured).</div></Card>}
            </div>
            {b.metrics.parameter_search && (
              <Card title="In-sample parameter search" sub={`selected on ${b.metrics.parameter_search.selection_window.join(" → ")} by ${label(b.metrics.parameter_search.objective)}`} flush>
                <DataTable<AnyObj> rows={b.metrics.parameter_search.results} filterable={false} initialSort={{ key: b.metrics.parameter_search.objective, dir: "desc" }}
                  selected={(r) => JSON.stringify(r.params) === JSON.stringify(b.metrics.parameter_search.best_params)} columns={[
                    { key: "params", label: "Parameters", value: (r) => JSON.stringify(r.params), render: (r) => <code>{JSON.stringify(r.params)}</code> },
                    ...["sharpe_ratio", "annualized_return", "annualized_volatility", "max_drawdown", "calmar_ratio"].map((k) => ({ key: k, label: label(k), align: "right" as const, render: (r: AnyObj) => (k.includes("ratio") ? num(r[k], 3) : k === "annualized_volatility" ? pct(r[k]) : spct(r[k])) })),
                  ]} />
              </Card>
            )}
            <Card title="Equity curve">
              <Plot height={320} data={equity} layout={{ xaxis: dateAxis(theme), yaxis: { tickprefix: "$", tickformat: ",.3s" }, shapes }} />
            </Card>
            <div className="grid g2">
              <Card title="Drawdown (net)"><Plot height={230} data={[line(s.dates, s.drawdown, "Drawdown", seriesColor(theme, 7), { fill: "tozeroy", fillcolor: "rgba(227,73,72,0.14)" })]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false, shapes }} /></Card>
              <Card title="Monthly net returns">
                <Plot height={230} data={[{ type: "heatmap", z, x: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], y: years.map(String), zmin: -lim, zmax: lim, colorscale: diverging(theme), reversescale: true,
                  text: z.map((r) => r.map((v) => (v == null ? "" : (v * 100).toFixed(1)))) as never, texttemplate: "%{text}", textfont: { size: 9 }, hovertemplate: "%{y} %{x}: %{z:.2%}<extra></extra>", xgap: 2, ygap: 2, colorbar: { thickness: 10, tickformat: ".0%" } }]}
                  layout={{ hovermode: "closest", yaxis: { autorange: "reversed", type: "category" }, margin: { l: 44, r: 10, t: 8, b: 30 } }} />
              </Card>
              <Card title="Rolling 126-day Sharpe (net)"><Plot height={220} data={[line(s.dates, s.rolling_sharpe_126, "Sharpe", seriesColor(theme, 0))]} layout={{ xaxis: dateAxis(theme, false), yaxis: { hoverformat: ".2f" }, showlegend: false, shapes }} /></Card>
              <Card title="Rolling 63-day volatility (net)"><Plot height={220} data={[line(s.dates, s.rolling_vol_63, "Volatility", seriesColor(theme, 0))]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false }} /></Card>
              <Card title="Exposure" sub="share of equity invested"><Plot height={220} data={[line(s.dates, s.gross_exposure, "Gross", seriesColor(theme, 0)), line(s.dates, s.net_exposure, "Net", seriesColor(theme, 1))]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS }} /></Card>
              <Card title="Cumulative transaction costs"><Plot height={220} data={[line(s.dates, cumCosts, "Costs", seriesColor(theme, 1), { fill: "tozeroy", fillcolor: "rgba(235,104,52,0.12)" })]} layout={{ xaxis: dateAxis(theme, false), yaxis: { tickprefix: "$", tickformat: ",.2s" }, showlegend: false }} /></Card>
            </div>
            <QueryView q={trades} label="Loading trade log">
              {(t) => <><Trades rows={t.trades} /><Inspector id={id} symbols={Array.from(new Set((t.trades as AnyObj[]).map((x) => x.symbol))).sort()} /></>}
            </QueryView>
            {s.signal_log?.length > 0 && (
              <Card title="Signal log" sub="last 400 signal events recorded by the strategy" flush>
                <DataTable<AnyObj> rows={[...s.signal_log].reverse()} pageSize={10} columns={[
                  { key: "date", label: "Signal date" },
                  { key: "detail", label: "Detail", wrap: true, value: (r) => JSON.stringify(r), render: (r) => <code className="xs">{JSON.stringify({ ...r, date: undefined })}</code> },
                ]} />
              </Card>
            )}
            <Disclaimer>Historical simulation on {b.dataset_version}. Survivorship, market impact, borrow costs and taxes are not modelled.</Disclaimer>
          </div>
        );
      }}
    </QueryView>
  );
}

function Trades({ rows }: { rows: AnyObj[] }) {
  const [side, setSide] = useState("");
  const filtered = useMemo(() => (side ? rows.filter((r) => r.side === side) : rows), [rows, side]);
  return (
    <Card title="Trade log" sub={`${int(rows.length)} fills`} flush>
      <DataTable<AnyObj> rows={filtered} exportName="trades" pageSize={15}
        toolbar={<select className="input sm" style={{ width: 110 }} value={side} onChange={(e) => setSide(e.target.value)} aria-label="Side"><option value="">all sides</option><option>buy</option><option>sell</option><option>delist</option></select>}
        columns={[
          { key: "signal_date", label: "Signal" }, { key: "date", label: "Executed" },
          { key: "symbol", label: "Symbol", render: (r) => <b className="mono">{r.symbol}</b> },
          { key: "side", label: "Side", render: (r) => <span className={r.side === "buy" ? "pos" : r.side === "sell" ? "neg" : "muted"}>{r.side}</span> },
          { key: "shares", label: "Shares", align: "right", render: (r) => num(r.shares, 2) },
          { key: "price", label: "Fill price", align: "right", render: (r) => num(r.price, 4) },
          { key: "notional", label: "Notional", align: "right", render: (r) => money(r.notional) },
          { key: "commission", label: "Commission", align: "right", render: (r) => money(r.commission, 2) },
          { key: "slippage", label: "Slippage", align: "right", render: (r) => money(r.slippage, 2) },
          { key: "weight_before", label: "Wt before", align: "right", render: (r) => pct(r.weight_before) },
          { key: "weight_after", label: "Target wt", align: "right", render: (r) => pct(r.weight_after) },
        ]} />
    </Card>
  );
}

function Inspector({ id, symbols }: { id: number; symbols: string[] }) {
  const theme = useResolvedTheme();
  const [sym, setSym] = useState(symbols[0] ?? "");
  const q = useQuery({ queryKey: ["bt-diag", id, sym], queryFn: () => api.get<AnyObj>(`/backtests/${id}/diagnostics/${sym}`), enabled: !!sym, staleTime: Infinity });
  if (!symbols.length) return null;
  return (
    <Card title="Signal inspector" sub="price, the strategy's indicator and executed trades for one asset"
      actions={<select className="input sm" style={{ width: 120 }} value={sym} onChange={(e) => setSym(e.target.value)} aria-label="Symbol">{symbols.map((s) => <option key={s}>{s}</option>)}</select>}>
      <QueryView q={q}>
        {(d) => {
          const buys = d.trades.filter((t: AnyObj) => t.side === "buy"), sells = d.trades.filter((t: AnyObj) => t.side !== "buy");
          const ind = d.indicator;
          const priceData: Data[] = [
            line(d.dates, d.price, `${d.symbol} (adj. close)`, INK[theme].neutral),
            { type: "scatter", mode: "markers", x: buys.map((t: AnyObj) => t.date), y: buys.map((t: AnyObj) => t.price), name: "Buy fill", marker: { symbol: "triangle-up", size: 10, color: seriesColor(theme, 2), line: { width: 1.5, color: INK[theme].surface } } },
            { type: "scatter", mode: "markers", x: sells.map((t: AnyObj) => t.date), y: sells.map((t: AnyObj) => t.price), name: "Sell fill", marker: { symbol: "triangle-down", size: 10, color: seriesColor(theme, 7), line: { width: 1.5, color: INK[theme].surface } } },
          ];
          if (ind?.overlay) priceData.push(line(d.dates, ind.values, ind.name, seriesColor(theme, 0)));
          return (
            <div className="stack">
              <Plot height={260} data={priceData} layout={{ xaxis: dateAxis(theme), yaxis: { title: { text: "Price" } } }} />
              {ind && !ind.overlay && (
                <Plot height={180} data={[line(d.dates, ind.values, ind.name, seriesColor(theme, 0))]} layout={{
                  xaxis: dateAxis(theme, false), yaxis: { title: { text: ind.name }, hoverformat: ".3f" }, showlegend: false,
                  shapes: ind.thresholds ? [ind.thresholds.entry, ind.thresholds.exit].map((y: number, i: number) => ({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y, line: { dash: "dot", width: 1.5, color: i ? seriesColor(theme, 2) : seriesColor(theme, 7) } })) : [],
                }} />
              )}
              {ind?.thresholds && <div className="xs muted">Entry at z ≤ {ind.thresholds.entry}, exit at z ≥ {ind.thresholds.exit} (long side).</div>}
              <Plot height={140} data={[{ type: "scatter", mode: "lines", line: { shape: "hv", width: 2, color: seriesColor(theme, 0) }, x: d.target_weights.map((w: AnyObj) => w.date), y: d.target_weights.map((w: AnyObj) => w.weight), name: "Target weight" }]}
                layout={{ xaxis: dateAxis(theme, false), yaxis: { tickformat: ".1%", title: { text: "Target weight" } }, showlegend: false }} />
            </div>
          );
        }}
      </QueryView>
    </Card>
  );
}

// ---------------------------------------------------------------- walk-forward

function WalkForward() {
  const theme = useResolvedTheme();
  const [params, setParams] = useSearchParams();
  const exps = useExperiments("walk_forward");
  const wid = params.get("wf") ? Number(params.get("wf")) : exps.data?.[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["experiment", wid], queryFn: () => api.get<AnyObj>(`/experiments/${wid}`), enabled: wid != null, staleTime: Infinity });
  return (
    <div className="stack">
      <Config mode="walk_forward" onDone={(r) => { if (r?.experiment_id) setParams({ wf: String(r.experiment_id) }); }} />
      <Card title="Walk-forward experiments" flush>
        <QueryView q={exps}>
          {(rows) => (
            <DataTable<AnyObj> rows={rows} pageSize={6} onRowClick={(r) => setParams({ wf: String(r.id) })} selected={(r) => r.id === wid} columns={[
              { key: "code", label: "Code", render: (r) => <b className="mono">{r.code}</b> }, { key: "name", label: "Name" },
              { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
              { key: "folds", label: "Folds", align: "right", value: (r) => r.summary?.folds },
              { key: "oos", label: "OOS Sharpe", align: "right", value: (r) => r.summary?.aggregate_sharpe, render: (r) => num(r.summary?.aggregate_sharpe, 2) },
              { key: "is", label: "Mean IS objective", align: "right", value: (r) => r.summary?.mean_in_sample_objective, render: (r) => num(r.summary?.mean_in_sample_objective, 2) },
              { key: "duration_seconds", label: "Duration", align: "right", render: (r) => secs(r.duration_seconds) },
              { key: "created_at", label: "Run", render: (r) => dt(r.created_at) },
            ]} empty="No walk-forward runs yet." />
          )}
        </QueryView>
      </Card>
      {wid && (
        <QueryView q={detail}>
          {(e) => {
            const a = e.artifacts;
            return (
              <>
                <MetricStrip summary={a.aggregate_out_of_sample} keys={["cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "max_drawdown", "calmar_ratio"]} />
                <div className="banner neutral small">
                  Mean in-sample objective {num(a.mean_in_sample_objective, 2)} vs mean out-of-sample Sharpe {num(a.mean_out_of_sample_sharpe, 2)} — the gap is a measure of overfitting.
                  Aggregates are computed from the stitched fold test returns only. {a.anchored ? "Anchored (expanding) training windows." : "Rolling training windows."}
                </div>
                <div className="grid g2">
                  <Card title="Stitched out-of-sample equity"><Plot height={260} data={[line(a.series.dates, a.series.equity, "OOS equity", seriesColor(theme, 0))]} layout={{ xaxis: dateAxis(theme), yaxis: { tickprefix: "$", tickformat: ",.3s" }, showlegend: false }} /></Card>
                  <Card title="In-sample objective vs out-of-sample Sharpe by fold">
                    <Plot height={260} data={[
                      { type: "bar", name: "In-sample objective", x: a.folds.map((f: AnyObj) => `F${f.fold}`), y: a.folds.map((f: AnyObj) => f.in_sample_objective), marker: { color: seriesColor(theme, 0) } },
                      { type: "bar", name: "Out-of-sample Sharpe", x: a.folds.map((f: AnyObj) => `F${f.fold}`), y: a.folds.map((f: AnyObj) => f.test_sharpe_ratio), marker: { color: seriesColor(theme, 1) } },
                    ]} layout={{ barmode: "group", bargap: 0.3, yaxis: { hoverformat: ".2f" } }} />
                  </Card>
                </div>
                <Card title="Folds" sub="parameters selected on each training window, then frozen for its test window" flush>
                  <DataTable<AnyObj> rows={a.folds} filterable={false} exportName={`${e.code}-folds`} columns={[
                    { key: "fold", label: "Fold", align: "right" },
                    { key: "train", label: "Train", value: (f) => `${f.train_start} → ${f.train_end}` },
                    { key: "test", label: "Test", value: (f) => `${f.test_start} → ${f.test_end}` },
                    { key: "selected_params", label: "Selected params", value: (f) => JSON.stringify(f.selected_params), render: (f) => <code>{JSON.stringify(f.selected_params)}</code> },
                    { key: "in_sample_objective", label: "IS objective", align: "right", render: (f) => num(f.in_sample_objective, 2) },
                    { key: "test_sharpe_ratio", label: "OOS Sharpe", align: "right", render: (f) => num(f.test_sharpe_ratio, 2) },
                    { key: "test_cumulative_return", label: "OOS return", align: "right", render: (f) => <span className={tone(f.test_cumulative_return)}>{spct(f.test_cumulative_return)}</span> },
                    { key: "test_max_drawdown", label: "OOS max DD", align: "right", render: (f) => spct(f.test_max_drawdown) },
                    { key: "test_costs", label: "Costs", align: "right", render: (f) => money(f.test_costs) },
                  ]} />
                </Card>
                <div className="small text2">Parameter stability: {Object.entries(a.parameter_stability as AnyObj).map(([k, v]) => `${k}: ${Object.entries(v as AnyObj).map(([val, n]) => `${val}×${n}`).join(", ")}`).join(" · ")} · <Link to={`/experiments?id=${e.id}`}>open in registry</Link></div>
              </>
            );
          }}
        </QueryView>
      )}
    </div>
  );
}
