import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Data } from "plotly.js";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Field, JobStatus, Kpi, PageHead, QueryView, StatusBadge } from "../components/ui";
import { AssetSelect, defaultBenchmark } from "../components/pickers";
import { dateAxis, INK, sequential, seriesColor } from "../components/charts";
import { useAssets, useExperiment, useExperiments, useJobRunner } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import type { AnyObj } from "../types/api";
import { num, pct } from "../utils/format";

export default function RegimeAnalysis() {
  const [params, setParams] = useSearchParams();
  const { datasetId, dataset } = useWorkspace();
  const assets = useAssets(datasetId);
  const exps = useExperiments("regime", datasetId);
  const id = params.get("id") ? Number(params.get("id")) : exps.data?.[0]?.id ?? null;
  const job = useJobRunner([["experiments"]]);
  const [f, setF] = useState({ bench: "", k: 4, method: "gmm", train_end: "", seed: 42 });
  useEffect(() => {
    if (dataset?.start_date && dataset.end_date && !f.train_end) {
      const s = new Date(dataset.start_date).getTime(), e = new Date(dataset.end_date).getTime();
      setF((x) => ({ ...x, train_end: new Date(s + (e - s) * 0.6).toISOString().slice(0, 10) }));
    }
  }, [dataset, f.train_end]);
  const bench = f.bench || defaultBenchmark(assets.data);
  const submit = async () => {
    const j = await job.run("/ml/experiments/regime", { dataset_id: datasetId, benchmark_symbol: bench, n_regimes: f.k, method: f.method, train_end: f.train_end, seed: f.seed });
    if (j?.status === "succeeded") setParams({ id: String(j.result!.experiment_id) });
  };
  return (
    <>
      <PageHead title="Regime Analysis" desc="Unsupervised clustering of market-state features. The model is fitted on the training window only; later dates are classified with the frozen model." />
      <div className="grid g-side">
        <div className="stack">
          <Card title="Train a regime model">
            <div className="stack">
              <div className="form-grid">
                <Field label="Market series"><AssetSelect assets={assets.data ?? []} value={bench} onChange={(v) => setF({ ...f, bench: v })} benchmarksOnly /></Field>
                <Field label="Regimes (k)"><input className="input" type="number" min={2} max={8} value={f.k} onChange={(e) => setF({ ...f, k: Number(e.target.value) })} /></Field>
                <Field label="Method"><select className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}><option value="gmm">Gaussian mixture</option><option value="kmeans">K-means</option></select></Field>
                <Field label="Seed"><input className="input" type="number" value={f.seed} onChange={(e) => setF({ ...f, seed: Number(e.target.value) })} /></Field>
                <Field label="Train end"><input className="input" type="date" value={f.train_end} onChange={(e) => setF({ ...f, train_end: e.target.value })} /></Field>
              </div>
              <button className="btn primary" onClick={submit} disabled={job.running}>{job.running ? "Fitting…" : "Fit and classify"}</button>
              <JobStatus job={job.job} error={job.error} running={job.running} />
            </div>
          </Card>
          <Card title="Regime experiments" flush>
            <QueryView q={exps}>
              {(rows) => <DataTable<AnyObj> rows={rows} filterable={false} pageSize={8} onRowClick={(r) => setParams({ id: String(r.id) })} selected={(r) => r.id === id} columns={[
                { key: "code", label: "Code", render: (r) => <b className="mono">{r.code}</b> }, { key: "model", label: "Method" },
                { key: "k", label: "k", align: "right", value: (r) => r.config.n_regimes }, { key: "status", label: "", render: (r) => <StatusBadge status={r.status} /> },
              ]} empty="No regime models yet." />}
            </QueryView>
          </Card>
        </div>
        <div className="stack">{id ? <RegimeResult id={id} /> : <Card title="No model"><div className="state">Train a regime model.</div></Card>}</div>
      </div>
    </>
  );
}

