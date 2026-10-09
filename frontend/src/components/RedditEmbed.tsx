import { useEffect, useRef, useState } from "react";
import type { AnyObj } from "../types/api";

/* A Reddit post shared by a Nexis member, shown with Reddit's official embed (Reddit Embeds Terms). Nexis's servers
   never fetch or store the post: the reader's browser loads it from Reddit, and only when they ask, because the embed
   is Reddit's code and may set Reddit cookies. The link to the original is always there, even if the embed fails or
   the post was removed on Reddit. */

const SCRIPT = "https://embed.reddit.com/widgets.js";
const TIMEOUT_MS = 12_000;

function render() {
  // Reddit's script turns every blockquote.reddit-embed-bq on the page into an embed when it runs; adding it again
  // (from the browser cache) renders embeds added later in this single-page app.
  document.querySelectorAll(`script[data-reddit-embed]`).forEach((s) => s.remove());
  return new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.charset = "UTF-8";
    s.dataset.redditEmbed = "1";
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("blocked"));
    document.body.appendChild(s);
  });
}

export function RedditEmbed({ reddit, compact = false }: { reddit: AnyObj; compact?: boolean }) {
  const [state, setState] = useState<"idle" | "loading" | "shown" | "failed">("idle");
  const box = useRef<HTMLDivElement>(null);
  const dark = document.documentElement.getAttribute("data-theme") === "dark"
    || (document.documentElement.getAttribute("data-theme") !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);

  useEffect(() => {
    if (state !== "loading") return;
    let done = false;
    const timer = window.setTimeout(() => { if (!done) setState("failed"); }, TIMEOUT_MS);
    const seen = new MutationObserver(() => {
      if (box.current?.querySelector("iframe")) { done = true; setState("shown"); }
    });
    if (box.current) seen.observe(box.current, { childList: true, subtree: true });
    render().catch(() => { done = true; setState("failed"); });
    return () => { window.clearTimeout(timer); seen.disconnect(); };
  }, [state]);

  const open = <a href={reddit.url} target="_blank" rel="noreferrer noopener nofollow" className="np-orig">Open on Reddit ↗</a>;
  if (compact) return <span className="np-reddit-tag" title="A member shared a Reddit discussion">Reddit · {reddit.label}</span>;
  return (
    <section className="np-reddit" aria-label="Shared Reddit discussion">
      <div className="np-reddit-head">
        <span className="np-reddit-tag">Reddit · {reddit.label}</span>
        <span className="xs muted">Shared by the member who started this discussion. Written on Reddit, not on Nexis.</span>
        {open}
      </div>
      {state === "idle" && (
        <div className="np-reddit-gate">
          <button type="button" className="btn sm" onClick={() => setState("loading")}>Show the Reddit post here</button>
          <span className="xs muted">Loads Reddit's official embed from reddit.com, which may set Reddit cookies.</span>
        </div>
      )}
      {state === "failed" && <p className="np-note" role="status">The Reddit post couldn't be shown here — it may have been removed, or your browser blocked Reddit. {open}</p>}
      {(state === "loading" || state === "shown") && (
        <div ref={box} className="np-reddit-embed" aria-busy={state === "loading"}>
          <blockquote className="reddit-embed-bq" data-embed-showmedia="false" data-embed-theme={dark ? "dark" : "light"} data-embed-height="360">
            <a href={reddit.url}>{reddit.title}</a>
          </blockquote>
          {state === "loading" && <p className="xs muted" role="status">Loading from Reddit…</p>}
        </div>
      )}
    </section>
  );
}
