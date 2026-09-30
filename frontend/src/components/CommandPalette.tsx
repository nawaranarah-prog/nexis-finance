import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

interface Item { group: string; label: string; hint?: string; to: string }

/** ⌘K / Ctrl+K palette: jump to any page, or search stored assets, portfolios, experiments and backtests. */
export default function CommandPalette({ open, onClose, pages }: { open: boolean; onClose: () => void; pages: { to: string; label: string; group: string }[] }) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setQ(""); setActive(0); window.setTimeout(() => input.current?.focus(), 0); } }, [open]);
  useEffect(() => { const t = window.setTimeout(() => setDebounced(q.trim()), 160); return () => window.clearTimeout(t); }, [q]);
  const r = useQuery({ queryKey: ["search", debounced], queryFn: () => api.get<AnyObj>("/search", { q: debounced }), enabled: open && debounced.length > 0 });

  const items: Item[] = useMemo(() => {
    const norm = (x: string) => x.toLowerCase().replace(/[^a-z0-9]/g, "");
    const f = norm(q);
    const pageItems = pages.filter((p) => !f || norm(p.label).includes(f) || norm(p.group).includes(f))
      .slice(0, f ? 8 : 12).map((p) => ({ group: "Pages", label: p.label, hint: p.group, to: p.to }));
    const d = r.data;
    const found: Item[] = d ? [
      ...d.assets.map((x: AnyObj) => ({ group: "Assets", label: `${x.symbol} — ${x.name}`, hint: x.dataset, to: x.link })),
      ...d.portfolios.map((x: AnyObj) => ({ group: "Portfolios", label: x.name, to: x.link })),
      ...d.experiments.map((x: AnyObj) => ({ group: "Experiments", label: `${x.code} — ${x.name}`, hint: x.status, to: x.link })),
      ...d.backtests.map((x: AnyObj) => ({ group: "Backtests", label: x.name, hint: x.strategy, to: x.link })),
    ] : [];
    return [...pageItems, ...found];
  }, [q, r.data, pages]);

  useEffect(() => {
    setActive(0);
  }, [items.length]);
  if (!open) return null;
  const go = (it: Item) => { onClose(); nav(it.to); };
  let lastGroup = "";
  return (
    <div className="overlay" onMouseDown={onClose} role="dialog" aria-modal aria-label="Command palette">
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={input} className="palette-input" placeholder="Jump to a page, or search assets, portfolios, experiments…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
            if (e.key === "Enter" && items[active]) go(items[active]);
          }} aria-label="Command search" />
        <div className="palette-list">
          {items.map((it, i) => {
            const header = it.group !== lastGroup ? <div className="palette-group">{it.group}</div> : null;
            lastGroup = it.group;
            return (
              <div key={`${it.group}-${it.to}-${i}`}>
                {header}
                <div className={`palette-item ${i === active ? "active" : ""}`} onMouseEnter={() => setActive(i)} onClick={() => go(it)}>
                  <span>{it.label}</span>{it.hint && <span className="xs muted">{it.hint}</span>}
                </div>
              </div>
            );
          })}
          {debounced && r.isLoading && <div className="state">Searching…</div>}
          {items.length === 0 && !r.isLoading && <div className="state">No matches for “{q}”.</div>}
        </div>
        <div className="palette-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span></div>
      </div>
    </div>
  );
}
