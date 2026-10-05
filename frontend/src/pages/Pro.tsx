import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../components/market";
import { PlanToggle, price, startCheckout, usePlans } from "../components/pro";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

const ROWS: { label: string; free: (l: AnyObj) => string; pro: (l: AnyObj) => string }[] = [
  { label: "Markets, prices, charts, fundamentals, news", free: () => "Included", pro: () => "Included" },
  { label: "Interactive comparisons and valuation", free: () => "Included", pro: () => "Included" },
  { label: "Nexis Pulse — read, post, reply, follow", free: () => "Included", pro: () => "Included" },
  { label: "My Nexis — investments, watchlist, Today, alerts", free: () => "Included", pro: () => "Included" },
  { label: "AI Advisor questions", free: (l) => `${l.advisor.free} a month`, pro: (l) => `Up to ${l.advisor.pro} per period` },
  { label: "PDF research reports", free: (l) => `${l.report.free} a month`, pro: (l) => `Up to ${l.report.pro} per period` },
  { label: "AI briefs on assets you track", free: (l) => `${l.brief.free} a month`, pro: (l) => `Up to ${l.brief.pro} per period` },
];

export default function Pro() {
  const plans = usePlans();
  const me = useMe().data?.user;
  const [sp] = useSearchParams();
  const status = useQuery({ queryKey: ["billing-status"], queryFn: () => api.get<AnyObj>("/billing/status"), enabled: !!me });
  const [plan, setPlan] = useState<"pro_monthly" | "pro_yearly">("pro_monthly");
  const [busy, setBusy] = useState(false);
  const p = plans.data;
  const chosen = (p?.plans ?? []).find((x: AnyObj) => x.key === plan) ?? p?.plans?.[0];
  const monthly = (p?.plans ?? []).find((x: AnyObj) => x.key === "pro_monthly");
  const yearly = (p?.plans ?? []).find((x: AnyObj) => x.key === "pro_yearly");
  const saving = monthly && yearly && yearly.amount < monthly.amount * 12 ? Math.round(100 - (yearly.amount / (monthly.amount * 12)) * 100) : null;
  const isPro = status.data?.pro;
  const go = async () => {
    if (!me) { window.location.assign(`/login?mode=signup&next=${encodeURIComponent("/pro")}`); return; }
    setBusy(true);
    await startCheckout(chosen.key);
    setBusy(false);
  };
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Plans</span></div>
        <h1>Nexis is free to use. Pro is for using it a lot.</h1>
        <p className="np-lede">Everything in Nexis works on the free plan. Nexis Pro raises the monthly allowances for the features that cost us money each time you use them: the AI Advisor, PDF research reports and AI briefs.</p>
        {sp.get("checkout") === "canceled" && <p className="np-note">Checkout was canceled — nothing was charged.</p>}
      </header>

      <div className="pro-cols">
        <section className="pro-col">
          <h2>Free</h2>
          <div className="pro-amount">No cost</div>
          <ul className="pro-list">{(p?.features?.free ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul>
          {!me && <Link className="btn" to="/login?mode=signup&next=/pulse">Create a free account</Link>}
          {me && !isPro && <span className="xs muted">Your current plan</span>}
        </section>
        <section className="pro-col pro">
          <h2>Nexis Pro</h2>
          {p?.configured && chosen ? (
            <>
              <div className="pro-amount">{price(chosen)}</div>
              <PlanToggle plans={p.plans} value={chosen.key} onChange={setPlan} />
              {saving && plan === "pro_yearly" && <div className="xs muted">About {saving}% less than paying monthly for a year</div>}
            </>
          ) : <div className="pro-amount muted">Not available to buy yet</div>}
          <ul className="pro-list">{(p?.features?.pro ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul>
          {isPro ? <Link className="btn" to="/settings#plan">You have Nexis Pro — manage</Link>
            : p?.configured && chosen ? <button type="button" className="btn primary" disabled={busy} onClick={go}>{busy ? "…" : me ? "Start Nexis Pro" : "Create an account to start Pro"}</button>
              : null}
        </section>
      </div>

      {p && (
        <section className="np-sec">
          <h2 className="np-h">Side by side</h2>
          <table className="pro-table">
            <thead><tr><th /><th>Free</th><th>Nexis Pro</th></tr></thead>
            <tbody>{ROWS.map((r) => <tr key={r.label}><td>{r.label}</td><td>{r.free(p.limits)}</td><td>{r.pro(p.limits)}</td></tr>)}</tbody>
          </table>
          <p className="xs muted">Free allowances renew on the 1st of each month; Pro allowances each billing period. Pro limits are fair-use ceilings.</p>
        </section>
      )}

      <section className="np-sec pro-faq">
        <h2 className="np-h">Good to know</h2>
        <dl>
          <dt>How do I pay?</dt><dd>Through Stripe's secure checkout: {(p?.payment_methods ?? ["Visa", "Mastercard"]).join(", ")} where your device supports them. Nexis never sees or stores your card details.</dd>
          <dt>Can I cancel?</dt><dd>{p?.cancellation ?? "Any time."}</dd>
          <dt>What if a payment fails?</dt><dd>Stripe retries it and we'll let you know. You keep Pro while it's being retried, and can update your card in Manage subscription.</dd>
          <dt>Is anything about Nexis Pulse or my portfolio locked?</dt><dd>No. Reading and posting in Pulse, My Nexis and market research are free. Pro only raises the AI and report allowances.</dd>
        </dl>
        <p className="xs muted">Nexis Finance provides information, not investment advice. See the <Link to="/terms">Terms of Use</Link> and <Link to="/disclaimer">Disclaimer</Link>.</p>
      </section>
    </div>
  );
}

/** Back from Stripe. Pro is shown only once the server has it from Stripe — never because of this URL. */
export function ProWelcome() {
  const [sp] = useSearchParams();
  const qc = useQueryClient();
  const [state, setState] = useState<"checking" | "pro" | "waiting">("checking");
  useEffect(() => {
    const sid = sp.get("session_id");
    let tries = 0;
    let stop = false;
    const check = async () => {
      if (stop) return;
      tries += 1;
      try {
        const s = sid ? await api.post<AnyObj>("/billing/confirm", { session_id: sid }) : await api.get<AnyObj>("/billing/status");
        if (s.pro) { setState("pro"); qc.invalidateQueries({ queryKey: ["billing-status"] }); qc.invalidateQueries({ queryKey: ["advisor-status"] }); return; }
      } catch { /* keep waiting for the webhook */ }
      if (tries < 15) window.setTimeout(check, 2000); else setState("waiting");
    };
    void check();
    return () => { stop = true; };
  }, [sp, qc]);
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Nexis Pro</span></div>
        {state === "checking" && <><h1>Confirming your payment…</h1><p className="np-lede">This usually takes a few seconds.</p></>}
        {state === "pro" && (
          <>
            <h1>You're on Nexis Pro.</h1>
            <p className="np-lede">Your higher allowances are active now. Stripe has emailed your receipt.</p>
            <div className="np-head-actions"><Link className="btn primary" to="/advisor">Ask the AI Advisor</Link><Link className="btn" to="/my-nexis">My Nexis</Link><Link className="btn ghost" to="/settings#plan">Your plan</Link></div>
          </>
        )}
        {state === "waiting" && (
          <>
            <h1>We're still waiting for confirmation.</h1>
            <p className="np-lede">Payments occasionally take a minute to confirm. Your plan will update on its own — check <Link to="/settings#plan">Settings</Link> shortly. You won't be charged twice.</p>
          </>
        )}
      </header>
    </div>
  );
}
