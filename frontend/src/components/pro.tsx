import { askConfirm } from "./dialog";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "./market";
import { toast } from "./toast";
import { api, ApiError, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

/* Plans in the browser are presentation only. Tiers, prices, limits and capabilities come from GET /api/billing/plans,
   which is built from backend/app/core/plans.py; what a member may do is decided by the server. No number is typed here. */

const EVENT = "nexis:upgrade";
export type PlanKey = "plus_monthly" | "pro_monthly";
const PLAN_ERRORS = ["usage_limit", "sign_in_required", "already_pro", "upgrade_required", "investment_selection_required", "payment_failed"];

export const usePlans = () => useQuery({ queryKey: ["billing-plans"], queryFn: () => api.get<AnyObj>("/billing/plans"), staleTime: 600_000 });
export const useBilling = (enabled = true) => useQuery({ queryKey: ["billing-status"], queryFn: () => api.get<AnyObj>("/billing/status"), enabled });

export const money = (amount: number, currency = "AED") =>
  `${currency} ${new Intl.NumberFormat("en-US", { minimumFractionDigits: amount % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(amount)}`;
export const priceText = (p: AnyObj | undefined) => (p ? `${money(p.amount, p.currency)}/${p.interval}` : "");
export const tierOf = (plans: AnyObj | undefined, id: string) => (plans?.tiers as AnyObj[] | undefined)?.find((t) => t.id === id);

/** Turn a plan-related API error into the right explanation. Returns true when handled. */
export function handlePlanError(e: unknown): boolean {
  if (e instanceof ApiError && PLAN_ERRORS.includes(e.code)) {
    window.dispatchEvent(new CustomEvent(EVENT, { detail: { code: e.code, message: e.message, ...((e.details as AnyObj) ?? {}) } }));
    return true;
  }
  return false;
}

/** For raw fetch calls (the streaming Advisor). */
export function handlePlanErrorBody(status: number, body: AnyObj | null): boolean {
  const err = body?.error;
  return !!err && handlePlanError(new ApiError(status, err.code, err.message, err.details));
}

export async function startCheckout(plan: PlanKey): Promise<void> {
  try {
    const r = await api.post<{ url: string }>("/billing/checkout", { plan });
    window.location.assign(r.url);  // Stripe Checkout collects the payment; nothing is granted until Stripe confirms it
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { window.location.assign(`/login?mode=signup&next=${encodeURIComponent("/pricing")}`); return; }
    if (!handlePlanError(e)) toast("error", "Couldn't start checkout", errorMessage(e));
  }
}

export async function openPortal() {
  try { window.location.assign((await api.post<{ url: string }>("/billing/portal")).url); }
  catch (e) { toast("error", "Couldn't open billing", errorMessage(e)); }
}

function Shell({ onClose, children, label = "pro-title" }: { onClose: () => void; children: React.ReactNode; label?: string }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="np-modal pro-modal" role="dialog" aria-modal="true" aria-labelledby={label}>{children}</div>
    </div>
  );
}

