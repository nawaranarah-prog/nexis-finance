import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, fmtPrice, useMe } from "../components/market";
import { toast } from "../components/toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { ago, AuthorLink, discussionUrl, DiscussionRow, Disclosure, GeneratedMark, ScoreBlock, tickerUrl } from "../components/pulseParts";
import { ComposeToggle } from "./Pulse";

type Mode = "trending" | "latest" | "following" | "saved";
const MODES: { mode: Mode; label: string; to: string }[] = [
  { mode: "trending", label: "Trending", to: "/pulse" },
  { mode: "latest", label: "Latest", to: "/pulse/latest" },
  { mode: "following", label: "Following", to: "/pulse/following" },
  { mode: "saved", label: "Saved", to: "/pulse/saved" },
];
const SOURCES = [
  { key: null, label: "Everything" },
  { key: "community", label: "Members" },
  { key: "research", label: "Nexis Research" },
  { key: "generated", label: "AI perspectives" },
] as const;

const useDiscover = () => useQuery({ queryKey: ["pulse-discover"], queryFn: () => api.get<AnyObj>("/pulse/discover"), staleTime: 60_000 });
const useMeta = () => useQuery({ queryKey: ["pulse-meta"], queryFn: () => api.get<AnyObj>("/pulse/meta"), staleTime: 3600_000 });

/** Nudge the discussion engine (the server throttles it and enforces the daily limit). */
function useEngineNudge() {
  const qc = useQueryClient();
  useEffect(() => {
    let done = false;
    const id = window.setTimeout(() => {
      void api.post<AnyObj>("/pulse/engine/tick").then((r) => {
        if (!done && (r?.generated || r?.follow_up_replies)) qc.invalidateQueries({ queryKey: ["pulse-feed"] });
      }).catch(() => undefined);
    }, 1500);
    return () => { done = true; window.clearTimeout(id); };
  }, [qc]);
}

