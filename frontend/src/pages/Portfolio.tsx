import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, fmtPrice, SymbolSearch, useMe } from "../components/market";
import { toast } from "../components/toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { ago, DiscussionRow, tickerUrl } from "../components/pulseParts";

type Tab = "holdings" | "watchlist" | "intelligence" | "alerts";
const TYPES = ["stock", "etf", "fund", "bond", "crypto", "other"] as const;
const usd = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }));
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(2)}%`);

// ------------------------------------------------------------------ add / edit holding

function HoldingForm({ initialSymbol, holding, onDone, onCancel }: { initialSymbol?: string; holding?: AnyObj; onDone: () => void; onCancel: () => void }) {
  const { t } = useT();
  const [asset, setAsset] = useState<AnyObj | null>(holding ? { symbol: holding.symbol, name: holding.name } : initialSymbol ? { symbol: initialSymbol } : null);
  const [qty, setQty] = useState<string>(holding ? String(holding.quantity) : "");
  const [price, setPrice] = useState<string>(holding?.purchase_price != null ? String(holding.purchase_price) : "");
  const [date, setDate] = useState<string>(holding?.purchase_date ?? "");
  const [kind, setKind] = useState<string>(holding?.asset_type ?? "");
  const [name, setName] = useState<string>(holding?.portfolio ?? "Main");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!asset || !(Number(qty) > 0)) return;
    setBusy(true); setErr(null);
    const body: AnyObj = { quantity: Number(qty), purchase_price: price === "" ? null : Number(price), purchase_date: date || null, portfolio: name || "Main", ...(kind ? { asset_type: kind } : {}) };
    try {
      if (holding) await api.patch(`/me/holdings/${holding.id}`, body);
      else await api.post("/me/holdings", { symbol: asset.symbol, ...body });
      onDone();
    } catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <form className="pt-form" onSubmit={submit}>
      <div className="pt-form-grid">
        <label className="pl-field span2"><span className="pl-label">{t("Asset")}</span>
          {asset ? <span className="pl-picked"><span className="mono">{asset.symbol}</span> <span className="muted">{asset.name}</span>{!holding && <button type="button" className="link-btn xs" onClick={() => setAsset(null)}>{t("Change")}</button>}</span>
            : <SymbolSearch compact placeholder={t("Stock, ETF, fund, bond or crypto…")} onPick={(s) => setAsset(s)} />}
        </label>
        <label className="pl-field"><span className="pl-label">{t("Quantity")}</span><input className="input num" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} required /></label>
        <label className="pl-field"><span className="pl-label">{t("Purchase price")} <span className="muted">· {t("optional")}</span></span><input className="input num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        <label className="pl-field"><span className="pl-label">{t("Purchase date")} <span className="muted">· {t("optional")}</span></span><input className="input" type="date" value={date} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="pl-field"><span className="pl-label">{t("Type")}</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">{t("Detect automatically")}</option>{TYPES.map((x) => <option key={x} value={x}>{t(x)}</option>)}</select></label>
        <label className="pl-field"><span className="pl-label">{t("Portfolio")}</span><input className="input" maxLength={60} value={name} onChange={(e) => setName(e.target.value)} /></label>
      </div>
      {err && <div className="banner error small">{err}</div>}
      <div className="pl-compose-foot"><span className="xs muted">{t("Private to you. Nexis never asks for brokerage or bank logins.")}</span><span className="grow" />
        <button type="button" className="btn sm" onClick={onCancel}>{t("Cancel")}</button>
        <button className="btn primary sm" disabled={!asset || !(Number(qty) > 0) || busy}>{busy ? "…" : holding ? t("Save changes") : t("Add holding")}</button></div>
    </form>
  );
}

// ------------------------------------------------------------------ intelligence for one asset

function Brief({ symbol }: { symbol: string }) {
  const { t } = useT();
  const [data, setData] = useState<AnyObj | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { setData(await api.post<AnyObj>(`/intelligence/${encodeURIComponent(symbol)}/brief`)); } catch (x) { setData({ available: false, reason: errorMessage(x) }); } finally { setBusy(false); }
  };
  if (!data) return <button type="button" className="btn sm" disabled={busy} onClick={run}>{busy ? t("Writing the brief…") : t("AI brief on these developments")}</button>;
  if (!data.available) return <p className="pl-empty-line">{data.reason}</p>;
  return (
    <div className="pt-brief">
      <div className="pt-brief-row"><span className="pt-brief-k">{t("What happened")}</span><p dir="auto">{data.what_happened}</p></div>
      <div className="pt-brief-row"><span className="pt-brief-k">{t("Why it may matter")}</span><p dir="auto">{data.why_it_may_matter}</p></div>
      {data.perspectives?.length > 0 && <div className="pt-brief-row"><span className="pt-brief-k">{t("Perspectives")}</span><ul>{data.perspectives.map((p: AnyObj, i: number) => <li key={i} dir="auto">{p.view} <span className="muted xs">— {p.from}</span></li>)}</ul></div>}
      <div className="pt-brief-row"><span className="pt-brief-k">{t("Uncertainty")}</span><p dir="auto">{data.uncertainty}</p></div>
      <div className="pt-brief-row"><span className="pt-brief-k">{t("Sources")}</span><ul>{data.sources.map((s: AnyObj, i: number) => <li key={i}>{s.url && /^https?:/.test(s.url) ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.title} ↗</a> : s.title} <span className="muted xs">{s.publisher}</span></li>)}</ul></div>
      <p className="hm-fine">{data.disclaimer}{data.model ? ` · ${String(data.model).replace(/^\w+\//, "")}` : ""}</p>
    </div>
  );
}

