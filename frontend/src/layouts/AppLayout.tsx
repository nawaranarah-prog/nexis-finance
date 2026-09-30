import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../services/api";
import { Avatar, useMe } from "../components/market";
import CommandPalette from "../components/CommandPalette";
import { Toaster } from "../components/toast";
import { useWorkspace } from "../hooks/workspace";
import type { Notification } from "../types/api";
import { dt } from "../utils/format";
import { useT } from "../i18n";

export const NAV: { group: string; items: { to: string; label: string }[] }[] = [
  {
    group: "Discover",
    items: [
      { to: "/", label: "Home" },
      { to: "/finstagram", label: "Finstagram" },
      { to: "/advisor", label: "AI Advisor" },
      { to: "/markets", label: "UAE & Global Markets" },
      { to: "/compare", label: "Compare & Reports" },
      { to: "/valuation", label: "Valuation (IB)" },
    ],
  },
  { group: "Research workspace", items: [{ to: "/research", label: "Research Overview" }] },
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
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const u = me.data?.user;
  if (!u) return <button className="btn primary sm" onClick={() => nav(`/login?next=${encodeURIComponent(window.location.pathname)}`)}>{t("Sign in")}</button>;
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
          <button role="menuitem" onClick={() => { setOpen(false); nav(`/finstagram/u/${u.username}`); }}>{t("My Finstagram profile")}</button>
          <button role="menuitem" onClick={() => { setOpen(false); nav("/finstagram?mode=saved"); }}>{t("Saved posts")}</button>
          <button role="menuitem" onClick={() => { setOpen(false); nav("/advisor"); }}>{t("AI Advisor")}</button>
          <button role="menuitem" onClick={() => { setOpen(false); nav("/settings"); }}>{t("Account settings")}</button>
          <button role="menuitem" onClick={logout}>{t("Sign out")}</button>
        </div>
      )}
    </div>
  );
}

/** Instagram-style bottom navigation on phones. */
function TabBar() {
  const me = useMe().data?.user;
  const { t: tr } = useT();
  const loc = useLocation();
  const on = (p: string) => (p === "/" ? loc.pathname === "/" : loc.pathname.startsWith(p));
  const tabs = [
    { to: "/", label: "Home", icon: "⌂" },
    { to: "/markets", label: "Markets", icon: "↗" },
    { to: "/finstagram", label: "Finstagram", icon: "F", primary: true },
    { to: "/advisor", label: "Advisor", icon: "✦" },
    { to: me ? `/finstagram/u/${me.username}` : "/login", label: me ? "Me" : "Sign in", icon: "◉" },
  ];
  return (
    <nav className="tabbar" aria-label="Main">
      {tabs.map((t) => (
        <Link key={t.label} to={t.to} className={`tab ${on(t.to) ? "on" : ""} ${t.primary ? "primary" : ""}`}>
          <span className="tab-icon" aria-hidden>{t.icon}</span>
          <span className="tab-label">{tr(t.label)}</span>
        </Link>
      ))}
    </nav>
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
  const { t } = useT();
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
  // The research-dataset picker only matters on the research pages, not on Home, Finstagram, markets or the advisor.
  const consumer = ["/finstagram", "/advisor", "/markets", "/compare", "/valuation", "/login"];
  const researchPage = location.pathname !== "/" && !consumer.some((p) => location.pathname.startsWith(p));
  const pages = NAV.flatMap((g) => g.items.map((it) => ({ ...it, label: t(it.label), group: t(g.group) })));
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
              <div className="nav-group">{t(g.group)}</div>
              {g.items.map((it) => {
                idx += 1;
                return (
                  <NavLink key={it.to} to={it.to} end={it.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
                    <span className="nav-idx">{idx}</span>{t(it.label)}
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
          {researchPage && (
            <>
          <span className="xs muted hide-sm">Research dataset</span>
          <select className="input hide-sm" style={{ width: 230 }} value={dataset?.id ?? ""} onChange={(e) => setDatasetId(Number(e.target.value))} aria-label="Research dataset">
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.code} · v{d.version}</option>)}
          </select>
          {dataset && (dataset.is_synthetic
            ? <span className="badge synthetic hide-sm" title="The selected research dataset is artificially generated">DEMO / SYNTHETIC DATA MODE</span>
            : <span className="badge info hide-sm">LIVE PUBLIC DATA · {dataset.source}</span>)}
            </>
          )}
          <span className="spacer hide-sm" />
          <button className="search-trigger" onClick={() => setPalette(true)} aria-label="Open command palette">
            <span aria-hidden>⌕</span><span className="grow">{t("Search or jump to…")}</span><kbd className="hide-sm">Ctrl K</kbd>
          </button>
          <Link to="/finstagram" className="top-pill finsta hide-sm" aria-label="Open Finstagram"><span className="top-pill-icon">F</span>{t("Finstagram")}</Link>
          <Link to="/advisor" className="top-pill ai hide-sm" aria-label="Open the AI advisor"><span className="top-pill-icon">✦</span>{t("AI Advisor")}</Link>
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
      <TabBar />
      <Toaster />
    </div>
  );
}
