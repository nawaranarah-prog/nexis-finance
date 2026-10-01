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

/** Every route in the app, grouped by task. Discover and Research stay open; the rest fold away until needed. */
export const NAV: { group: string; fixed?: boolean; items: { to: string; label: string }[] }[] = [
  {
    group: "Discover",
    fixed: true,
    items: [
      { to: "/", label: "Home" },
      { to: "/markets", label: "Markets" },
      { to: "/finstagram", label: "Finstagram" },
      { to: "/advisor", label: "AI Advisor" },
      { to: "/pulse", label: "Nexis Pulse" },
    ],
  },
  {
    group: "Research",
    fixed: true,
    items: [
      { to: "/asset-research", label: "Asset Research" },
      { to: "/compare", label: "Compare & Reports" },
      { to: "/valuation", label: "Valuation" },
      { to: "/reports", label: "Reports" },
      { to: "/research", label: "Research Overview" },
    ],
  },
  {
    group: "Portfolio",
    items: [
      { to: "/portfolio-lab", label: "Portfolio Lab" },
      { to: "/xray", label: "Portfolio X-Ray" },
      { to: "/risk", label: "Risk Analytics" },
      { to: "/stress-testing", label: "Stress Testing" },
      { to: "/factors", label: "Factor Analytics" },
      { to: "/transactions", label: "Transactions" },
      { to: "/reconciliation", label: "Reconciliation" },
    ],
  },
  {
    group: "Intelligence",
    items: [
      { to: "/intelligence", label: "Financial Intelligence" },
      { to: "/connections", label: "Connections" },
      { to: "/graph", label: "Intelligence Graph" },
      { to: "/assistant", label: "Research Assistant" },
    ],
  },
  {
    group: "Quant",
    items: [
      { to: "/strategies", label: "Quant Strategies" },
      { to: "/backtesting", label: "Backtesting" },
      { to: "/machine-learning", label: "Machine Learning" },
      { to: "/regimes", label: "Regime Analysis" },
      { to: "/anomalies", label: "Anomaly Detection" },
      { to: "/experiments", label: "Research Experiments" },
    ],
  },
  {
    group: "Data",
    items: [
      { to: "/market-data", label: "Market Data" },
      { to: "/data-quality", label: "Data Quality" },
      { to: "/economic", label: "Economic & Filings" },
      { to: "/lineage", label: "Data Lineage" },
    ],
  },
  {
    group: "System",
    items: [
      { to: "/settings", label: "Settings" },
      { to: "/system", label: "System Health" },
      { to: "/audit", label: "Audit Log" },
      { to: "/developer", label: "Developer API" },
    ],
  },
];

const isActive = (to: string, path: string) => (to === "/" ? path === "/" : path === to || path.startsWith(`${to}/`));

