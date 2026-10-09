import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "../components/market";
import { priceText, startCheckout, tierOf, useBilling, usePlans, type PlanKey } from "../components/pro";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

/** The pricing page. Every price, limit and capability comes from GET /api/billing/plans (backend/app/core/plans.py). */
export default function Pro() {
  const plans = usePlans();
  const me = useMe().data?.user;
  const [sp] = useSearchParams();
  const status = useBilling(!!me);
  const [busy, setBusy] = useState<string | null>(null);
  const p = plans.data;
  if (plans.isError) return <div className="np pro-page"><p className="np-note" role="alert">Plans couldn't be loaded. Please reload the page.</p></div>;
  if (!p) return <div className="np pro-page"><div className="wire-skel"><span className="skel w60" /><span className="skel w80" /></div></div>;
  const current: string = status.data?.plan ?? "free";
  const F = tierOf(p, "free"), PL = tierOf(p, "plus"), PR = tierOf(p, "pro");
  if (!F || !PL || !PR) return <div className="np pro-page"><p className="np-note" role="alert">Plans couldn't be loaded. Please reload the page.</p></div>;
  const caps = p.capabilities as Record<string, AnyObj>;
  const go = async (key: PlanKey) => {
    if (!me) { window.location.assign(`/login?mode=signup&next=${encodeURIComponent("/pricing")}`); return; }
    setBusy(key);
    await startCheckout(key);
    setBusy(null);
  };
  const action = (t: AnyObj) => {
    if (t.id === current && me) return <span className="xs muted">Your current plan{t.id !== "free" && <> · <Link to="/settings#plan">manage</Link></>}</span>;
    if (t.id === "free") return me ? null : <Link className="btn" to="/login?mode=signup&next=/pulse">Create a free account</Link>;
    if (current !== "free") return <Link className="btn" to="/settings#plan">Switch to {t.name}</Link>;
    if (!p.available) return <span className="xs muted">{p.unavailable_reason}</span>;
    return <button type="button" className={`btn ${t.id === "pro" ? "primary" : ""}`} disabled={!!busy} onClick={() => go(t.price.key)}>
      {busy === t.price.key ? "…" : me ? `Get ${t.name}` : `Create an account to get ${t.name}`}
    </button>;
  };
  const cols: [AnyObj, string[]][] = [
    [F, [`${F.limits.investments} active investment`, `${F.limits.advisor} AI Advisor messages a month`, "Unlimited Nexis Pulse: read, post, reply and vote",
         "Markets, Compare & Reports, and basic investment tracking", "Help Center"]],
    [PL, [`${PL.limits.investments} active investments`, `${PL.limits.advisor} AI Advisor messages a month`, `${caps.portfolio_insights.label} — ${caps.portfolio_insights.summary}`, "Everything in Free"]],
    [PR, [`${PR.limits.investments} active investments`, `${PR.limits.advisor} AI Advisor messages a month`, `${caps.portfolio_risk.label} — ${caps.portfolio_risk.summary}`, "Everything in Plus"]],
  ];  // prettier-ignore
  const rows: [string, (t: AnyObj) => string][] = [
    ["Price", (t) => (t.id === "free" ? "AED 0" : priceText(t.price))],
    ["Active investments in My Nexis", (t) => String(t.limits.investments)],
    ["AI Advisor messages", (t) => `${t.limits.advisor}/month`],
    [caps.portfolio_insights.label, (t) => (t.capabilities.includes("portfolio_insights") ? "Included" : "—")],
    [caps.portfolio_risk.label, (t) => (t.capabilities.includes("portfolio_risk") ? "Included" : "—")],
    ["Nexis Pulse (read, post, reply, vote)", () => "Unlimited"],
    ["Watchlist tickers", (t) => String(t.limits.watchlist)],
    ["Markets, Compare & Reports", () => "Included"],
  ];
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Plans</span></div>
        <h1>Free to use. Plus and Pro for serious portfolios.</h1>
        <p className="np-lede">Nexis Pulse, Markets and Compare & Reports are free for everyone. Paid plans track more investments, raise the AI Advisor allowance and add portfolio analytics. All prices are monthly, in UAE dirhams.</p>
        {sp.get("checkout") === "canceled" && <p className="np-note">Checkout was canceled — your plan hasn't changed and nothing was charged.</p>}
      </header>

      <div className="pro-cols three">
        {cols.map(([t, items]) => (
          <section key={t.id} className={`pro-col ${t.id}`} aria-labelledby={`tier-${t.id}`}>
            <h2 id={`tier-${t.id}`}>{t.name}</h2>
            <div className="pro-amount">{t.id === "free" ? "AED 0" : priceText(t.price)}</div>
            <ul className="pro-list">{items.map((x) => <li key={x}>{x}</li>)}</ul>
            {action(t)}
          </section>
        ))}
      </div>

      <section className="np-sec">
        <h2 className="np-h">Side by side</h2>
        <div className="pro-table-wrap">
          <table className="pro-table">
            <thead><tr><th scope="col"><span className="sr-only">Feature</span></th>{[F, PL, PR].map((t) => <th key={t.id} scope="col">{t.name}</th>)}</tr></thead>
            <tbody>{rows.map(([l, f]) => <tr key={l}><th scope="row">{l}</th>{[F, PL, PR].map((t) => <td key={t.id}>{f(t)}</td>)}</tr>)}</tbody>
          </table>
        </div>
        <p className="xs muted">An investment is an asset you hold in My Nexis; more lots of the same asset don't use another slot. AI Advisor allowances reset on the 1st of each month.</p>
      </section>

      <section className="np-sec pro-faq">
        <h2 className="np-h">Good to know</h2>
        <dl>
          <dt>How do I pay?</dt><dd>Monthly, through Stripe's secure checkout: {p.payment_methods.join(", ")}. Prices are in UAE dirhams (AED). Nexis never sees or stores your card details.</dd>
          <dt>Can I switch between Plus and Pro?</dt><dd>Yes, in Settings → Billing. Upgrading charges the difference for the rest of the month; moving down credits the unused part to your next invoice.</dd>
          <dt>What happens if I cancel?</dt><dd>{p.cancellation}</dd>
          <dt>What if a payment fails?</dt><dd>Stripe retries it and we'll tell you. You keep your plan while it's being retried; update your card in Manage billing.</dd>
        </dl>
        <p className="xs muted">Nexis Finance provides information, not investment advice. See the <Link to="/terms">Terms of Use</Link> and <Link to="/disclaimer">Disclaimer</Link>.</p>
      </section>
    </div>
  );
}

