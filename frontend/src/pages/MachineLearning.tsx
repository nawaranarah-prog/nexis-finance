import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, download, Field, InfoTip, JobStatus, Kpi, PageHead, QueryView, StatusBadge } from "../components/ui";
import { AssetChecklist } from "../components/pickers";
import { dateAxis, INK, seriesColor } from "../components/charts";
import { line, PCT_AXIS } from "../components/metrics";
import { useAssets, useExperiment, useExperiments, useJobRunner } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, buildUrl } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int, num, pct, secs, spct } from "../utils/format";

const MODEL_LABEL: Record<string, string> = {
  naive_hist_vol: "Naive: trailing realised vol", ewma_vol: "Baseline: EWMA (λ=0.94)", ridge: "Ridge regression",
  random_forest: "Random forest", gradient_boosting: "Gradient boosting",
};

export default function MachineLearning() {
  const [params, setParams] = useSearchParams();
  const { datasetId } = useWorkspace();
  const exps = useExperiments("volatility_forecast", datasetId);
  const id = params.get("id") ? Number(params.get("id")) : exps.data?.[0]?.id ?? null;
  return (
    <>
      <PageHead title="Machine Learning" desc="Volatility forecasting with chronological validation and no-fit baselines. Regime classification and anomaly detection have their own pages." actions={
        <><Link className="btn" to="/regimes">Regime classification →</Link><Link className="btn" to="/anomalies">Anomaly detection →</Link></>} />
      <div className="grid g-side">
        <div className="stack">
          <NewExperiment onDone={(eid) => setParams({ id: String(eid) })} />
          <Card title="Volatility experiments" flush>
            <QueryView q={exps}>
              {(rows) => (
                <DataTable<AnyObj> rows={rows} pageSize={8} filterable={false} onRowClick={(r) => setParams({ id: String(r.id) })} selected={(r) => r.id === id} columns={[
                  { key: "code", label: "Code", render: (r) => <b className="mono">{r.code}</b> },
                  { key: "model", label: "Preferred", render: (r) => <span className="small">{r.model}</span> },
                  { key: "status", label: "", render: (r) => <StatusBadge status={r.status} /> },
                ]} empty="No experiments yet." />
              )}
            </QueryView>
          </Card>
        </div>
        <div className="stack">{id ? <VolResult id={id} /> : <Card title="No experiment"><div className="state">Run a volatility-forecasting experiment.</div></Card>}</div>
      </div>
    </>
  );
}

