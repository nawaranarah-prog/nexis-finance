import { handlePlanError } from "../components/pro";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import { Card, download, ErrorState, Field, Loading, PageHead, Seg } from "../components/ui";
import { Change, fmtBig, fmtPrice, SymbolSearch } from "../components/market";
import { toast } from "../components/toast";
import { api, buildUrl, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

const PERIODS = [
  { value: "5d", label: "1W" }, { value: "1mo", label: "1M" }, { value: "3mo", label: "3M" }, { value: "6mo", label: "6M" },
  { value: "ytd", label: "YTD" }, { value: "1y", label: "1Y" }, { value: "3y", label: "3Y" }, { value: "5y", label: "5Y" },
  { value: "10y", label: "10Y" }, { value: "max", label: "Max" }, { value: "custom", label: "Custom" },
];
const INTERVALS = [{ value: "auto", label: "Auto" }, { value: "1h", label: "Hourly" }, { value: "1d", label: "Daily" }, { value: "1wk", label: "Weekly" }, { value: "1mo", label: "Monthly" }];
const BUCKETS = [{ value: "auto", label: "Auto" }, { value: "hour", label: "Hours" }, { value: "day", label: "Days" }, { value: "week", label: "Weeks" }, { value: "month", label: "Months" }, { value: "quarter", label: "Quarters" }, { value: "year", label: "Years" }];
const PRESETS: { group: string; items: { label: string; s: string[] }[] }[] = [
  { group: "UAE sectors", items: [
    { label: "Banks", s: ["FAB.AD", "EMIRATESNBD.AE", "ADCB.AD", "ADIB.AD", "DIB.AE", "MASQ.AE"] },
    { label: "Real estate", s: ["EMAAR.AE", "ALDAR.AD", "EMAARDEV.AE", "TECOM.AE", "DUBAIRESI.AE", "RAKPROP.AD"] },
    { label: "Energy & chemicals", s: ["ADNOCGAS.AD", "ADNOCDRILL.AD", "BOROUGE.AD", "FERTIGLB.AD", "DANA.AD"] },
    { label: "Telecom & tech", s: ["EAND.AD", "DU.AE", "PRESIGHT.AD", "SPACE42.AD"] },
    { label: "Transport & logistics", s: ["ADPORTS.AD", "ADNOCLS.AD", "SALIK.AE", "AIRARABIA.AE", "DTC.AE", "PARKIN.AE"] },
    { label: "Consumer & retail", s: ["AMR.AD", "LULU.AD", "TALABAT.AE", "SPINNEYS.AE", "AGTHIA.AD", "ADNOCDIST.AD"] },
    { label: "Holdings", s: ["IHC.AD", "ALPHADHABI.AD", "2POINTZERO.AD", "MODON.AD", "WAHA.AD"] },
    { label: "Utilities", s: ["DEWA.AE", "EMPOWER.AE", "TABREED.AE"] },
  ] },
  { group: "UAE bonds & mixes", items: [
    { label: "UAE government bonds", s: ["UAE0732USD.BOND", "UAE0734USD.BOND", "UAE0752USD.BOND", "UAEGS0233AED.BOND"] },
    { label: "Sukuk: federal vs Sharjah vs Dubai", s: ["UAEGS0233AED.BOND", "SHRSK0633AED.BOND", "XS222704910.BOND"] },
    { label: "UAE stocks vs bonds vs gold", s: ["FADGI.AD", "DFMGI.AE", "UAE0734USD.BOND", "GC=F"] },
    { label: "Abu Dhabi vs Dubai", s: ["FADGI.AD", "DFMGI.AE"] },
    { label: "Top dividend payers", s: ["EMAAR.AE", "ADNOCGAS.AD", "DEWA.AE", "EAND.AD", "FAB.AD", "SALIK.AE"] },
  ] },
  { group: "Global", items: [
    { label: "UAE vs world", s: ["FADGI.AD", "DFMGI.AE", "^GSPC", "^TASI.SR", "EFA"] },
    { label: "Big tech", s: ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"] },
    { label: "Crypto vs Nasdaq", s: ["BTC-USD", "ETH-USD", "QQQ"] },
  ] },
];
const p = (v: number | null | undefined, d = 1, signed = true) => (v === null || v === undefined ? "—" : `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const n2 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(2));

export default function Compare() {
  const [params, setParams] = useSearchParams();
  const [symbols, setSymbols] = useState<string[]>(() => (params.get("s") ?? "FAB.AD,EMIRATESNBD.AE,ADCB.AD,ADIB.AD,DIB.AE").split(",").filter(Boolean).slice(0, 8));
  const [period, setPeriod] = useState(params.get("p") ?? "1y");
  const [start, setStart] = useState(params.get("from") ?? "");
  const [end, setEnd] = useState(params.get("to") ?? "");
  const [interval, setInterval] = useState(params.get("i") ?? "auto");
  const [bucket, setBucket] = useState(params.get("b") ?? "auto");
  const [busyReport, setBusyReport] = useState(false);
  const body = useMemo(() => ({
    symbols,
    ...(period === "custom" ? { period: null, start: start || null, end: end || null } : { period }),
    interval: interval === "auto" ? null : interval,
    bucket: bucket === "auto" ? null : bucket,
  }), [symbols, period, start, end, interval, bucket]);
  const ready = symbols.length > 0 && (period !== "custom" || !!start);
  const q = useQuery({ queryKey: ["compare", body], queryFn: () => api.post<AnyObj>("/markets/compare", body), enabled: ready, retry: false });

  const sync = (next: Partial<Record<string, string>>) => {
    const cur = Object.fromEntries(params.entries());
    setParams({ ...cur, ...next } as Record<string, string>, { replace: true });
  };
  const setSyms = (s: string[]) => { setSymbols(s); sync({ s: s.join(",") }); };
  const report = async () => {
    setBusyReport(true);
    try {
      const r = await api.post<AnyObj>("/markets/compare/report", body);
      toast("success", "Report ready", `${r.file_name}${r.ai_narrative ? " · AI narrative" : " · rule-based recommendation"}`);
      download(buildUrl(`/reports/${r.report_id}/download`));
    } catch (e) { if (!handlePlanError(e)) toast("error", "Report failed", errorMessage(e)); } finally { setBusyReport(false); }
  };

  const r = q.data;
  return (
    <>
      <PageHead title="Compare & Reports" desc="Compare UAE shares (ADX and DFM), UAE government bonds and sukuk, indices and global markets over any window — hourly, daily, weekly or monthly — then export a PDF report with a recommendation section."
        actions={<button className="btn primary" disabled={!r || busyReport} onClick={report}>{busyReport ? "Building PDF…" : "Download PDF report"}</button>} />
      <Card>
        <div className="stack" style={{ gap: 12 }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {symbols.map((s) => (
              <span key={s} className="chip on">
                <Link to={`/markets/${encodeURIComponent(s)}`}>{s}</Link>
                <button className="chip-x" aria-label={`Remove ${s}`} onClick={() => setSyms(symbols.filter((x) => x !== s))}>×</button>
              </span>
            ))}
            {symbols.length < 8 && <div style={{ minWidth: 260, flex: 1 }}><SymbolSearch compact placeholder="Add an instrument…" onPick={(s) => !symbols.includes(s.symbol) && setSyms([...symbols, s.symbol])} /></div>}
          </div>
          {PRESETS.map((g) => (
            <div key={g.group} className="row small" style={{ gap: 6, flexWrap: "wrap" }}>
              <span className="muted preset-label">{g.group}</span>
              {g.items.map((x) => <button key={x.label} className="chip" onClick={() => setSyms(x.s)}>{x.label}</button>)}
            </div>
          ))}
          <SectorBuilder onPick={setSyms} />
          <div className="form-grid">
            <Field label="Period"><Seg value={period} onChange={(v) => { setPeriod(v); sync({ p: v }); }} options={PERIODS} /></Field>
            {period === "custom" && (
              <>
                <Field label="From"><input className="input" type="date" value={start} onChange={(e) => { setStart(e.target.value); sync({ from: e.target.value }); }} /></Field>
                <Field label="To"><input className="input" type="date" value={end} onChange={(e) => { setEnd(e.target.value); sync({ to: e.target.value }); }} /></Field>
              </>
            )}
            <Field label="Bars" hint="Hourly data covers about the last two years"><Seg value={interval} onChange={(v) => { setInterval(v); sync({ i: v }); }} options={INTERVALS} /></Field>
            <Field label="Returns by"><Seg value={bucket} onChange={(v) => { setBucket(v); sync({ b: v }); }} options={BUCKETS} /></Field>
          </div>
        </div>
      </Card>
      {!ready ? null : q.isLoading ? <Loading label="Fetching prices and fundamentals" /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : r && (
        <>
          {r.currency_note && <div className="banner neutral small" style={{ marginTop: 12 }}>ⓘ {r.currency_note}</div>}
          <Card title="Growth of 100" sub={`${r.window.start.slice(0, 16).replace("T", " ")} → ${r.window.end.slice(0, 16).replace("T", " ")} · ${r.interval} bars`}>
            <Plot height={360} data={r.symbols.map((s: string) => ({ type: "scatter", mode: "lines", name: s, x: r.chart.t, y: r.chart.series[s], connectgaps: true,
              hovertemplate: `${s} %{y:.1f}<extra></extra>` }))} layout={{ xaxis: { type: "date" } as AnyObj }} />
          </Card>
          {r.scorecard?.available && (
            <Card title="Ranking" sub="relative to this group and window">
              <div className="rank-list">
                {r.scorecard.ranking.map((s: string, i: number) => (
                  <div key={s} className="rank-row">
                    <span className="rank-n">{i + 1}</span>
                    <Link className="mono" to={`/markets/${encodeURIComponent(s)}`}>{s}</Link>
                    <span className="rank-bar"><span style={{ width: `${r.scorecard.composite[s]}%` }} /></span>
                    <span className="num">{r.scorecard.composite[s].toFixed(0)}</span>
                    <span className="small text2 rank-why">{[...(r.scorecard.reasons[s] ?? []).slice(0, 2), ...(r.scorecard.weaknesses?.[s] ?? []).slice(0, 1)].join(" · ")}</span>
                  </div>
                ))}
              </div>
              <div className="xs muted" style={{ marginTop: 8 }}>{r.scorecard.method} Weights: {Object.entries(r.scorecard.weights).map(([k, w]) => `${k.replace("_", " ")} ${((w as number) * 100).toFixed(0)}%`).join(", ")}.</div>
            </Card>
          )}
          <Card title="Performance & risk" flush>
            <div className="table-wrap"><table className="dt">
              <thead><tr><th></th>{r.symbols.map((s: string) => <th key={s} className="r">{s}</th>)}</tr></thead>
              <tbody>
                {([["Total return", "total_return", p], ["Annualised return", "annualized_return", p], ["Annualised volatility", "annualized_volatility", (v: number) => p(v, 1, false)],
                  ["Sharpe ratio", "sharpe_ratio", n2], ["Sortino ratio", "sortino_ratio", n2], ["Max drawdown", "max_drawdown", p], ["Best bar", "best_period", p], ["Worst bar", "worst_period", p],
                  ["% up bars", "positive_periods", (v: number) => p(v, 0, false)], [`Correlation to ${r.symbols[0]}`, "correlation_to_first", n2], [`Beta to ${r.symbols[0]}`, "beta_to_first", n2],
                  ["Start → end price", "start_price", null]] as [string, string, ((v: number) => string) | null][]).map(([label, k, f]) => (
                  <tr key={k}><td>{label}</td>{r.symbols.map((s: string) => {
                    const m = r.metrics[s];
                    return <td key={s} className={`r num ${k === "total_return" ? (m[k] > 0 ? "pos" : "neg") : ""}`}>{f ? f(m[k]) : `${fmtPrice(m.start_price)} → ${fmtPrice(m.end_price)}`}</td>;
                  })}</tr>
                ))}
              </tbody>
            </table></div>
          </Card>
          <div className="grid g2">
            <Card title={`Returns by ${r.bucket}`} flush>
              <div className="table-wrap" style={{ maxHeight: 420 }}><table className="dt heat">
                <thead><tr><th>Period</th>{r.symbols.map((s: string) => <th key={s} className="r">{s}</th>)}</tr></thead>
                <tbody>{[...r.periodic_returns].reverse().map((row: AnyObj) => (
                  <tr key={row.period}><td className="nowrap">{row.period}</td>{r.symbols.map((s: string) => {
                    const v = row[s];
                    const a = v === null || v === undefined ? 0 : Math.min(Math.abs(v) / 0.1, 1);
                    return <td key={s} className="r num" style={{ background: v === null || v === undefined ? undefined : v >= 0 ? `rgba(22,163,74,${0.08 + a * 0.3})` : `rgba(220,38,38,${0.08 + a * 0.3})` }}>{p(v)}</td>;
                  })}</tr>
                ))}</tbody>
              </table></div>
            </Card>
            <Card title="Correlation of returns">
              <Plot height={320} data={[{ type: "heatmap", x: r.symbols, y: r.symbols, z: r.symbols.map((a: string) => r.symbols.map((b: string) => r.correlation[a][b])),
                zmin: -1, zmax: 1, colorscale: [[0, "#dc2626"], [0.5, "#f5f5f4"], [1, "#2563eb"]], texttemplate: "%{z:.2f}", hovertemplate: "%{y} / %{x}: %{z:.2f}<extra></extra>" } as AnyObj]}
                layout={{ margin: { l: 90, r: 10, t: 10, b: 80 }, yaxis: { autorange: "reversed" } as AnyObj }} />
            </Card>
          </div>
          <Card title="Fundamentals & analyst consensus" flush>
            <div className="table-wrap"><table className="dt">
              <thead><tr><th></th>{r.symbols.map((s: string) => <th key={s} className="r">{s}</th>)}</tr></thead>
              <tbody>
                {([["Name", "name"], ["Price", "price"], ["Market cap", "market_cap"], ["P/E (ttm)", "pe_trailing"], ["P/E (fwd)", "pe_forward"], ["P/B", "price_to_book"],
                  ["EV/EBITDA", "ev_to_ebitda"], ["Dividend yield", "dividend_yield"], ["Beta", "beta"], ["52-week position", "week52_position"],
                  ["Analyst consensus", "analyst_recommendation"], ["Analysts", "analyst_count"], ["Mean target", "analyst_target"], ["Upside to target", "analyst_upside"]] as [string, string][]).map(([label, k]) => (
                  <tr key={k}><td>{label}</td>{r.symbols.map((s: string) => {
                    const sn = r.snapshots[s]; const v = sn[k];
                    const out = k === "name" ? <span className="ellipsis" style={{ maxWidth: 180, display: "inline-block" }}>{v}</span>
                      : k === "price" ? `${fmtPrice(v)} ${sn.currency ?? ""}` : k === "market_cap" ? fmtBig(v)
                      : ["dividend_yield", "week52_position"].includes(k) ? p(v, 1, false) : k === "analyst_upside" ? <Change pct={v} />
                      : k === "analyst_recommendation" ? (v ? String(v).replace("_", " ") : "—") : k === "analyst_count" ? (v ?? "—") : n2(v);
                    return <td key={s} className="r num">{out}</td>;
                  })}</tr>
                ))}
              </tbody>
            </table></div>
            <div className="card-foot xs muted">{r.source}. Past performance does not predict future returns; not investment advice.</div>
          </Card>
        </>
      )}
    </>
  );
}

/** One click: the largest listed companies of any UAE sector, optionally with the index and a government bond. */
function SectorBuilder({ onPick }: { onPick: (s: string[]) => void }) {
  const q = useQuery({ queryKey: ["mk-list", "uae"], queryFn: () => api.get<AnyObj>("/markets/lists/uae"), staleTime: 300_000 });
  const [sector, setSector] = useState("");
  const [withBench, setWithBench] = useState(true);
  if (!q.data?.sectors) return null;
  const build = () => {
    const top = (q.data.items as AnyObj[]).filter((x) => x.sector === sector).sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0)).slice(0, withBench ? 5 : 7).map((x) => x.symbol);
    onPick(withBench ? [...top, "FADGI.AD", "UAE0734USD.BOND"].slice(0, 8) : top);
  };
  return (
    <div className="row small" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <span className="muted preset-label">Build from a sector</span>
      <select className="input" style={{ height: 32, width: 220 }} value={sector} onChange={(e) => setSector(e.target.value)} aria-label="UAE sector">
        <option value="">Choose a UAE sector…</option>
        {(q.data.sectors as string[]).map((x) => <option key={x}>{x}</option>)}
      </select>
      <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={withBench} onChange={(e) => setWithBench(e.target.checked)} /> add ADX index + UAE 2034 bond</label>
      <button className="btn sm primary" disabled={!sector} onClick={build}>Compare top names</button>
    </div>
  );
}
