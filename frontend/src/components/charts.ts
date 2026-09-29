// Chart styling derived from the validated reference palette (see docs/ARCHITECTURE.md, "Visual design").
// Categorical hues are assigned in a fixed order and follow the entity, never its rank.

import type { Layout } from "plotly.js";

export const SERIES = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};

export const INK = {
  light: { surface: "#fcfcfb", text: "#0b0b0b", text2: "#52514e", muted: "#898781", grid: "#e7e6e0", axis: "#c3c2b7", neutral: "#898781" },
  dark: { surface: "#1a1a19", text: "#f5f5f3", text2: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835", neutral: "#8f8d86" },
};

export const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };

/** Diverging blue ↔ red around a neutral gray midpoint (for correlations and signed returns). */
export function diverging(theme: "light" | "dark"): [number, string][] {
  const mid = theme === "dark" ? "#383835" : "#f0efec";
  return [
    [0, "#184f95"], [0.25, "#6da7ec"], [0.5, mid], [0.75, "#ee8a86"], [1, "#b3261e"],
  ];
}

/** Single-hue sequential blue ramp (magnitude). */
export function sequential(theme: "light" | "dark"): [number, string][] {
  return theme === "dark"
    ? [[0, "#1f2a3a"], [0.5, "#256abf"], [1, "#9ec5f4"]]
    : [[0, "#eef4fc"], [0.5, "#5598e7"], [1, "#0d366b"]];
}

export function seriesColor(theme: "light" | "dark", i: number): string {
  const p = SERIES[theme];
  return p[Math.min(i, p.length - 1)];
}

/** Stable colour per sector name (identity follows the entity). */
const SECTOR_ORDER = [
  "Technology", "Financials", "Healthcare", "Energy", "Industrials", "Consumer Staples", "Consumer Discretionary", "Utilities",
];
export function sectorColor(theme: "light" | "dark", sector: string | null | undefined): string {
  const i = SECTOR_ORDER.indexOf(sector ?? "");
  return i >= 0 ? SERIES[theme][i] : INK[theme].neutral;
}

export function baseLayout(theme: "light" | "dark", overrides: Partial<Layout> = {}): Partial<Layout> {
  const c = INK[theme];
  const axis = {
    gridcolor: c.grid, linecolor: c.axis, zerolinecolor: c.axis, tickfont: { color: c.muted, size: 10.5 },
    title: { font: { color: c.text2, size: 11 } }, automargin: true,
  };
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 11.5, color: c.text2 },
    margin: { l: 52, r: 16, t: 12, b: 36 },
    hovermode: "x unified",
    hoverlabel: { bgcolor: c.surface, bordercolor: c.axis, font: { color: c.text, size: 11.5 } },
    legend: { orientation: "h", x: 0, y: 1.08, font: { size: 11, color: c.text2 }, bgcolor: "rgba(0,0,0,0)" },
    xaxis: { ...axis },
    yaxis: { ...axis },
    colorway: SERIES[theme],
    ...overrides,
  } as Partial<Layout>;
}

/** Date x-axis with range presets (1M/6M/1Y/3Y/All) — the chart's date-range control. */
export function dateAxis(theme: "light" | "dark", withSelector = true): Partial<Layout["xaxis"]> {
  const c = INK[theme];
  return {
    type: "date",
    gridcolor: c.grid,
    linecolor: c.axis,
    tickfont: { color: c.muted, size: 10.5 },
    automargin: true,
    ...(withSelector
      ? {
          rangeselector: {
            x: 1, xanchor: "right", y: 1.02, yanchor: "bottom",
            bgcolor: theme === "dark" ? "#222220" : "#f3f3f0", activecolor: theme === "dark" ? "#2c2c2a" : "#dfe3ea",
            bordercolor: c.axis, borderwidth: 1, font: { size: 10.5, color: c.text2 },
            buttons: [
              { count: 1, label: "1M", step: "month", stepmode: "backward" },
              { count: 6, label: "6M", step: "month", stepmode: "backward" },
              { count: 1, label: "1Y", step: "year", stepmode: "backward" },
              { count: 3, label: "3Y", step: "year", stepmode: "backward" },
              { step: "all", label: "All" },
            ],
          },
        }
      : {}),
  } as Partial<Layout["xaxis"]>;
}
