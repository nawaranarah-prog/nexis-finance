import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, fmtPrice, SymbolSearch, useMe } from "../components/market";
import { Sparkline } from "../components/Sparkline";
import { toast } from "../components/toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

type Sentiment = "bullish" | "neutral" | "bearish";
const SENTIMENTS: Sentiment[] = ["bullish", "neutral", "bearish"];
const SENTIMENT_LABEL: Record<Sentiment, string> = { bullish: "Bullish", neutral: "Neutral", bearish: "Bearish" };

function ago(iso: string | null | undefined, lang: string): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
  if (s < 60) return rtf.format(0, "minute");
  if (s < 3600) return rtf.format(-Math.floor(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.floor(s / 3600), "hour");
  if (s < 86400 * 30) return rtf.format(-Math.floor(s / 86400), "day");
  return new Date(iso).toLocaleDateString(lang === "ar" ? "ar-AE" : "en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const useMeta = () => useQuery({ queryKey: ["pulse-meta"], queryFn: () => api.get<AnyObj>("/pulse/meta"), staleTime: 3600_000 });

// ------------------------------------------------------------------ small pieces

function SentimentTag({ value, ai }: { value: string | null | undefined; ai?: boolean }) {
  const { t } = useT();
  if (!value) return null;
  return (
    <span className={`pl-sent ${value} ${ai ? "ai" : ""}`} title={ai ? t("Detected by AI from the text — the author didn't choose one") : t("Chosen by the author")}>
      {ai && <span className="pl-sent-ai">AI</span>}{t(SENTIMENT_LABEL[value as Sentiment] ?? value)}
    </span>
  );
}

/** Marks content published by the official Nexis Research account. */
function Official() {
  const { t } = useT();
  return <span className="pl-official" title={t("Editorial analysis by the Nexis Research team — not a member opinion")}>{t("Official")}</span>;
}

function ScoreBlock({ score, compact }: { score: AnyObj | null | undefined; compact?: boolean }) {
  const { t } = useT();
  if (!score?.available) return <span className={`pl-score na ${compact ? "compact" : ""}`}>{compact ? "—" : t("Pulse unavailable")}</span>;
  const tone = score.value > 60 ? "pos" : score.value <= 40 ? "neg" : "mid";
  return (
    <span className={`pl-score ${tone} ${compact ? "compact" : ""}`}>
      <span className="pl-score-v num">{score.value}</span>{!compact && <span className="pl-score-of">/ 100</span>}
      {!compact && <span className="pl-score-l">{t(score.label)}</span>}
    </span>
  );
}

function LikeButton({ d }: { d: AnyObj }) {
  const { t } = useT();
  const me = useMe().data?.user;
  const nav = useNavigate();
  const [liked, setLiked] = useState<boolean>(d.liked_by_me);
  const [n, setN] = useState<number>(d.like_count);
  useEffect(() => { setLiked(d.liked_by_me); setN(d.like_count); }, [d.liked_by_me, d.like_count]);
  const toggle = async () => {
    if (!me) { nav(`/login?next=${encodeURIComponent(window.location.pathname)}`); return; }
    setLiked(!liked); setN(n + (liked ? -1 : 1));
    try { const r = await api.post<AnyObj>(`/social/posts/${d.id}/like`); setLiked(r.liked); setN(r.like_count); }
    catch (e) { setLiked(liked); setN(n); toast("error", t("Couldn't save your like"), errorMessage(e)); }
  };
  return (
    <button type="button" className={`pl-like ${liked ? "on" : ""}`} aria-pressed={liked} onClick={toggle} aria-label={liked ? t("Unlike") : t("Like")}>
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden><path d="M8 13.6 2.7 8.5A3.2 3.2 0 0 1 8 4.3a3.2 3.2 0 0 1 5.3 4.2Z" fill={liked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
      <span className="num">{n}</span>
    </button>
  );
}

/** One discussion in a list: sentiment, title, an excerpt and who said it. */
function DiscussionRow({ d, showAsset }: { d: AnyObj; showAsset?: boolean }) {
  const { t, lang } = useT();
  return (
    <li className={`pl-row ${d.source.editorial ? "editorial" : ""}`}>
      <div className="pl-row-main">
        <div className="pl-row-meta">
          {showAsset && <Link className="mono pl-asset" to={`/pulse/${encodeURIComponent(d.asset)}`}>{d.asset}</Link>}
          <SentimentTag value={d.sentiment} />
          {!d.sentiment && <SentimentTag value={d.ai_sentiment} ai />}
          {d.topics.map((tp: AnyObj) => <span key={tp.key} className="pl-topic">{t(tp.label)}</span>)}
        </div>
        <Link className="pl-row-title" to={`/pulse/d/${d.id}`} dir="auto">{d.title}</Link>
        <p className="pl-row-body" dir="auto">{d.body}</p>
        <div className="pl-row-foot">
          <Link to={`/finstagram/u/${d.author.username}`} className="pl-author">{d.author.display_name || d.author.username}</Link>
          {d.source.editorial && <Official />}
          <time dateTime={d.created_at}>{ago(d.created_at, lang)}</time>
          <span className="pl-src">{t(d.source.label)}</span>
          <span className="grow" />
          <LikeButton d={d} />
          <Link to={`/pulse/d/${d.id}#comments`} className="pl-comments" aria-label={`${d.comment_count} ${t("comments")}`}>
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
            <span className="num">{d.comment_count}</span>
          </Link>
        </div>
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ composer

function Composer({ symbol, initial, onDone, onCancel }: { symbol?: string; initial?: AnyObj; onDone: (d: AnyObj) => void; onCancel?: () => void }) {
  const { t } = useT();
  const meta = useMeta();
  const me = useMe();
  const [asset, setAsset] = useState<AnyObj | null>(symbol ? { symbol } : null);
  const [title, setTitle] = useState<string>(initial?.title ?? "");
  const [body, setBody] = useState<string>(initial?.body ?? "");
  const [sentiment, setSentiment] = useState<Sentiment | null>(initial?.sentiment ?? null);
  const [topics, setTopics] = useState<string[]>((initial?.topics ?? []).map((x: AnyObj) => x.key));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const editing = !!initial;
  if (!me.isLoading && !me.data?.user) {
    return (
      <div className="pl-compose-gate">
        <span>{t("Sign in to start a discussion. Reading is open to everyone.")}</span>
        <Link className="btn primary sm" to={`/login?next=${encodeURIComponent(window.location.pathname)}`}>{t("Sign in")}</Link>
      </div>
    );
  }
  const max = meta.data?.max_topics ?? 3;
  const toggle = (k: string) => setTopics((ts) => ts.includes(k) ? ts.filter((x) => x !== k) : ts.length < max ? [...ts, k] : ts);
  const ready = title.trim().length >= 8 && body.trim().length >= 20 && (editing || asset);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true); setErr(null);
    try {
      const payload = { title, body, sentiment, topics };
      const d = editing
        ? await api.patch<AnyObj>(`/pulse/discussions/${initial!.id}`, payload)
        : await api.post<AnyObj>("/pulse/discussions", { ...payload, symbol: asset!.symbol });
      onDone(d);
    } catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <form className="pl-compose" onSubmit={submit}>
      {!symbol && !editing && (
        <div className="pl-field">
          <span className="pl-label">{t("Asset")}</span>
          {asset
            ? <span className="pl-picked"><span className="mono">{asset.symbol}</span> <span className="muted">{asset.name}</span> <button type="button" className="link-btn xs" onClick={() => setAsset(null)}>{t("Change")}</button></span>
            : <SymbolSearch compact placeholder={t("Which stock, bond, fund or crypto?")} onPick={(s) => setAsset(s)} />}
        </div>
      )}
      <label className="pl-field">
        <span className="pl-label">{t("Title")}</span>
        <input className="input" value={title} maxLength={140} dir="auto" required onChange={(e) => setTitle(e.target.value)}
          placeholder={t("Your view in one line — e.g. Robotaxi upside is being underestimated")} />
        <span className="pl-count num">{title.trim().length}/140</span>
      </label>
      <label className="pl-field">
        <span className="pl-label">{t("Your reasoning")}</span>
        <textarea className="input" rows={5} value={body} maxLength={5000} dir="auto" required onChange={(e) => setBody(e.target.value)}
          placeholder={t("What do you see that others might be missing? Numbers, sources and risks make a discussion more useful.")} />
        <span className="pl-count num">{body.trim().length}/5,000</span>
      </label>
      <div className="pl-field">
        <span className="pl-label">{t("Your sentiment")} <span className="muted">· {t("optional")}</span></span>
        <div className="pl-seg" role="radiogroup" aria-label={t("Your sentiment")}>
          {SENTIMENTS.map((s) => (
            <button key={s} type="button" role="radio" aria-checked={sentiment === s} className={`${s} ${sentiment === s ? "on" : ""}`}
              onClick={() => setSentiment(sentiment === s ? null : s)}>{t(SENTIMENT_LABEL[s])}</button>
          ))}
        </div>
      </div>
      <div className="pl-field">
        <span className="pl-label">{t("Topics")} <span className="muted">· {t("up to")} {max}</span></span>
        <div className="pl-topics-pick">
          {(meta.data?.topics ?? []).map((tp: AnyObj) => (
            <button key={tp.key} type="button" aria-pressed={topics.includes(tp.key)} className={topics.includes(tp.key) ? "on" : ""}
              disabled={!topics.includes(tp.key) && topics.length >= max} onClick={() => toggle(tp.key)}>{t(tp.label)}</button>
          ))}
        </div>
      </div>
      {err && <div className="banner error small">{err}</div>}
      <div className="pl-compose-foot">
        <span className="xs muted">{t("Posted publicly as")} <b>{me.data?.user?.display_name || me.data?.user?.username}</b> · {t("Nexis Community")}</span>
        <span className="grow" />
        {onCancel && <button type="button" className="btn sm" onClick={onCancel}>{t("Cancel")}</button>}
        <button className="btn primary sm" disabled={!ready || busy}>{busy ? "…" : editing ? t("Save changes") : t("Publish")}</button>
      </div>
    </form>
  );
}

function ComposeToggle({ symbol, name, onPublished }: { symbol?: string; name?: string; onPublished: (d: AnyObj) => void }) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button type="button" className="pl-compose-closed" onClick={() => setOpen(true)}>
        <span>{symbol ? `${t("What do you think about")} ${name && name !== symbol ? name : symbol}?` : t("Start a discussion about any asset…")}</span>
        <span className="btn primary sm" aria-hidden>{t("New discussion")}</span>
      </button>
    );
  }
  return <Composer symbol={symbol} onCancel={() => setOpen(false)} onDone={(d) => { setOpen(false); onPublished(d); }} />;
}

