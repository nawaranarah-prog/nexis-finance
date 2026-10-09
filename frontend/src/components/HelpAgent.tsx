import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Markdown } from "./markdown";
import { api, ApiError, buildUrl } from "../services/api";
import { contextFor, isKnownRoute, searchArticles, suggestionsFor, type HelpArticle, type HelpLink } from "../help/search";

/* Nexis Help: a bottom-right launcher that opens a compact help panel (a full-screen sheet on phones).
   Articles are searchable without AI. Questions go to POST /api/help/ask, which answers only from those articles and
   the member's own plan facts. Nothing is stored: the conversation lives in this component until the page reloads. */

type Msg = { id: number; role: "user" | "assistant"; text: string; sources?: { id: string; title: string }[]; links?: HelpLink[] };
type View = { kind: "home" } | { kind: "article"; id: string; from: "home" | "chat" } | { kind: "chat" };
type Failure = { message: string; question: string; retry: boolean; code?: string };

const MAX = 800;
const TIMEOUT_MS = 30_000;
const MOBILE = "(max-width: 640px)";

function useArticles(enabled: boolean) {
  return useQuery({ queryKey: ["help-articles"], queryFn: () => api.get<{ articles: HelpArticle[] }>("/help/articles"), enabled, staleTime: 3_600_000 });
}

function useIsMobile() {
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia(MOBILE).matches);
  useEffect(() => {
    const mq = window.matchMedia(MOBILE);
    const h = () => setM(mq.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);
  return m;
}

async function ask(question: string, context: string, history: Msg[], signal: AbortSignal) {
  const res = await fetch(buildUrl("/help/ask"), {
    method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, context, history: history.slice(-6).map((m) => ({ role: m.role, content: m.text.slice(0, 4000) })) }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? "Help isn't reachable right now.", err?.details);
  }
  if (!data || typeof data.answer !== "string") throw new ApiError(502, "bad_response", "Help returned an unexpected response.");
  return data as { answer: string; sources: { id: string; title: string }[]; links: HelpLink[] };
}

const Icon = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden focusable="false"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
const HELP_ICON = "M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16ZM7.7 7.6a2.4 2.4 0 0 1 4.6 1c0 1.6-2.3 2-2.3 3.4M10 14.6v.1";
const CLOSE_ICON = "M5 5l10 10M15 5L5 15";
const BACK_ICON = "M12.5 4.5 7 10l5.5 5.5";
const SEND_ICON = "M3.5 10h12M11 5.5 15.5 10 11 14.5";