/** The sidebar navigation. Folded groups keep their links out of the tab order. */
function SideNav() {
  const { t } = useT();
  const { pathname } = useLocation();
  const [open, setOpen] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("nexis.nav.open") ?? "[]") as string[]; } catch { return []; }
  });
  const current = NAV.find((g) => g.items.some((it) => isActive(it.to, pathname)))?.group;
  const toggle = (g: string) => setOpen((o) => {
    const next = o.includes(g) ? o.filter((x) => x !== g) : [...o, g];
    try { localStorage.setItem("nexis.nav.open", JSON.stringify(next)); } catch { /* storage unavailable */ }
    return next;
  });
  return (
    <nav className="nav" aria-label="Main">
      {NAV.map((g) => {
        const expanded = !!g.fixed || open.includes(g.group) || current === g.group;
        return (
          <div key={g.group} className={`nav-sec ${expanded ? "open" : ""}`}>
            {g.fixed ? <div className="nav-group">{t(g.group)}</div> : (
              <button type="button" className="nav-group toggle" aria-expanded={expanded} onClick={() => toggle(g.group)} disabled={current === g.group}>
                {t(g.group)}
                <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><path d="M3 2l3 3-3 3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            )}
            <div className="nav-items" inert={!expanded}>
              <div>
                {g.items.map((it) => (
                  <NavLink key={it.to} to={it.to} end={it.to === "/"} className={() => (isActive(it.to, pathname) ? "active" : "")}>{t(it.label)}</NavLink>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </nav>
  );
}

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
      <button className="icon-btn top-icon" onClick={() => setOpen(!open)} aria-label={`Notifications, ${unread} unread`} aria-expanded={open}>
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><path d="M4 6.5a4 4 0 0 1 8 0c0 3 1.2 4.3 1.6 4.8H2.4C2.8 10.8 4 9.5 4 6.5Z" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinejoin="round" /><path d="M6.5 13.3a1.6 1.6 0 0 0 3 0" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" /></svg>
      </button>
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
  // Each page gets its own title and description for browser tabs, bookmarks and search results.
  useEffect(() => {
    const path = decodeURIComponent(location.pathname);
    const sym = /^\/(markets|valuation|finstagram\/s)\/([^/]+)/.exec(path);
    const page = NAV.flatMap((g) => g.items).find((it) => (it.to === "/" ? path === "/" : path.startsWith(it.to)));
    let title = "Nexis Finance — UAE stocks, bonds, AI financial advisor and Finstagram";
    let desc = "Live prices, charts and analysis for every ADX and DFM stock, UAE bonds and sukuk, and global markets, plus an AI financial advisor and the Finstagram investor feed.";
    if (sym && sym[1] === "markets") {
      title = `${sym[2]} share price, chart and analysis · Nexis Finance`;
      desc = `${sym[2]} live price, interactive chart, fundamentals, analyst ratings and latest news on Nexis Finance.`;
    } else if (sym && sym[1] === "valuation") {
      title = `${sym[2]} valuation — DCF and peer multiples · Nexis Finance`;
      desc = `Discounted cash flow and peer-multiple valuation of ${sym[2]}, with a downloadable report.`;
    } else if (sym) {
      title = `${sym[2]} on Finstagram — posts and news · Nexis Finance`;
    } else if (path.startsWith("/advisor")) {
      title = "AI Financial Advisor — ask about any UAE or global stock · Nexis Finance";
      desc = "Ask an AI financial advisor about Emaar, FAB, Aldar or any stock, bond or sukuk. It checks live prices, news and analyst ratings before it answers.";
    } else if (path.startsWith("/pulse/")) {
      const s = path.split("/")[2];
      title = `${s} — what investors are saying on Reddit, X and StockTwits · Nexis Pulse`;
      desc = `Latest public posts about ${s} from Reddit, X and StockTwits, with an AI summary of the arguments for and against.`;
    } else if (path.startsWith("/finstagram")) {
      title = "Finstagram — the investor feed · Nexis Finance";
      desc = "Real market news, charts and videos from UAE and global sources. Like, save, comment and follow stocks and news pages.";
    } else if (page && page.to !== "/") {
      title = `${page.label} · Nexis Finance`;
    }
    document.title = title;
    document.querySelector('meta[name="description"]')?.setAttribute("content", desc);
    document.querySelector('link[rel="canonical"]')?.setAttribute("href", `https://nexis-finance-five.vercel.app${location.pathname}`);
  }, [location.pathname]);
  // The research-dataset picker only matters on the research pages, not on Home, Finstagram, markets or the advisor.
  const consumer = ["/finstagram", "/advisor", "/pulse", "/markets", "/compare", "/valuation", "/login", "/settings"];
  const researchPage = location.pathname !== "/" && !consumer.some((p) => location.pathname.startsWith(p));
  const pages = NAV.flatMap((g) => g.items.map((it) => ({ ...it, label: t(it.label), group: t(g.group) })));
  return (
    <div className="app">
      <div className={`scrim ${drawer ? "open" : ""}`} onClick={() => setDrawer(false)} aria-hidden />
      <aside className={`sidebar ${drawer ? "open" : ""}`} aria-label="Navigation">
        <Link to="/" className="brand" aria-label="Nexis Finance home">
          <img src="/favicon.svg" alt="" width={22} height={22} />
          <span className="brand-name" dir="ltr">Nexis <span>Finance</span></span>
        </Link>
        <SideNav />
        <div className="side-foot">{t("Research software · not investment advice")}</div>
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
          <Link to="/finstagram" className={`top-link hide-sm ${location.pathname.startsWith("/finstagram") ? "on" : ""}`}><span className="top-link-mark" aria-hidden>F</span>{t("Finstagram")}</Link>
          <Link to="/advisor" className={`top-link hide-sm ${location.pathname.startsWith("/advisor") ? "on" : ""}`}><span className="top-link-mark ai" aria-hidden>✦</span>{t("AI Advisor")}</Link>
          <Notifications />
          <Account />
          <button className="icon-btn top-icon" aria-label={`Theme: ${settings.theme}`} title={`Theme: ${settings.theme}`}
            onClick={() => updateSettings({ theme: settings.theme === "dark" ? "light" : settings.theme === "light" ? "system" : "dark" })}>
            {settings.theme === "dark"
              ? <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinejoin="round" /></svg>
              : settings.theme === "light"
                ? <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.35" /><path d="M8 1.5v1.4M8 13.1v1.4M1.5 8h1.4M13.1 8h1.4M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" /></svg>
                : <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.35" /><path d="M8 2a6 6 0 0 0 0 12Z" fill="currentColor" /></svg>}
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
