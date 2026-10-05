import { askConfirm } from "../../components/dialog";
import { Fragment, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, fmtPrice } from "../../components/market";
import {
  ago, assetPath, Byline, DiscussionRow, KindLabel, Reactions, ReportButton, SectionHead, Stance, STANCE_GROUPS, stamp, Topics, useParticipate,
} from "../../components/pulse";
import { toast } from "../../components/toast";
import { ErrorState } from "../../components/ui";
import { useT } from "../../i18n";
import { api, ApiError, errorMessage } from "../../services/api";
import type { AnyObj } from "../../types/api";

const SITE = "https://nexis-finance-five.vercel.app";
const UPDATE_KIND: Record<string, string> = {
  opened: "Opened", development: "New development", what_changed: "What changed", new_bull: "New bullish argument",
  new_bear: "New bearish argument", open_question: "Open question", correction: "Correction",
};

function Paras({ text }: { text?: string | null }) {
  if (!text) return null;
  return <>{text.split(/\n{2,}/).map((p, i) => <p key={i} dir="auto">{p}</p>)}</>;
}

function Cites({ ids, order }: { ids: number[]; order: Map<number, number> }) {
  if (!ids?.length) return null;
  return <span className="np-cites">{ids.filter((i) => order.has(i)).map((i) => <a key={i} href={`#source-${i}`} className="np-cite" aria-label={`Source ${order.get(i)}`}>{order.get(i)}</a>)}</span>;
}

function Points({ items, order, empty }: { items: AnyObj[]; order: Map<number, number>; empty: string }) {
  if (!items?.length) return <p className="np-empty-line">{empty}</p>;
  return <ul className="np-points">{items.map((p, i) => <li key={i} dir="auto">{p.point} <Cites ids={p.source_ids} order={order} /></li>)}</ul>;
}

/** Search engines and link previews: the server renders these for crawlers too; this keeps the tab in sync. */
function useHead(d: AnyObj | undefined) {
  useEffect(() => {
    if (!d) return;
    const desc = String((d.kind === "editorial" ? d.editorial?.what_happened || d.body : d.body) || d.title).replace(/\s+/g, " ").slice(0, 158);
    document.title = `${d.title}${d.symbol ? ` (${d.symbol})` : ""} · Nexis Pulse`;
    const set = (sel: string, attr: string, val: string) => document.querySelector(sel)?.setAttribute(attr, val);
    set('meta[name="description"]', "content", desc);
    set('link[rel="canonical"]', "href", `${SITE}${d.url}`);
    set('meta[property="og:title"]', "content", document.title);
    set('meta[property="og:description"]', "content", desc);
    set('meta[property="og:url"]', "content", `${SITE}${d.url}`);
    const ld = {
      "@context": "https://schema.org", "@type": "DiscussionForumPosting", headline: d.title, url: `${SITE}${d.url}`, text: desc,
      datePublished: d.created_at, dateModified: d.updated_at ?? d.last_activity_at,
      author: d.kind === "editorial" ? { "@type": "Organization", name: "Nexis" } : { "@type": "Person", name: "Anonymous" },
      interactionStatistic: { "@type": "InteractionCounter", interactionType: "https://schema.org/CommentAction", userInteractionCount: d.counts.comments },
    };
    let el = document.getElementById("ld-discussion");
    if (!el) { el = document.createElement("script"); el.id = "ld-discussion"; el.setAttribute("type", "application/ld+json"); document.head.appendChild(el); }
    el.textContent = JSON.stringify(ld);
    return () => { document.getElementById("ld-discussion")?.remove(); };
  }, [d]);
}

// ------------------------------------------------------------------ comments