// ------------------------------------------------------------------ asset page

function SentimentBar({ share, counts }: { share: AnyObj | null; counts: AnyObj }) {
  const { t } = useT();
  if (!share) return <div className="pl-empty-line">{t("No sentiment expressed yet.")}</div>;
  return (
    <div className="pl-bar-wrap">
      <div className="pl-bar" role="img" aria-label={SENTIMENTS.map((s) => `${t(SENTIMENT_LABEL[s])} ${share[s]}%`).join(", ")}>
        {SENTIMENTS.map((s) => <span key={s} className={s} style={{ flexGrow: share[s] }} />)}
      </div>
      <div className="pl-bar-legend">
        {SENTIMENTS.map((s) => (
          <span key={s} className={s}><i aria-hidden />{t(SENTIMENT_LABEL[s])} <b className="num">{share[s]}%</b> <span className="muted num">({counts[s]})</span></span>
        ))}
      </div>
    </div>
  );
}

function Activity({ history }: { history: AnyObj[] | null }) {
  const { t } = useT();
  if (!history) return <p className="pl-empty-line">{t("Activity appears once there are discussions in at least two different weeks.")}</p>;
  const max = Math.max(1, ...history.map((h) => h.total));
  return (
    <div className="pl-activity">
      <div className="pl-activity-bars" role="img" aria-label={t("Discussions per week over the last 12 weeks")}>
        {history.map((h) => (
          <div key={h.week_start} className="pl-week" title={`${h.week_start}: ${h.total} ${t("discussions")}${h.score ? ` · Pulse ${h.score}` : ""}`}>
            <div className="pl-week-stack" style={{ height: `${(h.total / max) * 100}%` }}>
              {(["bearish", "neutral", "bullish", "unclassified"] as const).map((s) => h.counts[s] ? <span key={s} className={s} style={{ flexGrow: h.counts[s] }} /> : null)}
              {h.research > 0 && <span className="research" style={{ flexGrow: h.research }} />}
            </div>
          </div>
        ))}
      </div>
      <div className="pl-activity-axis"><span>{history[0].week_start}</span><span>{t("this week")}</span></div>
      <div className="pl-activity-legend xs"><span><i className="bullish" />{t("Bullish")}</span><span><i className="neutral" />{t("Neutral")}</span><span><i className="bearish" />{t("Bearish")}</span><span><i className="research" />{t("Nexis Research")}</span></div>
    </div>
  );
}

