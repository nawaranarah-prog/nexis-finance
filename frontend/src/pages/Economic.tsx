import { useQuery } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, PageHead, QueryView } from "../components/ui";
import { DataClassBadge } from "../components/connect";
import { dateAxis, seriesColor } from "../components/charts";
import { line } from "../components/metrics";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";
import { compact, dt, num, pct } from "../utils/format";

export default function Economic() {
  const theme = useResolvedTheme();
  const { settings, updateSettings } = useWorkspace();
  const e = useQuery({ queryKey: ["intel", "economic"], queryFn: () => api.get<AnyObj>("/intelligence/economic") });
  const f = useQuery({ queryKey: ["intel", "fundamentals"], queryFn: () => api.get<AnyObj[]>("/intelligence/fundamentals") });
  return (
    <>
      <PageHead title="Economic & Regulatory Data" desc="Real external data from the US Treasury, World Bank, FRED (with your key) and SEC EDGAR — with source, retrieval time and coverage for every series." />
      <QueryView q={e} label="Loading series">
        {(d) => {
          const ust = d.series.filter((s: AnyObj) => s.source === "us_treasury");
          const other = d.series.filter((s: AnyObj) => s.source !== "us_treasury");
          if (!d.series.length) return <Card title="No economic data yet"><div className="state">Connect US Treasury, World Bank or FRED on the Connections page and press Sync now.</div></Card>;
          return (
            <div className="stack">
              {d.suggested_risk_free_rate && (
                <div className="banner info small">
                  Latest 3-month Treasury par yield: <b>{pct(d.suggested_risk_free_rate.value)}</b> ({d.suggested_risk_free_rate.date}). Your analytics currently use {pct(settings.riskFreeRate)}.
                  <button className="btn sm" style={{ marginLeft: 8 }} onClick={() => updateSettings({ riskFreeRate: Number(d.suggested_risk_free_rate.value.toFixed(4)) })}>Use as risk-free rate</button>
                </div>
              )}
              <div className="grid g2">
                {d.yield_curve && (
                  <Card title="US Treasury par yield curve" sub={d.yield_curve.date}>
                    <Plot height={260} data={[{ type: "scatter", mode: "lines+markers", x: d.yield_curve.tenors.map((t: string) => t.replace("UST_", "")), y: d.yield_curve.yields,
                      line: { color: seriesColor(theme, 0), width: 2 }, marker: { size: 8 }, name: "yield", hovertemplate: "%{x}: %{y:.2f}%<extra></extra>" }]}
                      layout={{ yaxis: { ticksuffix: "%" }, xaxis: { type: "category" }, showlegend: false, hovermode: "closest" }} />
                  </Card>
                )}
                {ust.length > 0 && (
                  <Card title="3-month vs 10-year yield and term spread">
                    <Plot height={260} data={(() => {
                      const m3 = ust.find((s: AnyObj) => s.code === "UST_3M"), y10 = ust.find((s: AnyObj) => s.code === "UST_10Y");
                      const out = [];
                      if (m3) out.push(line(m3.dates, m3.values, "3-month", seriesColor(theme, 0)));
                      if (y10) out.push(line(y10.dates, y10.values, "10-year", seriesColor(theme, 1)));
                      if (m3 && y10) {
                        const map = new Map(m3.dates.map((dd: string, i: number) => [dd, m3.values[i]]));
                        out.push(line(y10.dates, y10.values.map((v: number, i: number) => (map.has(y10.dates[i]) ? v - (map.get(y10.dates[i]) as number) : null)), "10y − 3m spread", seriesColor(theme, 2)));
                      }
                      return out;
                    })()} layout={{ xaxis: dateAxis(theme), yaxis: { ticksuffix: "%" } }} />
                  </Card>
                )}
                {other.map((s: AnyObj, i: number) => (
                  <Card key={s.id} title={s.title} sub={`${s.source} · ${s.code} · ${s.frequency ?? ""}`}>
                    <Plot height={200} data={[{ type: s.frequency === "annual" ? "bar" : "scatter", mode: "lines", x: s.dates, y: s.values, marker: { color: seriesColor(theme, i % 8) }, line: { color: seriesColor(theme, i % 8), width: 2 }, name: s.code }]}
                      layout={{ showlegend: false, xaxis: dateAxis(theme, false), yaxis: { hoverformat: ".2f" } }} />
                    <div className="xs muted">Latest {s.latest?.date}: {num(s.latest?.value, 2)} {s.units ?? ""} · retrieved {dt(s.last_retrieved)}</div>
                  </Card>
                ))}
              </div>
              <Card title="Series catalogue" flush>
                <DataTable<AnyObj> rows={d.series} filterable={false} columns={[
                  { key: "source", label: "Source" }, { key: "code", label: "Series", render: (s) => <span className="mono">{s.code}</span> }, { key: "title", label: "Title" },
                  { key: "coverage", label: "Coverage", value: (s) => `${s.coverage[0]} → ${s.coverage[1]}` }, { key: "n", label: "Obs.", align: "right", value: (s) => s.values.length },
                  { key: "last_retrieved", label: "Retrieved", render: (s) => dt(s.last_retrieved) }, { key: "dc", label: "Data", render: () => <DataClassBadge value="real_external" /> },
                ]} />
              </Card>
            </div>
          );
        }}
      </QueryView>
      <div style={{ marginTop: 12 }}>
        <Card title="SEC EDGAR company profiles & reported fundamentals" sub="latest annual (10-K / 20-F) values as filed" flush>
          <QueryView q={f}>
            {(rows) => (
              <DataTable<AnyObj> rows={rows} exportName="sec-fundamentals" columns={[
                { key: "symbol", label: "Symbol", render: (p) => <a href={p.edgar_url} target="_blank" rel="noreferrer"><b className="mono">{p.symbol}</b></a> },
                { key: "name", label: "Issuer" }, { key: "sic", label: "SIC", render: (p) => <span title={p.sic_description ?? ""}>{p.sic ?? "–"}</span> },
                { key: "sic_major_group", label: "Industry (SIC major group)", render: (p) => p.sic_major_group ?? <span className="muted">none filed</span> },
                { key: "business_country", label: "HQ", render: (p) => `${p.business_country ?? "–"}${p.business_state_or_country ? ` (${p.business_state_or_country})` : ""}` },
                { key: "rev", label: "Revenue", align: "right", value: (p) => p.latest_annual?.Revenue?.value, render: (p) => (p.latest_annual?.Revenue ? `$${compact(p.latest_annual.Revenue.value)}` : "–") },
                { key: "ni", label: "Net income", align: "right", value: (p) => p.latest_annual?.["Net income"]?.value, render: (p) => (p.latest_annual?.["Net income"] ? `$${compact(p.latest_annual["Net income"].value)}` : "–") },
                { key: "eq", label: "Equity", align: "right", value: (p) => p.latest_annual?.["Stockholders' equity"]?.value, render: (p) => (p.latest_annual?.["Stockholders' equity"] ? `$${compact(p.latest_annual["Stockholders' equity"].value)}` : "–") },
                { key: "eps", label: "Diluted EPS", align: "right", value: (p) => p.latest_annual?.["Diluted EPS"]?.value, render: (p) => num(p.latest_annual?.["Diluted EPS"]?.value, 2) },
                { key: "period", label: "Period end", render: (p) => p.latest_annual?.Revenue?.period_end ?? "–" },
                { key: "fetched_at", label: "Retrieved", render: (p) => dt(p.fetched_at) },
              ]} empty="No SEC data yet — connect SEC EDGAR and sync (uses your holdings' tickers)." />
            )}
          </QueryView>
        </Card>
      </div>
    </>
  );
}
