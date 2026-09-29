import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, Field, PageHead, QueryView, Seg, StatusBadge } from "../components/ui";
import { useAssets } from "../hooks/queries";
import { useWorkspace } from "../hooks/workspace";
import { api, errorMessage } from "../services/api";
import type { AnyObj, Asset, Dataset } from "../types/api";
import { dt, int, num, secs } from "../utils/format";

export default function MarketData() {
  const { datasets, datasetId, setDatasetId } = useWorkspace();
  const assets = useAssets(datasetId);
  const [symbol, setSymbol] = useState("");
  const runs = useQuery({ queryKey: ["ingestion-runs"], queryFn: () => api.get<AnyObj[]>("/ingestion-runs") });
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api.get<AnyObj>("/system/config"), staleTime: Infinity });
  const bars = useQuery({
    queryKey: ["bars", datasetId, symbol],
    queryFn: () => api.get<AnyObj>("/market-data", { symbol, dataset_id: datasetId, limit: 500 }),
    enabled: !!symbol && datasetId != null,
  });

  return (
    <>
      <PageHead title="Market Data" desc="Datasets, ingestion history and stored OHLCV bars. Every provider's output passes the same validation pipeline before storage." />
      <div className="stack">
        <Card title="Datasets" sub="versioned research universes" flush>
          <DataTable<Dataset>
            rows={datasets} filterable={false} onRowClick={(d) => setDatasetId(d.id)} selected={(d) => d.id === datasetId}
            columns={[
              { key: "code", label: "Code", render: (d) => <b className="mono">{d.code}</b> },
              { key: "name", label: "Name" },
              { key: "mode", label: "Mode", value: (d) => d.mode_label, render: (d) => <span className={`badge ${d.is_synthetic ? "synthetic" : "info"}`}>{d.mode_label}</span> },
              { key: "version_label", label: "Version" },
              { key: "range", label: "Date range", value: (d) => `${d.start_date} → ${d.end_date}` },
              { key: "asset_count", label: "Assets", align: "right" },
              { key: "record_count", label: "Records", align: "right", render: (d) => int(d.record_count) },
              { key: "content_hash", label: "Content hash", render: (d) => <span className="mono xs" title={d.content_hash ?? ""}>{d.content_hash?.slice(0, 12)}</span> },
              { key: "updated_at", label: "Updated", render: (d) => dt(d.updated_at) },
            ]}
          />
        </Card>

        <div className="grid g2">
          <IngestPanel publicEnabled={!!cfg.data?.public_provider_enabled} datasets={datasets} />
          <Card title="Ingestion history" sub="latest 50 runs" flush>
            <QueryView q={runs}>
              {(rows) => (
                <DataTable<AnyObj> rows={rows} pageSize={8} filterable={false} columns={[
                  { key: "id", label: "#", align: "right" },
                  { key: "dataset_version", label: "Dataset version" },
                  { key: "provider", label: "Provider" },
                  { key: "mode", label: "Mode" },
                  { key: "status", label: "Status", render: (r) => <StatusBadge status={r.status} /> },
                  { key: "records_received", label: "Received", align: "right", render: (r) => int(r.records_received) },
                  { key: "records_inserted", label: "Inserted", align: "right", render: (r) => int(r.records_inserted) },
                  { key: "records_rejected", label: "Rejected", align: "right", render: (r) => <span className={r.records_rejected ? "neg" : ""}>{int(r.records_rejected)}</span> },
                  { key: "duplicates", label: "Dupes", align: "right" },
                  { key: "duration_seconds", label: "Duration", align: "right", render: (r) => secs(r.duration_seconds) },
                  { key: "started_at", label: "Started", render: (r) => dt(r.started_at) },
                  { key: "errors", label: "Errors", wrap: true, render: (r) => <span className="neg small">{(r.errors ?? []).join("; ")}</span> },
                ]} />
              )}
            </QueryView>
          </Card>
        </div>

        <Card title="Assets" sub="click a row to inspect stored bars" flush>
          <QueryView q={assets}>
            {(rows) => (
              <DataTable<Asset> rows={rows} exportName="assets" pageSize={15} onRowClick={(a) => setSymbol(a.symbol)} selected={(a) => a.symbol === symbol}
                columns={[
                  { key: "symbol", label: "Symbol", render: (a) => <b className="mono">{a.symbol}</b> },
                  { key: "name", label: "Name" },
                  { key: "asset_type", label: "Type" },
                  { key: "sector", label: "Sector", render: (a) => a.sector ?? "–" },
                  { key: "is_benchmark", label: "Benchmark", render: (a) => (a.is_benchmark ? <span className="badge info">benchmark</span> : "") },
                  { key: "first_date", label: "First bar" },
                  { key: "last_date", label: "Last bar" },
                  { key: "observations", label: "Bars", align: "right", render: (a) => int(a.observations) },
                  { key: "go", label: "", sortable: false, render: (a) => <Link to={`/asset-research?symbol=${a.symbol}`} onClick={(e) => e.stopPropagation()}>research →</Link> },
                ]} />
            )}
          </QueryView>
        </Card>

        {symbol && (
          <Card title={`Stored bars — ${symbol}`} sub="latest 500, as persisted after validation" flush>
            <QueryView q={bars}>
              {(b) => (
                <DataTable<AnyObj> rows={[...b.bars].reverse()} exportName={`${symbol}-bars`} pageSize={20} columns={[
                  { key: "date", label: "Date" },
                  ...(["open", "high", "low", "close", "adj_close"] as const).map((k) => ({ key: k, label: k.replace("_", " "), align: "right" as const, render: (r: AnyObj) => num(r[k], 2) })),
                  { key: "volume", label: "Volume", align: "right", render: (r) => (r.volume == null ? <span className="muted">null</span> : int(r.volume)) },
                ]} />
              )}
            </QueryView>
          </Card>
        )}
      </div>
    </>
  );
}