function ReplyBox({ pid, parent, onDone, autoFocus, placeholder }: { pid: string; parent?: number; onDone: () => void; autoFocus?: boolean; placeholder?: string }) {
  const participate = useParticipate();
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const c = await participate(() => api.post<AnyObj>(`/pulse/discussions/${pid}/comments`, { body, parent_id: parent ?? null }));
      if (c) {
        setBody("");
        if (c.status === "pending_review") toast("info", "Waiting for review", "Your reply matched a safety check, so a moderator will look at it first.");
        onDone();
      }
    } catch (x) { toast("error", "Couldn't post your reply", errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <form className={`np-reply ${parent ? "inline" : ""}`} onSubmit={send}>
      <textarea className="input" rows={parent ? 2 : 3} maxLength={4000} value={body} autoFocus={autoFocus} onChange={(e) => setBody(e.target.value)}
        placeholder={placeholder ?? "Add to the discussion. Challenge the reasoning, not the person."} aria-label="Reply" />
      <div className="np-reply-foot">
        <span className="xs muted">Replying as Anonymous</span>
        <span className="grow" />
        {parent && <button type="button" className="btn sm ghost" onClick={onDone}>Cancel</button>}
        <button className="btn primary sm" disabled={busy || body.trim().length < 2}>{busy ? "…" : "Reply"}</button>
      </div>
    </form>
  );
}

