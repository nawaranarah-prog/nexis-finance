import type { Page } from "@playwright/test";

/** Layout problems on the current page at the current viewport, measured in the browser. Each list holds short
    descriptions (tag.class "text…" and the measurement) so a failure says exactly what to fix. */
export type LayoutReport = {
  pageOverflow: number;          // scrollWidth - innerWidth (0 is good)
  pastRightEdge: string[];       // visible elements extending past the screen, outside any scroll container
  clippedText: string[];         // text cut off by overflow:hidden without an ellipsis
  smallTargets: string[];        // interactive elements under 44×44 (only measured at ≤ 768px)
  smallText: string[];           // body text under 14px (only measured on phones, ≤ 480px)
  wideTables: string[];          // tables wider than the screen and not inside a scroll container
  wideCharts: string[];          // charts wider than their container
};

export async function measure(page: Page): Promise<LayoutReport> {
  return page.evaluate(() => {
    const W = window.innerWidth;
    const label = (el: Element) => {
      const cls = typeof (el as HTMLElement).className === "string" ? (el as HTMLElement).className.trim().split(/\s+/).slice(0, 2).join(".") : "";
      const text = (el.textContent || (el as HTMLInputElement).placeholder || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ").slice(0, 40);
      return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}${text ? ` "${text}"` : ""}`;
    };
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      const s = getComputedStyle(el);
      return s.visibility !== "hidden" && s.display !== "none" && Number(s.opacity) > 0;
    };
    const scroller = (el: Element) => {
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        const o = getComputedStyle(a).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return a;
      }
      return null;
    };
    const fixed = (el: Element) => {
      for (let a: Element | null = el; a && a !== document.body; a = a.parentElement) if (getComputedStyle(a).position === "fixed") return true;
      return false;
    };
    const all = [...document.querySelectorAll("body *")].filter(visible);
    // on screen horizontally: excludes off-canvas UI such as the closed mobile navigation drawer
    const onScreen = (el: Element) => { const r = el.getBoundingClientRect(); return r.right > 0 && r.left < W; };
    const out = { pageOverflow: Math.max(0, document.documentElement.scrollWidth - W), pastRightEdge: [] as string[], clippedText: [] as string[],
      smallTargets: [] as string[], smallText: [] as string[], wideTables: [] as string[], wideCharts: [] as string[] };
    for (const el of all) {
      const r = el.getBoundingClientRect();
      if (r.right > W + 1 && !scroller(el) && !fixed(el) && !(el instanceof SVGElement && el.closest("svg") !== el)) out.pastRightEdge.push(`${label(el)} right=${Math.round(r.right)}`);
      const s = getComputedStyle(el);
      const ownText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim());
      if (ownText && (s.overflowX === "hidden" || s.overflowX === "clip") && s.textOverflow !== "ellipsis" && el.scrollWidth > el.clientWidth + 1)
        out.clippedText.push(`${label(el)} ${el.scrollWidth}>${el.clientWidth}`);
      if (W <= 480 && ownText && onScreen(el) && !["script", "style", "code", "kbd", "sup", "sub"].includes(el.tagName.toLowerCase()) && parseFloat(s.fontSize) < 14)
        out.smallText.push(`${label(el)} ${s.fontSize}`);
    }
    if (W <= 768) {
      const targets = all.filter(onScreen).filter((el) => el.matches("a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=tab], [role=radio], summary"));
      for (const el of targets) {
        const inline = el.tagName === "A" && el.parentElement && [...el.parentElement.childNodes].some((n) => n !== el && n.nodeType === 3 && n.textContent!.trim().length > 2);
        if (inline) continue;  // links inside a sentence are exempt (WCAG 2.5.8 inline exception)
        const r = el.getBoundingClientRect();
        if (r.width < 44 || r.height < 44) out.smallTargets.push(`${label(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
      }
    }
    for (const t of all.filter((el) => el.tagName === "TABLE")) {
      const r = t.getBoundingClientRect();
      if (r.width > W + 1 && !scroller(t)) out.wideTables.push(`${label(t)} width=${Math.round(r.width)}`);
    }
    for (const c of all.filter((el) => el.matches(".js-plotly-plot, .chart, canvas, svg.spark, .plot"))) {
      const p = c.parentElement?.getBoundingClientRect();
      const r = c.getBoundingClientRect();
      if (p && r.width > p.width + 2) out.wideCharts.push(`${label(c)} ${Math.round(r.width)}>${Math.round(p.width)}`);
    }
    for (const k of ["pastRightEdge", "clippedText", "smallTargets", "smallText"] as const) out[k] = [...new Set(out[k])];
    return out;
  });
}

export function problems(r: LayoutReport, width: number): string[] {
  const p: string[] = [];
  if (r.pageOverflow > 0) p.push(`page scrolls sideways by ${r.pageOverflow}px`);
  if (r.pastRightEdge.length) p.push(`${r.pastRightEdge.length} element(s) past the right edge: ${r.pastRightEdge.slice(0, 4).join("; ")}`);
  if (r.clippedText.length) p.push(`${r.clippedText.length} clipped text: ${r.clippedText.slice(0, 4).join("; ")}`);
  if (width <= 768 && r.smallTargets.length) p.push(`${r.smallTargets.length} tap target(s) < 44px: ${r.smallTargets.slice(0, 4).join("; ")}`);
  if (width <= 480 && r.smallText.length) p.push(`${r.smallText.length} text element(s) < 14px: ${r.smallText.slice(0, 4).join("; ")}`);
  if (r.wideTables.length) p.push(`wide tables: ${r.wideTables.join("; ")}`);
  if (r.wideCharts.length) p.push(`charts wider than their container: ${r.wideCharts.join("; ")}`);
  return p;
}
