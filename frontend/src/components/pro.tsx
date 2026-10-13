import { askConfirm } from "./dialog";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "./market";
import { toast } from "./toast";
import { api, ApiError, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { requireAccount, type AccountReason } from "./account";

/* Plans in the browser are presentation only. Tiers, prices, limits and capabilities come from GET /api/billing/plans,
   which is built from backend/app/core/plans.py; what a member may do is decided by the server. No number is typed here. */

const EVENT = "nexis:upgrade";
export type PlanKey = `${"plus" | "pro"}_${"monthly" | "yearly"}`;
const PLAN_ERRORS = ["usage_limit", "sign_in_required", "already_pro", "upgrade_required", "investment_selection_required", "payment_failed"];

export const usePlans = () => useQuery({ queryKey: ["billing-plans"], queryFn: () => api.get<AnyObj>("/billing/plans"), staleTime: 600_000 });
export const useBilling = (enabled = true) => useQuery({ queryKey: ["billing-status"], queryFn: () => api.get<AnyObj>("/billing/status"), enabled });

export const money = (amount: number, currency = "AED") =>
  `${currency} ${new Intl.NumberFormat("en-US", { minimumFractionDigits: amount % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(amount)}`;
export const priceText = (p: AnyObj | undefined) => (p ? `${money(p.amount, p.currency)}/${p.interval}` : "");

/** How annual billing is always described: one upfront payment for 12 months, its monthly equivalent and the real
    saving against 12 monthly payments — all computed on the server (plans.annual_offer). Never "AED 19/month". */
export function annualText(p: AnyObj | undefined, opts: { short?: boolean } = {}): string {
  const a = p?.annual;
  if (!a) return "";
  const upfront = money(a.upfront, p!.currency);
  const per = `about ${money(a.per_month, p!.currency)} a month`;
  return opts.short
    ? `${per}, billed annually upfront`
    : `${per}, billed annually upfront (${upfront} for 12 months). Save ${money(a.saving, p!.currency)} — about ${a.saving_pct}% — vs 12 monthly payments.`;
}

/** The largest annual saving on offer, for the Monthly / Annual switch. */
export const bestAnnualSaving = (catalog: AnyObj | undefined) =>
  Math.max(0, ...((catalog?.tiers ?? []) as AnyObj[]).map((t) => t.prices?.year?.annual?.saving_pct ?? 0));
export const tierOf = (plans: AnyObj | undefined, id: string) => (plans?.tiers as AnyObj[] | undefined)?.find((t) => t.id === id);

/** Turn a plan-related API error into the right explanation. Returns true when handled. */
export function handlePlanError(e: unknown, reason: AccountReason = "general"): boolean {
  if (e instanceof ApiError && e.status === 401) { requireAccount(reason); return true; }
  if (e instanceof ApiError && PLAN_ERRORS.includes(e.code)) {
    window.dispatchEvent(new CustomEvent(EVENT, { detail: { code: e.code, message: e.message, ...((e.details as AnyObj) ?? {}) } }));
    return true;
  }
  return false;
}

/** For raw fetch calls (the streaming Advisor). */
export function handlePlanErrorBody(status: number, body: AnyObj | null, reason: AccountReason = "general"): boolean {
  const err = body?.error;
  return !!err && handlePlanError(new ApiError(status, err.code, err.message, err.details), reason);
}

export async function startCheckout(plan: PlanKey): Promise<void> {
  try {
    const r = await api.post<{ url: string }>("/billing/checkout", { plan });
    window.location.assign(r.url);  // Stripe Checkout collects the payment; nothing is granted until Stripe confirms it
  } catch (e) {
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
    {target && t && (p?.available ? (
      <p className="pro-price">{t.name} · {priceText(t.price)}{t.prices?.year && <> or {priceText({ ...t.prices.year, interval: "year" })} <span className="xs muted">({annualText(t.prices.year, { short: true })})</span></>}</p>
    ) : <p className="np-note">Paid plans aren't available to buy yet.</p>)}
    {target && <p className="xs muted">Billed {(p?.intervals ?? ["month"]).includes("year") ? "monthly, or annually as one upfront payment," : "monthly"} by Stripe. Cancel any time and keep your plan until the end of the period you paid for.</p>}
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

const NUDGE: Record<string, { what: string; unit: string }> = {
  investments: { what: "Want to track more investments?", unit: "active investments" },
  advisor: { what: "Need more AI Advisor answers?", unit: "AI Advisor messages a month" },
};

/** A visible upgrade prompt where a plan limit applies, shown to Free and Plus members before they hit the limit.
    Tiers, limits and prices come from the server catalog; nothing is shown that the server wouldn't sell. */
export function UpgradeNudge({ feature }: { feature: "investments" | "advisor" }) {
  const plans = usePlans();
  const plan: string | undefined = useBilling().data?.plan;
  const rank = ["free", "plus", "pro"];
  if (!plan || !rank.includes(plan) || plan === "pro" || !plans.data) return null;
  const n = NUDGE[feature];
  const mine = tierOf(plans.data, plan)?.limits?.[feature];
  const better = rank.slice(rank.indexOf(plan) + 1)
    .map((id) => tierOf(plans.data, id)).filter((t): t is AnyObj => !!t && t.limits?.[feature] > (mine ?? 0));
  if (!better.length) return null;
  const offer = (t: AnyObj) => {
    const m = t.prices?.month;
    return <span key={t.id}><b>{t.name}</b>: {t.limits[feature]} {n.unit}{m ? ` (${priceText({ ...m, interval: "month" })})` : ""}</span>;
  };
  const saving = bestAnnualSaving(plans.data);
  const target = plan === "free" ? "/pricing" : "/settings#plan";
  return (
    <div className="pro-nudge" data-testid={`upgrade-nudge-${feature}`}>
      <p><b>{n.what}</b> {mine != null && <>Your plan includes {mine}. </>}
        {better.map((t, i) => <span key={t.id}>{i > 0 && " · "}{offer(t)}</span>)}.
        {saving > 0 && <span className="xs muted"> Save up to {saving}% with annual billing.</span>}</p>
      <Link className="btn primary sm" to={target}>{better.length > 1 ? "Upgrade to Plus or Pro" : `Upgrade to ${better[0].name.replace(/^Nexis /, "")}`}</Link>
    </div>
  );
}

const ORDER = ["investments", "advisor", "watchlist"];
const STATUS: Record<string, string> = { active: "Active", trialing: "Active (trial)", past_due: "Payment overdue — Stripe is retrying" };
const date = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }) : "");

/** Every plan on sale except the member's current one: the other tier on the same billing period first, then the
    same tier on the other period. Prices and keys come from the server catalog, the same one checkout uses. */
function planChoices(catalog: AnyObj | undefined, s: AnyObj): { key: PlanKey; tier: AnyObj; interval: string; price: AnyObj }[] {
  const out: { key: PlanKey; tier: AnyObj; interval: string; price: AnyObj }[] = [];
  const cur = s.interval ?? "month";
  for (const t of (catalog?.tiers ?? []) as AnyObj[]) {
    for (const [interval, p] of Object.entries((t.prices ?? {}) as Record<string, AnyObj>)) {
      if (p.key !== s.price_key) out.push({ key: p.key as PlanKey, tier: t, interval, price: { ...p, interval } });
    }
  }
  return out.filter((c) => c.tier.id !== "free").sort((x, y) => Number(x.interval !== cur) - Number(y.interval !== cur) || Number(x.tier.id === s.plan) - Number(y.tier.id === s.plan));
}

/** Change plan: pick a plan, see Stripe's own figures for it (charged today, credit, renewal, when it takes effect),
    then confirm that exact amount. The server applies it as a Stripe pending update: the new plan starts only once the
    payment succeeds, and if Stripe needs the member (declined card, 3-D Secure) they pay on Stripe's page. */
function ChangePlan({ s, onClose }: { s: AnyObj; onClose: () => void }) {
  const qc = useQueryClient();
  const plans = usePlans();
  const choices = planChoices(plans.data, s);
  const [pick, setPick] = useState<PlanKey | null>(null);
  const c = choices.find((x) => x.key === pick) ?? choices[0];
  const cap = c?.tier.limits?.investments ?? 0;
  const symbols: string[] = s.investments?.symbols ?? [];
  const needKeep = symbols.length > cap;
  const [keep, setKeep] = useState<string[]>(symbols.slice(0, cap));
  const [keepFor, setKeepFor] = useState(c?.key);
  if (c && keepFor !== c.key) { setKeepFor(c.key); setKeep(symbols.slice(0, cap)); }  // a different plan, a different allowance
  const quote = useQuery({
    queryKey: ["billing-preview", c?.key], enabled: !!c, staleTime: 0, gcTime: 0, retry: false,
    queryFn: () => api.post<AnyObj>("/billing/change-plan/preview", { plan: c!.key }),
  });
  const [busy, setBusy] = useState(false);
  if (!c) return null;
  const q = quote.data;
  const label = `${c.tier.name}, billed ${c.interval === "year" ? "annually" : "monthly"}`;
  const pay = q && q.amount_due_minor > 0;
  const go = async () => {
    if (!q) return;
    setBusy(true);
    try {
      const r = await api.post<AnyObj>("/billing/change-plan", {
        plan: c.key, expected_amount: q.amount_due_minor, proration_date: q.proration_date, ...(needKeep ? { keep } : {}),
      });
      qc.setQueryData(["billing-status"], r);
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      if (r.change?.result === "payment_required") {
        toast("info", "Payment needed", r.change.message);
        if (r.change.pay_url) { window.location.assign(r.change.pay_url); return; }
      } else toast("success", r.change?.message ?? `You're now on ${label}`);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.code === "change_needs_review") { toast("info", "Please review again", errorMessage(e)); void quote.refetch(); }
      else if (!handlePlanError(e)) toast("error", "Couldn't change your plan", errorMessage(e));
    } finally { setBusy(false); }
  };
  return <Shell onClose={onClose} label="cp-title">
    <h2 id="cp-title">Change plan</h2>
    <p className="small">You're on <b>{s.plan_name}</b>{s.billing ? ` (${s.billing})` : ""}.</p>
    {choices.length > 1 && (
      <fieldset className="cp-choices"><legend className="sr-only">New plan</legend>
        {choices.map((x) => (
          <label key={x.key} className="wg-check">
            <input type="radio" name="cp-plan" checked={x.key === c.key} onChange={() => setPick(x.key)} />
            {x.tier.name} · <b>{priceText(x.price)}</b>{x.interval === "year" && <span className="xs muted"> · {annualText(x.price, { short: true })}</span>}
          </label>
        ))}
      </fieldset>
    )}
    <p><b>{label} · {priceText(c.price)}</b> · {c.tier.limits.investments} active investments · {c.tier.limits.advisor} AI Advisor messages a month.</p>
    {quote.isLoading && <div className="wire-skel" aria-label="Calculating the cost with Stripe"><span className="skel w70" /><span className="skel w50" /></div>}
    {quote.isError && <p className="banner error small" role="alert">{errorMessage(quote.error)}</p>}
    {q && (
      <div className="cp-quote" data-testid="cp-terms">
        <dl className="pro-kv">
          <dt>Charged today</dt><dd><b>{q.amount_due > 0 ? money(q.amount_due, q.currency) : "Nothing"}</b>{q.credit > 0 && <span className="muted"> · credit {money(q.credit, q.currency)} for the unused part of your current plan</span>}</dd>
          <dt>Takes effect</dt><dd>{q.effective === "after_payment" ? "As soon as the payment succeeds" : "Immediately"}</dd>
          <dt>Then</dt><dd>{q.billing}{q.renews_at ? `, renewing on ${date(q.renews_at)}` : ""}</dd>
        </dl>
        <p className="small">{q.summary}</p>
      </div>
    )}
    {needKeep && <>
      <p className="small">{c.tier.name} includes {cap} active investments and you have {symbols.length}. The others are archived — kept, read-only, and restored if you move up again.</p>
      <KeepPicker symbols={symbols} limit={cap} value={keep} onChange={setKeep} />
    </>}
    <div className="np-modal-foot">
      <button type="button" className="btn sm ghost" onClick={onClose}>Cancel</button>
      <button type="button" className="btn primary sm" disabled={busy || !q || (needKeep && keep.length === 0)} onClick={go}>
        {busy ? "…" : !q ? "Checking the price…" : pay ? `Pay ${money(q.amount_due, q.currency)} and switch` : `Switch to ${label}`}
      </button>
    </div>
  </Shell>;
}

/** A plan change waiting for its payment: pay it on Stripe's page, or drop it (the current plan continues). */
function PendingChange({ s }: { s: AnyObj }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const p = s.pending_change;
  if (!p) return null;
  const payNow = async () => {
    setBusy(true);
    try { window.location.assign((await api.post<{ url: string }>("/billing/pending/pay")).url); }
    catch (e) { toast("error", "Couldn't open the payment page", errorMessage(e)); setBusy(false); }
  };
  const drop = async () => {
    setBusy(true);
    try { qc.setQueryData(["billing-status"], await api.post<AnyObj>("/billing/pending/cancel")); toast("success", "Change cancelled — nothing was charged"); }
    catch (e) { toast("error", "Couldn't cancel the change", errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="np-note" role="status" data-testid="pending-change">
      <p><b>Your change to {p.plan_name} ({p.billing}) is waiting for payment.</b> Until it's paid you stay on {s.plan_name}{p.expires_at ? `; the offer lapses on ${date(p.expires_at)}` : ""}.</p>
      <div className="row" style={{ gap: 8 }}>
        <button type="button" className="btn primary sm" disabled={busy} onClick={payNow}>Complete payment</button>
        <button type="button" className="btn sm ghost" disabled={busy} onClick={drop}>Cancel the change</button>
      </div>
    </div>
  );
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
      {s.test_subscription && <p className="np-note small" data-testid="test-subscription">Stripe test mode (Sandbox): this subscription was paid with a test card. No real money was charged, and it never counts once real payments are switched on.</p>}
      <PendingChange s={s} />
      <dl className="pro-kv">
        <dt>Current plan</dt><dd>{s.plan_name}</dd>
        {s.paid && s.status && <><dt>Status</dt><dd>{STATUS[s.status] ?? s.status}</dd></>}
        {s.billing && <><dt>Billing</dt><dd>{s.billing}{s.interval === "year" ? " · billed annually upfront" : s.interval === "month" ? " · billed monthly" : ""}</dd></>}
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
