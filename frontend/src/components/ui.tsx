import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";
import { useGlossary } from "../hooks/queries";
import { errorMessage } from "../services/api";
import type { Job } from "../types/api";

export function Card({ title, sub, actions, children, flush, className }: {
  title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="card-head">
          <div className="card-title">{title}{sub && <span className="card-sub">{sub}</span>}</div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={`card-body ${flush ? "flush" : ""}`}>{children}</div>
    </section>
  );
}

export function PageHead({ title, desc, actions }: { title: string; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {desc && <p>{desc}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

/** Methodology tooltip. With `metric`, text comes from the backend glossary (what / how / assumptions). */
export function InfoTip({ metric, text }: { metric?: string; text?: ReactNode }) {
  const g = useGlossary();
  const entry = metric ? g.data?.[metric] : undefined;
  if (!entry && !text) return null;
  return (
    <span className="tip" tabIndex={0} aria-label="methodology">
      <span className="tip-icon">i</span>
      <span className="tip-body" role="tooltip">
        {entry ? (
          <>
            <b>{entry.label}</b>
            <div>{entry.what}</div>
            <div className="tip-row"><span className="tip-k">Calculation</span><div>{entry.how}</div></div>
            <div className="tip-row"><span className="tip-k">Assumptions</span><div>{entry.assumptions}</div></div>
          </>
        ) : text}
      </span>
    </span>
  );
}

export function Kpi({ label, value, note, metric, tone, tip }: {
  label: ReactNode; value: ReactNode; note?: ReactNode; metric?: string; tone?: string; tip?: ReactNode;
}) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}{(metric || tip) && <InfoTip metric={metric} text={tip} />}</div>
      <div className={`kpi-value num ${tone ?? ""}`}>{value}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="state"><span className="spinner" /> <span style={{ marginLeft: 8 }}>{label}…</span></div>;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="state error" role="alert">
      {errorMessage(error)}
      {onRetry && <div style={{ marginTop: 8 }}><button className="btn sm" onClick={onRetry}>Retry</button></div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state">{children}</div>;
}

/** Wraps a query result into loading / error / content states. */
export function QueryView<T>({ q, children, label }: {
  q: { data?: T; isLoading: boolean; error: unknown; refetch?: () => unknown }; children: (d: T) => ReactNode; label?: string;
}) {
  if (q.isLoading) return <Loading label={label} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch ? () => q.refetch?.() : undefined} />;
  if (q.data === undefined) return <Empty>No data.</Empty>;
  return <>{children(q.data)}</>;
}

export function Field({ label, hint, children, metric }: { label: ReactNode; hint?: ReactNode; children: ReactNode; metric?: string }) {
  const auto = useId();
  // Tie the label to a single form control so screen readers (and clicks on the label) reach it.
  const control = isValidElement(children) && ["input", "select", "textarea"].includes(children.type as string) ? (children as ReactElement<{ id?: string }>) : null;
  const id = control ? control.props.id ?? auto : undefined;
  return (
    <div className="field">
      <label htmlFor={id}>{label}{metric && <InfoTip metric={metric} />}</label>
      {control ? cloneElement(control, { id }) : children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

export function Seg<T extends string | number>({ value, options, onChange }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="radiogroup">
      {options.map((o) => (
        <button key={String(o.value)} role="radio" aria-checked={o.value === value} className={o.value === value ? "on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, tabs, onChange }: { value: T; tabs: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.value} role="tab" aria-selected={t.value === value} className={t.value === value ? "on" : ""} onClick={() => onChange(t.value)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const s = (status ?? "").toLowerCase();
  const cls = ["pass", "success", "succeeded", "completed", "ok"].includes(s) ? "good"
    : ["fail", "failed", "error", "critical"].includes(s) ? "bad"
    : ["warning", "running", "queued", "high"].includes(s) ? "warn" : "";
  const icon = cls === "good" ? "✓" : cls === "bad" ? "✕" : cls === "warn" ? "!" : "•";
  return <span className={`badge ${cls}`}><span aria-hidden>{icon}</span>{status ?? "–"}</span>;
}

export function JobStatus({ job, error, running }: { job: Job | null; error: string | null; running: boolean }) {
  if (error) return <div className="banner error" role="alert">{error}</div>;
  if (!job) return null;
  if (running) {
    return (
      <div className="stack" style={{ gap: 4 }}>
        <div className="row small text2"><span className="spinner" /> {job.message ?? job.status} · {Math.round(job.progress * 100)}%</div>
        <div className="progress"><div style={{ width: `${Math.max(4, job.progress * 100)}%` }} /></div>
      </div>
    );
  }
  if (job.status === "succeeded") return <div className="banner info">Completed{job.result?.experiment_code ? ` — ${job.result.experiment_code}` : ""}.</div>;
  return null;
}

export function Disclaimer({ children }: { children?: ReactNode }) {
  return (
    <div className="banner neutral small">
      <span aria-hidden>ⓘ</span>
      <span>{children ?? "Research output and historical simulation for educational purposes. Not investment advice."}</span>
    </div>
  );
}

export function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
