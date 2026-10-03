import { useState } from "react";
import { Link } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../components/market";
import { toast } from "../components/toast";
import { useT } from "../i18n";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";
import { ago } from "../components/pulseParts";

const CATEGORY_LABEL: Record<string, string> = {
  important: "Important alerts", earnings: "Earnings and dividends", portfolio: "Portfolio events", watchlist: "Watchlist events", news: "News", pulse: "Pulse activity",
};
const MODES = ["immediate", "daily", "weekly", "off"] as const;
const MODE_LABEL: Record<string, string> = { immediate: "Immediate", daily: "Daily digest", weekly: "Weekly digest", off: "Off" };

function Settings() {
  const { t } = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["notif-prefs"], queryFn: () => api.get<AnyObj>("/me/notification-preferences") });
  const [preview, setPreview] = useState<AnyObj | null>(null);
  const p = q.data;
  const save = async (body: AnyObj) => {
    try { qc.setQueryData(["notif-prefs"], await api.put<AnyObj>("/me/notification-preferences", body)); } catch (x) { toast("error", t("Couldn't save"), errorMessage(x)); }
  };
  const prepare = async () => {
    try { const r = await api.post<AnyObj>("/me/digest", undefined, { period: "daily" }); toast("info", t("Digest prepared"), r.detail ?? undefined); qc.invalidateQueries({ queryKey: ["digests"] }); }
    catch (x) { toast("error", t("Couldn't prepare the digest"), errorMessage(x)); }
  };
  if (!p) return null;
  return (
    <aside className="nt-settings">
      <section className="pl-section">
        <div className="sec-head"><h2>{t("How you're notified")}</h2></div>
        <p className="xs muted">{t("Immediate alerts appear here and on the bell. Digest categories are collected into a daily or weekly summary.")}</p>
        <div className="nt-prefs">
          {Object.keys(CATEGORY_LABEL).map((c) => (
            <label key={c} className="nt-pref">
              <span>{t(CATEGORY_LABEL[c])}</span>
              <select className="input" value={p.channels[c]} onChange={(e) => save({ channels: { [c]: e.target.value } })}>
                {MODES.map((m) => <option key={m} value={m}>{t(MODE_LABEL[m])}</option>)}
              </select>
            </label>
          ))}
        </div>
        <Link className="sec-link" to="/portfolio?tab=alerts">{t("Choose which events alert you")} <span aria-hidden>→</span></Link>
      </section>
      <section className="pl-section">
        <div className="sec-head"><h2>{t("Email")}</h2></div>
        <label className="nt-pref">
          <span>{t("Email me my digests")}</span>
          <input type="checkbox" checked={p.email_enabled} onChange={(e) => save({ email_enabled: e.target.checked })} />
        </label>
        {!p.email_available && <p className="pl-disclosure compact">{t("Email delivery isn't connected yet, so no emails are sent. Digests are still prepared and you can preview them here.")}</p>}
        <div className="row" style={{ gap: 8, marginTop: 8 }}>
          <button type="button" className="btn sm" onClick={async () => setPreview(await api.get<AnyObj>("/me/digest/preview", { period: "daily" }))}>{t("Preview today's digest")}</button>
          <button type="button" className="btn sm ghost" onClick={prepare}>{t("Prepare it now")}</button>
        </div>
        {preview && (
          <div className="nt-digest">
            <b>{preview.subject}</b>
            {preview.empty && <p className="pl-empty-line">{t("Nothing to report today.")}</p>}
            {preview.assets.map((a: AnyObj) => (
              <div key={a.symbol} className="nt-digest-asset"><span className="mono">{a.symbol}</span>
                <ul>{a.items.map((n: AnyObj) => <li key={n.id}>{n.title}</li>)}</ul></div>
            ))}
            {preview.top_discussions.length > 0 && <div className="nt-digest-asset"><span>{t("Top Pulse discussions")}</span><ul>{preview.top_discussions.map((d: AnyObj) => <li key={d.id}><span className="mono">{d.asset}</span> {d.title} <span className="xs muted">· {d.source}</span></li>)}</ul></div>}
          </div>
        )}
      </section>
    </aside>
  );
}

