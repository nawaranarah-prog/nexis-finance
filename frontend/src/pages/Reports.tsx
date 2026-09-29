import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, download, Field, JobStatus, PageHead, QueryView } from "../components/ui";
import { PortfolioSelect } from "../components/pickers";
import { useExperiments, useJobRunner, usePortfolios } from "../hooks/queries";
import { useWorkspace } from "../hooks/workspace";
import { api, buildUrl } from "../services/api";
import type { AnyObj } from "../types/api";
import { bytes, dt } from "../utils/format";

export default function Reports() {
  const { datasetId, portfolioId } = useWorkspace();
  const ports = usePortfolios(datasetId);
  const bts = useQuery({ queryKey: ["backtests"], queryFn: () => api.get<AnyObj[]>("/backtests") });
  const exps = useExperiments(undefined, datasetId);
  const [pid, setPid] = useState<number | null>(portfolioId);
  const stress = useQuery({ queryKey: ["stress-tests", pid], queryFn: () => api.get<AnyObj[]>("/stress-tests", { portfolio_id: pid }), enabled: pid != null });
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api.get<AnyObj[]>("/reports") });
  const job = useJobRunner([["reports"]]);
  const [title, setTitle] = useState("Research Report");
  const [bt, setBt] = useState<number | null>(null);
  const [expIds, setExpIds] = useState<number[]>([]);
  const [stIds, setStIds] = useState<number[]>([]);
  const [notes, setNotes] = useState("");
  const toggle = (arr: number[], v: number, set: (x: number[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const mlExps = (exps.data ?? []).filter((e) => e.experiment_type !== "backtest" && e.status === "completed");
  const generate = async () => {
    const j = await job.run("/reports", { title, portfolio_id: pid, backtest_id: bt, experiment_ids: expIds, stress_test_ids: stIds, notes: notes || null });
    if (j?.status === "succeeded" && j.result?.report_id) download(buildUrl(`/reports/${j.result.report_id}/download`));
  };
  return (
    <>
      <PageHead title="Reports" desc="Assemble stored research results into a PDF: dataset and version, methodology, portfolio configuration, performance, risk, backtests, model outputs, limitations and reproducibility identifiers." />
      <div className="grid g2">
        <Card title="Report builder">
          <div className="stack">
            <Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} /></Field>
            <Field label="Portfolio (performance, risk, holdings)"><PortfolioSelect portfolios={ports.data ?? []} value={pid} onChange={(v) => { setPid(v); setStIds([]); }} allowEmpty="— none —" /></Field>
            <Field label="Backtest">
              <select className="input" value={bt ?? ""} onChange={(e) => setBt(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— none —</option>
                {(bts.data ?? []).map((b) => <option key={b.id} value={b.id}>{b.experiment_code} · {b.name}</option>)}
              </select>
            </Field>
            <Field label="Experiments (ML, walk-forward)">
              <div className="checklist" style={{ maxHeight: 150 }}>
                {mlExps.map((e) => <label key={e.id}><input type="checkbox" checked={expIds.includes(e.id)} onChange={() => toggle(expIds, e.id, setExpIds)} /><span className="mono">{e.code}</span><span className="small text2">{e.name}</span></label>)}
                {mlExps.length === 0 && <div className="state">No completed experiments.</div>}
              </div>
            </Field>
            {pid && (
              <Field label="Stress tests (hypothetical scenarios)">
                <div className="checklist" style={{ maxHeight: 130 }}>
                  {(stress.data ?? []).map((s) => <label key={s.id}><input type="checkbox" checked={stIds.includes(s.id)} onChange={() => toggle(stIds, s.id, setStIds)} />{s.name}</label>)}
                  {stress.data?.length === 0 && <div className="state">No saved scenarios for this portfolio.</div>}
                </div>
              </Field>
            )}
            <Field label="Analyst notes"><textarea className="input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
            <div className="form-actions">
              <button className="btn primary" onClick={generate} disabled={job.running || title.length < 3 || !(pid || bt || expIds.length || stIds.length)}>{job.running ? "Generating PDF…" : "Generate PDF report"}</button>
              <span className="xs muted">Reports use descriptive language only (research result, historical simulation, model output) — no recommendations.</span>
            </div>
            <JobStatus job={job.job} error={job.error} running={job.running} />
          </div>
        </Card>
        <Card title="Generated reports" flush>
          <QueryView q={reports}>
            {(rows) => (
              <DataTable<AnyObj> rows={rows} pageSize={12} filterable={false} columns={[
                { key: "created_at", label: "Created", render: (r) => dt(r.created_at) }, { key: "title", label: "Title" },
                { key: "sections", label: "Sections", render: (r) => <span className="small">{(r.sections ?? []).join(", ")}</span> },
                { key: "file_size", label: "Size", align: "right", render: (r) => bytes(r.file_size) },
                { key: "sha256", label: "SHA-256", render: (r) => <span className="mono xs" title={r.sha256}>{r.sha256.slice(0, 10)}</span> },
                { key: "dl", label: "", sortable: false, render: (r) => <button className="btn sm" onClick={() => download(buildUrl(`/reports/${r.id}/download`))}>Download PDF</button> },
              ]} empty="No reports yet." />
            )}
          </QueryView>
        </Card>
      </div>
      <Card title="Data exports" sub="CSV / JSON endpoints (also available per page)" className="" >
        <div className="small text2" style={{ lineHeight: 1.8 }}>
          <div><b>Experiment registry</b>: <a href={buildUrl("/exports/experiments")}>CSV</a> · <a href={buildUrl("/exports/experiments", { format: "json" })}>JSON</a></div>
          <div><b>Backtests</b>: trade logs, daily results and metrics from the Backtesting page (<code>/api/exports/backtests/&#123;id&#125;/trades|results</code>)</div>
          <div><b>Portfolio metrics & returns, risk metrics</b>: Portfolio Lab and Risk Analytics (<code>/api/exports/portfolios/&#123;id&#125;/metrics|returns</code>, <code>/api/exports/risk/&#123;id&#125;</code>)</div>
          <div><b>ML predictions and anomalies</b>: Machine Learning and Anomaly Detection pages (<code>/api/exports/experiments/&#123;id&#125;/predictions|anomalies</code>)</div>
          <div><b>API documentation</b>: <a href="/docs" onClick={(e) => { e.preventDefault(); window.open("http://127.0.0.1:8000/docs", "_blank", "noopener"); }}>OpenAPI / Swagger UI</a></div>
        </div>
      </Card>
    </>
  );
}
