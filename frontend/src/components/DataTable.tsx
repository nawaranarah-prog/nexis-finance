import { useMemo, useState, type ReactNode } from "react";

export interface Column<T> {
  key: string;
  label: ReactNode;
  align?: "left" | "right" | "center";
  value?: (row: T) => unknown; // raw value for sorting / filtering / CSV
  render?: (row: T) => ReactNode;
  sortable?: boolean;
  wrap?: boolean;
  width?: number | string;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  pageSize?: number;
  filterable?: boolean;
  exportName?: string;
  onRowClick?: (row: T) => void;
  selected?: (row: T) => boolean;
  toolbar?: ReactNode;
  maxHeight?: number;
  empty?: ReactNode;
  initialSort?: { key: string; dir: "asc" | "desc" };
}

function raw<T>(c: Column<T>, r: T): unknown {
  return c.value ? c.value(r) : (r as Record<string, unknown>)[c.key];
}

function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Sortable, filterable, paginated table with client-side CSV export of the filtered rows. */
export default function DataTable<T>({
  rows, columns, pageSize = 25, filterable = true, exportName, onRowClick, selected, toolbar, maxHeight, empty, initialSort,
}: Props<T>) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(initialSort ?? null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    if (!filter.trim()) return rows;
    const f = filter.toLowerCase();
    return rows.filter((r) => columns.some((c) => String(raw(c, r) ?? "").toLowerCase().includes(f)));
  }, [rows, columns, filter]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return filtered;
    const m = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = raw(col, a), vb = raw(col, b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * m;
      return String(va).localeCompare(String(vb)) * m;
    });
  }, [filtered, sort, columns]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const p = Math.min(page, pages - 1);
  const view = sorted.slice(p * pageSize, (p + 1) * pageSize);

  const exportCsv = () => {
    const head = columns.map((c) => csvCell(typeof c.label === "string" ? c.label : c.key)).join(",");
    const body = sorted.map((r) => columns.map((c) => csvCell(raw(c, r))).join(",")).join("\n");
    const blob = new Blob([`${head}\n${body}\n`], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${exportName ?? "table"}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div>
      {(filterable || exportName || toolbar) && (
        <div className="dt-toolbar">
          {filterable && (
            <input className="input sm" style={{ maxWidth: 220 }} placeholder="Filter rows…" value={filter}
              onChange={(e) => { setFilter(e.target.value); setPage(0); }} aria-label="Filter table" />
          )}
          {toolbar}
          <span style={{ flex: 1 }} />
          <span className="xs muted">{sorted.length.toLocaleString()} rows</span>
          {exportName && <button className="btn sm" onClick={exportCsv}>CSV</button>}
        </div>
      )}
      <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
        <table className="dt">
          <thead>
            <tr>
              {columns.map((c) => {
                const sortable = c.sortable !== false;
                const active = sort?.key === c.key;
                return (
                  <th key={c.key} className={`${sortable ? "sortable" : ""} ${c.align === "right" ? "r" : c.align === "center" ? "c" : ""}`}
                    style={c.width ? { width: c.width } : undefined}
                    aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
                    onClick={() => sortable && setSort(active && sort!.dir === "desc" ? { key: c.key, dir: "asc" } : { key: c.key, dir: "desc" })}>
                    {c.label}{active ? (sort!.dir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {view.map((r, i) => (
              <tr key={i} className={`${onRowClick ? "clickable" : ""} ${selected?.(r) ? "sel" : ""}`} onClick={() => onRowClick?.(r)}>
                {columns.map((c) => (
                  <td key={c.key} className={`${c.align === "right" ? "r" : c.align === "center" ? "c" : ""} ${c.wrap ? "wrap" : ""}`}>
                    {c.render ? c.render(r) : String(raw(c, r) ?? "–")}
                  </td>
                ))}
              </tr>
            ))}
            {view.length === 0 && (
              <tr><td colSpan={columns.length}><div className="state">{empty ?? "No rows."}</div></td></tr>
            )}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="dt-foot">
          <span>Page {p + 1} of {pages}</span>
          <span className="row">
            <button className="btn sm" disabled={p === 0} onClick={() => setPage(0)}>«</button>
            <button className="btn sm" disabled={p === 0} onClick={() => setPage(p - 1)}>Prev</button>
            <button className="btn sm" disabled={p >= pages - 1} onClick={() => setPage(p + 1)}>Next</button>
            <button className="btn sm" disabled={p >= pages - 1} onClick={() => setPage(pages - 1)}>»</button>
          </span>
        </div>
      )}
    </div>
  );
}
