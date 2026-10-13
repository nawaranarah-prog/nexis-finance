import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "../components/market";
import { annualText, bestAnnualSaving, money, priceText, startCheckout, tierOf, useBilling, usePlans, type PlanKey } from "../components/pro";
import { Seg } from "../components/ui";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

/** What checkout charges for a price and when it renews. */
function chargeText(p: AnyObj): string {
  const amount = money(p.amount, p.currency);
  return p.interval === "year"
    ? `Charged today: ${amount} for 12 months. Renews every year at ${amount} until you cancel.`
    : `Charged today: ${amount}. Renews every month at ${amount} until you cancel.`;
}

/** The pricing page. Every price, limit and capability comes from GET /api/billing/plans (backend/app/core/plans.py). */
export default function Pro() {
  const plans = usePlans();
  const me = useMe().data?.user;
  const [sp] = useSearchParams();
  const status = useBilling(!!me);
  const [busy, setBusy] = useState<string | null>(null);
  const [interval, setBillingInterval] = useState<"month" | "year">("month");
  const p = plans.data;
  if (plans.isError) return <div className="np pro-page"><p className="np-note" role="alert">Plans couldn't be loaded. Please reload the page.</p></div>;
  if (!p) return <div className="np pro-page"><div className="wire-skel"><span className="skel w60" /><span className="skel w80" /></div></div>;
  const current: string = status.data?.plan ?? "free";
  const F = tierOf(p, "free"), PL = tierOf(p, "plus"), PR = tierOf(p, "pro");
  if (!F || !PL || !PR) return <div className="np pro-page"><p className="np-note" role="alert">Plans couldn't be loaded. Please reload the page.</p></div>;
  const caps = p.capabilities as Record<string, AnyObj>;
  // Yearly billing appears only when yearly plans exist on the server (plans.PRICES); amounts always come from there.
  const intervals: string[] = p.intervals ?? ["month"];
  const yearly = intervals.includes("year");
  const priceOf = (t: AnyObj): AnyObj => (t.prices?.[interval] ? { ...t.prices[interval], interval } : t.price);
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
    return <button type="button" className={`btn ${t.id === "pro" ? "primary" : ""}`} disabled={!!busy} onClick={() => go(priceOf(t).key)}>
      {busy === priceOf(t).key ? "…" : me ? `Get ${t.name}${priceOf(t).annual ? " annually" : ""}` : `Create an account to get ${t.name}`}
    </button>;
  };
  const cols: [AnyObj, string[]][] = [
    [F, [`${F.limits.investments} active investment`, `${F.limits.advisor} AI Advisor messages a month`, "Unlimited Nexis Pulse: read, post, reply and vote",
         "Markets, Compare & Reports, and basic investment tracking", "Help Center"]],
    [PL, [`${PL.limits.investments} active investments`, `${PL.limits.advisor} AI Advisor messages a month`, `${caps.portfolio_insights.label} — ${caps.portfolio_insights.summary}`, "Everything in Free"]],
    [PR, [`${PR.limits.investments} active investments`, `${PR.limits.advisor} AI Advisor messages a month`, `${caps.portfolio_risk.label} — ${caps.portfolio_risk.summary}`, "Everything in Plus"]],
  ];  // prettier-ignore
  const rows: [string, (t: AnyObj) => string][] = [
    ["Price", (t) => (t.id === "free" ? "AED 0" : priceText(priceOf(t)) + (interval === "year" && priceOf(t).annual ? " (billed annually upfront)" : ""))],
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
        <p className="np-lede">Nexis Pulse, Markets and Compare & Reports are free for everyone. Paid plans track more investments, raise the AI Advisor allowance and add portfolio analytics. All prices are in UAE dirhams{yearly ? ", billed monthly or annually (one upfront payment for 12 months)" : " and billed monthly"}.</p>
        {yearly && <div className="pro-interval"><Seg value={interval} options={[{ value: "month", label: "Monthly" }, { value: "year", label: `Annual · save up to ${bestAnnualSaving(p)}%` }]} onChange={setBillingInterval} /></div>}
        {sp.get("checkout") === "canceled" && <p className="np-note">Checkout was canceled — your plan hasn't changed and nothing was charged.</p>}
      </header>

      <div className="pro-cols three">
        {cols.map(([t, items]) => (
          <section key={t.id} className={`pro-col ${t.id}`} aria-labelledby={`tier-${t.id}`}>
            <h2 id={`tier-${t.id}`}>{t.name}</h2>
            <div className="pro-amount">{t.id === "free" ? "AED 0" : priceText(priceOf(t))}</div>
            {t.id !== "free" && interval === "year" && priceOf(t).annual && <p className="pro-annual small">{annualText(priceOf(t))}</p>}
            {t.id !== "free" && interval === "month" && t.prices?.year?.annual && <p className="pro-annual xs muted">Or {priceText({ ...t.prices.year, interval: "year" })} billed annually upfront.</p>}
            {t.id !== "free" && <p className="pro-terms xs" data-testid={`terms-${t.id}`}>{chargeText(priceOf(t))}</p>}
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
          <dt>How do I pay?</dt><dd>{yearly ? "Monthly, or annually as one upfront payment for 12 months," : "Monthly,"} through Stripe's secure checkout: {p.payment_methods.join(", ")}. Prices are in UAE dirhams (AED). Nexis never sees or stores your card details.</dd>
          <dt>Can I switch plans or billing period?</dt><dd>Yes, in Settings → Billing. Before anything changes you see Stripe's figures: what's charged today, any credit, and the renewal amount and date. A change that costs something starts only once that payment succeeds; until then your plan stays as it is. Upgrading charges the difference for the rest of your current period; moving down credits the unused part to your next invoice (credits aren't refunded). Moving to annual billing charges the annual price today, minus a credit for the unused part of your current period, and your plan then renews a year from today.</dd>
          <dt>What happens if I cancel?</dt><dd>{p.cancellation}</dd>
          <dt>What if a payment fails?</dt><dd>Stripe retries it and we'll tell you. You keep your plan for up to 14 days while it's being retried; update your card in Manage billing. After that the account moves to Free until the payment succeeds.</dd>
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
  // checking: asking Stripe (via the server) · paid: the plan is active · processing: paid but not confirmed yet ·
  // incomplete: the checkout wasn't finished · failed: the payment didn't go through · waiting: still no answer
  const [state, setState] = useState<"checking" | "paid" | "processing" | "incomplete" | "failed" | "waiting">("checking");
  const [name, setName] = useState("your plan");
  const [billing, setBilling] = useState<string | null>(null);
  const [test, setTest] = useState(false);
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
          setName(s.plan_name); setBilling(s.billing ? `${s.billing}${s.interval === "year" ? ", billed annually upfront" : ""}` : null); setTest(!!s.test_subscription);
          setState("paid"); qc.setQueryData(["billing-status"], s); qc.invalidateQueries({ queryKey: ["advisor-status"] });
          return;
        }
        const c: AnyObj | null = s.checkout ?? null;
        if (c?.status === "open") { setState("incomplete"); return; }  // the member came back without paying
        if (c?.subscription_status === "incomplete_expired" || c?.status === "expired") { setState("failed"); return; }
        if (c?.status === "complete") setState("processing");  // paid or processing at Stripe; waiting for confirmation
      } catch { /* the webhook may still be on its way */ }
      if (tries < 20) window.setTimeout(check, 2000); else setState("waiting");
    };
    void check();
    return () => { stop = true; };
  }, [sp, qc]);
  const head: Record<string, [string, string]> = {
    checking: ["Checking your payment with Stripe…", "This usually takes a few seconds."],
    processing: ["Your payment is being confirmed.", "Stripe is still processing it. Your plan starts as soon as Stripe confirms the payment — this page updates on its own."],
    incomplete: ["Checkout wasn't completed.", "Nothing was charged and your plan hasn't changed. You can start again whenever you like."],
    failed: ["The payment didn't go through.", "Nothing was charged and your plan hasn't changed. Try again with another card."],
    waiting: ["We haven't heard back from Stripe yet.", "Your plan updates on its own once Stripe confirms the payment. Check Settings → Billing shortly. You won't be charged twice."],
  };
  return (
    <div className="np pro-page">
      <header className="np-head">
        <div className="np-eyebrow"><span>Nexis</span></div>
        {state === "paid" ? (
          <>
            <h1>Welcome to {name}.</h1>
            <p className="np-lede">Your new limits are active now.{billing && <> Billing: <b>{billing}</b>.</>} {test ? "This was a Stripe test-mode (Sandbox) payment: no real money was charged." : "Stripe has emailed your receipt."}</p>
            <div className="np-head-actions"><Link className="btn primary" to="/my-nexis/investments">My Nexis</Link><Link className="btn" to="/advisor">Ask the AI Advisor</Link><Link className="btn ghost" to="/settings#plan">Billing</Link></div>
          </>
        ) : (
          <>
            <h1>{head[state][0]}</h1>
            <p className="np-lede" role="status">{head[state][1]}</p>
            {(state === "incomplete" || state === "failed") && <div className="np-head-actions"><Link className="btn primary" to="/pricing">Back to plans</Link></div>}
          </>
        )}
      </header>
    </div>
  );
}
