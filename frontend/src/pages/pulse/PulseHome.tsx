import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ago, assetPath, Composer, DiscussionRow, SectionHead, stamp, topicPath } from "../../components/pulse";
import { ErrorState } from "../../components/ui";
import { useT } from "../../i18n";
import { api } from "../../services/api";
import type { AnyObj } from "../../types/api";

const SORTS = [
  { key: "trending", label: "Trending" },
  { key: "latest", label: "Latest" },
  { key: "discussed", label: "Most discussed" },
  { key: "activity", label: "Recent activity" },
  { key: "following", label: "Following" },
  { key: "for_you", label: "For you" },
  { key: "saved", label: "Saved" },
] as const;
const KINDS = [
  { key: "", label: "Everything" },
  { key: "community", label: "Community" },
  { key: "editorial", label: "Nexis editorial" },
] as const;
const UPDATE_KIND: Record<string, string> = {
  development: "New development", what_changed: "What changed", new_bull: "New bullish argument", new_bear: "New bearish argument",
  open_question: "Open question", correction: "Correction",
};

/** Ask the server to advance Pulse (it throttles itself; nothing happens if it ran recently). */
function useEngineNudge() {
  useEffect(() => {
    const id = window.setTimeout(() => { void api.post("/pulse/engine/tick").catch(() => undefined); }, 2500);
    return () => window.clearTimeout(id);
  }, []);
}

function SearchBox({ initial = "" }: { initial?: string }) {
  const nav = useNavigate();
  const [q, setQ] = useState(initial);
  useEffect(() => setQ(initial), [initial]);
  return (
    <form className="np-search" role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/pulse/search?q=${encodeURIComponent(q.trim())}`); }}>
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search discussions, assets, topics…" aria-label="Search Pulse" />
    </form>
  );
}

function Timeline({ items }: { items: AnyObj[] }) {
  const { lang } = useT();
  if (!items.length) return <p className="np-empty-line">Nexis records each material change to an editorial discussion here, with its time.</p>;
  return (
    <ol className="np-timeline">
      {items.map((u, i) => (
        <li key={i}>
          <time dateTime={u.created_at} title={stamp(u.created_at, lang)}>{ago(u.created_at, lang)}</time>
          <span className="np-tl-kind">{UPDATE_KIND[u.kind] ?? "Update"}</span>
          <Link to={`${u.discussion.url}#updates`} dir="auto">{u.headline}</Link>
          <span className="np-tl-of" dir="auto">{u.discussion.symbol ? <span className="mono">{u.discussion.symbol} · </span> : null}{u.discussion.title}</span>
        </li>
      ))}
    </ol>
  );
}

function Rail({ overview }: { overview: AnyObj | undefined }) {
  const s = overview?.stats;
  return (
    <aside className="np-rail">
      <section>
        <SectionHead title="How the debate moved" />
        {overview ? <Timeline items={overview.timeline ?? []} /> : <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /></div>}
      </section>
      <section>
        <SectionHead title="Topics" />
        <div className="np-topic-cloud">
          {(overview?.topics ?? []).map((t: AnyObj) => (
            <Link key={t.key} to={topicPath(t.key)} className="np-topic">{t.label}{t.discussions > 0 && <span className="num">{t.discussions}</span>}</Link>
          ))}
        </div>
      </section>
      <section className="np-howto">
        <SectionHead title="How Pulse works" />
        <p><b>Community</b> posts come from real Nexis members and are always shown as Anonymous. Nexis never posts as a member.</p>
        <p><b>Nexis editorial</b> discussions give context — what happened, the bull and bear cases, open questions — with sources and a dated history.</p>
        {s && <p className="np-honest">This week: {s.comments_7d.toLocaleString()} {s.comments_7d === 1 ? "reply" : "replies"} from {s.participants_7d.toLocaleString()} {s.participants_7d === 1 ? "member" : "members"}. Counts are real activity only.</p>}
        <p className="xs"><Link to="/community-guidelines">Community Guidelines</Link> · <Link to="/ai-disclosure">AI disclosure</Link> · <Link to="/disclaimer">Not investment advice</Link></p>
      </section>
    </aside>
  );
}

function Debates({ items }: { items: AnyObj[] }) {
  const { lang } = useT();
  if (!items.length) return null;
  return (
    <section className="np-debates" aria-label="Debates Nexis is tracking">
      <SectionHead title="What the market is debating"><span className="xs muted">Nexis editorial · updated as news arrives</span></SectionHead>
      <div className="np-debate-grid">
        {items.slice(0, 4).map((d) => (
          <Link key={d.id} to={d.url} className="np-debate">
            <span className="np-debate-meta">{d.symbol ? <span className="mono">{d.symbol}</span> : <span>{d.topics?.[0]?.label ?? "Markets"}</span>}<span>Updated {ago(d.updated_at ?? d.created_at, lang)}</span></span>
            <b dir="auto">{d.title}</b>
            <span className="np-split"><span className="pos">{d.debate?.bull ?? 0} bull</span><span className="neg">{d.debate?.bear ?? 0} bear</span><span>{d.counts.comments ? `${d.counts.comments} replies` : "No replies yet"}</span></span>
          </Link>
        ))}
      </div>
    </section>
  );
}

