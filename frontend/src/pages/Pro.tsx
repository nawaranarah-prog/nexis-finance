import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "../components/market";
import { cents, money, PlanToggle, priceText, startCheckout, useBilling, usePlans } from "../components/pro";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

/** The pricing page. Every price and limit comes from GET /api/billing/plans (backend/app/core/plans.py). */
export default function Pro() {
  const plans = usePlans();
  const me = useMe().data?.user;
  const [sp] = useSearchParams();
  const status = useBilling(!!me);
  const [plan, setPlan] = useState<"pro_monthly" | "pro_yearly">("pro_yearly");
  const [busy, setBusy] = useState(false);
  const p = plans.data;
  if (!p) return <div className="np pro-page"><div className="wire-skel"><span className="skel w60" /><span className="skel w80" /></div></div>;
  const F = p.limits.free, P = p.limits.pro, A = p.anonymous, yv = p.yearly_value;
  const isPro = status.data?.pro;
  const go = async () => {
    if (!me) { window.location.assign(`/login?mode=signup&next=${encodeURIComponent("/pro")}`); return; }
    setBusy(true);
    await startCheckout(plan);
    setBusy(false);
  };
  const rows: [string, string, string][] = [
    ["AI Advisor (not signed in)", `${A.advisor_daily} messages/day`, "—"],
    ["AI Advisor", `${F.advisor} messages/month`, `${P.advisor} messages/month`],
    ["My Nexis tracked assets", `${F.my_nexis_assets} assets`, `${P.my_nexis_assets} assets`],
    ["Pulse discussions you start", `${F.pulse_discussions}/month`, `${P.pulse_discussions}/month`],
    ["Pulse comments and replies", `${F.pulse_comments}/month`, `${P.pulse_comments}/month`],
    ["Reading Pulse, discussions and sentiment", "Unlimited", "Unlimited"],
    ["Searching and basic asset research", "Unlimited", "Unlimited"],
  ];
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Plans</span></div>
        <h1>Nexis is free to use. Pro is for using it more.</h1>
        <p className="np-lede">Reading Pulse, researching assets and following the market are free and unlimited. Nexis Pro raises the limits on the AI Advisor, My Nexis and posting in Pulse.</p>
        {sp.get("checkout") === "canceled" && <p className="np-note">Checkout was canceled — your plan hasn't changed and nothing was charged.</p>}
      </header>

      <div className="pro-cols">
        <section className="pro-col">
          <h2>Free</h2>
          <div className="pro-amount">{money(0)}</div>
          <ul className="pro-list">
            <li>{A.advisor_daily} AI Advisor messages/day if not signed in</li>
            <li>{F.advisor} AI Advisor messages/month with an account</li>
            <li>{F.my_nexis_assets} My Nexis assets</li>
            <li>{F.pulse_discussions} discussions/month</li>
            <li>{F.pulse_comments} comments/month</li>
            <li>Unlimited Pulse viewing</li>
            <li>Unlimited basic asset research</li>
            <li>Unlimited reading of discussions</li>
          </ul>
          {!me && <Link className="btn" to="/login?mode=signup&next=/pulse">Create a free account</Link>}
          {me && !isPro && <span className="xs muted">Your current plan</span>}
        </section>
        <section className="pro-col pro">
          <h2>Nexis Pro</h2>
          <div className="pro-amount">{priceText(p.prices[plan])}</div>
          <PlanToggle value={plan} onChange={setPlan} yearlyNote="best value" />
          <div className="xs muted pro-yearly">
            {plan === "pro_yearly"
              ? <>{cents(yv.per_month_cents)}/month billed yearly · {cents(yv.savings_cents)} less than twelve monthly payments ({cents(yv.twelve_monthly_cents)}) — about {yv.savings_pct}% savings</>
              : <>Or {priceText(p.prices.pro_yearly)} — {cents(yv.per_month_cents)}/month, about {yv.savings_pct}% less</>}
          </div>
          <ul className="pro-list">
            <li>{P.advisor} AI Advisor messages/month</li>
            <li>{P.my_nexis_assets} My Nexis assets</li>
            <li>{P.pulse_discussions} discussions/month</li>
            <li>{P.pulse_comments} comments/month</li>
            <li>Everything in Free</li>
          </ul>
          <p className="xs muted">Fair-use limits apply.</p>
          {isPro ? <Link className="btn" to="/settings#plan">You're on Nexis Pro — manage</Link>
            : p.available ? <button type="button" className="btn primary" disabled={busy} onClick={go}>{busy ? "…" : me ? "Start Nexis Pro" : "Create an account to start Pro"}</button>
              : <span className="xs muted">{p.unavailable_reason}</span>}
        </section>
      </div>

      <section className="np-sec">
        <h2 className="np-h">Side by side</h2>
        <table className="pro-table">
          <thead><tr><th /><th>Free</th><th>Nexis Pro</th></tr></thead>
          <tbody>{rows.map(([l, f, x]) => <tr key={l}><td>{l}</td><td>{f}</td><td>{x}</td></tr>)}</tbody>
        </table>
        <p className="xs muted">Monthly allowances reset on the 1st of each month, on both plans. Fair-use limits apply.</p>
      </section>

      <section className="np-sec pro-faq">
        <h2 className="np-h">Good to know</h2>
        <dl>
          <dt>How do I pay?</dt><dd>Through Stripe's secure checkout: {p.payment_methods.join(", ")} where your device supports them. Prices are in US dollars. Nexis never sees or stores your card details.</dd>
          <dt>Can I cancel?</dt><dd>{p.cancellation}</dd>
          <dt>What if a payment fails?</dt><dd>Stripe retries it and we'll tell you. You keep Pro while it's being retried; update your card in Manage billing.</dd>
          <dt>What happens to my data if I go back to Free?</dt><dd>Nothing is deleted. If you track more assets than Free allows, they all stay — you just can't add more until you remove some or upgrade again.</dd>
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
  const [state, setState] = useState<"activating" | "pro" | "waiting">("activating");
  const fromCheckout = !!sp.get("session_id");
  useEffect(() => {
    const sid = sp.get("session_id");
    let tries = 0;
    let stop = false;
    const check = async () => {
      if (stop) return;
      tries += 1;
      try {
        const s: AnyObj = sid ? await api.post("/billing/confirm", { session_id: sid }) : await api.get("/billing/status");
        if (s.pro) { setState("pro"); qc.setQueryData(["billing-status"], s); qc.invalidateQueries({ queryKey: ["advisor-status"] }); return; }
      } catch { /* the webhook may still be on its way */ }
      if (tries < 20) window.setTimeout(check, 2000); else setState("waiting");
    };
    void check();
    return () => { stop = true; };
  }, [sp, qc]);
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Nexis Pro</span></div>
        {state === "pro" ? (
          <>
            <h1>Welcome to Nexis Pro.</h1>
            <p className="np-lede">Your Pro limits are active now. Stripe has emailed your receipt.</p>
            <div className="np-head-actions"><Link className="btn primary" to="/advisor">Ask the AI Advisor</Link><Link className="btn" to="/my-nexis">My Nexis</Link><Link className="btn ghost" to="/settings#plan">Billing</Link></div>
          </>
        ) : (
          <>
            <h1>{fromCheckout ? "Your payment was received. We're activating Pro now." : "Checking your plan…"}</h1>
            <p className="np-lede">{state === "activating" ? "This usually takes a few seconds." : "It's taking longer than usual — your plan will update on its own. Check Settings → Billing shortly. You won't be charged twice."}</p>
          </>
        )}
      </header>
    </div>
  );
}
