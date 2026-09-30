import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

export const fmtPrice = (v: number | null | undefined, ccy?: string | null, d?: number) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const digits = d ?? (Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4);
  const s = v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return ccy ? `${s} ${ccy}` : s;
};

export const fmtBig = (v: number | null | undefined) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  return a >= 1e12 ? `${(v / 1e12).toFixed(2)}T` : a >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : a >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : v.toFixed(0);
};

export function Change({ pct, abs, ccy }: { pct?: number | null; abs?: number | null; ccy?: string | null }) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return <span className="muted">—</span>;
  const cls = pct > 0 ? "pos" : pct < 0 ? "neg" : "";
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return (
    <span className={`chg num ${cls}`}>
      {abs !== undefined && abs !== null ? `${sign}${Math.abs(abs).toFixed(2)}${ccy ? "" : ""} ` : ""}
      ({sign}{Math.abs(pct * 100).toFixed(2)}%)
    </span>
  );
}

export const TYPE_LABEL: Record<string, string> = {
  equity: "Stock", etf: "ETF", index: "Index", mutualfund: "Fund", fund: "Fund", currency: "FX", cryptocurrency: "Crypto", future: "Future", futures: "Future",
};

/** Debounced instrument search with keyboard navigation. */
export function SymbolSearch({ onPick, placeholder = "Search any stock, ETF, index, bond fund, currency or crypto…", autoFocus, compact }: {
  onPick: (s: AnyObj) => void; placeholder?: string; autoFocus?: boolean; compact?: boolean;
}) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 220);
    return () => window.clearTimeout(t);
  }, [q]);
  const res = useQuery({ queryKey: ["mk-search", debounced], queryFn: () => api.get<AnyObj[]>("/markets/search", { q: debounced }), enabled: debounced.length >= 1, staleTime: 3600_000 });
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const items = res.data ?? [];
  const pick = (s: AnyObj) => { onPick(s); setQ(""); setOpen(false); };
  return (
    <div className={`sym-search ${compact ? "compact" : ""}`} ref={box}>
      <input className="input" value={q} placeholder={placeholder} autoFocus={autoFocus} aria-label="Search instruments"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter") {
            e.preventDefault();
            if (items[active]) pick(items[active]);
            else if (/^[A-Za-z0-9^=.\-]{1,20}$/.test(q.trim())) pick({ symbol: q.trim().toUpperCase(), name: q.trim().toUpperCase() });
          } else if (e.key === "Escape") setOpen(false);
        }} />
      {open && debounced && (
        <div className="sym-results popover-in" role="listbox">
          {res.isLoading && <div className="sym-empty"><span className="spinner" /> Searching…</div>}
          {res.isError && <div className="sym-empty neg">{errorMessage(res.error)}</div>}
          {!res.isLoading && !res.isError && items.length === 0 && <div className="sym-empty">No matches. Dubai tickers end in .AE (e.g. EMAAR.AE).</div>}
          {items.map((s, i) => (
            <button key={s.symbol} role="option" aria-selected={i === active} className={`sym-row ${i === active ? "active" : ""}`}
              onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(s); }}>
              <span className="mono sym-code">{s.symbol}</span>
              <span className="sym-name">{s.name}</span>
              <span className="sym-meta">{TYPE_LABEL[s.type] ?? s.type} · {s.exchange}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ accounts

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<{ user: AnyObj | null }>("/auth/me"), staleTime: 60_000 });
}

export function Avatar({ user, size = 36 }: { user: AnyObj | null | undefined; size?: number }) {
  const name = user?.display_name || user?.username || "?";
  const hue = [...(user?.username ?? "x")].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);
  return user?.avatar_url
    ? <img className="avatar" src={user.avatar_url} alt="" width={size} height={size} style={{ width: size, height: size }} />
    : <span className="avatar" aria-hidden style={{ width: size, height: size, fontSize: size * 0.42, background: `hsl(${hue} 55% 46%)` }}>{name.slice(0, 1).toUpperCase()}</span>;
}