function RegimeResult({ id }: { id: number }) {
  const theme = useResolvedTheme();
  const q = useExperiment(id);
  return (
    <QueryView q={q}>
      {(e) => {
        const a = e.artifacts;
        if (!a) return <Card title={e.code}><div className="state">{e.error}</div></Card>;
        const order: string[] = a.order;
        const color = (r: string) => seriesColor(theme, order.indexOf(r));
        const t = a.timeline;
        const timeline: Data[] = [{ type: "scatter", mode: "lines", x: t.dates, y: t.benchmark, name: "Market series", line: { width: 1, color: INK[theme].neutral }, showlegend: false, hoverinfo: "skip" }];
        order.forEach((r) => {
          timeline.push({ type: "scatter", mode: "markers", name: r, x: t.dates.filter((_: string, i: number) => t.regime[i] === r), y: t.benchmark.filter((_: number, i: number) => t.regime[i] === r),
            marker: { size: 4, color: color(r) }, hovertemplate: `%{x}<br>%{y:.1f}<extra>${r}</extra>` });
        });
        const trainEnd = a.train_period[1];
        const ev = a.evaluation_vs_synthetic_truth;
        return (
          <>
            <div className="kpis">
              <Kpi label={`Latest regime (${a.latest.date})`} value={<span style={{ fontSize: 14 }}>{a.latest.regime}</span>} note="descriptive label, not a forecast" />
              <Kpi label="Posterior probability" value={pct(a.latest.probabilities[a.latest.regime], 1)} />
              <Kpi label="Method" value={a.method.toUpperCase()} note={`k = ${a.n_regimes}, seed ${e.seed}`} />
              <Kpi label="Training window" value={<span style={{ fontSize: 13 }}>{a.train_period[0]} → {a.train_period[1]}</span>} />
              {ev && <Kpi label="ARI vs synthetic truth" value={num(ev.adjusted_rand_index, 3)} tip={ev.note} />}
            </div>
            <div className="banner neutral small">Labels are derived from each cluster's mean features (volatility above/below the training median; momentum sign). They are conventions of this model and have no universal financial meaning.</div>
            <Card title="Regime timeline" sub="market series coloured by classified regime; shaded = out-of-sample classification">
              <Plot height={360} data={timeline} layout={{ hovermode: "closest", xaxis: dateAxis(theme), legend: { orientation: "h", y: -0.12, yanchor: "top" }, margin: { l: 52, r: 16, t: 30, b: 40 },
                shapes: [{ type: "rect", xref: "x", yref: "paper", x0: trainEnd, x1: t.dates[t.dates.length - 1], y0: 0, y1: 1, fillcolor: theme === "dark" ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.035)", line: { width: 0 }, layer: "below" }] }} />
            </Card>
            <Card title="Regime probabilities" sub="posterior membership per day">
              <Plot height={260} data={order.map((r) => ({ type: "scatter", mode: "lines", stackgroup: "p", name: r, x: t.dates, y: a.probabilities[r], line: { width: 0.5, color: color(r) }, fillcolor: color(r) }) as Data)}
                layout={{ xaxis: dateAxis(theme, false), yaxis: { tickformat: ".0%", range: [0, 1] }, hovermode: "x unified", legend: { orientation: "h", y: -0.15, yanchor: "top" }, margin: { l: 52, r: 16, t: 10, b: 40 } }} />
            </Card>
            <div className="grid g2">
              <Card title="Frequency and persistence" flush>
                <DataTable<AnyObj> rows={a.regime_stats} filterable={false} columns={[
                  { key: "regime", label: "Regime", render: (r) => <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}><span className="dot" style={{ background: color(r.regime) }} />{r.regime}</span> },
                  { key: "days", label: "Days", align: "right" }, { key: "frequency", label: "Share", align: "right", render: (r) => pct(r.frequency, 1) },
                  { key: "average_duration_days", label: "Avg run (d)", align: "right", render: (r) => num(r.average_duration_days, 1) },
                  { key: "fwd", label: "Next-day mkt return (ex-post)", align: "right", value: (r) => r.next_day_benchmark_return_mean, render: (r) => `${pct(r.next_day_benchmark_return_mean, 3)} ± ${pct(r.next_day_benchmark_return_std, 2)}` },
                ]} />
              </Card>
              <Card title="Empirical transition matrix" sub="P(regime tomorrow | regime today)">
                <Plot height={260} data={[{ type: "heatmap", z: a.transition_matrix.matrix, x: order, y: order, zmin: 0, zmax: 1, colorscale: sequential(theme),
                  text: a.transition_matrix.matrix.map((row: number[]) => row.map((v) => v.toFixed(3))), texttemplate: "%{text}", hovertemplate: "from %{y}<br>to %{x}: %{z:.3f}<extra></extra>", xgap: 2, ygap: 2, colorbar: { thickness: 10 } }]}
                  layout={{ hovermode: "closest", yaxis: { autorange: "reversed", tickfont: { size: 9.5 } }, xaxis: { tickfont: { size: 9.5 } }, margin: { l: 190, r: 10, t: 8, b: 110 } }} />
              </Card>
            </div>
            <Card title="Feature distributions by regime">
              <div className="grid g3">
                {a.features.map((feat: string) => (
                  <div key={feat}>
                    <div className="small text2 mono">{feat}</div>
                    <Plot height={260} data={order.map((r) => ({ type: "box", name: r.split(" · ").map((w: string) => w.split(" ")[0]).join("/"), y: a.feature_series[feat].filter((_: number, i: number) => t.regime[i] === r),
                      marker: { color: color(r), size: 2 }, line: { width: 1.5 }, boxpoints: false, hovertemplate: `${r}<br>%{y:.4f}<extra></extra>` }) as Data)}
                      layout={{ showlegend: false, hovermode: "closest", margin: { l: 44, r: 8, t: 6, b: 50 }, xaxis: { tickfont: { size: 9 } } }} />
                  </div>
                ))}
              </div>
            </Card>
            {a.model_selection.length > 0 && (
              <Card title="Model selection (training data)" sub="lower BIC/AIC = better fit penalised for complexity">
                <Plot height={220} data={[
                  { type: "scatter", mode: "lines+markers", name: "BIC", x: a.model_selection.map((b: AnyObj) => b.k), y: a.model_selection.map((b: AnyObj) => b.bic), line: { color: seriesColor(theme, 0), width: 2 }, marker: { size: 8 } },
                  { type: "scatter", mode: "lines+markers", name: "AIC", x: a.model_selection.map((b: AnyObj) => b.k), y: a.model_selection.map((b: AnyObj) => b.aic), line: { color: seriesColor(theme, 1), width: 2 }, marker: { size: 8 } },
                ]} layout={{ xaxis: { title: { text: "k" }, dtick: 1 }, hovermode: "x unified" }} />
              </Card>
            )}
            {ev && (
              <Card title="Evaluation against synthetic ground truth" sub="possible only because the data are generated" flush>
                <DataTable<AnyObj> rows={Object.entries(ev.contingency as Record<string, Record<string, number>>).map(([truth, row]) => ({ truth, ...row }))} filterable={false}
                  columns={[{ key: "truth", label: "True regime" }, ...order.map((r) => ({ key: r, label: r, align: "right" as const, render: (x: AnyObj) => x[r] ?? 0 }))]} />
              </Card>
            )}
          </>
        );
      }}
    </QueryView>
  );
}
