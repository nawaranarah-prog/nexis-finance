import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHead } from "../components/ui";
import { Markdown } from "../components/markdown";
import { api, buildUrl, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

interface Turn { role: "user" | "assistant"; content: string; meta?: AnyObj; error?: string; steps?: string[]; streaming?: boolean }
const KEY = "nx-advisor-v2";
const SUGGESTIONS = [
  "What's happening with Emaar? I want to buy 500 shares.",
  "Compare Emirates NBD and Dubai Islamic Bank for a 3-year horizon",
  "Is NVIDIA expensive right now?",
  "I have AED 20,000 — how would DEWA fit a dividend portfolio?",
  "How have gold and the S&P 500 compared over 5 years?",
  "ما الأخبار عن سهم إعمار؟",
];

function load(): Turn[] {
  try { return (JSON.parse(localStorage.getItem(KEY) ?? "[]") as Turn[]).map((t) => ({ ...t, streaming: false })); } catch { return []; }
}

/** Reads a server-sent-events response body and calls ``on`` for each JSON event. */
async function readEvents(res: Response, on: (ev: AnyObj) => void) {
  const reader = res.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data: ")) { try { on(JSON.parse(line.slice(6))); } catch { /* ignore partial */ } }
    }
  }
}

export default function Advisor() {
  const [params, setParams] = useSearchParams();
  const status = useQuery({ queryKey: ["advisor-status"], queryFn: () => api.get<AnyObj>("/advisor/status"), staleTime: 300_000 });
  const [turns, setTurns] = useState<Turn[]>(load);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const started = useRef(false);
  // Typewriter: incoming text is queued and revealed a few characters per frame, so answers flow like a live chat.
  const pending = useRef("");
  const raf = useRef<number | null>(null);

  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(turns.filter((t) => !t.streaming).slice(-30))); } catch { /* storage unavailable */ }
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);
  useEffect(() => () => { if (raf.current) cancelAnimationFrame(raf.current); }, []);

  const patchLast = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));
  const pump = () => {
    if (!pending.current) { raf.current = null; return; }
    const n = Math.max(3, Math.ceil(pending.current.length / 40));
    const take = pending.current.slice(0, n);
    pending.current = pending.current.slice(n);
    patchLast((t) => ({ ...t, content: t.content + take }));
    raf.current = requestAnimationFrame(pump);
  };
  const enqueue = (text: string) => {
    pending.current += text;
    if (!raf.current) raf.current = requestAnimationFrame(pump);
  };
  const drain = () => new Promise<void>((resolve) => {
    const check = () => (pending.current ? requestAnimationFrame(check) : resolve());
    check();
  });

  const send = async (text: string) => {
    const content = text.trim();
    if (content.length < 2 || busy) return;
    const history = [...turns.filter((t) => !t.error && t.content), { role: "user" as const, content }];
    setTurns([...turns, { role: "user", content }, { role: "assistant", content: "", steps: [], streaming: true }]);
    setQ("");
    setBusy(true);
    const messages = history.slice(-16).map(({ role, content: c }) => ({ role, content: c.slice(0, 4000) }));
    try {
      const res = await fetch(buildUrl("/advisor/chat/stream"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages }) });
      if (!res.ok || !res.body) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.error?.message ?? `Request failed (HTTP ${res.status})`);
      }
      let meta: AnyObj | undefined;
      await readEvents(res, (ev) => {
        if (ev.type === "status") patchLast((t) => ({ ...t, steps: [...(t.steps ?? []), ev.text] }));
        else if (ev.type === "delta") enqueue(ev.text);
        else if (ev.type === "done") meta = ev.meta;
        else if (ev.type === "error") throw new Error(ev.message);
      });
      await drain();
      patchLast((t) => ({ ...t, meta, streaming: false }));
    } catch (e) {
      pending.current = "";
      patchLast((t) => ({ ...t, streaming: false, error: t.content ? undefined : errorMessage(e), content: t.content }));
    } finally {
      setBusy(false);
      status.refetch();
    }
  };

  useEffect(() => {
    const pre = params.get("q");
    if (pre && !started.current) {
      started.current = true;
      setParams({}, { replace: true });
      void send(pre);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const s = status.data;
  return (
    <>
      <PageHead title="AI Financial Advisor" desc="Talk to it like you would to a private banker: it researches live prices, fundamentals, analyst ratings and today's news before answering, then gives you a clear view and the numbers behind it."
        actions={turns.length > 0 && <button className="btn" onClick={() => { setTurns([]); pending.current = ""; }}>New chat</button>} />
      {s && !s.available && (
        <div className="banner neutral small" style={{ marginBottom: 12 }}>
          <span aria-hidden>ⓘ</span>
          <span>{s.configured ? "The AI model is connected but not active yet" : "No AI model is connected"} — until it is, answers are written from live data by the built-in analyst engine. {s.disclaimer}</span>
        </div>
      )}
      <div className="chat-shell">
        <div className="chat-log">
          {turns.length === 0 && (
            <div className="chat-empty">
              <div className="chat-empty-title">{s?.available ? "✦ " : ""}What would you like to know?</div>
              <div className="chat-sugg">{SUGGESTIONS.map((x) => <button key={x} className="chip" dir="auto" onClick={() => send(x)}>{x}</button>)}</div>
            </div>
          )}
          {turns.map((t, i) => t.role === "user" ? (
            <div key={i} className="msg user" dir="auto">{t.content}</div>
          ) : (
            <div key={i} className="msg bot">
              {t.error ? <div className="neg">{t.error}</div> : (
                <>
                  {(t.steps?.length ?? 0) > 0 && (
                    <div className="advisor-steps">
                      {t.steps!.map((st, j) => (t.streaming && j === t.steps!.length - 1 && !t.content
                        ? <div key={j} className="advisor-status"><span className="dot" />{st}…</div>
                        : <div key={j} className="step">{st}</div>))}
                    </div>
                  )}
                  {t.streaming && !t.content && !(t.steps?.length) && <div className="advisor-status"><span className="dot" />Thinking…</div>}
                  {t.content && <div dir="auto"><Markdown text={t.content} />{t.streaming && <span className="caret" aria-hidden />}</div>}
                  {!t.streaming && (t.meta?.symbols ?? []).length > 0 && (
                    <div className="row small" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                      {t.meta!.symbols.map((sym: string) => (
                        <span key={sym} className="row" style={{ gap: 6 }}>
                          <Link className="chip" to={`/markets/${encodeURIComponent(sym)}`}>{sym} chart & details</Link>
                          <Link className="chip" to={`/compare?s=${encodeURIComponent(sym)}`}>compare</Link>
                          <Link className="chip" to={`/finstagram/s/${encodeURIComponent(sym)}`}>Finstagram</Link>
                        </span>
                      ))}
                    </div>
                  )}
                  {!t.streaming && t.meta?.disclaimer && <div className="xs muted" style={{ marginTop: 8 }}>{t.meta.mode === "ai" ? "✦ AI analysis · " : ""}{t.meta.disclaimer}</div>}
                </>
              )}
            </div>
          ))}
          <div ref={end} />
        </div>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void send(q); }}>
          <textarea className="input" rows={1} dir="auto" value={q} maxLength={2000} aria-label="Message"
            placeholder="Ask anything — e.g. What's up with Emaar? I want to buy 500 shares"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(q); } }} />
          <button className="btn primary" disabled={busy || q.trim().length < 2}>{busy ? "…" : "Send"}</button>
        </form>
      </div>
    </>
  );
}