export default function PulseHome({ view = "feed" }: { view?: "feed" | "topic" | "search" }) {
  const { topic } = useParams();
  const [sp, setSp] = useSearchParams();
  const sort = sp.get("sort") ?? (view === "feed" ? "trending" : "activity");
  const kind = sp.get("kind") ?? "";
  const q = sp.get("q") ?? "";
  const [composing, setComposing] = useState(sp.get("compose") === "1");
  useEngineNudge();
  const overview = useQuery({ queryKey: ["pulse-overview"], queryFn: () => api.get<AnyObj>("/pulse/overview"), staleTime: 60_000 });
  const meta = useQuery({ queryKey: ["pulse-meta"], queryFn: () => api.get<AnyObj>("/pulse/meta"), staleTime: 3600_000 });
  const feed = useInfiniteQuery({
    queryKey: ["pulse-feed", sort, kind, topic ?? "", q],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/pulse/feed", { sort, kind: kind || undefined, topic, q: q || undefined, cursor: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? null,
    enabled: view !== "search" || q.length >= 2,
  });
  const first = feed.data?.pages[0];
  const items: AnyObj[] = (feed.data?.pages ?? []).flatMap((p) => p.items);
  const topicLabel = (meta.data?.topics ?? []).find((t: AnyObj) => t.key === topic)?.label ?? topic;
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v) n.set(k, v); else n.delete(k); setSp(n, { replace: true }); };
  const search = useQuery({ queryKey: ["pulse-search", q], queryFn: () => api.get<AnyObj>("/pulse/search", { q }), enabled: view === "search" && q.length >= 2 });

  let empty: React.ReactNode = null;
  if (!feed.isLoading && !items.length) {
    if (first?.needs_sign_in) empty = <p className="np-empty-line"><Link to={`/login?next=${encodeURIComponent(`/pulse?sort=${sort}`)}`}>Sign in</Link> to see {sort === "saved" ? "what you saved" : sort === "following" ? "discussions you follow" : "discussions about what you track"}.</p>;
    else if (first?.needs_tracking) empty = <p className="np-empty-line">For you is built from what you own and watch. <Link to="/my-nexis/watchlist">Add assets in My Nexis</Link> and their discussions appear here.</p>;
    else if (sort === "following") empty = <p className="np-empty-line">You're not following any discussions yet. Follow one to hear when it changes.</p>;
    else if (sort === "saved") empty = <p className="np-empty-line">Nothing saved yet.</p>;
    else if (view === "search") empty = <p className="np-empty-line">{q.length < 2 ? "Type at least two characters." : `No discussions mention “${q}” yet.`}</p>;
    else empty = <p className="np-empty-line">Pulse is tracking what the market is debating{topic ? ` about ${topicLabel}` : ""}. No discussions here yet — join in when you have something to say.</p>;
  }

  return (
    <div className="np">
      <header className="np-head">
        <div className="np-eyebrow"><Link to="/pulse">Nexis Pulse</Link>{view === "topic" && <span>Topic</span>}{view === "search" && <span>Search</span>}</div>
        <h1>{view === "topic" ? topicLabel : view === "search" ? (q ? `“${q}”` : "Search Pulse") : "What the market is debating"}</h1>
        {view === "feed" && <p className="np-lede">Anonymous discussion between real investors, next to Nexis editorial context on the developments behind each debate.</p>}
        <div className="np-head-actions">
          <SearchBox initial={q} />
          <button type="button" className="btn primary" onClick={() => setComposing(true)}>Start a discussion</button>
        </div>
      </header>

      {composing && <Composer topic={view === "topic" ? topic : undefined} onClose={() => { setComposing(false); set("compose", ""); }} />}

      {view === "feed" && !kind && sort === "trending" && <Debates items={overview.data?.debates ?? []} />}

      {view === "search" && search.data && (search.data.assets.length > 0 || search.data.topics.length > 0) && (
        <section className="np-search-hits">
          {search.data.topics.map((t: AnyObj) => <Link key={t.key} to={topicPath(t.key)} className="np-topic">{t.label}</Link>)}
          {search.data.assets.map((a: AnyObj) => <Link key={a.symbol} to={assetPath(a.symbol)} className="np-asset-hit"><span className="mono">{a.symbol}</span> <span className="muted">{a.name}</span></Link>)}
        </section>
      )}

      <div className="np-grid">
        <main>
          <div className="np-controls">
            <nav className="np-tabs" aria-label="Sort discussions">
              {SORTS.filter((s) => view === "feed" || !["following", "for_you", "saved"].includes(s.key)).map((s) => (
                <button key={s.key} type="button" className={sort === s.key ? "on" : ""} aria-current={sort === s.key ? "page" : undefined}
                  onClick={() => set("sort", s.key === (view === "feed" ? "trending" : "activity") ? "" : s.key)}>{s.label}</button>
              ))}
            </nav>
            <div className="np-seg small" role="radiogroup" aria-label="Kind">
              {KINDS.map((k) => <button key={k.key} type="button" role="radio" aria-checked={kind === k.key} className={kind === k.key ? "on" : ""} onClick={() => set("kind", k.key)}>{k.label}</button>)}
            </div>
          </div>
          {first?.fallback === "latest" && <p className="np-note">Nothing has enough recent replies, reactions or follows to trend yet, so these are the newest discussions. Trending is based on real activity only.</p>}
          {sort === "for_you" && first?.tracked?.length > 0 && <p className="np-note">Based on what you track: {first!.tracked.slice(0, 8).join(", ")}{first!.tracked.length > 8 ? "…" : ""}. Only you can see this.</p>}
          {feed.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /><span className="skel w60" /></div>}
          {feed.isError && <ErrorState error={feed.error} onRetry={() => feed.refetch()} />}
          {empty}
          <ol className="np-list">{items.map((d) => <DiscussionRow key={d.id} d={d} />)}</ol>
          {feed.hasNextPage && <button type="button" className="btn sm np-more" disabled={feed.isFetchingNextPage} onClick={() => feed.fetchNextPage()}>{feed.isFetchingNextPage ? "Loading…" : "More discussions"}</button>}
          <p className="np-disclaimer">{overview.data?.disclaimer}</p>
        </main>
        <Rail overview={overview.data} />
      </div>
    </div>
  );
}
