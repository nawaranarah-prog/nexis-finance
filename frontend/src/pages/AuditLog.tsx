import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, PageHead, QueryView } from "../components/ui";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt } from "../utils/format";

export default function AuditLog() {
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: ["audit", action], queryFn: () => api.get<AnyObj[]>("/audit-log", { action, limit: 2000 }) });
  const actions = ["", "portfolio.created", "portfolio.modified", "portfolio.deleted", "dataset.imported", "market_data.refreshed", "file.imported",
    "backtest.executed", "ml_experiment.executed", "experiment.reproduced", "report.generated", "connection.connected", "connection.disconnected",
    "connection.synced", "connection.sync_failed", "data_quality.assessed", "api_key.created", "api_key.revoked", "webhook.created", "webhook.deleted"];
  return (
    <>
      <PageHead title="Research Audit Log" desc="Every meaningful action — imports, syncs, portfolio changes, backtests, experiments, reports, connections — with timestamp, actor and metadata. Secrets are redacted before anything is written."
        actions={<select className="input" style={{ width: 240 }} value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">{actions.map((a) => <option key={a} value={a}>{a || "all actions"}</option>)}</select>} />
      <Card title="Audit trail" flush>
        <QueryView q={q}>
          {(rows) => (
            <DataTable<AnyObj> rows={rows} pageSize={25} exportName="audit-log" columns={[
              { key: "created_at", label: "Time (UTC)", render: (r) => dt(r.created_at) }, { key: "actor", label: "User" },
              { key: "action", label: "Action", render: (r) => <span className="mono small">{r.action}</span> },
              { key: "object", label: "Object", value: (r) => `${r.object_type ?? ""}:${r.object_id ?? ""}`, render: (r) => <span className="small">{r.object_type} {r.object_id}</span> },
              { key: "details", label: "Metadata", wrap: true, value: (r) => JSON.stringify(r.details), render: (r) => <code className="xs">{JSON.stringify(r.details)}</code> },
            ]} empty="No audited actions yet." />
          )}
        </QueryView>
      </Card>
    </>
  );
}
