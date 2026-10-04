import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SymbolSearch, useMe } from "./market";
import { toast } from "./toast";
import { useT } from "../i18n";
import { api, ApiError, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

// ------------------------------------------------------------------ time

export function ago(iso: string | null | undefined, lang = "en"): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (s < 60) return lang === "ar" ? "الآن" : "just now";
  if (s < 3600) return rtf.format(-Math.floor(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.floor(s / 3600), "hour");
  if (s < 86400 * 30) return rtf.format(-Math.floor(s / 86400), "day");
  return new Date(iso).toLocaleDateString(lang, { day: "numeric", month: "short", year: "numeric" });
}

export function stamp(iso: string | null | undefined, lang = "en"): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(lang, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const assetPath = (sym: string) => `/pulse/asset/${encodeURIComponent(sym)}`;
export const topicPath = (key: string) => `/pulse/topic/${encodeURIComponent(key)}`;

// ------------------------------------------------------------------ the gate: sign-in and Terms before writing

const TERMS_EVENT = "nexis:terms-required";
const PENDING_KEY = "nexis.terms.pending";
const DISMISSED_KEY = "nexis.terms.dismissed";

export function requireTerms() {
  window.dispatchEvent(new CustomEvent(TERMS_EVENT));
}

/** Remember that the person ticked the Terms box before leaving for Google sign-in, so it can be recorded on return. */
export function rememberTermsChoice(versions: { terms_version: string; privacy_version: string }) {
  try { sessionStorage.setItem(PENDING_KEY, JSON.stringify(versions)); } catch { /* storage unavailable */ }
}

/** Runs a write; sends signed-out visitors to sign in and asks for the Terms when the server says they're needed. */
export function useParticipate() {
  const me = useMe().data?.user;
  const nav = useNavigate();
  return async function participate<T>(fn: () => Promise<T>): Promise<T | undefined> {
    if (!me) {
      nav(`/login?mode=signup&next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
      return undefined;
    }
    if (me.legal && !me.legal.accepted) {
      requireTerms();
      return undefined;
    }
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ApiError && e.code === "terms_required") { requireTerms(); return undefined; }
      if (e instanceof ApiError && e.status === 401) { nav(`/login?next=${encodeURIComponent(window.location.pathname)}`); return undefined; }
      throw e;
    }
  };
}

export function LegalGate() {
  const me = useMe().data?.user;
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const docs = useQuery({ queryKey: ["legal-docs"], queryFn: () => api.get<AnyObj>("/legal"), staleTime: 3600_000, enabled: !!me });
  const needs = !!me?.legal && !me.legal.accepted;
  const accept = async (versions: AnyObj) => {
    await api.post("/legal/accept", versions);
    await qc.invalidateQueries({ queryKey: ["me"] });
  };
  useEffect(() => {
    const h = () => setOpen(true);
    window.addEventListener(TERMS_EVENT, h);
    return () => window.removeEventListener(TERMS_EVENT, h);
  }, []);
  useEffect(() => {
    if (!needs || !docs.data) return;
    let pending: AnyObj | null = null;
    try { pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) ?? "null"); sessionStorage.removeItem(PENDING_KEY); } catch { pending = null; }
    if (pending && pending.terms_version === docs.data.terms_version && pending.privacy_version === docs.data.privacy_version) {
      void accept(pending).catch(() => setOpen(true));  // they ticked the box before continuing with Google
    } else {
      let dismissed = false;
      try { dismissed = sessionStorage.getItem(DISMISSED_KEY) === "1"; } catch { /* storage unavailable */ }
      if (!dismissed) setOpen(true);  // asked once per visit; posting asks again
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [needs, docs.data]);
  if (!open || !needs || !docs.data) return null;
  const submit = async () => {
    setBusy(true);
    try { await accept({ terms_version: docs.data.terms_version, privacy_version: docs.data.privacy_version }); setOpen(false); toast("success", "Thanks — you're all set"); }
    catch (x) { toast("error", "Couldn't record that", errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <div className="np-modal-scrim" role="presentation">
      <div className="np-modal" role="dialog" aria-modal="true" aria-labelledby="gate-title">
        <h2 id="gate-title">Before you post</h2>
        <p>Pulse is anonymous to other people, but it has rules. Please read and accept the current Terms of Use and Privacy Policy.</p>
        <ul className="np-doclist">
          <li><Link to="/terms" target="_blank">Terms of Use</Link> <span className="muted xs">· version {docs.data.terms_version}</span></li>
          <li><Link to="/privacy" target="_blank">Privacy Policy</Link> <span className="muted xs">· version {docs.data.privacy_version}</span></li>
          <li><Link to="/community-guidelines" target="_blank">Community Guidelines</Link></li>
        </ul>
        <label className="np-check">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span>I agree to the Nexis Finance Terms of Use and acknowledge the Privacy Policy.</span>
        </label>
        <div className="np-modal-foot">
          <button type="button" className="btn sm ghost" onClick={() => { try { sessionStorage.setItem(DISMISSED_KEY, "1"); } catch { /* ignore */ } setOpen(false); }}>Not now</button>
          <button type="button" className="btn primary sm" disabled={!agree || busy} onClick={submit}>{busy ? "…" : "Accept and continue"}</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ labels

export function Byline({ author, aiAssisted }: { author: AnyObj; aiAssisted?: boolean }) {
  if (author?.type === "editorial") {
    return (
      <span className="np-by editorial">
        <span className="np-mark" aria-hidden>N</span>Nexis Editorial{aiAssisted && <span className="np-ai" title="Drafted with AI assistance; see the AI disclosure">AI-assisted</span>}
      </span>
    );
  }
  if (author?.type === "public") {
    return <span className={`np-by public ${author.platform ?? ""}`}><span className="np-plat" aria-hidden>{PLATFORM_MARK[author.platform] ?? "↗"}</span>{author.display_name}</span>;
  }
  return <span className="np-by">Anonymous{author?.is_op && <span className="np-op" title="Wrote the original post">Author</span>}</span>;
}

const PLATFORM_MARK: Record<string, string> = { reddit: "r/", hn: "Y", stocktwits: "$" };
const PLATFORM_NAME: Record<string, string> = { reddit: "Reddit", hn: "Hacker News", stocktwits: "StockTwits" };

export function KindLabel({ kind, origin }: { kind: string; origin?: AnyObj | null }) {
  if (kind === "public") return <span className={`np-kind public ${origin?.platform ?? ""}`} title="Collected from a public discussion on another site">{origin?.label ?? "Public"}</span>;
  return kind === "editorial" ? <span className="np-kind editorial">Nexis</span> : <span className="np-kind">Community</span>;
}

export const STANCE_GROUPS: Record<string, string> = {
  support: "Agrees", pushback: "Pushes back", question: "Asks", context: "Adds context", bullish: "Bullish", bearish: "Bearish",
};

const STANCE: Record<string, string> = { bullish: "Bullish", bearish: "Bearish", neutral: "Neutral", question: "Question", agree: "Agrees", disagree: "Disagrees" };
export function Stance({ s }: { s?: string | null }) {
  if (!s) return null;
  return <span className={`np-stance ${s}`}>{STANCE[s] ?? s}</span>;
}

export function Topics({ topics }: { topics: AnyObj[] }) {
  if (!topics?.length) return null;
  return <span className="np-topics">{topics.map((t) => <Link key={t.key} to={topicPath(t.key)} className="np-topic">{t.label}</Link>)}</span>;
}

function plural(n: number, one: string, many: string) {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** One discussion in a list: who (Anonymous / Nexis), what, and real activity counts only. */
export function DiscussionRow({ d, showAsset = true }: { d: AnyObj; showAsset?: boolean }) {
  const { lang } = useT();
  const c = d.counts ?? {};
  const updated = d.kind === "editorial" ? d.updated_at : null;
  return (
    <li className={`np-row ${d.kind}`}>
      <div className="np-row-meta">
        <KindLabel kind={d.kind} origin={d.origin} />
        {showAsset && d.symbol && <Link to={assetPath(d.symbol)} className="np-sym mono">{d.symbol}</Link>}
        <Stance s={d.stance} />
        <time dateTime={updated ?? d.created_at} title={stamp(updated ?? d.created_at, lang)}>{updated ? `Updated ${ago(updated, lang)}` : ago(d.created_at, lang)}</time>
        {d.viewer?.pending_review && <span className="np-pending">Waiting for review</span>}
      </div>
      <Link to={d.url} className="np-row-title" dir="auto">{d.title}</Link>
      {d.excerpt && <p className="np-row-ex" dir="auto">{d.excerpt}</p>}
      <div className="np-row-foot">
        {d.kind === "editorial" && d.debate && (d.debate.bull || d.debate.bear) ? (
          <span className="np-split"><span className="pos">{d.debate.bull} bull</span><span className="neg">{d.debate.bear} bear</span>{d.debate.open ? <span>{d.debate.open} open</span> : null}</span>
        ) : null}
        {d.kind === "public" && d.origin ? (
          <span className="np-split">
            {Object.entries(d.origin.stances ?? {}).map(([k, n]) => <span key={k} className={k === "support" || k === "bullish" ? "pos" : k === "pushback" || k === "bearish" ? "neg" : ""}>{n as number} {(STANCE_GROUPS[k] ?? k).toLowerCase()}</span>)}
          </span>
        ) : null}
        {d.kind === "public"
          ? <span>{c.comments ? `${plural(c.comments, "reply", "replies")} on Nexis` : `Discuss on Nexis`}</span>
          : <span>{c.comments ? plural(c.comments, "reply", "replies") : "No replies yet"}</span>}
        {d.kind === "public" && d.origin?.url && <a href={d.origin.url} target="_blank" rel="noreferrer noopener nofollow" className="np-orig">Original on {PLATFORM_NAME[d.origin.platform] ?? "source"} ↗</a>}
        {c.comments > 0 && c.participants > 1 && <span>{plural(c.participants, "person", "people")}</span>}
        {c.agree + c.disagree > 0 && <span>{c.agree} agree · {c.disagree} disagree</span>}
        <Topics topics={(d.topics ?? []).slice(0, 2)} />
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ reactions

const REACTIONS = [
  { key: "agree", label: "Agree" },
  { key: "disagree", label: "Disagree" },
  { key: "interesting", label: "Interesting" },
] as const;

export function Reactions({ type, id, counts, mine, own, onChange }: {
  type: "discussion" | "comment"; id: string | number; counts: AnyObj; mine: string[]; own?: boolean; onChange?: (r: AnyObj) => void;
}) {
  const participate = useParticipate();
  const [state, setState] = useState({ counts, mine });
  useEffect(() => setState({ counts, mine }), [counts, mine]);
  const press = async (kind: string) => {
    try {
      const r = await participate(() => api.post<AnyObj>("/pulse/reactions", { target_type: type, target_id: String(id), kind }));
      if (r) { setState({ counts: r.counts, mine: r.reactions }); onChange?.(r); }
    } catch (x) { toast("error", "Couldn't react", errorMessage(x)); }
  };
  return (
    <span className="np-react" role="group" aria-label="Reactions">
      {REACTIONS.map((r) => {
        const on = state.mine.includes(r.key);
        const n = state.counts?.[r.key] ?? 0;
        return (
          <button key={r.key} type="button" className={`np-react-btn ${r.key} ${on ? "on" : ""}`} aria-pressed={on} disabled={own}
            title={own ? "You can't react to your own post" : undefined} onClick={() => press(r.key)}>
            {r.label}{n > 0 && <span className="num">{n}</span>}
          </button>
        );
      })}
    </span>
  );
}

// ------------------------------------------------------------------ report

export function ReportButton({ type, id, compact }: { type: "discussion" | "comment"; id: string | number; compact?: boolean }) {
  const participate = useParticipate();
  const meta = useQuery({ queryKey: ["pulse-meta"], queryFn: () => api.get<AnyObj>("/pulse/meta"), staleTime: 3600_000 });
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      const r = await participate(() => api.post<AnyObj>("/pulse/reports", { target_type: type, target_id: String(id), reason, detail: detail || null }));
      if (r) { toast("success", r.already ? "You already reported this" : "Report sent", "A moderator will review it. The author isn't told who reported it."); setOpen(false); }
    } catch (x) { toast("error", "Couldn't send the report", errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <>
      <button type="button" className={`link-btn xs ${compact ? "muted" : ""}`} onClick={() => setOpen(true)}>Report</button>
      {open && (
        <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div className="np-modal" role="dialog" aria-modal="true" aria-labelledby="report-title">
            <h2 id="report-title">Report this {type}</h2>
            <p className="small text2">Disagreement and negative opinions are allowed. Report content that breaks the <Link to="/community-guidelines" target="_blank">Community Guidelines</Link>.</p>
            <div className="np-reasons">
              {(meta.data?.report_reasons ?? []).map((r: AnyObj) => (
                <label key={r.key} className="np-radio"><input type="radio" name="reason" value={r.key} checked={reason === r.key} onChange={() => setReason(r.key)} /><span>{r.label}</span></label>
              ))}
            </div>
            <textarea className="input" rows={3} maxLength={500} placeholder="Anything a moderator should know (optional)" value={detail} onChange={(e) => setDetail(e.target.value)} />
            <div className="np-modal-foot">
              <button type="button" className="btn sm ghost" onClick={() => setOpen(false)}>Cancel</button>
              <button type="button" className="btn primary sm" disabled={!reason || busy} onClick={send}>{busy ? "…" : "Send report"}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ start a discussion

export function Composer({ symbol, topic, onClose }: { symbol?: string; topic?: string; onClose: () => void }) {
  const participate = useParticipate();
  const nav = useNavigate();
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["pulse-meta"], queryFn: () => api.get<AnyObj>("/pulse/meta"), staleTime: 3600_000 });
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [asset, setAsset] = useState<AnyObj | null>(symbol ? { symbol } : null);
  const [stance, setStance] = useState<string>("question");
  const [topics, setTopics] = useState<string[]>(topic ? [topic] : []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const primary: AnyObj[] = (meta.data?.topics ?? []).filter((t: AnyObj) => t.primary);
  const toggle = (k: string) => setTopics((ts) => (ts.includes(k) ? ts.filter((x) => x !== k) : ts.length >= 3 ? ts : [...ts, k]));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const d = await participate(() => api.post<AnyObj>("/pulse/discussions", { title, body, symbol: asset?.symbol ?? null, stance, topics }));
      if (d) {
        qc.invalidateQueries({ queryKey: ["pulse-feed"] });
        if (d.viewer?.pending_review) toast("info", "Waiting for review", "Your post matched one of our safety checks, so a moderator will look at it before it's public.");
        onClose();
        nav(d.url);
      }
    } catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <form className="np-compose" onSubmit={submit}>
      <div className="np-compose-head">
        <b>Start a discussion</b>
        <span className="np-by">Posting as Anonymous</span>
      </div>
      <input className="input np-title-in" placeholder="Ask a question or make a claim — e.g. Is NVIDIA's valuation still justified?" value={title} maxLength={160}
        onChange={(e) => setTitle(e.target.value)} required minLength={10} autoFocus aria-label="Title" />
      <textarea className="input" rows={5} maxLength={6000} placeholder="Your reasoning (optional). What do you see that others might be missing?" value={body}
        onChange={(e) => setBody(e.target.value)} aria-label="Details" />
      <div className="np-compose-grid">
        <div className="np-field"><span className="np-label">Asset <span className="muted">· optional</span></span>
          {asset ? <span className="np-picked"><span className="mono">{asset.symbol}</span> {asset.name && <span className="muted">{asset.name}</span>}<button type="button" className="link-btn xs" onClick={() => setAsset(null)}>Remove</button></span>
            : <SymbolSearch compact placeholder="NVDA, EMAAR.AE, Bitcoin…" onPick={(s) => setAsset(s)} />}
        </div>
        <div className="np-field"><span className="np-label">Your framing</span>
          <div className="np-seg" role="radiogroup" aria-label="Your framing">
            {["question", "bullish", "bearish", "neutral"].map((s) => (
              <button key={s} type="button" role="radio" aria-checked={stance === s} className={`${stance === s ? "on" : ""} ${s}`} onClick={() => setStance(s)}>{STANCE[s]}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="np-field"><span className="np-label">Topics <span className="muted">· up to 3, detected automatically if you skip this</span></span>
        <div className="np-topic-pick">{primary.map((t) => (
          <button key={t.key} type="button" className={`np-topic ${topics.includes(t.key) ? "on" : ""}`} aria-pressed={topics.includes(t.key)} onClick={() => toggle(t.key)}>{t.label}</button>
        ))}</div>
      </div>
      {err && <div className="banner error small">{err}</div>}
      <div className="np-compose-foot">
        <span className="xs muted">Your name and account are never shown. Follow the <Link to="/community-guidelines" target="_blank">Community Guidelines</Link> — no promises of returns, inside information or coordinated trading.</span>
        <span className="grow" />
        <button type="button" className="btn sm ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary sm" disabled={busy || title.trim().length < 10}>{busy ? "…" : "Post anonymously"}</button>
      </div>
    </form>
  );
}

export function SectionHead({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="np-sec-h"><h2>{title}</h2>{children}</div>;
}
