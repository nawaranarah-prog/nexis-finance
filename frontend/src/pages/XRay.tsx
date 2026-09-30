import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, InfoTip, Kpi, PageHead, QueryView, Tabs } from "../components/ui";
import { DataClassBadge, MethodSelect, ScopeSelect, Warnings } from "../components/connect";
import { DateRange } from "../components/pickers";
import { INK, seriesColor } from "../components/charts";
import { useResolvedTheme } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { money, num, pct, spct, tone } from "../utils/format";

type Tab = "xray" | "moving" | "risk" | "diagnostics";

export default function XRay() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "xray";
  const [scope, setScope] = useState("all");
  const [method, setMethod] = useState("fifo");
  return (
    <>
      <PageHead title="Portfolio X-Ray" desc="Decompose the reconstructed portfolio: what it holds, why it moved, where its risk comes from, and how it measures up — from your actual holdings and stored prices."
        actions={<><ScopeSelect value={scope} onChange={setScope} /><MethodSelect value={method} onChange={setMethod} /></>} />
      <Tabs value={tab} onChange={(t) => setParams({ tab: t })} tabs={[{ value: "xray", label: "X-Ray" }, { value: "moving", label: "Why is my portfolio moving?" },
        { value: "risk", label: "Risk drill-down" }, { value: "diagnostics", label: "Diagnostics" }]} />
      {tab === "xray" && <XRayTab scope={scope} method={method} />}
      {tab === "moving" && <Moving scope={scope} method={method} />}
      {tab === "risk" && <Risk scope={scope} method={method} />}
      {tab === "diagnostics" && <Diagnostics scope={scope} method={method} />}
    </>
  );
}

function Bars({ rows, title, sub }: { rows: AnyObj[]; title: string; sub?: string }) {
  const theme = useResolvedTheme();
  return (
    <Card title={title} sub={sub}>
      <Plot height={Math.max(160, rows.length * 24 + 40)} data={[{
        type: "bar", orientation: "h", y: rows.map((r) => r.label), x: rows.map((r) => r.weight),
        marker: { color: rows.map((r) => (String(r.label).startsWith("Unclassified") ? INK[theme].neutral : seriesColor(theme, 0))) },
        customdata: rows.map((r) => r.market_value), hovertemplate: "%{y}: %{x:.1%} (%{customdata:$,.0f})<extra></extra>",
      }]} layout={{ hovermode: "closest", xaxis: { tickformat: ".0%" }, yaxis: { autorange: "reversed", tickfont: { size: 10 } }, margin: { l: 210, r: 10, t: 6, b: 28 } }} />
    </Card>
  );
}

