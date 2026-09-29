// Thin typed wrapper around fetch. Errors from the API arrive as {error: {code, message, details}}.

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type Params = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function buildUrl(path: string, params?: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    qs.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const q = qs.toString();
  return `${BASE}/api${path}${q ? `?${q}` : ""}`;
}

async function request<T>(method: string, path: string, body?: unknown, params?: Params): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, params), {
      method,
      headers: body !== undefined && !(body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the Nexis API. Is the backend running on port 8000?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = (data as { error?: { code: string; message: string; details?: unknown } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (HTTP ${res.status})`, err?.details);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, params?: Params) => request<T>("GET", path, undefined, params),
  post: <T>(path: string, body?: unknown, params?: Params) => request<T>("POST", path, body ?? {}, params),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.details as { problems?: string[] } | { field: string; message: string }[] | null;
    if (Array.isArray(d) && d.length) return `${e.message}: ${d.map((x) => `${x.field} — ${x.message}`).join("; ")}`;
    if (d && !Array.isArray(d) && Array.isArray(d.problems)) return `${e.message}: ${d.problems.join("; ")}`;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
