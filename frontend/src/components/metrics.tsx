import type { Data } from "plotly.js";
import { useGlossary } from "../hooks/queries";
import type { AnyObj, Series } from "../types/api";
import { fmtMetric, tone } from "../utils/format";
import { InfoTip, Kpi } from "./ui";

const LABELS: Record<string, string> = {
  cumulative_return: "Cumulative return", annualized_return: "Annualised return", annualized_volatility: "Annualised volatility",
  downside_deviation: "Downside deviation", sharpe_ratio: "Sharpe ratio", sortino_ratio: "Sortino ratio", max_drawdown: "Max drawdown",
  calmar_ratio: "Calmar ratio", beta: "Beta", correlation: "Correlation", tracking_error: "Tracking error",
  information_ratio: "Information ratio", alpha: "Jensen's alpha", win_rate: "Win rate", profit_factor: "Profit factor",
  best_day: "Best day", worst_day: "Worst day", skewness: "Skewness", excess_kurtosis: "Excess kurtosis",
  annualized_turnover: "Annual turnover", total_transaction_costs: "Transaction costs", number_of_trades: "Trades",
  gross_annualized_return: "Gross ann. return", gross_cumulative_return: "Gross cumulative", gross_sharpe_ratio: "Gross Sharpe",
  annual_cost_drag: "Annual cost drag", observations: "Observations",
};

const GLOSSARY_KEY: Record<string, string> = {
  total_transaction_costs: "transaction_costs", gross_annualized_return: "annualized_return", gross_sharpe_ratio: "sharpe_ratio",
  gross_cumulative_return: "cumulative_return",
};

export function label(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

const SIGNED_TONE = /return|alpha|drawdown|best|worst|drag/;

/** KPI strip for a set of metric keys from a summary dict. */
export function MetricStrip({ summary, keys, notes }: { summary: AnyObj | null | undefined; keys: string[]; notes?: Record<string, string> }) {
  return (
    <div className="kpis">
      {keys.map((k) => {
        const v = summary?.[k] as number | null | undefined;
        const t = SIGNED_TONE.test(k) && k !== "annual_cost_drag" ? tone(v) : "";
        return <Kpi key={k} label={label(k)} value={fmtMetric(k, v)} tone={t} metric={GLOSSARY_KEY[k] ?? k} note={notes?.[k]} />;
      })}
    </div>
  );
}

/** Two-or-more column comparison of metric dicts (e.g. portfolio vs benchmark, net vs gross, IS vs OOS). */
export function MetricCompare({ columns, keys }: { columns: { name: string; values: AnyObj | null | undefined }[]; keys: string[] }) {
  const g = useGlossary();
  return (
    <div className="table-wrap">
      <table className="dt">
        <thead>
          <tr><th>Metric</th>{columns.map((c) => <th key={c.name} className="r">{c.name}</th>)}</tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td><span className="row" style={{ gap: 4 }}>{label(k)}{g.data?.[GLOSSARY_KEY[k] ?? k] && <InfoTip metric={GLOSSARY_KEY[k] ?? k} />}</span></td>
              {columns.map((c) => {
                const v = c.values?.[k] as number | null | undefined;
                return <td key={c.name} className={`r ${SIGNED_TONE.test(k) ? tone(v) : ""}`}>{fmtMetric(k, v)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function line(x: string[], y: Series, name: string, color?: string, extra: Partial<Data> = {}): Data {
  return { type: "scatter", mode: "lines", x, y, name, line: { width: 2, color }, connectgaps: false, ...extra } as Data;
}

export const PCT_AXIS = { tickformat: ".0%", hoverformat: ".2%" };
export const PCT_AXIS_1 = { tickformat: ".1%", hoverformat: ".2%" };