export default function HelpAgent() {
  const loc = useLocation();
  const nav = useNavigate();
  const mobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>({ kind: "home" });
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const abort = useRef<AbortController | null>(null);
  const nextId = useRef(1);
  const launcher = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const titleId = useId();
  const articles = useArticles(open);
  const list = useMemo(() => articles.data?.articles ?? [], [articles.data]);
  const context = contextFor(loc.pathname, loc.hash);
  const suggestions = useMemo(() => suggestionsFor(context, list), [context, list]);
  const results = useMemo(() => (query.trim() ? searchArticles(list, query, context) : []), [list, query, context]);
  const raised = loc.pathname.startsWith("/advisor") || loc.pathname.startsWith("/assistant");  // keep clear of the chat composer

  const close = useCallback(() => {
    abort.current?.abort();
    setOpen(false);
    window.setTimeout(() => launcher.current?.focus(), 0);
  }, []);

  // Escape closes; on phones the sheet is modal: lock the page behind it and keep focus inside.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) { e.stopPropagation(); close(); return; }
      if (e.key === "Tab" && mobile && panel.current) {
        const f = panel.current.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled])");
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    let undo = () => {};
    if (mobile) {
      const prev = document.documentElement.style.overflow;
      document.documentElement.style.overflow = "hidden";
      undo = () => { document.documentElement.style.overflow = prev; };
    }
    return () => { document.removeEventListener("keydown", onKey); undo(); };
  }, [open, mobile, close]);

  // Phones: size the sheet to the visible viewport so the composer stays above the on-screen keyboard.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!open || !mobile || !vv || !panel.current) return;
    const el = panel.current;
    const fit = () => { el.style.height = `${vv.height}px`; el.style.top = `${vv.offsetTop}px`; };
    fit();
    vv.addEventListener("resize", fit);
    vv.addEventListener("scroll", fit);
    return () => { vv.removeEventListener("resize", fit); vv.removeEventListener("scroll", fit); el.style.height = ""; el.style.top = ""; };
  }, [open, mobile]);

  useEffect(() => { if (open && view.kind === "home") searchBox.current?.focus(); }, [open, view.kind]);

  // Follow new messages only if the reader is already at the bottom (don't yank them away from older answers).
  useEffect(() => {
    const el = log.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs, pending, failure]);

  useEffect(() => () => abort.current?.abort(), []);

  const send = async (raw: string, isRetry = false) => {
    const question = raw.trim();
    if (!question || pending || question.length > MAX) return;
    const history = isRetry && msgs.at(-1)?.role === "user" ? msgs.slice(0, -1) : msgs;  // the retried question is sent once
    if (!isRetry) setMsgs((m) => [...m, { id: nextId.current++, role: "user", text: question }]);
    setFailure(null);
    setPending(true);
    setView({ kind: "chat" });
    if (!isRetry) setDraft("");
    stick.current = true;
    const ctl = new AbortController();
    abort.current = ctl;
    const timer = window.setTimeout(() => ctl.abort("timeout"), TIMEOUT_MS);
    try {
      const r = await ask(question, context, history, ctl.signal);
      setMsgs((m) => [...m, { id: nextId.current++, role: "assistant", text: r.answer, sources: r.sources, links: (r.links ?? []).filter((l) => isKnownRoute(l.path)) }]);
    } catch (e) {
      if (ctl.signal.aborted && ctl.signal.reason !== "timeout") {
        setFailure({ message: "Stopped before the answer arrived.", question, retry: true });
      } else if (ctl.signal.aborted) {
        setFailure({ message: "That took too long. Please try again.", question, retry: true });
      } else if (e instanceof ApiError) {
        const retry = !["help_limit", "help_ai_unavailable", "validation_failed"].includes(e.code) || e.message.includes("try again");
        setFailure({ message: e.message, question, retry, code: e.code });
      } else {
        setFailure({ message: "You seem to be offline. Check your connection and try again.", question, retry: true });
      }
    } finally {
      window.clearTimeout(timer);
      if (abort.current === ctl) abort.current = null;
      setPending(false);
    }
  };

  const newConversation = () => {
    abort.current?.abort();
    setMsgs([]);
    setFailure(null);
    setPending(false);
    setDraft("");
    setView({ kind: "home" });
  };

  const go = (link: HelpLink) => {
    if (!isKnownRoute(link.path)) return;
    nav(link.path);
    if (mobile) close();
  };

  if (!open) {
    return (
      <button ref={launcher} type="button" className={`help-launcher ${raised ? "raised" : ""}`} aria-label="Help and support" title="Help and support"
        aria-haspopup="dialog" aria-expanded={false} onClick={() => setOpen(true)}>
        <Icon d={HELP_ICON} size={22} />
      </button>
    );
  }

  const article = view.kind === "article" ? list.find((a) => a.id === view.id) : undefined;
  const composer = (
    <form className="help-composer" onSubmit={(e) => { e.preventDefault(); void send(draft); }}>
      <label htmlFor="help-q" className="sr-only">Ask a question about Nexis</label>
      <textarea id="help-q" rows={1} maxLength={MAX} value={draft} placeholder="Ask a question about using Nexis…" disabled={pending}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(draft); } }} />
      <button type="submit" className="help-send" aria-label="Send question" disabled={pending || !draft.trim()}><Icon d={SEND_ICON} /></button>
      {draft.length > MAX - 100 && <span className="help-count" aria-live="polite">{MAX - draft.length} characters left</span>}
    </form>
  );

  return (
    <div ref={panel} className={`help-panel ${raised ? "raised" : ""}`} role="dialog" aria-modal={mobile} aria-labelledby={titleId}>
      <header className="help-head">
        {view.kind !== "home" && (
          <button type="button" className="help-icon-btn" aria-label="Back" onClick={() => setView(view.kind === "article" && view.from === "chat" ? { kind: "chat" } : { kind: "home" })}>
            <Icon d={BACK_ICON} />
          </button>
        )}
        <div className="help-title">
          <h2 id={titleId}>Nexis Help</h2>
          <span>{view.kind === "chat" ? "Answers from the Nexis help articles" : "How can we help?"}</span>
        </div>
        {(msgs.length > 0 || failure) && <button type="button" className="help-text-btn" onClick={newConversation}>New conversation</button>}
        <button type="button" className="help-icon-btn" aria-label="Close help" onClick={close}><Icon d={CLOSE_ICON} /></button>
      </header>

      <div className="help-body" ref={view.kind === "chat" ? log : undefined}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; }}>
        {view.kind === "home" && (
          <>
            <p className="help-welcome">Search the help articles, or ask a question below. Answers cover how Nexis works — not investment advice.</p>
            <div className="help-search">
              <label htmlFor="help-search" className="sr-only">Search help articles</label>
              <input id="help-search" ref={searchBox} type="search" value={query} placeholder="Search help articles" autoComplete="off"
                onChange={(e) => setQuery(e.target.value)} />
            </div>
            {articles.isLoading && <div className="help-skel" aria-label="Loading help articles"><span /><span /><span /></div>}
            {articles.isError && (
              <p className="help-error" role="alert">Help articles couldn't load. <button type="button" className="help-link-btn" onClick={() => articles.refetch()}>Try again</button></p>
            )}
            {query.trim() ? (
              results.length ? (
                <ul className="help-list" aria-label="Matching articles">
                  {results.map((a) => <li key={a.id}><button type="button" onClick={() => setView({ kind: "article", id: a.id, from: "home" })}>{a.title}</button></li>)}
                </ul>
              ) : list.length > 0 && (
                <div className="help-empty" role="status">
                  <p>No articles match “{query.trim()}”.</p>
                  <button type="button" className="help-link-btn" onClick={() => { setDraft(query.trim().slice(0, MAX)); document.getElementById("help-q")?.focus(); }}>Ask it as a question instead</button>
                </div>
              )
            ) : suggestions.length > 0 && (
              <>
                <h3 className="help-h">Suggested</h3>
                <ul className="help-chips">
                  {suggestions.map((s) => <li key={s.question}><button type="button" onClick={() => setView({ kind: "article", id: s.article.id, from: "home" })}>{s.question}</button></li>)}
                </ul>
              </>
            )}
            {msgs.length > 0 && <button type="button" className="help-link-btn help-resume" onClick={() => setView({ kind: "chat" })}>Back to your conversation</button>}
          </>
        )}

        {view.kind === "article" && (article ? (
          <article className="help-article">
            <h3>{article.title}</h3>
            <Markdown text={article.body} />
            {article.links.filter((l) => isKnownRoute(l.path)).length > 0 && (
              <div className="help-actions">{article.links.filter((l) => isKnownRoute(l.path)).map((l) => <button key={l.path} type="button" className="btn sm" onClick={() => go(l)}>{l.label}</button>)}</div>
            )}
          </article>
        ) : <p className="help-error" role="alert">This article isn't available any more. <button type="button" className="help-link-btn" onClick={() => setView({ kind: "home" })}>Back to help</button></p>)}

        {view.kind === "chat" && (
          <div className="help-log" role="log" aria-live="polite" aria-relevant="additions">
            {msgs.map((m) => (
              <div key={m.id} className={`help-msg ${m.role}`}>
                <span className="sr-only">{m.role === "user" ? "You:" : "Nexis Help:"}</span>
                {m.role === "assistant" ? <Markdown text={m.text} /> : <p>{m.text}</p>}
                {m.role === "assistant" && (m.sources?.length || m.links?.length) ? (
                  <div className="help-refs">
                    {m.links?.map((l) => <button key={l.path} type="button" className="btn sm" onClick={() => go(l)}>{l.label}</button>)}
                    {m.sources?.map((s) => <button key={s.id} type="button" className="help-link-btn" onClick={() => setView({ kind: "article", id: s.id, from: "chat" })}>{s.title}</button>)}
                  </div>
                ) : null}
              </div>
            ))}
            {pending && <div className="help-msg assistant pending" role="status"><span className="help-dots" aria-hidden><i /><i /><i /></span> Looking that up…</div>}
            {failure && (
              <div className="help-fail" role="alert">
                <p><b>Couldn't answer.</b> {failure.message}</p>
                <div className="help-actions">
                  {failure.retry && <button type="button" className="btn sm" onClick={() => void send(failure.question, true)}>Try again</button>}
                  <button type="button" className="help-link-btn" onClick={() => { setQuery(failure.question.slice(0, 120)); setView({ kind: "home" }); }}>Search the help articles</button>
                  {failure.code === "help_limit" && <span className="xs muted">The help articles are always available.</span>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {view.kind !== "article" && composer}
      <footer className="help-foot">Nexis Help · Answers are AI-generated from Nexis help articles and can be wrong. It can't change your account.</footer>
    </div>
  );
}
