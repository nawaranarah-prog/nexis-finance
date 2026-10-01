import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fmtPrice, useMe } from "../components/market";
import { ResearchSearch, type ResearchSearchHandle } from "../components/ResearchSearch";
import { Sparkline } from "../components/Sparkline";
import { toast } from "../components/toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

// ------------------------------------------------------------------ reference data (labels and units, never values)

interface Instrument { symbol: string; label: string; unit: string; digits: number; bp?: boolean; source: string }
const STRIP: Instrument[] = [
  { symbol: "DFMGI.AE", label: "DFM General", unit: "pts", digits: 2, source: "TradingView" },
  { symbol: "FADGI.AD", label: "FTSE ADX General", unit: "pts", digits: 2, source: "TradingView" },
  { symbol: "USDAED=X", label: "USD / AED", unit: "AED", digits: 4, source: "Yahoo Finance" },
  { symbol: "^TNX", label: "US 10Y Treasury", unit: "%", digits: 3, bp: true, source: "Yahoo Finance" },
  { symbol: "BZ=F", label: "Brent crude", unit: "USD/bbl", digits: 2, source: "Yahoo Finance" },
  { symbol: "GC=F", label: "Gold", unit: "USD/oz", digits: 1, source: "Yahoo Finance" },
];

/** Starting points. Each one opens an existing page or sends the question to the real advisor. */
const EXAMPLES: { text: string; to: string }[] = [
  { text: "Compare FAB and Emirates NBD", to: "/compare?s=FAB.AD,EMIRATESNBD.AE&p=1y" },
  { text: "Why did NVIDIA move today?", to: "/advisor?q=" + encodeURIComponent("Why did NVIDIA move today?") },
  { text: "Is Aldar expensive vs. peers?", to: "/advisor?q=" + encodeURIComponent("Is Aldar expensive versus its UAE property peers?") },
  { text: "UAE bonds and sukuk", to: "/markets?list=uae_bonds" },
  { text: "What's happening with gold?", to: "/advisor?q=" + encodeURIComponent("What's happening with gold?") },
];
const STARTERS = ["EMAAR.AE", "FAB.AD", "ALDAR.AD", "EMIRATESNBD.AE", "ADNOCGAS.AD", "NVDA"];

// ------------------------------------------------------------------ time helpers

