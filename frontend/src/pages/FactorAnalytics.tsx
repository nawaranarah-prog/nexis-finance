import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Kpi, PageHead, QueryView } from "../components/ui";
import { DateRange, PortfolioSelect } from "../components/pickers";
import { dateAxis, diverging, seriesColor } from "../components/charts";
import { line, PCT_AXIS } from "../components/metrics";
import { usePortfolios } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { num, pct, spct, tone } from "../utils/format";

export default function FactorAnalytics() {
  const { datasetId, portfolioId, setPortfolioId } = useWorkspace();
  const theme = useResolvedTheme();
  const ports = usePortfolios(datasetId);
  const pid = portfolioId ?? ports.data?.[0]?.id ?? null;
  const [range, setRange] = useState({ start: "", end: "" });
  const [win, setWin] = useState(126);
  const q = useQuery({
    queryKey: ["factors", datasetId, pid, range, win],
    queryFn: () => api.get<AnyObj>("/research/factors", { dataset_id: datasetId, portfolio_id: pid, start: range.start, end: range.end, rolling_window: win }),
  });
  return (
    <>
      <PageHead title="Factor Analytics" desc="Long-short factor portfolios built from market-derived characteristics, and the selected portfolio's exposure to them."
        actions={<>
          <div style={{ width: 240 }}><PortfolioSelect portfolios={ports.data ?? []} value={pid} onChange={setPortfolioId} allowEmpty="No portfolio (factors only)" /></div>
          <select className="input" style={{ width: 130 }} value={win} onChange={(e) => setWin(Number(e.target.value))} aria-label="Rolling window">
            {[63, 126, 252].map((w) => <option key={w} value={w}>{w}-day betas</option>)}
          </select>
          <DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} />
        </>} />
      <QueryView q={q} label="Constructing factor portfolios">
        {(d) => {
          const factors = Object.keys(d.definitions);
          return (
            <div className="stack">
              <div className="banner info small"><b>Market-derived proxies.</b>&nbsp;{d.note} No accounting fundamentals are used or fabricated.</div>
              <Card title="Factor definitions and performance" sub={`${d.period[0]} → ${d.period[1]}`} flush>
                <DataTable<AnyObj> rows={d.performance} filterable={false} exportName="factor-performance" columns={[
                  { key: "factor", label: "Factor", render: (r) => <b>{r.factor}</b> },
                  { key: "definition", label: "Construction (proxy)", wrap: true },
                  { key: "formation_months", label: "Formations", align: "right" },
                  { key: "cumulative_return", label: "Cumulative", align: "right", render: (r) => <span className={tone(r.cumulative_return)}>{spct(r.cumulative_return)}</span> },
                  { key: "annualized_return", label: "Ann. return", align: "right", render: (r) => spct(r.annualized_return) },
                  { key: "annualized_volatility", label: "Ann. vol", align: "right", render: (r) => pct(r.annualized_volatility) },
                  { key: "sharpe_ratio", label: "Sharpe", align: "right", render: (r) => num(r.sharpe_ratio, 2) },
                  { key: "max_drawdown", label: "Max DD", align: "right", render: (r) => spct(r.max_drawdown) },
                ]} />
              </Card>
              <div className="grid g2">
                <Card title="Cumulative long-short factor returns" sub="top minus bottom quintile, equal-weighted">
                  <Plot height={300} data={factors.map((f, i) => line(d.cumulative.dates, d.cumulative[f], f, seriesColor(theme, i)))} layout={{ xaxis: dateAxis(theme), yaxis: PCT_AXIS }} />
                </Card>
                <Card title="Factor return correlation">
                  <Plot height={300} data={[{
                    type: "heatmap", z: d.correlation.matrix, x: d.correlation.factors, y: d.correlation.factors, zmin: -1, zmax: 1, colorscale: diverging(theme),
                    text: d.correlation.matrix.map((row: number[]) => row.map((v) => v.toFixed(2))), texttemplate: "%{text}", hovertemplate: "%{y} × %{x}: %{z:.3f}<extra></extra>",
                    colorbar: { thickness: 10 }, xgap: 2, ygap: 2,
                  }]} layout={{ hovermode: "closest", yaxis: { autorange: "reversed" }, margin: { l: 80, r: 10, t: 8, b: 40 } }} />
                </Card>
              </div>
              {d.exposure ? (
                <div className="grid g2">
                  <Card title={`Factor exposure — ${d.exposure.portfolio}`} sub={`OLS with ${d.exposure.standard_errors}; market = ${d.exposure.benchmark_as_market}`} flush>
                    <div className="kpis" style={{ border: 0, borderRadius: 0 }}>
                      <Kpi label="R²" value={num(d.exposure.r_squared, 3)} />
                      <Kpi label="Adj. R²" value={num(d.exposure.adj_r_squared, 3)} />
                      <Kpi label="Observations" value={d.exposure.observations} note={`${d.exposure.start_date} → ${d.exposure.end_date}`} />
                    </div>
                    <DataTable<AnyObj> rows={d.exposure.coefficients} filterable={false} exportName="factor-exposure" columns={[
                      { key: "factor", label: "Regressor" },
                      { key: "beta", label: "Coefficient", align: "right", render: (r) => num(r.beta, 4) },
                      { key: "std_error", label: "Std. error", align: "right", render: (r) => num(r.std_error, 4) },
                      { key: "t_stat", label: "t-stat", align: "right", render: (r) => <span className={Math.abs(r.t_stat) > 2 ? "" : "muted"}>{num(r.t_stat, 2)}</span> },
                      { key: "p_value", label: "p-value", align: "right", render: (r) => num(r.p_value, 3) },
                    ]} />
                    <div className="xs muted" style={{ padding: "6px 14px" }}>Historical, in-sample association — not a forecast of future exposure.</div>
                  </Card>
                  <Card title={`Rolling ${win}-day factor betas`}>
                    <Plot height={320} data={Object.keys(d.rolling_betas).filter((k) => !["window", "dates"].includes(k)).map((k, i) => line(d.rolling_betas.dates, d.rolling_betas[k], k, seriesColor(theme, i)))}
                      layout={{ xaxis: dateAxis(theme, false), yaxis: { hoverformat: ".2f" } }} />
                  </Card>
                </div>
              ) : <div className="state">Select a portfolio to estimate its factor exposures.</div>}
            </div>
          );
        }}
      </QueryView>
    </>
  );
}
