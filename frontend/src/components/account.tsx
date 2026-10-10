import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

/* "Join Nexis" — shown when a visitor tries something that needs a free account (the AI, taking part in Pulse or
   Finstagram, saving research, reports). Reading never triggers it. The visitor comes back to the same page after
   signing up; anything they had typed is kept and offered again (never sent automatically). The server enforces all of
   this independently: these prompts are only the friendly version of a 401. */

export type AccountReason = "ai" | "pulse" | "finstagram" | "report" | "save" | "graph" | "scenario" | "general";
const EVENT = "nexis:account-required";
const PENDING = "nexis.pending.";

const COPY: Record<AccountReason, { title: string; body: string }> = {
  ai: { title: "Create a free account to ask the AI", body: "Create a free account to ask the AI advisor and join the conversation. Reading markets and Pulse stays open to everyone." },
  pulse: { title: "Join the Nexis community", body: "Create a free account to start discussions, reply and react in Nexis Pulse. You post anonymously — your name is never shown." },
  finstagram: { title: "Join the Nexis community", body: "Create a free account to like, comment and follow news pages." },
  report: { title: "Create a free account to make reports", body: "Reports are saved privately to your account, so you need one to create and download them." },
  save: { title: "Create a free account to save research", body: "Saved investigations, scenarios and theses are private to your account." },
  graph: { title: "Create a free account to use the World Graph", body: "The World Graph is a free research tool for members: search a company, see who owns and controls it, and where each link comes from." },
  scenario: { title: "Create a free account to use the Time Machine", body: "The Time Machine is a free research tool for members: test market moves on your holdings, with the working shown, and save scenarios to run again later." },
  general: { title: "Join the Nexis community", body: "Create a free account to join discussions, save research and use the AI." },
};

/** Ask the visitor to sign up. ``pending`` keeps something they typed (by key) for after they come back. */
export function requireAccount(reason: AccountReason = "general", pending?: { key: string; value: string }) {
  if (pending?.value.trim()) {
    try { sessionStorage.setItem(PENDING + pending.key, pending.value); } catch { /* storage blocked: nothing kept */ }
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: reason }));
}

/** Text a visitor typed before signing up, returned once (then forgotten). */
export function takePending(key: string): string | null {
  try {
    const v = sessionStorage.getItem(PENDING + key);
    sessionStorage.removeItem(PENDING + key);
    return v;
  } catch { return null; }
}

/** Only same-site paths may be used as a return address (no //host or /\host tricks). */
export const safeNext = (raw: string | null | undefined, fallback = "/pulse") =>
  raw && raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\") ? raw : fallback;

export function AccountGate() {
  const [reason, setReason] = useState<AccountReason | null>(null);
  const loc = useLocation();
  const first = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    const h = (e: Event) => setReason(((e as CustomEvent).detail as AccountReason) ?? "general");
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
  useEffect(() => {
    if (!reason) return;
    first.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setReason(null); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [reason]);
  if (!reason) return null;
  const close = () => setReason(null);
  const here = encodeURIComponent(loc.pathname + loc.search + loc.hash);
  const c = COPY[reason];
  return (
    <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="np-modal account-gate" role="dialog" aria-modal="true" aria-labelledby="acct-title">
        <h2 id="acct-title">{c.title}</h2>
        <p>{c.body}</p>
        <div className="account-gate-actions">
          <Link className="btn primary" to={`/login?mode=signup&next=${here}`} onClick={close} ref={first}>Create account</Link>
          <Link className="btn" to={`/login?next=${here}`} onClick={close}>Sign in</Link>
          <button type="button" className="btn ghost" onClick={close}>Continue reading</button>
        </div>
      </div>
    </div>
  );
}