function NewExperiment({ onDone }: { onDone: (id: number) => void }) {
  const { datasetId, dataset } = useWorkspace();
  const assets = useAssets(datasetId);
  const catalog = useQuery({ queryKey: ["ml-catalog"], queryFn: () => api.get<AnyObj>("/ml/catalog"), staleTime: Infinity });
  const job = useJobRunner([["experiments"]]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [features, setFeatures] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>(["naive_hist_vol", "ewma_vol", "ridge", "random_forest", "gradient_boosting"]);
  const [f, setF] = useState({ horizon: 10, train_end: "", validation_end: "", seed: 42, min_improvement: 0.02, name: "" });
  useEffect(() => { if (catalog.data && !features.length) setFeatures(Object.keys(catalog.data.volatility_features)); }, [catalog.data, features.length]);
  useEffect(() => {
    if (dataset?.start_date && dataset.end_date && !f.train_end) {
      const s = new Date(dataset.start_date).getTime(), e = new Date(dataset.end_date).getTime();
      setF((x) => ({ ...x, train_end: new Date(s + (e - s) * 0.6).toISOString().slice(0, 10), validation_end: new Date(s + (e - s) * 0.8).toISOString().slice(0, 10) }));
    }
  }, [dataset, f.train_end]);
  useEffect(() => { if (assets.data && !symbols.length) setSymbols(assets.data.filter((a) => !a.is_benchmark).slice(0, 5).map((a) => a.symbol)); }, [assets.data, symbols.length]);
  const submit = async () => {
    const j = await job.run("/ml/experiments/volatility", { dataset_id: datasetId, symbols, features, models, horizon: f.horizon, train_end: f.train_end, validation_end: f.validation_end || null, seed: f.seed, min_improvement: f.min_improvement, name: f.name || undefined });
    if (j?.status === "succeeded" && j.result?.experiment_id) onDone(j.result.experiment_id);
  };
  return (
    <Card title="New volatility experiment">
      <div className="stack">
        <Field label="Assets (pooled panel, max 20)"><AssetChecklist assets={assets.data ?? []} value={symbols} onChange={setSymbols} includeBenchmarks={false} height={150} /></Field>
        <div className="form-grid">
          <Field label="Horizon (days)" hint="target = realised vol over t+1…t+h"><input className="input" type="number" min={1} max={63} value={f.horizon} onChange={(e) => setF({ ...f, horizon: Number(e.target.value) })} /></Field>
          <Field label="Seed"><input className="input" type="number" value={f.seed} onChange={(e) => setF({ ...f, seed: Number(e.target.value) })} /></Field>
          <Field label="Train end"><input className="input" type="date" value={f.train_end} onChange={(e) => setF({ ...f, train_end: e.target.value })} /></Field>
          <Field label="Validation end"><input className="input" type="date" value={f.validation_end} onChange={(e) => setF({ ...f, validation_end: e.target.value })} /></Field>
          <Field label="Min. improvement" hint="over best baseline RMSE"><input className="input" type="number" step={0.01} min={0} max={0.5} value={f.min_improvement} onChange={(e) => setF({ ...f, min_improvement: Number(e.target.value) })} /></Field>
        </div>
        <Field label="Models">
          <div className="stack" style={{ gap: 2 }}>{Object.keys(MODEL_LABEL).map((m) => <label key={m} className="check"><input type="checkbox" checked={models.includes(m)} onChange={() => setModels(models.includes(m) ? models.filter((x) => x !== m) : [...models, m])} />{MODEL_LABEL[m]}</label>)}</div>
        </Field>
        <Field label={<>Features <InfoTip text="Every feature at date t uses bars dated ≤ t only. Targets look forward and training rows whose target window overlaps the next split are purged." /></>}>
          <div className="checklist" style={{ maxHeight: 150 }}>
            {Object.entries((catalog.data?.volatility_features ?? {}) as Record<string, string>).map(([k, d]) => (
              <label key={k} title={d}><input type="checkbox" checked={features.includes(k)} onChange={() => setFeatures(features.includes(k) ? features.filter((x) => x !== k) : [...features, k])} /><span className="mono">{k}</span></label>
            ))}
          </div>
        </Field>
        <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="optional" /></Field>
        <button className="btn primary" onClick={submit} disabled={job.running || !symbols.length || !features.length || !models.length}>{job.running ? "Training…" : "Train and evaluate"}</button>
        <JobStatus job={job.job} error={job.error} running={job.running} />
      </div>
    </Card>
  );
}

function VolResult({ id }: { id: number }) {
  const theme = useResolvedTheme();
  const q = useExperiment(id);
  const [model, setModel] = useState("");
  const [symbol, setSymbol] = useState("");
  const e = q.data;
  const a = e?.artifacts;
  const sel = a?.selection;
  const m = model || sel?.preferred_model || "";
  const sym = symbol || (e?.config.symbols?.[0] ?? "");
  const preds = useQuery({
    queryKey: ["preds", id, m, sym], queryFn: () => api.get<AnyObj[]>(`/ml/experiments/${id}/predictions`, { model: m, symbol: sym }), enabled: !!m && !!sym, staleTime: Infinity,
  });
  const base = useQuery({
    queryKey: ["preds", id, sel?.best_baseline, sym], queryFn: () => api.get<AnyObj[]>(`/ml/experiments/${id}/predictions`, { model: sel?.best_baseline, symbol: sym }),
    enabled: !!sel?.best_baseline && !!sym && sel?.best_baseline !== m, staleTime: Infinity,
  });
  return (
    <QueryView q={q} label="Loading experiment">
      {() => {
        if (!a) return <Card title={e!.code}><div className="state">{e!.error ?? "No results stored."}</div></Card>;
        const sp = a.split;
        const models = a.models as string[];
        const fitted = Object.keys(a.explainability);
        const bestTree = fitted.includes("gradient_boosting") ? "gradient_boosting" : fitted.includes("random_forest") ? "random_forest" : fitted[0];
        const pim = a.explainability[sel.best_fitted_model ?? bestTree]?.permutation_importance ?? {};
        const pimRows = Object.entries(pim as Record<string, AnyObj>).sort((x, y) => y[1].mean - x[1].mean);
        const impModel = fitted.find((k) => a.explainability[k]?.impurity_importance);
        const imp = impModel ? a.explainability[impModel].impurity_importance : null;
        return (
          <>
            <Card title={<>{e!.name} <span className="badge">{e!.code}</span></>} sub={`${e!.dataset_version} · seed ${e!.seed} · ${secs(e!.duration_seconds)}`}
              actions={<><button className="btn sm" onClick={() => download(buildUrl(`/exports/experiments/${id}/predictions`))}>Predictions CSV</button><button className="btn sm" onClick={() => download(buildUrl(`/exports/experiments/${id}`))}>Metadata JSON</button><Link className="btn sm" to={`/experiments?id=${id}`}>Registry</Link></>}>
              <div className="small text2">Target: annualised realised volatility over the next {sp.horizon} trading days, pooled over {e!.config.symbols.join(", ")}.</div>
              <div className="row small" style={{ marginTop: 6, gap: 14 }}>
                <span><span className="dot" style={{ background: INK[theme].neutral }} /> Train {sp.train[0]} → {sp.train[1]} ({int(sp.rows.train)} rows)</span>
                {sp.validation && <span><span className="dot" style={{ background: seriesColor(theme, 3) }} /> Validation {sp.validation[0]} → {sp.validation[1]} ({int(sp.rows.validation)})</span>}
                <span><span className="dot" style={{ background: seriesColor(theme, 2) }} /> Test {sp.test[0]} → {sp.test[1]} ({int(sp.rows.test)})</span>
                <span className="muted">{int(sp.purged_rows)} rows purged at boundaries</span>
              </div>
            </Card>
            <div className={`banner ${sel.preferred_model === sel.best_baseline ? "neutral" : "info"} small`}>
              <b>Model output — preferred: {MODEL_LABEL[sel.preferred_model] ?? sel.preferred_model}.</b>&nbsp;
              Best fitted model ({sel.best_fitted_model}) changes validation RMSE by {spct(-(sel.rmse_improvement_vs_baseline ?? 0))} relative to the best baseline ({sel.best_baseline}); rule: {sel.rule} ({pct(sel.min_improvement, 0)}).
            </div>
            <Card title="Evaluation metrics" sub="chronological splits; test period never used for fitting or selection" flush>
              <div className="table-wrap">
                <table className="dt">
                  <thead>
                    <tr><th>Model</th>{["validation", "test"].flatMap((s) => [<th key={`${s}r`} className="r">{s} RMSE <InfoTip metric="rmse" /></th>, <th key={`${s}m`} className="r">{s} MAE</th>, <th key={`${s}2`} className="r">{s} R²</th>, <th key={`${s}q`} className="r">{s} QLIKE</th>])}<th className="r">train RMSE</th></tr>
                  </thead>
                  <tbody>
                    {models.map((mm) => {
                      const r = a.metrics[mm];
                      return (
                        <tr key={mm} className={mm === sel.preferred_model ? "sel" : ""}>
                          <td>{MODEL_LABEL[mm] ?? mm}{mm === sel.preferred_model && <span className="badge info" style={{ marginLeft: 6 }}>preferred</span>}</td>
                          {["validation", "test"].flatMap((s) => [<td key={`${s}r`} className="r">{num(r[s].rmse, 4)}</td>, <td key={`${s}m`} className="r">{num(r[s].mae, 4)}</td>, <td key={`${s}2`} className="r">{num(r[s].r2, 3)}</td>, <td key={`${s}q`} className="r">{num(r[s].qlike, 3)}</td>])}
                          <td className="r muted">{num(r.train.rmse, 4)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
            <Card title="Predicted vs realised volatility" sub="validation + test periods"
              actions={<>
                <select className="input sm" value={m} onChange={(ev) => setModel(ev.target.value)} aria-label="Model">{models.map((x) => <option key={x} value={x}>{MODEL_LABEL[x] ?? x}</option>)}</select>
                <select className="input sm" value={sym} onChange={(ev) => setSymbol(ev.target.value)} aria-label="Symbol">{(e!.config.symbols as string[]).map((s) => <option key={s}>{s}</option>)}</select>
              </>}>
              {preds.data ? (
                <Plot height={300} data={[
                  line(preds.data.map((r) => r.date), preds.data.map((r) => r.y_true), "Realised (target)", INK[theme].neutral),
                  line(preds.data.map((r) => r.date), preds.data.map((r) => r.y_pred), MODEL_LABEL[m] ?? m, seriesColor(theme, 0)),
                  ...(base.data ? [line(base.data.map((r) => r.date), base.data.map((r) => r.y_pred), `${MODEL_LABEL[sel.best_baseline]}`, seriesColor(theme, 1), { line: { width: 1.5, dash: "dot", color: seriesColor(theme, 1) } })] : []),
                ]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, shapes: sp.test ? [{ type: "rect", xref: "x", yref: "paper", x0: sp.test[0], x1: sp.test[1], y0: 0, y1: 1, fillcolor: theme === "dark" ? "rgba(25,158,112,0.08)" : "rgba(27,175,122,0.07)", line: { width: 0 }, layer: "below" }] : [] }} />
              ) : <div className="state">Loading predictions…</div>}
            </Card>
            <div className="grid g2">
              <Card title={`Permutation importance — ${sel.best_fitted_model ?? bestTree}`} sub="increase in test RMSE when the feature is shuffled (5 repeats)">
                <Plot height={Math.max(240, pimRows.length * 18 + 40)} data={[{
                  type: "bar", orientation: "h", y: pimRows.map((r) => r[0]), x: pimRows.map((r) => r[1].mean), error_x: { type: "data", array: pimRows.map((r) => r[1].std), color: INK[theme].muted, thickness: 1 },
                  marker: { color: seriesColor(theme, 0) }, hovertemplate: "%{y}: %{x:.5f}<extra></extra>",
                }]} layout={{ hovermode: "closest", yaxis: { autorange: "reversed", tickfont: { size: 10 } }, margin: { l: 110, r: 10, t: 8, b: 30 } }} />
              </Card>
              {imp ? (
                <Card title={`Impurity importance — ${impModel}`} sub="training-set split gain (biased toward high-cardinality features)">
                  <Plot height={Math.max(240, pimRows.length * 18 + 40)} data={[{
                    type: "bar", orientation: "h", y: Object.entries(imp as Record<string, number>).sort((x, y) => y[1] - x[1]).map((r) => r[0]),
                    x: Object.entries(imp as Record<string, number>).sort((x, y) => y[1] - x[1]).map((r) => r[1]), marker: { color: seriesColor(theme, 2) }, hovertemplate: "%{y}: %{x:.3f}<extra></extra>",
                  }]} layout={{ hovermode: "closest", yaxis: { autorange: "reversed", tickfont: { size: 10 } }, margin: { l: 110, r: 10, t: 8, b: 30 } }} />
                </Card>
              ) : <Card title="Impurity importance"><div className="state">Only available for random-forest models (histogram gradient boosting does not expose impurity importance).</div></Card>}
            </div>
            {a.partial_dependence?.features && (
              <Card title={`Partial dependence — ${a.partial_dependence.model}`} sub="average model output as one feature varies (test sample)">
                <div className="grid g3">
                  {Object.entries(a.partial_dependence.features as Record<string, AnyObj>).map(([feat, v], i) => (
                    <div key={feat}><div className="small text2 mono">{feat}</div>
                      <Plot height={200} data={[line(v.grid, v.average, feat, seriesColor(theme, i))]} layout={{ xaxis: { hoverformat: ".4f" }, yaxis: { tickformat: ".0%", hoverformat: ".2%" }, showlegend: false, hovermode: "closest", margin: { l: 44, r: 8, t: 6, b: 30 } }} />
                    </div>
                  ))}
                </div>
              </Card>
            )}
            {a.explainability.ridge?.standardized_coefficients && (
              <Card title="Ridge standardised coefficients" flush>
                <DataTable<AnyObj> rows={Object.entries(a.explainability.ridge.standardized_coefficients as Record<string, number>).map(([k, v]) => ({ feature: k, coef: v }))} filterable={false} initialSort={{ key: "coef", dir: "desc" }}
                  columns={[{ key: "feature", label: "Feature", render: (r) => <span className="mono">{r.feature}</span> }, { key: "coef", label: "Coefficient (per 1σ)", align: "right", render: (r) => num(r.coef, 5) }]} />
              </Card>
            )}
            <div className="grid g4">
              <Kpi label="Features" value={e!.features?.length ?? 0} />
              <Kpi label="Rows (train/val/test)" value={`${int(sp.rows.train)} / ${int(sp.rows.validation)} / ${int(sp.rows.test)}`} />
              <Kpi label="Created" value={<span style={{ fontSize: 13 }}>{dt(e!.created_at)}</span>} />
              <Kpi label="Reproducibility" value={<span style={{ fontSize: 13 }}>seed {e!.seed} · {e!.dataset_version}</span>} />
            </div>
          </>
        );
      }}
    </QueryView>
  );
}