function Summary({ symbol, total }: { symbol: string; total: number }) {
  const { t } = useT();
  const [asked, setAsked] = useState(false);
  const q = useQuery({ queryKey: ["pulse-summary", symbol, total], queryFn: () => api.get<AnyObj>(`/pulse/assets/${encodeURIComponent(symbol)}/summary`), enabled: asked && total >= 2, staleTime: 30 * 60_000, retry: 0 });
  const s = q.data;
  const ref = (ids: number[]) => ids.length ? <span className="refs">{ids.map((id) => <Link key={id} to={`/pulse/d/${id}`} title={s?.titles?.[String(id)]}>↗</Link>)}</span> : null;
  return (
    <section className="pl-section pl-ai">
      <div className="sec-head"><h2>{t("AI summary")}</h2><span className="sec-sub">{s?.available ? `${t("of")} ${s.discussions_used} ${t("discussions")}${s.model ? ` · ${String(s.model).replace(/^\w+\//, "")}` : ""}` : t("generated on request")}</span></div>
      {total < 2 ? <p className="pl-empty-line">{t("A summary needs at least two discussions.")}</p> : !asked ? (
        <button type="button" className="btn sm" onClick={() => setAsked(true)}>{t("Summarise the discussion")}</button>
      ) : q.isLoading ? <div className="ps-reading"><span className="rs-busy" aria-hidden /> {t("Reading the discussions…")}</div>
        : q.isError ? <div className="hm-error">{errorMessage(q.error)}</div>
        : s && !s.available ? <p className="pl-empty-line">{s.reason}</p>
        : s && (
          <>
            <p className="pl-ai-text" dir="auto">{s.summary}</p>
            {s.themes.length > 0 && <ul className="pl-ai-list">{s.themes.map((th: AnyObj, i: number) => <li key={i}><b>{th.title}</b> {th.text}{ref(th.refs)}</li>)}</ul>}
            <div className="pl-ai-sides">
              <div><div className="pl-mini-h pos">{t("Positive themes")}</div>{s.bull.length ? <ul className="pl-ai-list">{s.bull.map((p: AnyObj, i: number) => <li key={i}>{p.text}{ref(p.refs)}</li>)}</ul> : <p className="pl-empty-line">{t("None raised.")}</p>}</div>
              <div><div className="pl-mini-h neg">{t("Concerns")}</div>{s.bear.length ? <ul className="pl-ai-list">{s.bear.map((p: AnyObj, i: number) => <li key={i}>{p.text}{ref(p.refs)}</li>)}</ul> : <p className="pl-empty-line">{t("None raised.")}</p>}</div>
            </div>
            <p className="hm-fine">{t("Written by AI from the discussions on this page only. It describes members' opinions and does not check whether they are true.")}</p>
          </>
        )}
    </section>
  );
}

function Arguments({ side, items }: { side: "bullish" | "bearish"; items: AnyObj[] }) {
  const { t } = useT();
  return (
    <section className={`pl-args ${side}`}>
      <div className="pl-mini-h">{side === "bullish" ? t("Bullish arguments") : t("Bearish arguments")}</div>
      {items.length ? (
        <ol>
          {items.map((d) => (
            <li key={d.id}>
              <Link to={`/pulse/d/${d.id}`} dir="auto">{d.title}</Link>
              <span className="pl-args-meta">{d.author.display_name || d.author.username} · <span className="num">{d.like_count}</span> {d.like_count === 1 ? t("like") : t("likes")} · <span className="num">{d.comment_count}</span> {d.comment_count === 1 ? t("comment") : t("comments")}{!d.sentiment && ` · ${t("AI-classified")}`}</span>
            </li>
          ))}
        </ol>
      ) : <p className="pl-empty-line">{side === "bullish" ? t("No bullish arguments yet.") : t("No bearish arguments yet.")}</p>}
    </section>
  );
}

function DiscussionList({ symbol, topic, topicLabel, onTopic }: { symbol: string; topic: string | null; topicLabel: string; onTopic: (t: string | null) => void }) {
  const { t } = useT();
  const [sort, setSort] = useState<"new" | "top">("new");
  const [sentiment, setSentiment] = useState<Sentiment | null>(null);
  const [source, setSource] = useState<"community" | "research" | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["pulse-list", symbol, sort, sentiment, topic, source],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/pulse/discussions", { symbol, sort, ...(sentiment ? { sentiment } : {}), ...(topic ? { topic } : {}), ...(source ? { source } : {}), ...(pageParam ? { cursor: pageParam } : {}), limit: 15 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? null,
  });
  const items: AnyObj[] = (q.data?.pages ?? []).flatMap((p) => p.items);
  return (
    <div>
      <div className="pl-filters">
        <div className="pl-seg small" role="tablist" aria-label={t("Sort")}>
          {(["new", "top"] as const).map((s) => <button key={s} type="button" role="tab" aria-selected={sort === s} className={sort === s ? "on" : ""} onClick={() => setSort(s)}>{s === "new" ? t("Newest") : t("Most discussed")}</button>)}
        </div>
        <div className="pl-seg small" role="group" aria-label={t("Sentiment")}>
          {SENTIMENTS.map((s) => <button key={s} type="button" aria-pressed={sentiment === s} className={`${s} ${sentiment === s ? "on" : ""}`} onClick={() => setSentiment(sentiment === s ? null : s)}>{t(SENTIMENT_LABEL[s])}</button>)}
        </div>
        <div className="pl-seg small" role="group" aria-label={t("Who wrote it")}>
          {([["community", t("Members")], ["research", t("Nexis Research")]] as const).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={source === k} className={source === k ? "on" : ""} onClick={() => setSource(source === k ? null : k)}>{label}</button>
          ))}
        </div>
        {topic && <button type="button" className="pl-topic on" onClick={() => onTopic(null)}>{t("Topic")}: {t(topicLabel)} ×</button>}
      </div>
      {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w60" /><span className="skel w70" /></div>}
      {q.isError && <div className="hm-error">{errorMessage(q.error)} <button className="link-btn" onClick={() => q.refetch()}>{t("Retry")}</button></div>}
      <ol className="pl-list">{items.map((d) => <DiscussionRow key={d.id} d={d} />)}</ol>
      {!q.isLoading && !items.length && <p className="pl-empty-line">{sentiment || topic || source ? t("No discussions match these filters.") : t("No discussions yet — yours can be the first.")}</p>}
      {q.hasNextPage && <button type="button" className="btn sm pl-more" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "…" : t("Load more")}</button>}
    </div>
  );
}

