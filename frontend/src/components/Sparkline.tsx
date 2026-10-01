import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api";
import type { AnyObj } from "../types/api";

/** Daily closes for the last month, shared by every sparkline of the same symbol. */
export function useCloses(symbol: string, enabled = true) {
  return useQuery({
    queryKey: ["spark", symbol],
    queryFn: async () => {
      const h = await api.get<AnyObj>(`/markets/instruments/${encodeURIComponent(symbol)}/history`, { period: "1mo", interval: "1d" });
      return (h.bars as AnyObj[]).map((b) => b.close as number).filter((v) => Number.isFinite(v));
    },
    enabled,
    staleTime: 30 * 60_000,
    retry: 1,
  });
}

/** A one-month line drawn from real closes. Renders nothing until there are at least two points, so nothing is ever invented. */
export function Sparkline({ symbol, width = 72, height = 22, label }: { symbol: string; width?: number; height?: number; label?: string }) {
  const q = useCloses(symbol);
  const pts = q.data ?? [];
  if (q.isLoading) return <span className="spark spark-wait" style={{ width, height }} aria-hidden />;
  if (pts.length < 2) return <span className="spark" style={{ width, height }} aria-hidden />;
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const span = hi - lo || 1;
  const step = width / (pts.length - 1);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1)}`).join("");
  const dir = pts[pts.length - 1] >= pts[0] ? "up" : "down";
  return (
    <svg className={`spark ${dir}`} width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label={label ?? `${symbol}: ${dir === "up" ? "up" : "down"} over the last month`}>
      <path d={d} pathLength={1} />
    </svg>
  );
}
