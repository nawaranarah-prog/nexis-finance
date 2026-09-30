import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../services/api";

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.6-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.7-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.5z" />
    </svg>
  );
}

function AppleIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden fill="currentColor">
      <path d="M16.37 12.64c-.02-2.3 1.88-3.4 1.96-3.46-1.07-1.56-2.73-1.78-3.32-1.8-1.41-.14-2.76.83-3.47.83-.72 0-1.82-.81-2.99-.79-1.54.02-2.96.9-3.75 2.28-1.6 2.78-.41 6.89 1.15 9.14.76 1.1 1.67 2.34 2.86 2.3 1.15-.05 1.58-.74 2.97-.74 1.38 0 1.77.74 2.98.72 1.23-.02 2.01-1.12 2.76-2.23.87-1.28 1.23-2.52 1.25-2.58-.03-.01-2.39-.92-2.4-3.67zM14.1 5.9c.63-.77 1.06-1.83.94-2.9-.91.04-2.01.61-2.66 1.37-.58.67-1.09 1.76-.96 2.8 1.02.08 2.05-.52 2.68-1.27z" />
    </svg>
  );
}

export default function Login() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const next = sp.get("next") && sp.get("next")!.startsWith("/") ? sp.get("next")! : "/social";
  const providers = useQuery({ queryKey: ["auth-providers"], queryFn: () => api.get<Record<string, boolean>>("/auth/providers"), staleTime: 600_000 });
  const [mode, setMode] = useState<"signin" | "signup">(sp.get("mode") === "signup" ? "signup" : "signin");
  const [f, setF] = useState({ email: "", password: "", display_name: "" });
  const [err, setErr] = useState<string | null>(sp.get("error"));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (mode === "signin") await api.post("/auth/login", { identifier: f.email, password: f.password });
      else await api.post("/auth/register", { email: f.email, password: f.password, display_name: f.display_name || undefined });
      await qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["social"] });
      nav(next, { replace: true });
    } catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  const oauth = (p: "google" | "apple") => {
    if (providers.data && !providers.data[p]) {
      setNote(`${p === "google" ? "Google" : "Apple"} sign-in is being set up — please use your email for now.`);
      return;
    }
    window.location.assign(`/api/auth/oauth/${p}/start?next=${encodeURIComponent(next)}`);
  };

  return (
    <div className="login">
      <aside className="login-brand">
        <div className="login-logo">NEXIS FINANCE</div>
        <h1>Markets, research and a community of investors — in one place.</h1>
        <ul>
          <li>Live prices, fundamentals and news for global and UAE markets</li>
          <li>An AI advisor that researches before it answers</li>
          <li>InstaFin: follow stocks, news pages and people you trust</li>
          <li>Comparisons, valuations and PDF reports</li>
        </ul>
        <div className="xs" style={{ opacity: 0.7 }}>Educational research tools — not personalised financial advice.</div>
      </aside>
      <main className="login-main">
        <div className="login-card">
          <h2>{mode === "signin" ? "Welcome back" : "Create your account"}</h2>
          <p className="small text2">{mode === "signin" ? "Sign in to post, save, follow and get a feed tuned to you." : "One account for the whole site — the advisor, reports and InstaFin."}</p>
          {providers.data?.google && <button className="oauth-btn" onClick={() => oauth("google")}><GoogleIcon /> Continue with Google</button>}
          {providers.data?.apple && <button className="oauth-btn apple" onClick={() => oauth("apple")}><AppleIcon /> Continue with Apple</button>}
          {note && <div className="banner neutral small" style={{ marginTop: 8 }}>{note}</div>}
          {(providers.data?.google || providers.data?.apple) && <div className="login-or"><span>or with email</span></div>}
          <form onSubmit={submit} className="stack" style={{ gap: 10 }}>
            {mode === "signup" && (
              <label className="fld"><span>Your name</span>
                <input className="input" autoComplete="name" value={f.display_name} maxLength={60} onChange={(e) => setF({ ...f, display_name: e.target.value })} placeholder="How others see you" /></label>
            )}
            <label className="fld"><span>{mode === "signin" ? "Email or username" : "Email"}</span>
              <input className="input" type={mode === "signup" ? "email" : "text"} autoComplete={mode === "signin" ? "username" : "email"} required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoFocus /></label>
            <label className="fld"><span>Password</span>
              <input className="input" type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} required minLength={mode === "signup" ? 8 : 1} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
            {mode === "signup" && <div className="xs muted">At least 8 characters. Passwords are stored only as salted scrypt hashes.</div>}
            {err && <div className="banner error small">{err}</div>}
            <button className="btn primary block login-submit" disabled={busy}>{busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}</button>
          </form>
          <div className="small text2" style={{ marginTop: 14, textAlign: "center" }}>
            {mode === "signin" ? <>New here? <button className="link-btn" onClick={() => { setMode("signup"); setErr(null); }}>Create an account</button></>
              : <>Already have an account? <button className="link-btn" onClick={() => { setMode("signin"); setErr(null); }}>Sign in</button></>}
          </div>
          <div className="xs muted" style={{ marginTop: 18, textAlign: "center" }}><Link to="/">Continue without an account →</Link></div>
        </div>
      </main>
    </div>
  );
}
