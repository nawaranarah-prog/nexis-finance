import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../services/api";
import { useWorkspace } from "../hooks/workspace";
import type { AnyObj, Notification } from "../types/api";
import { dt } from "../utils/format";

export const NAV: { group: string; items: { to: string; label: string }[] }[] = [
  { group: "Workspace", items: [{ to: "/", label: "Overview" }] },
  { group: "Data", items: [{ to: "/market-data", label: "Market Data" }, { to: "/data-quality", label: "Data Quality" }] },
  {
    group: "Portfolio & Risk",
    items: [
      { to: "/asset-research", label: "Asset Research" },
      { to: "/portfolio-lab", label: "Portfolio Lab" },
      { to: "/risk", label: "Risk Analytics" },
      { to: "/stress-testing", label: "Stress Testing" },
      { to: "/factors", label: "Factor Analytics" },
    ],
  },
  {
    group: "Quant Research",
    items: [
      { to: "/strategies", label: "Quant Strategies" },
      { to: "/backtesting", label: "Backtesting" },
      { to: "/machine-learning", label: "Machine Learning" },
      { to: "/regimes", label: "Regime Analysis" },
      { to: "/anomalies", label: "Anomaly Detection" },
      { to: "/experiments", label: "Research Experiments" },
    ],
  },
  { group: "Output", items: [{ to: "/reports", label: "Reports" }] },
  { group: "Platform", items: [{ to: "/system", label: "System Health" }, { to: "/settings", label: "Settings" }] },
];

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [debounced, setDebounced] = useState("");
  const nav = useNavigate();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 200);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); ref.current?.querySelector("input")?.focus(); }
    };
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, []);
  const r = useQuery({ queryKey: ["search", debounced], queryFn: () => api.get<AnyObj>("/search", { q: debounced }), enabled: debounced.length > 0 });
  const groups: [string, AnyObj[], (x: AnyObj) => ReactNode][] = r.data
    ? [
        ["Assets", r.data.assets, (x) => <><span><b className="mono">{x.symbol}</b> <span className="text2">{x.name}</span></span><span className="xs muted">{x.dataset}</span></>],
        ["Portfolios", r.data.portfolios, (x) => <span>{x.name}</span>],
        ["Experiments", r.data.experiments, (x) => <><span><b className="mono">{x.code}</b> <span className="text2">{x.name}</span></span><span className="xs muted">{x.status}</span></>],
        ["Backtests", r.data.backtests, (x) => <><span>{x.name}</span><span className="xs muted">{x.strategy}</span></>],
      ]
    : [];
  const total = groups.reduce((n, g) => n + g[1].length, 0);
  const go = (link: string) => { setOpen(false); setQ(""); nav(link); };
  return (
    <div className="search" ref={ref}>
      <span className="icon" aria-hidden>⌕</span>
      <input className="input" placeholder="Search assets, portfolios, experiments, backtests  (Ctrl+K)" value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); if (e.key === "Enter" && total) { const first = groups.find((g) => g[1].length); if (first) go(first[1][0].link); } }}
        aria-label="Global search" />
      {open && debounced && (
        <div className="popover">
          {r.isLoading && <div className="state">Searching…</div>}
          {r.data && total === 0 && <div className="state">No results for “{debounced}”.</div>}
          {groups.filter((g) => g[1].length).map(([name, items, render]) => (
            <div key={name}>
              <div className="pop-group">{name}</div>
              {items.map((x, i) => <div key={i} className="pop-item" onClick={() => go(x.link)}>{render(x)}</div>)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Notifications() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const nav = useNavigate();
  const ref = useRef<HTMLDivElement>(null);
  const q = useQuery({ queryKey: ["notifications"], queryFn: () => api.get<{ items: Notification[]; unread: number }>("/notifications", { limit: 30 }), refetchInterval: 20_000 });
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  const markAll = async () => { await api.post("/notifications/read", { ids: null }); qc.invalidateQueries({ queryKey: ["notifications"] }); };
  const unread = q.data?.unread ?? 0;
  const icon: Record<string, string> = { error: "✕", warning: "!", success: "✓", info: "•" };
  const cls: Record<string, string> = { error: "neg", warning: "", success: "pos", info: "" };
  return (
    <div className="bell" ref={ref} style={{ position: "relative" }}>
      <button className="btn ghost" onClick={() => setOpen(!open)} aria-label={`Notifications, ${unread} unread`}>🔔</button>
      {unread > 0 && <span className="count">{unread > 99 ? "99+" : unread}</span>}
      {open && (
        <div className="popover right">
          <div className="row-between" style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <b>Notifications</b>
            <button className="btn sm ghost" onClick={markAll} disabled={!unread}>Mark all read</button>
          </div>
          {(q.data?.items ?? []).map((n) => (
            <div key={n.id} className={`notif ${n.is_read ? "" : "unread"}`} style={{ cursor: n.link ? "pointer" : "default" }}
              onClick={() => { if (n.link) { setOpen(false); nav(n.link); } }}>
              <div className="row-between"><b className={cls[n.level]}>{icon[n.level]} {n.title}</b><span className="xs muted">{dt(n.created_at)}</span></div>
              <div className="text2 small">{n.message}</div>
            </div>
          ))}
          {q.data && q.data.items.length === 0 && <div className="state">No notifications.</div>}
        </div>
      )}
    </div>
  );
}

export default function AppLayout({ children }: { children: ReactNode }) {
  const { datasets, dataset, setDatasetId, settings, updateSettings } = useWorkspace();
  let idx = 0;
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-name"><img src="/favicon.svg" alt="" width={20} height={20} /> NEXIS FINANCE</div>
          <div className="brand-sub">Quantitative Research · Portfolio Risk · Financial ML · Big Data</div>
        </div>
        <nav className="nav" aria-label="Main">
          {NAV.map((g) => (
            <div key={g.group}>
              <div className="nav-group">{g.group}</div>
              {g.items.map((it) => {
                idx += 1;
                return (
                  <NavLink key={it.to} to={it.to} end={it.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
                    <span className="nav-idx">{idx}</span>{it.label}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        <div style={{ marginTop: "auto", padding: 12 }} className="xs muted">
          Research software · not investment advice
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <select className="input" style={{ width: 250 }} value={dataset?.id ?? ""} onChange={(e) => setDatasetId(Number(e.target.value))} aria-label="Dataset">
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.code} · v{d.version}</option>)}
          </select>
          {dataset && (dataset.is_synthetic
            ? <span className="badge synthetic" title="All observations are artificially generated">DEMO / SYNTHETIC DATA MODE</span>
            : <span className="badge info">LIVE PUBLIC DATA · {dataset.source}</span>)}
          {dataset && <span className="xs muted">through {dataset.end_date}</span>}
          <span className="spacer" />
          <GlobalSearch />
          <Notifications />
          <button className="btn ghost" aria-label="Toggle theme" title="Toggle theme"
            onClick={() => updateSettings({ theme: settings.theme === "dark" ? "light" : settings.theme === "light" ? "system" : "dark" })}>
            {settings.theme === "dark" ? "☾" : settings.theme === "light" ? "☀" : "◐"}
          </button>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
