import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import { Card, Disclaimer, Kpi, PageHead, QueryView, StatusBadge } from "../components/ui";
import { DateRange, PortfolioSelect } from "../components/pickers";
import { dateAxis, diverging, INK, sectorColor, seriesColor } from "../components/charts";
import { line, PCT_AXIS } from "../components/metrics";
import { usePortfolios } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, int, money, num, pct, spct, tone } from "../utils/format";

export default function Overview() {
  const { datasetId, portfolioId, setPortfolioId } = useWorkspace();
  const theme = useResolvedTheme();
  const [range, setRange] = useState({ start: "", end: "" });
  const ports = usePortfolios(datasetId);
  const q = useQuery({
    queryKey: ["overview", datasetId, portfolioId, range],
    queryFn: () => api.get<AnyObj>("/overview", { dataset_id: datasetId, portfolio_id: portfolioId, start: range.start, end: range.end }),
    enabled: datasetId != null,
  });

  return (
    <>
      <PageHead
        title="Overview"
        desc="Market coverage, the selected portfolio's historical performance and risk, and the state of the research registry — all computed from the stored dataset."
        actions={
          <>
            <div style={{ width: 240 }}>
              <PortfolioSelect portfolios={ports.data ?? []} value={portfolioId ?? q.data?.portfolio?.id ?? null} onChange={setPortfolioId} />
            </div>
            <DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} />
          </>
        }
      />
      <QueryView q={q} label="Computing overview">
        {(d) => <OverviewBody d={d} theme={theme} />}
      </QueryView>
    </>
  );
}

