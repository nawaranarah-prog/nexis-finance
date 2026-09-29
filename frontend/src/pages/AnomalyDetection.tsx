import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { Data } from "plotly.js";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, download, Field, JobStatus, Kpi, PageHead, QueryView, StatusBadge } from "../components/ui";
import { dateAxis, INK, seriesColor, STATUS } from "../components/charts";
import { line } from "../components/metrics";
import { useExperiment, useExperiments, useJobRunner } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, buildUrl } from "../services/api";
import type { AnyObj } from "../types/api";
import { int, num, pct, spct } from "../utils/format";

export default function AnomalyDetection() {
  const [params, setParams] = useSearchParams();
  const { datasetId } = useWorkspace();
  const exps = useExperiments("anomaly", datasetId);
  const id = params.get("id") ? Number(params.get("id")) : exps.data?.[0]?.id ?? null;
  const job = useJobRunner([["experiments"]]);
  const [f, setF] = useState({ contamination: 0.002, z: 6, seed: 42 });
  const submit = async () => {
    const j = await job.run("/ml/experiments/anomaly", { dataset_id: datasetId, contamination: f.contamination, z_threshold: f.z, seed: f.seed });
    if (j?.status === "succeeded") setParams({ id: String(j.result!.experiment_id) });
  };
  return (
    <>
      <PageHead title="Anomaly Detection" desc="Isolation Forest compared with a transparent rolling z-score baseline. Flags are unusual observations — not evidence of wrongdoing — and are split into data-quality versus market-behaviour anomalies." />
      <div className="grid g-side">
        <div className="stack">
          <Card title="New scan">
            <div className="stack">
              <div className="form-grid">
                <Field label="IF contamination" hint="share of observations flagged"><input className="input" type="number" step={0.0005} min={0.0001} max={0.05} value={f.contamination} onChange={(e) => setF({ ...f, contamination: Number(e.target.value) })} /></Field>
                <Field label="z-score threshold"><input className="input" type="number" step={0.5} min={3} max={20} value={f.z} onChange={(e) => setF({ ...f, z: Number(e.target.value) })} /></Field>
                <Field label="Seed"><input className="input" type="number" value={f.seed} onChange={(e) => setF({ ...f, seed: Number(e.target.value) })} /></Field>
              </div>
              <button className="btn primary" onClick={submit} disabled={job.running}>{job.running ? "Scanning…" : "Scan universe"}</button>
              <JobStatus job={job.job} error={job.error} running={job.running} />
            </div>
          </Card>
          <Card title="Scans" flush>
            <QueryView q={exps}>
              {(rows) => <DataTable<AnyObj> rows={rows} filterable={false} pageSize={8} onRowClick={(r) => setParams({ id: String(r.id) })} selected={(r) => r.id === id} columns={[
                { key: "code", label: "Code", render: (r) => <b className="mono">{r.code}</b> },
                { key: "flags", label: "IF / z flags", align: "right", value: (r) => `${r.summary?.flagged_by_method?.isolation_forest ?? "–"} / ${r.summary?.flagged_by_method?.rolling_zscore ?? "–"}` },
                { key: "status", label: "", render: (r) => <StatusBadge status={r.status} /> },
              ]} empty="No scans yet." />}
            </QueryView>
          </Card>
        </div>
        <div className="stack">{id ? <ScanResult id={id} /> : <Card title="No scan"><div className="state">Run a scan.</div></Card>}</div>
      </div>
    </>
  );
}

