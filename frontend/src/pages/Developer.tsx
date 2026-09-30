import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import DataTable from "../components/DataTable";
import { Card, Field, PageHead, QueryView } from "../components/ui";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt } from "../utils/format";

export default function Developer() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api.get<AnyObj[]>("/developer/api-keys") });
  const hooks = useQuery({ queryKey: ["webhooks"], queryFn: () => api.get<AnyObj>("/developer/webhooks"), refetchInterval: 10_000 });
  const [name, setName] = useState("");
  const [created, setCreated] = useState<AnyObj | null>(null);
  const [wh, setWh] = useState({ url: "", events: ["data.sync.completed", "backtest.completed"] as string[] });
  const [whCreated, setWhCreated] = useState<AnyObj | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const origin = window.location.origin;
  const run = async (fn: () => Promise<void>) => { setErr(null); try { await fn(); } catch (e) { setErr(errorMessage(e)); } };
  return (
    <>
      <PageHead title="Developer API & Webhooks" desc="Read-only public API authenticated with bearer keys (only a SHA-256 hash is stored), and signed webhooks for research events. Full OpenAPI docs at /docs." />
      {err && <div className="banner error small" style={{ marginBottom: 12 }}>{err}</div>}
      <div className="grid g2">
        <Card title="API keys">
          <div className="stack">
            <div className="row"><input className="input" style={{ maxWidth: 260 }} placeholder="Key name (e.g. notebook)" value={name} onChange={(e) => setName(e.target.value)} />
              <button className="btn primary" disabled={name.length < 2} onClick={() => run(async () => { setCreated(await api.post<AnyObj>("/developer/api-keys", { name })); setName(""); qc.invalidateQueries({ queryKey: ["api-keys"] }); })}>Create key</button></div>
            {created && <div className="banner warn small"><div><b>Copy this key now — it will not be shown again:</b><div className="mono" style={{ userSelect: "all", wordBreak: "break-all", marginTop: 4 }}>{created.api_key}</div></div></div>}
            <QueryView q={keys}>
              {(rows) => (
                <DataTable<AnyObj> rows={rows} filterable={false} columns={[
                  { key: "name", label: "Name" }, { key: "prefix", label: "Prefix", render: (k) => <span className="mono">{k.prefix}…</span> },
                  { key: "created_at", label: "Created", render: (k) => dt(k.created_at) }, { key: "last_used_at", label: "Last used", render: (k) => dt(k.last_used_at) },
                  { key: "status", label: "Status", value: (k) => (k.revoked_at ? "revoked" : "active"), render: (k) => <span className={`badge ${k.revoked_at ? "bad" : "good"}`}>{k.revoked_at ? "revoked" : "active"}</span> },
                  { key: "x", label: "", sortable: false, render: (k) => !k.revoked_at && <button className="btn sm danger" onClick={() => run(async () => { await api.post(`/developer/api-keys/${k.id}/revoke`); qc.invalidateQueries({ queryKey: ["api-keys"] }); })}>Revoke</button> },
                ]} empty="No keys." />
              )}
            </QueryView>
            <pre className="pre">{`curl -H "Authorization: Bearer nx_..." ${origin}/api/v1/portfolio
curl -H "Authorization: Bearer nx_..." ${origin}/api/v1/portfolio/diagnostics
curl -H "Authorization: Bearer nx_..." ${origin}/api/v1/experiments
curl -H "Authorization: Bearer nx_..." ${origin}/api/v1/reports`}</pre>
          </div>
        </Card>
        <Card title="Webhooks" sub="HMAC-SHA256 signed, 3 attempts with back-off">
          <div className="stack">
            <Field label="Endpoint URL"><input className="input" placeholder="https://example.com/nexis-hook" value={wh.url} onChange={(e) => setWh({ ...wh, url: e.target.value })} /></Field>
            <Field label="Events">
              <div className="row">{(hooks.data?.events ?? []).map((ev: string) => (
                <label key={ev} className="check"><input type="checkbox" checked={wh.events.includes(ev)} onChange={() => setWh({ ...wh, events: wh.events.includes(ev) ? wh.events.filter((x) => x !== ev) : [...wh.events, ev] })} /><span className="mono xs">{ev}</span></label>))}</div>
            </Field>
            <div className="row">
              <button className="btn primary" disabled={!wh.url || !wh.events.length} onClick={() => run(async () => { setWhCreated(await api.post<AnyObj>("/developer/webhooks", { url: wh.url, events: wh.events })); qc.invalidateQueries({ queryKey: ["webhooks"] }); })}>Add endpoint</button>
              <button className="btn" onClick={() => run(async () => { await api.post("/developer/webhooks/test"); qc.invalidateQueries({ queryKey: ["webhooks"] }); })}>Send test ping</button>
            </div>
            {whCreated && <div className="banner warn small"><div><b>Signing secret (shown once):</b> <span className="mono" style={{ userSelect: "all" }}>{whCreated.signing_secret}</span><div className="xs">Verify: HMAC_SHA256(secret, raw_body) == X-Nexis-Signature (after "sha256=").</div></div></div>}
            <QueryView q={hooks}>
              {(h) => (
                <>
                  <DataTable<AnyObj> rows={h.endpoints} filterable={false} columns={[
                    { key: "url", label: "URL", render: (e) => <span className="mono xs">{e.url}</span> }, { key: "events", label: "Events", render: (e) => <span className="xs">{e.events.join(", ")}</span> },
                    { key: "x", label: "", sortable: false, render: (e) => <button className="btn sm danger" onClick={() => run(async () => { await api.del(`/developer/webhooks/${e.id}`); qc.invalidateQueries({ queryKey: ["webhooks"] }); })}>Delete</button> },
                  ]} empty="No endpoints." />
                  <div className="small text2">Recent deliveries</div>
                  <DataTable<AnyObj> rows={h.deliveries} filterable={false} pageSize={8} columns={[
                    { key: "created_at", label: "Queued", render: (d) => dt(d.created_at) }, { key: "event", label: "Event", render: (d) => <span className="mono xs">{d.event}</span> },
                    { key: "status", label: "Status", render: (d) => <span className={`badge ${d.status === "delivered" ? "good" : d.status === "failed" ? "bad" : "warn"}`}>{d.status}</span> },
                    { key: "attempts", label: "Attempts", align: "right" }, { key: "response_code", label: "HTTP", align: "right" }, { key: "error", label: "Error", render: (d) => <span className="xs neg">{d.error ?? ""}</span> },
                  ]} empty="No deliveries." />
                </>
              )}
            </QueryView>
          </div>
        </Card>
      </div>
    </>
  );
}