function AssetPulse({ symbol }: { symbol: string }) {
  const { t } = useT();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [topic, setTopic] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["pulse-asset", symbol], queryFn: () => api.get<AnyObj>(`/pulse/assets/${encodeURIComponent(symbol)}`), staleTime: 30_000 });
  const d = q.data;
  const refresh = () => { qc.invalidateQueries({ queryKey: ["pulse-asset", symbol] }); qc.invalidateQueries({ queryKey: ["pulse-list", symbol] }); qc.invalidateQueries({ queryKey: ["pulse-discover"] }); };
  const topicLabel = useMemo(() => Object.fromEntries((d?.topics ?? []).map((x: AnyObj) => [x.key, x.label])), [d]);
  if (q.isError) return <div className="hm ps"><div className="hm-error">{errorMessage(q.error)} <button className="link-btn" onClick={() => q.refetch()}>{t("Retry")}</button></div></div>;
  const quote = d?.quote;
  const vol = d?.volume;
  return (
    <div className="hm pl">
      <header className="pl-head">
        <div className="hm-eyebrow"><Link to="/pulse">{t("Nexis Pulse")}</Link><span>{t("What investors on Nexis are saying")}</span></div>
        <div className="pl-title-row">
          <h1 dir="auto">{d?.name ?? symbol} <span className="mono pl-tk">{symbol}</span></h1>
          <div className="pl-quote">
            {quote?.price !== null && quote?.price !== undefined ? (
              <><span className="num pl-price">{fmtPrice(quote.price)}</span> <span className="wl-ccy">{quote.currency}</span> <Change pct={quote.change_pct} /> <Sparkline symbol={symbol} width={88} height={22} /></>
            ) : d && <span className="xs muted">{t("No live quote")}</span>}
            <Link className="sec-link" to={`/markets/${encodeURIComponent(symbol)}`}>{t("Chart, fundamentals and news")} <span aria-hidden>→</span></Link>
          </div>
        </div>
        <div className="pl-switch"><SymbolSearch compact placeholder={t("Another asset…")} onPick={(s) => nav(`/pulse/${encodeURIComponent(s.symbol)}`)} /></div>
      </header>

      {!d ? <div className="wire-skel"><span className="skel lead" /><span className="skel w80" /></div> : (
        <>
          <section className="pl-panel" aria-label={t("Nexis Pulse")}>
            <div className="pl-panel-score">
              <div className="pl-mini-h">{t("Community Pulse")}</div>
              <ScoreBlock score={d.score} />
              <p className="pl-basis">
                {d.score.available
                  ? <>{t("Based on")} <b className="num">{d.score.basis}</b> {t("member discussions from the last")} {d.score.window_days} {t("days")}{d.score.ai_classified ? ` · ${d.score.ai_classified} ${t("classified by AI")}` : ""}</>
                  : <>{t("Not enough member discussions yet.")} {t("A community score needs at least")} {d.score.minimum} {t("member discussions with a sentiment; there")} {d.score.basis === 1 ? t("is") : t("are")} <b className="num">{d.score.basis}</b>.</>}
              </p>
              <p className="pl-disclaimer">{t("Sentiment of Nexis member discussions — not a price prediction, a probability of the price rising, or financial advice.")}</p>
              {d.research.discussions > 0 && (
                <div className="pl-research-view">
                  <span className="pl-research-h"><Official /> {t("Nexis Research view")}</span>
                  {d.research.view.available
                    ? <span className="pl-research-v"><b className="num">{d.research.view.value}</b> / 100 · {t(d.research.view.label)}</span>
                    : <span className="pl-research-v">{SENTIMENTS.map((s) => `${d.research.sentiment.counts[s]} ${t(SENTIMENT_LABEL[s]).toLowerCase()}`).join(" · ")}</span>}
                  <span className="xs muted">{t("From")} {d.research.discussions} {t("editorial discussions — the research team's stance, kept separate from community sentiment.")}</span>
                </div>
              )}
            </div>
            <div className="pl-panel-cell">
              <div className="pl-mini-h">{t("Member sentiment")}</div>
              <SentimentBar share={d.sentiment.share} counts={d.sentiment.counts} />
              {d.research.sentiment.share && (
                <p className="pl-research-split xs">
                  <span className="muted">{t("Nexis Research")}:</span> {SENTIMENTS.map((s) => <span key={s} className={s}>{d.research.sentiment.counts[s]} {t(SENTIMENT_LABEL[s]).toLowerCase()}</span>)}
                </p>
              )}
              {d.sentiment.counts.unclassified > 0 && <p className="xs muted">{d.sentiment.counts.unclassified} {t("without a sentiment")}</p>}
            </div>
            <div className="pl-panel-cell">
              <div className="pl-mini-h">{t("Discussion volume")}</div>
              <div className="pl-stat"><b className="num">{d.total_discussions}</b> <span>{d.total_discussions === 1 ? t("discussion") : t("discussions")}</span></div>
              <div className="pl-stat small"><span className="num">{d.community_discussions}</span> {t("by members")} · <span className="num">{d.research_discussions}</span> {t("by Nexis Research")}</div>
              <div className="pl-stat small"><span className="num">{d.participants}</span> {d.participants === 1 ? t("member") : t("members")} · <span className="num">{vol.last_7_days}</span> {t("this week")}</div>
              {vol.change_pct !== null
                ? <div className={`pl-delta ${vol.change_pct >= 0 ? "pos" : "neg"}`}><span className="num">{vol.change_pct > 0 ? "+" : ""}{vol.change_pct}%</span> {t("vs the previous week")}</div>
                : <div className="xs muted">{vol.last_7_days ? t("No discussions the week before to compare with") : ""}</div>}
            </div>
            <div className="pl-panel-cell">
              <div className="pl-mini-h">{t("Trending topics")} <span className="muted">· 30 {t("days")}</span></div>
              {d.topics.length ? (
                <div className="pl-topic-list">
                  {d.topics.map((x: AnyObj) => <button key={x.key} type="button" className={`pl-topic ${topic === x.key ? "on" : ""}`} onClick={() => setTopic(topic === x.key ? null : x.key)}>{t(x.label)} <span className="num">{x.count}</span></button>)}
                </div>
              ) : <p className="pl-empty-line">{t("No topics tagged yet.")}</p>}
            </div>
          </section>

          <div className="pl-grid">
            <div className="pl-main">
              <section className="pl-section">
                <div className="sec-head"><h2>{t("What investors are saying")}</h2><span className="sec-sub">{t("Members and Nexis Research")}</span></div>
                <ComposeToggle symbol={symbol} name={d.name} onPublished={(x) => { refresh(); toast("success", t("Discussion published")); nav(`/pulse/d/${x.id}`); }} />
                <DiscussionList symbol={symbol} topic={topic} topicLabel={topic ? topicLabel[topic] ?? topic : ""} onTopic={setTopic} />
              </section>
            </div>
            <aside className="pl-side">
              <Arguments side="bullish" items={d.arguments.bullish} />
              <Arguments side="bearish" items={d.arguments.bearish} />
              <Summary symbol={symbol} total={d.total_discussions} />
              <section className="pl-section">
                <div className="sec-head"><h2>{t("Discussion activity")}</h2><span className="sec-sub">12 {t("weeks")}</span></div>
                <Activity history={d.history} />
              </section>
              <p className="hm-fine">{t("Sources")}: {d.sources.map((s: AnyObj) => t(s.label)).join(", ")}. {d.disclaimer}</p>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ discussion page

function Comments({ id, count }: { id: number; count: number }) {
  const { t, lang } = useT();
  const me = useMe().data?.user;
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["pulse-comments", id], queryFn: () => api.get<AnyObj[]>(`/social/posts/${id}/comments`) });
  const [text, setText] = useState("");
  const [replyTo, setReplyTo] = useState<AnyObj | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const all: AnyObj[] = q.data ?? [];
  const top = all.filter((c) => !c.parent_id);
  const replies = (pid: number) => all.filter((c) => c.parent_id === pid);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await api.post(`/social/posts/${id}/comments`, { body: text, ...(replyTo ? { parent_id: replyTo.id } : {}) });
      setText(""); setReplyTo(null);
      await qc.invalidateQueries({ queryKey: ["pulse-comments", id] });
      qc.invalidateQueries({ queryKey: ["pulse-discussion", id] });
    } catch (x) { toast("error", t("Couldn't post the comment"), errorMessage(x)); } finally { setBusy(false); }
  };
  const remove = async (cid: number) => {
    if (!window.confirm(t("Delete this comment?"))) return;
    try { await api.del(`/social/comments/${cid}`); qc.invalidateQueries({ queryKey: ["pulse-comments", id] }); qc.invalidateQueries({ queryKey: ["pulse-discussion", id] }); }
    catch (x) { toast("error", t("Couldn't delete the comment"), errorMessage(x)); }
  };
  const Item = ({ c }: { c: AnyObj }) => (
    <div className="pl-c">
      <div className="pl-c-head">
        <Link to={`/finstagram/u/${c.author.username}`} className="pl-author">{c.author.display_name || c.author.username}</Link>
        <time dateTime={c.created_at}>{ago(c.created_at, lang)}</time>
      </div>
      <p className="pl-c-body" dir="auto">{c.body}</p>
      <div className="pl-c-actions">
        {me && <button type="button" className="link-btn xs" onClick={() => { setReplyTo(c.parent_id ? all.find((x) => x.id === c.parent_id) ?? c : c); box.current?.focus(); }}>{t("Reply")}</button>}
        {c.is_mine && <button type="button" className="link-btn xs neg" onClick={() => remove(c.id)}>{t("Delete")}</button>}
      </div>
    </div>
  );
  return (
    <section className="pl-section" id="comments">
      <div className="sec-head"><h2>{t("Comments")}</h2><span className="sec-sub num">{count}</span></div>
      {me ? (
        <form className="pl-c-form" onSubmit={send}>
          {replyTo && <div className="xs muted">{t("Replying to")} <b>{replyTo.author.display_name || replyTo.author.username}</b> <button type="button" className="link-btn xs" onClick={() => setReplyTo(null)}>{t("Cancel")}</button></div>}
          <textarea ref={box} className="input" rows={2} maxLength={1000} dir="auto" value={text} onChange={(e) => setText(e.target.value)} placeholder={t("Add to the discussion…")} />
          <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary sm" disabled={!text.trim() || busy}>{busy ? "…" : replyTo ? t("Reply") : t("Comment")}</button></div>
        </form>
      ) : <div className="pl-compose-gate"><span>{t("Sign in to comment.")}</span><Link className="btn sm" to={`/login?next=${encodeURIComponent(window.location.pathname)}`}>{t("Sign in")}</Link></div>}
      {q.isLoading && <div className="wire-skel"><span className="skel w70" /><span className="skel w60" /></div>}
      <div className="pl-thread">
        {top.map((c) => (
          <div key={c.id} className="pl-c-wrap">
            <Item c={c} />
            {replies(c.id).length > 0 && <div className="pl-replies">{replies(c.id).map((r) => <Item key={r.id} c={r} />)}</div>}
          </div>
        ))}
      </div>
      {!q.isLoading && !all.length && <p className="pl-empty-line">{t("No comments yet.")}</p>}
    </section>
  );
}

