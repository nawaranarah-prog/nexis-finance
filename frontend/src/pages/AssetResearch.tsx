import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Field, PageHead, QueryView, Seg } from "../components/ui";
import { AssetChecklist, AssetSelect, DateRange, defaultBenchmark } from "../components/pickers";
import { dateAxis, INK, seriesColor } from "../components/charts";
import { line, MetricStrip, PCT_AXIS } from "../components/metrics";
import { useAssets } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { num, pct, spct, tone } from "../utils/format";

export default function AssetResearch() {
  const { datasetId } = useWorkspace();
  const theme = useResolvedTheme();
  const [params, setParams] = useSearchParams();
  const assets = useAssets(datasetId);
  const symbol = params.get("symbol") ?? assets.data?.find((a) => !a.is_benchmark)?.symbol ?? "";
  const benchDefault = defaultBenchmark(assets.data);
  const [bench, setBench] = useState("");
  const [window_, setWindow] = useState(60);
  const [freq, setFreq] = useState<"daily" | "weekly" | "monthly">("daily");
  const [range, setRange] = useState({ start: "", end: "" });
  const [compare, setCompare] = useState<string[]>([]);
  const b = bench || benchDefault;

  const q = useQuery({
    queryKey: ["asset-research", datasetId, symbol, b, window_, freq, range],
    queryFn: () => api.get<AnyObj>(`/research/assets/${symbol}`, { dataset_id: datasetId, benchmark: b, window: window_, frequency: freq, start: range.start, end: range.end }),
    enabled: !!symbol && datasetId != null,
  });
  const cmpSyms = compare.length ? compare : [];
  const cmp = useQuery({
    queryKey: ["compare-assets", datasetId, cmpSyms, range],
    queryFn: () => api.get<AnyObj>("/research/compare-assets", { dataset_id: datasetId, symbols: cmpSyms.join(","), start: range.start, end: range.end }),
    enabled: cmpSyms.length > 0,
  });

  return (
    <>
      <PageHead title="Asset Research" desc="Price history, returns, drawdowns and rolling risk for a single asset versus a benchmark."
        actions={
          <>
            <div style={{ width: 260 }}><AssetSelect assets={assets.data ?? []} value={symbol} onChange={(s) => setParams({ symbol: s })} /></div>
            <div style={{ width: 200 }}><AssetSelect assets={assets.data ?? []} value={b} onChange={setBench} benchmarksOnly /></div>
            <Seg value={window_} onChange={setWindow} options={[20, 60, 120, 252].map((w) => ({ value: w, label: `${w}d` }))} />
            <DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} />
          </>
        } />
      <QueryView q={q} label="Loading asset analytics">
        {(d) => {
          const s = d.series;
          return (
            <div className="stack">
              <div className="row small text2">
                <b style={{ color: "var(--text)" }}>{d.symbol}</b> {d.name} · {d.asset_type}{d.sector ? ` · ${d.sector}` : ""}
                {d.attributes?.synthetic && <span className="badge synthetic">synthetic</span>}
                {d.attributes?.listed_from && <span className="badge">listed from {d.attributes.listed_from}</span>}
                {d.attributes?.delisted_after && <span className="badge bad">delisted after {d.attributes.delisted_after}</span>}
                {d.missing_bars_in_window > 0 && <span className="badge warn">{d.missing_bars_in_window} missing bars in window</span>}
              </div>
              <MetricStrip summary={d.summary} keys={["cumulative_return", "annualized_return", "annualized_volatility", "sharpe_ratio", "sortino_ratio", "max_drawdown", "beta", "correlation"]} />
              <Card title="Price" sub="OHLC candles with 50/200-day moving averages (adjusted close)">
                <Plot height={360} data={[
                  { type: "candlestick", x: s.dates, open: s.open, high: s.high, low: s.low, close: s.close, name: "OHLC",
                    increasing: { line: { color: seriesColor(theme, 2), width: 1 } }, decreasing: { line: { color: seriesColor(theme, 7), width: 1 } } } as never,
                  line(s.dates, s.ma_50, "MA 50", seriesColor(theme, 0), { line: { width: 1.5, color: seriesColor(theme, 0) } }),
                  line(s.dates, s.ma_200, "MA 200", seriesColor(theme, 3), { line: { width: 1.5, color: seriesColor(theme, 3) } }),
                ]} layout={{ xaxis: { ...dateAxis(theme), rangeslider: { visible: false } }, yaxis: { title: { text: "Price" } } }} />
              </Card>
              <div className="grid g2">
                <Card title="Volume">
                  <Plot height={220} data={[{ type: "bar", x: s.dates, y: s.volume, name: "Volume", marker: { color: seriesColor(theme, 0) }, hovertemplate: "%{y:,.0f}<extra></extra>" }]}
                    layout={{ xaxis: dateAxis(theme, false), yaxis: { tickformat: ".2s" }, bargap: 0, showlegend: false }} />
                </Card>
                <Card title="Cumulative return" sub={`vs ${d.benchmark}`}>
                  <Plot height={220} data={[
                    line(s.dates, s.cumulative_return, d.symbol, seriesColor(theme, 0)),
                    ...(s.benchmark_cumulative_return ? [line(s.dates, s.benchmark_cumulative_return, d.benchmark, INK[theme].neutral)] : []),
                  ]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS }} />
                </Card>
                <Card title="Drawdown">
                  <Plot height={220} data={[line(s.dates, s.drawdown, "Drawdown", seriesColor(theme, 7), { fill: "tozeroy", fillcolor: "rgba(227,73,72,0.14)" })]}
                    layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false }} />
                </Card>
                <Card title={`Rolling ${window_}-day volatility`} sub="annualised">
                  <Plot height={220} data={[line(s.dates, s.rolling_volatility, "Volatility", seriesColor(theme, 0))]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false }} />
                </Card>
                <Card title={`Rolling ${window_}-day beta and correlation`} sub={`vs ${d.benchmark}`}>
                  <Plot height={220} data={[
                    line(s.dates, s.rolling_beta, "Beta", seriesColor(theme, 0)),
                    line(s.dates, s.rolling_correlation, "Correlation", seriesColor(theme, 1)),
                  ]} layout={{ xaxis: dateAxis(theme, false), yaxis: { hoverformat: ".2f" } }} />
                </Card>
                <Card title="Return distribution" actions={<Seg value={freq} onChange={setFreq} options={[{ value: "daily", label: "Daily" }, { value: "weekly", label: "Weekly" }, { value: "monthly", label: "Monthly" }]} />}>
                  <Plot height={220} data={[{
                    type: "bar", x: d.distribution.histogram.edges.slice(0, -1).map((e: number, i: number) => (e + d.distribution.histogram.edges[i + 1]) / 2),
                    y: d.distribution.histogram.counts, marker: { color: seriesColor(theme, 0) }, hovertemplate: "%{x:.2%}: %{y}<extra></extra>", name: "count",
                  }]} layout={{ xaxis: { tickformat: ".1%" }, bargap: 0.05, hovermode: "closest", showlegend: false }} />
                  <div className="xs muted">n = {d.distribution.count} · mean {spct(d.distribution.mean)} · std {pct(d.distribution.std)}</div>
                </Card>
              </div>
            </div>
          );
        }}
      </QueryView>
      <div className="grid g-side" style={{ marginTop: 12 }}>
        <Card title="Compare assets">
          <Field label="Assets"><AssetChecklist assets={assets.data ?? []} value={compare} onChange={setCompare} height={300} /></Field>
        </Card>
        <Card title="Normalised performance" sub="growth of 1 from the first common date">
          {cmp.data ? (
            <>
              <Plot height={320} data={cmpSyms.map((sym, i) => line(cmp.data!.dates, cmp.data!.normalized[sym], sym, seriesColor(theme, i)))} layout={{ xaxis: dateAxis(theme), yaxis: { hoverformat: ".3f" } }} />
              <DataTable<AnyObj> rows={cmp.data.assets} filterable={false} exportName="asset-comparison" columns={[
                { key: "symbol", label: "Symbol", render: (r) => <b className="mono">{r.symbol}</b> },
                { key: "sector", label: "Sector" },
                { key: "cumulative_return", label: "Cumulative", align: "right", render: (r) => <span className={tone(r.cumulative_return)}>{spct(r.cumulative_return)}</span> },
                { key: "annualized_return", label: "Ann. return", align: "right", render: (r) => spct(r.annualized_return) },
                { key: "annualized_volatility", label: "Ann. vol", align: "right", render: (r) => pct(r.annualized_volatility) },
                { key: "sharpe_ratio", label: "Sharpe", align: "right", render: (r) => num(r.sharpe_ratio, 2) },
                { key: "max_drawdown", label: "Max DD", align: "right", render: (r) => spct(r.max_drawdown) },
              ]} />
              {cmpSyms.length > 8 && <div className="xs muted">More than 8 series share the final palette colour; use the table or fewer assets for identity.</div>}
            </>
          ) : <div className="state">Select assets to compare.</div>}
        </Card>
      </div>
    </>
  );
}
