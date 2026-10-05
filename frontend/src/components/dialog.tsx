import { Component, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";

/* In-app replacements for window.confirm / window.prompt, and the app's error boundary. */

type Ask = {
  title: string;
  body?: string;
  confirm?: string;
  cancel?: string;
  danger?: boolean;
  input?: { label: string; type?: "text" | "password"; required?: boolean; placeholder?: string };
  resolve: (v: string | boolean | null) => void;
};
const EVENT = "nexis:dialog";

function open(a: Omit<Ask, "resolve">): Promise<string | boolean | null> {
  return new Promise((resolve) => window.dispatchEvent(new CustomEvent(EVENT, { detail: { ...a, resolve } })));
}

/** Resolves true when confirmed. */
export const askConfirm = (a: { title: string; body?: string; confirm?: string; danger?: boolean }) => open(a).then((v) => v === true);

/** Resolves the typed text, or null when cancelled. */
export const askText = (a: { title: string; body?: string; confirm?: string; danger?: boolean; label: string; type?: "text" | "password"; required?: boolean; placeholder?: string }) =>
  open({ title: a.title, body: a.body, confirm: a.confirm, danger: a.danger, input: { label: a.label, type: a.type, required: a.required ?? true, placeholder: a.placeholder } })
    .then((v) => (typeof v === "string" ? v : null));

export function DialogHost() {
  const [a, setA] = useState<Ask | null>(null);
  const [text, setText] = useState("");
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const h = (e: Event) => { setText(""); setA((e as CustomEvent).detail); };
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
  useEffect(() => {
    if (!a) return;
    if (!a.input) btn.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") done(null); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  });  // eslint-disable-line react-hooks/exhaustive-deps
  if (!a) return null;
  const done = (v: string | boolean | null) => { a.resolve(v); setA(null); };
  const ok = () => done(a.input ? text : true);
  return (
    <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) done(null); }}>
      <form className="np-modal" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title" onSubmit={(e) => { e.preventDefault(); if (!a.input?.required || text.trim()) ok(); }}>
        <h2 id="dlg-title">{a.title}</h2>
        {a.body && <p>{a.body}</p>}
        {a.input && (
          <label className="fld"><span className="small">{a.input.label}</span>
            <input className="input" autoFocus type={a.input.type ?? "text"} value={text} placeholder={a.input.placeholder} onChange={(e) => setText(e.target.value)} />
          </label>
        )}
        <div className="np-modal-foot">
          <button type="button" className="btn sm ghost" onClick={() => done(null)}>{a.cancel ?? "Cancel"}</button>
          <button ref={btn} type="submit" className={`btn sm ${a.danger ? "danger" : "primary"}`} disabled={!!a.input?.required && !text.trim()}>{a.confirm ?? "OK"}</button>
        </div>
      </form>
    </div>
  );
}

/** Catches rendering errors so one broken widget never blanks the whole site. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("Nexis page error", error, info.componentStack); }
  componentDidUpdate(prev: { resetKey?: string }) { if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false }); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="np np-missing" role="alert">
        <h1>Something went wrong on this page</h1>
        <p>It's been logged. Reloading usually fixes it; the rest of Nexis is unaffected.</p>
        <div className="np-head-actions"><button type="button" className="btn primary" onClick={() => window.location.reload()}>Reload</button><Link className="btn" to="/">Home</Link></div>
      </div>
    );
  }
}

export function NotFound() {
  return (
    <div className="np np-missing">
      <div className="np-eyebrow"><span>404</span></div>
      <h1>This page doesn't exist</h1>
      <p>The link may be old, or the page may have moved.</p>
      <div className="np-head-actions"><Link className="btn primary" to="/">Home</Link><Link className="btn" to="/pulse">Nexis Pulse</Link><Link className="btn" to="/markets">Markets</Link></div>
    </div>
  );
}