function DiscussionPage({ id }: { id: number }) {
  const { t, lang } = useT();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const q = useQuery({ queryKey: ["pulse-discussion", id], queryFn: () => api.get<AnyObj>(`/pulse/discussions/${id}`) });
  const d = q.data;
  useEffect(() => { if (d && window.location.hash === "#comments") window.setTimeout(() => document.getElementById("comments")?.scrollIntoView({ block: "start" }), 200); }, [d]);
  if (q.isError) return <div className="hm pl"><div className="hm-error">{errorMessage(q.error)}</div><Link to="/pulse" className="sec-link">{t("Back to Nexis Pulse")} →</Link></div>;
  if (!d) return <div className="hm pl"><div className="wire-skel"><span className="skel w60" /><span className="skel w80" /><span className="skel w70" /></div></div>;
  const remove = async () => {
    if (!window.confirm(t("Delete this discussion? Its comments and likes are removed too."))) return;
    try {
      await api.del(`/pulse/discussions/${id}`);
      qc.invalidateQueries({ queryKey: ["pulse-asset", d.asset] }); qc.invalidateQueries({ queryKey: ["pulse-list"] }); qc.invalidateQueries({ queryKey: ["pulse-discover"] });
      toast("success", t("Discussion deleted")); nav(`/pulse/${encodeURIComponent(d.asset)}`);
    } catch (x) { toast("error", t("Couldn't delete"), errorMessage(x)); }
  };
  return (
    <div className="hm pl pl-detail">
      <div className="hm-eyebrow"><Link to="/pulse">{t("Nexis Pulse")}</Link><Link to={`/pulse/${encodeURIComponent(d.asset)}`} className="mono">{d.asset}</Link><span>{d.asset_name}</span></div>
      {editing ? (
        <Composer initial={d} onCancel={() => setEditing(false)} onDone={(x) => { qc.setQueryData(["pulse-discussion", id], x); qc.invalidateQueries({ queryKey: ["pulse-asset", d.asset] }); qc.invalidateQueries({ queryKey: ["pulse-list"] }); setEditing(false); toast("success", t("Changes saved")); }} />
      ) : (
        <article className="pl-article">
          <div className="pl-row-meta">
            <SentimentTag value={d.sentiment} />
            {d.ai_sentiment && (d.sentiment ? d.ai_sentiment !== d.sentiment : true) && <SentimentTag value={d.ai_sentiment} ai />}
            {d.topics.map((tp: AnyObj) => <span key={tp.key} className="pl-topic">{t(tp.label)}</span>)}
          </div>
          <h1 dir="auto">{d.title}</h1>
          <div className="pl-byline">
            <Link to={`/finstagram/u/${d.author.username}`} className="pl-author">{d.author.display_name || d.author.username}</Link>
            {d.source.editorial && <Official />}
            <span className="muted">@{d.author.username}</span>
            <time dateTime={d.created_at}>{ago(d.created_at, lang)}</time>
            {d.edited_at && <span className="muted">· {t("edited")}</span>}
            <span className="pl-src">{t(d.source.label)}</span>
          </div>
          <div className="pl-article-body" dir="auto">{d.body}</div>
          <div className="pl-article-actions">
            <LikeButton d={d} />
            <span className="grow" />
            <Link className="sec-link" to={`/pulse/${encodeURIComponent(d.asset)}`}>{t("See the Pulse for")} {d.asset} <span aria-hidden>→</span></Link>
            {d.is_mine && <><button type="button" className="btn sm" onClick={() => setEditing(true)}>{t("Edit")}</button><button type="button" className="btn sm danger" onClick={remove}>{t("Delete")}</button></>}
          </div>
          <p className="hm-fine">{d.source.editorial
            ? t("Editorial analysis by the Nexis Research team. It is not a member opinion, it does not count toward the community Pulse score, and it is not financial advice.")
            : t("A personal opinion from a Nexis member, not verified by Nexis and not financial advice.")}</p>
        </article>
      )}
      <Comments id={id} count={d.comment_count} />
    </div>
  );
}