function OverviewBody({ d, theme }: { d: AnyObj; theme: "light" | "dark" }) {
  const m = d.market, p = d.portfolio, r = d.research;
  const perf = d.asset_performance as AnyObj[];
  const ranked = useMemo(() => [...perf].sort((a, b) => b.cumulative_return - a.cumulative_return), [perf]);
  const top = [...ranked.slice(0, 8), ...ranked.slice(-8)];
  const sectors: string[] = Array.from(new Set(perf.map((x) => x.sector)));
  return (
    <div className="stack">
      <div className="grid g4">
        <Card title="Market snapshot" sub={m.dataset.version_label}>
          <dl className="kv">
            <dt>Universe</dt><dd>{m.dataset.name}</dd>
            <dt>Assets</dt><dd>{m.assets} tradable · {m.benchmarks.length} benchmarks</dd>
            <dt>Coverage</dt><dd>{m.first_date} → {m.latest_date}</dd>
            <dt>Completeness</dt><dd>{pct(m.coverage)}</dd>
            <dt>Data quality</dt>
            <dd>{m.data_quality ? <span className="row" style={{ gap: 6 }}><StatusBadge status={m.data_quality.status} /> {pct(m.data_quality.score)}</span> : <Link to="/data-quality">not assessed</Link>}</dd>
            <dt>Source</dt><dd>{m.dataset.mode_label}</dd>
          </dl>
        </Card>
        <Card title="Research snapshot">
          <dl className="kv">
            <dt>Saved experiments</dt><dd>{int(r.total_experiments)}</dd>
            {Object.entries(r.experiment_counts as Record<string, number>).map(([k, v]) => (<Fragment key={k}><dt className="small">· {k.replace("_", " ")}</dt><dd className="small">{v}</dd></Fragment>))}
            <dt>Latest backtest</dt>
            <dd>{r.latest_backtest ? <Link to={`/backtesting?id=${r.latest_backtest.id}`}>{r.latest_backtest.code}</Link> : "none"}</dd>
            <dt>Latest ML run</dt>
            <dd>{r.latest_ml ? <Link to={`/experiments?id=${r.latest_ml.id}`}>{r.latest_ml.code}</Link> : "none"} {r.latest_ml && <StatusBadge status={r.latest_ml.status} />}</dd>
          </dl>
        </Card>
        <Card title="Market condition" sub="latest regime model">
          {d.regime ? (
            <div className="stack" style={{ gap: 6 }}>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{d.regime.regime}</div>
              <div className="small text2">Classified {d.regime.date} by <Link to={`/regimes?id=${d.regime.experiment_id}`}>{d.regime.code}</Link>
                {" "}(posterior {pct(Math.max(...Object.values(d.regime.probabilities as Record<string, number>)), 0)})</div>
              <div className="xs muted">{d.regime.note}</div>
            </div>
          ) : <div className="small muted">No regime model trained. <Link to="/regimes">Train one</Link>.</div>}
        </Card>
        <Card title="Portfolio" sub={p?.name}>
          {p && !p.error ? (
            <dl className="kv">
              <dt>Benchmark</dt><dd>{p.benchmark}</dd>
              <dt>Period</dt><dd>{p.period.start} → {p.period.end}</dd>
              <dt>Final value</dt><dd>{money(p.series.value[p.series.value.length - 1])}</dd>
              <dt>Open in</dt><dd><Link to={`/portfolio-lab?id=${p.id}`}>Portfolio Lab</Link> · <Link to="/risk">Risk</Link></dd>
            </dl>
          ) : <div className="small muted">{p?.error ?? <>No portfolio yet. <Link to="/portfolio-lab">Create one</Link>.</>}</div>}
        </Card>
      </div>

      {p && !p.error && (
        <>
          <div className="kpis">
            <Kpi label="Cumulative return" value={spct(p.metrics.cumulative_return)} tone={tone(p.metrics.cumulative_return)} metric="cumulative_return" />
            <Kpi label="Annualised return" value={spct(p.metrics.annualized_return)} tone={tone(p.metrics.annualized_return)} metric="annualized_return" />
            <Kpi label="Annualised volatility" value={pct(p.metrics.annualized_volatility, 1)} metric="annualized_volatility" />
            <Kpi label="Sharpe ratio" value={num(p.metrics.sharpe_ratio, 2)} metric="sharpe_ratio" />
            <Kpi label="Max drawdown" value={spct(p.metrics.max_drawdown)} tone="neg" metric="max_drawdown" />
            <Kpi label={`Beta vs ${p.benchmark}`} value={num(p.metrics.beta, 2)} metric="beta" />
            <Kpi label="VaR 95% (1d, hist.)" value={pct(p.var_95)} metric="var" />
            <Kpi label="CVaR 95% (1d, hist.)" value={pct(p.cvar_95)} metric="cvar" />
          </div>
          <div className="grid g2">
            <Card title="Equity curve vs benchmark" sub="historical simulation, monthly rebalanced">
              <Plot height={280} data={[line(p.series.dates, p.series.value, p.name, seriesColor(theme, 0)), line(p.series.dates, p.series.benchmark_value, p.benchmark, INK[theme].neutral)]}
                layout={{ xaxis: dateAxis(theme), yaxis: { tickprefix: "$", tickformat: ",.2s" } }} />
            </Card>
            <Card title="Drawdown" sub="decline from running peak">
              <Plot height={280} data={[
                line(p.series.dates, p.series.drawdown, p.name, seriesColor(theme, 0), { fill: "tozeroy", fillcolor: theme === "dark" ? "rgba(57,135,229,0.18)" : "rgba(42,120,214,0.14)" }),
              ]} layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS, showlegend: false }} />
            </Card>
            <Card title="Rolling volatility" sub="63-day, annualised">
              <Plot height={260} data={[line(p.series.dates, p.series.rolling_volatility, p.name, seriesColor(theme, 0)), line(p.series.dates, p.series.benchmark_rolling_volatility, p.benchmark, INK[theme].neutral)]}
                layout={{ xaxis: dateAxis(theme, false), yaxis: PCT_AXIS }} />
            </Card>
            <Card title="Sector correlation" sub="equal-weight sector returns, selected period">
              <Plot height={260} data={[{
                type: "heatmap", z: d.sector_correlation.matrix, x: d.sector_correlation.sectors, y: d.sector_correlation.sectors,
                zmin: -1, zmax: 1, colorscale: diverging(theme), hovertemplate: "%{y} × %{x}: %{z:.2f}<extra></extra>", colorbar: { thickness: 10 },
              }]} layout={{ margin: { l: 150, r: 10, t: 8, b: 90 }, hovermode: "closest", yaxis: { autorange: "reversed" } }} />
            </Card>
          </div>
        </>
      )}

      <div className="grid g2">
        <Card title="Asset risk / return" sub="each point is an asset; colour = sector">
          <Plot height={300} data={sectors.map((s) => {
            const pts = perf.filter((x) => x.sector === s);
            return {
              type: "scatter", mode: "markers", name: s, x: pts.map((x) => x.annualized_volatility), y: pts.map((x) => x.cumulative_return),
              text: pts.map((x) => x.symbol), marker: { size: 9, color: sectorColor(theme, s), line: { width: 1.5, color: INK[theme].surface } },
              hovertemplate: "<b>%{text}</b><br>vol %{x:.1%}<br>cum. return %{y:.1%}<extra>" + s + "</extra>",
            };
          })} layout={{ hovermode: "closest", xaxis: { title: { text: "Annualised volatility" }, tickformat: ".0%" }, yaxis: { title: { text: "Cumulative return" }, tickformat: ".0%" }, legend: { orientation: "v", x: 1.02, y: 1 } }} />
        </Card>
        <Card title="Best and worst performers" sub="cumulative return over the selected period">
          <Plot height={300} data={[{
            type: "bar", orientation: "h", y: top.map((x) => x.symbol), x: top.map((x) => x.cumulative_return),
            marker: { color: top.map((x) => (x.cumulative_return >= 0 ? seriesColor(theme, 0) : seriesColor(theme, 7))) },
            hovertemplate: "%{y}: %{x:.1%}<extra></extra>",
          }]} layout={{ hovermode: "closest", xaxis: { tickformat: ".0%" }, yaxis: { autorange: "reversed", tickfont: { size: 10 } }, bargap: 0.25, margin: { l: 50, r: 10, t: 8, b: 30 } }} />
        </Card>
      </div>
      <div className="row-between">
        <span className="xs muted">Computed {dt(new Date().toISOString())} from {m.dataset.version_label} (hash {m.dataset.content_hash?.slice(0, 12)}).</span>
      </div>
      <Disclaimer />
    </div>
  );
}
