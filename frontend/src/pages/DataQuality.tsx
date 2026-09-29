import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, InfoTip, Kpi, PageHead, QueryView, Seg, StatusBadge } from "../components/ui";
import { INK, sequential, seriesColor } from "../components/charts";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int, pct } from "../utils/format";

export default function DataQuality() {
  const { datasetId } = useWorkspace();
  const theme = useResolvedTheme();
  const qc = useQueryClient();
  const [source, setSource] = useState<"ingestion" | "quality_run">("ingestion");
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dq = useQuery({ queryKey: ["dq", datasetId], queryFn: () => api.get<AnyObj>("/data-quality", { dataset_id: datasetId }) });
  const issues = useQuery({ queryKey: ["dq-issues", datasetId, source], queryFn: () => api.get<AnyObj[]>("/data-quality/issues", { dataset_id: datasetId, source, limit: 2000 }) });
  const cov = useQuery({ queryKey: ["coverage", datasetId], queryFn: () => api.get<AnyObj>("/market-data/coverage", { dataset_id: datasetId }) });

  const rerun = async () => {
    setRunning(true);
    setErr(null);
    try {
      await api.post("/data-quality/run", undefined, { dataset_id: datasetId });
      ["dq", "dq-issues", "notifications", "overview"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <PageHead title="Data Quality" desc="Completeness, validity, uniqueness, consistency and freshness of the stored dataset. Large moves are flagged as potential anomalies and are never deleted automatically."
        actions={<><button className="btn primary" onClick={rerun} disabled={running}>{running ? "Assessing…" : "Run quality check"}</button>{err && <span className="neg small">{err}</span>}</>} />
      <QueryView q={dq}>
        {(d) => {
          const l = d.latest;
          if (!l) return <Card title="No assessment yet"><p className="text2">Run a quality check to score this dataset.</p></Card>;
          const comps = Object.entries(l.components as Record<string, number>);
          return (
            <div className="stack">
              <div className="kpis">
                <Kpi label="Overall score" value={pct(l.overall_score)} metric="data_quality_score" note={<StatusBadge status={l.status} />} />
                {comps.map(([k, v]) => <Kpi key={k} label={k} value={pct(v)} tone={v < 0.98 ? "neg" : ""} />)}
                <Kpi label="Assessed" value={<span style={{ fontSize: 13 }}>{dt(l.created_at)}</span>} note={l.dataset_version} />
              </div>
              <div className="grid g2">
                <Card title="Checks" flush>
                  <DataTable<AnyObj> rows={l.checks} filterable={false} pageSize={20} columns={[
                    { key: "check", label: "Check", render: (r) => <span className="mono small">{r.check}</span> },
                    { key: "category", label: "Category" },
                    { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
                    { key: "count", label: "Count", align: "right", render: (r) => int(r.count) },
                    { key: "detail", label: "Detail", wrap: true },
                  ]} />
                </Card>
                <Card title="Score history" sub="per assessment">
                  <Plot height={250} data={[{
                    type: "scatter", mode: "lines+markers", x: [...d.history].reverse().map((h: AnyObj) => h.created_at), y: [...d.history].reverse().map((h: AnyObj) => h.overall_score),
                    line: { color: seriesColor(theme, 0), width: 2 }, marker: { size: 8 }, name: "score", hovertemplate: "%{y:.2%}<extra>%{x}</extra>",
                  }]} layout={{ yaxis: { tickformat: ".1%", range: [0.8, 1.005] }, showlegend: false, hovermode: "closest" }} />
                  <div className="small text2" style={{ marginTop: 6 }}>
                    Ingestion-time findings: {d.ingestion_issue_summary.map((s: AnyObj) => `${s.check} ${s.count} (${s.action})`).join(" · ") || "none"}
                  </div>
                </Card>
              </div>
              <Card title="Per-symbol completeness" flush>
                <DataTable<AnyObj> rows={l.per_symbol} exportName="dq-per-symbol" pageSize={15} initialSort={{ key: "completeness", dir: "asc" }} columns={[
                  { key: "symbol", label: "Symbol", render: (r) => <b className="mono">{r.symbol}</b> },
                  { key: "first_date", label: "First" }, { key: "last_date", label: "Last" },
                  { key: "observations", label: "Bars", align: "right", render: (r) => int(r.observations) },
                  { key: "expected", label: "Expected", align: "right", render: (r) => int(r.expected) },
                  { key: "missing_dates", label: "Missing", align: "right", render: (r) => <span className={r.missing_dates ? "neg" : ""}>{r.missing_dates}</span> },
                  { key: "missing_volume", label: "Null volume", align: "right" },
                  { key: "potential_anomalies", label: <>Potential anomalies <InfoTip text="Daily moves beyond 8× robust sigma (floor 15%). Flagged for review, not removed." /></>, align: "right" },
                  { key: "lag_vs_dataset_end_days", label: "Lag (bd)", align: "right", render: (r) => <span className={r.lag_vs_dataset_end_days > 5 ? "neg" : ""}>{r.lag_vs_dataset_end_days}</span> },
                  { key: "completeness", label: "Completeness", align: "right", render: (r) => pct(r.completeness) },
                ]} />
              </Card>
            </div>
          );
        }}
      </QueryView>
      <div className="stack" style={{ marginTop: 12 }}>
        <Card title="Record-level issues" actions={<Seg value={source} onChange={setSource} options={[{ value: "ingestion", label: "Ingestion validation" }, { value: "quality_run", label: "Latest quality run" }]} />} flush>
          <QueryView q={issues}>
            {(rows) => (
              <DataTable<AnyObj> rows={rows} exportName={`dq-issues-${source}`} pageSize={15} columns={[
                { key: "symbol", label: "Symbol", render: (r) => <span className="mono">{r.symbol ?? "–"}</span> },
                { key: "date", label: "Date" },
                { key: "check", label: "Check", render: (r) => <span className="mono small">{r.check}</span> },
                { key: "category", label: "Category" },
                { key: "severity", label: "Severity", render: (r) => <span className={`badge ${r.severity === "error" ? "bad" : r.severity === "warning" ? "warn" : "info"}`}>{r.severity}</span> },
                { key: "action", label: "Action" },
                { key: "detail", label: "Detail", wrap: true },
              ]} />
            )}
          </QueryView>
        </Card>
        <Card title="Monthly coverage heatmap" sub="share of dataset trading days with a bar, per symbol">
          <QueryView q={cov}>
            {(c) => (
              <Plot height={Math.max(300, c.symbols.length * 13 + 60)} data={[{
                type: "heatmap", z: c.coverage, x: c.months, y: c.symbols, zmin: 0, zmax: 1, colorscale: sequential(theme),
                hovertemplate: "%{y} %{x}: %{z:.0%}<extra></extra>", colorbar: { thickness: 10, tickformat: ".0%" }, xgap: 1, ygap: 1,
              }]} layout={{ hovermode: "closest", margin: { l: 60, r: 10, t: 8, b: 40 }, yaxis: { autorange: "reversed", tickfont: { size: 9, color: INK[theme].muted } } }} />
            )}
          </QueryView>
        </Card>
      </div>
    </>
  );
}
