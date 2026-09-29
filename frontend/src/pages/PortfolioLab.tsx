import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Disclaimer, download, Field, InfoTip, Kpi, PageHead, QueryView } from "../components/ui";
import { AssetChecklist, AssetSelect, DateRange, PortfolioSelect, defaultBenchmark } from "../components/pickers";
import { dateAxis, INK, seriesColor } from "../components/charts";
import { line, MetricCompare, MetricStrip, PCT_AXIS } from "../components/metrics";
import { useAssets, usePortfolios } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, buildUrl, errorMessage } from "../services/api";
import type { AnyObj, Portfolio } from "../types/api";
import { dt, money, num, pct, spct, tone } from "../utils/format";

const METHODS = [
  { value: "equal_weight", label: "Equal weight" },
  { value: "custom", label: "Custom weights" },
  { value: "inverse_volatility", label: "Inverse volatility" },
  { value: "min_variance", label: "Minimum variance" },
  { value: "max_sharpe", label: "Maximum Sharpe (in-sample)" },
  { value: "risk_parity", label: "Risk parity (equal risk contribution)" },
];

interface Form {
  name: string; symbols: string[]; allocation_method: string; weights: Record<string, number>; min_weight: number; max_weight: number;
  estimation_start: string; estimation_end: string; benchmark_symbol: string; initial_capital: number; rebalance_frequency: string; notes: string;
}

const emptyForm = (bench: string): Form => ({
  name: "", symbols: [], allocation_method: "equal_weight", weights: {}, min_weight: 0, max_weight: 1, estimation_start: "", estimation_end: "",
  benchmark_symbol: bench, initial_capital: 1_000_000, rebalance_frequency: "monthly", notes: "",
});

export default function PortfolioLab() {
  const { datasetId, portfolioId, setPortfolioId, settings } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const ports = usePortfolios(datasetId);
  const assets = useAssets(datasetId);
  const bench = defaultBenchmark(assets.data);
  const urlId = params.get("id") ? Number(params.get("id")) : null;
  const selectedId = urlId ?? portfolioId ?? ports.data?.[0]?.id ?? null;
  const select = (id: number | null) => { setPortfolioId(id); setParams(id ? { id: String(id) } : {}); };
  const selected = ports.data?.find((p) => p.id === selectedId) ?? null;
  const [editing, setEditing] = useState<"new" | "edit" | null>(null);

  return (
    <>
      <PageHead title="Portfolio Lab" desc="Construct research portfolios, choose an allocation method, and analyse historical performance, risk and benchmark-relative behaviour."
        actions={<button className="btn primary" onClick={() => setEditing("new")}>New portfolio</button>} />
      <div className="grid g-side">
        <div className="stack">
          <Card title="Portfolios" flush>
            <QueryView q={ports}>
              {(rows) => (
                <DataTable<Portfolio> rows={rows} filterable={rows.length > 8} pageSize={12} onRowClick={(p) => { select(p.id); setEditing(null); }} selected={(p) => p.id === selectedId}
                  columns={[
                    { key: "name", label: "Name", render: (p) => <b>{p.name}</b> },
                    { key: "allocation_method", label: "Method", render: (p) => <span className="small text2">{p.allocation_method.replace("_", " ")}</span> },
                    { key: "n", label: "Assets", align: "right", value: (p) => p.positions.length },
                  ]} empty="No portfolios yet — create one." />
              )}
            </QueryView>
          </Card>
          {editing && (
            <Builder key={`${editing}-${selectedId}`} mode={editing} existing={editing === "edit" ? selected : null} bench={bench} datasetId={datasetId!} assets={assets.data ?? []}
              onDone={(id) => { setEditing(null); if (id) select(id); }} />
          )}
        </div>
        <div className="stack">
          {selected ? <Analytics p={selected} rf={settings.riskFreeRate} window_={settings.rollingWindow} onEdit={() => setEditing("edit")} onDeleted={() => select(null)} others={ports.data ?? []} />
            : <Card title="No portfolio selected"><p className="text2">Create or select a portfolio to see its analytics.</p></Card>}
        </div>
      </div>
    </>
  );
}

