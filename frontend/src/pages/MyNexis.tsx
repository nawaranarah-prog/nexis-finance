import { askConfirm } from "../components/dialog";
import { Fragment, useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, fmtPrice, SymbolSearch, useMe } from "../components/market";
import { ago, assetPath, DiscussionRow, stamp } from "../components/pulse";
import { handlePlanError, useBilling } from "../components/pro";
import { toast } from "../components/toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

type Section = "today" | "investments" | "watchlist" | "alerts" | "activity";
const SECTIONS: { key: Section; label: string; to: string }[] = [
  { key: "today", label: "Today", to: "/my-nexis" },
  { key: "investments", label: "My investments", to: "/my-nexis/investments" },
  { key: "watchlist", label: "Watchlist", to: "/my-nexis/watchlist" },
  { key: "alerts", label: "Alerts", to: "/my-nexis/alerts" },
  { key: "activity", label: "My Pulse activity", to: "/my-nexis/activity" },
];
const TYPES = ["stock", "etf", "fund", "bond", "crypto", "other"] as const;
const usd = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }));
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(2)}%`);
const myAsset = (sym: string) => `/my-nexis/asset/${encodeURIComponent(sym)}`;
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function Source({ e }: { e: AnyObj }) {
  const { lang } = useT();
  return (
    <li>
      {e.url && /^https?:/.test(e.url) ? <a href={e.url} target="_blank" rel="noreferrer noopener" dir="auto">{e.title}</a> : <span dir="auto">{e.title}</span>}
      <span className="pl-news-meta">{e.publisher || e.provider_label} · {ago(e.published_at, lang)}</span>
    </li>
  );
}

// ------------------------------------------------------------------ add / edit an investment

function HoldingForm({ initialSymbol, holding, onDone, onCancel }: { initialSymbol?: string; holding?: AnyObj; onDone: () => void; onCancel: () => void }) {
  const [asset, setAsset] = useState<AnyObj | null>(holding ? { symbol: holding.symbol, name: holding.name } : initialSymbol ? { symbol: initialSymbol } : null);
  const [qty, setQty] = useState<string>(holding ? String(holding.quantity) : "");
  const [price, setPrice] = useState<string>(holding?.purchase_price != null ? String(holding.purchase_price) : "");
  const [date, setDate] = useState<string>(holding?.purchase_date ?? "");
  const [kind, setKind] = useState<string>(holding?.asset_type ?? "");
  const [name, setName] = useState<string>(holding?.portfolio ?? "Main");
  const [note, setNote] = useState<string>(holding?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!asset || !(Number(qty) > 0)) return;
    setBusy(true); setErr(null);
    const body: AnyObj = { quantity: Number(qty), purchase_price: price === "" ? null : Number(price), purchase_date: date || null, portfolio: name || "Main",
      note: note.trim() || null, ...(kind ? { asset_type: kind } : {}) };
    try {
      if (holding) await api.patch(`/me/holdings/${holding.id}`, body);
      else await api.post("/me/holdings", { symbol: asset.symbol, ...body });
      onDone();
    } catch (x) { if (!handlePlanError(x)) setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <form className="pt-form" onSubmit={submit}>
      <div className="pt-form-grid">
        <label className="pl-field span2"><span className="pl-label">Asset</span>
          {asset ? <span className="pl-picked"><span className="mono">{asset.symbol}</span> <span className="muted">{asset.name}</span>{!holding && <button type="button" className="link-btn xs" onClick={() => setAsset(null)}>Change</button>}</span>
            : <SymbolSearch compact placeholder="Stock, ETF, fund, bond or crypto — e.g. NVDA, FAB.AD" onPick={(s) => setAsset(s)} />}
        </label>
        <label className="pl-field"><span className="pl-label">Quantity</span><input className="input num" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} required /></label>
        <label className="pl-field"><span className="pl-label">Average purchase price <span className="muted">· optional</span></span><input className="input num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        <label className="pl-field"><span className="pl-label">Purchase date <span className="muted">· optional</span></span><input className="input" type="date" value={date} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="pl-field"><span className="pl-label">Type</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Detect automatically</option>{TYPES.map((x) => <option key={x} value={x}>{x}</option>)}</select></label>
        <label className="pl-field"><span className="pl-label">Portfolio</span><input className="input" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="pl-field span2"><span className="pl-label">Notes <span className="muted">· optional, private</span></span><input className="input" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why you bought it, what would change your mind…" /></label>
      </div>
      {err && <div className="banner error small">{err}</div>}
      <div className="pl-compose-foot"><span className="xs muted">Private to you — never shown in Pulse or anywhere public. Nexis never asks for brokerage or bank logins.</span><span className="grow" />
        <button type="button" className="btn sm" onClick={onCancel}>Cancel</button>
        <button className="btn primary sm" disabled={!asset || !(Number(qty) > 0) || busy}>{busy ? "…" : holding ? "Save changes" : "Add investment"}</button></div>
    </form>
  );
}

// ------------------------------------------------------------------ Today

function Today() {
  const { lang } = useT();
  const q = useQuery({ queryKey: ["my-today"], queryFn: () => api.get<AnyObj>("/me/today"), refetchInterval: 300_000 });
  const d = q.data;
  if (q.isLoading) return <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /><span className="skel w70" /></div>;
  if (!d) return null;
  if (!d.tracked) {
    return (
      <div className="mn-start">
        <h2>Tell Nexis what you care about</h2>
        <p>Add the investments you own — or just the assets you watch — and this page becomes a short list of what matters to them today: material news, earnings, and the Pulse discussions about them.</p>
        <div className="row" style={{ gap: 8 }}><Link className="btn primary" to="/my-nexis/investments?add=1">Add an investment</Link><Link className="btn" to="/my-nexis/watchlist">Build a watchlist</Link></div>
      </div>
    );
  }
  const c = d.counts;
  return (
    <>
      <section className="mn-today">
        <h2>Your Nexis today</h2>
        <ul className="mn-tally">
          <li><b className="num">{c.developments}</b> {c.developments === 1 ? "important development" : "important developments"} affecting what you track <span className="muted">· last 48 hours</span></li>
          <li><b className="num">{c.discussions}</b> active Pulse {c.discussions === 1 ? "discussion" : "discussions"}</li>
          <li><b className="num">{c.upcoming}</b> upcoming {c.upcoming === 1 ? "event" : "events"} <span className="muted">· next 3 weeks</span></li>
        </ul>
        <p className="xs muted">Tracking {plural(d.holdings, "investment", "investments")} and {plural(d.watching, "watched asset", "watched assets")}. Context, not advice.</p>
      </section>
      <div className="mn-cols">
        <section className="pl-section">
          <div className="sec-head"><h2>What happened</h2></div>
          {d.developments.length ? (
            <ol className="mn-devs">
              {d.developments.map((e: AnyObj) => (
                <li key={e.id}>
                  <div className="mn-dev-meta">{e.assets?.[0] && <Link className="mono" to={myAsset(e.assets[0])}>{e.assets[0]}</Link>}<span>{e.relation === "holding" ? "You own this" : "Watching"}</span><span>{e.kind.replace("_", " ")}</span><time dateTime={e.published_at}>{ago(e.published_at, lang)}</time></div>
                  {e.url && /^https?:/.test(e.url) ? <a href={e.url} target="_blank" rel="noreferrer noopener" dir="auto">{e.title}</a> : <span dir="auto">{e.title}</span>}
                  {e.publisher && <span className="pl-news-meta">{e.publisher}</span>}
                </li>
              ))}
            </ol>
          ) : <p className="pl-empty-line">Nothing material in the last 48 hours. Nexis doesn't alert you about minor moves.</p>}
        </section>
        <aside>
          <section className="pl-section">
            <div className="sec-head"><h2>Coming up</h2></div>
            {d.upcoming.length ? <ul className="pt-upcoming">{d.upcoming.map((u: AnyObj, i: number) => <li key={i}><Link className="mono" to={myAsset(u.symbol)}>{u.symbol}</Link> <b>{u.kind}</b> <span className="num">{u.date}</span><div className="xs muted">{u.source}</div></li>)}</ul>
              : <p className="pl-empty-line">No earnings or dividend dates in the next three weeks.</p>}
          </section>
          <section className="pl-section">
            <div className="sec-head"><h2>Pulse discussions</h2><Link className="sec-link" to="/pulse?sort=for_you">For you →</Link></div>
            {d.discussions.length ? <ol className="np-list compact">{d.discussions.map((x: AnyObj) => <DiscussionRow key={x.id} d={x} />)}</ol>
              : <p className="pl-empty-line">No active discussions about your assets in the last two days.</p>}
          </section>
        </aside>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ investments & watchlist

function Investments({ add }: { add: boolean }) {
  const qc = useQueryClient();
  const [, setSp] = useSearchParams();
  const q = useQuery({ queryKey: ["portfolio"], queryFn: () => api.get<AnyObj>("/me/portfolio"), refetchInterval: 120_000 });
  const [adding, setAdding] = useState<boolean>(add);
  const [editing, setEditing] = useState<AnyObj | null>(null);
  useEffect(() => { if (add) setAdding(true); }, [add]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["portfolio"] }); qc.invalidateQueries({ queryKey: ["my-today"] }); qc.invalidateQueries({ queryKey: ["billing-status"] }); };
  const remove = async (h: AnyObj) => {
    if (!(await askConfirm({ title: `Remove ${h.symbol} from your investments?`, body: "This removes the investment from My Nexis. It doesn't affect anything outside Nexis.", confirm: "Remove", danger: true }))) return;
    try { await api.del(`/me/holdings/${h.id}`); refresh(); toast("success", "Investment removed"); } catch (x) { toast("error", "Couldn't remove", errorMessage(x)); }
  };
  const d = q.data;
  const tot = d?.totals;
  return (
    <>
      {d && d.holdings.length > 0 && (
        <section className="pt-summary">
          <div><span className="pl-mini-h">Value</span><b className="num pt-big">{usd(tot.value_usd)}</b></div>
          <div><span className="pl-mini-h">Today</span><b className={`num ${tot.day_change_usd > 0 ? "pos" : tot.day_change_usd < 0 ? "neg" : ""}`}>{tot.day_change_usd == null ? "—" : `${tot.day_change_usd >= 0 ? "+" : "−"}${usd(Math.abs(tot.day_change_usd))}`}</b></div>
          <div><span className="pl-mini-h">Gain since purchase</span><b className={`num ${tot.gain_usd > 0 ? "pos" : tot.gain_usd < 0 ? "neg" : ""}`}>{tot.gain_usd == null ? "—" : `${tot.gain_usd >= 0 ? "+" : "−"}${usd(Math.abs(tot.gain_usd))}`}</b></div>
          <div className="pt-alloc">
            <span className="pl-mini-h">Allocation</span>
            <div className="pt-alloc-bar">{d.allocation.map((a: AnyObj) => <span key={a.asset_type} className={`at-${a.asset_type}`} style={{ flexGrow: a.weight }} title={`${a.asset_type} ${(a.weight * 100).toFixed(1)}%`} />)}</div>
            <div className="pt-alloc-legend">{d.allocation.map((a: AnyObj) => <span key={a.asset_type}><i className={`at-${a.asset_type}`} />{a.asset_type} <span className="num">{(a.weight * 100).toFixed(0)}%</span></span>)}</div>
          </div>
        </section>
      )}
      {tot?.excluded?.length > 0 && <p className="hm-fine">Not included in totals (no price or currency rate available): {tot.excluded.join(", ")}.</p>}
      {adding || editing ? (
        <HoldingForm holding={editing ?? undefined}
          onCancel={() => { setAdding(false); setEditing(null); setSp({}, { replace: true }); }}
          onDone={() => { refresh(); setAdding(false); setEditing(null); setSp({}, { replace: true }); toast("success", editing ? "Changes saved" : "Investment added"); }} />
      ) : <button type="button" className="pl-compose-closed" onClick={() => setAdding(true)}><span>Add an investment you own — stocks, ETFs, funds, bonds or crypto…</span><span className="btn primary sm" aria-hidden>Add</span></button>}
      <p className="pt-explain">Only add what you actually own. Assets you just follow belong on your <Link to="/my-nexis/watchlist">watchlist</Link>.</p>
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /></div>}
      {q.isError && <div className="hm-error">{errorMessage(q.error)}</div>}
      {d && !d.holdings.length && !adding && <p className="pl-empty-line pt-empty">No investments yet.</p>}
      {d && d.holdings.length > 0 && (
        <div className="pt-table-wrap">
          <table className="pt-table">
            <thead><tr><th>Asset</th><th className="r">Quantity</th><th className="r">Price</th><th className="r">Value</th><th className="r">Gain</th><th className="r hide-xs">Weight</th><th /></tr></thead>
            <tbody>
              {d.holdings.map((h: AnyObj) => (
                <Fragment key={h.id}>
                  <tr>
                    <td><Link to={myAsset(h.symbol)} className="pt-asset"><span className="mono wl-tk">{h.symbol}</span><span className="wl-name">{h.name ?? ""}{h.portfolio !== "Main" ? ` · ${h.portfolio}` : ""}</span></Link>
                      {h.note && <div className="xs muted mn-note" dir="auto">{h.note}</div>}</td>
                    <td className="r num">{h.quantity.toLocaleString("en-US", { maximumFractionDigits: 6 })}</td>
                    <td className="r num">{h.price != null ? <>{fmtPrice(h.price)} <span className="wl-ccy">{h.currency}</span><div><Change pct={h.change_pct} /></div></> : <span className="xs muted">price unavailable</span>}</td>
                    <td className="r num">{h.value != null ? fmtPrice(h.value) : "—"}</td>
                    <td className={`r num ${h.gain > 0 ? "pos" : h.gain < 0 ? "neg" : ""}`}>{h.gain != null ? <>{fmtPrice(h.gain)}<div className="xs">{pct(h.gain_pct)}</div></> : <span className="xs muted">{h.purchase_price == null ? "no cost entered" : "—"}</span>}</td>
                    <td className="r num hide-xs">{h.weight != null ? `${(h.weight * 100).toFixed(1)}%` : "—"}</td>
                    <td className="r pt-actions"><button type="button" className="link-btn xs" onClick={() => setEditing(h)}>Edit</button><button type="button" className="link-btn xs neg" onClick={() => remove(h)}>Remove</button></td>
                  </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {d && <p className="hm-fine">{d.note}</p>}
    </>
  );
}

function Watchlist() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["watchlist"], queryFn: () => api.get<AnyObj>("/me/watchlist"), refetchInterval: 120_000 });
  const change = async (symbol: string, on: boolean) => {
    try {
      if (on) await api.put(`/me/watchlist/${encodeURIComponent(symbol)}`, {}); else await api.del(`/me/watchlist/${encodeURIComponent(symbol)}`);
      qc.invalidateQueries({ queryKey: ["watchlist"] }); qc.invalidateQueries({ queryKey: ["track", symbol] }); qc.invalidateQueries({ queryKey: ["my-today"] }); qc.invalidateQueries({ queryKey: ["billing-status"] });
      toast("success", on ? `Watching ${symbol}` : `Stopped watching ${symbol}`);
    } catch (x) { if (!handlePlanError(x)) toast("error", "Couldn't update your watchlist", errorMessage(x)); }
  };
  const items: AnyObj[] = q.data?.items ?? [];
  return (
    <>
      <p className="pt-explain">Assets you follow without owning. Nexis watches them for material developments and Pulse discussions, the same as your investments.</p>
      <div className="pl-switch"><SymbolSearch compact placeholder="Add an asset to your watchlist…" onPick={(s) => change(s.symbol, true)} /></div>
      {!q.isLoading && !items.length && <p className="pl-empty-line pt-empty">Nothing on your watchlist yet.</p>}
      <table className="pt-table">
        <tbody>
          {items.map((w) => (
            <tr key={w.symbol}>
              <td><Link to={myAsset(w.symbol)} className="pt-asset"><span className="mono wl-tk">{w.symbol}</span><span className="wl-name">{w.name ?? ""}</span></Link></td>
              <td className="r num">{w.price != null ? <>{fmtPrice(w.price)} <span className="wl-ccy">{w.currency}</span></> : <span className="xs muted">price unavailable</span>}</td>
              <td className="r">{w.change_pct != null ? <Change pct={w.change_pct} /> : null}</td>
              <td className="r pt-actions"><Link className="link-btn xs" to={assetPath(w.symbol)}>Pulse</Link><button type="button" className="link-btn xs neg" onClick={() => change(w.symbol, false)}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// ------------------------------------------------------------------ one asset: what matters right now

function Brief({ symbol }: { symbol: string }) {
  const [data, setData] = useState<AnyObj | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { setData(await api.post<AnyObj>(`/intelligence/${encodeURIComponent(symbol)}/brief`)); }
    catch (x) { if (!handlePlanError(x)) setData({ available: false, reason: errorMessage(x) }); } finally { setBusy(false); }
  };
  if (!data) return <button type="button" className="btn sm" disabled={busy} onClick={run}>{busy ? "Writing the brief…" : "AI brief on these developments"}</button>;
  if (!data.available) return <p className="pl-empty-line">{data.reason}</p>;
  return (
    <div className="pt-brief">
      <div className="pt-brief-row"><span className="pt-brief-k">What happened</span><p dir="auto">{data.what_happened}</p></div>
      <div className="pt-brief-row"><span className="pt-brief-k">Why it may matter</span><p dir="auto">{data.why_it_may_matter}</p></div>
      {data.perspectives?.length > 0 && <div className="pt-brief-row"><span className="pt-brief-k">Perspectives</span><ul>{data.perspectives.map((p: AnyObj, i: number) => <li key={i} dir="auto">{p.view} <span className="muted xs">— {p.from}</span></li>)}</ul></div>}
      <div className="pt-brief-row"><span className="pt-brief-k">Uncertainty</span><p dir="auto">{data.uncertainty}</p></div>
      <div className="pt-brief-row"><span className="pt-brief-k">Sources</span><ul>{data.sources.map((s: AnyObj, i: number) => <li key={i}>{s.url && /^https?:/.test(s.url) ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title} ↗</a> : s.title} <span className="muted xs">{s.publisher}</span></li>)}</ul></div>
      <p className="hm-fine">{data.disclaimer} AI-generated from the sources listed.</p>
    </div>
  );
}

export function MyNexisAsset() {
  const { symbol = "" } = useParams();
  const sym = decodeURIComponent(symbol).toUpperCase();
  const { lang } = useT();
  const q = useQuery({ queryKey: ["intel", sym], queryFn: () => api.get<AnyObj>(`/intelligence/${encodeURIComponent(sym)}`), staleTime: 120_000 });
  const d = q.data;
  return (
    <div className="hm pt mn">
      <header className="pl-head">
        <div className="hm-eyebrow"><Link to="/my-nexis">My Nexis</Link><span>Private to you</span></div>
        <h1><span className="mono">{sym}</span> {d?.name && d.name !== sym && <span className="mn-name">{d.name}</span>}</h1>
        {d?.quote?.price != null && <div className="np-quote static"><span className="num">{fmtPrice(d.quote.price)} {d.quote.currency}</span><Change pct={d.quote.change_pct} /><span className="muted xs">{d.quote.exchange} · delayed</span></div>}
        {d?.status && <p className="xs muted">{d.status.holding ? "You own this." : d.status.watching ? "On your watchlist." : "You don't track this yet."} <Link to={`/markets/${encodeURIComponent(sym)}`}>Chart and fundamentals</Link> · <Link to={assetPath(sym)}>Open in Pulse</Link></p>}
      </header>
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /></div>}
      {q.isError && <div className="hm-error">{errorMessage(q.error)}</div>}
      {d && (
        <div className="mn-cols">
          <div>
            {d.moves.length > 0 && <section className="pl-section"><div className="sec-head"><h2>Major movement</h2></div><ul className="pl-news">{d.moves.map((e: AnyObj) => <Source key={e.id} e={e} />)}</ul></section>}
            <section className="pl-section"><div className="sec-head"><h2>Latest news</h2><span className="xs muted">30 days, sourced</span></div>
              {d.news.length ? <ul className="pl-news">{d.news.map((e: AnyObj) => <Source key={e.id} e={e} />)}</ul> : <p className="pl-empty-line">No sourced news in the last 30 days.</p>}
              {d.events.length > 0 && <div style={{ marginTop: 10 }}><Brief symbol={sym} /></div>}
            </section>
            {d.company.length > 0 && <section className="pl-section"><div className="sec-head"><h2>Company developments</h2></div><ul className="pl-news">{d.company.map((e: AnyObj) => <Source key={e.id} e={e} />)}</ul></section>}
            <section className="pl-section"><div className="sec-head"><h2>Potential risks</h2></div>
              {d.risks.length ? <ul className="mn-risks">{d.risks.map((r: AnyObj, i: number) => (
                <li key={i} dir="auto">{r.point} <span className="xs muted">— {r.from}{r.sources?.[0]?.url ? <> · <a href={r.sources[0].url} target="_blank" rel="noreferrer noopener">{r.sources[0].publisher ?? "source"}</a></> : null}</span></li>
              ))}</ul> : <p className="pl-empty-line">No specific risks raised in recent sourced coverage.</p>}
            </section>
            {d.sector?.events?.length > 0 && <section className="pl-section"><div className="sec-head"><h2>Sector developments</h2><span className="xs muted">{d.sector.key?.replace("_", " ")} · 7 days</span></div><ul className="pl-news">{d.sector.events.map((e: AnyObj) => <Source key={e.id} e={e} />)}</ul></section>}
          </div>
          <aside>
            {d.position && (
              <section className="pl-section"><div className="sec-head"><h2>Your position</h2></div>
                <p className="num">{d.position.quantity.toLocaleString("en-US", { maximumFractionDigits: 6 })} units</p>
                <ul className="xs muted">{d.position.lots.map((l: AnyObj) => <li key={l.id}>{l.quantity} @ {l.purchase_price ?? "—"}{l.purchase_date ? ` on ${l.purchase_date}` : ""}{l.note ? ` · ${l.note}` : ""}</li>)}</ul>
              </section>
            )}
            <section className="pl-section"><div className="sec-head"><h2>Upcoming</h2></div>
              {d.upcoming.length ? <ul className="pt-upcoming">{d.upcoming.map((u: AnyObj, i: number) => <li key={i}><b>{u.kind}</b> <span className="num">{u.date}</span> <span className="xs muted">· {u.source}</span></li>)}</ul> : <p className="pl-empty-line">No scheduled events found.</p>}
            </section>
            <section className="pl-section"><div className="sec-head"><h2>Nexis analysis</h2></div>
              {d.analysis ? (
                <Link to={d.analysis.url} className="np-ed-card compact">
                  <b dir="auto">{d.analysis.title}</b>
                  {d.analysis.editorial?.what_happened && <p dir="auto">{d.analysis.editorial.what_happened}</p>}
                  <span className="xs muted">Updated {ago(d.analysis.updated_at ?? d.analysis.created_at, lang)} · {d.analysis.sources.length} sources</span>
                </Link>
              ) : <p className="pl-empty-line">No Nexis editorial on {sym} yet.</p>}
            </section>
            <section className="pl-section"><div className="sec-head"><h2>Pulse discussions</h2><span className="xs muted">{plural(d.discussions.length, "active discussion", "active discussions")}</span></div>
              {d.discussions.length ? <ol className="np-list compact">{d.discussions.map((x: AnyObj) => <DiscussionRow key={x.id} d={x} showAsset={false} />)}</ol>
                : <p className="pl-empty-line">Nobody is discussing {sym} yet. <Link to={`${assetPath(sym)}`}>Start the first discussion</Link> — anonymously.</p>}
            </section>
          </aside>
        </div>
      )}
      <p className="hm-fine">Information and context only. Nexis does not tell you to buy, sell or hold.</p>
    </div>
  );
}

// ------------------------------------------------------------------ alerts & activity

function Alerts() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notif-prefs"], queryFn: () => api.get<AnyObj>("/me/notification-preferences") });
  const p = q.data;
  const ruleFor = (type: string) => p?.rules.find((r: AnyObj) => r.symbol == null && r.event_type === type);
  const enabled = (type: string) => (ruleFor(type)?.enabled ?? !(p?.opt_in ?? []).includes(type));
  const set = async (type: string, on: boolean) => {
    try { qc.setQueryData(["notif-prefs"], await api.put<AnyObj>("/me/alert-rules", { event_type: type, enabled: on })); }
    catch (x) { toast("error", "Couldn't save", errorMessage(x)); }
  };
  const threshold = async (v: number) => {
    try { qc.setQueryData(["notif-prefs"], await api.put<AnyObj>("/me/notification-preferences", { price_move_pct: v })); } catch (x) { toast("error", "Couldn't save", errorMessage(x)); }
  };
  const LABEL: Record<string, string> = { news: "Major company news", earnings: "Earnings", dividend: "Dividend announcements", price_move: "Large price movements", filing: "Company filings",
    regulation: "Regulatory developments", rates: "Interest-rate events", macro: "Macro events", bond: "Bond events and maturities", pulse: "Pulse activity" };
  return (
    <>
      <p className="pt-explain">Which events about your investments and watchlist should reach you. How and when — immediately, in a daily digest, or not at all — is set per category in <Link to="/notifications">Notifications</Link>.</p>
      {p && (
        <div className="pt-rules">
          {p.event_types.map((type: string) => (
            <label key={type} className="pt-rule">
              <input type="checkbox" checked={enabled(type)} onChange={(e) => set(type, e.target.checked)} />
              <span>{LABEL[type] ?? type}{(p.opt_in ?? []).includes(type) && <span className="xs muted"> · market-wide, off unless you switch it on</span>}</span>
            </label>
          ))}
          <label className="pt-rule threshold">
            <span>Only alert me about daily moves of at least</span>
            <input className="input num" type="number" min={1} max={50} step={0.5} defaultValue={p.price_move_pct} onBlur={(e) => threshold(Number(e.target.value))} />
            <span>%</span>
          </label>
        </div>
      )}
    </>
  );
}

function Activity() {
  const { lang } = useT();
  const q = useQuery({ queryKey: ["my-pulse"], queryFn: () => api.get<AnyObj>("/pulse/me/activity") });
  const d = q.data;
  return (
    <>
      <p className="pt-explain">{d?.note ?? "Only you can see this list."}</p>
      <section className="pl-section"><div className="sec-head"><h2>Your discussions</h2></div>
        {d && !d.discussions.length && <p className="pl-empty-line">You haven't started a discussion yet.</p>}
        <ol className="np-list">{(d?.discussions ?? []).map((x: AnyObj) => <DiscussionRow key={x.id} d={x} />)}</ol>
      </section>
      <section className="pl-section"><div className="sec-head"><h2>Your replies</h2></div>
        {d && !d.comments.length && <p className="pl-empty-line">No replies yet.</p>}
        <ol className="mn-replies">{(d?.comments ?? []).map((c: AnyObj) => (
          <li key={c.id}><Link to={`${c.discussion.url}#c${c.id}`} dir="auto">{c.discussion.title}</Link><p dir="auto">{c.body}</p>
            <span className="xs muted" title={stamp(c.created_at, lang)}>{ago(c.created_at, lang)}{c.pending_review ? " · waiting for review" : ""}</span></li>
        ))}</ol>
      </section>
    </>
  );
}

