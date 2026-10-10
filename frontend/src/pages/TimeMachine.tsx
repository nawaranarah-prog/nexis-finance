import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { requireAccount } from "../components/account";
import { SymbolSearch, useMe } from "../components/market";
import { ErrorState, Loading, PageHead, Seg } from "../components/ui";
import { api, errorMessage } from "../services/api";

/* Time Machine: "what happens to my holdings if…". Deterministic arithmetic only — the moves you choose, measured
   past sensitivities with their uncertainty, bond duration, and the DCF with changed assumptions. No probabilities,
   no AI, and never a forecast. Members only (the API refuses visitors). */

type FactorKey = "oil" | "market_us" | "market_uae" | "gold" | "eurusd" | "rates_us10y";
interface Inputs {
  holdings: "mine" | "custom";
  positions: { symbol: string; amount: number }[];
  factor_shocks: Partial<Record<FactorKey, number | null>>;
  asset_shocks: Record<string, number>;
  window_weeks: number;
  valuation: { symbol: string; growth_change: number; wacc_change: number; terminal_growth_change: number } | null;
}
interface Line { symbol: string; name: string; value: number; method: "explicit" | "factor" | "duration" | "none"; pct: number | null; low?: number; high?: number; change?: number; flags: string[];
  stats?: { beta?: Record<string, number>; se?: Record<string, number>; r2?: number; weeks?: number; from?: string; to?: string; modified_duration?: number; convexity?: number; assumes?: string } }
interface Output {
  currency: string; lines: Line[]; unpriced: string[]; missing_factors: string[]; notes: string[];
  portfolio: { value: number; change: number; change_pct: number | null; wide_low: number; wide_high: number; covered_value: number };
  valuation: null | { symbol: string; name: string; currency: string; price: number | null; before: number; after: number; change_pct: number | null; flags: string[];
    assumptions: Record<"growth_start" | "wacc" | "terminal_growth", [number, number]> };
}
interface RunResult { inputs: Inputs; snapshot: { computed_at: string; window_weeks: number; series: Record<string, { first: string; last: string; last_close: number; days: number } | null> }; output: Output; model_version: string }
interface StoredRun extends Partial<RunResult> { id: number; created_at: string; model_version: string; summary?: Output["portfolio"] }
interface Item { id: number; title: string; updated_at: string; state?: { inputs: Inputs }; notes: string | null }

const FACTORS: { key: FactorKey; label: string; unit: "%" | "bp"; hint: string }[] = [
  { key: "oil", label: "Brent crude oil", unit: "%", hint: "e.g. −30" },
  { key: "market_us", label: "S&P 500", unit: "%", hint: "e.g. −20" },
  { key: "market_uae", label: "DFM General Index", unit: "%", hint: "e.g. −15" },
  { key: "gold", label: "Gold", unit: "%", hint: "e.g. +10" },
  { key: "eurusd", label: "Euro vs US dollar", unit: "%", hint: "−10 = stronger dollar" },
  { key: "rates_us10y", label: "US 10-year yield", unit: "bp", hint: "+100 = one point" },
];
const PRESETS: { label: string; shocks: Partial<Record<FactorKey, number>> }[] = [
  { label: "Oil falls 30%", shocks: { oil: -30 } },
  { label: "Global stocks fall 20%", shocks: { market_us: -20 } },
  { label: "UAE market falls 15%", shocks: { market_uae: -15 } },
  { label: "Rates up one point", shocks: { rates_us10y: 100 } },
  { label: "Dollar 10% stronger", shocks: { eurusd: -10 } },
];
const METHOD: Record<Line["method"], string> = { explicit: "Your move", factor: "Measured sensitivity", duration: "Bond duration", none: "Not estimated" };
const EMPTY: Inputs = { holdings: "custom", positions: [], factor_shocks: {}, asset_shocks: {}, window_weeks: 104, valuation: null };

const num = (v: number | null | undefined, d = 1) => (v == null || !Number.isFinite(v) ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v), d)}%`);
const money = (v: number | null | undefined, ccy: string) => (v == null ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}${ccy === "as entered" ? "" : ` ${ccy}`}`);
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "");

