// Consistent financial number formatting. Missing values render as an en dash.

export type Num = number | null | undefined;
const DASH = "–";
const ok = (v: Num): v is number => typeof v === "number" && Number.isFinite(v);

export const pct = (v: Num, d = 2) => (ok(v) ? `${(v * 100).toFixed(d)}%` : DASH);
export const spct = (v: Num, d = 2) => (ok(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}%` : DASH);
export const num = (v: Num, d = 3) =>
  ok(v) ? v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : DASH;
export const int = (v: Num) => (ok(v) ? Math.round(v).toLocaleString("en-US") : DASH);
export const money = (v: Num, d = 0) =>
  ok(v) ? `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}` : DASH;
export const compact = (v: Num) =>
  ok(v) ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v) : DASH;
export const secs = (v: Num) => (ok(v) ? (v < 1 ? `${(v * 1000).toFixed(0)} ms` : `${v.toFixed(1)} s`) : DASH);
export const bytes = (v: Num) => (ok(v) ? (v > 1e6 ? `${(v / 1e6).toFixed(1)} MB` : `${(v / 1e3).toFixed(0)} KB`) : DASH);
export const dt = (s: string | null | undefined) => (s ? s.replace("T", " ").slice(0, 16) : DASH);
export const tone = (v: Num) => (!ok(v) || v === 0 ? "" : v > 0 ? "pos" : "neg");

const SIGNED = /(return|drawdown|alpha|impact|pnl|difference)/;
const PCT = /(volatility|tracking_error|deviation|win_rate|frequency|recall|precision|completeness|score|coverage|^var|cvar|weight|contribution|exposure|turnover_pct|cost_drag|costs_pct)/;

/** Format a metric by its key so tables of mixed metrics render consistently. */
export function fmtMetric(key: string, v: Num): string {
  if (!ok(v)) return DASH;
  const k = key.toLowerCase();
  if (/(ratio|factor|beta|correlation|sharpe|sortino|calmar|skew|kurt|r2|rmse|mae|qlike|bic|aic|index|lr_stat|p_value|objective)/.test(k))
    return num(v);
  if (/(transaction_costs|final_equity|value|capital|amount)/.test(k) && !/pct/.test(k)) return money(v);
  if (/(trades|count|observations|days|flagged|rows|rebalance|events|n_regimes)/.test(k)) return int(v);
  if (SIGNED.test(k)) return spct(v);
  if (PCT.test(k)) return pct(v);
  if (/turnover/.test(k)) return num(v, 2);
  return num(v);
}
