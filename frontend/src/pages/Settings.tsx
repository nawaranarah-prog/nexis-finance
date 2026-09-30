import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, Field, PageHead, QueryView, Seg } from "../components/ui";
import { type Theme, useWorkspace } from "../hooks/workspace";
import { api, errorMessage } from "../services/api";
import { Avatar, useMe } from "../components/market";
import { toast } from "../components/toast";
import { useT, type Lang } from "../i18n";
import type { AnyObj } from "../types/api";
import { pct } from "../utils/format";

function AccountSettings() {
  const { t, lang, setLang } = useT();
  const qc = useQueryClient();
  const nav = useNavigate();
  const me = useMe();
  const u = me.data?.user;
  const [form, setForm] = useState({ display_name: "", username: "", email: "", phone: "" });
  const [pw, setPw] = useState({ current: "", next: "" });
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState("");
  useEffect(() => {
    if (u) setForm({ display_name: u.display_name ?? "", username: u.username ?? "", email: u.email ?? "", phone: u.phone ?? "" });
  }, [u?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try { await fn(); } catch (e) { toast("error", "Couldn't save that", errorMessage(e)); } finally { setBusy(null); }
  };
  if (me.isLoading) return <Card title={t("Account")}><div className="state">…</div></Card>;
  if (!u) return (
    <Card title={t("Account")}>
      <div className="stack">
        <div className="text2 small">{t("Sign in to manage your account")} — password, email, phone number and language follow you to every device.</div>
        <div><Link className="btn primary" to="/login?next=/settings">{t("Sign in")}</Link></div>
      </div>
    </Card>
  );

  const changed = form.display_name !== (u.display_name ?? "") || form.username !== u.username || form.email !== (u.email ?? "") || form.phone !== (u.phone ?? "");
  const saveDetails = () => run("details", async () => {
    const body: AnyObj = {};
    if (form.display_name !== (u.display_name ?? "")) body.display_name = form.display_name;
    if (form.username !== u.username) body.username = form.username;
    if (form.email !== (u.email ?? "")) body.email = form.email;
    if (form.phone !== (u.phone ?? "")) body.phone = form.phone;
    await api.patch("/auth/me", body);
    await qc.invalidateQueries({ queryKey: ["me"] });
    toast("success", t("Changes saved"));
  });
  const savePassword = () => run("password", async () => {
    await api.post("/auth/me/password", { current_password: u.has_password ? pw.current : null, new_password: pw.next });
    setPw({ current: "", next: "" });
    await qc.invalidateQueries({ queryKey: ["me"] });
    toast("success", u.has_password ? "Password changed" : "Password set", "Other devices have been signed out.");
  });
  const signOutOthers = () => run("sessions", async () => {
    const r = await api.post<AnyObj>("/auth/me/logout-everywhere");
    toast("success", "Signed out of other devices", `${r.signed_out_sessions} other session${r.signed_out_sessions === 1 ? "" : "s"} ended.`);
  });
  const deleteAccount = () => run("delete", async () => {
    if (!window.confirm("Delete your account, posts, comments and saved items permanently?")) return;
    await api.post("/auth/me/delete", { password: u.has_password ? confirmDelete : "DELETE" });
    await qc.invalidateQueries({ queryKey: ["me"] });
    qc.invalidateQueries({ queryKey: ["social"] });
    toast("info", "Your account has been deleted");
    nav("/");
  });

  return (
    <>
      <Card title={t("Account")} sub={`@${u.username}`}>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (changed) saveDetails(); }}>
          <div className="row" style={{ gap: 12 }}>
            <Avatar user={u} size={44} />
            <div className="small"><b>{u.display_name || u.username}</b><div className="xs muted">{u.email || u.phone || ""}</div></div>
            <Link className="btn sm" style={{ marginInlineStart: "auto" }} to={`/finstagram/u/${u.username}`}>{t("Edit profile")}</Link>
          </div>
          <Field label={t("Display name")}>
            <input className="input" value={form.display_name} maxLength={60} autoComplete="name" onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </Field>
          <Field label={t("Username")} hint="3–30 lowercase letters, digits, dots or underscores. Your profile link changes with it.">
            <input className="input" value={form.username} maxLength={30} autoComplete="username" onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} />
          </Field>
          <Field label={t("Email")}>
            <input className="input" type="email" value={form.email} maxLength={254} autoComplete="email" dir="ltr" onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field label={t("Phone number")} hint="UAE numbers can be typed as 05X XXX XXXX.">
            <input className="input" type="tel" value={form.phone} maxLength={30} autoComplete="tel" dir="ltr" onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          </Field>
          <div><button className="btn primary" disabled={!changed || busy === "details"}>{busy === "details" ? "…" : t("Save changes")}</button></div>
        </form>
      </Card>
      <Card title={t("Preferences")}>
        <div className="stack">
          <Field label={t("Language")} hint="Also sets the language the AI advisor answers in.">
            <Seg<Lang> value={lang} onChange={(v) => setLang(v)} options={[{ value: "en", label: "English" }, { value: "ar", label: "العربية" }]} />
          </Field>
        </div>
      </Card>
      <Card title={t("Security")}>
        <form className="stack" onSubmit={(e) => { e.preventDefault(); savePassword(); }}>
          <div className="small text2">{u.has_password ? "Change the password you use to sign in." : "Your account signs in without a password. Add one to also sign in with your email or phone and a password."}</div>
          <input type="text" name="username" autoComplete="username" value={u.email || u.phone || u.username} readOnly hidden />
          {u.has_password && (
            <Field label={t("Current password")}>
              <input className="input" type="password" value={pw.current} autoComplete="current-password" onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            </Field>
          )}
          <Field label={t("New password")} hint="At least 8 characters.">
            <input className="input" type="password" value={pw.next} autoComplete="new-password" minLength={8} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
          </Field>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={pw.next.length < 8 || (u.has_password && !pw.current) || busy === "password"}>
              {busy === "password" ? "…" : u.has_password ? t("Change password") : t("Set a password")}
            </button>
            <button type="button" className="btn" disabled={busy === "sessions"} onClick={signOutOthers}>{t("Sign out of all other devices")}</button>
          </div>
        </form>
      </Card>
      <Card title={t("Danger zone")}>
        <div className="stack">
          <div className="small text2">Deleting your account removes your profile, posts, comments, likes and saved posts. This can't be undone.</div>
          {u.has_password && (
            <Field label={t("Password")}>
              <input className="input" type="password" value={confirmDelete} autoComplete="current-password" onChange={(e) => setConfirmDelete(e.target.value)} style={{ maxWidth: 280 }} />
            </Field>
          )}
          <div><button className="btn danger" disabled={(u.has_password && !confirmDelete) || busy === "delete"} onClick={deleteAccount}>{t("Delete account")}</button></div>
        </div>
      </Card>
    </>
  );
}

