import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useMe } from "./market";
import { toast } from "./toast";
import { api, ApiError, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

/* Nexis Pro on the client: purely presentation. What a member may do is decided by the server
   (entitlements.py); this file only explains it when the server says a limit was reached. */

const EVENT = "nexis:upgrade";

export const usePlans = () => useQuery({ queryKey: ["billing-plans"], queryFn: () => api.get<AnyObj>("/billing/plans"), staleTime: 600_000 });

export function price(p: AnyObj | undefined): string | null {
  if (!p || p.amount == null) return null;
  const amount = new Intl.NumberFormat("en", { style: "currency", currency: p.currency || "USD", maximumFractionDigits: p.amount % 1 ? 2 : 0 }).format(p.amount);
  const every = p.interval_count > 1 ? `${p.interval_count} ${p.interval}s` : p.interval;
  return `${amount} / ${every}`;
}

/** Turn a usage-limit or sign-in error from the API into the right explanation. Returns true when handled. */
export function handlePlanError(e: unknown): boolean {
  if (e instanceof ApiError && (e.code === "usage_limit" || e.code === "sign_in_required")) {
    window.dispatchEvent(new CustomEvent(EVENT, { detail: { code: e.code, message: e.message, ...(e.details as AnyObj ?? {}) } }));
    return true;
  }
  return false;
}

/** For raw fetch calls (the streaming Advisor): same as above from an error body. */
export function handlePlanErrorBody(status: number, body: AnyObj | null): boolean {
  const err = body?.error;
  if (!err) return false;
  return handlePlanError(new ApiError(status, err.code, err.message, err.details));
}

export async function startCheckout(plan: "pro_monthly" | "pro_yearly"): Promise<void> {
  try {
    const r = await api.post<{ url: string }>("/billing/checkout", { plan });
    window.location.assign(r.url);  // Stripe Checkout: card, Apple Pay and Google Pay are handled there
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { window.location.assign(`/login?mode=signup&next=${encodeURIComponent("/pro")}`); return; }
    toast("error", "Couldn't start checkout", errorMessage(e));
  }
}

export function PlanToggle({ plans, value, onChange }: { plans: AnyObj[]; value: string; onChange: (v: "pro_monthly" | "pro_yearly") => void }) {
  if (plans.length < 2) return null;
  return (
    <div className="np-seg small pro-toggle" role="radiogroup" aria-label="Billing">
      {plans.map((p) => (
        <button key={p.key} type="button" role="radio" aria-checked={value === p.key} className={value === p.key ? "on" : ""} onClick={() => onChange(p.key)}>
          {p.key === "pro_yearly" ? "Annual" : "Monthly"}
        </button>
      ))}
    </div>
  );
}

/** Mounted once in the app shell. Opens only when the server reported a limit or a sign-in requirement. */
export function UpgradeDialog() {
  const [d, setD] = useState<AnyObj | null>(null);
  const [plan, setPlan] = useState<"pro_monthly" | "pro_yearly">("pro_monthly");
  const [busy, setBusy] = useState(false);
  const plans = usePlans();
  const me = useMe().data?.user;
  const nav = useNavigate();
  useEffect(() => {
    const h = (e: Event) => setD((e as CustomEvent).detail);
    window.addEventListener(EVENT, h);
    return () => window.removeEventListener(EVENT, h);
  }, []);
  useEffect(() => {
    if (!d) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setD(null); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [d]);
  if (!d) return null;
  const close = () => setD(null);
  if (d.code === "sign_in_required" || !me) {
    return (
      <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
        <div className="np-modal pro-modal" role="dialog" aria-modal="true" aria-labelledby="pro-title">
          <h2 id="pro-title">Keep going with a free account</h2>
          <p>{d.message}</p>
          <p className="small text2">A free account also gives you My Nexis, alerts on what you track, and anonymous posting in Pulse. No payment needed.</p>
          <div className="np-modal-foot">
            <button type="button" className="btn sm ghost" onClick={close}>Not now</button>
            <button type="button" className="btn primary sm" onClick={() => { close(); nav(`/login?mode=signup&next=${encodeURIComponent(window.location.pathname)}`); }}>Create a free account</button>
          </div>
        </div>
      </div>
    );
  }
  const available = plans.data?.configured && (plans.data?.plans ?? []).length > 0;
  const chosen = (plans.data?.plans ?? []).find((p: AnyObj) => p.key === plan) ?? plans.data?.plans?.[0];
  const isPro = d.plan === "pro";
  return (
    <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="np-modal pro-modal" role="dialog" aria-modal="true" aria-labelledby="pro-title">
        <div className="pro-eyebrow">{isPro ? "Nexis Pro" : "Free plan limit"}</div>
        <h2 id="pro-title">{d.message}</h2>
        {isPro ? (
          <p>Nexis Pro allowances are fair-use limits that keep the service fast for everyone. Your allowance renews on {new Date(d.resets_at).toLocaleDateString()}.</p>
        ) : (
          <>
            <p><b>You tried to {d.action}.</b> {d.value}</p>
            <ul className="pro-list">
              {(plans.data?.features?.pro ?? []).slice(0, 3).map((f: string) => <li key={f}>{f}</li>)}
            </ul>
            {available ? (
              <div className="pro-price-row">
                <PlanToggle plans={plans.data!.plans} value={chosen?.key ?? plan} onChange={setPlan} />
                <span className="pro-price">{price(chosen)}</span>
              </div>
            ) : <p className="np-note">Nexis Pro isn't available to buy yet. Your free allowance renews on {new Date(d.resets_at).toLocaleDateString()}.</p>}
            <p className="xs muted">Billed by Stripe. Cancel any time; you keep Pro until the end of the period you paid for. Your free allowance renews on {new Date(d.resets_at).toLocaleDateString()}.</p>
          </>
        )}
        <div className="np-modal-foot">
          <button type="button" className="btn sm ghost" onClick={close}>Maybe later</button>
          {!isPro && <Link to="/pro" className="btn sm" onClick={close}>Compare plans</Link>}
          {!isPro && available && (
            <button type="button" className="btn primary sm" disabled={busy} onClick={async () => { setBusy(true); await startCheckout(chosen.key); setBusy(false); }}>
              {busy ? "…" : "Start Nexis Pro"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** "3 of 20 AI Advisor questions used this month" — quiet, shown where the feature is used. */
export function UsageNote({ usage, plan }: { usage: AnyObj | null | undefined; plan?: string | null }) {
  if (!usage) return null;
  const low = usage.remaining <= Math.max(1, Math.ceil(usage.limit * 0.2));
  return (
    <span className={`pro-usage ${low ? "low" : ""}`}>
      {usage.used} of {usage.limit} {usage.label} used {plan === "pro" ? "this billing period" : "this month"}
      {plan !== "pro" && low && <> · <Link to="/pro">More with Nexis Pro</Link></>}
    </span>
  );
}

/** The plan block in Settings. */
export function PlanSection() {
  const q = useQuery({ queryKey: ["billing-status"], queryFn: () => api.get<AnyObj>("/billing/status") });
  const [busy, setBusy] = useState(false);
  const s = q.data;
  if (!s) return null;
  const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "");
  const manage = async () => {
    setBusy(true);
    try { window.location.assign((await api.post<{ url: string }>("/billing/portal")).url); }
    catch (e) { toast("error", "Couldn't open billing", errorMessage(e)); setBusy(false); }
  };
  return (
    <section className="pro-plan" id="plan" aria-labelledby="plan-h">
      <h2 id="plan-h">Plan</h2>
      <dl className="pro-kv">
        <dt>Plan</dt><dd>{s.pro ? "Nexis Pro" : "Free"}{s.pro && s.interval && <span className="muted"> · billed {s.interval === "year" ? "annually" : "monthly"}</span>}</dd>
        {s.pro && <><dt>Status</dt><dd>{s.payment_issue ? "Payment issue" : s.status === "trialing" ? "Trial" : "Active"}</dd></>}
        {s.renews_at && <><dt>Renews</dt><dd>{date(s.renews_at)}</dd></>}
        {s.access_until && <><dt>Access until</dt><dd>{date(s.access_until)} <span className="muted">· your subscription won't renew</span></dd></>}
      </dl>
      {s.payment_issue && <p className="banner error small">We couldn't process your latest payment. Update your payment method to keep Nexis Pro.</p>}
      <ul className="pro-meters">
        {Object.entries(s.usage as Record<string, AnyObj>).map(([k, u]) => (
          <li key={k}>
            <span>{u.label}</span>
            <span className="pro-bar" aria-hidden><span style={{ transform: `scaleX(${Math.min(1, u.used / Math.max(1, u.limit))})` }} /></span>
            <span className="num">{u.used} / {u.limit}</span>
          </li>
        ))}
      </ul>
      <p className="xs muted">Allowances renew {s.pro ? "each billing period" : "on the 1st of each month"}.</p>
      <div className="row" style={{ gap: 8 }}>
        {s.can_manage && <button type="button" className="btn sm" disabled={busy} onClick={manage}>{s.payment_issue ? "Manage billing" : "Manage subscription"}</button>}
        {!s.pro && <Link className="btn primary sm" to="/pro">Upgrade to Nexis Pro</Link>}
      </div>
    </section>
  );
}
