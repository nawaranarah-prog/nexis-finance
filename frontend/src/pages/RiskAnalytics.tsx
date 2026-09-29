import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, download, ErrorState, Field, InfoTip, Kpi, Loading, PageHead, QueryView, Tabs } from "../components/ui";
import { AssetChecklist, DateRange, PortfolioSelect } from "../components/pickers";
import { dateAxis, diverging, INK, seriesColor, STATUS } from "../components/charts";
import { line } from "../components/metrics";
import { useAssets, usePortfolios } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, buildUrl, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int, money, num, pct } from "../utils/format";

type Tab = "var" | "correlation" | "history";

export default function RiskAnalytics() {
  const { datasetId, portfolioId, setPortfolioId } = useWorkspace();
  const ports = usePortfolios(datasetId);
  const pid = portfolioId ?? ports.data?.[0]?.id ?? null;
  const [tab, setTab] = useState<Tab>("var");
  return (
    <>
      <PageHead title="Risk Analytics" desc="Value-at-Risk and Expected Shortfall under three methodologies, an out-of-sample VaR backtest, and correlation / dependency structure."
        actions={<div style={{ width: 260 }}><PortfolioSelect portfolios={ports.data ?? []} value={pid} onChange={setPortfolioId} /></div>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "var", label: "VaR / CVaR" }, { value: "correlation", label: "Correlation & dependency" }, { value: "history", label: "Stored risk estimates" }]} />
      {tab === "var" && (pid ? <VarPanel pid={pid} /> : <Card title="No portfolio"><p className="text2">Create a portfolio in Portfolio Lab first.</p></Card>)}
      {tab === "correlation" && <CorrelationPanel holdings={ports.data?.find((p) => p.id === pid)?.positions.map((x) => x.symbol) ?? []} />}
      {tab === "history" && pid && <HistoryPanel pid={pid} />}
    </>
  );
}