export default function Notifications() {
  const { t, lang } = useT();
  const me = useMe();
  const qc = useQueryClient();
  const [unread, setUnread] = useState(false);
  const q = useInfiniteQuery({
    queryKey: ["my-notifications", unread],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/me/notifications", { ...(unread ? { unread: true } : {}), ...(pageParam ? { before: pageParam } : {}) }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next ?? null,
    enabled: !!me.data?.user,
  });
  if (me.isLoading) return <div className="hm pt" />;
  if (!me.data?.user) {
    return (
      <div className="hm pt"><header className="pl-head"><h1>{t("Notifications")}</h1>
        <p className="hm-lede">{t("Sign in to get alerts about the assets you hold and watch, and replies to your discussions.")}</p>
        <Link className="btn primary" to="/login?next=/notifications">{t("Sign in")}</Link></header></div>
    );
  }
  const items: AnyObj[] = (q.data?.pages ?? []).flatMap((p) => p.items);
  const count = q.data?.pages?.[0]?.unread ?? 0;
  const markAll = async () => {
    await api.post("/me/notifications/read", {});
    qc.invalidateQueries({ queryKey: ["my-notifications"] });
    qc.invalidateQueries({ queryKey: ["my-unread"] });
  };
  const open = async (n: AnyObj) => {
    if (!n.read) {
      await api.post("/me/notifications/read", { ids: [n.id] }).catch(() => undefined);
      qc.invalidateQueries({ queryKey: ["my-notifications"] });
      qc.invalidateQueries({ queryKey: ["my-unread"] });
    }
  };
  return (
    <div className="hm pt">
      <header className="pl-head"><div className="hm-eyebrow"><span>{t("Notifications")}</span><span className="num">{count} {t("unread")}</span></div><h1>{t("Developments you track")}</h1></header>
      <div className="nt-grid">
        <section>
          <div className="pt-tabs" role="tablist">
            <button role="tab" aria-selected={!unread} className={!unread ? "on" : ""} onClick={() => setUnread(false)}>{t("All")}</button>
            <button role="tab" aria-selected={unread} className={unread ? "on" : ""} onClick={() => setUnread(true)}>{t("Unread")}</button>
            <span className="grow" />
            <button type="button" className="link-btn xs" disabled={!count} onClick={markAll}>{t("Mark all read")}</button>
          </div>
          {q.isLoading && <div className="wire-skel"><span className="skel w80" /><span className="skel w70" /></div>}
          {!q.isLoading && !items.length && <p className="pl-empty-line pt-empty">{t("No notifications yet. Add holdings or watch assets and Nexis will tell you when something relevant happens.")}</p>}
          <ol className="nt-list">
            {items.map((n) => (
              <li key={n.id} className={`nt-item ${n.read ? "" : "unread"} sev-${n.severity}`}>
                <span className="nt-dot" aria-hidden />
                <div className="nt-body">
                  <div className="nt-meta">
                    {n.symbol && <span className="mono">{n.symbol}</span>}
                    <span>{t(CATEGORY_LABEL[n.category] ?? n.category)}</span>
                    {n.severity !== "info" && <span className={`nt-sev ${n.severity}`}>{n.severity === "high" ? t("Important") : t("Notable")}</span>}
                    <time dateTime={n.created_at}>{ago(n.created_at, lang)}</time>
                  </div>
                  {n.link ? <Link to={n.link} className="nt-title" onClick={() => open(n)} dir="auto">{n.title}</Link> : <span className="nt-title" dir="auto">{n.title}</span>}
                  {n.body && <p className="nt-text" dir="auto">{n.body}</p>}
                  {n.source_name && (
                    <span className="nt-source">{t("Source")}: {n.source_url ? <a href={n.source_url} target="_blank" rel="noreferrer noopener" onClick={() => open(n)}>{n.source_name} ↗</a> : n.source_name}</span>
                  )}
                </div>
                {!n.read && <button type="button" className="link-btn xs" onClick={() => open(n)}>{t("Mark read")}</button>}
              </li>
            ))}
          </ol>
          {q.hasNextPage && <button type="button" className="btn sm pl-more" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{t("Load more")}</button>}
        </section>
        <Settings />
      </div>
    </div>
  );
}
