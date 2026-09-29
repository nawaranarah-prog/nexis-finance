import { useQuery } from "@tanstack/react-query";
import { Card, Field, PageHead, QueryView, Seg } from "../components/ui";
import { type Theme, useWorkspace } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { pct } from "../utils/format";

export default function Settings() {
  const { settings, updateSettings } = useWorkspace();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api.get<AnyObj>("/system/config"), staleTime: Infinity });
  return (
    <>
      <PageHead title="Settings" desc="Workspace preferences are stored in this browser only. Server configuration comes from environment variables and is shown read-only." />
      <div className="grid g2">
        <Card title="Analysis defaults">
          <div className="stack">
            <Field label="Risk-free rate (annual, decimal)" metric="sharpe_ratio" hint={`Used for Sharpe/Sortino in portfolio analytics. Currently ${pct(settings.riskFreeRate)}.`}>
              <input className="input" type="number" step={0.0025} min={-0.05} max={0.25} value={settings.riskFreeRate} onChange={(e) => updateSettings({ riskFreeRate: Number(e.target.value) })} style={{ maxWidth: 160 }} />
            </Field>
            <Field label="Rolling window for portfolio charts (days)">
              <Seg value={settings.rollingWindow} onChange={(v) => updateSettings({ rollingWindow: v })} options={[20, 63, 126, 252].map((w) => ({ value: w, label: String(w) }))} />
            </Field>
            <Field label="Theme">
              <Seg<Theme> value={settings.theme} onChange={(v) => updateSettings({ theme: v })} options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} />
            </Field>
            <div>
              <button className="btn" onClick={() => { try { Object.keys(localStorage).filter((k) => k.startsWith("nexis.")).forEach((k) => localStorage.removeItem(k)); } catch { /* ignore */ } window.location.reload(); }}>Reset local preferences</button>
            </div>
          </div>
        </Card>
        <Card title="Server configuration" sub="read-only">
          <QueryView q={cfg}>
            {(c) => (
              <dl className="kv">
                <dt>Version</dt><dd>{c.version}</dd>
                <dt>Environment</dt><dd>{c.environment}</dd>
                <dt>Database engine</dt><dd>{c.database_engine}</dd>
                <dt>Public data provider</dt><dd>{c.public_provider_enabled ? "enabled (Yahoo Finance chart endpoint, unofficial)" : "disabled — synthetic/CSV only"}</dd>
                <dt>Default risk-free rate</dt><dd>{pct(c.default_risk_free_rate)}</dd>
                <dt>Trading days per year</dt><dd>{c.trading_days_per_year}</dd>
                <dt>Max CSV upload</dt><dd>{c.max_upload_mb} MB</dd>
                <dt>Background job workers</dt><dd>{c.job_workers}</dd>
              </dl>
            )}
          </QueryView>
          <div className="xs muted" style={{ marginTop: 10 }}>Change these via <code>NEXIS_*</code> environment variables (see <code>.env.example</code>) and restart the API. No credentials are stored in the application.</div>
        </Card>
      </div>
    </>
  );
}
