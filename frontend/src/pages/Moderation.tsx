import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../components/market";
import { ago } from "../components/pulse";
import { toast } from "../components/toast";
import { ErrorState } from "../components/ui";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

const ACTION_LABEL: Record<string, string> = {
  approve: "Approve", remove: "Remove", restore: "Restore", lock: "Lock", unlock: "Unlock", dismiss: "Dismiss reports", suspend_author: "Pause author 7 days",
};

function Item({ it }: { it: AnyObj }) {
  const qc = useQueryClient();
  const act = async (action: string) => {
    const reason = action === "remove" || action === "suspend_author" ? window.prompt("Reason (kept in the moderation log):") ?? "" : "";
    if ((action === "remove" || action === "suspend_author") && !reason) return;
    try {
      await api.post("/moderation/actions", { target_type: it.target_type, target_id: it.target_id, action, reason: reason || null, ...(action === "suspend_author" ? { days: 7 } : {}) });
      qc.invalidateQueries({ queryKey: ["mod-queue"] });
      qc.invalidateQueries({ queryKey: ["mod-log"] });
    } catch (x) { toast("error", "Couldn't apply that", errorMessage(x)); }
  };
  const c = it.content;
  const actions = it.status === "removed" ? ["restore"] : [
    ...(it.status === "held" ? ["approve"] : []), "remove", ...(it.reports.length || it.flagged ? ["dismiss"] : []),
    ...(it.target_type === "discussion" ? [it.locked ? "unlock" : "lock"] : []), "suspend_author",
  ];
  return (
    <li className={`md-item ${it.status}`}>
      <div className="md-meta">
        <span className="np-kind">{it.target_type}</span>
        <span className={`md-status ${it.status}`}>{it.status === "held" ? "Held — not public" : it.status}</span>
        <time dateTime={it.created_at}>{ago(it.created_at)}</time>
        <span>Author: Anonymous · {it.author.prior_removals} earlier removal{it.author.prior_removals === 1 ? "" : "s"}{it.author.posting_paused ? " · posting paused" : ""}</span>
      </div>
      {c.title && <b dir="auto">{c.title}</b>}
      <p className="md-body" dir="auto">{c.body || <i className="muted">(no text)</i>}</p>
      {(c.url || c.discussion?.url) && <Link className="xs" to={c.url || c.discussion.url}>{c.discussion?.title ? `In: ${c.discussion.title}` : "Open discussion"} →</Link>}
      {it.flags.length > 0 && <div className="md-flags">{it.flags.map((f: AnyObj) => <span key={f.key} className={`md-flag ${f.severity}`}>{f.label}</span>)}</div>}
      {it.reports.length > 0 && (
        <ul className="md-reports">{it.reports.map((r: AnyObj, i: number) => <li key={i}><b>{r.label}</b>{r.detail ? ` — “${r.detail}”` : ""} <span className="xs muted">{ago(r.created_at)}</span></li>)}</ul>
      )}
      <div className="md-actions">{actions.map((a) => <button key={a} type="button" className={`btn sm ${a === "remove" ? "danger" : a === "approve" ? "primary" : ""}`} onClick={() => act(a)}>{ACTION_LABEL[a]}</button>)}</div>
    </li>
  );
}

export default function Moderation() {
  const me = useMe().data?.user;
  const allowed = me && (me.role === "moderator" || me.role === "admin");
  const q = useQuery({ queryKey: ["mod-queue"], queryFn: () => api.get<AnyObj>("/moderation/queue"), enabled: !!allowed, refetchInterval: 60_000 });
  const log = useQuery({ queryKey: ["mod-log"], queryFn: () => api.get<AnyObj[]>("/moderation/log", { limit: 30 }), enabled: !!allowed });
  if (!me) return <div className="np"><p className="np-empty-line"><Link to="/login?next=/moderation">Sign in</Link> with a moderator account.</p></div>;
  if (!allowed) return <div className="np"><p className="np-empty-line">This page is for Pulse moderators.</p></div>;
  return (
    <div className="np md">
      <header className="np-head"><div className="np-eyebrow"><span>Pulse moderation</span></div><h1>Review queue</h1>
        <p className="np-lede">Flag → review → action. You see content, automatic flags and report reasons — never who wrote or reported anything. Controversial or bearish views are not violations.</p></header>
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && !q.data.items.length && <p className="np-empty-line">Nothing waiting for review.</p>}
      <ol className="md-list">{(q.data?.items ?? []).map((it: AnyObj) => <Item key={`${it.target_type}-${it.target_id}`} it={it} />)}</ol>
      <section className="np-sec"><h2 className="np-h">Recent decisions</h2>
        <ol className="md-log">{(log.data ?? []).map((a) => <li key={a.id}><span className="mono xs">{a.target_type} #{a.target_id}</span> <b>{a.action.replace("_", " ")}</b> {a.reason && <span>— {a.reason}</span>} <span className="xs muted">{a.by} · {ago(a.created_at)}</span></li>)}</ol>
      </section>
    </div>
  );
}
