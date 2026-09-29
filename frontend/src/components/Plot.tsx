import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";
import type { Config, Data, Layout } from "plotly.js";
import { useResolvedTheme } from "../hooks/workspace";
import { baseLayout } from "./charts";

interface Props {
  data: Data[];
  layout?: Partial<Layout>;
  height?: number;
  config?: Partial<Config>;
  onClick?: (pt: { x: unknown; y: unknown; customdata?: unknown; curveNumber: number }) => void;
}

const CONFIG: Partial<Config> = {
  displaylogo: false,
  responsive: true,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
  toImageButtonOptions: { format: "png", scale: 2 },
};

/** Interactive Plotly chart (hover, zoom, pan, reset, PNG export) themed with the app tokens. */
export default function Plot({ data, layout, height = 300, config, onClick }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useResolvedTheme();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const base = baseLayout(theme);
    const merged: Partial<Layout> = {
      ...base,
      ...layout,
      xaxis: { ...(base.xaxis as object), ...(layout?.xaxis as object) },
      yaxis: { ...(base.yaxis as object), ...(layout?.yaxis as object) },
      height,
    } as Partial<Layout>;
    // A range selector and a legend would collide in the top strip: stack the legend above it.
    if ((merged.xaxis as { rangeselector?: unknown } | undefined)?.rangeselector && merged.showlegend !== false && data.length > 1) {
      const m = merged.margin ?? {};
      merged.margin = { ...m, t: Math.max(m.t ?? 12, 48) };
      merged.legend = { ...(merged.legend ?? {}), y: 1.2, yanchor: "bottom" };
    }
    Plotly.react(el, data, merged, { ...CONFIG, ...config });
  }, [data, layout, height, config, theme]);

  useEffect(() => {
    const el = ref.current as (HTMLDivElement & { on?: (ev: string, cb: (e: { points: never[] }) => void) => void }) | null;
    if (!el || !onClick || !el.on) return;
    el.on("plotly_click", (e: { points: never[] }) => {
      const p = e.points?.[0] as unknown as { x: unknown; y: unknown; customdata?: unknown; curveNumber: number } | undefined;
      if (p) onClick(p);
    });
    return () => {
      (el as unknown as { removeAllListeners?: (ev: string) => void }).removeAllListeners?.("plotly_click");
    };
  }, [onClick, data]);

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (el.offsetParent !== null) Plotly.Plots.resize(el);
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      Plotly.purge(el);
    };
  }, []);

  return <div ref={ref} className="chart" style={{ minHeight: height }} />;
}
