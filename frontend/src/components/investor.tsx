import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../services/api";

/* The member's investor profile: what the AI Advisor tailors its guidance to. Five questions are scored by the server
   (cautious / balanced / growth / adventurous); the member can see and change everything they've told it. */

interface Profile { goals: string[]; horizon: string; monthly_budget_aed: number | null; savings_band: string | null; experience: string; constraints: string[]; answers: Record<string, string>; risk_score: number; risk_band: string; updated_at: string }
interface Catalog { profile: Profile | null; questions: { key: string; text: string; options: { key: string; label: string }[] }[]; horizons: Record<string, string>; savings: Record<string, string>; goals: Record<string, string>; constraints: Record<string, string>; bands: Record<string, string> }

const BAND = { cautious: "Cautious", balanced: "Balanced", growth: "Growth", adventurous: "Adventurous" } as Record<string, string>;

export function useInvestorProfile(enabled = true) {
  return useQuery({ queryKey: ["investor-profile"], queryFn: () => api.get<Catalog>("/me/investor-profile"), enabled, staleTime: 300_000 });
}

/** One line under the Advisor's header: what it tailors to, with Edit and History — or an invitation to set it up. */
export function ProfileBar() {
  const q = useInvestorProfile();
  const [open, setOpen] = useState(false);
  if (q.isLoading || !q.data) return null;
  const p = q.data.profile;
  return (
    <div className="ip-bar">
      {p ? (
        <span className="small">Tailored to your profile: <b>{BAND[p.risk_band]}</b> · {q.data.horizons[p.horizon]}{p.monthly_budget_aed != null ? ` · AED ${p.monthly_budget_aed.toLocaleString()}/month` : ""}</span>
      ) : (
        <span className="small">Tell the advisor your goals, horizon and comfort with risk, and its guidance will fit your situation.</span>
      )}
      <span className="row">
        <button type="button" className={`btn sm ${p ? "" : "primary"}`} aria-expanded={open} onClick={() => setOpen((x) => !x)}>{p ? "Edit profile" : "Personalise the advisor"}</button>
        <Link className="btn sm ghost" to="/advisor/history">Your advice history</Link>
      </span>
      {open && <ProfileForm data={q.data} onDone={() => setOpen(false)} />}
    </div>
  );
}

export function ProfileForm({ data, onDone }: { data: Catalog; onDone: () => void }) {
  const qc = useQueryClient();
  const p = data.profile;
  const [goals, setGoals] = useState<string[]>(p?.goals ?? []);
  const [horizon, setHorizon] = useState(p?.horizon ?? "");
  const [budget, setBudget] = useState(p?.monthly_budget_aed != null ? String(p.monthly_budget_aed) : "");
  const [savings, setSavings] = useState(p?.savings_band ?? "");
  const [constraints, setConstraints] = useState<string[]>(p?.constraints ?? []);
  const [answers, setAnswers] = useState<Record<string, string>>(p?.answers ?? {});
  const save = useMutation({
    mutationFn: () => api.put<{ profile: Profile }>("/me/investor-profile", {
      goals, horizon, monthly_budget_aed: budget === "" ? null : Number(budget), savings_band: savings || null, constraints, answers,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["investor-profile"] }); onDone(); },
  });
  const toggle = (list: string[], set: (v: string[]) => void, k: string) => set(list.includes(k) ? list.filter((x) => x !== k) : [...list, k]);
  const complete = goals.length > 0 && !!horizon && data.questions.every((q) => answers[q.key]);
  return (
    <form className="ip-form card" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <fieldset><legend>Your goals (up to 4)</legend>
        <div className="ip-opts">{Object.entries(data.goals).map(([k, label]) => (
          <label key={k} className="wg-check"><input type="checkbox" checked={goals.includes(k)} disabled={!goals.includes(k) && goals.length >= 4} onChange={() => toggle(goals, setGoals, k)} /> {label}</label>
        ))}</div>
      </fieldset>
      <div className="ip-row">
        <label className="fld"><span>When will you need this money?</span>
          <select className="input" value={horizon} onChange={(e) => setHorizon(e.target.value)} required>
            <option value="">Choose…</option>{Object.entries(data.horizons).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
        <label className="fld"><span>Monthly amount you can invest (AED, optional)</span>
          <input className="input" type="number" min={0} max={10000000} step={100} inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
        <label className="fld"><span>Savings (optional)</span>
          <select className="input" value={savings} onChange={(e) => setSavings(e.target.value)}>
            <option value="">Prefer not to say</option>{Object.entries(data.savings).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></label>
      </div>
      {data.questions.map((q) => (
        <fieldset key={q.key}><legend>{q.text}</legend>
          <div className="ip-opts">{q.options.map((o) => (
            <label key={o.key} className="wg-check"><input type="radio" name={`ip-${q.key}`} checked={answers[q.key] === o.key} onChange={() => setAnswers({ ...answers, [q.key]: o.key })} /> {o.label}</label>
          ))}</div>
        </fieldset>
      ))}
      <fieldset><legend>Preferences (optional)</legend>
        <div className="ip-opts">{Object.entries(data.constraints).map(([k, label]) => (
          <label key={k} className="wg-check"><input type="checkbox" checked={constraints.includes(k)} onChange={() => toggle(constraints, setConstraints, k)} /> {label}</label>
        ))}</div>
      </fieldset>
      <p className="muted xs">Your answers are private to your account and used only to tailor the advisor's guidance. The advisor explains options and trade-offs; it doesn't tell you what to buy or sell.</p>
      {save.error && <div className="banner error small" role="alert">{errorMessage(save.error)}</div>}
      <div className="row">
        <button className="btn primary" disabled={!complete || save.isPending}>{save.isPending ? "Saving…" : "Save profile"}</button>
        <button type="button" className="btn ghost" onClick={onDone}>Cancel</button>
        {!complete && <span className="muted xs">Choose a goal and a horizon, and answer the five questions.</span>}
      </div>
    </form>
  );
}
