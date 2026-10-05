import { handlePlanError } from "../../components/pro";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, fmtPrice, useMe } from "../../components/market";
import { ago, Composer, DiscussionRow, SectionHead, topicPath, useParticipate } from "../../components/pulse";
import { toast } from "../../components/toast";
import { ErrorState } from "../../components/ui";
import { useT } from "../../i18n";
import { api, ApiError, errorMessage } from "../../services/api";
import type { AnyObj } from "../../types/api";

function Track({ symbol }: { symbol: string }) {
  const me = useMe().data?.user;
  const qc = useQueryClient();
  const participate = useParticipate();
  const st = useQuery({ queryKey: ["track", symbol], queryFn: () => api.get<AnyObj>(`/me/track/${encodeURIComponent(symbol)}`), enabled: !!me });
  const watching = st.data?.watching;
  const toggle = async () => {
    try {
      await participate(() => (watching ? api.del(`/me/watchlist/${encodeURIComponent(symbol)}`) : api.put(`/me/watchlist/${encodeURIComponent(symbol)}`, {})));
      qc.invalidateQueries({ queryKey: ["track", symbol] });
      qc.invalidateQueries({ queryKey: ["watchlist"] });
    } catch (x) { if (!handlePlanError(x)) toast("error", "Couldn't update your watchlist", errorMessage(x)); }
  };
  if (st.data?.holding) return <Link className="btn sm" to={`/my-nexis/asset/${encodeURIComponent(symbol)}`}>In your investments</Link>;
  return <button type="button" className={`btn sm ${watching ? "on" : ""}`} aria-pressed={!!watching} onClick={toggle}>{watching ? "On your watchlist" : "Add to watchlist"}</button>;
}

export default function PulseAsset() {
  const { symbol = "" } = useParams();
  const sym = decodeURIComponent(symbol).toUpperCase();
  const { lang } = useT();
  const [composing, setComposing] = useState(false);
  const page = useQuery({ queryKey: ["pulse-asset", sym], queryFn: () => api.get<AnyObj>(`/pulse/assets/${encodeURIComponent(sym)}`) });
  const feed = useInfiniteQuery({
    queryKey: ["pulse-feed", "asset", sym],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/pulse/feed", { sort: "activity", symbol: sym, kind: "community", cursor: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? null,
  });
  const web = useQuery({ queryKey: ["pulse-feed", "asset-web", sym], queryFn: () => api.get<AnyObj>("/pulse/feed", { sort: "activity", symbol: sym, kind: "public", limit: 6 }) });
  const p = page.data;
  const ed = p?.editorial;
  const items: AnyObj[] = (feed.data?.pages ?? []).flatMap((x) => x.items);
  if (page.isError) {
    const missing = page.error instanceof ApiError && page.error.status === 404;
    return <div className="np">{missing ? <div className="np-missing"><h1>{sym}</h1><p>No market data or Pulse discussions found for this symbol. Dubai shares end in .AE, Abu Dhabi in .AD.</p><Link className="btn" to="/pulse">Back to Pulse</Link></div> : <ErrorState error={page.error} />}</div>;
  }
  return (
    <div className="np">
      <header className="np-head">
        <div className="np-eyebrow"><Link to="/pulse">Nexis Pulse</Link><span className="mono">{sym}</span></div>
        <h1>{p?.name ?? sym}</h1>
        <div className="np-d-sub">
          {p?.quote?.price != null && <span className="np-quote static"><span className="num">{fmtPrice(p.quote.price)} {p.quote.currency}</span><Change pct={p.quote.change_pct} />{p.quote.exchange && <span className="muted">{p.quote.exchange} · delayed</span>}</span>}
          {(p?.topics ?? []).map((t: AnyObj) => <Link key={t.key} to={topicPath(t.key)} className="np-topic">{t.label}</Link>)}
        </div>
        <div className="np-head-actions">
          <Link className="btn sm ghost" to={`/markets/${encodeURIComponent(sym)}`}>Chart and fundamentals</Link>
          <Track symbol={sym} />
          <button type="button" className="btn primary sm" onClick={() => setComposing(true)}>Discuss {sym}</button>
        </div>
      </header>
      {composing && <Composer symbol={sym} onClose={() => setComposing(false)} />}

      <section className="np-sec">
        <SectionHead title="Nexis editorial">{ed && <span className="xs muted">Updated {ago(ed.updated_at ?? ed.created_at, lang)}</span>}</SectionHead>
        {page.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /></div>}
        {p && !ed && <p className="np-empty-line">Nexis hasn't published context on {sym} yet. It does once there is enough sourced coverage to describe a real debate — never before.</p>}
        {ed && (
          <Link to={ed.url} className="np-ed-card">
            <b dir="auto">{ed.title}</b>
            {ed.editorial?.what_happened && <p dir="auto">{ed.editorial.what_happened}</p>}
            <span className="np-split">
              <span className="pos">{ed.editorial?.bull_case.length ?? 0} bull points</span><span className="neg">{ed.editorial?.bear_case.length ?? 0} bear points</span>
              <span>{ed.editorial?.open_questions.length ?? 0} open questions</span><span>{ed.sources.length} sources</span>
              <span>{ed.counts.comments ? `${ed.counts.comments} replies` : "No replies yet"}</span>
            </span>
          </Link>
        )}
      </section>

      {(web.data?.items ?? []).length > 0 && (
        <section className="np-sec">
          <SectionHead title={`What people are arguing about ${sym} around the web`}><span className="xs muted">Reddit, Hacker News, StockTwits · no usernames</span></SectionHead>
          <ol className="np-list">{web.data!.items.map((d: AnyObj) => <DiscussionRow key={d.id} d={d} showAsset={false} />)}</ol>
        </section>
      )}

      <section className="np-sec">
        <SectionHead title={`Community discussions about ${sym}`}><span className="xs muted">{p ? `${p.discussions} in total` : ""}</span></SectionHead>
        {feed.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /></div>}
        {feed.data && !items.length && <p className="np-empty-line">No community discussions about {sym} yet. If you have a view, <button type="button" className="link-btn" onClick={() => setComposing(true)}>start one</button> — it's posted anonymously.</p>}
        <ol className="np-list">{items.map((d) => <DiscussionRow key={d.id} d={d} showAsset={false} />)}</ol>
        {feed.hasNextPage && <button type="button" className="btn sm np-more" onClick={() => feed.fetchNextPage()}>More</button>}
      </section>
      <p className="np-disclaimer">Prices are delayed. Discussions are opinions, not advice.</p>
    </div>
  );
}