function AssetIntel({ symbol }: { symbol: string }) {
  const { t, lang } = useT();
  const q = useQuery({ queryKey: ["intel", symbol], queryFn: () => api.get<AnyObj>(`/intelligence/${encodeURIComponent(symbol)}`), staleTime: 120_000 });
  const d = q.data;
  if (q.isLoading) return <div className="wire-skel"><span className="skel w70" /><span className="skel w60" /></div>;
  if (!d) return null;
  return (
    <div className="pt-intel">
      {d.upcoming.length > 0 && (
        <div className="pt-intel-sec"><div className="pl-mini-h">{t("Upcoming")}</div>
          <ul className="pt-upcoming">{d.upcoming.map((u: AnyObj, i: number) => <li key={i}><b>{t(u.kind)}</b> <span className="num">{u.date}</span> <span className="xs muted">· {u.source}</span></li>)}</ul></div>
      )}
      <div className="pt-intel-sec"><div className="pl-mini-h">{t("Recent developments")} <span className="muted">· 30 {t("days")}</span></div>
        {d.events.length ? (
          <ul className="pl-news">{d.events.slice(0, 6).map((e: AnyObj) => (
            <li key={e.id}>{e.url && /^https?:/.test(e.url) ? <a href={e.url} target="_blank" rel="noreferrer noopener" dir="auto">{e.title}</a> : <span dir="auto">{e.title}</span>}
              <span className="pl-news-meta">{e.publisher || e.provider_label} · {ago(e.published_at, lang)}</span></li>
          ))}</ul>
        ) : <p className="pl-empty-line">{t("No sourced developments in the last 30 days.")}</p>}
      </div>
      {d.events.length > 0 && <div className="pt-intel-sec"><Brief symbol={symbol} /></div>}
      <div className="pt-intel-sec"><div className="pl-mini-h">{t("Pulse discussions")}</div>
        {d.discussions.length ? <ol className="pl-list compact">{d.discussions.slice(0, 3).map((x: AnyObj) => <DiscussionRow key={x.id} d={x} />)}</ol> : <p className="pl-empty-line">{t("No discussions yet.")}</p>}
        <Link className="sec-link" to={tickerUrl(symbol)}>{t("Open the")} {symbol} {t("Pulse")} <span aria-hidden>→</span></Link>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ tabs

function Holdings({ addSymbol }: { addSymbol: string | null }) {
  const { t } = useT();
  const qc = useQueryClient();
  const [, setSp] = useSearchParams();
  const q = useQuery({ queryKey: ["portfolio"], queryFn: () => api.get<AnyObj>("/me/portfolio"), refetchInterval: 120_000 });
  const [adding, setAdding] = useState<boolean>(!!addSymbol);
  const [editing, setEditing] = useState<AnyObj | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => { if (addSymbol) setAdding(true); }, [addSymbol]);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["portfolio"] }); qc.invalidateQueries({ queryKey: ["intelligence"] }); };
  const remove = async (h: AnyObj) => {
    if (!window.confirm(`${t("Remove")} ${h.symbol} ${t("from your portfolio?")}`)) return;
    try { await api.del(`/me/holdings/${h.id}`); refresh(); toast("success", t("Holding removed")); } catch (x) { toast("error", t("Couldn't remove"), errorMessage(x)); }
  };
  const d = q.data;
  const tot = d?.totals;
  return (
    <>
      {d && d.holdings.length > 0 && (
        <section className="pt-summary">
          <div><span className="pl-mini-h">{t("Value")}</span><b className="num pt-big">{usd(tot.value_usd)}</b></div>
          <div><span className="pl-mini-h">{t("Today")}</span><b className={`num ${tot.day_change_usd > 0 ? "pos" : tot.day_change_usd < 0 ? "neg" : ""}`}>{tot.day_change_usd == null ? "—" : `${tot.day_change_usd >= 0 ? "+" : "−"}${usd(Math.abs(tot.day_change_usd))}`}</b></div>
          <div><span className="pl-mini-h">{t("Gain since purchase")}</span><b className={`num ${tot.gain_usd > 0 ? "pos" : tot.gain_usd < 0 ? "neg" : ""}`}>{tot.gain_usd == null ? "—" : `${tot.gain_usd >= 0 ? "+" : "−"}${usd(Math.abs(tot.gain_usd))}`}</b></div>
          <div className="pt-alloc">
            <span className="pl-mini-h">{t("Allocation")}</span>
            <div className="pt-alloc-bar">{d.allocation.map((a: AnyObj) => <span key={a.asset_type} className={`at-${a.asset_type}`} style={{ flexGrow: a.weight }} title={`${t(a.asset_type)} ${(a.weight * 100).toFixed(1)}%`} />)}</div>
            <div className="pt-alloc-legend">{d.allocation.map((a: AnyObj) => <span key={a.asset_type}><i className={`at-${a.asset_type}`} />{t(a.asset_type)} <span className="num">{(a.weight * 100).toFixed(0)}%</span></span>)}</div>
          </div>
        </section>
      )}
      {tot?.excluded?.length > 0 && <p className="hm-fine">{t("Not included in totals (no price or currency rate available)")}: {tot.excluded.join(", ")}.</p>}
      {adding || editing ? (
        <HoldingForm initialSymbol={addSymbol ?? undefined} holding={editing ?? undefined}
          onCancel={() => { setAdding(false); setEditing(null); setSp({}, { replace: true }); }}
          onDone={() => { refresh(); setAdding(false); setEditing(null); setSp({}, { replace: true }); toast("success", editing ? t("Changes saved") : t("Holding added")); }} />
      ) : <button type="button" className="pl-compose-closed" onClick={() => setAdding(true)}><span>{t("Add a holding — stocks, ETFs, funds, bonds or crypto…")}</span><span className="btn primary sm" aria-hidden>{t("Add")}</span></button>}
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /></div>}
      {q.isError && <div className="hm-error">{errorMessage(q.error)}</div>}
      {d && !d.holdings.length && !adding && <p className="pl-empty-line pt-empty">{t("No holdings yet. Add what you own to see its value, the developments that affect it, and what investors are saying about it.")}</p>}
      {d && d.holdings.length > 0 && (
        <div className="pt-table-wrap">
          <table className="pt-table">
            <thead><tr><th>{t("Asset")}</th><th className="r">{t("Quantity")}</th><th className="r">{t("Price")}</th><th className="r">{t("Value")}</th><th className="r">{t("Gain")}</th><th className="r hide-xs">{t("Weight")}</th><th /></tr></thead>
            <tbody>
              {d.holdings.map((h: AnyObj) => (
                <Fragment key={h.id}>
                  <tr className={open === h.id ? "open" : ""}>
                    <td><button type="button" className="pt-asset" onClick={() => setOpen(open === h.id ? null : h.id)} aria-expanded={open === h.id}>
                      <span className="mono wl-tk">{h.symbol}</span><span className="wl-name">{h.name ?? ""}{h.portfolio !== "Main" ? ` · ${h.portfolio}` : ""}</span></button></td>
                    <td className="r num">{h.quantity.toLocaleString("en-US", { maximumFractionDigits: 6 })}</td>
                    <td className="r num">{h.price != null ? <>{fmtPrice(h.price)} <span className="wl-ccy">{h.currency}</span><div><Change pct={h.change_pct} /></div></> : <span className="xs muted">{t("price unavailable")}</span>}</td>
                    <td className="r num">{h.value != null ? fmtPrice(h.value) : "—"}</td>
                    <td className={`r num ${h.gain > 0 ? "pos" : h.gain < 0 ? "neg" : ""}`}>{h.gain != null ? <>{fmtPrice(h.gain)}<div className="xs">{pct(h.gain_pct)}</div></> : <span className="xs muted">{h.purchase_price == null ? t("no cost entered") : "—"}</span>}</td>
                    <td className="r num hide-xs">{h.weight != null ? `${(h.weight * 100).toFixed(1)}%` : "—"}</td>
                    <td className="r pt-actions"><button type="button" className="link-btn xs" onClick={() => setEditing(h)}>{t("Edit")}</button><button type="button" className="link-btn xs neg" onClick={() => remove(h)}>{t("Remove")}</button></td>
                  </tr>
                  {open === h.id && <tr className="pt-intel-row"><td colSpan={7}><AssetIntel symbol={h.symbol} /></td></tr>}
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
  const { t } = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["watchlist"], queryFn: () => api.get<AnyObj>("/me/watchlist"), refetchInterval: 120_000 });
  const change = async (symbol: string, on: boolean) => {
    try {
      if (on) await api.put(`/me/watchlist/${encodeURIComponent(symbol)}`, {}); else await api.del(`/me/watchlist/${encodeURIComponent(symbol)}`);
      qc.invalidateQueries({ queryKey: ["watchlist"] }); qc.invalidateQueries({ queryKey: ["track", symbol] });
      toast("success", on ? `${t("Tracking")} ${symbol}` : `${t("Stopped tracking")} ${symbol}`);
    } catch (x) { toast("error", t("Couldn't update your watchlist"), errorMessage(x)); }
  };
  const items: AnyObj[] = q.data?.items ?? [];
  return (
    <>
      <p className="pt-explain">{t("Your watchlist is what you follow without owning. Nexis watches both for developments.")}</p>
      <div className="pl-switch"><SymbolSearch compact placeholder={t("Add an asset to your watchlist…")} onPick={(s) => change(s.symbol, true)} /></div>
      {!q.isLoading && !items.length && <p className="pl-empty-line pt-empty">{t("Nothing on your watchlist yet.")}</p>}
      <table className="pt-table">
        <tbody>
          {items.map((w) => (
            <tr key={w.symbol}>
              <td><Link to={tickerUrl(w.symbol)} className="pt-asset"><span className="mono wl-tk">{w.symbol}</span><span className="wl-name">{w.name ?? ""}</span></Link></td>
              <td className="r num">{w.price != null ? <>{fmtPrice(w.price)} <span className="wl-ccy">{w.currency}</span></> : <span className="xs muted">{t("price unavailable")}</span>}</td>
              <td className="r">{w.change_pct != null ? <Change pct={w.change_pct} /> : null}</td>
              <td className="r pt-actions"><Link className="link-btn xs" to={`/portfolio?tab=intelligence&symbol=${encodeURIComponent(w.symbol)}`}>{t("Intelligence")}</Link><button type="button" className="link-btn xs neg" onClick={() => change(w.symbol, false)}>{t("Remove")}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Intelligence({ focus }: { focus: string | null }) {
  const { t, lang } = useT();
  const q = useQuery({ queryKey: ["intelligence"], queryFn: () => api.get<AnyObj>("/me/intelligence"), staleTime: 120_000 });
  const d = q.data;
  const [open, setOpen] = useState<string | null>(focus);
  useEffect(() => { if (focus) setOpen(focus); }, [focus]);
  const tracked = new Set((d?.assets ?? []).map((a: AnyObj) => a.symbol));
  return (
    <>
      {d && (
        <section className="pt-headline">
          <b className="num">{d.important}</b> {d.important === 1 ? t("important development") : t("important developments")} {t("related to your tracked assets")} <span className="muted">· {d.developments} {t("in total over")} {d.days} {t("days")}</span>
        </section>
      )}
      <p className="pt-explain">{t("Sourced developments for what you hold and watch, with the discussions around them. Information and context only — Nexis never tells you to buy or sell.")}</p>
      {focus && !tracked.has(focus) && (
        <section className="pt-card"><div className="pt-card-h"><span className="mono">{focus}</span><span className="xs muted">{t("not tracked")}</span></div><AssetIntel symbol={focus} /></section>
      )}
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /></div>}
      {d && !d.tracked && <p className="pl-empty-line pt-empty">{t("Add holdings or watch assets to see their developments here.")}</p>}
      {(d?.assets ?? []).map((a: AnyObj) => (
        <section key={a.symbol} className={`pt-card ${open === a.symbol ? "open" : ""}`}>
          <button type="button" className="pt-card-h" onClick={() => setOpen(open === a.symbol ? null : a.symbol)} aria-expanded={open === a.symbol}>
            <span className="mono">{a.symbol}</span>
            <span className="xs muted">{a.relation === "holding" ? t("You hold this") : t("On your watchlist")}</span>
            <span className="grow" />
            <span className="num xs">{a.developments.length} {a.developments.length === 1 ? t("development") : t("developments")}</span>
          </button>
          {open !== a.symbol && a.developments[0] && (
            <p className="pt-card-peek" dir="auto">{a.developments[0].title} <span className="xs muted">· {a.developments[0].publisher || a.developments[0].provider_label} · {ago(a.developments[0].published_at, lang)}</span></p>
          )}
          {open === a.symbol && <AssetIntel symbol={a.symbol} />}
        </section>
      ))}
    </>
  );
}

function Alerts() {
  const { t } = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notif-prefs"], queryFn: () => api.get<AnyObj>("/me/notification-preferences") });
  const p = q.data;
  const ruleFor = (type: string) => p?.rules.find((r: AnyObj) => r.symbol == null && r.event_type === type);
  const enabled = (type: string) => (ruleFor(type)?.enabled ?? !(p?.opt_in ?? []).includes(type));
  const set = async (type: string, on: boolean) => {
    try { qc.setQueryData(["notif-prefs"], await api.put<AnyObj>("/me/alert-rules", { event_type: type, enabled: on })); }
    catch (x) { toast("error", t("Couldn't save"), errorMessage(x)); }
  };
  const threshold = async (v: number) => {
    try { qc.setQueryData(["notif-prefs"], await api.put<AnyObj>("/me/notification-preferences", { price_move_pct: v })); } catch (x) { toast("error", t("Couldn't save"), errorMessage(x)); }
  };
  const LABEL: Record<string, string> = { news: "Major company news", earnings: "Earnings", dividend: "Dividend announcements", price_move: "Large price movements", filing: "Company filings",
    regulation: "Regulatory developments", rates: "Interest-rate events", macro: "Macro events", bond: "Bond events and maturities", pulse: "Replies to your Pulse posts" };
  return (
    <>
      <p className="pt-explain">{t("Choose which events about your holdings and watchlist should alert you. How and when you hear about them is set in")} <Link to="/notifications">{t("Notifications")}</Link>.</p>
      {p && (
        <div className="pt-rules">
          {p.event_types.map((type: string) => (
            <label key={type} className="pt-rule">
              <input type="checkbox" checked={enabled(type)} onChange={(e) => set(type, e.target.checked)} />
              <span>{t(LABEL[type] ?? type)}{(p.opt_in ?? []).includes(type) && <span className="xs muted"> · {t("market-wide, off unless you switch it on")}</span>}</span>
            </label>
          ))}
          <label className="pt-rule threshold">
            <span>{t("Alert on daily moves of at least")}</span>
            <input className="input num" type="number" min={1} max={50} step={0.5} defaultValue={p.price_move_pct} onBlur={(e) => threshold(Number(e.target.value))} />
            <span>%</span>
          </label>
        </div>
      )}
    </>
  );
}

export default function Portfolio() {
  const { t } = useT();
  const me = useMe();
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "holdings";
  const tabs: { key: Tab; label: string }[] = [
    { key: "holdings", label: "Holdings" }, { key: "watchlist", label: "Watchlist" }, { key: "intelligence", label: "Intelligence" }, { key: "alerts", label: "Alerts" },
  ];
  if (me.isLoading) return <div className="hm pt"><div className="wire-skel"><span className="skel w60" /></div></div>;
  if (!me.data?.user) {
    return (
      <div className="hm pt">
        <header className="pl-head"><div className="hm-eyebrow"><span>{t("Portfolio")}</span></div><h1>{t("Your investments, with context")}</h1>
          <p className="hm-lede">{t("Add what you own and what you watch. Nexis follows the real developments that affect them and connects you to what investors are discussing. Private to you, and no brokerage or bank login needed.")}</p>
          <Link className="btn primary" to="/login?mode=signup&next=/portfolio">{t("Create a free account")}</Link> <Link className="btn" to="/login?next=/portfolio">{t("Sign in")}</Link>
        </header>
      </div>
    );
  }
  return (
    <div className="hm pt">
      <header className="pl-head"><div className="hm-eyebrow"><span>{t("Portfolio")}</span><span>{t("Private to you")}</span></div><h1>{t("Your portfolio")}</h1></header>
      <div className="pt-tabs" role="tablist">
        {tabs.map((x) => <button key={x.key} role="tab" aria-selected={tab === x.key} className={tab === x.key ? "on" : ""} onClick={() => setSp(x.key === "holdings" ? {} : { tab: x.key })}>{t(x.label)}</button>)}
      </div>
      {tab === "holdings" && <Holdings addSymbol={sp.get("add")} />}
      {tab === "watchlist" && <Watchlist />}
      {tab === "intelligence" && <Intelligence focus={sp.get("symbol")} />}
      {tab === "alerts" && <Alerts />}
    </div>
  );
}
