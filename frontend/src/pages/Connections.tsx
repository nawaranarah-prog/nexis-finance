import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, Field, JobStatus, PageHead, QueryView, Tabs } from "../components/ui";
import { ConnectionStatus, DataClassBadge } from "../components/connect";
import { useJobRunner } from "../hooks/queries";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int, pct } from "../utils/format";

const CATS = [
  { value: "all", label: "All" }, { value: "markets", label: "Markets" }, { value: "accounts", label: "Accounts" },
  { value: "economics", label: "Economics" }, { value: "regulatory", label: "Regulatory" }, { value: "files", label: "Files" },
] as const;

export default function Connections() {
  const qc = useQueryClient();
  const [cat, setCat] = useState<string>("all");
  const mk = useQuery({ queryKey: ["marketplace"], queryFn: () => api.get<AnyObj[]>("/connections/marketplace") });
  const runs = useQuery({ queryKey: ["sync-runs"], queryFn: () => api.get<AnyObj[]>("/connections/sync-runs") });
  const refresh = () => ["marketplace", "sync-runs", "intel-accounts", "intel", "notifications", "datasets"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  return (
    <>
      <PageHead title="Connect your financial world"
        desc="Connect market, economic and regulatory data sources, connect brokerage accounts where a secure API exists, or import statements. Everything is normalised into one data model with full lineage." />
      <div className="banner neutral small" style={{ marginBottom: 12 }}>
        <span aria-hidden>🔒</span>
        <span>Nexis never asks for brokerage passwords. Provider API keys are encrypted at rest (Fernet), never returned to the browser and never logged; disconnecting wipes them. Providers without a secure integration are marked <b>Coming soon</b> rather than simulated.</span>
      </div>
      <ImportWizard onDone={refresh} />
      <div style={{ marginTop: 12 }}>
        <Tabs value={cat} onChange={setCat} tabs={CATS.map((c) => ({ value: c.value, label: c.label }))} />
        <QueryView q={mk} label="Loading integrations">
          {(items) => (
            <div className="grid g2">
              {items.filter((i) => cat === "all" || i.category === cat).map((i) => <Integration key={i.key} spec={i} onChange={refresh} />)}
            </div>
          )}
        </QueryView>
      </div>
      <Card title="Synchronisation log" sub="every sync attempt, with records added / updated / removed" flush className="">
        <QueryView q={runs}>
          {(rows) => (
            <DataTable<AnyObj> rows={rows} pageSize={10} columns={[
              { key: "started_at", label: "Started", render: (r) => dt(r.started_at) },
              { key: "connection_id", label: "Connection", align: "right" },
              { key: "status", label: "Status", render: (r) => <span className={`badge ${r.status === "success" ? "good" : r.status === "failed" ? "bad" : "warn"}`}>{r.status}</span> },
              { key: "records_added", label: "Added", align: "right", render: (r) => int(r.records_added) },
              { key: "records_updated", label: "Updated", align: "right", render: (r) => int(r.records_updated) },
              { key: "records_removed", label: "Removed", align: "right", render: (r) => int(r.records_removed) },
              { key: "warnings", label: "Warnings / errors", wrap: true, render: (r) => <span className="small">{[...(r.errors ?? []), ...(r.warnings ?? [])].slice(0, 3).join(" · ")}{(r.warnings?.length ?? 0) > 3 ? ` (+${r.warnings.length - 3})` : ""}</span> },
            ]} empty="No syncs yet." />
          )}
        </QueryView>
      </Card>
    </>
  );
}

function Integration({ spec, onChange }: { spec: AnyObj; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [config, setConfig] = useState<Record<string, string>>(() => Object.fromEntries((spec.config_fields ?? []).map((f: AnyObj) => [f.name, String(f.default ?? "")])));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const job = useJobRunner([["marketplace"], ["sync-runs"], ["intel"], ["intel-accounts"]]);
  const conns: AnyObj[] = spec.connections ?? [];
  const connect = async () => {
    setBusy(true); setErr(null);
    try {
      const cfg = Object.fromEntries(Object.entries(config).map(([k, v]) => [k, /^\d+$/.test(v) ? Number(v) : v]));
      const c = await api.post<AnyObj>("/connections", { provider_key: spec.key, credentials: spec.credential_fields.length ? creds : undefined, config: cfg });
      if (c.status !== "connected") setErr(c.last_error ?? `status: ${c.status}`);
      else setOpen(false);
      setCreds({});
      onChange();
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const act = async (path: string, method: "post" | "del" = "post") => {
    setErr(null);
    try { if (method === "del") await api.del(path); else await api.post(path); onChange(); } catch (e) { setErr(errorMessage(e)); }
  };
  return (
    <Card title={spec.name} sub={spec.category} actions={
      !spec.implemented ? <span className="badge">Coming soon</span>
        : spec.auth_type === "file" ? <a className="btn sm" href="#import">Import a file</a>
        : !spec.enabled ? <span className="badge bad" title="Disabled by server configuration">Unavailable</span>
        : <button className="btn sm primary" onClick={() => setOpen(!open)}>{conns.length ? "Add connection" : "Connect"}</button>}>
      <div className="stack" style={{ gap: 8 }}>
        <div className="small text2">{spec.description}</div>
        <div className="row small" style={{ gap: 6 }}>
          {spec.capabilities.map((c: string) => <span key={c} className="badge">{c.replace("_", " ")}</span>)}
          <DataClassBadge value={spec.data_class} />
          {spec.implemented && <span className={`badge ${spec.verification === "live" ? "good" : "warn"}`} title={spec.notes}>{spec.verification === "live" ? "verified against live endpoint" : "verified with mocked responses only"}</span>}
        </div>
        <div className="xs muted">Requires: {spec.requirements.join(" · ") || "nothing"} · <a href={spec.docs_url} target={spec.docs_url.startsWith("http") ? "_blank" : undefined} rel="noreferrer">documentation</a>{spec.notes && ` · ${spec.notes}`}</div>
        {open && (
          <div className="stack" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <div className="form-grid">
              {spec.credential_fields.map((f: AnyObj) => (
                <Field key={f.name} label={f.label} hint={f.help}>
                  <input className="input" type={f.secret ? "password" : "text"} autoComplete="off" value={creds[f.name] ?? ""} onChange={(e) => setCreds({ ...creds, [f.name]: e.target.value })} />
                </Field>
              ))}
              {(spec.config_fields ?? []).map((f: AnyObj) => (
                <Field key={f.name} label={f.label}>
                  {f.type === "choice"
                    ? <select className="input" value={config[f.name]} onChange={(e) => setConfig({ ...config, [f.name]: e.target.value })}>{f.choices.map((c: string) => <option key={c}>{c}</option>)}</select>
                    : <input className="input" type={f.type === "date" ? "date" : "text"} value={config[f.name] ?? ""} onChange={(e) => setConfig({ ...config, [f.name]: e.target.value })} />}
                </Field>
              ))}
            </div>
            <div className="row"><button className="btn primary" disabled={busy} onClick={connect}>{busy ? "Verifying…" : "Verify and connect"}</button>
              <span className="xs muted">A real verification request is made; the connection is only marked connected if it succeeds.</span></div>
          </div>
        )}
        {err && <div className="banner error small">{err}</div>}
        {conns.map((c) => (
          <div key={c.id} className="row-between" style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <div className="stack" style={{ gap: 2 }}>
              <div className="row" style={{ gap: 6 }}><b>{c.display_name}</b><ConnectionStatus status={c.status} />{c.credential_hint && <span className="xs muted mono">key {c.credential_hint}</span>}</div>
              <div className="xs muted">Last sync {dt(c.last_sync_at)} {c.last_sync_status ? `(${c.last_sync_status})` : ""} · authorised: {c.authorized ? "yes" : "no"}{c.last_error ? ` · ${c.last_error}` : ""}</div>
            </div>
            <div className="row">
              {c.can_sync && <button className="btn sm" disabled={job.running || c.status === "disconnected" || c.status === "unavailable"} onClick={() => job.run(`/connections/${c.id}/sync`, {})}>Sync now</button>}
              {c.status !== "disconnected" && c.auth_type !== "file" && <button className="btn sm" onClick={() => act(`/connections/${c.id}/disconnect`)}>Disconnect</button>}
              <button className="btn sm danger" onClick={() => window.confirm(`Remove ${c.display_name}? Imported records stay; the connection and its sync log are removed.`) && act(`/connections/${c.id}`, "del")}>Remove</button>
            </div>
          </div>
        ))}
        <JobStatus job={job.job?.status === "succeeded" ? null : job.job} error={job.error} running={job.running} />
        {job.job?.status === "succeeded" && job.job.result && (
          <div className={`banner ${job.job.result.status === "failed" ? "error" : job.job.result.status === "warning" ? "warn" : "info"} small`}>
            Sync {job.job.result.status}: {job.job.result.records_added} added, {job.job.result.records_updated} updated, {job.job.result.records_removed} removed.
            {job.job.result.errors?.[0] && ` ${job.job.result.errors[0]}`}{job.job.result.warnings?.length ? ` ${job.job.result.warnings.length} warning(s) — see the sync log.` : ""}
          </div>
        )}
      </div>
    </Card>
  );
}

function ImportWizard({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [prev, setPrev] = useState<AnyObj | null>(null);
  const [kind, setKind] = useState("holdings");
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [opts, setOpts] = useState({ source_label: "", institution: "", account: "", account_name: "", as_of: "", dayfirst: false, default_currency: "USD" });
  const [result, setResult] = useState<AnyObj | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const samples = useQuery({ queryKey: ["samples"], queryFn: () => api.get<AnyObj[]>("/imports/samples"), staleTime: Infinity });

  const doPreview = async (f: File, k?: string) => {
    setErr(null); setResult(null); setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", f); if (k) fd.append("kind", k);
      const p = await api.upload<AnyObj>("/imports/preview", fd);
      setPrev(p); setKind(p.proposal.kind); setMapping(p.proposal.mapping);
      setOpts((o) => ({ ...o, source_label: o.source_label || f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ") }));
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const commit = async (force = false) => {
    if (!file || !prev) return;
    setErr(null); setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file); fd.append("source_label", opts.source_label); fd.append("kind", kind);
      fd.append("mapping", JSON.stringify(mapping)); fd.append("force", String(force));
      const o: AnyObj = { institution: opts.institution || opts.source_label, default_currency: opts.default_currency, dayfirst: opts.dayfirst };
      if (opts.account) o.account = opts.account;
      if (opts.account_name) o.account_name = opts.account_name;
      if (opts.as_of) o.as_of = opts.as_of;
      fd.append("options", JSON.stringify(o));
      setResult(await api.upload<AnyObj>("/imports", fd));
      onDone();
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const loadSamples = async () => {
    setErr(null); setBusy(true);
    try { const r = await api.post<AnyObj>("/imports/samples/load"); setResult({ samples: r.results, note: r.note }); onDone(); }
    catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const fields: string[] = prev ? prev.fields[kind] : [];
  const required: string[] = prev ? prev.required[kind] : [];
  const conf: Record<string, number> = prev && prev.proposal.kind === kind ? prev.proposal.confidence : {};
  return (
    <Card title={<span id="import">Universal import</span>} sub="holdings · transactions · market data — CSV, JSON or XLSX"
      actions={<button className="btn sm" onClick={loadSamples} disabled={busy || !samples.data?.every((s) => s.available)} title="Imports bundled fictional statements through the normal pipeline">Load sample statements</button>}>
      <div className="stack">
        <div className="row">
          <input type="file" accept=".csv,.json,.xlsx,.txt" className="input" style={{ maxWidth: 360, paddingTop: 4 }}
            onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); setPrev(null); if (f) doPreview(f); }} aria-label="Statement file" />
          {prev && <span className="small text2">{prev.file_format.toUpperCase()} · {int(prev.row_count)} rows · detected <b>{prev.proposal.kind}</b> {prev.needs_review && <span className="badge warn">review mapping</span>}</span>}
        </div>
        {prev && (
          <>
            <div className="form-grid">
              <Field label="Record type">
                <select className="input" value={kind} onChange={(e) => { setKind(e.target.value); if (file) doPreview(file, e.target.value); }}>
                  <option value="holdings">Holdings</option><option value="transactions">Transactions</option><option value="market_data">Market data (OHLCV)</option>
                </select>
              </Field>
              <Field label="Source label" hint="e.g. 'Brokerage A'"><input className="input" value={opts.source_label} onChange={(e) => setOpts({ ...opts, source_label: e.target.value })} /></Field>
              {kind !== "market_data" && <>
                <Field label="Institution"><input className="input" value={opts.institution} placeholder={opts.source_label} onChange={(e) => setOpts({ ...opts, institution: e.target.value })} /></Field>
                <Field label="Default account ID" hint={mapping.account ? "per-row account column mapped" : "required if no account column"}><input className="input" value={opts.account} onChange={(e) => setOpts({ ...opts, account: e.target.value })} /></Field>
                <Field label="Account name"><input className="input" value={opts.account_name} onChange={(e) => setOpts({ ...opts, account_name: e.target.value })} /></Field>
              </>}
              {kind === "holdings" && <Field label="Snapshot as of"><input className="input" type="date" value={opts.as_of} onChange={(e) => setOpts({ ...opts, as_of: e.target.value })} /></Field>}
              <Field label="Default currency"><input className="input" value={opts.default_currency} maxLength={3} onChange={(e) => setOpts({ ...opts, default_currency: e.target.value.toUpperCase() })} /></Field>
              {kind === "transactions" && <Field label="Date format"><label className="check"><input type="checkbox" checked={opts.dayfirst} onChange={(e) => setOpts({ ...opts, dayfirst: e.target.checked })} /> Day first (DD/MM/YYYY)</label></Field>}
            </div>
            <div className="field-label">Column mapping (confidence from automatic detection)</div>
            <div className="table-wrap">
              <table className="dt">
                <thead><tr><th>Nexis field</th><th>Source column</th><th className="r">Confidence</th><th>Sample value</th></tr></thead>
                <tbody>
                  {fields.map((f) => (
                    <tr key={f}>
                      <td><span className="mono">{f}</span>{required.includes(f) && <span className="neg"> *</span>}</td>
                      <td>
                        <select className="input sm" style={{ width: 220 }} value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value || null })}>
                          <option value="">— not mapped —</option>
                          {prev.columns.map((c: string) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="r">{mapping[f] ? (conf[f] != null && prev.proposal.mapping[f] === mapping[f] ? <span className={conf[f] < 0.9 ? "neg" : ""}>{pct(conf[f], 0)}</span> : <span className="muted">manual</span>) : "–"}</td>
                      <td className="small text2">{mapping[f] ? String(prev.sample_rows[0]?.[mapping[f]!] ?? "") : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details><summary className="small text2" style={{ cursor: "pointer" }}>Preview first rows</summary>
              <div className="table-wrap" style={{ maxHeight: 240 }}><table className="dt"><thead><tr>{prev.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{prev.sample_rows.map((r: AnyObj, i: number) => <tr key={i}>{prev.columns.map((c: string) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>)}</tbody></table></div>
            </details>
            <div className="row">
              <button className="btn primary" disabled={busy || !opts.source_label || required.some((f) => !mapping[f])} onClick={() => commit(false)}>{busy ? "Importing…" : "Import"}</button>
              <span className="xs muted">Every row is kept verbatim for lineage; invalid rows are rejected with a reason, never silently dropped.</span>
            </div>
          </>
        )}
        {err && <div className="banner error small">{err}{/already imported/.test(err) && <> — <button className="btn sm" onClick={() => commit(true)}>Import again anyway</button> (duplicate transactions are still detected)</>}</div>}
        {result && !result.samples && (
          <div className="banner info small">Imported <b>{int(result.rows_imported)}</b> of {int(result.rows_total)} rows · {result.rows_rejected} rejected · {result.rows_duplicate} duplicates (batch #{result.id}).{" "}
            <Link to={`/lineage?batch=${result.id}`}>Inspect lineage</Link> · <Link to="/intelligence">Open Financial Intelligence</Link>
            {result.issues?.length > 0 && <div className="xs" style={{ marginTop: 4 }}>{result.issues.slice(0, 5).map((i: AnyObj) => `row ${i.row ?? "-"}: ${i.message}`).join(" · ")}</div>}
          </div>
        )}
        {result?.samples && (
          <div className="banner info small"><div>{result.note}</div>
            {result.samples.map((s: AnyObj, i: number) => <div key={i}>• {s.file_name}: {s.error ? <span className="neg">{s.error}</span> : `${s.rows_imported} imported, ${s.rows_rejected} rejected`}</div>)}
            <div style={{ marginTop: 4 }}>Next: connect <b>Public Market Data</b> and <b>SEC EDGAR</b> below and press <b>Sync now</b> to price holdings and classify issuers, then open <Link to="/intelligence">Financial Intelligence</Link>.</div>
          </div>
        )}
      </div>
    </Card>
  );
}