function XRayTab({ scope, method }: { scope: string; method: string }) {
  const q = useQuery({ queryKey: ["intel", "xray", scope, method], queryFn: () => api.get<AnyObj>("/intelligence/xray", { scope, method }) });
  return (
    <QueryView q={q} label="Decomposing portfolio">
      {(x) => {
        const c = x.concentration ?? {};
        return (
          <div className="stack">
            <Warnings items={x.warnings} />
            <div className="kpis">
              <Kpi label="Market value" value={money(x.totals.market_value)} note={`securities ${money(x.totals.securities)}`} />
              <Kpi label="Cash" value={money(x.totals.cash)} note={pct(x.totals.cash / x.totals.market_value, 1)} />
              <Kpi label="Top-5 weight" value={pct(c.top5_weight, 1)} />
              <Kpi label="Top-10 weight" value={pct(c.top10_weight, 1)} />
              <Kpi label="Herfindahl index" value={num(c.herfindahl_index, 3)} metric="herfindahl_index" note={`effective N ${num(c.effective_number_of_assets, 1)}`} />
              <Kpi label="Largest position" value={c.largest_position ?? "–"} note={pct(c.largest_weight, 1)} />
            </div>
            <div className="small text2">{x.classification_note}</div>
            <div className="grid g2">
              <Bars rows={x.allocation} title="Asset allocation" sub="asset class of each holding" />
              <Bars rows={x.industry} title="Industry exposure" sub="SEC SIC major group" />
              <Bars rows={x.country} title="Geographic exposure" sub="SEC business address (headquarters)" />
              <Bars rows={x.currency} title="Currency exposure" sub="holding currency" />
            </div>
            <Card title="Holdings" flush>
              <DataTable<AnyObj> rows={x.positions} exportName="xray-holdings" pageSize={25} columns={[
                { key: "symbol", label: "Symbol", render: (p) => <Link to={`/graph?entity=asset:${p.symbol}`}><b className="mono">{p.symbol}</b></Link> },
                { key: "name", label: "Name" },
                { key: "asset_class", label: "Class" },
                { key: "industry", label: "Industry", render: (p) => (p.industry ? <span title={p.industry_source}>{p.industry}</span> : <span className="muted">unclassified</span>) },
                { key: "currency", label: "CCY" },
                { key: "quantity", label: "Quantity", align: "right", render: (p) => num(p.quantity, 2) },
                { key: "price", label: "Price", align: "right", render: (p) => (p.price == null ? "–" : num(p.price, 2)) },
                { key: "market_value", label: "Value (USD)", align: "right", render: (p) => money(p.market_value) },
                { key: "weight", label: "Weight", align: "right", render: (p) => pct(p.weight, 1) },
                { key: "pct_volatility_contribution", label: "% of vol", align: "right", render: (p) => pct(p.pct_volatility_contribution, 1) },
                { key: "unrealized_pnl", label: "Unrealised", align: "right", render: (p) => <span className={tone(p.unrealized_pnl)}>{money(p.unrealized_pnl)}</span> },
                { key: "accounts", label: "Accounts", value: (p) => p.accounts.map((a: AnyObj) => a.account).join(", "), render: (p) => <span className="small">{p.accounts.map((a: AnyObj) => a.account).join(", ")}</span> },
                { key: "price_source", label: "Price source", render: (p) => <span className="xs text2">{p.price_source}{p.price_date ? ` · ${p.price_date}` : ""}</span> },
                { key: "data_class", label: "Data", render: (p) => <DataClassBadge value={p.data_class} /> },
              ]} />
            </Card>
          </div>
        );
      }}
    </QueryView>
  );
}

function Moving({ scope, method }: { scope: string; method: string }) {
  const theme = useResolvedTheme();
  const [range, setRange] = useState({ start: "", end: "" });
  const q = useQuery({ queryKey: ["intel", "attr", scope, method, range], queryFn: () => api.get<AnyObj>("/intelligence/attribution", { scope, method, start: range.start, end: range.end }) });
  return (
    <div className="stack">
      <div className="row"><span className="small text2">Period (default: last ~21 trading days)</span><DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} /></div>
      <QueryView q={q} label="Attributing returns">
        {(a) => {
          const wf = [...a.assets].sort((x: AnyObj, y: AnyObj) => x.contribution - y.contribution);
          return (
            <>
              <div className="kpis">
                <Kpi label={`Portfolio return ${a.start} → ${a.end}`} value={<span className={tone(a.portfolio_return)}>{spct(a.portfolio_return)}</span>} note={`${a.days} trading days`} />
                <Kpi label={`Benchmark (${a.benchmark.symbol})`} value={spct(a.benchmark.return)} />
                <Kpi label="Active return" value={<span className={tone(a.active_return)}>{spct(a.active_return)}</span>} />
                <Kpi label="Sum of contributions" value={spct(a.sum_of_contributions)} note={`compounding residual ${spct(a.compounding_residual, 3)}`} />
              </div>
              <div className="grid g2">
                <Card title="Contribution by holding" sub="percentage points of portfolio return">
                  <Plot height={Math.max(220, wf.length * 22 + 40)} data={[{
                    type: "bar", orientation: "h", y: wf.map((x: AnyObj) => x.symbol), x: wf.map((x: AnyObj) => x.contribution),
                    marker: { color: wf.map((x: AnyObj) => (x.contribution < 0 ? seriesColor(theme, 7) : seriesColor(theme, 0))) },
                    customdata: wf.map((x: AnyObj) => [x.asset_return, x.avg_weight]), hovertemplate: "%{y}: %{x:.2%}<br>asset return %{customdata[0]:.2%}, avg weight %{customdata[1]:.1%}<extra></extra>",
                  }]} layout={{ hovermode: "closest", xaxis: { tickformat: ".2%" }, margin: { l: 70, r: 10, t: 6, b: 30 } }} />
                </Card>
                <Card title="Contribution by industry" sub="SEC SIC major group">
                  <Plot height={Math.max(220, a.industries.length * 24 + 40)} data={[{
                    type: "bar", orientation: "h", y: a.industries.map((x: AnyObj) => x.industry), x: a.industries.map((x: AnyObj) => x.contribution),
                    marker: { color: a.industries.map((x: AnyObj) => (x.contribution < 0 ? seriesColor(theme, 7) : seriesColor(theme, 0))) }, hovertemplate: "%{y}: %{x:.2%}<extra></extra>",
                  }]} layout={{ hovermode: "closest", xaxis: { tickformat: ".2%" }, margin: { l: 210, r: 10, t: 6, b: 30 } }} />
                </Card>
                <Card title="Largest negative contributors" flush><ContribTable rows={a.negative_contributors} /></Card>
                <Card title="Largest positive contributors" flush><ContribTable rows={a.positive_contributors} /></Card>
              </div>
              <div className="small text2">{a.methodology} History: {a.history_method}.</div>
            </>
          );
        }}
      </QueryView>
    </div>
  );
}

