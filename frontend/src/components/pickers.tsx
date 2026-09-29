import { useMemo, useState } from "react";
import type { Asset, Portfolio } from "../types/api";

/** Searchable, sector-grouped asset checklist. */
export function AssetChecklist({ assets, value, onChange, includeBenchmarks = true, height = 240 }: {
  assets: Asset[]; value: string[]; onChange: (v: string[]) => void; includeBenchmarks?: boolean; height?: number;
}) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const f = q.toLowerCase();
    const g: Record<string, Asset[]> = {};
    for (const a of assets) {
      if (!includeBenchmarks && a.is_benchmark) continue;
      if (f && !`${a.symbol} ${a.name} ${a.sector}`.toLowerCase().includes(f)) continue;
      const k = a.is_benchmark ? "Benchmarks / indices" : a.sector ?? "Other";
      (g[k] ??= []).push(a);
    }
    return Object.entries(g).sort(([a], [b]) => a.localeCompare(b));
  }, [assets, q, includeBenchmarks]);
  const set = new Set(value);
  const toggle = (s: string) => onChange(set.has(s) ? value.filter((x) => x !== s) : [...value, s]);
  const visible = groups.flatMap(([, list]) => list.map((a) => a.symbol));
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row">
        <input className="input sm" style={{ flex: 1 }} placeholder="Search assets…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search assets" />
        <button className="btn sm" type="button" onClick={() => onChange(Array.from(new Set([...value, ...visible])))}>All</button>
        <button className="btn sm" type="button" onClick={() => onChange(value.filter((v) => !visible.includes(v)))}>None</button>
      </div>
      <div className="checklist" style={{ maxHeight: height }}>
        {groups.map(([g, list]) => (
          <div key={g}>
            <div className="grp">{g}</div>
            {list.map((a) => (
              <label key={a.symbol}>
                <input type="checkbox" checked={set.has(a.symbol)} onChange={() => toggle(a.symbol)} />
                <span className="mono" style={{ width: 52 }}>{a.symbol}</span>
                <span className="text2 small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</span>
              </label>
            ))}
          </div>
        ))}
        {groups.length === 0 && <div className="state">No matches.</div>}
      </div>
      <div className="xs muted">{value.length} selected</div>
    </div>
  );
}

export function AssetSelect({ assets, value, onChange, benchmarksOnly, allowEmpty, id }: {
  assets: Asset[]; value: string; onChange: (v: string) => void; benchmarksOnly?: boolean; allowEmpty?: string; id?: string;
}) {
  const list = benchmarksOnly ? assets.filter((a) => a.is_benchmark) : assets;
  return (
    <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowEmpty !== undefined && <option value="">{allowEmpty}</option>}
      {list.map((a) => (
        <option key={a.symbol} value={a.symbol}>{a.symbol} — {a.name}</option>
      ))}
    </select>
  );
}

export function PortfolioSelect({ portfolios, value, onChange, allowEmpty }: {
  portfolios: Portfolio[]; value: number | null; onChange: (v: number | null) => void; allowEmpty?: string;
}) {
  return (
    <select className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} aria-label="Portfolio">
      {allowEmpty !== undefined && <option value="">{allowEmpty}</option>}
      {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  );
}

export function DateRange({ start, end, onChange }: { start: string; end: string; onChange: (s: string, e: string) => void }) {
  return (
    <div className="row" style={{ gap: 4 }}>
      <input type="date" className="input sm" style={{ width: 138 }} value={start} onChange={(e) => onChange(e.target.value, end)} aria-label="Start date" />
      <span className="muted">→</span>
      <input type="date" className="input sm" style={{ width: 138 }} value={end} onChange={(e) => onChange(start, e.target.value)} aria-label="End date" />
      {(start || end) && <button className="btn sm ghost" onClick={() => onChange("", "")}>Clear</button>}
    </div>
  );
}

/** Default benchmark: prefer an equity index over other benchmark series (e.g. a bond index). */
export function defaultBenchmark(assets: Asset[] | undefined): string {
  const b = (assets ?? []).filter((a) => a.is_benchmark);
  return (b.find((a) => a.asset_type === "index") ?? b[0])?.symbol ?? "";
}