function Builder({ mode, existing, bench, datasetId, assets, onDone }: {
  mode: "new" | "edit"; existing: Portfolio | null; bench: string; datasetId: number; assets: import("../types/api").Asset[]; onDone: (id?: number) => void;
}) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(() => existing ? {
    name: existing.name, symbols: existing.positions.map((p) => p.symbol), allocation_method: existing.allocation_method,
    weights: Object.fromEntries(existing.positions.map((p) => [p.symbol, p.weight])), min_weight: existing.constraints?.min_weight ?? 0,
    max_weight: existing.constraints?.max_weight ?? 1, estimation_start: existing.constraints?.estimation_start ?? "", estimation_end: existing.constraints?.estimation_end ?? "",
    benchmark_symbol: existing.benchmark_symbol, initial_capital: existing.initial_capital, rebalance_frequency: existing.rebalance_frequency, notes: existing.notes ?? "",
  } : emptyForm(bench));
  const [preview, setPreview] = useState<AnyObj | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => { setF((x) => ({ ...x, [k]: v })); setPreview(null); };
  const benchmark = f.benchmark_symbol || bench; // assets may load after the form is initialised
  const weightSum = f.symbols.reduce((s, x) => s + (Number(f.weights[x]) || 0), 0);

  const payload = () => ({
    ...f, benchmark_symbol: benchmark, dataset_id: datasetId, weights: f.allocation_method === "custom" ? Object.fromEntries(f.symbols.map((s) => [s, Number(f.weights[s]) || 0])) : null,
    estimation_start: f.estimation_start || null, estimation_end: f.estimation_end || null, notes: f.notes || null, name: f.name || "Untitled",
  });
  const doPreview = async () => {
    setErr(null); setBusy(true);
    try { setPreview(await api.post<AnyObj>("/portfolios/preview-allocation", payload())); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const save = async () => {
    setErr(null); setBusy(true);
    try {
      const p = mode === "new" ? await api.post<Portfolio>("/portfolios", payload()) : await api.put<Portfolio>(`/portfolios/${existing!.id}`, payload());
      qc.invalidateQueries({ queryKey: ["portfolios"] });
      qc.invalidateQueries({ queryKey: ["portfolio-analytics"] });
      onDone(p.id);
    } catch (e) { setErr(errorMessage(e)); qc.invalidateQueries({ queryKey: ["notifications"] }); } finally { setBusy(false); }
  };

  return (
    <Card title={mode === "new" ? "New portfolio" : `Edit ${existing?.name}`} actions={<button className="btn sm ghost" onClick={() => onDone()}>Cancel</button>}>
      <div className="stack">
        <Field label="Name"><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} maxLength={120} /></Field>
        <Field label="Assets"><AssetChecklist assets={assets} value={f.symbols} onChange={(v) => set("symbols", v)} /></Field>
        <Field label="Allocation method">
          <select className="input" value={f.allocation_method} onChange={(e) => set("allocation_method", e.target.value)}>
            {METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </Field>
        {f.allocation_method === "custom" && f.symbols.length > 0 && (
          <div className="stack" style={{ gap: 4 }}>
            <div className="row-between small"><span className="text2">Weights (decimal)</span>
              <span className={Math.abs(weightSum - 1) > 1e-4 ? "neg" : "pos"}>Σ = {num(weightSum, 4)}</span></div>
            <div className="checklist" style={{ maxHeight: 200 }}>
              {f.symbols.map((s) => (
                <label key={s} style={{ justifyContent: "space-between" }}>
                  <span className="mono">{s}</span>
                  <input className="input sm num" style={{ width: 90, textAlign: "right" }} type="number" step="0.01" min={0} max={1}
                    value={f.weights[s] ?? ""} onChange={(e) => set("weights", { ...f.weights, [s]: e.target.value === "" ? NaN : Number(e.target.value) })} />
                </label>
              ))}
            </div>
            <button className="btn sm" type="button" onClick={() => set("weights", Object.fromEntries(f.symbols.map((s) => [s, +(1 / f.symbols.length).toFixed(6)])))}>Fill equal</button>
          </div>
        )}
        <div className="form-grid">
          <Field label="Min weight"><input className="input" type="number" step="0.01" min={0} max={1} value={f.min_weight} onChange={(e) => set("min_weight", Number(e.target.value))} /></Field>
          <Field label="Max weight"><input className="input" type="number" step="0.01" min={0} max={1} value={f.max_weight} onChange={(e) => set("max_weight", Number(e.target.value))} /></Field>
          <Field label="Benchmark"><AssetSelect assets={assets} value={benchmark} onChange={(v) => set("benchmark_symbol", v)} benchmarksOnly /></Field>
          <Field label="Rebalance">
            <select className="input" value={f.rebalance_frequency} onChange={(e) => set("rebalance_frequency", e.target.value)}>
              {["none", "daily", "weekly", "monthly", "quarterly", "annually"].map((r) => <option key={r}>{r}</option>)}
            </select>
          </Field>
          <Field label="Initial capital"><input className="input" type="number" min={1000} value={f.initial_capital} onChange={(e) => set("initial_capital", Number(e.target.value))} /></Field>
        </div>
        {!["custom", "equal_weight"].includes(f.allocation_method) && (
          <Field label="Estimation window" hint="Optimisation uses returns in this window only (in-sample). Blank = full history.">
            <DateRange start={f.estimation_start} end={f.estimation_end} onChange={(s, e) => setF((x) => ({ ...x, estimation_start: s, estimation_end: e }))} />
          </Field>
        )}
        <Field label="Notes"><textarea className="input" rows={2} value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        {preview && (
          <div className="stack" style={{ gap: 4 }}>
            <div className="small text2">Proposed weights {preview.details.ex_ante_volatility != null && <>· ex-ante vol {pct(preview.details.ex_ante_volatility)}</>}
              {preview.details.ledoit_wolf_shrinkage != null && <> · Ledoit-Wolf shrinkage {num(preview.details.ledoit_wolf_shrinkage, 3)}</>}</div>
            <table className="dt"><tbody>
              {Object.entries(preview.weights as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([s, w]) => (
                <tr key={s}><td className="mono">{s}</td><td className="r">{pct(w)}</td><td style={{ width: "50%" }}><div className="bar-inline"><div style={{ width: `${w * 100}%` }} /></div></td></tr>
              ))}
            </tbody></table>
            {preview.details.bound_warning && <div className="banner warn small">{preview.details.bound_warning}</div>}
            <div className="xs muted">{preview.details.disclaimer}</div>
          </div>
        )}
        {err && <div className="banner error small" role="alert">{err}</div>}
        <div className="form-actions">
          <button className="btn" onClick={doPreview} disabled={busy || f.symbols.length === 0}>Preview allocation</button>
          <button className="btn primary" onClick={save} disabled={busy || f.symbols.length === 0 || !f.name}>{mode === "new" ? "Save portfolio" : "Update"}</button>
        </div>
      </div>
    </Card>
  );
}

function Analytics({ p, rf, window_, onEdit, onDeleted, others }: { p: Portfolio; rf: number; window_: number; onEdit: () => void; onDeleted: () => void; others: Portfolio[] }) {
  const theme = useResolvedTheme();
  const qc = useQueryClient();
  const [range, setRange] = useState({ start: "", end: "" });
  const [cmpId, setCmpId] = useState<number | null>(null);
  useEffect(() => setCmpId(null), [p.id]);
  const q = useQuery({
    queryKey: ["portfolio-analytics", p.id, p.updated_at, range, rf, window_],
    queryFn: () => api.get<AnyObj>(`/portfolios/${p.id}/analytics`, { start: range.start, end: range.end, risk_free_rate: rf, rolling_window: window_ }),
  });
  const cmp = useQuery({
    queryKey: ["portfolio-compare", p.id, cmpId, range],
    queryFn: () => api.get<AnyObj>("/portfolios/compare", { a: p.id, b: cmpId!, start: range.start, end: range.end }),
    enabled: cmpId != null,
  });
  const remove = async () => {
    if (!window.confirm(`Delete portfolio “${p.name}”? Its stored returns, risk metrics and stress tests are deleted too.`)) return;
    await api.del(`/portfolios/${p.id}`);
    qc.invalidateQueries({ queryKey: ["portfolios"] });
    onDeleted();
  };
  const riskByAsset = useMemo(() => Object.fromEntries(((q.data?.risk_decomposition?.assets ?? []) as AnyObj[]).map((a) => [a.symbol, a])), [q.data]);

  return (
    <>
      <div className="row-between">
        <div>
          <h2 style={{ fontSize: 16 }}>{p.name}</h2>
          <div className="small text2">{p.allocation_method.replace("_", " ")} · rebalanced {p.rebalance_frequency} · {money(p.initial_capital)} · benchmark {p.benchmark_symbol}
            {p.constraints?.estimation_end && <> · estimated {p.constraints.estimation_start ?? "start"} → {p.constraints.estimation_end}</>}</div>
        </div>
        <div className="row">
          <DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} />
          <button className="btn sm" onClick={() => download(buildUrl(`/exports/portfolios/${p.id}/metrics`, { start: range.start, end: range.end }))}>Metrics CSV</button>
          <button className="btn sm" onClick={() => download(buildUrl(`/exports/portfolios/${p.id}/metrics`, { format: "json" }))}>JSON</button>
          <button className="btn sm" onClick={() => download(buildUrl(`/exports/portfolios/${p.id}/returns`))}>Returns CSV</button>
          <button className="btn sm" onClick={onEdit}>Edit</button>
          <button className="btn sm danger" onClick={remove}>Delete</button>
        </div>
      </div>
      <QueryView q={q} label="Simulating portfolio">
        {(a) => {
          const s = a.series;
          const v95 = a.var["95"], v99 = a.var["99"];
          return (
            <div className="stack">
              {a.notes.length > 0 && <div className="banner neutral small">{a.notes.join(" ")}</div>}
              <MetricStrip summary={a.summary} keys={["cumulative_return", "annualized_return", "annualized_volatility", "downside_deviation", "sharpe_ratio", "sortino_ratio", "calmar_ratio", "max_drawdown", "beta", "tracking_error", "information_ratio", "alpha"]} />
              <div className="kpis">
                <Kpi label="VaR 95% (1d, historical)" metric="var" value={pct(v95?.historical?.var)} note={money(v95?.historical?.var_amount)} />
                <Kpi label="CVaR 95% (1d, historical)" metric="cvar" value={pct(v95?.historical?.cvar)} note={money(v95?.historical?.cvar_amount)} />
                <Kpi label="VaR 99% (1d, historical)" metric="var" value={pct(v99?.historical?.var)} note={money(v99?.historical?.var_amount)} />
                <Kpi label="CVaR 99% (1d, historical)" metric="cvar" value={pct(v99?.historical?.cvar)} note={money(v99?.historical?.cvar_amount)} />
                <Kpi label="Effective # assets" metric="herfindahl_index" value={num(a.concentration.effective_number_of_assets, 1)} note={`HHI ${num(a.concentration.herfindahl_index, 3)}`} />
                <Kpi label="Diversification ratio" metric="diversification_ratio" value={num(a.risk_decomposition.diversification_ratio, 2)} />
                <Kpi label="Wtd. avg correlation" value={num(a.correlation_exposure.weighted_avg_correlation, 2)} tip="Weight-averaged pairwise correlation of holdings (off-diagonal)." />
                <Kpi label="Annual turnover" metric="annualized_turnover" value={num(a.turnover.annualized, 2)} note={`${a.turnover.rebalances} rebalances`} />
              </div>
              <div className="grid g2">
                <Card title="Value vs benchmark" sub={`${a.period.start} → ${a.period.end}`}>
                  <Plot height={280} data={[line(s.dates, s.value, p.name, seriesColor(theme, 0)), line(s.dates, s.benchmark_value, a.benchmark.symbol, INK[theme].neutral)]}
                    layout={{ xaxis: dateAxis(theme), yaxis: { tickprefix: "$", tickformat: ",.3s" } }} />
                </Card>
                <Card title="Benchmark comparison" flush>
                  <MetricCompare columns={[{ name: p.name, values: a.summary }, { name: a.benchmark.symbol, values: a.benchmark.summary }]}
                    keys={["cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "sortino_ratio", "max_drawdown", "calmar_ratio", "skewness", "excess_kurtosis"]} />
                </Card>
                <Card title="Relative performance" sub="portfolio wealth / benchmark wealth − 1">
                  <Plot height={230} data={[line(s.dates, s.relative_performance, "Relative", seriesColor(theme, 0), { fill: "tozeroy", fillcolor: "rgba(42,120,214,0.10)" })]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false }} />
                </Card>
                <Card title="Drawdown" sub="portfolio vs benchmark">
                  <Plot height={230} data={[line(s.dates, s.drawdown, p.name, seriesColor(theme, 0)), line(s.dates, s.benchmark_drawdown, a.benchmark.symbol, INK[theme].neutral)]}
                    layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS }} />
                  <div className="xs muted">Max drawdown {spct(a.max_drawdown_details.max_drawdown)}: peak {a.max_drawdown_details.peak_date ?? "start"} → trough {a.max_drawdown_details.trough_date} → {a.max_drawdown_details.recovery_date ? `recovered ${a.max_drawdown_details.recovery_date}` : "not yet recovered"}</div>
                </Card>
                <Card title={`Rolling ${s.rolling_window}-day beta and correlation`}>
                  <Plot height={230} data={[line(s.dates, s.rolling_beta, "Beta", seriesColor(theme, 0)), line(s.dates, s.rolling_correlation, "Correlation", seriesColor(theme, 1))]} layout={{ xaxis: dateAxis(theme, false), yaxis: { hoverformat: ".2f" } }} />
                </Card>
                <Card title="Weight vs contribution to volatility" sub="Euler decomposition, full period" actions={<InfoTip metric="risk_contribution" />}>
                  <Plot height={230} data={[
                    { type: "bar", name: "Target weight", x: a.assets.map((x: AnyObj) => x.symbol), y: a.assets.map((x: AnyObj) => x.target_weight), marker: { color: seriesColor(theme, 0) } },
                    { type: "bar", name: "% of volatility", x: a.assets.map((x: AnyObj) => x.symbol), y: a.assets.map((x: AnyObj) => riskByAsset[x.symbol]?.pct_contribution), marker: { color: seriesColor(theme, 1) } },
                  ]} layout={{ barmode: "group", yaxis: { tickformat: ".0%", hoverformat: ".2%" }, bargap: 0.25, bargroupgap: 0.08 }} />
                </Card>
              </div>
              <Card title="Holdings" flush>
                <DataTable<AnyObj> rows={a.assets} exportName={`${p.name}-holdings`} filterable={false} pageSize={60} columns={[
                  { key: "symbol", label: "Symbol", render: (r) => <b className="mono">{r.symbol}</b> },
                  { key: "name", label: "Name" }, { key: "sector", label: "Sector", render: (r) => r.sector ?? "–" },
                  { key: "target_weight", label: "Target", align: "right", render: (r) => pct(r.target_weight) },
                  { key: "current_weight", label: "Current (drifted)", align: "right", render: (r) => pct(r.current_weight) },
                  { key: "cumulative_return", label: "Cumulative", align: "right", render: (r) => <span className={tone(r.cumulative_return)}>{spct(r.cumulative_return)}</span> },
                  { key: "annualized_volatility", label: "Ann. vol", align: "right", render: (r) => pct(r.annualized_volatility) },
                  { key: "beta", label: "Beta", align: "right", render: (r) => num(r.beta, 2) },
                  { key: "mcr", label: "Marginal contrib.", align: "right", value: (r) => riskByAsset[r.symbol]?.marginal_contribution, render: (r) => pct(riskByAsset[r.symbol]?.marginal_contribution) },
                  { key: "pct", label: "% of vol", align: "right", value: (r) => riskByAsset[r.symbol]?.pct_contribution, render: (r) => pct(riskByAsset[r.symbol]?.pct_contribution) },
                ]} />
              </Card>
              <div className="grid g2">
                <Card title="Sector allocation">
                  <Plot height={220} data={[{ type: "bar", orientation: "h", y: Object.keys(a.sector_weights), x: Object.values(a.sector_weights) as number[], marker: { color: seriesColor(theme, 0) }, hovertemplate: "%{y}: %{x:.1%}<extra></extra>" }]}
                    layout={{ hovermode: "closest", xaxis: { tickformat: ".0%" }, yaxis: { autorange: "reversed" }, margin: { l: 150, r: 10, t: 8, b: 30 } }} />
                </Card>
                <Card title="Allocation details" sub="stored with the portfolio">
                  <dl className="kv small">
                    <dt>Method</dt><dd>{p.allocation_details?.method}</dd>
                    <dt>Estimation</dt><dd>{p.allocation_details?.estimation_start ?? "–"} → {p.allocation_details?.estimation_end ?? "–"} ({p.allocation_details?.observations} obs.)</dd>
                    {p.allocation_details?.ledoit_wolf_shrinkage != null && <><dt>Covariance shrinkage</dt><dd>{num(p.allocation_details.ledoit_wolf_shrinkage, 3)}</dd></>}
                    {p.allocation_details?.ex_ante_volatility != null && <><dt>Ex-ante volatility</dt><dd>{pct(p.allocation_details.ex_ante_volatility)}</dd></>}
                    {p.allocation_details?.in_sample_sharpe != null && <><dt>In-sample Sharpe</dt><dd>{num(p.allocation_details.in_sample_sharpe, 2)}</dd></>}
                    <dt>Updated</dt><dd>{dt(p.updated_at)}</dd>
                    {p.notes && <><dt>Notes</dt><dd>{p.notes}</dd></>}
                  </dl>
                  <div className="xs muted" style={{ marginTop: 8 }}>{p.allocation_details?.disclaimer}</div>
                </Card>
              </div>
            </div>
          );
        }}
      </QueryView>
      <Card title="Compare with another portfolio" actions={<div style={{ width: 240 }}><PortfolioSelect portfolios={others.filter((o) => o.id !== p.id)} value={cmpId} onChange={setCmpId} allowEmpty="Select portfolio…" /></div>}>
        {cmp.data ? (
          <div className="grid g2">
            <div className="table-wrap">
              <table className="dt">
                <thead><tr><th>Metric</th><th className="r">{cmp.data.a.name}</th><th className="r">{cmp.data.b.name}</th></tr></thead>
                <tbody>{cmp.data.metrics.map((m: AnyObj) => <tr key={m.metric}><td>{m.metric.replace(/_/g, " ")}</td><td className="r">{fmt(m.metric, m.a)}</td><td className="r">{fmt(m.metric, m.b)}</td></tr>)}</tbody>
              </table>
              <div className="xs muted" style={{ padding: 8 }}>{cmp.data.note}</div>
            </div>
            <Plot height={280} data={[line(cmp.data.series.a_dates, cmp.data.series.a_value, cmp.data.a.name, seriesColor(theme, 0)), line(cmp.data.series.b_dates, cmp.data.series.b_value, cmp.data.b.name, seriesColor(theme, 1))]}
              layout={{ xaxis: dateAxis(theme), yaxis: { tickprefix: "$", tickformat: ",.3s" } }} />
          </div>
        ) : cmp.isLoading && cmpId ? <div className="state">Comparing…</div> : <div className="small muted">Choose a second portfolio to compare metrics side by side.</div>}
      </Card>
      <Disclaimer>Allocation methods are research optimisations on historical data. Maximum-Sharpe weights depend heavily on noisy in-sample mean estimates. Not investment advice.</Disclaimer>
    </>
  );
}

function fmt(metric: string, v: number | null) {
  if (/ratio|beta/.test(metric)) return num(v, 3);
  if (/turnover/.test(metric)) return num(v, 2);
  if (/return|drawdown/.test(metric)) return spct(v);
  return pct(v);
}
