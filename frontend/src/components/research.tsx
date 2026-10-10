import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { api, errorMessage } from "../services/api";
import { download } from "./ui";

/* Shared pieces of the research tools: links that carry a company from one tool to another, and the PDF button for a
   stored run. */

export type Tool = "graph" | "scenario" | "thesis";

/** "Connections / Stress-test / Challenge" for one symbol, leaving out the tool you're already in. */
export function ResearchLinks({ symbol, except, size = "sm", text }: { symbol: string; except?: Tool; size?: "sm" | ""; text?: boolean }) {
  const enc = encodeURIComponent(symbol);
  const cls = text ? "touch-link small" : `btn ${size}`.trim();
  return (
    <>
      {except !== "graph" && <Link className={cls} to={`/world-graph?q=${enc}`}>Connections</Link>}
      {except !== "scenario" && (
        <Link className={cls} to="/time-machine" state={{ inputs: { holdings: "custom", positions: [{ symbol, amount: 10000 }], factor_shocks: {}, asset_shocks: {}, window_weeks: 104, valuation: null } }}>Stress-test</Link>
      )}
      {except !== "thesis" && <Link className={cls} to={`/devils-advocate?symbol=${enc}`}>Challenge a thesis</Link>}
    </>
  );
}

/** Make a private PDF of one stored run and download it. */
export function ReportButton({ runId }: { runId: number }) {
  const [made, setMade] = useState<number | null>(null);
  const make = useMutation({
    mutationFn: () => api.post<{ id: number; download_url: string }>(`/saved-research/runs/${runId}/report`),
    onSuccess: (r) => { setMade(runId); download(r.download_url); },
  });
  return (
    <span className="row">
      <button type="button" className="btn" disabled={make.isPending} onClick={() => make.mutate()}>{make.isPending ? "Preparing PDF…" : "Download PDF report"}</button>
      {made === runId && <span className="muted xs" role="status">Saved to your <Link to="/reports">Reports</Link> (private to you).</span>}
      {make.error && <span className="small neg" role="alert">{errorMessage(make.error)}</span>}
    </span>
  );
}