export default function TimeMachine() {
  const { id } = useParams();
  const me = useMe();
  return (
    <div className="tm">
      <PageHead title="Time Machine" desc="Test what a market move would do to your holdings — using the moves you choose, measured past sensitivities with their uncertainty, bond duration and the valuation model. A what-if, never a forecast." />
      {me.isLoading ? <Loading /> : !me.data?.user ? <VisitorIntro /> : id ? <Saved id={Number(id)} /> : <Start />}
    </div>
  );
}

function VisitorIntro() {
  return (
    <section className="card tm-card">
      <h2>Stress-test your holdings, with the working shown</h2>
      <ul className="tm-list">
        <li>“What if oil falls 30%?” — measured from how each holding moved with oil, with a range for the uncertainty.</li>
        <li>Rate shocks for bonds via duration and convexity; your own move for any single holding.</li>
        <li>Change growth or the discount rate and see what the valuation model gives.</li>
        <li>Save scenarios, run them again on new data, and compare runs.</li>
      </ul>
      <button type="button" className="btn primary" onClick={() => requireAccount("scenario")}>Create a free account to start</button>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------- start: builder + saved list

function Start() {
  const loc = useLocation();
  const initial = (loc.state as { inputs?: Inputs } | null)?.inputs;
  const saved = useQuery({ queryKey: ["saved-research", "scenario"], queryFn: () => api.get<Item[]>("/saved-research", { kind: "scenario" }) });
  return (
    <>
      <Builder initial={initial} />
      <section className="card tm-card">
        <h2>Your saved scenarios</h2>
        {saved.isLoading ? <Loading /> : saved.error ? <ErrorState error={saved.error} /> : !saved.data?.length ? (
          <p className="muted small">Nothing saved yet. Run a scenario and choose “Save scenario”.</p>
        ) : (
          <ul className="tm-saved">{saved.data.map((s) => <li key={s.id}><Link className="touch-link" to={`/time-machine/${s.id}`}><b>{s.title}</b> <span className="muted small">· updated {day(s.updated_at)}</span></Link></li>)}</ul>
        )}
      </section>
    </>
  );
}

function Builder({ initial }: { initial?: Inputs }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [inp, setInp] = useState<Inputs>(initial ?? EMPTY);
  const [title, setTitle] = useState("");
  const runQ = useMutation({ mutationFn: (i: Inputs) => api.post<RunResult>("/time-machine/run", clean(i)) });
  const save = useMutation({
    mutationFn: () => api.post<{ item: Item }>("/time-machine/scenarios", { title: title.trim() || "My scenario", inputs: clean(inp) }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["saved-research"] }); nav(`/time-machine/${r.item.id}`); },
  });
  const set = (p: Partial<Inputs>) => setInp((x) => ({ ...x, ...p }));
  const shock = (k: FactorKey, v: string) => set({ factor_shocks: { ...inp.factor_shocks, [k]: v === "" ? null : Number(v) } });
  const moves = Object.values(inp.factor_shocks).some((v) => v != null && v !== 0) || Object.keys(inp.asset_shocks).length > 0;
  // a valuation check works on its own; market moves need holdings to apply to
  const ready = moves ? inp.holdings === "mine" || inp.positions.length > 0 : !!inp.valuation?.symbol;

  return (
    <>
      <section className="card tm-card">
        <h2>1. Holdings</h2>
        <Seg value={inp.holdings} options={[{ value: "custom", label: "Enter holdings" }, { value: "mine", label: "My investments" }]} onChange={(v) => set({ holdings: v })} />
        {inp.holdings === "mine" ? (
          <p className="muted small">Uses your active investments in <Link to="/my-nexis/investments">My Nexis</Link>, valued in US dollars at the latest delayed prices.</p>
        ) : (
          <div className="tm-positions">
            {inp.positions.map((p, i) => (
              <div key={p.symbol} className="tm-row">
                <span className="tm-sym">{p.symbol}</span>
                <label className="sr-only" htmlFor={`tm-amt-${i}`}>Amount in {p.symbol}</label>
                <input id={`tm-amt-${i}`} className="input tm-num" type="number" min={1} step="any" value={p.amount}
                  onChange={(e) => set({ positions: inp.positions.map((x, j) => (j === i ? { ...x, amount: Number(e.target.value) } : x)) })} />
                <button type="button" className="btn ghost" aria-label={`Remove ${p.symbol}`} onClick={() => set({ positions: inp.positions.filter((_, j) => j !== i) })}>✕</button>
              </div>
            ))}
            {inp.positions.length < 30 && (
              <div className="tm-add"><SymbolSearch compact placeholder="Add a holding: stock, ETF, index, bond…" onPick={(s) => {
                if (!inp.positions.some((x) => x.symbol === s.symbol)) set({ positions: [...inp.positions, { symbol: s.symbol, amount: 10000 }] });
              }} /></div>
            )}
            <p className="muted xs">Amounts in any one currency (all the same). Only the proportions matter for the percentages.</p>
          </div>
        )}
      </section>

      <section className="card tm-card">
        <h2>2. The move</h2>
        <div className="tm-presets" role="group" aria-label="Example scenarios">
          {PRESETS.map((p) => <button key={p.label} type="button" className="chip" onClick={() => set({ factor_shocks: { ...p.shocks } })}>{p.label}</button>)}
        </div>
        <div className="tm-factors">
          {FACTORS.map((f) => (
            <label key={f.key} className="fld">
              <span>{f.label} <span className="muted">({f.unit === "bp" ? "basis points" : "%"})</span></span>
              <input className="input tm-num" type="number" step="any" inputMode="decimal" placeholder={f.hint} value={inp.factor_shocks[f.key] ?? ""} onChange={(e) => shock(f.key, e.target.value)} />
            </label>
          ))}
        </div>
        <ExplicitMoves inp={inp} set={set} />
        <div className="tm-window"><span className="wg-tool-label" id="tm-win">History used to measure sensitivity</span>
          <Seg value={inp.window_weeks} options={[{ value: 52, label: "1 year" }, { value: 104, label: "2 years" }, { value: 156, label: "3 years" }]} onChange={(v) => set({ window_weeks: v })} /></div>
      </section>

      <ValuationInput inp={inp} set={set} />

      <div className="tm-actions">
        <button type="button" className="btn primary" disabled={!ready || runQ.isPending} onClick={() => runQ.mutate(inp)}>{runQ.isPending ? "Calculating…" : "Run scenario"}</button>
        {!ready && <span className="muted small">Add holdings and at least one move (or a valuation change).</span>}
      </div>
      {runQ.error && <div className="banner error small" role="alert">{errorMessage(runQ.error)}</div>}
      {runQ.data && (
        <>
          <Results r={runQ.data} />
          <form className="row tm-save" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
            <label className="sr-only" htmlFor="tm-title">Scenario name</label>
            <input id="tm-title" className="input" placeholder="Name this scenario, e.g. Oil shock" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
            <button className="btn" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save scenario"}</button>
            {save.error && <span className="small neg" role="alert">{errorMessage(save.error)}</span>}
          </form>
        </>
      )}
    </>
  );
}