function ContribTable({ rows }: { rows: AnyObj[] }) {
  return (
    <table className="dt"><thead><tr><th>Holding</th><th className="r">Contribution</th><th className="r">Asset return</th><th className="r">Avg weight</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.symbol}><td><b className="mono">{r.symbol}</b> <span className="small text2">{r.name}</span></td>
        <td className={`r ${tone(r.contribution)}`}>{spct(r.contribution)}</td><td className="r">{spct(r.asset_return)}</td><td className="r">{pct(r.avg_weight, 1)}</td></tr>)}
        {rows.length === 0 && <tr><td colSpan={4}><div className="state">None in this period.</div></td></tr>}</tbody></table>
  );
}

function Risk({ scope, method }: { scope: string; method: string }) {
  const theme = useResolvedTheme();
  const q = useQuery({ queryKey: ["intel", "risk", scope, method], queryFn: () => api.get<AnyObj>("/intelligence/risk-drilldown", { scope, method }) });
  return (
    <QueryView q={q} label="Decomposing risk">
      {(r) => (
        <div className="stack">
          <div className="banner neutral small">Portfolio risk → asset contribution → correlation effect → industry concentration → largest contributors. {r.methodology}</div>
          <div className="kpis">
            <Kpi label="Portfolio volatility" value={pct(r.portfolio_volatility, 1)} metric="annualized_volatility" note={`${r.observations} days, ${r.window[0]} → ${r.window[1]}`} />
            <Kpi label="Undiversified volatility" value={pct(r.undiversified_volatility, 1)} tip={r.correlation_effect_note} />
            <Kpi label="Diversification benefit" value={<span className="pos">−{pct(r.diversification_benefit, 1)}</span>} />
            <Kpi label="Average pairwise correlation" value={num(r.average_pairwise_correlation, 2)} metric="correlation" />
            <Kpi label={`CVaR ${pct(r.tail_contributions.confidence, 0)} (1-day)`} value={pct(r.tail_contributions.cvar)} metric="cvar" note={`${r.tail_contributions.observations_in_tail} tail days`} />
            <Kpi label="Max drawdown (history)" value={<span className="neg">{spct(r.drawdown.max_drawdown)}</span>} note={`${r.drawdown.peak} → ${r.drawdown.trough}`} />
          </div>
          <div className="grid g2">
            <Card title="Volatility contribution vs weight" actions={<InfoTip metric="risk_contribution" />}>
              <Plot height={Math.max(240, r.assets.length * 22 + 50)} data={[
                { type: "bar", orientation: "h", name: "Weight", y: r.assets.map((a: AnyObj) => a.symbol), x: r.assets.map((a: AnyObj) => a.weight), marker: { color: seriesColor(theme, 0) } },
                { type: "bar", orientation: "h", name: "% of volatility", y: r.assets.map((a: AnyObj) => a.symbol), x: r.assets.map((a: AnyObj) => a.pct_contribution), marker: { color: seriesColor(theme, 1) } },
              ]} layout={{ barmode: "group", xaxis: { tickformat: ".0%" }, yaxis: { autorange: "reversed" }, margin: { l: 70, r: 10, t: 30, b: 30 } }} />
            </Card>
            <Card title="Risk by industry" flush>
              <table className="dt"><thead><tr><th>Industry</th><th className="r">Weight</th><th className="r">% of volatility</th><th className="r">Risk / weight</th></tr></thead>
                <tbody>{r.industries.map((i: AnyObj) => <tr key={i.industry}><td>{i.industry}</td><td className="r">{pct(i.weight, 1)}</td><td className="r">{pct(i.pct_risk, 1)}</td><td className="r">{num(i.weight ? i.pct_risk / i.weight : null, 2)}</td></tr>)}</tbody></table>
            </Card>
            <Card title="Tail-loss (CVaR) contributors" sub="average weighted return on the worst days" flush>
              <table className="dt"><thead><tr><th>Holding</th><th className="r">CVaR contribution</th><th className="r">Share</th></tr></thead>
                <tbody>{r.tail_contributions.assets.map((a: AnyObj) => <tr key={a.symbol}><td className="mono">{a.symbol}</td><td className="r">{pct(a.cvar_contribution, 3)}</td><td className="r">{pct(a.share, 1)}</td></tr>)}</tbody></table>
            </Card>
            <Card title="Drawdown contributors" sub={`peak ${r.drawdown.peak} → trough ${r.drawdown.trough}`}>
              <Plot height={Math.max(220, r.drawdown.contributions.length * 22 + 40)} data={[{
                type: "bar", orientation: "h", y: r.drawdown.contributions.map((c: AnyObj) => c.symbol), x: r.drawdown.contributions.map((c: AnyObj) => c.contribution),
                marker: { color: r.drawdown.contributions.map((c: AnyObj) => (c.contribution < 0 ? seriesColor(theme, 7) : seriesColor(theme, 0))) }, hovertemplate: "%{y}: %{x:.2%}<extra></extra>",
              }]} layout={{ hovermode: "closest", xaxis: { tickformat: ".1%" }, margin: { l: 70, r: 10, t: 6, b: 30 } }} />
            </Card>
          </div>
          <Card title="Asset risk detail" flush>
            <DataTable<AnyObj> rows={r.assets} filterable={false} exportName="risk-contributions" columns={[
              { key: "symbol", label: "Symbol", render: (a) => <b className="mono">{a.symbol}</b> }, { key: "industry", label: "Industry", render: (a) => a.industry ?? "–" },
              { key: "weight", label: "Weight", align: "right", render: (a) => pct(a.weight, 1) },
              { key: "standalone_volatility", label: "Standalone vol", align: "right", render: (a) => pct(a.standalone_volatility, 1) },
              { key: "contribution", label: "Vol contribution", align: "right", render: (a) => pct(a.contribution, 2) },
              { key: "pct_contribution", label: "% of vol", align: "right", render: (a) => pct(a.pct_contribution, 1) },
              { key: "risk_to_weight", label: "Risk / weight", align: "right", render: (a) => num(a.risk_to_weight, 2) },
            ]} />
          </Card>
        </div>
      )}
    </QueryView>
  );
}