function VarPanel({ pid }: { pid: number }) {
  const theme = useResolvedTheme();
  const qc = useQueryClient();
  const [conf, setConf] = useState<number[]>([0.95, 0.99]);
  const [lookback, setLookback] = useState(756);
  const [horizon, setHorizon] = useState(1);
  const [res, setRes] = useState<AnyObj | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      setRes(await api.post<AnyObj>("/risk/analyze", { portfolio_id: pid, confidences: conf, lookback_days: lookback, horizon_days: horizon }));
      qc.invalidateQueries({ queryKey: ["risk-history", pid] });
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const toggle = (c: number) => setConf(conf.includes(c) ? conf.filter((x) => x !== c) : [...conf, c].sort());

  return (
    <div className="stack">
      <Card title="Configuration">
        <div className="row" style={{ gap: 16, alignItems: "flex-end" }}>
          <Field label="Confidence levels">
            <div className="row">{[0.9, 0.95, 0.99].map((c) => <label key={c} className="check"><input type="checkbox" checked={conf.includes(c)} onChange={() => toggle(c)} />{pct(c, 0)}</label>)}</div>
          </Field>
          <Field label="Lookback (trading days)"><input className="input" type="number" min={60} max={5000} value={lookback} onChange={(e) => setLookback(Number(e.target.value))} style={{ width: 120 }} /></Field>
          <Field label="Horizon (days)" hint="historical: overlapping h-day returns; parametric: √h scaling">
            <input className="input" type="number" min={1} max={20} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} style={{ width: 100 }} />
          </Field>
          <button className="btn primary" onClick={run} disabled={busy || conf.length === 0}>{busy ? "Estimating…" : "Estimate risk"}</button>
          {err && <span className="neg small">{err}</span>}
        </div>
      </Card>
      {!res && !busy && <div className="state">Configure and run an estimate. Each run is stored in the risk-metrics table.</div>}
      {busy && <Loading label="Estimating VaR / CVaR" />}
      {res && (
        <>
          <div className="kpis">
            <Kpi label="Portfolio value" value={money(res.portfolio_value)} note={`as of ${res.as_of}`} />
            <Kpi label="Observations" value={int(res.lookback_days)} note={`${res.horizon_days}-day horizon`} />
            {res.estimates.map((e: AnyObj) => (
              <Kpi key={e.confidence} label={`Hist. VaR ${pct(e.confidence, 0)}`} metric="var" value={pct(e.historical.var)} note={`CVaR ${pct(e.historical.cvar)} · ${money(e.historical.cvar_amount)}`} />
            ))}
          </div>
          <Card title="Estimates by methodology" flush>
            <div className="table-wrap">
              <table className="dt">
                <thead><tr><th>Confidence</th><th>Method</th><th className="r">VaR</th><th className="r">VaR ($)</th><th className="r">CVaR</th><th className="r">CVaR ($)</th><th className="r">Tail obs.</th></tr></thead>
                <tbody>
                  {res.estimates.flatMap((e: AnyObj) => (["historical", "parametric_normal", "cornish_fisher"] as const).map((m) => (
                    <tr key={`${e.confidence}-${m}`}>
                      <td>{pct(e.confidence, 0)}</td>
                      <td><span className="row" style={{ gap: 4 }}>{m.replace("_", " ")}<InfoTip text={res.methodology[m]} /></span></td>
                      <td className="r">{pct(e[m].var)}</td><td className="r">{money(e[m].var_amount)}</td>
                      <td className="r">{e[m].cvar == null ? "–" : pct(e[m].cvar)}</td><td className="r">{money(e[m].cvar_amount)}</td>
                      <td className="r">{m === "historical" ? <span className={e.small_tail_sample ? "neg" : ""}>{e.tail_observations}</span> : "–"}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
            <div className="small text2" style={{ padding: "8px 14px" }}>
              Sample skewness {num(res.estimates[0].sample_skewness, 2)}, excess kurtosis {num(res.estimates[0].sample_excess_kurtosis, 2)}. {res.methodology.caveat}
              {res.estimates.some((e: AnyObj) => e.small_tail_sample) && " Fewer than 10 tail observations make the estimate noisy."}
            </div>
          </Card>
          <div className="grid g2">
            <Card title={`${res.horizon_days}-day return distribution`} sub="lookback sample, with VaR thresholds">
              <Plot height={280} data={[{
                type: "bar", x: res.distribution.edges.slice(0, -1).map((e: number, i: number) => (e + res.distribution.edges[i + 1]) / 2), y: res.distribution.counts,
                marker: { color: seriesColor(theme, 0) }, name: "frequency", hovertemplate: "%{x:.2%}: %{y}<extra></extra>",
              }]} layout={{
                hovermode: "closest", bargap: 0.04, showlegend: false, xaxis: { tickformat: ".1%" },
                shapes: res.estimates.map((e: AnyObj, i: number) => ({ type: "line", x0: -e.historical.var, x1: -e.historical.var, yref: "paper", y0: 0, y1: 1, line: { color: i ? STATUS.critical : STATUS.serious, width: 2, dash: "dot" } })),
                annotations: res.estimates.map((e: AnyObj) => ({ x: -e.historical.var, yref: "paper", y: 1, text: `VaR ${pct(e.confidence, 0)}`, showarrow: false, yanchor: "bottom", font: { size: 10, color: INK[theme].text2 } })),
              }} />
            </Card>
            <VarBacktest bt={res.var_backtest} theme={theme} />
          </div>
        </>
      )}
    </div>
  );
}

function VarBacktest({ bt, theme }: { bt: AnyObj; theme: "light" | "dark" }) {
  if (bt.error) return <Card title="VaR backtest"><ErrorState error={new Error(bt.error)} /></Card>;
  const exc = bt.dates.map((d: string, i: number) => (bt.exception_flags[i] ? d : null)).filter(Boolean);
  const excY = bt.returns.filter((_: number, i: number) => bt.exception_flags[i]);
  const reject = bt.kupiec_p_value != null && bt.kupiec_p_value < 0.05;
  return (
    <Card title={`Out-of-sample VaR backtest (${pct(bt.confidence, 0)})`} sub={`rolling ${bt.window}-day historical VaR, estimated from prior days only`}>
      <Plot height={250} data={[
        { type: "scatter", mode: "lines", x: bt.dates, y: bt.returns, name: "Daily return", line: { width: 1, color: INK[theme].neutral } },
        line(bt.dates, bt.var.map((v: number) => -v), "−VaR", seriesColor(theme, 0)),
        { type: "scatter", mode: "markers", x: exc, y: excY, name: "Exception", marker: { color: STATUS.critical, size: 8, symbol: "x" } },
      ]} layout={{ xaxis: dateAxis(theme, false), yaxis: { tickformat: ".1%" } }} />
      <div className="small" style={{ marginTop: 6 }}>
        {bt.exceptions} exceptions in {bt.observations} days (expected {num(bt.expected_exceptions, 1)}; rate {pct(bt.exception_rate)}).
        Kupiec POF LR = {num(bt.kupiec_lr, 2)}, p = {num(bt.kupiec_p_value, 3)} — {reject ? <span className="neg">coverage rejected at 5%</span> : <span className="pos">coverage not rejected at 5%</span>}.
      </div>
    </Card>
  );
}

function CorrelationPanel({ holdings }: { holdings: string[] }) {
  const { datasetId } = useWorkspace();
  const theme = useResolvedTheme();
  const assets = useAssets(datasetId);
  const [symbols, setSymbols] = useState<string[]>(holdings);
  const [method, setMethod] = useState("pearson");
  const [threshold, setThreshold] = useState(0.8);
  const [win, setWin] = useState(60);
  const [range, setRange] = useState({ start: "", end: "" });
  const [pair, setPair] = useState<[string, string] | null>(null);
  const syms = symbols.length >= 2 ? symbols : [];
  const q = useQuery({
    queryKey: ["correlation", datasetId, syms, method, threshold, win, range, pair],
    queryFn: () => api.get<AnyObj>("/research/correlation", { dataset_id: datasetId, symbols: syms.length ? syms.join(",") : undefined, method, threshold, rolling_window: win, start: range.start, end: range.end, pair: pair?.join(",") }),
  });
  return (
    <div className="grid g-side">
      <Card title="Selection">
        <div className="stack">
          <Field label="Assets" hint="fewer than two selected = full tradable universe"><AssetChecklist assets={assets.data ?? []} value={symbols} onChange={setSymbols} height={260} /></Field>
          <Field label="Date range"><DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} /></Field>
          <div className="form-grid">
            <Field label="Method"><select className="input" value={method} onChange={(e) => setMethod(e.target.value)}><option value="pearson">Pearson</option><option value="spearman">Spearman (rank)</option></select></Field>
            <Field label="Rolling window"><input className="input" type="number" min={20} max={504} value={win} onChange={(e) => setWin(Number(e.target.value))} /></Field>
            <Field label="High-corr threshold"><input className="input" type="number" step="0.05" min={0} max={1} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></Field>
          </div>
        </div>
      </Card>
      <QueryView q={q} label="Computing correlations">
        {(c) => {
          const m = c.matrix;
          const text = m.matrix.map((row: (number | null)[], i: number) => row.map((v, j) => `${m.symbols[i]} × ${m.symbols[j]}<br>ρ = ${v == null ? "n/a" : v.toFixed(3)}<br>${m.pair_counts[i][j]} overlapping obs.`));
          return (
            <div className="stack">
              <div className="kpis">
                <Kpi label="Assets" value={m.symbols.length} />
                <Kpi label="Avg pairwise correlation" value={num(m.average_pairwise_correlation, 3)} metric="correlation" />
                <Kpi label="Total / complete-case obs." value={`${int(m.observations_total)} / ${int(m.complete_case_observations)}`} tip="Cells use pairwise-complete observations; the hover shows each cell's sample size so missing-data effects are visible." />
                <Kpi label={`Pairs |ρ| ≥ ${threshold}`} value={c.high_pairs.length} />
              </div>
              <Card title="Correlation matrix" sub={m.clustered ? "hierarchically clustered (average linkage, distance √(½(1−ρ)))" : "unclustered"}>
                <Plot height={Math.min(720, 120 + m.symbols.length * 16)} data={[{
                  type: "heatmap", z: m.matrix, x: m.symbols, y: m.symbols, zmin: -1, zmax: 1, colorscale: diverging(theme), text, hoverinfo: "text",
                  colorbar: { thickness: 10 }, xgap: 1, ygap: 1,
                }]} layout={{ hovermode: "closest", margin: { l: 60, r: 10, t: 8, b: 60 }, yaxis: { autorange: "reversed", tickfont: { size: 9.5 } }, xaxis: { tickfont: { size: 9.5 } } }}
                  onClick={(pt) => pt.x !== pt.y && setPair([String(pt.y), String(pt.x)])} />
                <div className="xs muted">Click a cell to plot that pair's rolling correlation.</div>
              </Card>
              <div className="grid g2">
                <Card title={`Rolling ${win}-day average correlation`} sub="market-wide dependency gauge">
                  <Plot height={230} data={[line(c.rolling_average.dates, c.rolling_average.values, "Avg ρ", seriesColor(theme, 0))]} layout={{ xaxis: dateAxis(theme, false), yaxis: { hoverformat: ".3f" }, showlegend: false }} />
                </Card>
                <Card title={pair ? `Rolling ${win}-day correlation: ${pair[0]} × ${pair[1]}` : "Pair rolling correlation"}>
                  {c.pair ? <Plot height={230} data={[line(c.pair.dates, c.pair.values, `${c.pair.a}×${c.pair.b}`, seriesColor(theme, 1))]} layout={{ xaxis: dateAxis(theme, false), yaxis: { range: [-1, 1], hoverformat: ".3f" }, showlegend: false }} />
                    : <div className="state">Click a matrix cell.</div>}
                </Card>
              </div>
              <Card title="Highly correlated pairs" flush>
                <DataTable<AnyObj> rows={c.high_pairs} exportName="high-correlation-pairs" pageSize={10} onRowClick={(r) => setPair([r.a, r.b])} columns={[
                  { key: "a", label: "Asset A", render: (r) => <span className="mono">{r.a}</span> },
                  { key: "b", label: "Asset B", render: (r) => <span className="mono">{r.b}</span> },
                  { key: "correlation", label: "ρ", align: "right", render: (r) => num(r.correlation, 3) },
                  { key: "observations", label: "Obs.", align: "right", render: (r) => int(r.observations) },
                ]} empty={`No pairs with |ρ| ≥ ${threshold}.`} />
              </Card>
            </div>
          );
        }}
      </QueryView>
    </div>
  );
}

function HistoryPanel({ pid }: { pid: number }) {
  const q = useQuery({ queryKey: ["risk-history", pid], queryFn: () => api.get<AnyObj[]>(`/risk/history/${pid}`) });
  return (
    <Card title="Stored risk estimates" sub="risk_metrics table" actions={<button className="btn sm" onClick={() => download(buildUrl(`/exports/risk/${pid}`))}>Export CSV</button>} flush>
      <QueryView q={q}>
        {(rows) => (
          <DataTable<AnyObj> rows={rows} pageSize={20} columns={[
            { key: "created_at", label: "Computed", render: (r) => dt(r.created_at) },
            { key: "as_of", label: "As of" }, { key: "method", label: "Method" },
            { key: "confidence", label: "Confidence", align: "right", render: (r) => pct(r.confidence, 0) },
            { key: "horizon_days", label: "Horizon", align: "right" }, { key: "lookback_days", label: "Lookback", align: "right" },
            { key: "var", label: "VaR", align: "right", render: (r) => pct(r.var) }, { key: "cvar", label: "CVaR", align: "right", render: (r) => pct(r.cvar) },
            { key: "var_amount", label: "VaR ($)", align: "right", render: (r) => money(r.var_amount) },
          ]} empty="No stored estimates yet — run one on the VaR tab." />
        )}
      </QueryView>
    </Card>
  );
}
