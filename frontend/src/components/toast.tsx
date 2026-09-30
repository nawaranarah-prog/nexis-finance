import { useEffect, useState } from "react";
import { STATUS } from "./charts";

type Level = "success" | "error" | "warning" | "info";
interface Toast { id: number; level: Level; title: string; body?: string; leaving?: boolean }

let seq = 0;
let toasts: Toast[] = [];
const listeners = new Set<(t: Toast[]) => void>();
const emit = () => listeners.forEach((l) => l([...toasts]));

function dismiss(id: number) {
  toasts = toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t));
  emit();
  window.setTimeout(() => { toasts = toasts.filter((t) => t.id !== id); emit(); }, 200);
}

/** Show a transient message. Kept to three visible at once; newest in front. */
export function toast(level: Level, title: string, body?: string, ms = 4500) {
  const id = ++seq;
  toasts = [...toasts, { id, level, title, body }].slice(-3);
  emit();
  window.setTimeout(() => dismiss(id), ms);
}

const ICON: Record<Level, [string, string]> = {
  success: ["✓", STATUS.good], error: ["✕", STATUS.critical], warning: ["!", STATUS.warning], info: ["i", "#3987e5"],
};

export function Toaster() {
  const [items, setItems] = useState<Toast[]>([]);
  useEffect(() => {
    listeners.add(setItems);
    return () => { listeners.delete(setItems); };
  }, []);
  return (
    <div className="toaster" role="region" aria-live="polite" aria-label="Notifications">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.leaving ? "leaving" : ""}`} onClick={() => dismiss(t.id)}>
          <span className="t-icon" style={{ background: ICON[t.level][1] }} aria-hidden>{ICON[t.level][0]}</span>
          <div><div className="t-title">{t.title}</div>{t.body && <div className="t-body">{t.body}</div>}</div>
        </div>
      ))}
    </div>
  );
}
