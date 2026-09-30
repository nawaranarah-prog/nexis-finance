import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Avatar, Change, fmtPrice, SymbolSearch, useMe } from "../components/market";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { useT } from "../i18n";

const TILES = [
  { symbol: "DFMGI.AE", label: "DFM General" }, { symbol: "FADGI.AD", label: "FTSE ADX General" }, { symbol: "USDAED=X", label: "USD / AED" },
  { symbol: "^TNX", label: "US 10-year yield" }, { symbol: "BZ=F", label: "Brent crude" }, { symbol: "GC=F", label: "Gold" },
];
const QUICK = [
  { label: "Compare UAE banks", to: "/compare?s=FAB.AD,EMIRATESNBD.AE,ADCB.AD,ADIB.AD,DIB.AE&p=1y" },
  { label: "UAE property stocks", to: "/compare?s=EMAAR.AE,ALDAR.AD,EMAARDEV.AE,TECOM.AE,DFMGI.AE&p=1y" },
  { label: "Stocks vs UAE bonds", to: "/compare?s=FADGI.AD,DFMGI.AE,UAE0734USD.BOND,UAEGS0233AED.BOND,GC%3DF&p=1y" },
  { label: "Value Aldar", to: "/valuation/ALDAR.AD" },
  { label: "UAE bonds & sukuk", to: "/markets?list=uae_bonds" },
];