/** Pick which investments stay active (never chosen for the member). */
export function KeepPicker({ symbols, limit, value, onChange }: { symbols: string[]; limit: number; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="keep-picker">
      <legend className="small">Choose up to {limit} to keep active <span className="muted">({value.length} selected)</span></legend>
      <div className="keep-grid">
        {symbols.map((s) => {
          const on = value.includes(s);
          return (
            <label key={s} className={`keep-chip ${on ? "on" : ""}`}>
              <input type="checkbox" checked={on} disabled={!on && value.length >= limit}
                onChange={() => onChange(on ? value.filter((x) => x !== s) : [...value, s])} />
              <span className="mono">{s}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

/** Mounted once in the app shell. Opens only when the server reported a limit, a sign-in need, or a plan problem. */
export function UpgradeDialog() {
  const [d, setD] = useState<AnyObj | null>(null);
  const [busy, setBusy] = useState(false);
  const plans = usePlans();
  const me = useMe().data?.user;
  const nav = useNavigate();
  useEffect(() => {
    const h = (e: Event) => setD((e as CustomEvent).detail);
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
  if (!d) return null;
  const close = () => setD(null);
  if (d.code === "already_pro") {
    return <Shell onClose={close}>
      <h2 id="pro-title">You already have a paid plan</h2>
      <p>{d.message}</p>
      <div className="np-modal-foot">
        <button type="button" className="btn sm ghost" onClick={close}>Close</button>
        <Link className="btn primary sm" to="/settings#plan" onClick={close}>Open Billing settings</Link>
      </div>
    </Shell>;
  }
  if (d.code === "payment_failed") {
    return <Shell onClose={close}>
      <h2 id="pro-title">Payment didn't go through</h2>
      <p>{d.message}</p>
      <div className="np-modal-foot">
        <button type="button" className="btn sm ghost" onClick={close}>Close</button>
        <button type="button" className="btn primary sm" onClick={() => { close(); void openPortal(); }}>Update payment method</button>
      </div>
    </Shell>;
  }
  if (d.code === "investment_selection_required") {
    return <Shell onClose={close}>
      <h2 id="pro-title">Choose which investments to keep active</h2>
      <p>{d.message}</p>
      <div className="np-modal-foot">
        <button type="button" className="btn sm ghost" onClick={close}>Not now</button>
        <Link className="btn primary sm" to="/my-nexis/investments#plan-allowance" onClick={close}>Choose investments</Link>
      </div>
    </Shell>;
  }
  if (d.code === "sign_in_required" || !me) {
    return <Shell onClose={close}>
      <h2 id="pro-title">{d.message}</h2>
      <p>{d.next ?? "Create a free Nexis account to continue."}</p>
      <p className="small text2">Free accounts also get My Nexis, alerts on what you track, and unlimited participation in Nexis Pulse. No payment needed.</p>
      <div className="np-modal-foot">
        <button type="button" className="btn sm ghost" onClick={close}>Not now</button>
        <button type="button" className="btn primary sm" onClick={() => { close(); nav(`/login?mode=signup&next=${encodeURIComponent(window.location.pathname)}`); }}>Create a free account</button>
      </div>
    </Shell>;
  }
  const p = plans.data;
  const target: string | null = d.code === "upgrade_required" ? d.required : d.next_plan;
  const t = target ? tierOf(p, target) : null;
  const resets = d.resets_at ? new Date(d.resets_at).toLocaleDateString(undefined, { day: "numeric", month: "long" }) : null;
  const paid = d.plan && d.plan !== "free";
  return <Shell onClose={close}>
    <div className="pro-eyebrow">{d.code === "upgrade_required" ? "Included with a paid plan" : `${d.plan === "free" ? "Free" : d.plan === "plus" ? "Nexis Plus" : "Nexis Pro"} plan limit`}</div>
    <h2 id="pro-title">{d.message}</h2>
    {d.upgrade_text && <p><b>{d.upgrade_text}</b> {d.summary ?? d.value}</p>}
    {!target && <p>{d.monthly ? `Your allowance resets on ${resets}.` : "Archive or remove one to make room."}</p>}
    {target && d.monthly && resets && <p className="xs muted">Or wait: your allowance resets on {resets}.</p>}
    {target && t && (p?.available ? <p className="pro-price">{t.name} · {priceText(t.price)}</p> : <p className="np-note">Paid plans aren't available to buy yet.</p>)}
    {target && <p className="xs muted">Billed monthly by Stripe. Cancel any time and keep your plan until the end of the month you paid for.</p>}
    <div className="np-modal-foot">
      <button type="button" className="btn sm ghost" onClick={close}>Maybe later</button>
      {target && <Link to="/pricing" className="btn sm" onClick={close}>Compare plans</Link>}
      {target && t && p?.available && (paid
        ? <Link className="btn primary sm" to="/settings#plan" onClick={close}>Change plan</Link>
        : <button type="button" className="btn primary sm" disabled={busy} onClick={async () => { setBusy(true); await startCheckout(t.price.key); setBusy(false); }}>
            {busy ? "…" : `Get ${t.name}`}
          </button>)}
    </div>
  </Shell>;
}

/** A quiet usage line where a limited feature is used, with the warnings at 90% and 100%. */
export function UsageNote({ usage, plan }: { usage: AnyObj | null | undefined; plan?: string | null }) {
  if (!usage) return null;
  const period = usage.monthly ? " this month" : "";
  return (
    <span className={`pro-usage ${usage.near_limit ? "low" : ""}`}>
      {usage.used} of {usage.limit} {usage.unit} used{period}.
      {usage.near_limit && !usage.at_limit && usage.monthly && " You're close to your monthly limit."}
      {usage.at_limit && plan !== "pro" && <> <Link to="/pricing">See plans with a higher allowance</Link></>}
    </span>
  );
}

const ORDER = ["investments", "advisor", "watchlist"];
const date = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "");

function ChangePlan({ s, onClose }: { s: AnyObj; onClose: () => void }) {
  const qc = useQueryClient();
  const plans = usePlans();
  const other = s.plan === "plus" ? "pro" : "plus";
  const t = tierOf(plans.data, other);
  const cap = t?.limits?.investments ?? 0;
  const symbols: string[] = s.investments?.symbols ?? [];
  const needKeep = symbols.length > cap;
  const [keep, setKeep] = useState<string[]>(symbols.slice(0, cap));
  const [busy, setBusy] = useState(false);
  if (!t) return null;
  const upgrade = other === "pro";
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.post<AnyObj>("/billing/change-plan", { plan: t.price.key, ...(needKeep ? { keep } : {}) });
      qc.setQueryData(["billing-status"], r);
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      toast("success", `You're now on ${t.name}`);
      onClose();
    } catch (e) { if (!handlePlanError(e)) toast("error", "Couldn't change your plan", errorMessage(e)); }
    finally { setBusy(false); }
  };
  return <Shell onClose={onClose} label="cp-title">
    <h2 id="cp-title">Switch to {t.name}</h2>
    <p><b>{priceText(t.price)}</b> · {t.limits.investments} active investments · {t.limits.advisor} AI Advisor messages a month.</p>
    <p className="small">{upgrade
      ? "You'll be charged now for the difference for the rest of this month. If the payment doesn't go through, your plan stays the same."
      : "The change happens now; the unused part of this month is credited to your next invoice."}</p>
    {needKeep && <>
      <p className="small">{t.name} includes {cap} active investments and you have {symbols.length}. The others are archived — kept, read-only, and restored if you move up again.</p>
      <KeepPicker symbols={symbols} limit={cap} value={keep} onChange={setKeep} />
    </>}
    <div className="np-modal-foot">
      <button type="button" className="btn sm ghost" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary sm" disabled={busy || (needKeep && keep.length === 0)} onClick={go}>{busy ? "…" : `Switch to ${t.name}`}</button>
    </div>
  </Shell>;
}

/** Settings → Billing. */
export function BillingSection() {
  const qc = useQueryClient();
  const q = useBilling();
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState(false);
  const s = q.data;
  if (!s) return null;
  const change = async (path: "/billing/cancel" | "/billing/resume") => {
    if (path === "/billing/cancel" && !(await askConfirm({
      title: `Cancel ${s.plan_name}?`, danger: true, confirm: "Cancel subscription",
      body: `You keep ${s.plan_name} until ${date(s.renews_at)}, then move to Free (1 active investment). You'll choose which investment stays active; the others are archived, not deleted.`,
    }))) return;
    setBusy(true);
    try { qc.setQueryData(["billing-status"], await api.post<AnyObj>(path)); toast("success", path === "/billing/cancel" ? "Cancellation scheduled" : "Subscription resumed"); }
    catch (e) { toast("error", "Couldn't update your subscription", errorMessage(e)); } finally { setBusy(false); }
  };
  const up = s.investments?.upcoming;
  return (
    <section className="pro-plan" id="plan" aria-labelledby="plan-h">
      <h2 id="plan-h">Billing</h2>
      {s.payment_issue && <p className="banner error small" role="alert">{s.payment_message}</p>}
      <dl className="pro-kv">
        <dt>Current plan</dt><dd>{s.plan_name}</dd>
        {s.billing && <><dt>Billing</dt><dd>{s.billing}</dd></>}
        {s.renews_at && <><dt>Renews</dt><dd>{date(s.renews_at)}</dd></>}
        {s.access_until && <><dt>Plan ends</dt><dd>{date(s.access_until)} <span className="muted">· cancellation scheduled; it won't renew</span></dd></>}
      </dl>
      {up?.choice_needed && (
        <p className="np-note">From {date(up.ends_at)} your plan includes {up.limit} active investment{up.limit === 1 ? "" : "s"}. <Link to="/my-nexis/investments#plan-allowance">Choose which to keep</Link> — the others will be archived, not deleted.</p>
      )}
      <ul className="pro-meters">
        {ORDER.filter((k) => s.usage[k]).map((k) => {
          const u = s.usage[k];
          return (
            <li key={k} className={u.at_limit ? "full" : u.near_limit ? "near" : ""}>
              <span>{u.label}</span>
              <span className="pro-bar" aria-hidden><span style={{ transform: `scaleX(${Math.min(1, u.used / Math.max(1, u.limit))})` }} /></span>
              <span className="num">{u.used} / {u.limit}{u.monthly ? " this month" : ""}</span>
            </li>
          );
        })}
      </ul>
      <p className="xs muted">AI Advisor allowances reset on the 1st of each month. Nexis Pulse is unlimited on every plan.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {!s.paid && <Link className="btn primary sm" to="/pricing">See plans</Link>}
        {s.can_change_plan && <button type="button" className="btn primary sm" disabled={busy} onClick={() => setChanging(true)}>Change plan</button>}
        {s.can_manage && <button type="button" className="btn sm" disabled={busy} onClick={() => openPortal()}>Manage billing</button>}
        {s.can_cancel && <button type="button" className="btn sm ghost" disabled={busy} onClick={() => change("/billing/cancel")}>Cancel subscription</button>}
        {s.can_resume && <button type="button" className="btn sm ghost" disabled={busy} onClick={() => change("/billing/resume")}>Keep my subscription</button>}
      </div>
      {changing && <ChangePlan s={s} onClose={() => setChanging(false)} />}
    </section>
  );
}
