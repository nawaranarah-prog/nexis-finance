import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api";
import type { Dataset } from "../types/api";

export type Theme = "system" | "light" | "dark";

export interface Settings {
  riskFreeRate: number;
  rollingWindow: number;
  theme: Theme;
}

interface Workspace {
  datasets: Dataset[];
  dataset: Dataset | null;
  datasetId: number | null;
  setDatasetId: (id: number) => void;
  portfolioId: number | null;
  setPortfolioId: (id: number | null) => void;
  settings: Settings;
  updateSettings: (s: Partial<Settings>) => void;
  loading: boolean;
  error: unknown;
}

const DEFAULTS: Settings = { riskFreeRate: 0.02, rollingWindow: 63, theme: "system" };
const Ctx = createContext<Workspace | null>(null);

function load<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable (private mode) — preferences simply don't persist */
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [datasetId, setDs] = useState<number | null>(() => load("nexis.dataset", null));
  const [portfolioId, setPf] = useState<number | null>(() => load("nexis.portfolio", null));
  const [settings, setSettings] = useState<Settings>(() => ({ ...DEFAULTS, ...load("nexis.settings", {}) }));

  const q = useQuery({ queryKey: ["datasets"], queryFn: () => api.get<Dataset[]>("/datasets") });
  const datasets = useMemo(() => q.data ?? [], [q.data]);
  const dataset = datasets.find((d) => d.id === datasetId) ?? datasets[0] ?? null;

  useEffect(() => {
    if (dataset && dataset.id !== datasetId) setDs(dataset.id);
  }, [dataset, datasetId]);

  useEffect(() => {
    const root = document.documentElement;
    if (settings.theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", settings.theme);
  }, [settings.theme]);

  const setDatasetId = useCallback((id: number) => {
    setDs(id);
    save("nexis.dataset", id);
    setPf(null);
    save("nexis.portfolio", null);
  }, []);
  const setPortfolioId = useCallback((id: number | null) => {
    setPf(id);
    save("nexis.portfolio", id);
  }, []);
  const updateSettings = useCallback((s: Partial<Settings>) => {
    setSettings((prev) => {
      const next = { ...prev, ...s };
      save("nexis.settings", next);
      return next;
    });
  }, []);

  const value: Workspace = {
    datasets, dataset, datasetId: dataset?.id ?? null, setDatasetId, portfolioId, setPortfolioId,
    settings, updateSettings, loading: q.isLoading, error: q.error,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): Workspace {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return v;
}

/** Resolved current theme ("light" | "dark") for chart styling. */
export function useResolvedTheme(): "light" | "dark" {
  const { settings } = useWorkspace();
  const [sys, setSys] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => {
    const m = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!m) return;
    const h = (e: MediaQueryListEvent) => setSys(e.matches);
    m.addEventListener("change", h);
    return () => m.removeEventListener("change", h);
  }, []);
  return settings.theme === "system" ? (sys ? "dark" : "light") : settings.theme;
}
