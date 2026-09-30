import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, PageHead } from "../components/ui";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

interface Turn { q: string; a?: AnyObj; error?: string }

export default function Assistant() {
  const sugg = useQuery({ queryKey: ["assistant-suggestions"], queryFn: () => api.get<string[]>("/assistant/suggestions"), staleTime: Infinity });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const ask = async (question: string) => {
    const text = question.trim();
    if (text.length < 3 || busy) return;
    setQ("");
    setBusy(true);
    setTurns((t) => [...t, { q: text }]);
    try {
      const a = await api.post<AnyObj>("/assistant/ask", { question: text });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: errorMessage(e) } : x)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead title="Research Assistant" desc="Ask about your portfolios, risk, anomalies, backtests and regimes. Every answer is assembled from stored metrics and shows its evidence; anything it cannot verify, it says so." />
      <div className="banner neutral small" style={{ marginBottom: 12 }}>
        <span aria-hidden>ⓘ</span>
        <span>Deterministic retrieval over the application's own data — no generative model writes these numbers. Answers are descriptive, not investment advice.</span>
      </div>
      <Card title="Conversation" sub={turns.length ? `${turns.length} question${turns.length > 1 ? "s" : ""}` : "try a suggestion"}>
        <div className="chat">
          {turns.length === 0 && (
            <div className="row" style={{ gap: 6 }}>
              {(sugg.data ?? []).map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className="stack" style={{ gap: 8 }}>
              <div className="msg user">{t.q}</div>
              {!t.a && !t.error && <div className="msg bot"><span className="spinner" /> <span className="text2">Retrieving stored metrics…</span></div>}
              {t.error && <div className="msg bot neg">{t.error}</div>}
              {t.a && (
                <div className="msg bot">
                  <div className="row" style={{ gap: 6, marginBottom: 6 }}>
                    <span className={`badge ${t.a.grounded ? "good" : "warn"}`}>{t.a.grounded ? "✓ grounded in stored data" : "! not verifiable"}</span>
                    {t.a.intent && <span className="badge">{t.a.intent.replace("_", " ")}</span>}
                  </div>
                  <div style={{ lineHeight: 1.55 }}>{t.a.answer}</div>
                  {t.a.evidence?.length > 0 && (
                    <table className="dt" style={{ marginTop: 10 }}>
                      <thead><tr><th>Evidence</th><th className="r">Value</th><th>Source</th></tr></thead>
                      <tbody>{t.a.evidence.map((e: AnyObj, j: number) => <tr key={j}><td>{e.label}</td><td className="r num">{e.value}</td><td className="small text2">{e.source}</td></tr>)}</tbody>
                    </table>
                  )}
                  <div className="row small" style={{ marginTop: 8, gap: 10 }}>
                    {(t.a.links ?? []).map((l: AnyObj) => <Link key={l.to} to={l.to}>{l.label} →</Link>)}
                  </div>
                  {t.a.suggestions && <div className="row" style={{ gap: 6, marginTop: 8 }}>{t.a.suggestions.slice(0, 5).map((s: string) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}</div>}
                </div>
              )}
            </div>
          ))}
          <div ref={end} />
        </div>
        <form className="row" style={{ marginTop: 14 }} onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <input className="input" style={{ flex: 1, height: 38 }} placeholder="e.g. What contributed most to my portfolio's volatility?" value={q}
            onChange={(e) => setQ(e.target.value)} maxLength={500} aria-label="Question" />
          <button className="btn primary" style={{ height: 38 }} disabled={busy || q.trim().length < 3}>Ask</button>
        </form>
      </Card>
    </>
  );
}