function Thread({ d }: { d: AnyObj }) {
  const { lang } = useT();
  const qc = useQueryClient();
  const [sort, setSort] = useState<"old" | "new" | "top">("old");
  const [replying, setReplying] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["pulse-comments", d.id, sort], queryFn: () => api.get<AnyObj>(`/pulse/discussions/${d.id}/comments`, { sort }) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["pulse-comments", d.id] }); qc.invalidateQueries({ queryKey: ["pulse-discussion", d.id] }); };
  const remove = async (id: number) => {
    if (!(await askConfirm({ title: "Delete your comment?", body: "The text is erased for good.", confirm: "Delete", danger: true }))) return;
    try { await api.del(`/pulse/comments/${id}`); refresh(); } catch (x) { toast("error", "Couldn't delete", errorMessage(x)); }
  };
  const items: AnyObj[] = q.data?.items ?? [];
  return (
    <section className="np-thread" aria-labelledby="community-h" id="community">
      <SectionHead title={d.kind === "public" ? "Discuss it on Nexis" : "Community discussion"}>
        <span className="xs muted">{d.counts.comments ? `${d.counts.comments} ${d.counts.comments === 1 ? "reply" : "replies"} from ${d.counts.participants} ${d.counts.participants === 1 ? "person" : "people"}` : "Anonymous replies from members"}</span>
        <span className="grow" />
        {items.length > 1 && (
          <div className="np-seg small" role="radiogroup" aria-label="Order">
            {(["old", "new", "top"] as const).map((s) => <button key={s} type="button" role="radio" aria-checked={sort === s} className={sort === s ? "on" : ""} onClick={() => setSort(s)}>{s === "old" ? "Oldest" : s === "new" ? "Newest" : "Top"}</button>)}
          </div>
        )}
      </SectionHead>
      {d.locked ? <p className="np-note">This discussion is locked. Existing replies stay visible.</p> : <ReplyBox pid={d.id} onDone={refresh} />}
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /></div>}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && !items.length && <p className="np-empty-line">No community responses yet.</p>}
      <ol className="np-comments">
        {items.map((c) => (
          <li key={c.id} id={`c${c.id}`} className={`np-comment ${c.status !== "visible" ? "gone" : ""}`} style={{ ["--depth" as string]: Math.min(c.depth, 4) }}>
            {c.status === "deleted" || c.status === "removed" ? (
              <p className="np-gone">{c.status === "deleted" ? "Deleted by the author." : "Removed by a moderator."}</p>
            ) : (
              <>
                <div className="np-comment-meta">
                  <Byline author={c.author} />
                  <Stance s={c.stance} />
                  <time dateTime={c.created_at} title={stamp(c.created_at, lang)}>{ago(c.created_at, lang)}</time>
                  {c.status === "pending_review" && <span className="np-pending">Only you can see this until it's reviewed</span>}
                </div>
                <div className="np-comment-body"><Paras text={c.body} /></div>
                <div className="np-comment-actions">
                  <Reactions type="comment" id={c.id} counts={c.counts} mine={c.viewer.reactions} own={c.viewer.is_mine} />
                  {!d.locked && c.status === "visible" && <button type="button" className="link-btn xs" onClick={() => setReplying(replying === c.id ? null : c.id)}>Reply</button>}
                  {c.viewer.is_mine ? <button type="button" className="link-btn xs neg" onClick={() => remove(c.id)}>Delete</button> : <ReportButton type="comment" id={c.id} compact />}
                </div>
                {replying === c.id && <ReplyBox pid={d.id} parent={c.id} autoFocus placeholder="Reply to this point…" onDone={() => { setReplying(null); refresh(); }} />}
              </>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

// ------------------------------------------------------------------ page

function Actions({ d }: { d: AnyObj }) {
  const participate = useParticipate();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toggle = async (what: "follow" | "save") => {
    try {
      const r = await participate(() => api.post<AnyObj>(`/pulse/discussions/${d.id}/${what}`, {}));
      if (r) {
        qc.invalidateQueries({ queryKey: ["pulse-discussion", d.id] });
        if (what === "follow") toast("success", r.following ? "Following" : "Unfollowed", r.following ? "You'll be notified about updates and new replies." : undefined);
        else toast("success", r.saved ? "Saved" : "Removed from saved");
      }
    } catch (x) { toast("error", "Couldn't update", errorMessage(x)); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(`${SITE}${d.url}`); toast("success", "Link copied"); } catch { toast("info", `${SITE}${d.url}`); }
  };
  const remove = async () => {
    if (!(await askConfirm({ title: "Delete your discussion?", body: "Its text is erased for good and replies stop being shown.", confirm: "Delete", danger: true }))) return;
    try { await api.del(`/pulse/discussions/${d.id}`); toast("success", "Discussion deleted"); nav("/pulse"); } catch (x) { toast("error", "Couldn't delete", errorMessage(x)); }
  };
  return (
    <div className="np-actions">
      <button type="button" className={`btn sm ${d.viewer.following ? "on" : ""}`} aria-pressed={d.viewer.following} onClick={() => toggle("follow")}>
        {d.viewer.following ? "Following" : "Follow"}{d.counts.followers > 0 && <span className="num"> · {d.counts.followers}</span>}
      </button>
      <button type="button" className={`btn sm ghost ${d.viewer.saved ? "on" : ""}`} aria-pressed={d.viewer.saved} onClick={() => toggle("save")}>{d.viewer.saved ? "Saved" : "Save"}</button>
      <button type="button" className="btn sm ghost" onClick={copy}>Copy link</button>
      <span className="grow" />
      {d.viewer.is_mine ? <button type="button" className="link-btn xs neg" onClick={remove}>Delete</button> : <ReportButton type="discussion" id={d.id} />}
    </div>
  );
}

function Editorial({ d }: { d: AnyObj }) {
  const { lang } = useT();
  const e = d.editorial;
  const order = new Map<number, number>((d.sources ?? []).map((s: AnyObj, i: number) => [s.id, i + 1]));
  const structured = !!(e.what_happened || e.bull_case.length || e.bear_case.length);
  if (!structured) {
    return <section className="np-sec"><h2 className="np-h">Nexis analysis</h2><div className="np-prose"><Paras text={d.body} /></div><p className="np-disclosure">{e.disclosure}</p></section>;
  }
  return (
    <>
      <section className="np-sec"><h2 className="np-h">What happened?</h2><div className="np-prose"><Paras text={e.what_happened} /></div></section>
      {e.debate && <section className="np-sec"><h2 className="np-h">What is being debated?</h2><div className="np-prose"><Paras text={e.debate} /></div></section>}
      <section className="np-sec np-cases">
        <div className="np-case bull"><h2 className="np-h">Bull case</h2><Points items={e.bull_case} order={order} empty="The sources don't currently support a clear bullish argument." /></div>
        <div className="np-case bear"><h2 className="np-h">Bear case</h2><Points items={e.bear_case} order={order} empty="The sources don't currently support a clear bearish argument." /></div>
      </section>
      {e.neutral_case.length > 0 && <section className="np-sec"><h2 className="np-h">Cuts both ways</h2><Points items={e.neutral_case} order={order} empty="" /></section>}
      <section className="np-sec"><h2 className="np-h">What is still unknown?</h2>
        {e.open_questions.length ? <ul className="np-questions">{e.open_questions.map((q: string, i: number) => <li key={i} dir="auto">{q}</li>)}</ul> : <p className="np-empty-line">No open questions recorded.</p>}
      </section>
      {d.sources.length > 0 && (
        <section className="np-sec" id="sources"><h2 className="np-h">Sources</h2>
          <ol className="np-sources">
            {d.sources.map((s: AnyObj) => (
              <li key={s.id} id={`source-${s.id}`}>
                <a href={s.url} target="_blank" rel="noreferrer noopener nofollow" dir="auto">{s.title}</a>
                <span className="np-src-meta">{s.publisher ?? new URL(s.url).hostname}{s.published_at ? ` · published ${stamp(s.published_at, lang)}` : ""} · retrieved {ago(s.retrieved_at, lang)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
      <p className="np-disclosure">{e.disclosure} <Link to="/ai-disclosure">How Nexis uses AI</Link></p>
    </>
  );
}

const GROUP_ORDER = ["support", "bullish", "pushback", "bearish", "question", "context"];

/** A public thread from another site: the post, then the replies grouped by how they argue. No accounts. */
function PublicThread({ d }: { d: AnyObj }) {
  const { lang } = useT();
  const p = d.public;
  const o = d.origin ?? {};
  const groups = GROUP_ORDER.map((k) => ({ k, items: (p.quotes as AnyObj[]).filter((q) => q.stance === k) })).filter((g) => g.items.length);
  const tone = (k: string) => (k === "support" || k === "bullish" ? "bull" : k === "pushback" || k === "bearish" ? "bear" : "");
  return (
    <>
      <section className="np-sec">
        {d.body && <div className="np-prose np-orig-post"><Paras text={d.body} /></div>}
        <p className="np-src-meta">Posted on {o.label}{o.posted_at ? ` · ${ago(o.posted_at, lang)}` : ""} · collected {ago(d.updated_at, lang)} · <a href={o.url} target="_blank" rel="noreferrer noopener nofollow">Read the full thread ↗</a></p>
      </section>
      {p.debate && <section className="np-sec"><h2 className="np-h">What they're arguing about</h2><div className="np-prose"><p dir="auto">{p.debate}</p></div></section>}
      <section className="np-sec">
        <h2 className="np-h">The arguments</h2>
        <div className="np-args">
          {groups.map((g) => (
            <div key={g.k} className={`np-arg-group ${tone(g.k)}`}>
              <h3>{STANCE_GROUPS[g.k] ?? g.k} <span className="num">{g.items.length}</span></h3>
              <ul>
                {g.items.map((q, i) => (
                  <li key={i}>
                    <p dir="auto">“{q.text}”</p>
                    <span className="np-src-meta">{q.url ? <a href={q.url} target="_blank" rel="noreferrer noopener nofollow">A reply on {o.label?.split(" · ")[0] ?? "the site"} ↗</a> : "A reply"}{q.at ? ` · ${ago(q.at, lang)}` : ""}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <p className="np-disclosure">{p.disclosure} <Link to="/ai-disclosure">How this works</Link></p>
      </section>
    </>
  );
}

function Updates({ d }: { d: AnyObj }) {
  const { lang } = useT();
  if (!d.updates?.length) return null;
  const order = new Map<number, number>((d.sources ?? []).map((s: AnyObj, i: number) => [s.id, i + 1]));
  return (
    <section className="np-sec" id="updates">
      <h2 className="np-h">How this discussion evolved</h2>
      <ol className="np-history">
        {d.updates.map((u: AnyObj) => (
          <li key={u.id}>
            <time dateTime={u.created_at}>{stamp(u.created_at, lang)}</time>
            <div><span className="np-tl-kind">{UPDATE_KIND[u.kind] ?? "Update"}</span><p dir="auto">{u.headline} <Cites ids={u.source_ids} order={order} /></p>{u.body && <p className="xs muted" dir="auto">{u.body}</p>}</div>
          </li>
        ))}
      </ol>
      <p className="xs muted">Editorial context changes as new information arrives. Community replies are never edited by Nexis.</p>
    </section>
  );
}

export default function PulseDiscussion() {
  const { id = "" } = useParams();
  const { lang } = useT();
  const nav = useNavigate();
  const loc = useLocation();
  const legacy = /^\d+$/.test(id);
  useEffect(() => {
    if (!legacy) return;
    api.get<AnyObj>(`/pulse/legacy/${id}`).then((r) => nav(r.url, { replace: true })).catch(() => nav("/pulse", { replace: true }));
  }, [legacy, id, nav]);
  const q = useQuery({ queryKey: ["pulse-discussion", id], queryFn: () => api.get<AnyObj>(`/pulse/discussions/${id}`), enabled: !legacy });
  const related = useQuery({ queryKey: ["pulse-related", id], queryFn: () => api.get<AnyObj[]>(`/pulse/discussions/${id}/related`), enabled: !legacy && !!q.data });
  const asset = useQuery({ queryKey: ["pulse-asset-quote", q.data?.symbol], queryFn: () => api.get<AnyObj>(`/pulse/assets/${encodeURIComponent(q.data!.symbol)}`), enabled: !!q.data?.symbol, staleTime: 120_000 });
  const d = q.data;
  useHead(d);
  useEffect(() => {
    if (d && loc.hash) window.setTimeout(() => document.getElementById(loc.hash.slice(1))?.scrollIntoView({ block: "start" }), 120);
  }, [d, loc.hash]);
  if (legacy || q.isLoading) return <div className="np"><div className="wire-skel"><span className="skel w60" /><span className="skel w80" /><span className="skel w70" /></div></div>;
  if (q.isError) {
    const missing = q.error instanceof ApiError && q.error.status === 404;
    return <div className="np">{missing ? <div className="np-missing"><h1>Discussion not found</h1><p>It may have been deleted by its author or removed by a moderator.</p><Link className="btn" to="/pulse">Back to Pulse</Link></div> : <ErrorState error={q.error} onRetry={() => q.refetch()} />}</div>;
  }
  if (!d) return null;
  const quote = asset.data?.quote;
  return (
    <div className="np np-detail">
      <nav className="np-eyebrow" aria-label="Breadcrumb"><Link to="/pulse">Nexis Pulse</Link>{d.symbol && <Link to={assetPath(d.symbol)} className="mono">{d.symbol}</Link>}</nav>
      <article>
        <header className="np-d-head">
          <div className="np-row-meta"><KindLabel kind={d.kind} origin={d.origin} /><Stance s={d.stance} /><Topics topics={d.topics} /></div>
          <h1 dir="auto">{d.title}</h1>
          <div className="np-d-sub">
            <Byline author={d.author} aiAssisted={d.ai_assisted} />
            {d.symbol && (
              <Link to={assetPath(d.symbol)} className="np-quote">
                <span className="mono">{d.symbol}</span>{d.asset_name && <span className="muted">{d.asset_name}</span>}
                {quote?.price != null && <><span className="num">{fmtPrice(quote.price)} {quote.currency}</span><Change pct={quote.change_pct} /></>}
              </Link>
            )}
            <time dateTime={d.updated_at ?? d.created_at} title={stamp(d.updated_at ?? d.created_at, lang)}>
              {d.kind === "editorial" ? `Updated ${ago(d.updated_at ?? d.created_at, lang)}` : d.kind === "public" ? `Collected ${ago(d.updated_at ?? d.created_at, lang)}` : `Posted ${ago(d.created_at, lang)}`}
            </time>
          </div>
          {d.viewer.pending_review && <p className="np-note">Only you can see this discussion while a moderator reviews it. It matched one of the automatic safety checks.</p>}
        </header>

        {d.kind === "editorial" ? <Editorial d={d} /> : d.kind === "public" ? <PublicThread d={d} /> : (
          <section className="np-sec">
            <div className="np-prose"><Paras text={d.body} /></div>
            <Reactions type="discussion" id={d.id} counts={d.counts} mine={d.viewer.reactions} own={d.viewer.is_mine} />
          </section>
        )}
        <Actions d={d} />
        <Updates d={d} />
        <Thread d={d} />
      </article>
      {(related.data ?? []).length > 0 && (
        <section className="np-sec np-related">
          <SectionHead title="Related discussions" />
          <ol className="np-list">{related.data!.map((r) => <Fragment key={r.id}><DiscussionRow d={r} /></Fragment>)}</ol>
        </section>
      )}
      <p className="np-disclaimer">Community posts are opinions from anonymous members. Nexis editorial content is context, not advice. Nothing here is a recommendation to buy or sell. <Link to="/disclaimer">Disclaimer</Link></p>
    </div>
  );
}