/** Back from Stripe. A plan is shown only once the server has it from Stripe — never because of this URL. */
export function ProWelcome() {
  const [sp] = useSearchParams();
  const qc = useQueryClient();
  const [state, setState] = useState<"activating" | "paid" | "waiting">("activating");
  const [name, setName] = useState("your plan");
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
        if (s.paid) {
          setName(s.plan_name); setState("paid"); qc.setQueryData(["billing-status"], s); qc.invalidateQueries({ queryKey: ["advisor-status"] });
          return;
        }
      } catch { /* the webhook may still be on its way */ }
      if (tries < 20) window.setTimeout(check, 2000); else setState("waiting");
    };
    void check();
    return () => { stop = true; };
  }, [sp, qc]);
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Nexis</span></div>
        {state === "paid" ? (
          <>
            <h1>Welcome to {name}.</h1>
            <p className="np-lede">Your new limits are active now. Stripe has emailed your receipt.</p>
            <div className="np-head-actions"><Link className="btn primary" to="/my-nexis/investments">My Nexis</Link><Link className="btn" to="/advisor">Ask the AI Advisor</Link><Link className="btn ghost" to="/settings#plan">Billing</Link></div>
          </>
        ) : (
          <>
            <h1>{fromCheckout ? "Your payment was received. We're activating your plan now." : "Checking your plan…"}</h1>
            <p className="np-lede" role="status">{state === "activating" ? "This usually takes a few seconds." : "It's taking longer than usual — your plan will update on its own. Check Settings → Billing shortly. You won't be charged twice."}</p>
          </>
        )}
      </header>
    </div>
  );
}
