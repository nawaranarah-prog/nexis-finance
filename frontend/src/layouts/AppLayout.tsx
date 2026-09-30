import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../services/api";
import { Avatar, useMe } from "../components/market";
import CommandPalette from "../components/CommandPalette";
import { Toaster } from "../components/toast";
import { useWorkspace } from "../hooks/workspace";
import type { Notification } from "../types/api";
import { dt } from "../utils/format";

export const NAV: { group: string; items: { to: string; label: string }[] }[] = [
  { group: "Workspace", items: [{ to: "/", label: "Overview" }] },
  {
    group: "Markets & Advice",
    items: [
      { to: "/markets", label: "Global Markets" },
      { to: "/advisor", label: "AI Advisor" },
      { to: "/compare", label: "Compare & Reports" },
      { to: "/valuation", label: "Valuation (IB)" },
      { to: "/social", label: "InstaFin" },
    ],
  },
  {
    group: "Connect & Understand",
    items: [
      { to: "/connections", label: "Connections" },
      { to: "/intelligence", label: "Financial Intelligence" },
      { to: "/xray", label: "Portfolio X-Ray" },
      { to: "/transactions", label: "Transactions" },
      { to: "/graph", label: "Intelligence Graph" },
      { to: "/reconciliation", label: "Reconciliation" },
      { to: "/economic", label: "Economic & Filings" },
    ],
  },
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
      { to: "/assistant", label: "Research Assistant" },
    ],
  },
  { group: "Output", items: [{ to: "/reports", label: "Reports" }] },
  {
    group: "Platform",
    items: [
      { to: "/lineage", label: "Data Lineage" },
      { to: "/audit", label: "Audit Log" },
      { to: "/developer", label: "Developer API" },
      { to: "/system", label: "System Health" },
      { to: "/settings", label: "Settings" },
    ],
  },
];

function Account() {
  const me = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const u = me.data?.user;
  if (!u) return <button className="btn primary sm" onClick={() => nav(`/login?next=${encodeURIComponent(window.location.pathname)}`)}>Sign in</button>;
  const logout = async () => {
    await api.post("/auth/logout");
    await qc.invalidateQueries({ queryKey: ["me"] });
    qc.invalidateQueries({ queryKey: ["social"] });
    setOpen(false);
  };
  return (
    <div className="account" ref={ref} style={{ position: "relative" }}>
      <button className="icon-btn" aria-label="Account menu" onClick={() => setOpen(!open)} style={{ width: 34, height: 34 }}><Avatar user={u} size={28} /></button>
      {open && (
        <div className="share-menu popover-in" role="menu" style={{ right: 0, left: "auto", transformOrigin: "top right" }}>
          <div className="small" style={{ padding: "8px 10px" }}><b>{u.display_name}</b><div className="xs muted">@{u.username}{u.email ? ` · ${u.email}` : ""}</div></div>
          <button role="menuitem" onClick={() => { setOpen(false); nav(`/social/u/${u.username}`); }}>My InstaFin profile</button>
          <button role="menuitem" onClick={() => { setOpen(false); nav("/social?mode=saved"); }}>Saved posts</button>
          <button role="menuitem" onClick={() => { setOpen(false); nav("/advisor"); }}>AI Advisor</button>
          <button role="menuitem" onClick={logout}>Sign out</button>
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
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  const location = useLocation();
  useEffect(() => {
    setDrawer(false);
  }, [location.pathname]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); }
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, []);
  const pages = NAV.flatMap((g) => g.items.map((it) => ({ ...it, group: g.group })));
  let idx = 0;
  return (
    <div className="app">
      <div className={`scrim ${drawer ? "open" : ""}`} onClick={() => setDrawer(false)} aria-hidden />
      <aside className={`sidebar ${drawer ? "open" : ""}`} aria-label="Navigation">
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
          <button className="btn ghost menu-btn" aria-label="Open navigation" onClick={() => setDrawer(true)}>☰</button>
          <span className="xs muted hide-sm">Research dataset</span>
          <select className="input hide-sm" style={{ width: 230 }} value={dataset?.id ?? ""} onChange={(e) => setDatasetId(Number(e.target.value))} aria-label="Research dataset">
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.code} · v{d.version}</option>)}
          </select>
          {dataset && (dataset.is_synthetic
            ? <span className="badge synthetic hide-sm" title="The selected research dataset is artificially generated">DEMO / SYNTHETIC DATA MODE</span>
            : <span className="badge info hide-sm">LIVE PUBLIC DATA · {dataset.source}</span>)}
          <span className="spacer hide-sm" />
          <button className="search-trigger" onClick={() => setPalette(true)} aria-label="Open command palette">
            <span aria-hidden>⌕</span><span className="grow">Search or jump to…</span><kbd className="hide-sm">Ctrl K</kbd>
          </button>
          <Notifications />
          <Account />
          <button className="btn ghost" aria-label="Toggle theme" title="Toggle theme"
            onClick={() => updateSettings({ theme: settings.theme === "dark" ? "light" : settings.theme === "light" ? "system" : "dark" })}>
            {settings.theme === "dark" ? "☾" : settings.theme === "light" ? "☀" : "◐"}
          </button>
        </header>
        <main className="content"><div key={location.pathname} className="route-enter">{children}</div></main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} pages={pages} />
      <Toaster />
    </div>
  );
}
