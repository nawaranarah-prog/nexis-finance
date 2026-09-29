import { useQuery } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, Kpi, PageHead, QueryView, StatusBadge } from "../components/ui";
import { api } from "../services/api";
import type { AnyObj, Job } from "../types/api";
import { dt, int, pct, secs } from "../utils/format";

export default function SystemHealth() {
  const h = useQuery({ queryKey: ["system-health"], queryFn: () => api.get<AnyObj>("/system/health"), refetchInterval: 15_000 });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.get<Job[]>("/jobs", { limit: 50 }), refetchInterval: 15_000 });
  return (
    <>
      <PageHead title="System Health" desc="API and database status, dataset freshness, record counts, job history and request latency (refreshes every 15 s)." />
      <QueryView q={h} label="Checking system">
        {(d) => (
          <div className="stack">
            <div className="kpis">
              <Kpi label="API" value={<StatusBadge status={d.api} />} note={`v${d.version} · ${d.environment}`} />
              <Kpi label="Database" value={<StatusBadge status={d.database.ok ? "ok" : "error"} />} note={`${d.database.engine} · ${d.database.latency_ms} ms`} />
              <Kpi label="Uptime" value={secs(d.requests.uptime_seconds)} />
              <Kpi label="Requests recorded" value={int(d.requests.requests_recorded)} note={`${d.requests.server_errors} server errors`} />
              <Kpi label="Running jobs" value={int(d.running_jobs)} />
              <Kpi label="Failed ingestions" value={int(d.failed_ingestions)} tone={d.failed_ingestions ? "neg" : ""} />
              <Kpi label="Avg ingestion" value={secs(d.ingestion_durations?.avg_seconds)} note={`max ${secs(d.ingestion_durations?.max_seconds)}`} />
            </div>
            <div className="grid g2">
              <Card title="Datasets and freshness" flush>
                <DataTable<AnyObj> rows={d.datasets} filterable={false} columns={[
                  { key: "code", label: "Dataset", render: (r) => <b className="mono">{r.code}</b> }, { key: "version", label: "Version" },
                  { key: "end_date", label: "Latest bar" },
                  { key: "business_days_since_end", label: "Age (bd)", align: "right", render: (r) => (r.is_synthetic ? <span className="muted" title={r.freshness_note}>static</span> : r.business_days_since_end) },
                  { key: "records", label: "Records", align: "right", render: (r) => int(r.records) },
                  { key: "dq", label: "DQ", value: (r) => r.dq_score, render: (r) => (r.dq_status ? <><StatusBadge status={r.dq_status} /> {pct(r.dq_score, 1)}</> : "–") },
                ]} />
              </Card>
              <Card title="Record counts" flush>
                <DataTable<AnyObj> rows={Object.entries(d.record_counts as Record<string, number>).map(([t, n]) => ({ table: t, rows: n }))} filterable={false} pageSize={20}
                  columns={[{ key: "table", label: "Table", render: (r) => <span className="mono">{r.table}</span> }, { key: "rows", label: "Rows", align: "right", render: (r) => int(r.rows) }]} />
              </Card>
              <Card title="Request latency" sub="in-memory, since process start" flush>
                <DataTable<AnyObj> rows={d.requests.routes} filterable={false} pageSize={12} columns={[
                  { key: "route", label: "Route", render: (r) => <span className="mono xs">{r.route}</span> }, { key: "count", label: "Count", align: "right" },
                  { key: "p50_ms", label: "p50 ms", align: "right" }, { key: "p95_ms", label: "p95 ms", align: "right" }, { key: "max_ms", label: "max ms", align: "right" },
                ]} empty="No requests recorded yet." />
              </Card>
              <Card title="Research run durations" flush>
                <DataTable<AnyObj> rows={d.experiment_durations} filterable={false} columns={[
                  { key: "type", label: "Type" }, { key: "count", label: "Runs", align: "right" },
                  { key: "avg_seconds", label: "Average", align: "right", render: (r) => secs(r.avg_seconds) }, { key: "max_seconds", label: "Max", align: "right", render: (r) => secs(r.max_seconds) },
                ]} />
              </Card>
              <Card title="Recent ingestions" flush>
                <DataTable<AnyObj> rows={d.recent_ingestions} filterable={false} columns={[
                  { key: "id", label: "#", align: "right" }, { key: "provider", label: "Provider" }, { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
                  { key: "records_inserted", label: "Inserted", align: "right", render: (r) => int(r.records_inserted) },
                  { key: "records_rejected", label: "Rejected", align: "right" }, { key: "duration_seconds", label: "Duration", align: "right", render: (r) => secs(r.duration_seconds) },
                  { key: "started_at", label: "Started", render: (r) => dt(r.started_at) },
                ]} />
              </Card>
              <Card title="Failed jobs" flush>
                <DataTable<AnyObj> rows={d.failed_jobs} filterable={false} columns={[
                  { key: "created_at", label: "When", render: (r) => dt(r.created_at) }, { key: "job_type", label: "Type" }, { key: "error", label: "Error", wrap: true, render: (r) => <span className="neg small">{r.error}</span> },
                ]} empty="No failed jobs." />
              </Card>
            </div>
          </div>
        )}
      </QueryView>
      <Card title="Job history" flush className="">
        <QueryView q={jobs}>
          {(rows) => (
            <DataTable<Job> rows={rows} pageSize={10} columns={[
              { key: "created_at", label: "Queued", render: (j) => dt(j.created_at) }, { key: "job_type", label: "Type" },
              { key: "status", label: "Status", render: (j) => <StatusBadge status={j.status} /> },
              { key: "progress", label: "Progress", align: "right", render: (j) => pct(j.progress, 0) },
              { key: "duration", label: "Duration", align: "right", value: (j) => (j.finished_at && j.started_at ? (new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000 : null), render: (j) => secs(j.finished_at && j.started_at ? (new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000 : null) },
              { key: "result", label: "Result", render: (j) => <span className="small mono">{j.result?.experiment_code ?? j.result?.file_name ?? ""}</span> },
              { key: "error", label: "Error", wrap: true, render: (j) => <span className="neg small">{j.error ?? ""}</span> },
            ]} />
          )}
        </QueryView>
      </Card>
    </>
  );
}