export default function Settings() {
  const { settings, updateSettings } = useWorkspace();
  const { t } = useT();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api.get<AnyObj>("/system/config"), staleTime: Infinity });
  return (
    <>
      <PageHead title={t("Settings")} desc="Your account, password and language, plus analysis preferences for the research tools." />
      <div className="grid g2" style={{ marginBottom: 14 }}>
        <AccountSettings />
      </div>
      <div className="grid g2">
        <Card title="Analysis defaults">
          <div className="stack">
            <Field label="Risk-free rate (annual, decimal)" metric="sharpe_ratio" hint={`Used for Sharpe/Sortino in portfolio analytics. Currently ${pct(settings.riskFreeRate)}.`}>
              <input className="input" type="number" step={0.0025} min={-0.05} max={0.25} value={settings.riskFreeRate} onChange={(e) => updateSettings({ riskFreeRate: Number(e.target.value) })} style={{ maxWidth: 160 }} />
            </Field>
            <Field label="Rolling window for portfolio charts (days)">
              <Seg value={settings.rollingWindow} onChange={(v) => updateSettings({ rollingWindow: v })} options={[20, 63, 126, 252].map((w) => ({ value: w, label: String(w) }))} />
            </Field>
            <Field label={t("Theme")}>
              <Seg<Theme> value={settings.theme} onChange={(v) => updateSettings({ theme: v })} options={[{ value: "system", label: t("System") }, { value: "light", label: t("Light") }, { value: "dark", label: t("Dark") }]} />
            </Field>
            <div>
              <button className="btn" onClick={() => { try { Object.keys(localStorage).filter((k) => k.startsWith("nexis.")).forEach((k) => localStorage.removeItem(k)); } catch { /* ignore */ } window.location.reload(); }}>Reset local preferences</button>
            </div>
          </div>
        </Card>
        <Card title="Server configuration" sub="read-only">
          <QueryView q={cfg}>
            {(c) => (
              <dl className="kv">
                <dt>Version</dt><dd>{c.version}</dd>
                <dt>Environment</dt><dd>{c.environment}</dd>
                <dt>Database engine</dt><dd>{c.database_engine}</dd>
                <dt>Public data provider</dt><dd>{c.public_provider_enabled ? "enabled (Yahoo Finance chart endpoint, unofficial)" : "disabled — synthetic/CSV only"}</dd>
                <dt>Default risk-free rate</dt><dd>{pct(c.default_risk_free_rate)}</dd>
                <dt>Trading days per year</dt><dd>{c.trading_days_per_year}</dd>
                <dt>Max CSV upload</dt><dd>{c.max_upload_mb} MB</dd>
                <dt>Background job workers</dt><dd>{c.job_workers}</dd>
              </dl>
            )}
          </QueryView>
          <div className="xs muted" style={{ marginTop: 10 }}>Change these via <code>NEXIS_*</code> environment variables (see <code>.env.example</code>) and restart the API. No credentials are stored in the application.</div>
        </Card>
      </div>
    </>
  );
}