// ------------------------------------------------------------------ discover

function AssetTable({ rows, empty, metric }: { rows: AnyObj[]; empty: string; metric: string }) {
  const { t } = useT();
  if (!rows.length) return <p className="pl-empty-line">{empty}</p>;
  return (
    <table className="pl-table">
      <thead><tr><th>{t("Asset")}</th><th className="r">{metric}</th><th className="r" title={t("Community Pulse from member discussions")}>{t("Pulse")}</th><th className="r" title={t("Nexis Research view — editorial, not community sentiment")}>{t("Research")}</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol}>
            <td><Link to={`/pulse/${encodeURIComponent(r.symbol)}`} className="wl-asset"><span className="mono wl-tk">{r.symbol}</span><span className="wl-name">{r.name}</span></Link></td>
            <td className="r num">{r.discussions}</td>
            <td className="r"><ScoreBlock score={r.score} compact /></td>
            <td className="r"><ScoreBlock score={r.research_view} compact /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PulseDiscover() {
  const { t } = useT();
  const nav = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["pulse-discover"], queryFn: () => api.get<AnyObj>("/pulse/discover"), staleTime: 30_000 });
  const d = q.data;
  const empty = d && d.totals.discussions === 0;
  return (
    <div className="hm pl">
      <header className="pl-head">
        <div className="hm-eyebrow"><span>{t("Nexis Pulse")}</span>
          {d && !empty && <span className="num">{d.totals.discussions} {t("discussions")} · {d.totals.assets} {t("assets")} · {d.totals.research_discussions} {t("by Nexis Research")}{d.totals.community_discussions > 0 && <> · {d.totals.community_discussions} {t("by")} {d.totals.participants} {d.totals.participants === 1 ? t("member") : t("members")}</>}</span>}</div>
        <h1>{t("See what investors are saying.")}</h1>
        <p className="hm-lede">{t("Discussions by Nexis members and the Nexis Research team — the arguments for and against and the topics people care about. The community Pulse score is built from member discussions only.")}</p>
        <div className="pl-switch wide"><SymbolSearch placeholder={t("Search an asset — Tesla, NVDA, Emirates NBD…")} onPick={(s) => nav(`/pulse/${encodeURIComponent(s.symbol)}`)} /></div>
      </header>
      {q.isError && <div className="hm-error">{errorMessage(q.error)} <button className="link-btn" onClick={() => q.refetch()}>{t("Retry")}</button></div>}
      {!d && !q.isError && <div className="wire-skel"><span className="skel lead" /><span className="skel w80" /></div>}
      {empty && (
        <section className="pl-empty">
          <h2>{t("No discussions yet")}</h2>
          <p>{t("Pulse is built only from what members write, so it starts empty. Pick an asset you follow, share your view and why, and mark whether you're bullish, neutral or bearish. Once a few people have weighed in, its Pulse score, sentiment and trending topics appear.")}</p>
          <ComposeToggle onPublished={(x) => { qc.invalidateQueries({ queryKey: ["pulse-discover"] }); nav(`/pulse/d/${x.id}`); }} />
        </section>
      )}
      {d && !empty && (
        <div className="pl-grid">
          <div className="pl-main">
            <section className="pl-section">
              <ComposeToggle onPublished={(x) => { qc.invalidateQueries({ queryKey: ["pulse-discover"] }); toast("success", t("Discussion published")); nav(`/pulse/d/${x.id}`); }} />
            </section>
            {d.trending_discussions.length > 0 && (
              <section className="pl-section">
                <div className="sec-head"><h2>{t("Trending discussions")}</h2><span className="sec-sub">{t("most engagement · 14 days")}</span></div>
                <ol className="pl-list">{d.trending_discussions.map((x: AnyObj) => <DiscussionRow key={x.id} d={x} showAsset />)}</ol>
              </section>
            )}
            <section className="pl-section">
              <div className="sec-head"><h2>{t("Recent discussions")}</h2></div>
              <ol className="pl-list">{d.recent.map((x: AnyObj) => <DiscussionRow key={x.id} d={x} showAsset />)}</ol>
            </section>
          </div>
          <aside className="pl-side">
            <section className="pl-section">
              <div className="sec-head"><h2>{t("Trending assets")}</h2><span className="sec-sub">7 {t("days")}</span></div>
              <AssetTable rows={d.trending_assets} metric={t("Discussions")} empty={t("No discussions in the last 7 days.")} />
            </section>
            <section className="pl-section">
              <div className="sec-head"><h2>{t("Most discussed")}</h2><span className="sec-sub">30 {t("days")}</span></div>
              <AssetTable rows={d.most_discussed} metric={t("Discussions")} empty={t("No discussions in the last 30 days.")} />
            </section>
            <section className="pl-section">
              <div className="sec-head"><h2>{t("Biggest sentiment changes")}</h2><span className="sec-sub">{t("last 14 days vs the 14 before")}</span></div>
              {d.sentiment_changes.length ? (
                <table className="pl-table"><tbody>
                  {d.sentiment_changes.map((r: AnyObj) => (
                    <tr key={r.symbol}>
                      <td><Link to={`/pulse/${encodeURIComponent(r.symbol)}`} className="wl-asset"><span className="mono wl-tk">{r.symbol}</span><span className="wl-name">{r.name}</span></Link></td>
                      <td className="r num">{r.from} → {r.to}</td>
                      <td className={`r num ${r.change > 0 ? "pos" : "neg"}`}>{r.change > 0 ? "+" : ""}{r.change}</td>
                    </tr>
                  ))}
                </tbody></table>
              ) : <p className="pl-empty-line">{t("Appears when an asset has enough discussions in both periods to compare.")}</p>}
            </section>
            <section className="pl-section">
              <div className="sec-head"><h2>{t("Trending topics")}</h2><span className="sec-sub">7 {t("days")}</span></div>
              {d.topics.length ? <div className="pl-topic-list">{d.topics.map((x: AnyObj) => <span key={x.key} className="pl-topic">{t(x.label)} <span className="num">{x.count}</span></span>)}</div>
                : <p className="pl-empty-line">{t("No topics tagged in the last 7 days.")}</p>}
            </section>
            <p className="hm-fine">{d.disclaimer}</p>
          </aside>
        </div>
      )}
    </div>
  );
}

export default function Pulse() {
  const { symbol, id } = useParams();
  if (id) return <DiscussionPage key={id} id={Number(id)} />;
  return symbol ? <AssetPulse key={symbol} symbol={decodeURIComponent(symbol).toUpperCase()} /> : <PulseDiscover />;
}
