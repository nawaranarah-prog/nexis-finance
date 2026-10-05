import { askConfirm } from "../components/dialog";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, download, Field, JobStatus, PageHead, QueryView, StatusBadge } from "../components/ui";
import { useExperiment, useExperiments, useJobRunner } from "../hooks/queries";
import { api, buildUrl } from "../services/api";
import type { AnyObj, Experiment } from "../types/api";
import { dt, fmtMetric, secs } from "../utils/format";

const TYPES = ["", "backtest", "walk_forward", "volatility_forecast", "regime", "anomaly"];
const PAGE_FOR: Record<string, (e: Experiment) => string> = {
  backtest: (e) => `/backtesting?id=${e.summary?.backtest_id ?? ""}`,
  walk_forward: (e) => `/backtesting?wf=${e.id}`,
  volatility_forecast: (e) => `/machine-learning?id=${e.id}`,
  regime: (e) => `/regimes?id=${e.id}`,
  anomaly: (e) => `/anomalies?id=${e.id}`,
};

export default function Experiments() {
  const [params, setParams] = useSearchParams();
  const [type, setType] = useState("");
  const [cmp, setCmp] = useState<{ a: number | null; b: number | null }>({ a: null, b: null });
  const list = useExperiments(type || undefined);
  const id = params.get("id") ? Number(params.get("id")) : null;
  return (
    <>
      <PageHead title="Research Experiments" desc="Registry of every research run with its dataset version, configuration, seed and metrics. Any completed run can be reproduced from its stored configuration."
        actions={<>
          <select className="input" style={{ width: 200 }} value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">{TYPES.map((t) => <option key={t} value={t}>{t ? t.replace("_", " ") : "all types"}</option>)}</select>
          <button className="btn" onClick={() => download(buildUrl("/exports/experiments"))}>Export registry CSV</button>
        </>} />
      <div className="stack">
        <Card title="Registry" flush>
          <QueryView q={list}>
            {(rows) => (
              <DataTable<Experiment> rows={rows} pageSize={12} onRowClick={(e) => setParams({ id: String(e.id) })} selected={(e) => e.id === id} exportName="experiments-view" columns={[
                { key: "code", label: "ID", render: (e) => <b className="mono">{e.code}</b> },
                { key: "experiment_type", label: "Type" }, { key: "name", label: "Name" },
                { key: "dataset_version", label: "Dataset", render: (e) => <span className="mono small">{e.dataset_version}</span> },
                { key: "model", label: "Model / strategy" }, { key: "seed", label: "Seed", align: "right", render: (e) => e.seed ?? "–" },
                { key: "status", label: "Status", render: (e) => <StatusBadge status={e.status} /> },
                { key: "parent_id", label: "Reproduces", render: (e) => (e.parent_id ? <span className="small">#{e.parent_id}</span> : "") },
                { key: "duration_seconds", label: "Duration", align: "right", render: (e) => secs(e.duration_seconds) },
                { key: "created_at", label: "Created", render: (e) => dt(e.created_at) },
                { key: "cmp", label: "Compare", sortable: false, render: (e) => (
                  <span className="row" style={{ gap: 4 }} onClick={(ev) => ev.stopPropagation()}>
                    <button className={`btn sm ${cmp.a === e.id ? "primary" : ""}`} onClick={() => setCmp({ ...cmp, a: e.id })}>A</button>
                    <button className={`btn sm ${cmp.b === e.id ? "primary" : ""}`} onClick={() => setCmp({ ...cmp, b: e.id })}>B</button>
                  </span>) },
              ]} empty="No experiments yet." />
            )}
          </QueryView>
        </Card>
        {cmp.a && cmp.b && <Compare a={cmp.a} b={cmp.b} />}
        {id && <Detail id={id} onDeleted={() => setParams({})} />}
      </div>
    </>
  );
}

function Detail({ id, onDeleted }: { id: number; onDeleted: () => void }) {
  const q = useExperiment(id);
  const qc = useQueryClient();
  const repro = useJobRunner([["experiments"]]);
  const [notes, setNotes] = useState("");
  useEffect(() => setNotes(q.data?.notes ?? ""), [q.data]);
  const saveNotes = async () => { await api.patch(`/experiments/${id}`, { notes }); qc.invalidateQueries({ queryKey: ["experiment", id] }); qc.invalidateQueries({ queryKey: ["experiments"] }); };
  const remove = async () => {
    if (!(await askConfirm({ title: "Delete this experiment?", body: "Its stored metrics, predictions and trades are deleted too.", confirm: "Delete", danger: true }))) return;
    await api.del(`/experiments/${id}`); qc.invalidateQueries({ queryKey: ["experiments"] }); qc.invalidateQueries({ queryKey: ["backtests"] }); onDeleted();
  };
  return (
    <QueryView q={q}>
      {(e) => (
        <div className="grid g2">
          <Card title={<>{e.code} <StatusBadge status={e.status} /></>} sub={e.name} actions={<>
            {PAGE_FOR[e.experiment_type] && <Link className="btn sm" to={PAGE_FOR[e.experiment_type](e)}>Open results</Link>}
            <button className="btn sm primary" disabled={e.status !== "completed" || repro.running} onClick={() => repro.run(`/experiments/${id}/reproduce`, {})}>{repro.running ? "Reproducing…" : "Reproduce experiment"}</button>
            <button className="btn sm" onClick={() => download(buildUrl(`/exports/experiments/${id}`))}>JSON</button>
            <button className="btn sm" onClick={() => download(buildUrl(`/exports/experiments/${id}`, { format: "csv" }))}>Metrics CSV</button>
            <button className="btn sm danger" onClick={remove}>Delete</button>
          </>}>
            <div className="stack">
              <dl className="kv">
                <dt>Type</dt><dd>{e.experiment_type}</dd>
                <dt>Dataset version</dt><dd className="mono">{e.dataset_version}</dd>
                <dt>Dataset hash</dt><dd className="mono xs">{e.dataset_hash}</dd>
                <dt>Model / strategy</dt><dd>{e.model ?? "–"}</dd>
                <dt>Random seed</dt><dd>{e.seed ?? "– (deterministic)"}</dd>
                <dt>Train</dt><dd>{e.train_start ?? "–"} → {e.train_end ?? "–"}</dd>
                <dt>Test</dt><dd>{e.test_start ?? "–"} → {e.test_end ?? "–"}</dd>
                <dt>Features</dt><dd className="small">{e.features?.join(", ") ?? "–"}</dd>
                <dt>Duration</dt><dd>{secs(e.duration_seconds)}</dd>
                <dt>Created</dt><dd>{dt(e.created_at)}</dd>
                {e.parent_id && <><dt>Reproduction of</dt><dd><Link to={`/experiments?id=${e.parent_id}`}>#{e.parent_id}</Link></dd></>}
                {e.error && <><dt>Error</dt><dd className="neg">{e.error}</dd></>}
              </dl>
              {e.reproducibility && <ReproBox r={e.reproducibility} />}
              {repro.job?.status === "succeeded" && repro.job.result && <ReproBox r={repro.job.result.reproducibility} newCode={repro.job.result.experiment_code} />}
              <JobStatus job={repro.job?.status === "succeeded" ? null : repro.job} error={repro.error} running={repro.running} />
              <Field label="Notes"><textarea className="input" rows={3} value={notes} onChange={(ev) => setNotes(ev.target.value)} /></Field>
              <div><button className="btn sm" onClick={saveNotes} disabled={notes === (e.notes ?? "")}>Save notes</button></div>
              <Field label="Stored configuration (used by Reproduce)"><pre className="pre">{JSON.stringify(e.config, null, 2)}</pre></Field>
            </div>
          </Card>
          <Card title="Metrics" sub="experiment_metrics rows" flush>
            <DataTable<AnyObj> rows={e.metrics} pageSize={40} exportName={`${e.code}-metrics`} columns={[
              { key: "split", label: "Split" }, { key: "model", label: "Model", render: (m) => m.model ?? "–" },
              { key: "metric", label: "Metric", render: (m) => <span className="mono small">{m.metric}</span> },
              { key: "value", label: "Value", align: "right", render: (m) => fmtMetric(m.metric, m.value) },
            ]} />
          </Card>
        </div>
      )}
    </QueryView>
  );
}

function ReproBox({ r, newCode }: { r: AnyObj; newCode?: string }) {
  return (
    <div className={`banner ${r.matches ? "info" : "warn"} small`}>
      <div>
        <b>{newCode ? `Reproduced as ${newCode}` : `Reproduction of ${r.original_experiment}`}:</b> {r.matches ? "results match" : "results differ"} —
        {" "}{r.metrics_compared} metrics compared, max |Δ| {r.max_abs_difference == null ? "n/a" : r.max_abs_difference.toExponential(2)} (tolerance {r.tolerance}).
        Dataset {r.dataset_unchanged ? "unchanged" : `changed (${r.original_dataset_version} → ${r.current_dataset_version})`}.
        {r.largest_differences?.length > 0 && <div className="xs">Largest: {r.largest_differences.map((d: AnyObj) => `${d.split}/${d.metric} ${d.abs_diff.toExponential(1)}`).join(", ")}</div>}
      </div>
    </div>
  );
}

function Compare({ a, b }: { a: number; b: number }) {
  const q = useQuery({ queryKey: ["compare", a, b], queryFn: () => api.get<AnyObj>("/experiments/compare", { a, b }) });
  const [onlyDiff, setOnlyDiff] = useState(true);
  return (
    <QueryView q={q}>
      {(c) => (
        <Card title={`Compare ${c.a.code} (A) vs ${c.b.code} (B)`} sub={`${c.same_type ? "same type" : "different types"} · ${c.same_dataset ? "same dataset content" : "different dataset content"}`}
          actions={<label className="check"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> only differing metrics</label>}>
          <div className="grid g2">
            <DataTable<AnyObj> rows={c.metrics.filter((m: AnyObj) => !onlyDiff || m.difference == null || Math.abs(m.difference) > 1e-12)} pageSize={20} exportName="experiment-comparison" columns={[
              { key: "split", label: "Split" }, { key: "model", label: "Model", render: (m) => m.model ?? "–" }, { key: "metric", label: "Metric", render: (m) => <span className="mono small">{m.metric}</span> },
              { key: "a", label: "A", align: "right", render: (m) => fmtMetric(m.metric, m.a) }, { key: "b", label: "B", align: "right", render: (m) => fmtMetric(m.metric, m.b) },
              { key: "difference", label: "B − A", align: "right", render: (m) => fmtMetric(m.metric.includes("ratio") ? m.metric : `${m.metric}_difference`, m.difference) },
            ]} />
            <div className="stack">
              <div className="small text2">Configuration differences</div>
              <DataTable<AnyObj> rows={c.config_differences} filterable={false} pageSize={20} columns={[
                { key: "key", label: "Key", render: (d) => <span className="mono small">{d.key}</span> },
                { key: "a", label: "A", wrap: true, render: (d) => <code className="xs">{JSON.stringify(d.a)}</code> },
                { key: "b", label: "B", wrap: true, render: (d) => <code className="xs">{JSON.stringify(d.b)}</code> },
              ]} empty="Identical configurations." />
              <div className="xs muted">{c.note}</div>
            </div>
          </div>
        </Card>
      )}
    </QueryView>
  );
}
