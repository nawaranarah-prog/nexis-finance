import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import { Card, ErrorState, Loading, QueryView, Seg } from "../components/ui";
import { Change, fmtBig, fmtPrice, SymbolSearch, TYPE_LABEL } from "../components/market";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

const PERIODS = ["1d", "5d", "1mo", "3mo", "6mo", "ytd", "1y", "5y", "10y", "max"] as const;
type Period = (typeof PERIODS)[number];
const P_LABEL: Record<Period, string> = { "1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", ytd: "YTD", "1y": "1Y", "5y": "5Y", "10y": "10Y", max: "Max" };
const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
const x = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : `${v.toFixed(d)}×`);

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return <div className="stat"><div className="stat-k">{label}</div><div className="stat-v num">{value}</div></div>;
}

export default function Instrument() {
  const { symbol = "" } = useParams();
  const sym = decodeURIComponent(symbol).toUpperCase();
  const nav = useNavigate();
  const [period, setPeriod] = useState<Period>("1y");
  const [kind, setKind] = useState<"line" | "candle">("line");
  const d = useQuery({ queryKey: ["mk-inst", sym], queryFn: () => api.get<AnyObj>(`/markets/instruments/${encodeURIComponent(sym)}`), refetchInterval: 60_000 });
  const h = useQuery({ queryKey: ["mk-hist", sym, period], queryFn: () => api.get<AnyObj>(`/markets/instruments/${encodeURIComponent(sym)}/history`, { period }) });
  const news = useQuery({ queryKey: ["mk-news", sym], queryFn: () => api.get<AnyObj>(`/markets/instruments/${encodeURIComponent(sym)}/news`), staleTime: 600_000 });
  const st = useQuery({ queryKey: ["mk-st", sym], queryFn: () => api.get<AnyObj>(`/markets/instruments/${encodeURIComponent(sym)}/statements`), enabled: d.data?.type === "equity", staleTime: 3600_000, retry: false });
  const posts = useQuery({ queryKey: ["social", "sym", sym], queryFn: () => api.get<AnyObj>("/social/feed", { symbol: sym }) });

  if (d.isLoading) return <Loading label={`Loading ${sym}`} />;
  if (d.error) return <><SymbolSearch onPick={(s) => nav(`/markets/${encodeURIComponent(s.symbol)}`)} /><div style={{ height: 12 }} /><ErrorState error={d.error} onRetry={() => d.refetch()} /></>;
  const i = d.data!;
  const q = i.quote, v = i.valuation, f = i.financials, dv = i.dividends, an = i.analysts, pr = i.profile;
  const bars: AnyObj[] = h.data?.bars ?? [];
  const up = bars.length > 1 ? bars[bars.length - 1].close >= bars[0].close : true;
  const color = up ? "#16a34a" : "#dc2626";
  const periodChange = bars.length > 1 ? bars[bars.length - 1].close / bars[0].close - 1 : null;
  const lows = bars.map((b) => (kind === "candle" ? b.low ?? b.close : b.close)).filter((v) => v !== null);
  const highs = bars.map((b) => (kind === "candle" ? b.high ?? b.close : b.close)).filter((v) => v !== null);
  const lo = lows.length ? Math.min(...lows) : 0, hi = highs.length ? Math.max(...highs) : 1;
  const pad = (hi - lo) * 0.06 || hi * 0.01 || 1;
  const state = ({ REGULAR: "market open", PRE: "pre-market", PREPRE: "pre-market", POST: "after hours", POSTPOST: "market closed", CLOSED: "market closed" } as Record<string, string>)[i.market_state ?? ""];
  const trend = an.trend as AnyObj | null;
  const trendTotal = trend ? Object.values(trend).reduce((a: number, b) => a + (Number(b) || 0), 0) : 0;
  return (
    <>
      <div className="inst-head">
        <div>
          <div className="row" style={{ gap: 8 }}>
            <h1 style={{ margin: 0 }}>{i.name}</h1>
            <span className="badge">{TYPE_LABEL[i.type] ?? i.type}</span>
          </div>
          <div className="small text2">{sym} · {i.exchange} · {i.currency}{state ? ` · ${state}` : ""}</div>
          <div className="inst-price">
            <span className="num">{fmtPrice(q.price)}</span> <span className="small muted">{i.currency}</span>{" "}
            <Change pct={q.change_pct} abs={q.change} />
          </div>
          <div className="xs muted">As of {q.market_time?.replace("T", " ").slice(0, 16) ?? "—"} UTC{i.cache?.stale ? " · showing cached data (provider unavailable)" : ""}</div>
        </div>
        <div className="inst-actions">
          <SymbolSearch compact placeholder="Another symbol…" onPick={(s) => nav(`/markets/${encodeURIComponent(s.symbol)}`)} />
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
            <Link className="btn primary" to={`/advisor?q=${encodeURIComponent(`What's happening with ${i.name} (${sym})? Should I consider buying?`)}`}>Ask the advisor</Link>
            <Link className="btn" to={`/compare?s=${encodeURIComponent(sym)}`}>Compare</Link>
            {i.type === "equity" && <Link className="btn" to={`/valuation/${encodeURIComponent(sym)}`}>Valuation</Link>}
            <Link className="btn" to={`/finstagram?compose=${encodeURIComponent(`$${sym} `)}`}>Post</Link>
          </div>
        </div>
      </div>

      <Card title="Price" sub={periodChange !== null ? <Change pct={periodChange} /> : undefined}
        actions={<><Seg value={kind} onChange={setKind} options={[{ value: "line", label: "Line" }, { value: "candle", label: "Candles" }]} />
          <Seg value={period} onChange={setPeriod} options={PERIODS.map((p) => ({ value: p, label: P_LABEL[p] }))} /></>}>
        {h.isLoading ? <Loading label="Loading prices" /> : h.error ? <ErrorState error={h.error} /> : (
          <Plot height={340} data={kind === "line" ? [{
            type: "scatter", mode: "lines", x: bars.map((b) => b.t), y: bars.map((b) => b.close), name: sym,
            line: { color, width: 1.6 }, fill: "tozeroy", fillcolor: up ? "rgba(22,163,74,0.08)" : "rgba(220,38,38,0.08)",
            hovertemplate: `%{y:.2f} ${i.currency}<extra></extra>`,
          }] : [{
            type: "candlestick", x: bars.map((b) => b.t), open: bars.map((b) => b.open), high: bars.map((b) => b.high),
            low: bars.map((b) => b.low), close: bars.map((b) => b.close), name: sym,
            increasing: { line: { color: "#16a34a" } }, decreasing: { line: { color: "#dc2626" } },
          } as AnyObj]} layout={{ showlegend: false, yaxis: { range: [lo - pad, hi + pad], fixedrange: false } as AnyObj,
            xaxis: { type: "date", rangeslider: { visible: false }, rangebreaks: ["1d", "5d", "1mo"].includes(period) ? [{ bounds: ["sat", "mon"] }] : [] } as AnyObj }} />
        )}
        <div className="xs muted">{h.data ? `${bars.length} ${h.data.interval} bars · adjusted for splits` : ""}</div>
      </Card>

      <div className="grid g2">
        <Card title="Key statistics">
          <div className="stats">
            <Stat label="Open" value={fmtPrice(q.open)} /><Stat label="Previous close" value={fmtPrice(q.previous_close)} />
            <Stat label="Day range" value={`${fmtPrice(q.day_low)} – ${fmtPrice(q.day_high)}`} />
            <Stat label="52-week range" value={`${fmtPrice(q.week52_low)} – ${fmtPrice(q.week52_high)}`} />
            <Stat label="50-day avg" value={fmtPrice(q.ma50)} /><Stat label="200-day avg" value={fmtPrice(q.ma200)} />
            <Stat label="Volume" value={fmtBig(q.volume)} /><Stat label="Avg volume" value={fmtBig(q.avg_volume)} />
            <Stat label="Market cap" value={fmtBig(v.market_cap)} /><Stat label="Enterprise value" value={fmtBig(v.enterprise_value)} />
            <Stat label="P/E (ttm)" value={x(v.pe_trailing, 1)} /><Stat label="P/E (fwd)" value={x(v.pe_forward, 1)} />
            <Stat label="P/B" value={x(v.price_to_book)} /><Stat label="EV/EBITDA" value={x(v.ev_to_ebitda, 1)} />
            <Stat label="EPS (ttm)" value={fmtPrice(v.eps_trailing)} /><Stat label="Beta" value={v.beta?.toFixed(2) ?? "—"} />
            <Stat label="Dividend yield" value={pct(dv.yield, 2)} /><Stat label="Payout ratio" value={pct(dv.payout_ratio)} />
          </div>
          {i.partial && <div className="xs muted" style={{ marginTop: 8 }}>Some details are unavailable right now: {i.partial}</div>}
        </Card>
        <Card title="What analysts say" sub={an.count ? `${an.count} analysts` : "no coverage"}>
          {an.count ? (
            <div className="stack" style={{ gap: 10 }}>
              <div className="row" style={{ gap: 10, alignItems: "baseline" }}>
                <span className={`rec rec-${an.recommendation}`}>{(an.recommendation ?? "—").replace("_", " ")}</span>
                <span className="small text2">mean {an.recommendation_mean?.toFixed(2)} (1 = strong buy … 5 = sell)</span>
              </div>
              {trend && trendTotal > 0 && (
                <div className="rec-bar" aria-label="Recommendation distribution">
                  {(["strongBuy", "buy", "hold", "sell", "strongSell"] as const).map((k) => trend[k] ? (
                    <div key={k} className={`rb rb-${k}`} style={{ flex: trend[k] }} title={`${k}: ${trend[k]}`}>{trend[k]}</div>
                  ) : null)}
                </div>
              )}
              <div className="stats">
                <Stat label="Mean target" value={fmtPrice(an.target_mean)} /><Stat label="Upside" value={<Change pct={an.upside_to_mean_target} />} />
                <Stat label="Low target" value={fmtPrice(an.target_low)} /><Stat label="High target" value={fmtPrice(an.target_high)} />
              </div>
              <div className="xs muted">Consensus of professional sell-side analysts as reported by the data provider.</div>
            </div>
          ) : <div className="small text2">No analyst coverage is reported for this instrument.</div>}
        </Card>
      </div>

      {i.type === "equity" && (
        <div className="grid g2">
          <Card title="Financials (ttm)" sub={i.financial_currency ? `in ${i.financial_currency}` : undefined}>
            <div className="stats">
              <Stat label="Revenue" value={fmtBig(f.revenue)} /><Stat label="Revenue growth" value={pct(f.revenue_growth)} />
              <Stat label="Gross margin" value={pct(f.gross_margin)} /><Stat label="Operating margin" value={pct(f.operating_margin)} />
              <Stat label="EBITDA" value={fmtBig(f.ebitda)} /><Stat label="Net income" value={fmtBig(f.net_income)} />
              <Stat label="Free cash flow" value={fmtBig(f.free_cash_flow)} /><Stat label="Profit margin" value={pct(f.profit_margin)} />
              <Stat label="Cash" value={fmtBig(f.cash)} /><Stat label="Debt" value={fmtBig(f.debt)} />
              <Stat label="ROE" value={pct(f.return_on_equity)} /><Stat label="Current ratio" value={f.current_ratio?.toFixed(2) ?? "—"} />
            </div>
          </Card>
          <Card title="Annual statements">
            <QueryView q={st} label="Loading statements">
              {(s) => s.rows.length ? (
                <div className="table-wrap"><table className="dt">
                  <thead><tr><th></th>{s.years.map((y: string) => <th key={y} className="r">{y}</th>)}</tr></thead>
                  <tbody>{s.rows.map((r: AnyObj) => <tr key={r.key}><td>{r.label}</td>{s.years.map((y: string) => <td key={y} className="r num">{r.key === "annualDilutedEPS" ? r.values[y]?.toFixed(2) ?? "—" : fmtBig(r.values[y])}</td>)}</tr>)}</tbody>
                </table></div>
              ) : <div className="small text2">No annual statements reported.</div>}
            </QueryView>
          </Card>
        </div>
      )}

      <div className="grid g2">
        <Card title="Latest news" sub={news.data ? Object.entries(news.data.feeds).map(([k, v]) => `${k}${v === "ok" ? "" : " (unavailable)"}`).join(" · ") : undefined}>
          <QueryView q={news} label="Loading news">
            {(n) => n.items.length ? (
              <ul className="news">
                {n.items.map((it: AnyObj) => (
                  <li key={it.url}>
                    <a href={it.url} target="_blank" rel="noreferrer noopener" dir="auto">{it.title}</a>
                    <div className="xs muted">{it.publisher ?? it.source} · {it.published_at?.slice(0, 10)}</div>
                  </li>
                ))}
              </ul>
            ) : <div className="small text2">No recent headlines.</div>}
          </QueryView>
        </Card>
        <div className="stack" style={{ gap: 12 }}>
          {(pr.summary || pr.sector) && (
            <Card title="Profile">
              <div className="row small" style={{ gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
                {pr.sector && <span className="badge">{pr.sector}</span>}{pr.industry && <span className="badge">{pr.industry}</span>}
                {pr.country && <span className="badge">{pr.city ? `${pr.city}, ` : ""}{pr.country}</span>}
                {pr.employees && <span className="badge">{pr.employees.toLocaleString()} employees</span>}
              </div>
              {pr.summary && <p className="small" style={{ lineHeight: 1.6 }}>{pr.summary}</p>}
              <div className="row small" style={{ gap: 12 }}>
                {pr.website && <a href={pr.website} target="_blank" rel="noreferrer noopener">{pr.website.replace(/^https?:\/\//, "")}</a>}
                {i.next_earnings && <span className="text2">Next earnings: {i.next_earnings}</span>}
              </div>
            </Card>
          )}
          {i.fund?.top_holdings?.length > 0 && (
            <Card title="Top holdings" sub={i.fund.category ?? undefined}>
              <table className="dt"><tbody>{i.fund.top_holdings.map((t: AnyObj) => <tr key={t.symbol ?? t.name}><td className="mono">{t.symbol}</td><td>{t.name}</td><td className="r num">{pct(t.weight, 2)}</td></tr>)}</tbody></table>
            </Card>
          )}
          <Card title={`Finstagram · $${sym}`} actions={<><Link className="btn sm" to={`/pulse/asset/${encodeURIComponent(sym)}`}>Pulse discussions</Link> <Link className="btn sm" to={`/finstagram?symbol=${encodeURIComponent(sym)}`}>Open feed</Link></>}>
            <QueryView q={posts} label="Loading posts">
              {(p) => p.items.length ? (
                <div className="mini-posts">{p.items.slice(0, 4).map((it: AnyObj) => (
                  <Link key={it.id} to={`/finstagram/p/${it.id}`} className="mini-post">
                    <b>@{it.author.username}</b> <span className="text2">{it.body.slice(0, 140)}</span>
                    <span className="xs muted"> · ♥ {it.like_count} · 💬 {it.comment_count}</span>
                  </Link>
                ))}</div>
              ) : <div className="small text2">No posts about ${sym} yet. <Link to={`/finstagram?compose=${encodeURIComponent(`$${sym} `)}`}>Be the first.</Link></div>}
            </QueryView>
          </Card>
        </div>
      </div>
      <div className="xs muted" style={{ marginTop: 8 }}>{i.source}. Not investment advice.</div>
    </>
  );
}
