import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

export const DATA_CLASS: Record<string, { label: string; cls: string; title: string }> = {
  real_external: { label: "Real external data", cls: "info", title: "Retrieved from an external provider" },
  user_imported: { label: "User-imported data", cls: "", title: "Imported from a file or connected account" },
  synthetic: { label: "Synthetic research data", cls: "synthetic", title: "Artificially generated; not real market history" },
};

export function DataClassBadge({ value, sample }: { value: string | null | undefined; sample?: boolean }) {
  const d = DATA_CLASS[value ?? ""] ?? { label: value ?? "unknown", cls: "", title: "" };
  return (
    <span className="row" style={{ gap: 4, display: "inline-flex" }}>
      <span className={`badge ${d.cls}`} title={d.title}>{d.label}</span>
      {sample && <span className="badge warn" title="Bundled sample file with fictional quantities">SAMPLE FILE</span>}
    </span>
  );
}

const STATUS: Record<string, { cls: string; icon: string; label: string }> = {
  connected: { cls: "good", icon: "✓", label: "Connected" },
  syncing: { cls: "info", icon: "↻", label: "Syncing" },
  needs_attention: { cls: "warn", icon: "!", label: "Needs attention" },
  disconnected: { cls: "", icon: "○", label: "Disconnected" },
  unavailable: { cls: "bad", icon: "✕", label: "Unavailable" },
};

export function ConnectionStatus({ status }: { status: string }) {
  const s = STATUS[status] ?? { cls: "", icon: "•", label: status };
  return <span className={`badge ${s.cls}`}><span aria-hidden>{s.icon}</span>{s.label}</span>;
}

/** Account scope: consolidated ("all") or one account. Accounts come from the reconstructed book. */
export function useAccounts() {
  return useQuery({
    queryKey: ["intel-accounts"],
    queryFn: async () => (await api.get<AnyObj>("/intelligence/portfolio", { scope: "all" })).accounts as AnyObj[],
    staleTime: 30_000,
  });
}

export function ScopeSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const q = useAccounts();
  return (
    <select className="input" style={{ width: 260 }} value={value} onChange={(e) => onChange(e.target.value)} aria-label="Account scope">
      <option value="all">All accounts (consolidated)</option>
      {(q.data ?? []).map((a) => <option key={a.id} value={String(a.id)}>{a.name} — {a.institution}</option>)}
    </select>
  );
}

export function MethodSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select className="input" style={{ width: 170 }} value={value} onChange={(e) => onChange(e.target.value)} aria-label="Cost basis method">
      <option value="fifo">Cost basis: FIFO</option>
      <option value="average">Cost basis: Average cost</option>
    </select>
  );
}

export function Warnings({ items }: { items: string[] | undefined }) {
  if (!items?.length) return null;
  return (
    <div className="banner warn small" role="status">
      <span aria-hidden>!</span>
      <div>{items.map((w) => <div key={w}>{w}</div>)}</div>
    </div>
  );
}

export function useSystemConfig() {
  return useQuery({ queryKey: ["config"], queryFn: () => api.get<AnyObj>("/system/config"), staleTime: Infinity });
}

/** Shown on a shared public deployment, where every visitor works in the same workspace. */
export function PublicWorkspaceNotice() {
  const cfg = useSystemConfig();
  if (!cfg.data?.public_instance) return null;
  return (
    <div className="banner warn small" role="note" style={{ marginBottom: 12 }}>
      <span aria-hidden>!</span>
      <span>This is a <b>shared public workspace</b>: files you import here are visible to other visitors, and API keys are not stored.
        Import only files you are happy to share, or <a href="https://github.com/nawaranarah-prog/nexis-finance#quick-start" target="_blank" rel="noreferrer">run your own instance</a> for private data and account connections.
        Uploads are limited to {cfg.data.max_upload_mb} MB.</span>
    </div>
  );
}