function clean(i: Inputs): Inputs {
  const fs = Object.fromEntries(Object.entries(i.factor_shocks).filter(([, v]) => v != null && v !== 0 && Number.isFinite(v))) as Inputs["factor_shocks"];
  return { ...i, factor_shocks: fs, positions: i.holdings === "custom" ? i.positions : [], valuation: i.valuation?.symbol ? i.valuation : null };
}

function ExplicitMoves({ inp, set }: { inp: Inputs; set: (p: Partial<Inputs>) => void }) {
  const entries = Object.entries(inp.asset_shocks);
  const symbols = inp.holdings === "custom" ? inp.positions.map((p) => p.symbol) : [];
  const [sym, setSym] = useState("");
  return (
    <details className="tm-explicit" open={entries.length > 0}>
      <summary>Set your own move for a holding</summary>
      <p className="muted small">Replaces the measured estimate for that holding — use it for company-specific news.</p>
      {entries.map(([s, v]) => (
        <div key={s} className="tm-row">
          <span className="tm-sym">{s}</span>
          <label className="sr-only" htmlFor={`tm-x-${s}`}>Move for {s} in percent</label>
          <input id={`tm-x-${s}`} className="input tm-num" type="number" step="any" value={v} onChange={(e) => set({ asset_shocks: { ...inp.asset_shocks, [s]: Number(e.target.value) } })} /> <span className="muted">%</span>
          <button type="button" className="btn ghost" aria-label={`Remove the move for ${s}`} onClick={() => { const n = { ...inp.asset_shocks }; delete n[s]; set({ asset_shocks: n }); }}>✕</button>
        </div>
      ))}
      <div className="row">
        {symbols.length ? (
          <>
            <label className="sr-only" htmlFor="tm-x-pick">Holding</label>
            <select id="tm-x-pick" className="input" value={sym} onChange={(e) => setSym(e.target.value)}>
              <option value="">Choose a holding…</option>
              {symbols.filter((s) => !(s in inp.asset_shocks)).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </>
        ) : (
          <>
            <label className="sr-only" htmlFor="tm-x-sym">Symbol</label>
            <input id="tm-x-sym" className="input" placeholder="Symbol, e.g. EMAAR.AE" value={sym} maxLength={32} onChange={(e) => setSym(e.target.value.toUpperCase())} />
          </>
        )}
        <button type="button" className="btn" disabled={!sym} onClick={() => { set({ asset_shocks: { ...inp.asset_shocks, [sym]: -10 } }); setSym(""); }}>Add move</button>
      </div>
    </details>
  );
}

function ValuationInput({ inp, set }: { inp: Inputs; set: (p: Partial<Inputs>) => void }) {
  const v = inp.valuation;
  const upd = (p: Partial<NonNullable<Inputs["valuation"]>>) => set({ valuation: { symbol: "", growth_change: 0, wacc_change: 0, terminal_growth_change: 0, ...v, ...p } });
  return (
    <details className="card tm-card tm-val" open={!!v?.symbol}>
      <summary><h2>3. Valuation check <span className="muted small">(optional)</span></h2></summary>
      <p className="muted small">Re-runs the <Link to="/valuation">valuation model</Link>'s DCF for one company with your changes, in percentage points.</p>
      {v?.symbol ? (
        <div className="row"><b>{v.symbol}</b><button type="button" className="btn ghost sm" onClick={() => set({ valuation: null })}>Remove</button></div>
      ) : <SymbolSearch compact placeholder="Company to value, e.g. EMAAR.AE" onPick={(s) => upd({ symbol: s.symbol })} />}
      {v?.symbol && (
        <div className="tm-factors">
          {([["growth_change", "Revenue/cash-flow growth", -10, 10], ["wacc_change", "Discount rate (WACC)", -5, 5], ["terminal_growth_change", "Long-run growth", -2, 2]] as const).map(([k, label, lo, hi]) => (
            <label key={k} className="fld"><span>{label} <span className="muted">(pp, {lo} to +{hi})</span></span>
              <input className="input tm-num" type="number" step="0.25" min={lo} max={hi} value={v[k]} onChange={(e) => upd({ [k]: Number(e.target.value) })} /></label>
          ))}
        </div>
      )}
    </details>
  );
}

// ---------------------------------------------------------------------------------------------- results

function Results({ r }: { r: RunResult }) {
  const o = r.output;
  const p = o.portfolio;
  const ccy = o.currency;
  return (
    <section className="card tm-card tm-results" aria-label="Scenario result">
      {o.lines.length > 0 && (
        <div className="tm-headline">
          <div>
            <div className="muted small">Estimated change in value</div>
            <div className={`tm-big ${p.change < 0 ? "neg" : p.change > 0 ? "pos" : ""}`}>{money(p.change, ccy)} <span className="tm-big-pct">({pct(p.change_pct)})</span></div>
            <div className="muted small">Wide range {money(p.wide_low, ccy)} to {money(p.wide_high, ccy)} · covers {num((p.covered_value / (p.value || 1)) * 100, 0)}% of {money(p.value, ccy).replace(/^\+/, "")}</div>
          </div>
        </div>
      )}
      {(o.missing_factors.length > 0 || o.unpriced.length > 0) && (
        <div className="banner warn small" role="status">
          {o.missing_factors.length > 0 && <>No price history was available for: {o.missing_factors.join(", ")} — those moves weren't applied. </>}
          {o.unpriced.length > 0 && <>Left out (no current price): {o.unpriced.join(", ")}.</>}
        </div>
      )}
      {o.lines.length > 0 && (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Result per holding">
          <table className="dt tm-table">
            <thead><tr><th>Holding</th><th className="r">Value</th><th>Method</th><th className="r">Impact</th><th className="r">95% range</th><th className="r">Change</th><th>How it was measured</th></tr></thead>
            <tbody>
              {o.lines.map((l) => (
                <tr key={l.symbol}>
                  <td><b>{l.symbol}</b>{l.name !== l.symbol && <div className="muted xs">{l.name}</div>}</td>
                  <td className="r">{num(l.value, 0)}</td>
                  <td>{METHOD[l.method]}</td>
                  <td className="r">{pct(l.pct)}</td>
                  <td className="r">{l.method === "factor" ? `${pct(l.low)} to ${pct(l.high)}` : "—"}</td>
                  <td className="r">{money(l.change, ccy)}</td>
                  <td className="tm-how">
                    {l.method === "factor" && l.stats && (
                      <span className="small">{Object.entries(l.stats.beta ?? {}).map(([k, b]) => `${FACTORS.find((f) => f.key === k)?.label ?? k}: β ${num(b, 2)} ± ${num((l.stats?.se?.[k] ?? 0) * 1.96, 2)}`).join(" · ")} · R² {num(l.stats.r2, 2)} · {l.stats.weeks} weeks ({l.stats.from} to {l.stats.to})</span>
                    )}
                    {l.method === "duration" && l.stats && <span className="small">Modified duration {num(l.stats.modified_duration, 2)}, convexity {num(l.stats.convexity, 1)}; assumes {l.stats.assumes}.</span>}
                    {l.flags.map((f) => <div key={f} className="tm-flag small">⚠ {f}</div>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {o.valuation && (
        <div className="tm-valres">
          <h3>Valuation check: {o.valuation.name}</h3>
          <p>DCF value per share <b>{num(o.valuation.before, 2)}</b> → <b>{num(o.valuation.after, 2)} {o.valuation.currency}</b> ({pct(o.valuation.change_pct)}){o.valuation.price != null && <> · market price {num(o.valuation.price, 2)}</>}</p>
          <p className="muted small">Growth {num(o.valuation.assumptions.growth_start[0] * 100, 2)}% → {num(o.valuation.assumptions.growth_start[1] * 100, 2)}% · discount rate {num(o.valuation.assumptions.wacc[0] * 100, 2)}% → {num(o.valuation.assumptions.wacc[1] * 100, 2)}% · long-run growth {num(o.valuation.assumptions.terminal_growth[0] * 100, 2)}% → {num(o.valuation.assumptions.terminal_growth[1] * 100, 2)}%</p>
          {o.valuation.flags.map((f) => <div key={f} className="tm-flag small">⚠ {f}</div>)}
        </div>
      )}
      <details className="tm-how-box">
        <summary>How this works and what it can't tell you</summary>
        <ul className="small">
          {o.notes.map((n) => <li key={n}>{n}</li>)}
          <li>Measured sensitivity: weekly (Friday-to-Friday) returns of each holding regressed on the weekly moves of the factors you shocked, over the window you chose. β is the measured move per unit of the factor; ± is the 95% margin.</li>
          <li>Data: delayed market prices{Object.values(r.snapshot.series).some(Boolean) && <> up to {Object.values(r.snapshot.series).filter(Boolean).map((s) => s!.last).sort().slice(-1)[0]}</>}. Model {r.model_version}, calculated {day(r.snapshot.computed_at)}.</li>
        </ul>
      </details>
    </section>
  );
}

// ---------------------------------------------------------------------------------------------- a saved scenario

function Saved({ id }: { id: number }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["tm-scenario", id], queryFn: () => api.get<{ item: Item; runs: StoredRun[]; latest: StoredRun | null }>(`/time-machine/scenarios/${id}`) });
  const [shown, setShown] = useState<number | null>(null);
  const [cmp, setCmp] = useState<[number, number] | null>(null);
  const run = useQuery({ queryKey: ["tm-run", shown], queryFn: () => api.get<StoredRun>(`/time-machine/runs/${shown}`), enabled: shown != null });
  const rerun = useMutation({
    mutationFn: () => api.post<StoredRun>(`/time-machine/scenarios/${id}/rerun`),
    onSuccess: (r) => { setShown(null); qc.invalidateQueries({ queryKey: ["tm-scenario", id] }); qc.setQueryData(["tm-run", r.id], r); },
  });
  const diff = useQuery({
    queryKey: ["tm-compare", cmp],
    queryFn: () => api.get<{ diff: { portfolio: { a: number | null; b: number | null; diff: number | null }; lines: { symbol: string; a: number | null; b: number | null; diff: number | null }[] } }>("/time-machine/compare", { a: cmp![0], b: cmp![1] }),
    enabled: !!cmp,
  });
  if (s.isLoading) return <Loading />;
  if (s.error) return <ErrorState error={s.error} />;
  const { item, runs, latest } = s.data!;
  const current = shown == null ? latest : run.data ?? null;
  return (
    <>
      <div className="tm-saved-head">
        <div><h2>{item.title}</h2><p className="muted small">{runs.length} run{runs.length === 1 ? "" : "s"} · each run keeps the data it used</p></div>
        <div className="row">
          <button type="button" className="btn primary" disabled={rerun.isPending} onClick={() => rerun.mutate()}>{rerun.isPending ? "Running…" : "Run again on today's data"}</button>
          <button type="button" className="btn" onClick={() => nav("/time-machine", { state: { inputs: item.state?.inputs } })}>Duplicate and edit</button>
          <Link className="btn ghost" to="/time-machine">All scenarios</Link>
        </div>
      </div>
      {rerun.error && <div className="banner error small" role="alert">{errorMessage(rerun.error)}</div>}
      <section className="card tm-card">
        <h3>Runs</h3>
        <ul className="tm-runs">
          {runs.map((r) => (
            <li key={r.id}>
              <button type="button" className={`linkish ${(current?.id ?? -1) === r.id ? "on" : ""}`} aria-current={(current?.id ?? -1) === r.id} onClick={() => setShown(r.id)}>{new Date(r.created_at).toLocaleString()}</button>
              <span className="muted small"> · {r.summary?.change_pct != null ? pct(r.summary.change_pct) : "—"} · model {r.model_version}</span>
            </li>
          ))}
        </ul>
        {runs.length >= 2 && <button type="button" className="btn sm" onClick={() => setCmp([runs[runs.length - 1].id, runs[0].id])}>Compare first and latest run</button>}
        {diff.data && (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Run comparison">
            <table className="dt">
              <thead><tr><th>Holding</th><th className="r">First run</th><th className="r">Latest run</th><th className="r">Difference</th></tr></thead>
              <tbody>
                <tr><td><b>Whole portfolio</b></td><td className="r">{pct(diff.data.diff.portfolio.a)}</td><td className="r">{pct(diff.data.diff.portfolio.b)}</td><td className="r">{diff.data.diff.portfolio.diff == null ? "—" : `${num(diff.data.diff.portfolio.diff, 2)} pts`}</td></tr>
                {diff.data.diff.lines.map((l) => <tr key={l.symbol}><td>{l.symbol}</td><td className="r">{pct(l.a)}</td><td className="r">{pct(l.b)}</td><td className="r">{l.diff == null ? "—" : `${num(l.diff, 2)} pts`}</td></tr>)}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {run.isLoading && shown != null ? <Loading /> : current?.output ? <Results r={current as RunResult} /> : null}
    </>
  );
}
