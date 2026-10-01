import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { TYPE_LABEL } from "./market";
import { useT } from "../i18n";

/** One destination the research box can send the query to. Every option maps to an existing page. */
interface Option { key: string; kind: "advisor" | "compare" | "instrument"; title: string; meta: string; go: () => void }

const QUESTION = /\?|^(why|what|how|is|are|should|can|will|which|when|who|explain|analy[sz]e|tell|give|show)\b/i;
const COMPARE = /^(?:compare|vs\.?)\s+(.+)$/i;

/** "compare FAB and Emirates NBD" → ["FAB", "Emirates NBD"] */
function compareParts(q: string): string[] {
  const m = COMPARE.exec(q.trim()) ?? (/\bvs\.?\b/i.test(q) ? [q, q] : null);
  if (!m) return [];
  return m[1].split(/\s*(?:,|\band\b|\bvs\.?\b|\bversus\b|&|\bwith\b)\s*/i).map((s) => s.replace(/\b(for|over)\b.*$/i, "").trim()).filter((s) => s.length >= 2).slice(0, 6);
}

export interface ResearchSearchHandle { focus: () => void; run: (text: string) => void }

export const ResearchSearch = forwardRef<ResearchSearchHandle, { placeholder: string }>(function ResearchSearch({ placeholder }, ref) {
  const nav = useNavigate();
  const { t } = useT();
  const input = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(q.trim()), 200);
    return () => window.clearTimeout(id);
  }, [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const words = debounced.split(/\s+/).filter(Boolean).length;
  const parts = useMemo(() => compareParts(debounced), [debounced]);
  // Instrument lookups only make sense for short queries ("EMAAR", "First Abu Dhabi Bank"), not sentences.
  const lookup = useQuery({
    queryKey: ["mk-search", debounced],
    queryFn: () => api.get<AnyObj[]>("/markets/search", { q: debounced }),
    enabled: debounced.length >= 1 && words <= 4 && !parts.length && !debounced.endsWith("?"),
    staleTime: 3600_000,
  });
  const resolved = useQueries({
    queries: parts.map((p) => ({ queryKey: ["mk-search", p], queryFn: () => api.get<AnyObj[]>("/markets/search", { q: p }), staleTime: 3600_000 })),
  });
  const compareSyms = resolved.every((r) => r.data?.length) ? resolved.map((r) => r.data![0].symbol as string) : [];

  const ask = (text: string) => nav(`/advisor?q=${encodeURIComponent(text)}`);
  const options: Option[] = [];
  if (debounced) {
    const advisor: Option = { key: "ask", kind: "advisor", title: debounced, meta: t("Ask the AI advisor"), go: () => ask(debounced) };
    if (parts.length >= 2 && compareSyms.length >= 2) {
      options.push({ key: "cmp", kind: "compare", title: compareSyms.join(" · "), meta: t("Open in Compare & Reports"),
        go: () => nav(`/compare?s=${compareSyms.map(encodeURIComponent).join(",")}&p=1y`) });
    }
    const instruments: Option[] = (lookup.data ?? []).slice(0, 6).map((s) => ({
      key: `i-${s.symbol}`, kind: "instrument", title: s.symbol, meta: `${s.name} · ${TYPE_LABEL[s.type] ?? s.type}${s.exchange ? ` · ${s.exchange}` : ""}`,
      go: () => nav(`/markets/${encodeURIComponent(s.symbol)}`),
    }));
    // A question goes to the advisor first; a name or ticker goes to its research page first.
    if (QUESTION.test(debounced) || words >= 4) options.push(advisor, ...instruments);
    else options.push(...instruments, advisor);
  }
  const busy = lookup.isFetching || resolved.some((r) => r.isFetching);
  const choose = (o: Option | undefined) => { if (!o) return; setOpen(false); setQ(""); o.go(); };

  useImperativeHandle(ref, () => ({
    focus: () => input.current?.focus(),
    run: (text: string) => { setQ(text); setOpen(true); input.current?.focus(); },
  }));

  const show = open && !!debounced;
  return (
    <div className={`rs ${show ? "open" : ""}`} ref={box}>
      <form className="rs-field" role="search" onSubmit={(e) => {
        e.preventDefault();
        if (options[active]) choose(options[active]);
        else if (q.trim().length > 1) ask(q.trim());
      }}>
        <svg className="rs-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden><circle cx="7" cy="7" r="5.25" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        <input ref={input} value={q} placeholder={placeholder} aria-label={t("Search a market or ask a research question")} dir="auto"
          role="combobox" aria-expanded={show} aria-controls="rs-list" aria-autocomplete="list"
          aria-activedescendant={show && options[active] ? `rs-${options[active].key}` : undefined}
          onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, options.length - 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
            else if (e.key === "Escape") { setOpen(false); }
          }} />
        {busy ? <span className="rs-busy" aria-hidden /> : !q && <kbd className="rs-kbd hide-xs" aria-hidden>/</kbd>}
        <button className="rs-go" disabled={q.trim().length < 2} aria-label={t("Search")}>↵</button>
      </form>
      {show && (
        <div className="rs-list" id="rs-list" role="listbox">
          {options.map((o, i) => (
            <button key={o.key} id={`rs-${o.key}`} type="button" role="option" aria-selected={i === active}
              className={`rs-opt ${o.kind} ${i === active ? "active" : ""}`} onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); choose(o); }}>
              <span className="rs-kind">{o.kind === "advisor" ? "✦" : o.kind === "compare" ? "⇄" : "↗"}</span>
              <span className={`rs-title ${o.kind === "instrument" || o.kind === "compare" ? "mono" : ""}`} dir="auto">{o.title}</span>
              <span className="rs-meta">{o.meta}</span>
            </button>
          ))}
          {!options.length && <div className="rs-empty">{t("Searching…")}</div>}
          <div className="rs-foot">
            <span><kbd>↑</kbd><kbd>↓</kbd> {t("move")}</span><span><kbd>↵</kbd> {t("open")}</span><span><kbd>esc</kbd> {t("close")}</span>
          </div>
        </div>
      )}
    </div>
  );
});
