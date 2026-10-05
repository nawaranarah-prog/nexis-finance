import { askConfirm } from "./dialog";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "./market";
import { toast } from "./toast";
import { api, ApiError, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

/* Nexis Pro in the browser is presentation only. Prices and limits come from GET /api/billing/plans, which is
   built from backend/app/core/plans.py; what a member may do is decided by the server. No number is typed here. */

const EVENT = "nexis:upgrade";
type Plan = "pro_monthly" | "pro_yearly";

export const usePlans = () => useQuery({ queryKey: ["billing-plans"], queryFn: () => api.get<AnyObj>("/billing/plans"), staleTime: 600_000 });
export const useBilling = (enabled = true) => useQuery({ queryKey: ["billing-status"], queryFn: () => api.get<AnyObj>("/billing/status"), enabled });

export const money = (amount: number, currency = "USD") =>
  new Intl.NumberFormat("en-US", { style: "currency", currency, minimumFractionDigits: amount % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(amount);
export const priceText = (p: AnyObj | undefined) => (p ? `${money(p.amount, p.currency)}/${p.interval}` : "");
export const cents = (c: number) => money(c / 100);

/** Turn a plan-related API error into the right explanation. Returns true when handled. */
export function handlePlanError(e: unknown): boolean {
  if (e instanceof ApiError && ["usage_limit", "sign_in_required", "already_pro"].includes(e.code)) {
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

export async function startCheckout(plan: Plan): Promise<void> {
  try {
    const r = await api.post<{ url: string }>("/billing/checkout", { plan });
    window.location.assign(r.url);  // Stripe Checkout: card, Apple Pay and Google Pay are handled there
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { window.location.assign(`/login?mode=signup&next=${encodeURIComponent("/pro")}`); return; }
    if (!handlePlanError(e)) toast("error", "Couldn't start checkout", errorMessage(e));
  }
}

async function openPortal() {
  try { window.location.assign((await api.post<{ url: string }>("/billing/portal")).url); }
  catch (e) { toast("error", "Couldn't open billing", errorMessage(e)); }
}

export function PlanToggle({ value, onChange, yearlyNote }: { value: Plan; onChange: (v: Plan) => void; yearlyNote?: string }) {
  return (
    <div className="np-seg small pro-toggle" role="radiogroup" aria-label="Billing">
      <button type="button" role="radio" aria-checked={value === "pro_monthly"} className={value === "pro_monthly" ? "on" : ""} onClick={() => onChange("pro_monthly")}>Monthly</button>
      <button type="button" role="radio" aria-checked={value === "pro_yearly"} className={value === "pro_yearly" ? "on" : ""} onClick={() => onChange("pro_yearly")}>
        Yearly{yearlyNote && <span className="pro-save"> {yearlyNote}</span>}
      </button>
    </div>
  );
}

/** Mounted once in the app shell. Opens only when the server reported a limit, a sign-in need, or an existing plan. */
export function UpgradeDialog() {
  const [d, setD] = useState<AnyObj | null>(null);
  const [plan, setPlan] = useState<Plan>("pro_yearly");
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
  const shell = (body: React.ReactNode) => (
    <div className="np-modal-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="np-modal pro-modal" role="dialog" aria-modal="true" aria-labelledby="pro-title">{body}</div>
    </div>
  );
  if (d.code === "already_pro") {
    return shell(<>
      <h2 id="pro-title">You're already on Nexis Pro.</h2>
      <p>Change your plan, payment method or cancellation in Manage subscription.</p>
      <div className="np-modal-foot">
        <button type="button" className="btn sm ghost" onClick={close}>Close</button>
        <button type="button" className="btn primary sm" onClick={() => { close(); void openPortal(); }}>Manage subscription</button>
      </div>
    </>);
  }
  if (d.code === "sign_in_required" || !me) {
    return shell(<>
      <h2 id="pro-title">{d.message}</h2>
      <p>{d.next ?? "Create a free Nexis account to continue."}</p>
      <p className="small text2">Free accounts also get My Nexis, alerts on what you track, and anonymous posting in Pulse. No payment needed.</p>
      <div className="np-modal-foot">
        <button type="button" className="btn sm ghost" onClick={close}>Not now</button>
        <button type="button" className="btn primary sm" onClick={() => { close(); nav(`/login?mode=signup&next=${encodeURIComponent(window.location.pathname)}`); }}>Create a free account</button>
      </div>
    </>);
  }
  const p = plans.data;
  const isPro = d.plan === "pro";
  const resets = d.resets_at ? new Date(d.resets_at).toLocaleDateString(undefined, { day: "numeric", month: "long" }) : null;
  const yv = p?.yearly_value;
  return shell(<>
    <div className="pro-eyebrow">{isPro ? "Nexis Pro fair-use limit" : "Free plan limit"}</div>
    <h2 id="pro-title">{d.message}</h2>
    {isPro ? (
      <p>{d.monthly ? `Your allowance resets on ${resets}.` : "Remove an asset to free a slot."} Fair-use limits keep Nexis fast for everyone.</p>
    ) : (
      <>
        <p><b>{d.upgrade_text}</b> {d.value}</p>
        {d.monthly && resets && <p className="xs muted">Or wait: your free allowance resets on {resets}.</p>}
        {p?.available ? (
          <div className="pro-price-row">
            <PlanToggle value={plan} onChange={setPlan} yearlyNote={yv ? `save ${yv.savings_pct}%` : undefined} />
            <span className="pro-price">{priceText(p.prices[plan])}</span>
          </div>
        ) : <p className="np-note">Nexis Pro isn't available to buy yet.</p>}
        <p className="xs muted">Fair-use limits apply. Billed by Stripe; cancel any time and keep Pro until the end of the period you paid for.</p>
      </>
    )}
    <div className="np-modal-foot">
      <button type="button" className="btn sm ghost" onClick={close}>Maybe later</button>
      {!isPro && <Link to="/pro" className="btn sm" onClick={close}>Compare plans</Link>}
      {!isPro && p?.available && (
        <button type="button" className="btn primary sm" disabled={busy} onClick={async () => { setBusy(true); await startCheckout(plan); setBusy(false); }}>
          {busy ? "…" : "Start Nexis Pro"}
        </button>
      )}
    </div>
  </>);
}

/** A quiet usage line where a limited feature is used, with the warnings at 90% and 100%. */
export function UsageNote({ usage, plan }: { usage: AnyObj | null | undefined; plan?: string | null }) {
  const plans = usePlans();
  if (!usage) return null;
  const proLimit = plans.data?.limits?.pro?.advisor;
  const period = usage.monthly ? " this month" : "";
  return (
    <span className={`pro-usage ${usage.near_limit ? "low" : ""}`}>
      {usage.used} of {usage.limit} {usage.unit} used{period}.
      {usage.near_limit && !usage.at_limit && usage.monthly && " You're close to your monthly limit."}
      {usage.at_limit && plan !== "pro" && <> You've reached your Free {usage.label} limit. <Link to="/pro">Upgrade to Nexis Pro{proLimit && usage.label === "AI Advisor" ? ` for ${proLimit} messages/month` : ""}</Link></>}
    </span>
  );
}

const ORDER = ["advisor", "my_nexis_assets", "pulse_discussions", "pulse_comments"];

/** Settings → Billing. */
export function BillingSection() {
  const qc = useQueryClient();
  const q = useBilling();
  const [busy, setBusy] = useState(false);
  const s = q.data;
  if (!s) return null;
  const date = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "");
  const change = async (path: "/billing/cancel" | "/billing/resume") => {
    if (path === "/billing/cancel" && !(await askConfirm({ title: "Cancel Nexis Pro?", body: `You keep Pro until ${date(s.renews_at)}, then move to Free. Nothing is deleted.`, confirm: "Cancel subscription", danger: true }))) return;
    setBusy(true);
    try { qc.setQueryData(["billing-status"], await api.post<AnyObj>(path)); toast("success", path === "/billing/cancel" ? "Subscription canceled" : "Subscription resumed"); }
    catch (e) { toast("error", "Couldn't update your subscription", errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <section className="pro-plan" id="plan" aria-labelledby="plan-h">
      <h2 id="plan-h">Billing</h2>
      {s.payment_issue && <p className="banner error small">Payment issue — please update your payment method.</p>}
      <dl className="pro-kv">
        <dt>Current plan</dt><dd>{s.pro ? "Nexis Pro" : "Free"}</dd>
        {s.billing && <><dt>Billing</dt><dd>{s.billing}</dd></>}
        {s.renews_at && <><dt>Renews</dt><dd>{date(s.renews_at)}</dd></>}
        {s.access_until && <><dt>Access until</dt><dd>{date(s.access_until)} <span className="muted">· your subscription won't renew</span></dd></>}
      </dl>
      <ul className="pro-meters">
        {ORDER.filter((k) => s.usage[k]).map((k) => {
          const u = s.usage[k];
          return (
            <li key={k} className={u.at_limit ? "full" : u.near_limit ? "near" : ""}>
              <span>{u.label}</span>
              <span className="pro-bar" aria-hidden><span style={{ transform: `scaleX(${Math.min(1, u.used / Math.max(1, u.limit))})` }} /></span>
              <span className="num">{u.used} / {u.limit}{u.monthly ? " this month" : " assets"}</span>
            </li>
          );
        })}
      </ul>
      {s.usage.my_nexis_assets?.over_capacity && (
        <p className="np-note">You're tracking {s.usage.my_nexis_assets.used} assets and your plan includes {s.usage.my_nexis_assets.limit}. Everything stays saved; to add another, remove some or upgrade.</p>
      )}
      <p className="xs muted">Monthly allowances reset on the 1st of each month. Fair-use limits apply.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {!s.pro && <Link className="btn primary sm" to="/pro">Upgrade to Pro</Link>}
        {s.can_manage && <button type="button" className="btn sm" disabled={busy} onClick={() => openPortal()}>Manage billing</button>}
        {s.can_cancel && <button type="button" className="btn sm ghost" disabled={busy} onClick={() => change("/billing/cancel")}>Cancel subscription</button>}
        {s.can_resume && <button type="button" className="btn sm ghost" disabled={busy} onClick={() => change("/billing/resume")}>Keep my subscription</button>}
      </div>
    </section>
  );
}
