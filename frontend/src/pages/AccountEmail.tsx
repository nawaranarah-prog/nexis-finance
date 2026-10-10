import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

/* Password reset and email verification. The server decides everything (signed, expiring, single-use links); these
   pages only collect input and report the server's answer. When email isn't set up, they say so instead of
   pretending a message was sent. */

function Frame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="np account-page">
      <header className="np-head"><div className="np-eyebrow"><span>Account</span></div><h1>{title}</h1></header>
      <div className="account-card">{children}</div>
    </div>
  );
}

export function ForgotPassword() {
  const providers = useQuery({ queryKey: ["auth-providers"], queryFn: () => api.get<AnyObj>("/auth/providers"), staleTime: 600_000 });
  const [mail, setMail] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const unavailable = providers.data && providers.data.email_delivery === false;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { setDone((await api.post<AnyObj>("/auth/password/forgot", { email: mail })).message); }
    catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <Frame title="Reset your password">
      {unavailable ? (
        <p className="np-note" role="status">Password reset by email isn't available yet. If you can't sign in, contact support from the email address on your account.</p>
      ) : done ? (
        <p className="np-note" role="status">{done} The link works for one hour.</p>
      ) : (
        <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
          <label className="fld"><span>Email address on your account</span>
            <input className="input" type="email" autoComplete="email" required value={mail} onChange={(e) => setMail(e.target.value)} /></label>
          {err && <div className="banner error small" role="alert">{err}</div>}
          <button className="btn primary" disabled={busy || !mail.trim()}>{busy ? "…" : "Email me a reset link"}</button>
        </form>
      )}
      <p className="small"><Link className="touch-link" to="/login">Back to sign in</Link></p>
    </Frame>
  );
}

export function ResetPassword() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const token = sp.get("token") ?? "";
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await api.post("/auth/password/reset", { token, password: pw }); nav("/login?reset=1", { replace: true }); }
    catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  return (
    <Frame title="Choose a new password">
      {!token ? <p className="np-note" role="alert">This link is incomplete. Request a new one from <Link to="/forgot-password">Reset your password</Link>.</p> : (
        <form className="stack" style={{ gap: 10 }} onSubmit={submit}>
          <label className="fld"><span>New password</span>
            <input className="input" type="password" autoComplete="new-password" required minLength={8} value={pw} onChange={(e) => setPw(e.target.value)} /></label>
          <span className="xs muted">At least 8 characters. Every device will need to sign in again.</span>
          {err && <div className="banner error small" role="alert">{err} {/expired|invalid/i.test(err) && <Link to="/forgot-password">Request a new link</Link>}</div>}
          <button className="btn primary" disabled={busy || pw.length < 8}>{busy ? "…" : "Save new password"}</button>
        </form>
      )}
    </Frame>
  );
}

export function VerifyEmail() {
  const [sp] = useSearchParams();
  const qc = useQueryClient();
  const token = sp.get("token") ?? "";
  const [state, setState] = useState<"checking" | "ok" | "failed">(token ? "checking" : "failed");
  const [err, setErr] = useState<string | null>(token ? null : "This link is incomplete.");
  const ran = useRef(false);
  useEffect(() => {
    if (!token || ran.current) return;
    ran.current = true;
    api.post("/auth/email/verify", { token })
      .then(() => { setState("ok"); qc.invalidateQueries({ queryKey: ["me"] }); })
      .catch((x) => { setState("failed"); setErr(errorMessage(x)); });
  }, [token, qc]);
  return (
    <Frame title="Confirm your email">
      <p className={state === "failed" ? "np-note" : ""} role="status">
        {state === "checking" ? "Checking your link…" : state === "ok" ? "Your email address is confirmed." : `${err} You can send a new link from Settings.`}
      </p>
      <p className="small"><Link className="touch-link" to={state === "ok" ? "/my-nexis" : "/settings"}>{state === "ok" ? "Go to My Nexis" : "Open Settings"}</Link></p>
    </Frame>
  );
}
