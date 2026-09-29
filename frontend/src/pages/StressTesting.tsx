import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Plot from "../components/Plot";
import DataTable from "../components/DataTable";
import { Card, Disclaimer, download, Field, Kpi, PageHead, QueryView } from "../components/ui";
import { PortfolioSelect } from "../components/pickers";
import { seriesColor } from "../components/charts";
import { usePortfolios } from "../hooks/queries";
import { useResolvedTheme, useWorkspace } from "../hooks/workspace";
import { api, buildUrl, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { dt, money, num, pct, spct, tone } from "../utils/format";

const SECTORS = ["Technology", "Financials", "Healthcare", "Energy", "Industrials", "Consumer Staples", "Consumer Discretionary", "Utilities", "Commodities", "Fixed Income"];
const TYPES = [
  { value: "market_shock", label: "Market shock (beta-scaled)" },
  { value: "sector_shock", label: "Sector shock (with spill-over)" },
  { value: "volatility_spike", label: "Volatility spike" },
  { value: "correlation_increase", label: "Correlation increase" },
  { value: "historical_worst", label: "Worst historical window" },
  { value: "custom", label: "Custom asset shocks" },
];

export default function StressTesting() {
  const { datasetId, portfolioId, setPortfolioId } = useWorkspace();
  const ports = usePortfolios(datasetId);
  const pid = portfolioId ?? ports.data?.[0]?.id ?? null;
  const portfolio = ports.data?.find((p) => p.id === pid);
  const qc = useQueryClient();
  const theme = useResolvedTheme();
  const [type, setType] = useState("market_shock");
  const [shock, setShock] = useState(-10);
  const [sector, setSector] = useState("Technology");
  const [mult, setMult] = useState(2);
  const [intensity, setIntensity] = useState(0.5);
  const [windowDays, setWindowDays] = useState(10);
  const [custom, setCustom] = useState("");
  const [lookback, setLookback] = useState(504);
  const [name, setName] = useState("");
  const [res, setRes] = useState<AnyObj | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const presets = useQuery({ queryKey: ["stress-presets"], queryFn: () => api.get<AnyObj>("/stress-tests/presets"), staleTime: Infinity });
  const history = useQuery({ queryKey: ["stress-tests", pid], queryFn: () => api.get<AnyObj[]>("/stress-tests", { portfolio_id: pid }), enabled: pid != null });

  const params = (): AnyObj => {
    switch (type) {
      case "market_shock": return { shock: shock / 100 };
      case "sector_shock": return { shock: shock / 100, sector };
      case "volatility_spike": return { multiplier: mult };
      case "correlation_increase": return { intensity };
      case "historical_worst": return { window_days: windowDays };
      default: {
        const asset_shocks: Record<string, number> = {};
        custom.split(/[,\n]/).map((s) => s.trim()).filter(Boolean).forEach((kv) => {
          const [k, v] = kv.split(/[:=]/).map((x) => x.trim());
          if (k && v) asset_shocks[k.toUpperCase()] = Number(v) / 100;
        });
        return { asset_shocks };
      }
    }
  };
  const run = async (body?: AnyObj) => {
    if (!pid) return;
    setBusy(true); setErr(null);
    try {
      const b = body ?? { name: name || TYPES.find((t) => t.value === type)!.label, scenario_type: type, parameters: params(), lookback_days: lookback };
      setRes(await api.post<AnyObj>("/stress-tests", { portfolio_id: pid, ...b }));
      qc.invalidateQueries({ queryKey: ["stress-tests", pid] });
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };
  const r = res?.results;
  const distributional = r && r.baseline;

  return (
    <>
      <PageHead title="Stress Testing" desc="Hypothetical scenarios applied to the portfolio's current (drifted) holdings. Linear beta assumptions; results are not forecasts."
        actions={<div style={{ width: 260 }}><PortfolioSelect portfolios={ports.data ?? []} value={pid} onChange={setPortfolioId} /></div>} />
      <div className="grid g-side">
        <div className="stack">
          <Card title="Preset scenarios">
            <div className="stack" style={{ gap: 6 }}>
              {(presets.data?.presets ?? []).map((p: AnyObj) => (
                <button key={p.name} className="btn" style={{ justifyContent: "flex-start" }} disabled={busy || !pid} onClick={() => run({ name: p.name, scenario_type: p.scenario_type, parameters: p.parameters })}>{p.name}</button>
              ))}
            </div>
          </Card>
          <Card title="Custom scenario">
            <div className="stack">
              <Field label="Scenario type"><select className="input" value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></Field>
              {(type === "market_shock" || type === "sector_shock") && <Field label="Shock (%)" hint="e.g. −15 for a 15% decline"><input className="input" type="number" step={1} value={shock} onChange={(e) => setShock(Number(e.target.value))} /></Field>}
              {type === "sector_shock" && <Field label="Sector"><select className="input" value={sector} onChange={(e) => setSector(e.target.value)}>{SECTORS.map((s) => <option key={s}>{s}</option>)}</select></Field>}
              {type === "volatility_spike" && <Field label="Volatility multiplier"><input className="input" type="number" step={0.25} min={0.1} max={10} value={mult} onChange={(e) => setMult(Number(e.target.value))} /></Field>}
              {type === "correlation_increase" && <Field label="Blend toward ρ = 1" hint="0 = unchanged, 1 = perfectly correlated"><input className="input" type="number" step={0.1} min={0} max={1} value={intensity} onChange={(e) => setIntensity(Number(e.target.value))} /></Field>}
              {type === "historical_worst" && <Field label="Window (trading days)"><input className="input" type="number" min={1} max={250} value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))} /></Field>}
              {type === "custom" && <Field label="Asset shocks (%)" hint={`one per line, e.g. ${portfolio?.positions[0]?.symbol ?? "TCH1"}: -25`}><textarea className="input mono" rows={4} value={custom} onChange={(e) => setCustom(e.target.value)} /></Field>}
              <Field label="Beta / covariance lookback (days)"><input className="input" type="number" min={60} max={5000} value={lookback} onChange={(e) => setLookback(Number(e.target.value))} /></Field>
              <Field label="Name (optional)"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
              <button className="btn primary" onClick={() => run()} disabled={busy || !pid}>{busy ? "Running…" : "Run scenario"}</button>
              {err && <div className="banner error small">{err}</div>}
            </div>
          </Card>
        </div>
        <div className="stack">
          {!r && <Card title="Result"><div className="state">Choose a preset or build a custom scenario.</div></Card>}
          {r && (
            <>
              <div className="banner warn small"><b>Hypothetical scenario</b> — {r.method}. As of {r.as_of}, lookback {r.lookback_days} days.</div>
              <div className="kpis">
                <Kpi label="Scenario" value={<span style={{ fontSize: 14 }}>{res!.name}</span>} />
                <Kpi label="Value before" value={money(r.portfolio_value_before)} />
                <Kpi label={distributional ? "Value after stressed 1-day CVaR" : "Value after"} value={money(r.portfolio_value_after)} />
                <Kpi label={distributional ? `Stressed CVaR ${pct(r.confidence, 0)}` : "Portfolio impact"} value={distributional ? pct(r.loss_pct) : spct(r.portfolio_return)} tone={distributional ? "neg" : tone(r.portfolio_return)} />
                <Kpi label="P&L" value={money(r.pnl)} tone={tone(r.pnl)} />
              </div>
              {distributional ? (
                <Card title="Baseline vs stressed (parametric normal, 1-day)" flush>
                  <table className="dt"><thead><tr><th>Measure</th><th className="r">Baseline</th><th className="r">Stressed</th></tr></thead>
                    <tbody>{["annualized_volatility", "var", "cvar", "var_amount", "cvar_amount"].map((k) => (
                      <tr key={k}><td>{k.replace("_", " ")}</td><td className="r">{k.includes("amount") ? money(r.baseline[k]) : pct(r.baseline[k])}</td><td className="r">{k.includes("amount") ? money(r.stressed[k]) : pct(r.stressed[k])}</td></tr>
                    ))}</tbody></table>
                </Card>
              ) : (
                <Card title="Contribution to portfolio impact" sub="weight × asset shock">
                  <Plot height={Math.max(220, r.assets.length * 20 + 50)} data={[{
                    type: "bar", orientation: "h", y: r.assets.map((a: AnyObj) => a.symbol), x: r.assets.map((a: AnyObj) => a.contribution),
                    marker: { color: r.assets.map((a: AnyObj) => (a.contribution < 0 ? seriesColor(theme, 7) : seriesColor(theme, 0))) },
                    customdata: r.assets.map((a: AnyObj) => [a.shock_return, a.weight]), hovertemplate: "%{y}: %{x:.2%} (shock %{customdata[0]:.1%}, weight %{customdata[1]:.1%})<extra></extra>",
                  }]} layout={{ hovermode: "closest", xaxis: { tickformat: ".1%" }, yaxis: { autorange: "reversed" }, margin: { l: 60, r: 10, t: 8, b: 30 } }} />
                </Card>
              )}
              <Card title="Asset-level impact" flush actions={res!.id && <button className="btn sm" onClick={() => download(buildUrl(`/exports/stress-tests/${res!.id}`))}>CSV</button>}>
                <DataTable<AnyObj> rows={r.assets} filterable={false} pageSize={60} columns={distributional ? [
                  { key: "symbol", label: "Symbol", render: (a) => <b className="mono">{a.symbol}</b> }, { key: "name", label: "Name" },
                  { key: "weight", label: "Weight", align: "right", render: (a) => pct(a.weight) },
                  { key: "pct_risk_contribution_stressed", label: "% of stressed vol", align: "right", render: (a) => pct(a.pct_risk_contribution_stressed) },
                  { key: "standalone_daily_vol_stressed", label: "Stressed daily vol", align: "right", render: (a) => pct(a.standalone_daily_vol_stressed) },
                ] : [
                  { key: "symbol", label: "Symbol", render: (a) => <b className="mono">{a.symbol}</b> }, { key: "name", label: "Name" }, { key: "sector", label: "Sector" },
                  { key: "weight", label: "Weight", align: "right", render: (a) => pct(a.weight) },
                  { key: "shock_return", label: "Asset move", align: "right", render: (a) => <span className={tone(a.shock_return)}>{spct(a.shock_return)}</span> },
                  { key: "contribution", label: "Contribution", align: "right", render: (a) => <span className={tone(a.contribution)}>{spct(a.contribution)}</span> },
                  { key: "position_value_before", label: "Value before", align: "right", render: (a) => money(a.position_value_before) },
                  { key: "position_value_after", label: "Value after", align: "right", render: (a) => money(a.position_value_after) },
                ]} />
              </Card>
              {r.betas && <div className="xs muted">Betas estimated on {r.lookback_days} days: {Object.entries(r.betas as Record<string, number>).map(([k, v]) => `${k} ${num(v, 2)}`).join(" · ")}</div>}
              {r.window_start && <div className="xs muted">Replayed window {r.window_start} → {r.window_end}.</div>}
            </>
          )}
          <Card title="Saved stress tests" flush>
            <QueryView q={history}>
              {(rows) => (
                <DataTable<AnyObj> rows={rows} pageSize={10} onRowClick={(t) => api.get<AnyObj>(`/stress-tests/${t.id}`).then(setRes)} columns={[
                  { key: "created_at", label: "Run", render: (t) => dt(t.created_at) }, { key: "name", label: "Scenario" }, { key: "scenario_type", label: "Type" },
                  { key: "portfolio_impact_pct", label: "Impact", align: "right", render: (t) => <span className={tone(t.portfolio_impact_pct)}>{spct(t.portfolio_impact_pct)}</span> },
                ]} empty="No saved scenarios." />
              )}
            </QueryView>
          </Card>
          <Disclaimer>Stress results are hypothetical scenarios under linear assumptions (betas, scaled covariance). They are not forecasts and do not capture liquidity, non-linear instruments or second-round effects.</Disclaimer>
        </div>
      </div>
    </>
  );
}