function PulseSearchBox({ initial = "" }: { initial?: string }) {
  const { t } = useT();
  const nav = useNavigate();
  const [q, setQ] = useState(initial);
  useEffect(() => setQ(initial), [initial]);
  return (
    <form className="pf-search" role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/pulse/search?q=${encodeURIComponent(q.trim())}`); }}>
      <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Search Pulse — NVDA, UAE banks, interest rates, a person…")} aria-label={t("Search Pulse")} />
    </form>
  );
}

function LeftRail() {
  const { t } = useT();
  const meta = useMeta();
  const disc = useDiscover();
  const topics: AnyObj[] = disc.data?.topics?.length ? disc.data.topics : (meta.data?.topics ?? []).slice(0, 8);
  return (
    <nav className="pf-left" aria-label={t("Pulse")}>
      <div className="pf-left-group">
        {MODES.map((m) => (
          <NavLink key={m.mode} to={m.to} end className={({ isActive }) => `pf-nav ${isActive ? "on" : ""}`}>{t(m.label)}</NavLink>
        ))}
        <NavLink to="/pulse/topics" className={({ isActive }) => `pf-nav ${isActive ? "on" : ""}`}>{t("Topics")}</NavLink>
        <NavLink to="/pulse/search" className={({ isActive }) => `pf-nav ${isActive ? "on" : ""}`}>{t("Search")}</NavLink>
        <NavLink to="/pulse/overview" className={({ isActive }) => `pf-nav ${isActive ? "on" : ""}`}>{t("Overview")}</NavLink>
        <Link to="/markets" className="pf-nav">{t("Markets")} ↗</Link>
      </div>
      <div className="pf-left-group hide-mobile">
        <div className="pf-left-h">{t("Topics")}</div>
        {topics.map((tp) => <NavLink key={tp.key} to={`/pulse/topics/${tp.key}`} className={({ isActive }) => `pf-nav sub ${isActive ? "on" : ""}`}>{t(tp.label)}</NavLink>)}
      </div>
    </nav>
  );
}

function RightRail() {
  const { t, lang } = useT();
  const me = useMe().data?.user;
  const disc = useDiscover();
  const ev = useQuery({ queryKey: ["pulse-events"], queryFn: () => api.get<AnyObj>("/pulse/events"), staleTime: 300_000 });
  const watch = useQuery({ queryKey: ["watchlist"], queryFn: () => api.get<AnyObj>("/me/watchlist"), enabled: !!me, staleTime: 60_000 });
  const d = disc.data;
  const assets: AnyObj[] = (d?.trending_assets?.length ? d.trending_assets : d?.most_discussed ?? []).slice(0, 6);
  return (
    <aside className="pf-right">
      <section className="pf-card">
        <div className="pf-card-h">{t("Trending assets")}</div>
        {!assets.length && <p className="pl-empty-line">{t("Nothing yet.")}</p>}
        {assets.map((a) => (
          <Link key={a.symbol} to={tickerUrl(a.symbol)} className="pf-asset">
            <span className="mono">{a.symbol}</span><span className="pf-asset-name">{a.name}</span>
            <span className="num pf-asset-n">{a.discussions}</span>
          </Link>
        ))}
      </section>
      <section className="pf-card">
        <div className="pf-card-h">{t("Market events")}<span className="xs muted"> · {t("sourced")}</span></div>
        {(ev.data?.items ?? []).slice(0, 5).map((e: AnyObj) => (
          <div key={e.id} className="pf-event">
            {e.url && /^https?:/.test(e.url) ? <a href={e.url} target="_blank" rel="noreferrer noopener" dir="auto">{e.title}</a> : <span dir="auto">{e.title}</span>}
            <span className="pf-event-meta">{e.publisher || e.provider_label} · {ago(e.published_at, lang)}{e.assets?.length ? ` · ${e.assets.slice(0, 2).join(", ")}` : ""}</span>
          </div>
        ))}
        {ev.data && !ev.data.items.length && <p className="pl-empty-line">{t("No major events in the last few days.")}</p>}
      </section>
      {(d?.trending_discussions ?? []).length > 0 && (
        <section className="pf-card">
          <div className="pf-card-h">{t("Trending discussions")}</div>
          {(d?.trending_discussions ?? []).slice(0, 4).map((x: AnyObj) => (
            <Link key={x.id} to={discussionUrl(x.id)} className="pf-mini" dir="auto">{x.title}<span className="pf-event-meta">{x.asset} · {x.comment_count} {t("replies")}</span></Link>
          ))}
        </section>
      )}
      {me && (
        <section className="pf-card">
          <div className="pf-card-h">{t("Your tracked assets")}</div>
          {(watch.data?.items ?? []).length === 0 && <p className="pl-empty-line">{t("Track an asset from its Pulse page to follow it here.")}</p>}
          {(watch.data?.items ?? []).slice(0, 6).map((w: AnyObj) => (
            <Link key={w.symbol} to={tickerUrl(w.symbol)} className="pf-asset">
              <span className="mono">{w.symbol}</span>
              <span className="pf-asset-name num">{w.price != null ? fmtPrice(w.price) : t("no quote")}</span>
              {w.change_pct != null && <Change pct={w.change_pct} />}
            </Link>
          ))}
          <Link to="/portfolio?tab=watchlist" className="sec-link">{t("Watchlist")} <span aria-hidden>→</span></Link>
        </section>
      )}
      <Disclosure compact />
    </aside>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  useEngineNudge();
  return (
    <div className="pf">
      <LeftRail />
      <main className="pf-main">{children}</main>
      <RightRail />
    </div>
  );
}

function Feed({ mode, topic }: { mode: Mode; topic?: string }) {
  const { t } = useT();
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useMe().data?.user;
  const [source, setSource] = useState<string | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["pulse-feed", mode, topic ?? null, source],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/pulse/feed", { mode, ...(topic ? { topic } : {}), ...(source ? { source } : {}), ...(pageParam ? { cursor: pageParam } : {}), limit: 15 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? null,
  });
  const items: AnyObj[] = (q.data?.pages ?? []).flatMap((p) => p.items);
  const needsSignIn = q.data?.pages?.[0]?.needs_sign_in;
  return (
    <>
      <ComposeToggle onPublished={(x) => { qc.invalidateQueries({ queryKey: ["pulse-feed"] }); toast("success", t("Discussion published")); nav(discussionUrl(x.id)); }} />
      <div className="pf-filters" role="group" aria-label={t("Who wrote it")}>
        {SOURCES.map((s) => (
          <button key={s.label} type="button" aria-pressed={source === s.key} className={source === s.key ? "on" : ""} onClick={() => setSource(s.key)}>{t(s.label)}</button>
        ))}
      </div>
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /><span className="skel w70" /><span className="skel w80" /></div>}
      {q.isError && <div className="hm-error">{errorMessage(q.error)} <button className="link-btn" onClick={() => q.refetch()}>{t("Retry")}</button></div>}
      {needsSignIn && (
        <div className="pl-compose-gate"><span>{mode === "saved" ? t("Sign in to see the discussions you saved.") : t("Sign in to see discussions about the assets you track and people you follow.")}</span>
          <Link className="btn primary sm" to={`/login?next=${encodeURIComponent(window.location.pathname)}`}>{t("Sign in")}</Link></div>
      )}
      <ol className="pl-list pf-list">{items.map((d) => <DiscussionRow key={d.id} d={d} showAsset preview />)}</ol>
      {!q.isLoading && !needsSignIn && !items.length && (
        <p className="pl-empty-line pf-empty">
          {mode === "following" ? (me ? t("Nothing here yet. Track assets or follow people and their discussions will appear here.") : "")
            : mode === "saved" ? t("You haven't saved any discussions yet.") : t("No discussions match.")}
        </p>
      )}
      {q.hasNextPage && <button type="button" className="btn sm pl-more" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "…" : t("Load more")}</button>}
    </>
  );
}

export function PulseHome({ mode }: { mode: Mode }) {
  const { t } = useT();
  const title = { trending: "What investors are discussing", latest: "Latest discussions", following: "Following", saved: "Saved" }[mode];
  return (
    <Shell>
      <header className="pf-head">
        <div className="hm-eyebrow"><span>{t("Nexis Pulse")}</span></div>
        <h1>{t(title)}</h1>
        <PulseSearchBox />
      </header>
      <Feed mode={mode} />
    </Shell>
  );
}

export function PulseTopic() {
  const { t } = useT();
  const { topic } = useParams();
  const meta = useMeta();
  const disc = useDiscover();
  if (!topic) {
    const counts = Object.fromEntries((disc.data?.topics ?? []).map((x: AnyObj) => [x.key, x.count]));
    return (
      <Shell>
        <header className="pf-head"><div className="hm-eyebrow"><Link to="/pulse">{t("Nexis Pulse")}</Link></div><h1>{t("Topics")}</h1></header>
        <div className="pf-topics">
          {(meta.data?.topics ?? []).map((tp: AnyObj) => (
            <Link key={tp.key} to={`/pulse/topics/${tp.key}`} className="pf-topic-card">
              <b>{t(tp.label)}</b><span className="xs muted">{counts[tp.key] ? `${counts[tp.key]} ${t("this week")}` : t("Browse")}</span>
            </Link>
          ))}
        </div>
      </Shell>
    );
  }
  const label = (meta.data?.topics ?? []).find((x: AnyObj) => x.key === topic)?.label ?? topic;
  return (
    <Shell>
      <header className="pf-head"><div className="hm-eyebrow"><Link to="/pulse">{t("Nexis Pulse")}</Link><Link to="/pulse/topics">{t("Topics")}</Link></div><h1>{t(label)}</h1></header>
      <Feed mode="latest" topic={topic} />
    </Shell>
  );
}

export function PulseSearch() {
  const { t } = useT();
  const [sp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const res = useQuery({ queryKey: ["pulse-search", q], queryFn: () => api.get<AnyObj>("/pulse/search", { q }), enabled: q.trim().length >= 2, staleTime: 30_000 });
  const r = res.data;
  const nothing = r && !r.assets.length && !r.discussed_assets.length && !r.discussions.length && !r.topics.length && !r.people.length;
  return (
    <Shell>
      <header className="pf-head"><div className="hm-eyebrow"><Link to="/pulse">{t("Nexis Pulse")}</Link></div><h1>{t("Search")}</h1><PulseSearchBox initial={q} /></header>
      {q.trim().length < 2 && <p className="pl-empty-line">{t("Search for a ticker, company, topic, person or any keyword.")}</p>}
      {res.isLoading && q.trim().length >= 2 && <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /></div>}
      {nothing && <p className="pl-empty-line">{t("No results for")} “{q}”.</p>}
      {r && (
        <>
          {(r.discussed_assets.length > 0 || r.assets.length > 0) && (
            <section className="pl-section">
              <div className="sec-head"><h2>{t("Assets")}</h2></div>
              <div className="pf-chips">
                {r.discussed_assets.map((a: AnyObj) => <Link key={`d-${a.symbol}`} to={tickerUrl(a.symbol)} className="pf-chip"><span className="mono">{a.symbol}</span> {a.name} <span className="num muted">{a.discussions}</span></Link>)}
                {r.assets.filter((a: AnyObj) => !r.discussed_assets.some((x: AnyObj) => x.symbol === a.symbol)).map((a: AnyObj) => (
                  <Link key={a.symbol} to={tickerUrl(a.symbol)} className="pf-chip"><span className="mono">{a.symbol}</span> {a.name}</Link>
                ))}
              </div>
            </section>
          )}
          {r.topics.length > 0 && (
            <section className="pl-section"><div className="sec-head"><h2>{t("Topics")}</h2></div>
              <div className="pf-chips">{r.topics.map((x: AnyObj) => <Link key={x.key} to={`/pulse/topics/${x.key}`} className="pf-chip">{t(x.label)}</Link>)}</div></section>
          )}
          {r.discussions.length > 0 && (
            <section className="pl-section"><div className="sec-head"><h2>{t("Discussions")}</h2></div>
              <ol className="pl-list">{r.discussions.map((d: AnyObj) => <DiscussionRow key={d.id} d={d} showAsset />)}</ol></section>
          )}
          {r.people.length > 0 && (
            <section className="pl-section"><div className="sec-head"><h2>{t("People")}</h2></div>
              <ul className="pl-voices">{r.people.map((u: AnyObj) => <li key={u.username}><AuthorLink author={u} /><span className="xs muted">{u.bio}</span></li>)}</ul></section>
          )}
        </>
      )}
    </Shell>
  );
}

export function PersonaPage() {
  const { t, lang } = useT();
  const { username } = useParams();
  const q = useQuery({ queryKey: ["persona", username], queryFn: () => api.get<AnyObj>(`/pulse/personas/${username}`) });
  const p = q.data;
  return (
    <Shell>
      {q.isError && <div className="hm-error">{errorMessage(q.error)}</div>}
      {!p && !q.isError && <div className="wire-skel"><span className="skel w60" /><span className="skel w80" /></div>}
      {p && (
        <>
          <header className="pf-head pf-persona">
            <div className="hm-eyebrow"><Link to="/pulse">{t("Nexis Pulse")}</Link><span>{t("Nexis-generated persona")}</span></div>
            <h1>{p.user.display_name} <span className="pl-tk">@{p.user.username}</span> <GeneratedMark /></h1>
            <p className="hm-lede">{p.user.bio}</p>
            <p className="pl-disclosure compact"><b>{t("Not a real person.")}</b> {t("This is a fictional investor created by Nexis. Its posts and replies are written by AI from its profile, reacting to real, sourced events. Its views are interpretations, not facts or advice.")}</p>
            {p.profile && (
              <dl className="pf-profile">
                <dt>{t("Style")}</dt><dd>{p.profile.archetype}</dd>
                <dt>{t("Experience")}</dt><dd>{p.profile.experience}</dd>
                <dt>{t("Risk")}</dt><dd>{p.profile.risk_tolerance}</dd>
                <dt>{t("Focus")}</dt><dd>{[...(p.profile.sectors ?? []), ...(p.profile.markets ?? [])].join(", ")}</dd>
                <dt>{t("Lens")}</dt><dd>{p.profile.framework}</dd>
              </dl>
            )}
          </header>
          <section className="pl-section">
            <div className="sec-head"><h2>{t("Discussions started")}</h2></div>
            {p.discussions.length ? <ol className="pl-list">{p.discussions.map((d: AnyObj) => <DiscussionRow key={d.id} d={d} showAsset />)}</ol> : <p className="pl-empty-line">{t("None yet.")}</p>}
          </section>
          <section className="pl-section">
            <div className="sec-head"><h2>{t("Recent replies")}</h2></div>
            {p.recent_comments.length ? (
              <ul className="pf-replies">
                {p.recent_comments.map((c: AnyObj) => (
                  <li key={c.id}><Link to={`${discussionUrl(c.discussion.id)}#c${c.id}`} dir="auto">{c.body}</Link>
                    <span className="pf-event-meta"><span className="mono">{c.discussion.asset}</span> · {c.discussion.title} · {ago(c.created_at, lang)}</span></li>
                ))}
              </ul>
            ) : <p className="pl-empty-line">{t("None yet.")}</p>}
          </section>
        </>
      )}
    </Shell>
  );
}

/** The engine status, for the overview page. */
export function EngineNote() {
  const { t } = useT();
  const q = useQuery({ queryKey: ["pulse-engine"], queryFn: () => api.get<AnyObj>("/pulse/engine"), staleTime: 300_000 });
  const s = q.data;
  if (!s) return null;
  return (
    <p className="hm-fine">
      {s.model_configured ? `${t("AI perspectives")}: ${s.generated_last_24h} ${t("in the last 24 hours")} (${t("limit")} ${s.daily_limit}) · ${s.personas} ${t("personas")}` : t("AI perspectives are paused: no AI model is configured.")}
      {" · "}{t("Sources")}: {s.providers.filter((p: AnyObj) => p.connected).map((p: AnyObj) => p.label).join(", ")}. {t("Not connected")}: {s.providers.filter((p: AnyObj) => !p.connected).map((p: AnyObj) => p.label).join(", ")}.
    </p>
  );
}

export { ScoreBlock };
