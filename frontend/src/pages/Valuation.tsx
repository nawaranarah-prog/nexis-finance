import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import { Card, download, Empty, ErrorState, Field, Kpi, Loading, PageHead } from "../components/ui";
import { Change, fmtBig, fmtPrice, SymbolSearch } from "../components/market";
import { toast } from "../components/toast";
import { api, buildUrl, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

const EDITABLE: { key: string; label: string; pct?: boolean; big?: boolean; step?: string }[] = [
  { key: "base_fcf", label: "Base free cash flow", big: true },
  { key: "growth_start", label: "Starting growth", pct: true },
  { key: "terminal_growth", label: "Terminal growth", pct: true },
  { key: "years", label: "Projection years", step: "1" },
  { key: "risk_free_rate", label: "Risk-free rate", pct: true },
  { key: "beta", label: "Beta", step: "0.01" },
  { key: "equity_risk_premium", label: "Equity risk premium", pct: true },
  { key: "country_risk_premium", label: "Country risk premium", pct: true },
  { key: "pre_tax_cost_of_debt", label: "Pre-tax cost of debt", pct: true },
  { key: "tax_rate", label: "Tax rate", pct: true },
];
const p = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);

export default function Valuation() {
  const { symbol } = useParams();
  const nav = useNavigate();
  const sym = symbol ? decodeURIComponent(symbol).toUpperCase() : null;
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [peers, setPeers] = useState<string[] | null>(null);
  const [wacc, setWacc] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setOverrides({}); setDraft({}); setPeers(null); setWacc(""); }, [sym]);
  const body = { overrides: { ...overrides, ...(wacc ? { wacc: Number(wacc) / 100 } : {}) }, peers };
  const q = useQuery({ queryKey: ["valuation", sym, body], queryFn: () => api.post<AnyObj>(`/valuation/${encodeURIComponent(sym!)}`, body), enabled: !!sym, retry: false });

  const apply = () => {
    const o: Record<string, number> = {};
    for (const f of EDITABLE) {
      const raw = draft[f.key];
      if (raw === undefined || raw === "") continue;
      const v = Number(raw);
      if (!Number.isFinite(v)) continue;
      o[f.key] = f.pct ? v / 100 : f.big ? v * 1e6 : v;
    }
    setOverrides(o);
  };
  const report = async () => {
    setBusy(true);
    try {
      const r = await api.post<AnyObj>(`/valuation/${encodeURIComponent(sym!)}/report`, body);
      toast("success", "Valuation report ready", r.file_name);
      download(buildUrl(`/reports/${r.report_id}/download`));
    } catch (e) { toast("error", "Report failed", errorMessage(e)); } finally { setBusy(false); }
  };

  const v = q.data;
  return (
    <>
      <PageHead title="Valuation · Investment Banking" desc="Discounted cash flow with CAPM-based WACC, trading comparables, market-implied growth (reverse DCF) and a football field — every assumption visible and editable."
        actions={v && <button className="btn primary" disabled={busy} onClick={report}>{busy ? "Building PDF…" : "Download valuation report"}</button>} />
      <div style={{ maxWidth: 560, marginBottom: 12 }}>
        <SymbolSearch placeholder="Company to value (e.g. EMAAR.AE, AAPL)…" onPick={(s) => nav(`/valuation/${encodeURIComponent(s.symbol)}`)} />
      </div>
      {!sym ? (
        <Empty>Pick a company to value. Try <Link to="/valuation/EMAAR.AE">Emaar</Link>, <Link to="/valuation/EMIRATESNBD.AE">Emirates NBD</Link>, <Link to="/valuation/AAPL">Apple</Link> or <Link to="/valuation/MSFT">Microsoft</Link>.</Empty>
      ) : q.isLoading ? <Loading label={`Valuing ${sym}`} /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : v && (
        <>
          <div className="row" style={{ gap: 8, alignItems: "baseline", marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}><Link to={`/markets/${encodeURIComponent(v.symbol)}`}>{v.name}</Link></h2>
            <span className="small text2">{v.symbol} · price {fmtPrice(v.price, v.currency)}</span>
          </div>
          <div className="kpis">
            <Kpi label="DCF value / share" value={fmtPrice(v.dcf.value_per_share)} note={v.currency} />
            <Kpi label="Blended fair value" value={fmtPrice(v.blended_fair_value)} note={v.method} />
            <Kpi label="vs market price" value={<Change pct={v.upside} />} note={v.view ?? ""} />
            <Kpi label="WACC" value={p(v.wacc.used, 2)} note={`Ke ${p(v.wacc.cost_of_equity, 2)} · Kd ${p(v.wacc.after_tax_cost_of_debt, 2)} after tax`} />
            <Kpi label="Market-implied growth" value={v.market_implied_growth?.growth !== null && v.market_implied_growth?.growth !== undefined ? p(v.market_implied_growth.growth) : "—"} note={v.market_implied_growth?.note} />
            <Kpi label="Analyst mean target" value={fmtPrice(v.analysts?.target_mean)} note={v.analysts?.count ? `${v.analysts.count} analysts · ${(v.analysts.recommendation ?? "").replace("_", " ")}` : "no coverage"} />
          </div>
          {v.warnings?.length > 0 && <div className="banner warn small" style={{ margin: "12px 0" }}><span aria-hidden>!</span><div>{v.warnings.map((w: string) => <div key={w}>{w}</div>)}</div></div>}
          <Card title="Football field" sub={`value per share, ${v.currency}`}>
            <Plot height={Math.max(220, 60 + v.football_field.length * 44)} data={[
              { type: "bar", orientation: "h", y: v.football_field.map((r: AnyObj) => r.method), x: v.football_field.map((r: AnyObj) => r.high - r.low), base: v.football_field.map((r: AnyObj) => r.low),
                marker: { color: "rgba(37,99,235,0.55)" }, hovertemplate: "%{base:.2f} – %{x:.2f}<extra></extra>", name: "range" } as AnyObj,
              { type: "scatter", mode: "markers", y: v.football_field.filter((r: AnyObj) => r.mid).map((r: AnyObj) => r.method), x: v.football_field.filter((r: AnyObj) => r.mid).map((r: AnyObj) => r.mid),
                marker: { color: "#111827", size: 9, symbol: "line-ns-open", line: { width: 2 } }, name: "mid", hovertemplate: "mid %{x:.2f}<extra></extra>" } as AnyObj,
            ]} layout={{ showlegend: false, margin: { l: 210, r: 20, t: 10, b: 36 }, yaxis: { autorange: "reversed" } as AnyObj,
              shapes: [{ type: "line", x0: v.price, x1: v.price, yref: "paper", y0: 0, y1: 1, line: { color: "#dc2626", width: 2, dash: "dot" } }],
              annotations: [{ x: v.price, yref: "paper", y: 1.02, text: `price ${v.price.toFixed(2)}`, showarrow: false, font: { color: "#dc2626", size: 11 } }] } as AnyObj} />
          </Card>
          <div className="grid g2">
            <Card title="Assumptions" sub="edit and recalculate" actions={<><button className="btn sm" onClick={() => { setDraft({}); setOverrides({}); setWacc(""); }}>Reset</button><button className="btn sm primary" onClick={apply}>Recalculate</button></>}>
              <div className="form-grid">
                {EDITABLE.map((f) => {
                  const cur = v.assumptions[f.key];
                  const shown = f.pct ? (cur * 100).toFixed(2) : f.big ? (cur / 1e6).toFixed(1) : String(cur);
                  return (
                    <Field key={f.key} label={`${f.label}${f.pct ? " (%)" : f.big ? " (millions)" : ""}`} hint={v.sources[f.key]}>
                      <input className="input" inputMode="decimal" value={draft[f.key] ?? shown} step={f.step}
                        onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} onKeyDown={(e) => e.key === "Enter" && apply()} />
                    </Field>
                  );
                })}
                <Field label="WACC override (%)" hint="leave empty to use CAPM + market-value weights">
                  <input className="input" inputMode="decimal" value={wacc} placeholder={(v.wacc.wacc * 100).toFixed(2)} onChange={(e) => setWacc(e.target.value)} />
                </Field>
              </div>
            </Card>
            <Card title="DCF projection" sub={`${v.financial_currency}${v.fx_to_trading_currency !== 1 ? ` → ${v.currency} at ${v.fx_to_trading_currency.toFixed(4)}` : ""}`} flush>
              <table className="dt">
                <thead><tr><th>Year</th><th className="r">Growth</th><th className="r">FCF</th><th className="r">DF</th><th className="r">PV</th></tr></thead>
                <tbody>
                  {v.dcf.projection.map((r: AnyObj) => <tr key={r.year}><td>{r.year}</td><td className="r num">{p(r.growth)}</td><td className="r num">{fmtBig(r.fcf)}</td><td className="r num">{r.discount_factor.toFixed(3)}</td><td className="r num">{fmtBig(r.pv)}</td></tr>)}
                  <tr><td>Terminal</td><td></td><td className="r num">{fmtBig(v.dcf.terminal_value)}</td><td></td><td className="r num">{fmtBig(v.dcf.pv_terminal_value)}</td></tr>
                  <tr className="strong"><td>Enterprise value</td><td colSpan={3} className="xs muted">terminal = {p(v.dcf.terminal_share_of_ev, 0)} of EV</td><td className="r num">{fmtBig(v.dcf.enterprise_value)}</td></tr>
                  <tr><td>− debt + cash</td><td colSpan={3}></td><td className="r num">{fmtBig(v.assumptions.cash - v.assumptions.debt)}</td></tr>
                  <tr className="strong"><td>Equity value</td><td colSpan={3}></td><td className="r num">{fmtBig(v.dcf.equity_value)}</td></tr>
                </tbody>
              </table>
            </Card>
          </div>
          <div className="grid g2">
            <Card title="Sensitivity" sub="value per share · rows WACC, columns terminal growth" flush>
              <table className="dt heat">
                <thead><tr><th>WACC \ g</th>{v.sensitivity.terminal_growth.map((g: number) => <th key={g} className="r">{p(g)}</th>)}</tr></thead>
                <tbody>{v.sensitivity.wacc.map((w: number, i: number) => (
                  <tr key={w}><td>{p(w)}</td>{v.sensitivity.value_per_share[i].map((x: number | null, j: number) => {
                    const up = x !== null ? x / v.price - 1 : 0;
                    return <td key={j} className="r num" style={{ background: x === null ? undefined : up >= 0 ? `rgba(22,163,74,${0.06 + Math.min(up, 1) * 0.3})` : `rgba(220,38,38,${0.06 + Math.min(-up, 1) * 0.3})`, fontWeight: i === 2 && j === 2 ? 700 : undefined }}>{x === null ? "—" : x.toFixed(2)}</td>;
                  })}</tr>
                ))}</tbody>
              </table>
            </Card>
            <Card title="Trading comparables" sub={`${v.comparables.source} — edit below`} flush>
              <div className="table-wrap"><table className="dt">
                <thead><tr><th>Peer</th><th className="r">Mkt cap</th><th className="r">P/E</th><th className="r">EV/EBITDA</th><th className="r">P/B</th><th className="r">EBITDA mgn</th></tr></thead>
                <tbody>{v.comparables.peers.map((c: AnyObj) => (
                  <tr key={c.symbol}><td><Link className="mono" to={`/markets/${encodeURIComponent(c.symbol)}`}>{c.symbol}</Link></td><td className="r num">{fmtBig(c.market_cap)}</td>
                    <td className="r num">{c.pe?.toFixed(1) ?? "—"}</td><td className="r num">{c.ev_ebitda?.toFixed(1) ?? "—"}</td><td className="r num">{c.pb?.toFixed(2) ?? "—"}</td><td className="r num">{p(c.ebitda_margin)}</td></tr>
                ))}</tbody>
              </table></div>
              <table className="dt" style={{ marginTop: 8 }}>
                <thead><tr><th>Multiple</th><th className="r">Peer median</th><th className="r">{v.symbol}</th><th className="r">Implied price</th></tr></thead>
                <tbody>{Object.values(v.comparables.implied).map((c) => { const x = c as AnyObj; return (
                  <tr key={x.label}><td>{x.label}</td><td className="r num">{x.peer_median.toFixed(2)}</td><td className="r num">{x.target_multiple?.toFixed(2) ?? "—"}</td><td className="r num">{x.implied_price.toFixed(2)}</td></tr>
                ); })}</tbody>
              </table>
              <div className="card-body">
                <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                  {(peers ?? v.comparables.peers.map((c: AnyObj) => c.symbol)).map((s: string) => (
                    <span key={s} className="chip on">{s}<button className="chip-x" aria-label={`Remove ${s}`} onClick={() => setPeers((peers ?? v.comparables.peers.map((c: AnyObj) => c.symbol)).filter((x: string) => x !== s))}>×</button></span>
                  ))}
                  <div style={{ minWidth: 220, flex: 1 }}><SymbolSearch compact placeholder="Add a peer…" onPick={(s) => { const cur = peers ?? v.comparables.peers.map((c: AnyObj) => c.symbol); if (!cur.includes(s.symbol)) setPeers([...cur, s.symbol].slice(0, 8)); }} /></div>
                </div>
                {v.comparables.skipped?.length > 0 && <div className="xs muted" style={{ marginTop: 6 }}>Skipped: {v.comparables.skipped.map((s: AnyObj) => `${s.symbol} (${s.reason})`).join("; ")}</div>}
              </div>
            </Card>
          </div>
          <div className="xs muted">{v.source}. A model valuation under the stated assumptions — not a price target or investment advice.</div>
        </>
      )}
    </>
  );
}
