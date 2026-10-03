import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "./market";
import { toast } from "./toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

export type Sentiment = "bullish" | "neutral" | "bearish";
export const SENTIMENTS: Sentiment[] = ["bullish", "neutral", "bearish"];
export const SENTIMENT_LABEL: Record<Sentiment, string> = { bullish: "Bullish", neutral: "Neutral", bearish: "Bearish" };

export const discussionUrl = (id: number | string) => `/pulse/discussion/${id}`;
export const tickerUrl = (sym: string) => `/pulse/ticker/${encodeURIComponent(sym)}`;

export function ago(iso: string | null | undefined, lang: string): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto", style: "short" });
  if (s < 60) return rtf.format(0, "minute");
  if (s < 3600) return rtf.format(-Math.floor(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.floor(s / 3600), "hour");
  if (s < 86400 * 30) return rtf.format(-Math.floor(s / 86400), "day");
  return new Date(iso).toLocaleDateString(lang === "ar" ? "ar-AE" : "en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function SentimentTag({ value, ai }: { value: string | null | undefined; ai?: boolean }) {
  const { t } = useT();
  if (!value) return null;
  return (
    <span className={`pl-sent ${value} ${ai ? "ai" : ""}`} title={ai ? t("Detected by AI from the text — the author didn't choose one") : t("Chosen by the author")}>
      {ai && <span className="pl-sent-ai">AI</span>}{t(SENTIMENT_LABEL[value as Sentiment] ?? value)}
    </span>
  );
}

/** Marks content published by the official Nexis Research account. */
export function Official() {
  const { t } = useT();
  return <span className="pl-official" title={t("Editorial analysis by the Nexis Research team — not a member opinion")}>{t("Official")}</span>;
}

/** Marks a Nexis-generated persona. Small on purpose, but always present. */
export function GeneratedMark() {
  const { t } = useT();
  return <span className="pl-gen" title={t("Nexis-generated persona — a fictional investor whose views are written by AI from real events. Not a real person.")}>{t("AI")}</span>;
}

export function ScoreBlock({ score, compact }: { score: AnyObj | null | undefined; compact?: boolean }) {
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

export function LikeButton({ d }: { d: AnyObj }) {
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

export function SaveButton({ d }: { d: AnyObj }) {
  const { t } = useT();
  const me = useMe().data?.user;
  const nav = useNavigate();
  const [saved, setSaved] = useState<boolean>(!!d.saved_by_me);
  useEffect(() => setSaved(!!d.saved_by_me), [d.saved_by_me]);
  const toggle = async () => {
    if (!me) { nav(`/login?next=${encodeURIComponent(window.location.pathname)}`); return; }
    setSaved(!saved);
    try { const r = await api.post<AnyObj>(`/social/posts/${d.id}/save`); setSaved(r.saved); }
    catch (e) { setSaved(saved); toast("error", t("Couldn't save"), errorMessage(e)); }
  };
  return (
    <button type="button" className={`pl-like save ${saved ? "on" : ""}`} aria-pressed={saved} onClick={toggle} aria-label={saved ? t("Remove from saved") : t("Save")}>
      <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden><path d="M4 2.5h8v11l-4-3-4 3Z" fill={saved ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
    </button>
  );
}

/** Author name with the right link and marker: member, Nexis Research (official) or Nexis-generated persona. */
export function AuthorLink({ author }: { author: AnyObj }) {
  const persona = author.kind === "persona";
  const to = persona ? `/pulse/persona/${author.username}` : `/finstagram/u/${author.username}`;
  return (
    <span className="pl-who">
      <Link to={to} className="pl-author"><bdi>{author.display_name || author.username}</bdi></Link>
      {persona && <GeneratedMark />}
      {author.kind === "editorial" && <Official />}
    </span>
  );
}

/** Where a discussion's facts come from. */
export function SourceLine({ event }: { event: AnyObj | null | undefined }) {
  const { t, lang } = useT();
  if (!event) return null;
  const label = event.publisher || event.provider_label;
  return (
    <span className="pl-sourceline">
      <span className="pl-sourceline-k">{t("Source")}</span>
      {event.url && /^https?:/.test(event.url)
        ? <a href={event.url} target="_blank" rel="noreferrer noopener">{label} ↗</a>
        : event.url ? <Link to={event.url}>{label}</Link> : <span>{label}</span>}
      <time dateTime={event.published_at}>{ago(event.published_at, lang)}</time>
    </span>
  );
}

const CommentIcon = () => <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>;

/** One discussion in a list: sentiment, title, an excerpt and who said it. */
export function DiscussionRow({ d, showAsset, preview }: { d: AnyObj; showAsset?: boolean; preview?: boolean }) {
  const { t, lang } = useT();
  return (
    <li className={`pl-row ${d.source.editorial ? "editorial" : ""} ${d.source.generated ? "generated" : ""}`}>
      <div className="pl-row-main">
        <div className="pl-row-meta">
          {showAsset && <Link className="mono pl-asset" to={tickerUrl(d.asset)}>{d.asset}</Link>}
          <SentimentTag value={d.sentiment} />
          {!d.sentiment && <SentimentTag value={d.ai_sentiment} ai />}
          {d.topics.map((tp: AnyObj) => <span key={tp.key} className="pl-topic">{t(tp.label)}</span>)}
        </div>
        <Link className="pl-row-title" to={discussionUrl(d.id)} dir="auto">{d.title}</Link>
        <p className="pl-row-body" dir="auto">{d.body}</p>
        {d.event && <SourceLine event={d.event} />}
        {preview && d.last_reply && (
          <Link to={`${discussionUrl(d.id)}#comments`} className="pl-preview">
            <span className="pl-preview-who">{d.last_reply.author.display_name || d.last_reply.author.username}{d.last_reply.author.kind === "persona" && <GeneratedMark />}</span>
            <span className="pl-preview-text" dir="auto">{d.last_reply.body}</span>
          </Link>
        )}
        <div className="pl-row-foot">
          <AuthorLink author={d.author} />
          <time dateTime={d.created_at}>{ago(d.created_at, lang)}</time>
          <span className="pl-src">{t(d.source.label)}</span>
          <span className="grow" />
          <LikeButton d={d} />
          <Link to={`${discussionUrl(d.id)}#comments`} className="pl-comments" aria-label={`${d.comment_count} ${t("comments")}`}>
            <CommentIcon /><span className="num">{d.comment_count}</span>
          </Link>
          {preview && <SaveButton d={d} />}
        </div>
      </div>
    </li>
  );
}

/** Track / add to portfolio / intelligence actions for one asset. */
export function TrackBar({ symbol, compact }: { symbol: string; compact?: boolean }) {
  const { t } = useT();
  const nav = useNavigate();
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["track", symbol], queryFn: () => api.get<AnyObj>(`/me/track/${encodeURIComponent(symbol)}`), staleTime: 60_000 });
  const s = st.data;
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    if (!s?.signed_in) { nav(`/login?next=${encodeURIComponent(window.location.pathname)}`); return; }
    setBusy(true);
    try {
      if (s.watching) await api.del(`/me/watchlist/${encodeURIComponent(symbol)}`);
      else await api.put(`/me/watchlist/${encodeURIComponent(symbol)}`, {});
      await qc.invalidateQueries({ queryKey: ["track", symbol] });
      qc.invalidateQueries({ queryKey: ["watchlist"] });
      toast("success", s.watching ? `${t("Stopped tracking")} ${symbol}` : `${t("Tracking")} ${symbol}`, s.watching ? undefined : t("You'll see its developments in Portfolio → Intelligence."));
    } catch (e) { toast("error", t("Couldn't update your watchlist"), errorMessage(e)); }
    finally { setBusy(false); }
  };
  return (
    <div className={`pl-trackbar ${compact ? "compact" : ""}`}>
      <button type="button" className={`btn sm ${s?.watching ? "" : "primary"}`} disabled={busy} onClick={toggle} aria-pressed={!!s?.watching}>
        {s?.watching ? `✓ ${t("Tracking")}` : `${t("Track")} ${symbol}`}
      </button>
      {s?.holding
        ? <Link className="btn sm" to="/portfolio">{t("In your portfolio")}</Link>
        : <Link className="btn sm" to={`/portfolio?add=${encodeURIComponent(symbol)}`}>{t("Add to portfolio")}</Link>}
      <Link className="btn sm ghost" to={`/portfolio?tab=intelligence&symbol=${encodeURIComponent(symbol)}`}>{t("View intelligence")}</Link>
    </div>
  );
}

/** Product-level note on what is generated and what is written by members. */
export function Disclosure({ compact }: { compact?: boolean }) {
  const { t } = useT();
  return (
    <p className={`pl-disclosure ${compact ? "compact" : ""}`}>
      <b>{t("About Pulse content")}</b> {t("Some conversations are generated by Nexis to provide diverse financial perspectives: they are written by AI personas (marked AI) about real, sourced events. Community posts are written by Nexis members; Nexis Research posts are editorial. Nothing here is financial advice.")}
    </p>
  );
}
