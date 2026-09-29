// Shapes of the core API entities. Large analytical payloads are typed loosely (AnyObj) on purpose:
// they are rendered generically and their schema is documented in the OpenAPI spec (/docs).

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyObj = Record<string, any>;
export type Series = (number | null)[];

export interface Dataset {
  id: number;
  code: string;
  name: string;
  source: string;
  is_synthetic: boolean;
  description: string | null;
  version: number;
  version_label: string;
  content_hash: string | null;
  start_date: string | null;
  end_date: string | null;
  record_count: number;
  asset_count: number;
  benchmark_count: number;
  created_at: string;
  updated_at: string;
  mode_label: string;
}

export interface Asset {
  id: number;
  symbol: string;
  name: string;
  asset_type: string;
  sector: string | null;
  is_benchmark: boolean;
  first_date: string | null;
  last_date: string | null;
  observations: number;
}

export interface Position {
  symbol: string;
  weight: number;
}

export interface Portfolio {
  id: number;
  name: string;
  dataset_id: number;
  benchmark_symbol: string;
  initial_capital: number;
  rebalance_frequency: string;
  allocation_method: string;
  constraints: AnyObj | null;
  allocation_details: AnyObj | null;
  notes: string | null;
  positions: Position[];
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: string;
  job_type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  message: string | null;
  result: AnyObj | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface MetricRow {
  split: string;
  model: string | null;
  metric: string;
  value: number | null;
}

export interface Experiment {
  id: number;
  code: string;
  experiment_type: string;
  name: string;
  dataset_id: number;
  dataset_version: string;
  dataset_hash: string | null;
  config: AnyObj;
  seed: number | null;
  model: string | null;
  features: string[] | null;
  train_start: string | null;
  train_end: string | null;
  test_start: string | null;
  test_end: string | null;
  status: string;
  duration_seconds: number | null;
  error: string | null;
  notes: string | null;
  parent_id: number | null;
  reproducibility: AnyObj | null;
  summary: AnyObj | null;
  created_at: string;
  metrics: MetricRow[];
  artifacts?: AnyObj | null;
  backtest_id?: number;
}

export interface StrategyParam {
  name: string;
  type: string;
  default: unknown;
  description: string;
  minimum: number | null;
  maximum: number | null;
  choices: unknown[] | null;
}

export interface StrategyInfo {
  key: string;
  name: string;
  description: string;
  params: StrategyParam[];
}

export interface GlossaryEntry {
  label: string;
  what: string;
  how: string;
  assumptions: string;
}

export interface Notification {
  id: number;
  level: string;
  category: string;
  title: string;
  message: string;
  link: string | null;
  is_read: boolean;
  created_at: string;
}
