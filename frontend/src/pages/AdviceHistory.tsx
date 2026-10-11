import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Markdown } from "../components/markdown";
import { useMe } from "../components/market";
import { ErrorState, Loading, PageHead } from "../components/ui";
import { api } from "../services/api";

/* Every answer the AI Advisor gave the member, with the profile it was tailored to — their own record. */

interface Entry { id: number; question: string; answer: string; profile: { risk_band: string; horizon: string } | null; symbols: string[]; mode: string; model: string | null; created_at: string }

export default function AdviceHistory() {
  const me = useMe();
  const signedIn = !!me.data?.user;
  const q = useInfiniteQuery({
    queryKey: ["advice-log"],
    queryFn: ({ pageParam }) => api.get<{ items: Entry[] }>("/me/advice-log", pageParam ? { before: pageParam } : undefined),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.items.length === 30 ? last.items[last.items.length - 1].id : undefined),
    enabled: signedIn,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="da">
      <PageHead title="Your advice history" desc="Every answer the AI Advisor gave you, with the profile it was tailored to. Private to your account." actions={<Link className="btn" to="/advisor">Back to the advisor</Link>} />
      {me.isLoading || q.isLoading ? <Loading /> : !signedIn ? <p className="np-note">Sign in to see your advice history.</p> : q.error ? <ErrorState error={q.error} /> : !items.length ? (
        <p className="muted">No answers yet. <Link to="/advisor">Ask the advisor a question.</Link></p>
      ) : (
        <ol className="ah-list">
          {items.map((e) => (
            <li key={e.id} className="card da-card">
              <details>
                <summary><b>{e.question}</b> <span className="muted small">· {new Date(e.created_at).toLocaleString()}{e.profile ? ` · ${e.profile.risk_band} profile` : " · no profile"}{e.mode !== "ai" ? " · data-only answer" : ""}</span></summary>
                <div className="ah-answer"><Markdown text={e.answer} /></div>
                <p className="muted xs">{e.symbols.length ? `Instruments: ${e.symbols.join(", ")} · ` : ""}{e.model ? `Model: ${e.model}` : "Built-in data engine"}</p>
              </details>
            </li>
          ))}
        </ol>
      )}
      {q.hasNextPage && <button type="button" className="btn" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Older answers"}</button>}
    </div>
  );
}
