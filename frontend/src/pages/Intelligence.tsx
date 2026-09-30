import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import { Card, Disclaimer, InfoTip, PageHead, QueryView, StatusBadge } from "../components/ui";
import { MethodSelect, ScopeSelect, Warnings } from "../components/connect";
import { dateAxis, INK, seriesColor } from "../components/charts";
import { line, PCT_AXIS } from "../components/metrics";
import { useJobRunner } from "../hooks/queries";
import { useResolvedTheme } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int, money, num, pct, spct, tone } from "../utils/format";

function Stat({ label, value, to, note }: { label: string; value: React.ReactNode; to?: string; note?: React.ReactNode }) {
  const nav = useNavigate();
  return (
    <div className="kpi" style={{ cursor: to ? "pointer" : "default" }} onClick={() => to && nav(to)} role={to ? "link" : undefined} tabIndex={to ? 0 : undefined}
      onKeyDown={(e) => { if (to && e.key === "Enter") nav(to); }}>
      <div className="kpi-label">{label}{to && <span className="muted" aria-hidden> ›</span>}</div>
      <div className="kpi-value num">{value}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

export default function Intelligence() {
  const theme = useResolvedTheme();
  const [scope, setScope] = useState("all");
  const [method, setMethod] = useState("fifo");
  const o = useQuery({ queryKey: ["intel", "overview", scope, method], queryFn: () => api.get<AnyObj>("/intelligence/overview", { scope, method }) });
  const pf = useQuery({ queryKey: ["intel", "portfolio", scope, method], queryFn: () => api.get<AnyObj>("/intelligence/portfolio", { scope, method }), enabled: !!o.data?.ready });
  const regime = useJobRunner([["intel"]]);
  const datasets = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<AnyObj[]>("/datasets") });
  const live = datasets.data?.find((d) => d.code === "LIVE-MARKET");
  return (
    <>
      <PageHead title="Your Financial Intelligence" desc="Connect → Normalise → Understand → Research → Explain. Every figure below is calculated from your connected and imported data."
        actions={<><ScopeSelect value={scope} onChange={setScope} /><MethodSelect value={method} onChange={setMethod} /></>} />
      <QueryView q={o} label="Assembling your portfolio">
        {(d) => {
          const s = d.data;
          return (
            <div className="stack">
              <Card title="Your financial data" sub={`last sync ${dt(s.last_sync)}`} flush>
                <div className="kpis" style={{ border: 0, borderRadius: 0 }}>
                  <Stat label="Connected sources" value={int(s.connected_sources)} note={`${s.connections_total} configured`} to="/connections" />
                  <Stat label="Accounts" value={int(s.accounts)} to="/xray" />
                  <Stat label="Assets held" value={int(s.assets_held)} to="/xray" />
                  <Stat label="Transactions" value={int(s.transactions)} to="/transactions" />
                  <Stat label="Import batches" value={int(s.import_batches)} to="/lineage" />
                  <Stat label="Market-data bars" value={int(s.market_data_bars)} note={`${s.datasets} datasets`} to="/market-data" />
                  <Stat label="Economic series" value={int(s.economic_series)} to="/economic" />
                  <Stat label="SEC company profiles" value={int(s.company_profiles)} to="/economic" />
                </div>
              </Card>
              {!d.ready ? (
                <Card title="Get started">
                  <ol className="stack" style={{ margin: 0, paddingLeft: 18 }}>
                    {d.steps.map((st: AnyObj) => <li key={st.step}><span className={st.done ? "pos" : ""}>{st.done ? "✓ " : ""}{st.step}</span> — <Link to={st.link}>open</Link></li>)}
                  </ol>
                  <div className="small text2" style={{ marginTop: 8 }}>No accounts yet. Import a statement or load the bundled sample statements on <Link to="/connections">Connections</Link>.</div>
                </Card>
              ) : (
                <>
                  <Warnings items={d.warnings} />
                  <div className="kpis">
                    <Stat label="Portfolio value" value={money(d.totals.market_value)} note={`${money(d.totals.cash)} cash`} to="/xray" />
                    <Stat label="Holdings / accounts" value={`${d.totals.positions} / ${d.totals.accounts}`} to="/xray" />
                    <Stat label="Largest exposure" value={<span style={{ fontSize: 14 }}>{d.largest_exposure?.label ?? "Unclassified"}</span>} note={d.largest_exposure ? pct(d.largest_exposure.weight, 1) + " (SEC SIC major group)" : "needs SEC metadata"} to="/xray" />
                    <Stat label="Largest position" value={d.largest_position ?? "–"} note={pct(d.largest_position_weight, 1)} to={d.largest_position ? `/graph?entity=asset:${d.largest_position}` : undefined} />
                    <Stat label="Annualised volatility" value={pct(d.annualized_volatility, 1)} to="/xray?tab=risk" note="click to drill down" />
                    <Stat label="Maximum drawdown" value={<span className="neg">{spct(d.max_drawdown)}</span>} to="/xray?tab=risk" />
                    <Stat label="Top risk contributor" value={d.top_risk_contributor?.symbol ?? "–"} note={d.top_risk_contributor ? `${pct(d.top_risk_contributor.pct_contribution, 1)} of volatility` : undefined} to="/xray?tab=risk" />
                    <Stat label={`Beta vs ${d.benchmark ?? "benchmark"}`} value={num(d.beta, 2)} note={`correlation ${num(d.correlation, 2)}`} to="/xray?tab=moving" />
                    <Stat label="VaR 95% (1-day, hist.)" value={pct(d.var_95)} to="/xray?tab=risk" />
                    <Stat label="Current regime" value={<span style={{ fontSize: 13 }}>{d.regime?.regime ?? "no model"}</span>}
                      note={d.regime ? `${d.regime.code} · ${d.regime.dataset}` : live ? "train one on your market data" : "sync market data first"} to={d.regime ? `/regimes?id=${d.regime.experiment_id}` : undefined} />
                  </div>
                  {!d.regime && live && (
                    <div className="row small">
                      <button className="btn sm" disabled={regime.running} onClick={() => regime.run("/ml/experiments/regime", { dataset_id: live.id, benchmark_symbol: "SPY", train_end: new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10), n_regimes: 3, name: "Regime model on live market data" })}>
                        {regime.running ? "Training…" : "Train a regime model on LIVE-MARKET (SPY)"}</button>
                      <span className="text2">Unsupervised GMM on real SPY features; the label is descriptive, not a forecast.</span>
                      {regime.error && <span className="neg">{regime.error}</span>}
                    </div>
                  )}
                  <div className="small text2">History: {d.history_method ?? "unavailable"} <InfoTip text="Accounts with transactions are reconstructed day by day (time-weighted, flows excluded). Accounts with only a holdings snapshot are backcast: today's quantities applied to past prices. A backcast is not the account's actual history." /></div>
                  {pf.data?.history && (
                    <div className="grid g2">
                      <Card title="Growth of 1 — portfolio vs benchmark">
                        <Plot height={280} data={[line(pf.data.history.dates, pf.data.history.portfolio_growth, "Portfolio", seriesColor(theme, 0)),
                          ...(pf.data.history.benchmark_growth ? [line(pf.data.history.dates, pf.data.history.benchmark_growth, pf.data.benchmark?.symbol ?? "Benchmark", INK[theme].neutral)] : [])]}
                          layout={{ xaxis: dateAxis(theme), yaxis: { hoverformat: ".3f" } }} />
                      </Card>
                      <Card title="Drawdown">
                        <Plot height={280} data={[line(pf.data.history.dates, pf.data.history.drawdown, "Drawdown", seriesColor(theme, 7), { fill: "tozeroy", fillcolor: "rgba(227,73,72,0.14)" })]}
                          layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false }} />
                      </Card>
                      <Card title="Accounts" flush>
                        <table className="dt"><thead><tr><th>Account</th><th>Institution</th><th>Position source</th><th className="r">Value</th></tr></thead>
                          <tbody>{pf.data.accounts.map((a: AnyObj) => (
                            <tr key={a.id} className="clickable" onClick={() => setScope(String(a.id))}><td><b>{a.name}</b></td><td>{a.institution}</td><td className="small text2 wrap">{a.position_source}</td><td className="r">{money(a.market_value)}</td></tr>
                          ))}</tbody></table>
                      </Card>
                      <Card title="Largest holdings" flush actions={<Link className="btn sm" to="/xray">Portfolio X-Ray</Link>}>
                        <table className="dt"><thead><tr><th>Symbol</th><th>Industry (SEC SIC)</th><th className="r">Weight</th><th className="r">Unrealised</th></tr></thead>
                          <tbody>{pf.data.positions.slice(0, 8).map((p: AnyObj) => (
                            <tr key={p.symbol}><td><Link to={`/graph?entity=asset:${p.symbol}`}><b className="mono">{p.symbol}</b></Link></td><td className="small">{p.industry ?? <span className="muted">unclassified</span>}</td>
                              <td className="r">{pct(p.weight, 1)}</td><td className={`r ${tone(p.unrealized_pnl)}`}>{money(p.unrealized_pnl)}</td></tr>
                          ))}</tbody></table>
                      </Card>
                    </div>
                  )}
                </>
              )}
              <Card title="Where your data comes from">
                <div className="row small" style={{ gap: 16 }}>
                  {d.steps.map((st: AnyObj) => <span key={st.step}><StatusBadge status={st.done ? "ok" : "pending"} /> {st.step}</span>)}
                </div>
              </Card>
              <Disclaimer>Portfolio intelligence is descriptive analysis of your data. It contains no recommendations.</Disclaimer>
            </div>
          );
        }}
      </QueryView>
    </>
  );
}