function ScanResult({ id }: { id: number }) {
  const theme = useResolvedTheme();
  const { datasetId } = useWorkspace();
  const e = useExperiment(id);
  const [method, setMethod] = useState("isolation_forest");
  const [category, setCategory] = useState("");
  const [picked, setPicked] = useState<AnyObj | null>(null);
  const rows = useQuery({ queryKey: ["anomalies", id], queryFn: () => api.get<AnyObj[]>(`/ml/experiments/${id}/anomalies`), staleTime: Infinity });
  const filtered = useMemo(() => (rows.data ?? []).filter((r) => (!method || r.method === method) && (!category || r.category === category)), [rows.data, method, category]);
  const around = picked ? { start: shift(picked.date, -45), end: shift(picked.date, 45) } : null;
  const ctx = useQuery({
    queryKey: ["bars-ctx", datasetId, picked?.symbol, around?.start],
    queryFn: () => api.get<AnyObj>("/market-data", { symbol: picked!.symbol, dataset_id: datasetId, start: around!.start, end: around!.end }),
    enabled: !!picked,
  });
  return (
    <QueryView q={e}>
      {(x) => {
        const a = x.artifacts;
        if (!a) return <Card title={x.code}><div className="state">{x.error}</div></Card>;
        const s = a.summary, ev = a.evaluation_vs_injected;
        const catColor = (c: string) => (c === "data_quality" ? STATUS.serious : seriesColor(theme, 0));
        const scatter: Data[] = ["market_behaviour", "data_quality"].map((c) => {
          const pts = filtered.filter((r) => r.category === c);
          return { type: "scatter", mode: "markers", name: c.replace("_", " "), x: pts.map((r) => r.features.ret_z), y: pts.map((r) => r.features.volume_z),
            text: pts.map((r) => `${r.symbol} ${r.date}`), marker: { size: 9, color: catColor(c), symbol: c === "data_quality" ? "diamond" : "circle", line: { width: 1.5, color: INK[theme].surface } },
            hovertemplate: "%{text}<br>return z %{x:.1f}<br>volume z %{y:.1f}<extra></extra>" } as Data;
        });
        return (
          <>
            <div className="kpis">
              <Kpi label="Observations scanned" value={int(s.observations)} note={`${s.symbols} assets · ${s.period[0]} → ${s.period[1]}`} />
              <Kpi label="Isolation Forest flags" value={int(s.flagged_by_method.isolation_forest)} note={`contamination ${a.parameters.contamination}`} />
              <Kpi label="z-score flags" value={int(s.flagged_by_method.rolling_zscore)} note={`|z| > ${a.parameters.z_threshold}`} />
              <Kpi label="Flagged by both" value={int(s.flagged_by_both)} />
              <Kpi label="Data-quality / market" value={`${s.data_quality_flags} / ${s.market_behaviour_flags}`} />
            </div>
            {ev && (
              <Card title="Detection of injected synthetic events" sub={`${ev.injected_events_in_sample} controlled events in sample`} flush>
                <DataTable<AnyObj> rows={Object.entries(ev.methods as Record<string, AnyObj>).map(([m, v]) => ({ method: m, ...v }))} filterable={false} columns={[
                  { key: "method", label: "Method" }, { key: "flagged", label: "Flagged", align: "right" },
                  { key: "true_positives", label: "Injected found", align: "right" }, { key: "recall", label: "Recall", align: "right", render: (r) => pct(r.recall, 1) },
                  { key: "precision_vs_injected", label: "Precision vs injected", align: "right", render: (r) => pct(r.precision_vs_injected, 1) },
                  { key: "missed", label: "Missed events", wrap: true, render: (r) => <span className="xs mono">{r.missed.join(", ") || "none"}</span> },
                ]} />
                <div className="xs muted" style={{ padding: "6px 14px" }}>{ev.note}</div>
              </Card>
            )}
            <div className="grid g2">
              <Card title="Isolation Forest score distribution" sub="higher = more isolated; dashed = flag threshold">
                <Plot height={240} data={[{ type: "bar", x: a.score_histogram.edges.slice(0, -1).map((v: number, i: number) => (v + a.score_histogram.edges[i + 1]) / 2), y: a.score_histogram.counts, marker: { color: seriesColor(theme, 0) }, hovertemplate: "%{x:.3f}: %{y}<extra></extra>" }]}
                  layout={{ yaxis: { type: "log", title: { text: "count (log)" } }, bargap: 0.05, hovermode: "closest", showlegend: false,
                    shapes: a.score_histogram.threshold ? [{ type: "line", x0: a.score_histogram.threshold, x1: a.score_histogram.threshold, yref: "paper", y0: 0, y1: 1, line: { dash: "dash", color: STATUS.critical, width: 1.5 } }] : [] }} />
              </Card>
              <Card title="Return z vs volume z of flagged points" sub="colour & shape = category">
                <Plot height={240} data={scatter} layout={{ hovermode: "closest", xaxis: { title: { text: "return z-score" } }, yaxis: { title: { text: "log-volume z-score" } } }} onClick={(pt) => { const r = filtered.find((f) => `${f.symbol} ${f.date}` === (pt as AnyObj).text); if (r) setPicked(r); }} />
              </Card>
            </div>
            <Card title="Flagged observations" flush actions={<button className="btn sm" onClick={() => download(buildUrl(`/exports/experiments/${id}/anomalies`))}>Export CSV</button>}>
              <QueryView q={rows}>
                {() => (
                  <DataTable<AnyObj> rows={filtered} exportName={`${x.code}-anomalies-view`} pageSize={12} onRowClick={setPicked} selected={(r) => r.id === picked?.id}
                    toolbar={<>
                      <select className="input sm" style={{ width: 150 }} value={method} onChange={(ev2) => setMethod(ev2.target.value)} aria-label="Method"><option value="">both methods</option><option value="isolation_forest">Isolation Forest</option><option value="rolling_zscore">rolling z-score</option></select>
                      <select className="input sm" style={{ width: 150 }} value={category} onChange={(ev2) => setCategory(ev2.target.value)} aria-label="Category"><option value="">all categories</option><option value="market_behaviour">market behaviour</option><option value="data_quality">data quality</option></select>
                    </>}
                    columns={[
                      { key: "date", label: "Date" }, { key: "symbol", label: "Asset", render: (r) => <b className="mono">{r.symbol}</b> },
                      { key: "score", label: "Score", align: "right", render: (r) => num(r.score, 3) },
                      { key: "severity", label: "Severity", render: (r) => <span className={`badge ${r.severity === "critical" ? "bad" : r.severity === "high" ? "warn" : ""}`}>{r.severity}</span> },
                      { key: "category", label: "Category", render: (r) => <span className="row" style={{ gap: 5, flexWrap: "nowrap" }}><span className="dot" style={{ background: catColor(r.category) }} />{r.category.replace("_", " ")}</span> },
                      { key: "top_feature", label: "Top feature", render: (r) => <span className="mono small">{r.top_feature}</span> },
                      { key: "ret", label: "Return", align: "right", value: (r) => r.features.return, render: (r) => spct(r.features.return) },
                      { key: "ret_z", label: "ret z", align: "right", value: (r) => r.features.ret_z, render: (r) => num(r.features.ret_z, 1) },
                      { key: "volume_z", label: "vol z", align: "right", value: (r) => r.features.volume_z, render: (r) => num(r.features.volume_z, 1) },
                      { key: "range_z", label: "range z", align: "right", value: (r) => r.features.range_z, render: (r) => num(r.features.range_z, 1) },
                      { key: "inj", label: "Injected?", value: (r) => r.matches_injected_event, render: (r) => (r.matches_injected_event == null ? "–" : r.matches_injected_event ? <span className="badge info">yes</span> : "") },
                    ]} />
                )}
              </QueryView>
            </Card>
            {picked && (
              <Card title={`${picked.symbol} around ${picked.date}`} sub={picked.features.category_reason}>
                {ctx.data ? (
                  <div className="grid g2">
                    <Plot height={220} data={[
                      line(ctx.data.bars.map((b: AnyObj) => b.date), ctx.data.bars.map((b: AnyObj) => b.close), "Close", seriesColor(theme, 0)),
                      { type: "scatter", mode: "markers", x: [picked.date], y: [ctx.data.bars.find((b: AnyObj) => b.date === picked.date)?.close], name: "Flagged bar", marker: { size: 12, color: STATUS.critical, symbol: "circle-open", line: { width: 2.5 } } },
                    ]} layout={{ xaxis: dateAxis(theme, false), hovermode: "x unified" }} />
                    <Plot height={220} data={[{ type: "bar", x: ctx.data.bars.map((b: AnyObj) => b.date), y: ctx.data.bars.map((b: AnyObj) => b.volume), marker: { color: ctx.data.bars.map((b: AnyObj) => (b.date === picked.date ? STATUS.critical : seriesColor(theme, 0))) }, name: "Volume" }]}
                      layout={{ xaxis: dateAxis(theme, false), yaxis: { tickformat: ".2s" }, showlegend: false }} />
                  </div>
                ) : <div className="state">Loading context…</div>}
              </Card>
            )}
          </>
        );
      }}
    </QueryView>
  );
}

function shift(date: string, days: number): string {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}