function Diagnostics({ scope, method }: { scope: string; method: string }) {
  const q = useQuery({ queryKey: ["intel", "diag", scope, method], queryFn: () => api.get<AnyObj>("/intelligence/diagnostics", { scope, method }) });
  const fmt = (m: AnyObj) => (m.unit === "pct" ? pct(m.value, 1) : m.unit === "int" ? String(m.value ?? "–") : num(m.value, 2));
  return (
    <QueryView q={q} label="Measuring">
      {(d) => (
        <div className="stack">
          <div className="banner neutral small">{d.note}</div>
          <div className="grid g2">
            {d.categories.map((c: AnyObj) => (
              <Card key={c.category} title={c.category} flush>
                <table className="dt"><thead><tr><th>Measurement</th><th className="r">Value</th><th className="r">Reference</th><th>Status</th></tr></thead>
                  <tbody>{c.measurements.map((m: AnyObj) => (
                    <tr key={m.name}><td>{m.name}{m.detail && <span className="small text2"> · {m.detail}</span>}</td><td className="r">{fmt(m)}</td>
                      <td className="r muted">{m.reference == null ? "–" : m.unit === "pct" ? pct(m.reference, 0) : num(m.reference, m.reference < 10 ? 1 : 0)}</td>
                      <td>{m.status ? <span className={`badge ${m.status === "above reference" ? "warn" : "good"}`}>{m.status}</span> : ""}</td></tr>
                  ))}</tbody></table>
              </Card>
            ))}
          </div>
        </div>
      )}
    </QueryView>
  );
}