/** Capacity on the current plan (from the server): shown before anyone hits it, and after a downgrade. */
function Capacity() {
  const b = useBilling();
  const u = b.data?.usage?.my_nexis_assets;
  if (!u) return null;
  if (u.over_capacity) {
    return <p className="np-note">You're tracking {u.used} assets and your plan includes {u.limit}. Everything you track stays saved and keeps working; to add another, remove some or <Link to="/pro">upgrade to Nexis Pro</Link>.</p>;
  }
  return <p className={`pro-usage ${u.near_limit ? "low" : ""}`}>{u.used} of {u.limit} tracked assets on your plan{u.at_limit && b.data?.plan === "free" ? <> · <Link to="/pro">More with Nexis Pro</Link></> : null}</p>;
}

export default function MyNexis() {
  const me = useMe();
  const { section } = useParams();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const current = (SECTIONS.find((s) => s.key === section)?.key ?? "today") as Section;
  useEffect(() => { if (section && !SECTIONS.some((s) => s.key === section)) nav("/my-nexis", { replace: true }); }, [section, nav]);
  if (me.isLoading) return <div className="hm pt"><div className="wire-skel"><span className="skel w60" /></div></div>;
  if (!me.data?.user) {
    return (
      <div className="hm pt">
        <header className="pl-head"><div className="hm-eyebrow"><span>My Nexis</span></div><h1>What matters to your investments, today</h1>
          <p className="hm-lede">Add what you own and what you watch. Nexis follows the real developments that affect them and connects you to the Pulse discussions about them. Private to you — nothing here is ever public, and no brokerage or bank login is needed.</p>
          <Link className="btn primary" to="/login?mode=signup&next=/my-nexis">Create a free account</Link> <Link className="btn" to="/login?next=/my-nexis">Sign in</Link>
        </header>
      </div>
    );
  }
  return (
    <div className="hm pt mn">
      <header className="pl-head"><div className="hm-eyebrow"><span>My Nexis</span><span>Private to you</span></div><h1>{current === "today" ? "What matters to you right now" : SECTIONS.find((s) => s.key === current)!.label}</h1></header>
      <nav className="pt-tabs" aria-label="My Nexis">
        {SECTIONS.map((x) => <NavLink key={x.key} to={x.to} end className={() => (current === x.key ? "on" : "")}>{x.label}</NavLink>)}
      </nav>
      {current === "today" && <Today />}
      {(current === "investments" || current === "watchlist") && <Capacity />}
      {current === "investments" && <Investments add={sp.get("add") === "1"} />}
      {current === "watchlist" && <Watchlist />}
      {current === "alerts" && <Alerts />}
      {current === "activity" && <Activity />}
    </div>
  );
}