const dubai = (iso: string, opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", ...opts }).format(new Date(iso));
function ago(iso: string, lang = "en"): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
  if (s < 60) return rtf.format(0, "minute");
  if (s < 3600) return rtf.format(-Math.floor(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.floor(s / 3600), "hour");
  return dubai(iso, { day: "numeric", month: "short" });
}
/** Regular ADX/DFM session from the clock: Monday–Friday 10:00–15:00 Gulf time. Public holidays are not known here, so this says "regular hours". */
function uaeSession(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const mins = Number(get("hour")) * 60 + Number(get("minute"));
  const weekday = !["Sat", "Sun"].includes(get("weekday"));
  return weekday && mins >= 600 && mins < 900;
}

// ------------------------------------------------------------------ pieces

function Move({ q, ins }: { q: AnyObj; ins?: Instrument }) {
  if (q.change_pct === null || q.change_pct === undefined) return <span className="mv muted">—</span>;
  const dir = q.change_pct > 0 ? "pos" : q.change_pct < 0 ? "neg" : "flat";
  const sign = q.change > 0 ? "+" : q.change < 0 ? "−" : "";
  const abs = Math.abs(q.change ?? 0);
  const digits = ins?.digits ?? (Math.abs(q.price ?? 0) < 1 ? 4 : 2);
  const absText = ins?.bp ? `${sign}${(abs * 100).toFixed(1)} bp` : `${sign}${abs.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  return (
    <span className={`mv ${dir}`}>
      <span className="mv-abs">{absText}</span>
      <span className="mv-pct">{sign}{Math.abs(q.change_pct * 100).toFixed(2)}%</span>
    </span>
  );
}

/** Briefly tints a number when a refresh changes it, so live updates are noticed without moving anything. */
function Tick({ value, children }: { value: number | undefined; children: React.ReactNode }) {
  const prev = useRef(value);
  const [flash, setFlash] = useState<"" | "up" | "down">("");
  useEffect(() => {
    if (prev.current !== undefined && value !== undefined && value !== prev.current) {
      setFlash(value > prev.current ? "up" : "down");
      const id = window.setTimeout(() => setFlash(""), 900);
      prev.current = value;
      return () => window.clearTimeout(id);
    }
    prev.current = value;
  }, [value]);
  return <span className={`tick ${flash}`}>{children}</span>;
}

function stamp(q: AnyObj, ins: Instrument) {
  if (!q.market_time) return "Delayed";
  // TradingView index values carry the trading day, not the time of the last trade.
  if (ins.source === "TradingView") return `Delayed · ${dubai(q.market_time, { day: "numeric", month: "short" })}`;
  const state = q.market_state === "REGULAR" ? "Open" : q.market_state ? "Closed" : null;
  return `${state ? `${state} · ` : ""}${dubai(q.market_time, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })} GST`;
}

function MarketStrip() {
  const { t } = useT();
  const q = useQuery({ queryKey: ["home-tiles"], queryFn: () => api.get<AnyObj[]>("/markets/quotes", { symbols: STRIP.map((s) => s.symbol).join(",") }), refetchInterval: 60_000 });
  const bySym = Object.fromEntries((q.data ?? []).map((x) => [x.symbol, x]));
  return (
    <section className="hm-strip" aria-label={t("Markets now")}>
      <div className="strip">
        {STRIP.map((ins) => {
          const d = bySym[ins.symbol];
          return (
            <Link key={ins.symbol} to={`/markets/${encodeURIComponent(ins.symbol)}`} className="strip-cell">
              <span className="strip-name">{t(ins.label)}</span>
              {d ? (
                <>
                  <span className="strip-row">
                    <Tick value={d.price}><span className="strip-v num">{fmtPrice(d.price, undefined, ins.digits)}</span></Tick>
                    <span className="strip-unit">{ins.unit}</span>
                  </span>
                  <span className="strip-row between"><Move q={d} ins={ins} /><Sparkline symbol={ins.symbol} width={56} height={18} /></span>
                  <span className="strip-meta">{stamp(d, ins)}</span>
                </>
              ) : q.isLoading ? (
                <><span className="skel w60" /><span className="skel w40" /></>
              ) : (
                <span className="strip-na">{t("Unavailable right now")}</span>
              )}
            </Link>
          );
        })}
      </div>
      <div className="strip-note">
        {q.isError ? <span className="neg">{t("Market data could not be loaded")} — {errorMessage(q.error)} <button className="link-btn" onClick={() => q.refetch()}>{t("Retry")}</button></span>
          : <>{t("Delayed quotes · UAE indices via TradingView, FX, rates and commodities via Yahoo Finance · lines show the last month of daily closes")}</>}
      </div>
    </section>
  );
}

function SectionHead({ title, sub, to, cta }: { title: string; sub?: string; to?: string; cta?: string }) {
  return (
    <div className="sec-head">
      <h2>{title}</h2>
      {sub && <span className="sec-sub">{sub}</span>}
      {to && <Link className="sec-link" to={to}>{cta} <span aria-hidden>→</span></Link>}
    </div>
  );
}

/** Market news and member posts from Finstagram, as an editorial wire: one lead story and a dated list. */
function Wire() {
  const { t, lang } = useT();
  const feed = useQuery({ queryKey: ["social", "home-preview"], queryFn: () => api.get<AnyObj>("/social/feed", { mode: "latest" }), staleTime: 120_000 });
  const [broken, setBroken] = useState<Record<number, boolean>>({});
  const posts: AnyObj[] = feed.data?.items ?? [];
  const imageOf = (p: AnyObj): string | null => {
    if (broken[p.id]) return null;
    const vid = /watch\?v=([\w-]+)/.exec(p.link?.url ?? "")?.[1];
    return p.image_url ?? (vid ? `https://i.ytimg.com/vi/${vid}/hqdefault.jpg` : p.link?.image ?? null);
  };
  const lead = posts.find((p) => imageOf(p)) ?? posts[0];
  const rest = posts.filter((p) => p !== lead).slice(0, 7);
  const headline = (p: AnyObj) => String(p.link?.title || p.body.split(/\n+/)[0]).replace(/^▶ /, "");
  const summary = (p: AnyObj) => {
    const body = p.body.split(/\n\n+/);
    const text = (p.link ? body.slice(1) : body).join(" ").replace(/[#$][\w.=^-]+/g, "").replace(/\s+/g, " ").trim();
    return text.length > 220 ? `${text.slice(0, 217)}…` : text;
  };
  const Meta = ({ p }: { p: AnyObj }) => {
    const news = p.author.kind === "page";
    return (
      <span className="wire-meta">
        <span className={`kind ${news ? "news" : "member"}`}>{news ? t("News") : t("Member post")}</span>
        <span>{news ? p.link?.source ?? p.author.display_name : `@${p.author.username}`}</span>
        <time dateTime={p.created_at}>{ago(p.created_at, lang)}</time>
        {p.symbols?.slice(0, 3).map((s: AnyObj) => <span key={s.symbol} className="mono tk">{s.symbol}</span>)}
        {!p.symbols?.length && p.tags?.[0] && <span className="tag">#{p.tags[0]}</span>}
      </span>
    );
  };
  return (
    <section className="hm-wire">
      <SectionHead title={t("Market wire")} sub={t("Latest on Finstagram")} to="/finstagram" cta={t("Open Finstagram")} />
      {feed.isLoading && <div className="wire-skel"><span className="skel lead" /><span className="skel w80" /><span className="skel w60" /><span className="skel w70" /></div>}
      {feed.isError && <div className="hm-error">{t("The feed could not be loaded")} — {errorMessage(feed.error)} <button className="link-btn" onClick={() => feed.refetch()}>{t("Retry")}</button></div>}
      {lead && (
        <article className={`wire-lead ${imageOf(lead) ? "" : "no-img"}`}>
          {imageOf(lead) && (
            <Link to={`/finstagram/p/${lead.id}`} className="wire-img" tabIndex={-1} aria-hidden>
              <img src={imageOf(lead)!} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken((b) => ({ ...b, [lead.id]: true }))} />
            </Link>
          )}
          <div className="wire-lead-text">
            <Meta p={lead} />
            <Link to={`/finstagram/p/${lead.id}`} className="wire-lead-h" dir="auto">{headline(lead)}</Link>
            {summary(lead) && <p className="wire-sum" dir="auto">{summary(lead)}</p>}
            <Link className="wire-ask" to={`/advisor?post=${lead.id}`}>
              <span className="top-link-mark ai" aria-hidden>✦</span>{t("Ask the advisor what this story means")}<span aria-hidden className="arr">→</span>
            </Link>
          </div>
        </article>
      )}
      <ol className="wire-list">
        {rest.map((p) => (
          <li key={p.id}>
            <Link to={`/finstagram/p/${p.id}`} className="wire-item">
              <time className="wire-time num" dateTime={p.created_at}>{dubai(p.created_at, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}</time>
              <span className="wire-body">
                <span className="wire-h" dir="auto">{headline(p)}</span>
                <Meta p={p} />
              </span>
            </Link>
          </li>
        ))}
      </ol>
      {!feed.isLoading && !feed.isError && !posts.length && <div className="hm-empty">{t("No posts yet. News pages refresh daily.")}</div>}
    </section>
  );
}

/** The tickers the user follows on Finstagram double as their watchlist. */
function Watchlist() {
  const { t } = useT();
  const qc = useQueryClient();
  const me = useMe();
  const signedIn = !!me.data?.user;
  const following = useQuery({ queryKey: ["social", "following"], queryFn: () => api.get<AnyObj>("/social/following"), enabled: signedIn });
  const symbols: string[] = following.data?.symbols ?? [];
  const quotes = useQuery({
    queryKey: ["watch-quotes", symbols.join(",")],
    queryFn: () => api.get<AnyObj[]>("/markets/quotes", { symbols: symbols.join(",") }),
    enabled: symbols.length > 0, refetchInterval: 60_000,
  });
  const [leaving, setLeaving] = useState<string | null>(null);
  const bySym = Object.fromEntries((quotes.data ?? []).map((x) => [x.symbol, x]));

  const toggle = async (symbol: string, add: boolean) => {
    if (!add) setLeaving(symbol);
    try {
      await api.post("/social/topics/follow", { kind: "symbol", value: symbol });
      if (!add) await new Promise((r) => window.setTimeout(r, 180));
      await qc.invalidateQueries({ queryKey: ["social", "following"] });
      toast("success", add ? `${symbol} ${t("added to your watchlist")}` : `${symbol} ${t("removed from your watchlist")}`);
    } catch (e) { toast("error", t("Couldn't update your watchlist"), errorMessage(e)); }
    finally { setLeaving(null); }
  };

  return (
    <section className="hm-watch">
      <SectionHead title={t("Watchlist")} sub={signedIn && symbols.length ? `${symbols.length}` : undefined} />
      {!signedIn && !me.isLoading && (
        <div className="hm-empty-state">
          <p>{t("Keep the stocks, bonds and indices you research in one place, with live prices and a month of history.")}</p>
          <Link className="btn primary sm" to="/login?mode=signup&next=/">{t("Create a free account")}</Link>
          <span className="xs muted"> {t("or")} <Link to="/login?next=/">{t("sign in")}</Link></span>
        </div>
      )}
      {signedIn && following.isSuccess && !symbols.length && (
        <div className="hm-empty-state">
          <p>{t("Your watchlist is empty. Follow a ticker here or on any Finstagram stock page.")}</p>
          <div className="starters">
            {STARTERS.map((s) => <button key={s} className="starter mono" onClick={() => toggle(s, true)}>+ {s}</button>)}
          </div>
        </div>
      )}
      {symbols.length > 0 && (
        <table className="wl">
          <thead><tr><th>{t("Asset")}</th><th className="r">{t("Last")}</th><th className="r">{t("Day")}</th><th className="hide-xs" aria-label={t("1 month")} /><th aria-label={t("Remove")} /></tr></thead>
          <tbody>
            {symbols.map((s) => {
              const d = bySym[s];
              return (
                <tr key={s} className={leaving === s ? "leaving" : ""}>
                  <td><Link to={`/markets/${encodeURIComponent(s)}`} className="wl-asset"><span className="mono wl-tk">{s}</span><span className="wl-name">{d?.name ?? ""}</span></Link></td>
                  <td className="r num">{d ? fmtPrice(d.price) : quotes.isLoading ? <span className="skel w40" /> : "—"}<span className="wl-ccy">{d?.currency ?? ""}</span></td>
                  <td className="r">{d ? <Move q={d} /> : null}</td>
                  <td className="hide-xs"><Sparkline symbol={s} width={60} height={18} /></td>
                  <td><button className="wl-x" aria-label={`${t("Remove")} ${s}`} title={t("Remove")} onClick={() => toggle(s, false)}>×</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** What Finstagram is talking about: mention counts only. Not sentiment, and not a signal. */
function Pulse() {
  const { t } = useT();
  const q = useQuery({ queryKey: ["social", "trending"], queryFn: () => api.get<AnyObj>("/social/trending"), staleTime: 300_000 });
  const rows: AnyObj[] = (q.data?.symbols ?? []).slice(0, 6);
  const max = Math.max(1, ...rows.map((r) => r.posts));
  return (
    <section className="hm-pulse">
      <SectionHead title={t("Nexis Pulse")} sub={t("Most mentioned · 7 days")} to="/pulse" cta={t("Investor opinions")} />
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /><span className="skel w70" /></div>}
      <ol className="pulse">
        {rows.map((r, i) => (
          <li key={r.symbol}>
            <Link to={`/pulse/${encodeURIComponent(r.symbol)}`} className="pulse-row" title={t("What Reddit, X and StockTwits are saying")}>
              <span className="pulse-rank num">{i + 1}</span>
              <span className="pulse-name"><span className="mono">{r.symbol}</span><span className="wl-name">{r.name ?? ""}</span></span>
              <span className="pulse-bar" aria-hidden><span style={{ transform: `scaleX(${r.posts / max})` }} /></span>
              <span className="pulse-n num">{r.posts}</span>
            </Link>
          </li>
        ))}
      </ol>
      <p className="hm-fine">{t("Number of Finstagram posts that mention each ticker, mostly from news pages — a measure of attention, not sentiment or a prediction. Open a ticker to read what people say about it on Reddit, X and StockTwits.")}</p>
    </section>
  );
}

function Movers() {
  const { t } = useT();
  const uae = useQuery({ queryKey: ["mk-list", "uae"], queryFn: () => api.get<AnyObj>("/markets/lists/uae"), refetchInterval: 120_000 });
  const items: AnyObj[] = (uae.data?.items ?? []).filter((x: AnyObj) => Number.isFinite(x.change_pct));
  const up = [...items].sort((a, b) => b.change_pct - a.change_pct).slice(0, 5);
  const down = [...items].sort((a, b) => a.change_pct - b.change_pct).slice(0, 5);
  const Table = ({ title, rows }: { title: string; rows: AnyObj[] }) => (
    <div className="mv-col">
      <div className="mv-title">{title}</div>
      {rows.map((x) => (
        <Link key={x.symbol} to={`/markets/${encodeURIComponent(x.symbol)}`} className="mv-row">
          <span className="mono wl-tk">{x.symbol.replace(/\.(AE|AD)$/, "")}</span>
          <span className="wl-name">{x.name}</span>
          <span className="num r">{fmtPrice(x.price)}</span>
          <Move q={x} />
        </Link>
      ))}
      {uae.isLoading && <><span className="skel w80" /><span className="skel w70" /><span className="skel w80" /></>}
    </div>
  );
  return (
    <section className="hm-movers">
      <SectionHead title={t("UAE movers")} sub={uae.data ? `${items.length} ${t("ADX and DFM shares")} · ${t("delayed")}` : undefined} to="/markets?list=uae" cta={t("All UAE shares")} />
      {uae.isError ? <div className="hm-error">{errorMessage(uae.error)}</div> : (
        <div className="mv-cols"><Table title={t("Top gainers")} rows={up} /><Table title={t("Biggest decliners")} rows={down} /></div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ page

export default function Home() {
  const { t, lang } = useT();
  const search = useRef<ResearchSearchHandle>(null);
  const status = useQuery({ queryKey: ["advisor-status"], queryFn: () => api.get<AnyObj>("/advisor/status"), staleTime: 300_000 });
  const open = uaeSession();
  const today = new Intl.DateTimeFormat(lang === "ar" ? "ar-AE" : "en-GB", { timeZone: "Asia/Dubai", weekday: "long", day: "numeric", month: "long" }).format(new Date());

  // "/" jumps to the research box from anywhere on the page.
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.key === "/" && !e.ctrlKey && !e.metaKey && !["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) && !el.isContentEditable) { e.preventDefault(); search.current?.focus(); }
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, []);

  const s = status.data;
  return (
    <div className="hm">
      <header className="hm-head">
        <div className="hm-eyebrow">
          <span>{today}</span>
          <span className={`session ${open ? "open" : ""}`}><i aria-hidden />{open ? t("ADX & DFM in session") : t("ADX & DFM closed")}</span>
          <span className="hide-xs">{t("Regular hours Mon–Fri 10:00–15:00 GST")}</span>
        </div>
        <h1>{t("Understand the move before you make one.")}</h1>
        <p className="hm-lede">{t("Search any ADX or DFM share, UAE bond or global market — or ask a research question. The advisor checks live prices, news and valuations, and shows its working.")}</p>
        <ResearchSearch ref={search} placeholder={window.matchMedia("(max-width: 720px)").matches ? t("Ticker or question") : t("Search a ticker or ask a question — e.g. Compare FAB and Emirates NBD")} />
        <div className="hm-try">
          <span className="hm-try-label">{t("Try")}</span>
          {EXAMPLES.map((x) => <Link key={x.text} to={x.to}>{t(x.text)}</Link>)}
        </div>
        <div className="hm-advisor">
          <span className={`dot ${s?.available ? "on" : ""}`} aria-hidden />
          {s ? (s.available
            ? <>{t("AI advisor online")} · <span className="mono">{String(s.model ?? "").replace(/^\w+\//, "")}</span><span className="hide-xs"> · {t("uses live quotes, news, price statistics, comparisons and DCF valuations")}</span></>
            : <>{t("AI model offline — the advisor answers with its built-in analyst engine from live data")}</>) : t("Checking the AI advisor…")}
          <Link to="/advisor" className="sec-link">{t("Open the advisor")} <span aria-hidden>→</span></Link>
        </div>
      </header>

      <MarketStrip />

      <div className="hm-grid">
        <Wire />
        <aside className="hm-side">
          <Watchlist />
          <Pulse />
        </aside>
        <Movers />
      </div>

      <footer className="hm-foot">{t("Market data from public sources, delayed. AI answers are generated from that data and can be wrong. Educational tools — not personalised financial advice.")}</footer>
    </div>
  );
}