function IngestPanel({ publicEnabled, datasets }: { publicEnabled: boolean; datasets: Dataset[] }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<"synthetic" | "public" | "csv">("synthetic");
  const syn = datasets.find((d) => d.is_synthetic);
  const [form, setForm] = useState({ code: syn?.code ?? "SYN-MULTI-DEMO", name: syn?.name ?? "Nexis Synthetic Multi-Sector Universe", end: "", seed: 42, symbols: "SPY,QQQ,IWM,TLT,GLD,XLK,XLF,XLE", bench: "SPY", start: "2018-01-01", ingestMode: "incremental" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (k: string, v: string | number) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setBusy(true);
    setMsg(null);
    try {
      let run: AnyObj;
      if (mode === "csv") {
        if (!file) throw new Error("choose a CSV file first");
        const fd = new FormData();
        fd.append("file", file);
        fd.append("dataset_code", form.code);
        fd.append("dataset_name", form.name);
        if (form.bench) fd.append("benchmark_symbol", form.bench);
        run = await api.upload<AnyObj>("/market-data/upload", fd);
      } else {
        const body: AnyObj = { provider: mode, dataset_code: form.code, dataset_name: form.name, mode: form.ingestMode, end: form.end || undefined };
        if (mode === "synthetic") body.seed = Number(form.seed);
        else {
          body.symbols = form.symbols.split(",").map((s) => s.trim()).filter(Boolean);
          body.benchmark_symbols = form.bench ? [form.bench] : undefined;
          body.start = form.start || undefined;
        }
        run = await api.post<AnyObj>("/market-data/ingest", body);
      }
      setMsg({ ok: true, text: `Run #${run.id} ${run.status}: received ${int(run.records_received)}, inserted ${int(run.records_inserted)}, rejected ${run.records_rejected}, duplicates ${run.duplicates} → ${run.dataset_version}` });
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e) });
    } finally {
      setBusy(false);
      ["datasets", "ingestion-runs", "assets", "notifications"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    }
  };

  return (
    <Card title="Ingest data" sub="fetch → validate → store">
      <div className="stack">
        <Seg value={mode} onChange={setMode} options={[{ value: "synthetic", label: "Synthetic generator" }, { value: "public", label: "Public provider" }, { value: "csv", label: "CSV upload" }]} />
        {mode === "synthetic" && <div className="banner warn small">Synthetic data is artificial and clearly labelled. Incremental mode requests only bars after each symbol's last stored date.</div>}
        {mode === "public" && !publicEnabled && <div className="banner neutral small">The public provider (Yahoo Finance chart endpoint, unofficial) is disabled. Set <code>NEXIS_PUBLIC_PROVIDER_ENABLED=true</code> and restart the API to use it.</div>}
        <div className="form-grid">
          <Field label="Dataset code"><input className="input" value={form.code} onChange={(e) => set("code", e.target.value)} /></Field>
          <Field label="Dataset name"><input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} /></Field>
          {mode !== "csv" && (
            <Field label="Mode">
              <select className="input" value={form.ingestMode} onChange={(e) => set("ingestMode", e.target.value)}>
                <option value="incremental">incremental</option><option value="full">full (replace)</option>
              </select>
            </Field>
          )}
          {mode !== "csv" && <Field label="End date" hint="blank = provider default"><input type="date" className="input" value={form.end} onChange={(e) => set("end", e.target.value)} /></Field>}
          {mode === "synthetic" && <Field label="Seed"><input type="number" className="input" value={form.seed} onChange={(e) => set("seed", Number(e.target.value))} /></Field>}
          {mode === "public" && <>
            <Field label="Symbols (comma-separated)"><input className="input" value={form.symbols} onChange={(e) => set("symbols", e.target.value)} /></Field>
            <Field label="Start date"><input type="date" className="input" value={form.start} onChange={(e) => set("start", e.target.value)} /></Field>
          </>}
          {mode !== "synthetic" && <Field label="Benchmark symbol"><input className="input" value={form.bench} onChange={(e) => set("bench", e.target.value)} /></Field>}
          {mode === "csv" && <Field label="CSV file" hint="columns: symbol,date,open,high,low,close[,adj_close,volume]"><input type="file" accept=".csv,text/csv" className="input" style={{ paddingTop: 4 }} onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>}
        </div>
        <div className="form-actions">
          <button className="btn primary" disabled={busy || (mode === "public" && !publicEnabled)} onClick={submit}>{busy ? "Ingesting…" : "Run ingestion"}</button>
          {msg && <span className={`small ${msg.ok ? "text2" : "neg"}`}>{msg.text}</span>}
        </div>
      </div>
    </Card>
  );
}
