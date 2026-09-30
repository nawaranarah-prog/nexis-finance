import { Fragment, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { Data } from "plotly.js";
import Plot from "../components/Plot";
import { Card, PageHead, QueryView } from "../components/ui";
import { DataClassBadge, ScopeSelect } from "../components/connect";
import { INK, seriesColor } from "../components/charts";
import { useResolvedTheme } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { money, num, pct } from "../utils/format";

const LAYER_COLOR: Record<string, number> = { account: 6, portfolio: 0, asset: 2, industry: 3, factor: 4, risk: 7, benchmark: 1, regime: 5 };

export default function Graph() {
  const theme = useResolvedTheme();
  const [params, setParams] = useSearchParams();
  const [scope, setScope] = useState("all");
  const q = useQuery({ queryKey: ["intel", "graph", scope], queryFn: () => api.get<AnyObj>("/intelligence/graph", { scope }) });
  const selected = params.get("entity");
  return (
    <>
      <PageHead title="Nexis Financial Intelligence Graph" desc="Account → Portfolio → Asset → Industry → Factor → Risk → Benchmark → Market regime, generated from the relationships in your data. Click a node to inspect it."
        actions={<ScopeSelect value={scope} onChange={setScope} />} />
      <QueryView q={q} label="Building graph">
        {(g) => (
          <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 380px" }}>
            <GraphView g={g} theme={theme} selected={selected} onSelect={(id) => setParams({ entity: id })} />
            <EntityPanel id={selected} scope={scope} g={g} />
          </div>
        )}
      </QueryView>
    </>
  );
}

function GraphView({ g, theme, selected, onSelect }: { g: AnyObj; theme: "light" | "dark"; selected: string | null; onSelect: (id: string) => void }) {
  const { data, height } = useMemo(() => {
    const layers: string[] = g.layers;
    const byLayer: Record<string, AnyObj[]> = {};
    for (const n of g.nodes) (byLayer[n.type] ??= []).push(n);
    const pos: Record<string, [number, number]> = {};
    const maxN = Math.max(...Object.values(byLayer).map((l) => l.length), 1);
    layers.forEach((layer, li) => {
      const ns = (byLayer[layer] ?? []).sort((a, b) => (b.value ?? b.weight ?? 0) - (a.value ?? a.weight ?? 0));
      ns.forEach((n, i) => { pos[n.id] = [li, ns.length === 1 ? 0.5 : 1 - (i + 0.5) / ns.length]; });
    });
    const neighbours = new Set<string>();
    if (selected) for (const e of g.edges) { if (e.source === selected) neighbours.add(e.target); if (e.target === selected) neighbours.add(e.source); }
    const ex: (number | null)[] = [], ey: (number | null)[] = [], hx: (number | null)[] = [], hy: (number | null)[] = [];
    for (const e of g.edges) {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) continue;
      const hot = selected && (e.source === selected || e.target === selected);
      (hot ? hx : ex).push(a[0], b[0], null);
      (hot ? hy : ey).push(a[1], b[1], null);
    }
    const traces: Data[] = [
      { type: "scatter", mode: "lines", x: ex, y: ey, line: { width: 0.7, color: theme === "dark" ? "rgba(255,255,255,0.14)" : "rgba(0,0,0,0.12)" }, hoverinfo: "skip", showlegend: false },
      { type: "scatter", mode: "lines", x: hx, y: hy, line: { width: 2, color: seriesColor(theme, 0) }, hoverinfo: "skip", showlegend: false },
    ];
    for (const layer of layers) {
      const ns = byLayer[layer] ?? [];
      if (!ns.length) continue;
      traces.push({
        type: "scatter", mode: "text+markers", name: layer, x: ns.map((n) => pos[n.id][0]), y: ns.map((n) => pos[n.id][1]),
        text: ns.map((n) => n.label), textposition: "middle right", textfont: { size: 9.5, color: INK[theme].text2 },
        customdata: ns.map((n) => n.id), hovertemplate: "%{text}<extra>" + layer + "</extra>",
        marker: { size: ns.map((n) => (n.id === selected ? 16 : neighbours.has(n.id) ? 12 : 9)), color: seriesColor(theme, LAYER_COLOR[layer] ?? 0),
          line: { width: 1.5, color: INK[theme].surface }, opacity: ns.map((n) => (!selected || n.id === selected || neighbours.has(n.id) ? 1 : 0.35)) },
      } as Data);
    }
    return { data: traces, height: Math.max(460, maxN * 26 + 80) };
  }, [g, theme, selected]);
  return (
    <Card title={`${g.nodes.length} entities · ${g.edges.length} relationships`} sub={g.note}>
      <Plot height={height} data={data} onClick={(pt) => pt.customdata && onSelect(String(pt.customdata))}
        layout={{ hovermode: "closest", showlegend: true, legend: { orientation: "h", y: 1.04 },
          xaxis: { tickvals: g.layers.map((_: string, i: number) => i), ticktext: g.layers, range: [-0.3, g.layers.length - 0.2], showgrid: false, zeroline: false },
          yaxis: { visible: false, range: [-0.03, 1.03] }, margin: { l: 10, r: 10, t: 30, b: 30 } }} />
    </Card>
  );
}

