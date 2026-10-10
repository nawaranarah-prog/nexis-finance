import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { requireAccount } from "../components/account";
import { SymbolSearch, useMe } from "../components/market";
import { ErrorState, Loading, PageHead } from "../components/ui";
import { ReportButton, ResearchLinks } from "../components/research";
import { api, errorMessage } from "../services/api";

/* Devil's Advocate: the AI argues against a member's thesis using stored evidence only. Every citation shown here was
   checked by the server — its quote appears word for word in that evidence item; anything that failed was removed.
   Claim statuses are computed from those citations, not taken from the AI. Members only. */

type Status = "supported" | "contradicted" | "mixed" | "untested";
interface Cite { evidence_id: number; label: string; quote: string }
interface Claim { id: string; text: string; kind: "fact" | "assumption" | "prediction"; status: Status; for: Cite[]; against: Cite[]; comment: string; unverified_numbers: string[] }
interface Point { text: string; citations: Cite[]; unverified_numbers: string[] }
interface Evidence { id: number; label: string; source_kind: string; title: string; excerpt: string; publisher: string | null; url: string | null; published_at: string | null; retrieved_at: string }
interface Output {
  error?: string; claims?: Claim[]; counter_case?: Point[]; risks?: Point[]; gaps?: string[];
  signals?: { text: string; direction: "weakens" | "strengthens"; unverified_numbers: string[] }[];
  validation?: { citations: number; dropped: number; points_dropped: number }; ai?: { provider: string; model: string };
}
interface Run { id: number; status: "completed" | "failed"; created_at: string; model_version: string; output?: Output; snapshot?: { evidence: Evidence[]; computed_at: string } }
interface Item { id: number; title: string; status: string; updated_at: string; state: { symbol: string; thesis: string; horizon: string | null; claims: { id: string; text: string; kind: string }[] | null } }
interface View { item: Item; runs: Run[]; latest: Run | null; changes_since_previous: { id: string; text: string; before: Status | null; now: Status; changed: boolean }[] | null; daily_limit: number; run?: Run }

const STATUS_LABEL: Record<Status, string> = { supported: "Supported", contradicted: "Contradicted", mixed: "Mixed evidence", untested: "Not tested by the evidence" };
const KIND_LABEL = { fact: "Fact", assumption: "Assumption", prediction: "Prediction" } as const;
const HORIZONS = ["Under 1 year", "1–3 years", "3–5 years", "Over 5 years"];
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "");

export default function DevilsAdvocate() {
  const { id } = useParams();
  const me = useMe();
  return (
    <div className="da">
      <PageHead title="Devil's Advocate" desc="Write your investment thesis and get the strongest case against it — argued only from evidence you can open, with every quote checked. It challenges your reasoning; it never tells you to buy or sell." />
      {me.isLoading ? <Loading /> : !me.data?.user ? <VisitorIntro /> : id ? <ThesisView id={Number(id)} /> : <Start />}
    </div>
  );
}

