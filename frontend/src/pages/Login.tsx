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

export default function Login() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const next = sp.get("next") && sp.get("next")!.startsWith("/") ? sp.get("next")! : "/finstagram";
  const providers = useQuery({ queryKey: ["auth-providers"], queryFn: () => api.get<Record<string, boolean>>("/auth/providers"), staleTime: 600_000 });
  const [mode, setMode] = useState<"signin" | "signup">(sp.get("mode") === "signup" ? "signup" : "signin");
  const [method, setMethod] = useState<"email" | "phone">("email");
  const [f, setF] = useState({ email: "", phone: "", password: "", display_name: "", code: "" });
  const [codeSent, setCodeSent] = useState(false);
  const [err, setErr] = useState<string | null>(sp.get("error"));
  const [busy, setBusy] = useState(false);
  const otp = method === "phone" && !!providers.data?.phone_otp;

  const done = async () => {
    await qc.invalidateQueries({ queryKey: ["me"] });
    qc.invalidateQueries({ queryKey: ["social"] });
    nav(next, { replace: true });
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (otp) {
        if (!codeSent) {
          await api.post("/auth/phone/start", { phone: f.phone });
          setCodeSent(true);
          return;
        }
        await api.post("/auth/phone/verify", { phone: f.phone, code: f.code, display_name: f.display_name || undefined });
      } else if (mode === "signin") {
        await api.post("/auth/login", { identifier: method === "phone" ? f.phone : f.email, password: f.password });
      } else {
        await api.post("/auth/register", {
          ...(method === "phone" ? { phone: f.phone } : { email: f.email }),
          password: f.password, display_name: f.display_name || undefined,
        });
      }
      await done();
    } catch (x) { setErr(errorMessage(x)); } finally { setBusy(false); }
  };
  const google = () => window.location.assign(`/api/auth/oauth/google/start?next=${encodeURIComponent(next)}`);
  const switchMethod = (m: "email" | "phone") => { setMethod(m); setErr(null); setCodeSent(false); };

  return (
    <div className="login">
      <aside className="login-brand">
        <div className="login-logo">NEXIS FINANCE</div>
        <h1>UAE markets, an AI advisor and a community of investors — in one place.</h1>
        <ul>
          <li>Every ADX and DFM share, UAE bonds and sukuk, plus global markets</li>
          <li>An AI advisor that researches live data before it answers</li>
          <li>Finstagram: follow stocks, news pages and people you trust</li>
          <li>Comparisons, valuations and PDF reports</li>
        </ul>
        <div className="xs" style={{ opacity: 0.7 }}>Educational research tools — not personalised financial advice.</div>
      </aside>
      <main className="login-main">
        <div className="login-card">
          <h2>{mode === "signin" ? "Welcome back" : "Create your account"}</h2>
          <p className="small text2">{mode === "signin" ? "Sign in to post, save, follow and get a feed tuned to you." : "One account for the whole site — the advisor, reports and Finstagram."}</p>
          {providers.data?.google && (
            <>
              <button className="oauth-btn" onClick={google}><GoogleIcon /> Continue with Google</button>
              <div className="login-or"><span>or</span></div>
            </>
          )}
          <div className="seg-tabs" role="tablist" style={{ marginBottom: 6 }}>
            <button type="button" role="tab" aria-selected={method === "email"} className={method === "email" ? "on" : ""} onClick={() => switchMethod("email")}>Email</button>
            <button type="button" role="tab" aria-selected={method === "phone"} className={method === "phone" ? "on" : ""} onClick={() => switchMethod("phone")}>Phone number</button>
          </div>
          <form onSubmit={submit} className="stack" style={{ gap: 10 }}>
            {(mode === "signup" || (otp && codeSent)) && (
              <label className="fld"><span>Your name{otp ? " (new accounts)" : ""}</span>
                <input className="input" autoComplete="name" value={f.display_name} maxLength={60} onChange={(e) => setF({ ...f, display_name: e.target.value })} placeholder="How others see you" /></label>
            )}
            {method === "email" ? (
              <label className="fld"><span>{mode === "signin" ? "Email or username" : "Email"}</span>
                <input className="input" type={mode === "signup" ? "email" : "text"} autoComplete={mode === "signin" ? "username" : "email"} required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoFocus /></label>
            ) : (
              <label className="fld"><span>Mobile number</span>
                <div className="phone-field">
                  <span className="phone-cc" aria-hidden>🇦🇪 +971</span>
                  <input className="input" type="tel" inputMode="tel" autoComplete="tel" required placeholder="50 123 4567" value={f.phone} disabled={otp && codeSent}
                    onChange={(e) => setF({ ...f, phone: e.target.value })} autoFocus />
                </div>
                <span className="xs muted">UAE numbers can be typed as 050 123 4567. For other countries start with + and the country code.</span>
              </label>
            )}
            {otp ? (codeSent && (
              <label className="fld"><span>Code from the SMS</span>
                <input className="input" inputMode="numeric" autoComplete="one-time-code" required maxLength={10} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} autoFocus /></label>
            )) : (
              <label className="fld"><span>Password</span>
                <input className="input" type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} required minLength={mode === "signup" ? 8 : 1} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
            )}
            {mode === "signup" && !otp && <div className="xs muted">At least 8 characters. Passwords are stored only as salted scrypt hashes.</div>}
            {err && <div className="banner error small">{err}</div>}
            <button className="btn primary block login-submit" disabled={busy}>
              {busy ? "…" : otp ? (codeSent ? "Verify and continue" : "Send me a code") : mode === "signin" ? "Sign in" : "Create account"}
            </button>
            {otp && codeSent && <button type="button" className="link-btn small" onClick={() => { setCodeSent(false); setF({ ...f, code: "" }); }}>Use a different number or resend</button>}
          </form>
          {!otp && (
            <div className="small text2" style={{ marginTop: 14, textAlign: "center" }}>
              {mode === "signin" ? <>New here? <button className="link-btn" onClick={() => { setMode("signup"); setErr(null); }}>Create an account</button></>
                : <>Already have an account? <button className="link-btn" onClick={() => { setMode("signin"); setErr(null); }}>Sign in</button></>}
            </div>
          )}
          <div className="xs muted" style={{ marginTop: 18, textAlign: "center" }}><Link to="/">Continue without an account →</Link></div>
        </div>
      </main>
    </div>
  );
}
