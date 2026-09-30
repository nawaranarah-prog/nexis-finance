import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, PageHead, QueryView, Seg } from "../components/ui";
import { DataClassBadge } from "../components/connect";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int } from "../utils/format";

export default function Lineage() {
  const [params, setParams] = useSearchParams();
  const q = useQuery({ queryKey: ["lineage"], queryFn: () => api.get<AnyObj>("/lineage") });
  const batch = params.get("batch") ? Number(params.get("batch")) : null;
  const tx = params.get("transaction");
  const rec = params.get("record");
  return (
    <>
      <PageHead title="Data Lineage" desc="Every imported record keeps its provenance: source, file hash, import time, original row and the canonical ID it became. Market datasets show provider, retrieval, coverage and transformations." />
      {tx && <TraceCard path={`/lineage/transaction/${tx}`} />}
      {rec && <TraceCard path={`/lineage/source-record/${rec}`} />}
      <QueryView q={q} label="Loading lineage">
        {(l) => (
          <div className="stack">
            <Card title="Import batches" sub="files and API pulls" flush>
              <DataTable<AnyObj> rows={l.import_batches} pageSize={10} onRowClick={(b) => setParams({ batch: String(b.id) })} selected={(b) => b.id === batch} columns={[
                { key: "id", label: "Batch", align: "right" }, { key: "created_at", label: "Imported", render: (b) => dt(b.created_at) },
                { key: "source", label: "Source" }, { key: "file", label: "File", render: (b) => <span className="small">{b.file ?? "API"}</span> },
                { key: "kind", label: "Kind" }, { key: "data_class", label: "Data", render: (b) => <DataClassBadge value={b.data_class} sample={b.is_sample} /> },
                { key: "imported", label: "Imported", align: "right", render: (b) => int(b.imported) },
                { key: "rejected", label: "Rejected", align: "right", render: (b) => <span className={b.rejected ? "neg" : ""}>{b.rejected}</span> },
                { key: "duplicates", label: "Dupes", align: "right" },
                { key: "sha256", label: "SHA-256", render: (b) => <span className="mono xs" title={b.sha256}>{b.sha256?.slice(0, 10) ?? "–"}</span> },
              ]} empty="No imports yet." />
            </Card>
            {batch && <BatchRecords id={batch} />}
            <Card title="Market & research datasets" flush>
              <DataTable<AnyObj> rows={l.datasets} filterable={false} columns={[
                { key: "code", label: "Dataset", render: (d) => <b className="mono">{d.code}</b> }, { key: "provider", label: "Provider" },
                { key: "synthetic", label: "Data", render: (d) => <DataClassBadge value={d.synthetic ? "synthetic" : d.provider === "csv_upload" ? "user_imported" : "real_external"} /> },
                { key: "version", label: "Version" }, { key: "coverage", label: "Coverage", value: (d) => `${d.coverage[0]} → ${d.coverage[1]}` },
                { key: "records", label: "Bars", align: "right", render: (d) => int(d.records) },
                { key: "updated_at", label: "Retrieved / updated", render: (d) => dt(d.updated_at) },
                { key: "experiments_using", label: "Experiments using", align: "right" },
                { key: "transformations", label: "Transformations", wrap: true, render: (d) => <span className="xs text2">{d.transformations.join(" → ")}</span> },
              ]} />
            </Card>
            <div className="grid g2">
              <Card title="Recent ingestion runs" flush>
                <DataTable<AnyObj> rows={l.ingestion_runs} pageSize={8} filterable={false} columns={[
                  { key: "id", label: "#", align: "right" }, { key: "started_at", label: "Started", render: (r) => dt(r.started_at) }, { key: "provider", label: "Provider" },
                  { key: "status", label: "Status" }, { key: "inserted", label: "Inserted", align: "right" }, { key: "rejected", label: "Rejected", align: "right" }, { key: "version", label: "Version" },
                ]} />
              </Card>
              <Card title="Recent sync runs" flush>
                <DataTable<AnyObj> rows={l.sync_runs} pageSize={8} filterable={false} columns={[
                  { key: "id", label: "#", align: "right" }, { key: "started_at", label: "Started", render: (r) => dt(r.started_at) }, { key: "connection", label: "Connection" },
                  { key: "status", label: "Status" }, { key: "added", label: "Added", align: "right" }, { key: "updated", label: "Updated", align: "right" },
                ]} empty="No syncs yet." />
              </Card>
            </div>
          </div>
        )}
      </QueryView>
    </>
  );
}

function BatchRecords({ id }: { id: number }) {
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["lineage-batch", id, status], queryFn: () => api.get<AnyObj>(`/lineage/batches/${id}`, { status }) });
  return (
    <QueryView q={q}>
      {(b) => (
        <Card title={`Batch #${id} — ${b.batch.source} (${b.batch.kind})`} sub={`${b.batch.file ?? "API"} · imported ${dt(b.batch.imported_at)}`}
          actions={<Seg value={status} onChange={setStatus} options={[{ value: "", label: "All" }, { value: "imported", label: "Imported" }, { value: "rejected", label: "Rejected" }, { value: "duplicate", label: "Duplicate" }]} />} flush>
          <div className="small text2" style={{ padding: "8px 14px" }}>Column mapping: <code className="xs">{JSON.stringify(Object.fromEntries(Object.entries(b.batch.mapping ?? {}).filter(([, v]) => v)))}</code></div>
          <DataTable<AnyObj> rows={b.records} pageSize={15} exportName={`batch-${id}-records`} columns={[
            { key: "row", label: "Original row", align: "right" },
            { key: "status", label: "Outcome", render: (r) => <span className={`badge ${r.status === "imported" ? "good" : r.status === "rejected" ? "bad" : "warn"}`}>{r.status}</span> },
            { key: "normalized_ref", label: "Normalised as", render: (r) => <span className="mono">{r.normalized_ref ?? "–"}</span> },
            { key: "message", label: "Message", wrap: true, render: (r) => <span className="small">{r.message ?? ""}</span> },
            { key: "raw", label: "Original record", wrap: true, value: (r) => JSON.stringify(r.raw), render: (r) => <code className="xs">{Object.entries(r.raw).filter(([, v]) => v !== "").map(([k, v]) => `${k}=${v}`).join(" · ")}</code> },
          ]} />
        </Card>
      )}
    </QueryView>
  );
}

function TraceCard({ path }: { path: string }) {
  const q = useQuery({ queryKey: ["lineage-rec", path], queryFn: () => api.get<AnyObj>(path) });
  return (
    <div style={{ marginBottom: 12 }}>
      <QueryView q={q}>
        {(t) => (
          <Card title={`Lineage of ${t.normalized_id ?? `source record #${t.record_id}`}`}>
            <dl className="kv">
              <dt>Source</dt><dd>{t.source} ({t.source_type})</dd><dt>File</dt><dd>{t.file ?? "API"} <span className="mono xs">{t.sha256?.slice(0, 16)}</span></dd>
              <dt>Imported</dt><dd>{dt(t.imported_at)} UTC</dd><dt>Original record</dt><dd>row {t.original_row}</dd>
              <dt>Data class</dt><dd><DataClassBadge value={t.data_class} /></dd>
              <dt>Raw values</dt><dd><code className="xs">{JSON.stringify(t.original_record)}</code></dd>
            </dl>
          </Card>
        )}
      </QueryView>
    </div>
  );
}
