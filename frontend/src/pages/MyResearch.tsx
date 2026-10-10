import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { requireAccount } from "../components/account";
import { useMe } from "../components/market";
import { ErrorState, Loading, PageHead } from "../components/ui";
import { api } from "../services/api";

/* The research hub: what the three tools are for, and everything the member has saved in them. */

interface Saved { id: number; kind: "graph" | "scenario" | "thesis" | "report"; title: string; status: string; updated_at: string; entity: { id: number; name: string } | null }

const TOOLS = [
  { to: "/world-graph", name: "World Graph", what: "Who owns, controls and lists a company — every link with its source and how well it's referenced." },
  { to: "/time-machine", name: "Time Machine", what: "What a market move would do to your holdings, using measured sensitivities with their uncertainty. A what-if, never a forecast." },
  { to: "/devils-advocate", name: "Devil's Advocate", what: "The strongest evidence-based case against your investment thesis, with every quote checked." },
];
const KIND: Record<Saved["kind"], string> = { graph: "World Graph investigations", scenario: "Time Machine scenarios", thesis: "Devil's Advocate theses", report: "Reports" };

function href(s: Saved): string {
  if (s.kind === "graph") return s.entity ? `/world-graph/${s.entity.id}?saved=${s.id}` : "/world-graph";
  if (s.kind === "scenario") return `/time-machine/${s.id}`;
  if (s.kind === "thesis") return `/devils-advocate/${s.id}`;
  return "/reports";
}

export default function MyResearch() {
  const me = useMe();
  const signedIn = !!me.data?.user;
  const saved = useQuery({ queryKey: ["saved-research", "all"], queryFn: () => api.get<Saved[]>("/saved-research"), enabled: signedIn });
  const groups = (["graph", "scenario", "thesis"] as const).map((k) => ({ kind: k, items: (saved.data ?? []).filter((s) => s.kind === k) }));
  return (
    <div className="da">
      <PageHead title="My research" desc="Three tools that show their working: sourced relationships, deterministic what-ifs, and an evidence-checked challenge to your thesis. Everything you save here is private to you." />
      <div className="mr-tools">
        {TOOLS.map((t) => (
          <Link key={t.to} to={t.to} className="card mr-tool">
            <b>{t.name}</b>
            <span className="small muted">{t.what}</span>
          </Link>
        ))}
      </div>
      {me.isLoading ? <Loading /> : !signedIn ? (
        <section className="card da-card">
          <p>The research tools are free for members.</p>
          <button type="button" className="btn primary" onClick={() => requireAccount("save")}>Create a free account</button>
        </section>
      ) : saved.isLoading ? <Loading /> : saved.error ? <ErrorState error={saved.error} /> : (
        groups.map((g) => (
          <section key={g.kind} className="card da-card">
            <h2>{KIND[g.kind]}</h2>
            {g.items.length ? (
              <ul className="tm-saved">{g.items.map((s) => (
                <li key={s.id}><Link className="touch-link" to={href(s)}><b>{s.title}</b> <span className="muted small">{s.entity ? `· ${s.entity.name} ` : ""}· updated {s.updated_at.slice(0, 10)}{s.status !== "active" ? ` · ${s.status.replace("_", " ")}` : ""}</span></Link></li>
              ))}</ul>
            ) : <p className="muted small">Nothing saved yet.</p>}
          </section>
        ))
      )}
      <p className="muted xs">Educational research tools — not investment advice. PDF reports of saved runs are in <Link to="/reports">Reports</Link>.</p>
    </div>
  );
}
