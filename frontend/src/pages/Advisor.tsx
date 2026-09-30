import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PageHead } from "../components/ui";
import { Markdown } from "../components/markdown";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

interface Turn { role: "user" | "assistant"; content: string; meta?: AnyObj; error?: string }
const KEY = "nx-advisor-v1";
const SUGGESTIONS = [
  "What's happening with Emaar? I want to buy 500 shares.",
  "Compare Emirates NBD and Dubai Islamic Bank for a 3-year horizon",
  "Is NVIDIA expensive right now?",
  "I have AED 20,000 — how would DEWA fit a dividend portfolio?",
  "How have gold and the S&P 500 compared over 5 years?",
  "ما الأخبار عن سهم إعمار؟",
];
const TOOL_LABEL: Record<string, string> = {
  search_instruments: "searched instruments", get_instrument: "quote & fundamentals", get_news: "news", get_price_stats: "price history",
  compare_instruments: "comparison", run_valuation: "valuation", position_calculator: "position calculator",
};

function load(): Turn[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]") as Turn[]; } catch { return []; }
}

export default function Advisor() {
  const [params, setParams] = useSearchParams();
  const status = useQuery({ queryKey: ["advisor-status"], queryFn: () => api.get<AnyObj>("/advisor/status"), staleTime: 300_000 });
  const [turns, setTurns] = useState<Turn[]>(load);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(turns.slice(-30))); } catch { /* storage unavailable */ }
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const send = async (text: string) => {
    const content = text.trim();
    if (content.length < 2 || busy) return;
    const history = [...turns.filter((t) => !t.error), { role: "user" as const, content }];
    setTurns([...turns, { role: "user", content }]);
    setQ("");
    setBusy(true);
    try {
      const r = await api.post<AnyObj>("/advisor/chat", { messages: history.slice(-12).map(({ role, content: c }) => ({ role, content: c.slice(0, 4000) })) });
      setTurns((t) => [...t, { role: "assistant", content: r.answer, meta: r }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", content: "", error: errorMessage(e) }]);
    } finally {
      setBusy(false);
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
      <PageHead title="AI Financial Advisor" desc="Ask about any stock, fund, bond ETF, index or currency — including Dubai-listed shares. Answers are built from live prices, fundamentals, analyst consensus and today's news, with sources."
        actions={turns.length > 0 && <button className="btn" onClick={() => setTurns([])}>New conversation</button>} />
      {s && (
        <div className={`banner ${s.available ? "info" : "neutral"} small`} style={{ marginBottom: 12 }}>
          <span aria-hidden>{s.available ? "✦" : "ⓘ"}</span>
          <span>{s.available
            ? <>AI mode · {s.model} via {s.provider}. The model can only use figures it fetches with the data tools shown under each answer.</>
            : <>Data mode: {s.configured ? `the AI model is connected but unavailable right now (${s.last_error})` : "no AI model is connected on this server"}, so answers are live data briefings (prices, news, analyst consensus, position maths) without AI commentary.</>}
            {" "}{s.disclaimer}</span>
        </div>
      )}
      <div className="chat-shell">
        <div className="chat-log">
          {turns.length === 0 && (
            <div className="chat-empty">
              <div className="chat-empty-title">What would you like to know?</div>
              <div className="chat-sugg">{SUGGESTIONS.map((x) => <button key={x} className="chip" dir="auto" onClick={() => send(x)}>{x}</button>)}</div>
            </div>
          )}
          {turns.map((t, i) => t.role === "user" ? (
            <div key={i} className="msg user" dir="auto">{t.content}</div>
          ) : (
            <div key={i} className="msg bot">
              {t.error ? <div className="neg">{t.error}</div> : (
                <>
                  <div className="row xs" style={{ gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
                    <span className={`badge ${t.meta?.mode === "ai" ? "good" : ""}`}>{t.meta?.mode === "ai" ? "✦ AI analysis" : "Live data briefing"}</span>
                    {(t.meta?.tools ?? []).map((tl: AnyObj, j: number) => <span key={j} className={`badge ${tl.ok ? "" : "warn"}`} title={JSON.stringify(tl.args)}>{TOOL_LABEL[tl.tool] ?? tl.tool}{tl.args?.symbol ? ` · ${tl.args.symbol}` : ""}</span>)}
                  </div>
                  <div dir="auto"><Markdown text={t.content} /></div>
                  {t.meta?.mode === "data" && t.meta?.ai?.reason && <div className="xs muted" style={{ marginTop: 6 }}>AI commentary unavailable: {t.meta.ai.reason}</div>}
                  {(t.meta?.symbols ?? []).length > 0 && (
                    <div className="row small" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                      {t.meta!.symbols.map((sym: string) => (
                        <span key={sym} className="row" style={{ gap: 6 }}>
                          <Link className="chip" to={`/markets/${encodeURIComponent(sym)}`}>{sym} details</Link>
                          <Link className="chip" to={`/compare?s=${encodeURIComponent(sym)}`}>compare</Link>
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="xs muted" style={{ marginTop: 8 }}>{t.meta?.disclaimer}</div>
                </>
              )}
            </div>
          ))}
          {busy && <div className="msg bot"><span className="spinner" /> <span className="text2">Checking live prices, fundamentals and news…</span></div>}
          <div ref={end} />
        </div>
        <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void send(q); }}>
          <textarea className="input" rows={1} dir="auto" value={q} maxLength={2000} aria-label="Message"
            placeholder="e.g. What's up with Emaar stock? I want to buy 500 shares"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(q); } }} />
          <button className="btn primary" disabled={busy || q.trim().length < 2}>Send</button>
        </form>
      </div>
    </>
  );
}