function EntityPanel({ id, scope, g }: { id: string | null; scope: string; g: AnyObj }) {
  const [kind, ...rest] = (id ?? "").split(":");
  const key = rest.join(":");
  const drill = ["asset", "account", "industry"].includes(kind);
  const q = useQuery({ queryKey: ["intel", "entity", id, scope], queryFn: () => api.get<AnyObj>(`/intelligence/entity/${kind}/${encodeURIComponent(key)}`, { scope }), enabled: !!id && drill });
  const node = g.nodes.find((n: AnyObj) => n.id === id);
  const rels = id ? g.edges.filter((e: AnyObj) => e.source === id || e.target === id) : [];
  if (!id) return <Card title="Select an entity"><div className="state">Click a node — e.g. an asset — to see its weight, accounts, industry, correlations, risk contribution, factors, benchmark relationship and transactions.</div></Card>;
  return (
    <Card title={node?.label ?? id} sub={kind}>
      <div className="stack" style={{ gap: 8 }}>
        {node && <dl className="kv small">{Object.entries(node).filter(([k]) => !["id", "type", "label"].includes(k)).map(([k, v]) => (
          <Fragment key={k}><dt>{k}</dt><dd>{typeof v === "number" ? (k === "weight" ? pct(v, 2) : k === "value" ? money(v) : num(v, 3)) : String(v)}</dd></Fragment>))}</dl>}
        {drill && q.data?.kind === "asset" && (() => {
          const d = q.data;
          return (
            <>
              <dl className="kv small">
                <dt>Portfolio weight</dt><dd>{pct(d.position.weight, 2)}</dd>
                <dt>Value</dt><dd>{money(d.position.market_value)}</dd>
                <dt>Industry</dt><dd>{d.position.industry ?? "unclassified"}</dd>
                <dt>Volatility (ann.)</dt><dd>{pct(d.annualized_volatility, 1)}</dd>
                <dt>Beta to {d.benchmark}</dt><dd>{num(d.beta_to_benchmark, 2)}</dd>
                <dt>% of portfolio vol</dt><dd>{pct(d.risk_contribution?.pct_contribution, 1)}</dd>
                <dt>Data</dt><dd><DataClassBadge value={d.position.data_class} /></dd>
              </dl>
              <div className="small"><b>Held in</b>: {d.position.accounts.map((a: AnyObj) => `${a.account} (${num(a.quantity, 2)})`).join(", ")}</div>
              <div className="small"><b>Factors</b>: {d.factors.length ? d.factors.map((f: AnyObj) => f.factor).join(", ") : "no tercile tag"}</div>
              <div className="small"><b>Most correlated holdings</b>: {d.correlated_holdings.map((c: AnyObj) => `${c.symbol} ${num(c.correlation, 2)}`).join(" · ") || "–"}</div>
              <div className="small"><b>Transactions</b> ({d.transactions.length}):</div>
              <table className="dt"><tbody>{d.transactions.slice(-8).map((t: AnyObj) => <tr key={t.id}><td className="xs">{t.date}</td><td className="xs">{t.type}</td><td className="r xs">{num(t.quantity, 2)}</td><td className="r xs">{num(t.price, 2)}</td></tr>)}</tbody></table>
            </>
          );
        })()}
        {drill && q.data?.kind === "industry" && <div className="small">{q.data.holdings.map((h: AnyObj) => `${h.symbol} ${pct(h.weight, 1)}`).join(" · ")} — total {pct(q.data.weight, 1)}</div>}
        {drill && q.data?.kind === "account" && <div className="small">{q.data.holdings.map((h: AnyObj) => `${h.symbol} × ${num(h.quantity, 2)}`).join(" · ")}</div>}
        <div className="small text2"><b>Relationships ({rels.length})</b></div>
        <table className="dt"><tbody>{rels.slice(0, 30).map((e: AnyObj, i: number) => (
          <tr key={i}><td className="xs">{e.source === id ? "→" : "←"} {(e.source === id ? e.target : e.source).split(":").slice(1).join(":")}</td><td className="xs muted">{e.type}</td><td className="r xs">{e.weight == null ? "" : num(e.weight, 3)}</td></tr>))}</tbody></table>
      </div>
    </Card>
  );
}