export default function Home() {
  const nav = useNavigate();
  const me = useMe().data?.user;
  const [q, setQ] = useState("");
  const { t: tr } = useT();
  const tiles = useQuery({ queryKey: ["home-tiles"], queryFn: () => api.get<AnyObj[]>("/markets/quotes", { symbols: TILES.map((t) => t.symbol).join(",") }), refetchInterval: 60_000 });
  const uae = useQuery({ queryKey: ["mk-list", "uae"], queryFn: () => api.get<AnyObj>("/markets/lists/uae"), refetchInterval: 120_000 });
  const feed = useQuery({ queryKey: ["social", "home-preview"], queryFn: () => api.get<AnyObj>("/social/feed", { mode: "latest" }), staleTime: 120_000 });
  const bySym = Object.fromEntries((tiles.data ?? []).map((t) => [t.symbol, t]));
  const movers = (uae.data?.items ?? []).filter((x: AnyObj) => x.change_pct !== null && x.change_pct !== undefined);
  const gainers = [...movers].sort((a, b) => b.change_pct - a.change_pct).slice(0, 5);
  const losers = [...movers].sort((a, b) => a.change_pct - b.change_pct).slice(0, 5);
  const posts: AnyObj[] = (feed.data?.items ?? []).slice(0, 6);
  const ask = (e: React.FormEvent) => { e.preventDefault(); if (q.trim().length > 1) nav(`/advisor?q=${encodeURIComponent(q.trim())}`); };

  return (
    <div className="home">
      <section className="home-hero">
        <div className="home-hello">{me ? `${tr("Welcome back")}${tr(", ")}${(me.display_name || me.username).split(" ")[0]}` : tr("Your UAE investing hub")}</div>
        <h1>{tr("Research UAE and global markets, ask an AI advisor, and follow what investors are saying.")}</h1>
        <SymbolSearch placeholder={tr("Search any UAE stock, bond or sukuk — or any global market…")} onPick={(s) => nav(`/markets/${encodeURIComponent(s.symbol)}`)} />
        <div className="home-quick">
          {QUICK.map((x) => <Link key={x.label} className="chip" to={x.to}>{tr(x.label)}</Link>)}
        </div>
      </section>

      <section className="home-tiles">
        {TILES.map((t) => {
          const d = bySym[t.symbol];
          return (
            <Link key={t.symbol} to={`/markets/${encodeURIComponent(t.symbol)}`} className="home-tile">
              <span className="xs muted">{tr(t.label)}</span>
              <span className="home-tile-v num">{d ? (t.symbol === "^TNX" ? `${d.price?.toFixed(2)}%` : fmtPrice(d.price, undefined, t.symbol.endsWith("=X") ? 4 : 2)) : "—"}</span>
              {d ? <Change pct={d.change_pct} /> : <span className="xs muted">loading…</span>}
            </Link>
          );
        })}
      </section>

      <div className="home-grid">
        <section className="home-card home-advisor">
          <div className="home-card-head"><span className="home-badge ai">✦</span><b>{tr("AI Advisor")}</b><Link className="link-btn small" to="/advisor">{tr("Open")}</Link></div>
          <p className="small text2">{tr("Ask like you would ask a private banker. It checks live prices, news, analyst ratings and valuations before it answers.")}</p>
          <form onSubmit={ask} className="row" style={{ gap: 8 }}>
            <input className="input" style={{ flex: 1, height: 42 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("e.g. Should I buy FAB or ADCB for dividends?")} aria-label="Ask the AI advisor" />
            <button className="btn primary" style={{ height: 42 }}>{tr("Ask")}</button>
          </form>
          <div className="row" style={{ gap: 6, marginTop: 10, flexWrap: "wrap" }}>
            {["Which UAE banks look cheapest?", "Is Aldar a good buy now?", "What UAE bonds or sukuk yield the most?"].map((x) => tr(x)).map((x) => (
              <button key={x} className="chip" onClick={() => nav(`/advisor?q=${encodeURIComponent(x)}`)}>{x}</button>
            ))}
          </div>
        </section>

        <section className="home-card home-finsta">
          <div className="home-card-head"><span className="home-badge finsta">F</span><b>{tr("Finstagram")}</b><Link className="link-btn small" to="/finstagram">{tr("Open feed")}</Link></div>
          <div className="home-posts">
            {posts.map((p) => {
              const vid = /watch\?v=([\w-]+)/.exec(p.link?.url ?? "")?.[1];
              const img = p.image_url ?? (vid ? `https://i.ytimg.com/vi/${vid}/hqdefault.jpg` : p.link?.image);
              return (
                <Link key={p.id} to={`/finstagram/p/${p.id}`} className="home-post">
                  {img ? <img src={img} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="home-post-text">{(p.link?.title ?? p.body).slice(0, 90)}</span>}
                  <span className="home-post-meta"><Avatar user={p.author} size={18} /> {p.author.username}</span>
                </Link>
              );
            })}
            {!posts.length && <div className="small text2">{tr("Loading the latest posts…")}</div>}
          </div>
          <Link className="btn primary block" to="/finstagram" style={{ marginTop: 10 }}>{tr("Open Finstagram")}</Link>
        </section>

        <section className="home-card">
          <div className="home-card-head"><b>{tr("UAE movers today")}</b><Link className="link-btn small" to="/markets">{tr("All UAE shares")} ({uae.data?.items?.length ?? ""})</Link></div>
          <div className="home-movers">
            {[["Top gainers", gainers], ["Biggest decliners", losers]].map(([title, rows]) => (
              <div key={title as string}>
                <div className="xs muted" style={{ marginBottom: 6 }}>{tr(title as string)}</div>
                {(rows as AnyObj[]).map((x) => (
                  <Link key={x.symbol} to={`/markets/${encodeURIComponent(x.symbol)}`} className="home-mover">
                    <span className="grow ellipsis"><b>{x.symbol.replace(/\.(AE|AD)$/, "")}</b> <span className="xs muted">{x.name}</span></span>
                    <span className="num small">{fmtPrice(x.price)}</span>
                    <Change pct={x.change_pct} />
                  </Link>
                ))}
              </div>
            ))}
          </div>
        </section>
      </div>
      <div className="xs muted" style={{ marginTop: 14 }}>{tr("Market data from public sources, delayed. Educational tools — not personalised financial advice.")}</div>
    </div>
  );
}
