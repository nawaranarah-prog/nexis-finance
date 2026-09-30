import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../services/api";
import { toast } from "../components/toast";
import type { Asset, Experiment, GlossaryEntry, Job, Portfolio, StrategyInfo } from "../types/api";

export function useAssets(datasetId: number | null) {
  return useQuery({
    queryKey: ["assets", datasetId],
    queryFn: () => api.get<Asset[]>("/assets", { dataset_id: datasetId }),
    enabled: datasetId != null,
    staleTime: 60_000,
  });
}

export function usePortfolios(datasetId: number | null) {
  return useQuery({
    queryKey: ["portfolios", datasetId],
    queryFn: () => api.get<Portfolio[]>("/portfolios", { dataset_id: datasetId }),
    enabled: datasetId != null,
  });
}

export function useGlossary() {
  return useQuery({
    queryKey: ["glossary"],
    queryFn: () => api.get<Record<string, GlossaryEntry>>("/meta/glossary"),
    staleTime: Infinity,
  });
}

export function useStrategies() {
  return useQuery({ queryKey: ["strategies"], queryFn: () => api.get<StrategyInfo[]>("/strategies"), staleTime: Infinity });
}

export function useExperiments(type?: string, datasetId?: number | null) {
  return useQuery({
    queryKey: ["experiments", type ?? "all", datasetId ?? null],
    queryFn: () => api.get<Experiment[]>("/experiments", { experiment_type: type, dataset_id: datasetId ?? undefined }),
  });
}

export function useExperiment(id: number | null) {
  return useQuery({
    queryKey: ["experiment", id],
    queryFn: () => api.get<Experiment>(`/experiments/${id}`),
    enabled: id != null,
    staleTime: 5 * 60_000,
  });
}

const JOB_LABEL: Record<string, string> = {
  backtest: "Backtest", walk_forward: "Walk-forward analysis", volatility_forecast: "Volatility experiment", regime: "Regime model",
  anomaly: "Anomaly scan", report: "Research report", reproduce: "Reproduction", sync: "Sync",
};

function announce(j: Job) {
  const label = JOB_LABEL[j.job_type] ?? "Job";
  if (j.status === "succeeded") {
    const r = j.result ?? {};
    const detail = r.experiment_code ?? r.file_name ?? (r.records_added != null ? `${r.records_added} added, ${r.records_updated ?? 0} updated` : undefined);
    toast(r.status === "failed" ? "error" : r.status === "warning" ? "warning" : "success", `${label} ${r.status === "failed" ? "failed" : "completed"}`, detail);
  } else if (j.status === "failed") {
    toast("error", `${label} failed`, j.error ?? undefined, 7000);
  }
}

/**
 * Submit a long-running operation that returns a Job (HTTP 202) and poll it to completion.
 * On success the given query keys are invalidated so lists refresh.
 */
export function useJobRunner(invalidate: string[][] = []) {
  const qc = useQueryClient();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  const poll = useCallback(
    (id: string, resolve: (j: Job) => void) => {
      api.get<Job>(`/jobs/${id}`).then(
        (j) => {
          setJob(j);
          if (j.status === "succeeded" || j.status === "failed") {
            announce(j);
            if (j.status === "failed") setError(j.error ?? "Job failed");
            invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k }));
            qc.invalidateQueries({ queryKey: ["notifications"] });
            resolve(j);
          } else {
            timer.current = window.setTimeout(() => poll(id, resolve), 700);
          }
        },
        (e) => {
          setError(errorMessage(e));
          resolve({ ...(job as Job), status: "failed" });
        },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [qc, JSON.stringify(invalidate)],
  );

  const run = useCallback(
    async (path: string, body: unknown): Promise<Job | null> => {
      setError(null);
      setSubmitting(true);
      try {
        const j = await api.post<Job>(path, body);
        setJob(j);
        if (j.status === "succeeded" || j.status === "failed") {
          announce(j);
          if (j.status === "failed") setError(j.error ?? "Job failed");
          invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k }));
          return j;
        }
        return await new Promise<Job>((resolve) => poll(j.id, resolve));
      } catch (e) {
        setError(errorMessage(e));
        toast("error", "Request rejected", errorMessage(e), 7000);
        return null;
      } finally {
        setSubmitting(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [poll],
  );

  const running = submitting || (job != null && (job.status === "queued" || job.status === "running"));
  return { run, job, error, running, reset: () => { setJob(null); setError(null); } };
}