function VisitorIntro() {
  return (
    <section className="card da-card">
      <h2>Stress-test your reasoning before the market does</h2>
      <ul className="tm-list">
        <li>Your thesis is split into claims, and each one is checked against market data, headlines and company relationships.</li>
        <li>Every argument cites evidence with an exact quote. The server checks each quote; anything it can't verify is removed.</li>
        <li>See which assumptions are supported, contradicted or untested — and track how that changes over time.</li>
      </ul>
      <button type="button" className="btn primary" onClick={() => requireAccount("thesis")}>Create a free account to start</button>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------- new thesis + list

function Start() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [sp] = useSearchParams();
  const pre = sp.get("symbol");  // arriving from another tool or an instrument page
  const [symbol, setSymbol] = useState<{ symbol: string; name: string } | null>(pre ? { symbol: pre.toUpperCase(), name: "" } : null);
  const [thesis, setThesis] = useState("");
  const [horizon, setHorizon] = useState("");
  const saved = useQuery({ queryKey: ["saved-research", "thesis"], queryFn: () => api.get<Item[]>("/saved-research", { kind: "thesis" }) });
  const create = useMutation({
    mutationFn: () => api.post<View>("/devils-advocate/theses", { symbol: symbol!.symbol, thesis, horizon: horizon || null }),
    onSuccess: (v) => { qc.invalidateQueries({ queryKey: ["saved-research"] }); nav(`/devils-advocate/${v.item.id}`); },
  });
  const len = thesis.trim().length;
  return (
    <>
      <section className="card da-card">
        <h2>Your thesis</h2>
        <form className="da-form" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <div className="fld"><span>Company</span>
            {symbol ? <div className="row"><b>{symbol.symbol}</b> <span className="muted small">{symbol.name}</span><button type="button" className="btn ghost sm" onClick={() => setSymbol(null)}>Change</button></div>
              : <SymbolSearch compact placeholder="Search a company, e.g. Emaar" onPick={(s) => setSymbol({ symbol: s.symbol, name: s.name })} />}
          </div>
          <label className="fld"><span>Why you think it's a good (or bad) investment</span>
            <textarea className="input" rows={5} maxLength={3000} value={thesis} onChange={(e) => setThesis(e.target.value)}
              placeholder="e.g. Emaar will keep rising because Dubai property sales stay strong and its dividend is safe." /></label>
          <span className={`xs ${len > 0 && len < 30 ? "neg" : "muted"}`}>{len}/3000 · at least 30 characters</span>
          <label className="fld"><span>Time horizon (optional)</span>
            <select className="input" value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              <option value="">Not stated</option>{HORIZONS.map((h) => <option key={h}>{h}</option>)}
            </select></label>
          {create.error && <div className="banner error small" role="alert">{errorMessage(create.error)}</div>}
          <div><button className="btn primary" disabled={!symbol || len < 30 || create.isPending}>{create.isPending ? "Saving…" : "Save thesis"}</button></div>
        </form>
      </section>
      <section className="card da-card">
        <h2>Your theses</h2>
        {saved.isLoading ? <Loading /> : saved.error ? <ErrorState error={saved.error} /> : !saved.data?.length ? (
          <p className="muted small">None yet.</p>
        ) : (
          <ul className="tm-saved">{saved.data.map((s) => <li key={s.id}><Link className="touch-link" to={`/devils-advocate/${s.id}`}><b>{s.title}</b> <span className="muted small">· {s.status.replace("_", " ")} · updated {day(s.updated_at)}</span></Link></li>)}</ul>
        )}
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------------------------- one thesis

function ThesisView({ id }: { id: number }) {
  const qc = useQueryClient();
  const v = useQuery({ queryKey: ["da-thesis", id], queryFn: () => api.get<View>(`/devils-advocate/theses/${id}`) });
  const [shown, setShown] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const run = useQuery({ queryKey: ["da-run", shown], queryFn: () => api.get<Run>(`/devils-advocate/runs/${shown}`), enabled: shown != null });
  const analyse = useMutation({
    mutationFn: () => api.post<View>(`/devils-advocate/theses/${id}/analyse`),
    onSuccess: (r) => { setShown(null); qc.setQueryData(["da-thesis", id], r); },
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<View>(`/devils-advocate/theses/${id}`, body),
    onSuccess: (r) => { setEditing(false); qc.setQueryData(["da-thesis", id], r); qc.invalidateQueries({ queryKey: ["saved-research"] }); },
  });
  if (v.isLoading) return <Loading />;
  if (v.error) return <ErrorState error={v.error} />;
  const { item, runs, latest, changes_since_previous: changes, daily_limit } = v.data!;
  const current = shown == null ? latest : run.data ?? null;
  return (
    <>
      <section className="card da-card">
        <div className="da-head">
          <div>
            <p className="muted small"><Link to={`/markets/${encodeURIComponent(item.state.symbol)}`}>{item.state.symbol}</Link>{item.state.horizon ? ` · horizon ${item.state.horizon}` : ""}</p>
            <h2>{item.title}</h2>
          </div>
          <div className="row">
            <label className="sr-only" htmlFor="da-status">Thesis status</label>
            <select id="da-status" className="input da-status" value={item.status} onChange={(e) => patch.mutate({ status: e.target.value })}>
              <option value="active">Active</option><option value="under_review">Under review</option><option value="archived">Archived</option>
            </select>
            <ResearchLinks symbol={item.state.symbol} except="thesis" />
            <Link className="btn ghost" to="/devils-advocate">All theses</Link>
          </div>
        </div>
        {editing ? (
          <form onSubmit={(e) => { e.preventDefault(); patch.mutate({ thesis: draft }); }}>
            <label className="sr-only" htmlFor="da-edit">Thesis</label>
            <textarea id="da-edit" className="input" rows={5} maxLength={3000} value={draft} onChange={(e) => setDraft(e.target.value)} />
            <p className="muted xs">Changing the wording starts the claim list afresh on the next challenge.</p>
            <div className="row"><button className="btn primary" disabled={draft.trim().length < 30 || patch.isPending}>Save</button><button type="button" className="btn ghost" onClick={() => setEditing(false)}>Cancel</button></div>
          </form>
        ) : (
          <blockquote className="da-thesis">{item.state.thesis} <button type="button" className="linkish small" onClick={() => { setDraft(item.state.thesis); setEditing(true); }}>Edit</button></blockquote>
        )}
        {patch.error && <div className="banner error small" role="alert">{errorMessage(patch.error)}</div>}
        <div className="row">
          <button type="button" className="btn primary" disabled={analyse.isPending} onClick={() => analyse.mutate()}>{analyse.isPending ? "Gathering evidence and challenging… (up to a minute)" : runs.length ? "Challenge again with today's evidence" : "Challenge this thesis"}</button>
          <span className="muted xs">Up to {daily_limit} challenges a day.</span>
        </div>
        {analyse.error && <div className="banner error small" role="alert">{errorMessage(analyse.error)}</div>}
        {runs.length > 1 && (
          <div className="da-runs"><span className="small muted">Earlier challenges:</span>
            {runs.map((r) => <button key={r.id} type="button" className={`chip ${(current?.id ?? -1) === r.id ? "on" : ""}`} aria-pressed={(current?.id ?? -1) === r.id} onClick={() => setShown(r.id)}>{new Date(r.created_at).toLocaleString()}{r.status === "failed" ? " (failed)" : ""}</button>)}
          </div>
        )}
      </section>
      {current?.status === "completed" && <div className="row"><ReportButton runId={current.id} /></div>}
      {run.isLoading && shown != null ? <Loading /> : current ? <Result run={current} changes={shown == null ? changes : null} /> : null}
    </>
  );
}

function Result({ run, changes }: { run: Run; changes: View["changes_since_previous"] }) {
  const o = run.output ?? {};
  const ev = run.snapshot?.evidence ?? [];
  if (run.status === "failed") {
    return <section className="card da-card"><div className="banner warn" role="status">{o.error ?? "The analysis could not be completed."} No result is shown for this run.</div></section>;
  }
  const claims = o.claims ?? [];
  const forCites = claims.flatMap((c) => c.for.map((x) => ({ claim: c, cite: x })));
  const againstCites = claims.flatMap((c) => c.against.map((x) => ({ claim: c, cite: x })));
  const change = new Map((changes ?? []).map((c) => [c.id, c]));
  return (
    <div className="da-result" aria-label="Challenge result">
      <Section letter="A" title="Your thesis, as claims">
        <ol className="da-claims">{claims.map((c) => <li key={c.id}><span className="badge">{KIND_LABEL[c.kind]}</span> {c.text} <StatusBadge s={c.status} /></li>)}</ol>
      </Section>
      <Section letter="B" title="Evidence for">
        {forCites.length ? <ul className="da-list">{forCites.map(({ claim, cite }, i) => <li key={i}><span className="muted small">{claim.id} · </span><Quote cite={cite} /></li>)}</ul> : <p className="muted small">No evidence item supports a claim.</p>}
      </Section>
      <Section letter="C" title="Evidence against">
        {againstCites.length ? <ul className="da-list">{againstCites.map(({ claim, cite }, i) => <li key={i}><span className="muted small">{claim.id} · </span><Quote cite={cite} /></li>)}</ul> : <p className="muted small">No evidence item contradicts a claim.</p>}
      </Section>
      <Section letter="D" title="The strongest case against">
        <Points items={o.counter_case ?? []} empty="No counter-argument could be supported with the available evidence." />
      </Section>
      <Section letter="E" title="Assumption check">
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Assumption status">
          <table className="dt da-table">
            <thead><tr><th>Claim</th><th>Type</th><th>Status</th><th>Since last challenge</th><th>Note</th></tr></thead>
            <tbody>{claims.map((c) => {
              const ch = change.get(c.id);
              return (
                <tr key={c.id}>
                  <td data-label="Claim">{c.id}: {c.text}</td><td data-label="Type">{KIND_LABEL[c.kind]}</td><td data-label="Status"><StatusBadge s={c.status} /></td>
                  <td data-label="Since last challenge">{!ch || ch.before == null ? "—" : ch.changed ? <b>{STATUS_LABEL[ch.before]} → {STATUS_LABEL[ch.now]}</b> : "No change"}</td>
                  <td data-label="Note" className="small">{c.comment}{c.unverified_numbers.length > 0 && <Unverified nums={c.unverified_numbers} />}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      </Section>
      <Section letter="F" title="Risks"><Points items={o.risks ?? []} empty="No specific risk could be supported with the available evidence." /></Section>
      <Section letter="G" title="What would change the view">
        {(o.signals ?? []).length ? <ul className="da-list">{o.signals!.map((s, i) => <li key={i}><span className={`badge ${s.direction === "weakens" ? "da-weak" : "da-strong"}`}>{s.direction === "weakens" ? "Would weaken it" : "Would strengthen it"}</span> {s.text}{s.unverified_numbers.length > 0 && <Unverified nums={s.unverified_numbers} />}</li>)}</ul> : <p className="muted small">None suggested.</p>}
      </Section>
      <Section letter="H" title="Gaps, limits and sources">
        {(o.gaps ?? []).length > 0 && <ul className="da-list">{o.gaps!.map((g) => <li key={g}>{g}</li>)}</ul>}
        {o.validation && (
          <p className="small">Checks: {o.validation.citations} citations from the AI, {o.validation.dropped} removed because the quote wasn't in that evidence item; {o.validation.points_dropped} argument(s) removed for lack of a valid citation. Statuses are computed from the remaining citations.</p>
        )}
        <p className="muted xs">AI: {o.ai?.model ?? "—"} · method {run.model_version} · {new Date(run.created_at).toLocaleString()}. Headlines are cited as headlines — the articles themselves weren't read.</p>
        <ol className="da-evidence">{ev.map((e) => (
          <li key={e.id} id={`ev-${e.id}`}>
            <b>{e.label}</b> <span className="muted small">{e.publisher ?? e.source_kind} · {e.published_at ? day(e.published_at) : `retrieved ${day(e.retrieved_at)}`}</span>
            <div className="small">{e.title}</div>
            <div className="muted small">{e.excerpt}</div>
            {e.url && <a className="touch-link small" href={e.url} target="_blank" rel="noopener noreferrer nofollow">Open source ↗<span className="sr-only"> (opens in a new tab)</span></a>}
          </li>
        ))}</ol>
      </Section>
      <p className="muted xs">Educational research only — not investment advice.</p>
    </div>
  );
}

function Section({ letter, title, children }: { letter: string; title: string; children: React.ReactNode }) {
  return <section className="card da-card"><h3><span className="da-letter" aria-hidden>{letter}</span> {title}</h3>{children}</section>;
}

function StatusBadge({ s }: { s: Status }) {
  return <span className={`badge da-st-${s}`}>{STATUS_LABEL[s]}</span>;
}

function Quote({ cite }: { cite: Cite }) {
  return <><a href={`#ev-${cite.evidence_id}`} aria-label={`Evidence ${cite.label}`}>{cite.label}</a>: <q className="da-quote">{cite.quote}</q></>;
}

function Points({ items, empty }: { items: Point[]; empty: string }) {
  if (!items.length) return <p className="muted small">{empty}</p>;
  return (
    <ul className="da-list">{items.map((p, i) => (
      <li key={i}>
        <div>{p.text}{p.unverified_numbers.length > 0 && <Unverified nums={p.unverified_numbers} />}</div>
        <div className="da-cites">{p.citations.map((c, j) => <span key={j} className="small"><Quote cite={c} /></span>)}</div>
      </li>
    ))}</ul>
  );
}

function Unverified({ nums }: { nums: string[] }) {
  return <span className="da-unverified small"> ⚠ Figure{nums.length > 1 ? "s" : ""} not found in the evidence: {nums.join(", ")}</span>;
}
