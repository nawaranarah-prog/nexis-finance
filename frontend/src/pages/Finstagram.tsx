import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useT } from "../i18n";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Avatar, Change, fmtPrice, TYPE_LABEL, useMe } from "../components/market";
import { ErrorState, Loading } from "../components/ui";
import { toast } from "../components/toast";
import { api, errorMessage } from "../services/api";
import type { AnyObj } from "../types/api";

// ------------------------------------------------------------------ helpers

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

const RICH = /(\$[A-Za-z][A-Za-z0-9]{0,11}(?:[.\-=][A-Za-z0-9]{1,4})?|#[A-Za-z]\w{1,39}|@[a-z0-9_.]{3,30}|https?:\/\/[^\s]+)/g;
function Rich({ text }: { text: string }) {
  return (
    <>{text.split(RICH).map((p, i) => {
      if (/^\$[A-Za-z]/.test(p)) return <Link key={i} className="cashtag" to={`/finstagram/s/${encodeURIComponent(p.slice(1).toUpperCase())}`}>{p.toUpperCase()}</Link>;
      if (p.startsWith("#") && p.length > 2) return <Link key={i} className="hashtag" to={`/finstagram/t/${encodeURIComponent(p.slice(1).toLowerCase())}`}>{p}</Link>;
      if (p.startsWith("@") && p.length > 3) return <Link key={i} className="mention" to={`/finstagram/u/${p.slice(1)}`}>{p}</Link>;
      if (/^https?:\/\//.test(p)) return <a key={i} href={p} target="_blank" rel="noreferrer noopener nofollow">{p.replace(/^https?:\/\//, "").slice(0, 40)}</a>;
      return <Fragment key={i}>{p}</Fragment>;
    })}</>
  );
}

/** Downscale to ≤1440px JPEG in the browser so uploads stay under the hosting request limit. */
async function shrink(file: File): Promise<Blob> {
  const img = await createImageBitmap(file).catch(() => null);
  if (!img) return file;
  const scale = Math.min(1, 1440 / Math.max(img.width, img.height));
  const c = document.createElement("canvas");
  c.width = Math.round(img.width * scale);
  c.height = Math.round(img.height * scale);
  c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
  return await new Promise<Blob>((res) => c.toBlob((b) => res(b ?? file), "image/jpeg", 0.86));
}

const ytId = (url?: string | null) => (url ? /(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]{6,})/.exec(url)?.[1] ?? null : null);
const host = (url: string) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };
const GRADIENTS = ["linear-gradient(135deg,#0f172a,#1e3a8a)", "linear-gradient(135deg,#134e4a,#0f766e)", "linear-gradient(135deg,#3b0764,#7c3aed)",
  "linear-gradient(135deg,#7c2d12,#ea580c)", "linear-gradient(135deg,#1f2937,#475569)", "linear-gradient(135deg,#831843,#db2777)"];
const gradientFor = (s: string) => GRADIENTS[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % GRADIENTS.length];

const SOCIAL_LINKS: Record<string, { label: string; url: (h: string) => string }> = {
  instagram: { label: "Instagram", url: (h) => `https://www.instagram.com/${h}` },
  x: { label: "X", url: (h) => `https://x.com/${h}` },
  linkedin: { label: "LinkedIn", url: (h) => `https://www.linkedin.com/in/${h}` },
  tiktok: { label: "TikTok", url: (h) => `https://www.tiktok.com/@${h}` },
  youtube: { label: "YouTube", url: (h) => `https://www.youtube.com/@${h}` },
  website: { label: "Website", url: (h) => h },
};

type Gate = (why: string, fn: () => void) => void;
/** Reddit / X accounts the person proved they own; each opens their profile on that site. */
function LinkedBadges({ user }: { user: AnyObj }) {
  const linked: AnyObj[] = user?.linked ?? [];
  if (!linked.length) return null;
  return (
    <span className="linked">
      {linked.map((a) => (
        <a key={a.provider} href={a.url} target="_blank" rel="noreferrer noopener" title={`${a.provider === "x" ? "@" : "u/"}${a.username} on ${a.provider === "x" ? "X" : "Reddit"} (verified)`}
          aria-label={`${a.username} on ${a.provider === "x" ? "X" : "Reddit"}`}>
          <span className={`src-mark ${a.provider}`} aria-hidden>{a.provider === "x" ? "𝕏" : "r/"}</span>
        </a>
      ))}
    </span>
  );
}

function useGate(): { me: AnyObj | null; gate: Gate } {
  const me = useMe();
  const nav = useNavigate();
  const gate: Gate = (why, fn) => {
    if (me.data?.user) fn();
    else { toast("info", why, "Sign in or create a free account — it takes a few seconds."); nav(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`); }
  };
  return { me: me.data?.user ?? null, gate };
}

function Icon({ name, filled }: { name: "heart" | "comment" | "share" | "bookmark" | "more" | "play"; filled?: boolean }) {
  const p = { width: 24, height: 24, viewBox: "0 0 24 24", fill: filled ? "currentColor" : "none", stroke: "currentColor", strokeWidth: 1.9, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (name === "heart") return <svg {...p}><path d="M12 20.5s-7.5-4.6-9.3-9.3C1.4 7.9 3.6 4.5 7 4.5c2 0 3.6 1.1 5 3 1.4-1.9 3-3 5-3 3.4 0 5.6 3.4 4.3 6.7-1.8 4.7-9.3 9.3-9.3 9.3z" /></svg>;
  if (name === "comment") return <svg {...p} fill="none"><path d="M20.5 11.5a8.5 8.5 0 0 1-12.6 7.4L3.5 20.5l1.6-4.4A8.5 8.5 0 1 1 20.5 11.5z" /></svg>;
  if (name === "share") return <svg {...p} fill="none"><path d="M21 3 10 14M21 3l-7 18-4-7-7-4 18-7z" /></svg>;
  if (name === "bookmark") return <svg {...p}><path d="M6 3.5h12v17l-6-4.5-6 4.5z" /></svg>;
  if (name === "play") return <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden><circle cx="32" cy="32" r="30" fill="rgba(0,0,0,.55)" /><path d="M26 20l20 12-20 12z" fill="#fff" /></svg>;
  return <svg {...p} fill="currentColor" stroke="none"><circle cx="5" cy="12" r="1.8" /><circle cx="12" cy="12" r="1.8" /><circle cx="19" cy="12" r="1.8" /></svg>;
}

// ------------------------------------------------------------------ search

function SearchBar() {
  const { t: tr } = useT();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [d, setD] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { const t = window.setTimeout(() => setD(q.trim()), 220); return () => window.clearTimeout(t); }, [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const r = useQuery({ queryKey: ["social", "search", d], queryFn: () => api.get<AnyObj>("/social/search", { q: d }), enabled: d.length > 0, staleTime: 60_000 });
  const go = (to: string) => { setOpen(false); setQ(""); nav(to); };
  const res = r.data;
  const empty = res && !res.instruments.length && !res.accounts.length && !res.tags.length;
  return (
    <div className="insta-search" ref={box}>
      <span className="insta-search-icon" aria-hidden>⌕</span>
      <input className="input" value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
        placeholder={tr("Search stocks, pages, people, #tags")} aria-label="Search Finstagram"
        onKeyDown={(e) => {
          if (e.key === "Enter" && res) {
            if (res.instruments[0]) go(`/finstagram/s/${encodeURIComponent(res.instruments[0].symbol)}`);
            else if (res.accounts[0]) go(`/finstagram/u/${res.accounts[0].username}`);
            else if (res.tags[0]) go(`/finstagram/t/${res.tags[0].tag}`);
          } else if (e.key === "Escape") setOpen(false);
        }} />
      {open && d && (
        <div className="insta-search-results popover-in">
          {r.isLoading && <div className="sym-empty"><span className="spinner" /> Searching…</div>}
          {empty && <div className="sym-empty">No results for “{d}”.</div>}
          {res?.instruments.length > 0 && <div className="isr-head">Stocks & markets</div>}
          {res?.instruments.map((s: AnyObj) => (
            <button key={s.symbol} className="isr-row" onMouseDown={(e) => { e.preventDefault(); go(`/finstagram/s/${encodeURIComponent(s.symbol)}`); }}>
              <span className="isr-ticker">{s.symbol.slice(0, 5)}</span>
              <span className="grow"><b>{s.name}</b><span className="xs muted"> · {s.symbol} · {TYPE_LABEL[s.type] ?? s.type} · {s.exchange}</span></span>
            </button>
          ))}
          {res?.accounts.length > 0 && <div className="isr-head">Accounts</div>}
          {res?.accounts.map((u: AnyObj) => (
            <button key={u.id} className="isr-row" onMouseDown={(e) => { e.preventDefault(); go(`/finstagram/u/${u.username}`); }}>
              <Avatar user={u} size={30} />
              <span className="grow"><b>{u.username}</b>{u.kind === "page" && <span className="page-badge">News page</span>}<span className="xs muted"> · {u.display_name}</span></span>
            </button>
          ))}
          {res?.tags.length > 0 && <div className="isr-head">{tr("Hashtags")}</div>}
          {res?.tags.map((t: AnyObj) => (
            <button key={t.tag} className="isr-row" onMouseDown={(e) => { e.preventDefault(); go(`/finstagram/t/${t.tag}`); }}>
              <span className="isr-ticker">#</span><span className="grow"><b>#{t.tag}</b><span className="xs muted"> · {t.posts} posts</span></span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ media

function VideoEmbed({ id, title }: { id: string; title: string }) {
  const [play, setPlay] = useState(false);
  if (play) return (
    <div className="post-video">
      <iframe src={`https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`} title={title} allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowFullScreen />
    </div>
  );
  return (
    <button className="post-video poster" onClick={() => setPlay(true)} aria-label={`Play video: ${title}`}>
      <img src={`https://i.ytimg.com/vi/${id}/hqdefault.jpg`} alt="" loading="lazy" />
      <span className="play"><Icon name="play" /></span>
    </button>
  );
}

function PostMedia({ post, onDoubleTap, pop }: { post: AnyObj; onDoubleTap: () => void; pop: boolean }) {
  const [broken, setBroken] = useState(false);
  const link = post.link as AnyObj | null;
  const vid = ytId(link?.url);
  if (post.image_url) return (
    <div className="post-media" onDoubleClick={onDoubleTap}>
      <img src={post.image_url} alt="" loading="lazy" />
      {pop && <span className="heart-pop" aria-hidden>♥</span>}
    </div>
  );
  if (!link) return null;
  if (vid) return <VideoEmbed id={vid} title={link.title} />;
  if (link.image && !broken) return (
    <a className="post-media linked" href={link.url} target="_blank" rel="noreferrer noopener" onDoubleClick={(e) => { e.preventDefault(); onDoubleTap(); }}>
      <img src={link.image} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} />
      {pop && <span className="heart-pop" aria-hidden>♥</span>}
    </a>
  );
  return (
    <a className="text-card" style={{ background: gradientFor(link.source ?? link.url) }} href={link.url} target="_blank" rel="noreferrer noopener"
      onDoubleClick={(e) => { e.preventDefault(); onDoubleTap(); }}>
      <span className="text-card-src">{link.source || host(link.url)}</span>
      <span className="text-card-title">{link.title}</span>
      <span className="text-card-cta">Read the article ↗</span>
      {pop && <span className="heart-pop" aria-hidden>♥</span>}
    </a>
  );
}

// ------------------------------------------------------------------ share & menu

function ShareMenu({ post, onClose }: { post: AnyObj; onClose: () => void }) {
  const url = `${window.location.origin}/finstagram/p/${post.id}`;
  const text = `${post.link?.title ?? post.body.slice(0, 160)} — via Finstagram`;
  const e = encodeURIComponent;
  const copy = async (then?: string) => {
    try { await navigator.clipboard.writeText(url); toast("success", "Link copied", then); } catch { toast("warning", "Copy failed", url); }
  };
  const native = async () => {
    try {
      const data: ShareData = { title: "Finstagram", text, url };
      if (post.image_url && navigator.canShare) {
        const blob = await fetch(post.image_url).then((r) => r.blob());
        const f = new File([blob], `instafin-${post.id}.jpg`, { type: blob.type });
        if (navigator.canShare({ files: [f] })) data.files = [f];
      }
      await navigator.share(data);
    } catch { /* dismissed */ }
    onClose();
  };
  const targets = [
    { label: "WhatsApp", href: `https://wa.me/?text=${e(`${text} ${url}`)}` },
    { label: "X", href: `https://x.com/intent/post?text=${e(text)}&url=${e(url)}` },
    { label: "LinkedIn", href: `https://www.linkedin.com/sharing/share-offsite/?url=${e(url)}` },
    { label: "Telegram", href: `https://t.me/share/url?url=${e(url)}&text=${e(text)}` },
    { label: "Facebook", href: `https://www.facebook.com/sharer/sharer.php?u=${e(url)}` },
    { label: "Email", href: `mailto:?subject=${e("From Finstagram")}&body=${e(`${text}\n\n${url}`)}` },
  ];
  return (
    <div className="share-menu popover-in" role="menu">
      {"share" in navigator && <button role="menuitem" onClick={native}>Share… <span className="xs muted">Instagram, WhatsApp & more</span></button>}
      <button role="menuitem" onClick={async () => { await copy("Paste it into your Instagram story or bio."); window.open("https://www.instagram.com/", "_blank", "noopener"); onClose(); }}>Instagram <span className="xs muted">copy link & open</span></button>
      {targets.map((t) => <a key={t.label} role="menuitem" href={t.href} target="_blank" rel="noreferrer noopener" onClick={onClose}>{t.label}</a>)}
      <button role="menuitem" onClick={() => { void copy(); onClose(); }}>Copy link</button>
    </div>
  );
}

// ------------------------------------------------------------------ post card

function Comments({ post, me, gate }: { post: AnyObj; me: AnyObj | null; gate: Gate }) {
  const { t: tr } = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["social", "comments", post.id], queryFn: () => api.get<AnyObj[]>(`/social/posts/${post.id}/comments`) });
  const [text, setText] = useState("");
  const send = async () => {
    if (!text.trim()) return;
    try { await api.post(`/social/posts/${post.id}/comments`, { body: text }); setText(""); qc.invalidateQueries({ queryKey: ["social"] }); }
    catch (e) { toast("error", "Comment failed", errorMessage(e)); }
  };
  return (
    <div className="comments">
      {q.data?.length === 0 && <div className="xs muted">No comments yet — start the conversation.</div>}
      {q.data?.map((c) => (
        <div key={c.id} className="comment">
          <Link to={`/finstagram/u/${c.author.username}`}><b>{c.author.username}</b></Link><LinkedBadges user={c.author} /> <span dir="auto"><Rich text={c.body} /></span>
          <span className="xs muted"> · {ago(c.created_at)}</span>
          {(c.is_mine || post.is_mine) && <button className="link-btn xs" onClick={async () => { await api.del(`/social/comments/${c.id}`); qc.invalidateQueries({ queryKey: ["social"] }); }}>delete</button>}
        </div>
      ))}
      <form className="comment-form" onSubmit={(e) => { e.preventDefault(); gate("Sign in to comment", () => void send()); }}>
        <input className="input" dir="auto" placeholder={me ? tr("Add a comment…") : tr("Sign in to comment")} value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} />
        <button className="link-btn" disabled={!text.trim()}>{tr("Post")}</button>
      </form>
    </div>
  );
}

export function PostCard({ post, gate, me, openComments = false }: { post: AnyObj; gate: Gate; me: AnyObj | null; openComments?: boolean }) {
  const qc = useQueryClient();
  const [liked, setLiked] = useState<boolean>(post.liked_by_me);
  const [likes, setLikes] = useState<number>(post.like_count);
  const [saved, setSaved] = useState<boolean>(post.saved_by_me);
  const [following, setFollowing] = useState<boolean>(post.author.followed_by_me);
  const [hidden, setHidden] = useState(false);
  const [pop, setPop] = useState(false);
  const [showComments, setShowComments] = useState(openComments);
  const [share, setShare] = useState(false);
  const [more, setMore] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const { t } = useT();
  useEffect(() => { setLiked(post.liked_by_me); setLikes(post.like_count); setSaved(post.saved_by_me); setFollowing(post.author.followed_by_me); }, [post]);

  const like = () => gate("Sign in to like posts", async () => {
    setLiked(!liked); setLikes(likes + (liked ? -1 : 1));
    if (!liked) { setPop(true); window.setTimeout(() => setPop(false), 480); }
    try { const r = await api.post<AnyObj>(`/social/posts/${post.id}/like`); setLiked(r.liked); setLikes(r.like_count); }
    catch (e) { setLiked(liked); setLikes(likes); toast("error", "Like failed", errorMessage(e)); }
  });
  const save = () => gate("Sign in to save posts", async () => {
    setSaved(!saved);
    try { const r = await api.post<AnyObj>(`/social/posts/${post.id}/save`); setSaved(r.saved); toast("success", r.saved ? "Saved" : "Removed from saved"); }
    catch (e) { setSaved(saved); toast("error", "Save failed", errorMessage(e)); }
  });
  const follow = () => gate("Sign in to follow pages and people", async () => {
    try { await api.post(`/social/users/${post.author.username}/follow`); setFollowing(!following); qc.invalidateQueries({ queryKey: ["social", "trending"] }); }
    catch (e) { toast("error", "Follow failed", errorMessage(e)); }
  });
  const feedback = (signal: "more" | "less") => gate("Sign in to tune your feed", async () => {
    setMore(false);
    try {
      await api.post(`/social/posts/${post.id}/feedback`, { signal });
      if (signal === "less") { setHidden(true); toast("info", "Got it — you'll see fewer posts like this"); }
      else toast("success", "Got it — you'll see more posts like this");
    } catch (e) { toast("error", "Couldn't save that", errorMessage(e)); }
  });
  const remove = async () => {
    if (!window.confirm("Delete this post?")) return;
    try { await api.del(`/social/posts/${post.id}`); qc.invalidateQueries({ queryKey: ["social"] }); toast("success", "Post deleted"); } catch (e) { toast("error", "Delete failed", errorMessage(e)); }
  };
  const report = () => gate("Sign in to report posts", async () => {
    setMore(false);
    try { const r = await api.post<AnyObj>(`/social/posts/${post.id}/report`, { reason: "reported from feed" }); toast("info", "Thanks — reported", r.hidden ? "The post has been hidden." : undefined); } catch (e) { toast("error", "Report failed", errorMessage(e)); }
  });
  const copyLink = async () => { setMore(false); try { await navigator.clipboard.writeText(`${window.location.origin}/finstagram/p/${post.id}`); toast("success", "Link copied"); } catch { /* ignore */ } };

  if (hidden) return (
    <div className="post hidden-note small text2">Post hidden. We'll show you fewer like this. <button className="link-btn" onClick={() => setHidden(false)}>Undo</button></div>
  );
  const link = post.link as AnyObj | null;
  const paragraphs: string[] = post.body.split(/\n\n+/);
  const headline = link ? paragraphs[0] : null;
  const rest = (link ? paragraphs.slice(1) : paragraphs).join("\n\n");
  const long = rest.length > 260 && !expanded;
  const isPage = post.author.kind === "page";
  return (
    <article className="post">
      <header className="post-head">
        <Link to={`/finstagram/u/${post.author.username}`} className="row" style={{ gap: 10, minWidth: 0 }}>
          <Avatar user={post.author} size={34} />
          <span style={{ minWidth: 0 }}>
            <b>{post.author.username}</b>{isPage && <span className="page-badge" title="Automated news page">✓ {t("News page")}</span>}
            <span className="xs muted"> · {ago(post.created_at)}</span>
            {link?.source && <div className="xs muted ellipsis">{isPage ? `via ${link.source}` : link.source}</div>}
          </span>
        </Link>
        <LinkedBadges user={post.author} />
        <div className="row" style={{ gap: 4 }}>
          {!post.is_mine && !following && <button className="link-btn follow-inline" onClick={follow}>{t("Follow")}</button>}
          <div className="post-more">
            <button className="icon-btn" aria-label="More options" onClick={() => setMore(!more)}><Icon name="more" /></button>
            {more && (
              <div className="share-menu popover-in" role="menu" style={{ right: 0, left: "auto", transformOrigin: "top right" }} onMouseLeave={() => setMore(false)}>
                <button role="menuitem" onClick={() => { setMore(false); save(); }}>{saved ? t("Remove from saved") : t("Save")}</button>
                <Link role="menuitem" to={`/advisor?post=${post.id}`}>✦ {t("Ask the AI advisor about this")}</Link>
                <button role="menuitem" onClick={() => feedback("more")}>{t("Suggest more like this")}</button>
                <button role="menuitem" onClick={() => feedback("less")}>{t("Suggest less like this")}</button>
                {!post.is_mine && <button role="menuitem" onClick={() => { setMore(false); follow(); }}>{following ? `Unfollow ${post.author.username}` : `Follow ${post.author.username}`}</button>}
                {link && <a role="menuitem" href={link.url} target="_blank" rel="noreferrer noopener" onClick={() => setMore(false)}>{t("Open original ↗")}</a>}
                <Link role="menuitem" to={`/finstagram/p/${post.id}`}>{t("Go to post")}</Link>
                <button role="menuitem" onClick={copyLink}>{t("Copy link")}</button>
                {post.is_mine ? <button role="menuitem" className="neg" onClick={remove}>{t("Delete")}</button> : <button role="menuitem" className="neg" onClick={report}>{t("Report")}</button>}
              </div>
            )}
          </div>
        </div>
      </header>
      <PostMedia post={post} onDoubleTap={() => !liked && like()} pop={pop} />
      <div className="post-actions">
        <button className={`act ${liked ? "liked" : ""}`} aria-pressed={liked} aria-label="Like" onClick={like}><span className={pop ? "beat" : ""}><Icon name="heart" filled={liked} /></span></button>
        <button className="act" aria-label="Comments" onClick={() => setShowComments(!showComments)}><Icon name="comment" /></button>
        <div className="post-more">
          <button className="act" aria-label="Share" onClick={() => setShare(!share)}><Icon name="share" /></button>
          {share && <ShareMenu post={post} onClose={() => setShare(false)} />}
        </div>
        <Link className="ask-ai" to={`/advisor?post=${post.id}`} title={t("Ask the AI advisor about this")}><span aria-hidden>✦</span>{t("Ask AI")}</Link>
        <button className={`act save ${saved ? "saved" : ""}`} aria-pressed={saved} aria-label="Save" onClick={save}><Icon name="bookmark" filled={saved} /></button>
      </div>
      <div className="post-likes">{likes.toLocaleString()} {likes === 1 ? t("like") : t("likes")}</div>
      <div className="post-body" dir="auto">
        {headline && <div className="post-headline">{link ? <a href={link.url} target="_blank" rel="noreferrer noopener">{headline.replace(/^▶ /, "")}</a> : headline}</div>}
        {rest && <div className={long ? "clamp" : ""}>{!link && <Link to={`/finstagram/u/${post.author.username}`}><b>{post.author.username}</b></Link>} <Rich text={rest} /></div>}
        {long && <button className="link-btn xs" onClick={() => setExpanded(true)}>more</button>}
      </div>
      {post.symbols?.length > 0 && (
        <div className="ticker-cards">
          {post.symbols.map((s: AnyObj) => (
            <Link key={s.symbol} className="ticker-card" to={`/finstagram/s/${encodeURIComponent(s.symbol)}`}>
              <span className="mono">{s.symbol}</span>
              {s.price !== undefined && s.price !== null && <span className="num">{fmtPrice(s.price, s.currency)}</span>}
              <Change pct={s.change_pct} />
            </Link>
          ))}
        </div>
      )}
      {!showComments && post.comment_count > 0 && <button className="link-btn small post-view-comments" onClick={() => setShowComments(true)}>View all {post.comment_count} comments</button>}
      {showComments && <Comments post={post} me={me} gate={gate} />}
    </article>
  );
}

// ------------------------------------------------------------------ composer

function Composer({ me, initial, onPosted }: { me: AnyObj; initial?: string; onPosted: () => void }) {
  const { t: tr } = useT();
  const [text, setText] = useState(initial ?? "");
  const [file, setFile] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);
  const pick = async (f: File | undefined) => {
    if (!f) return;
    if (!f.type.startsWith("image/")) { toast("warning", "Choose an image file"); return; }
    const b = await shrink(f);
    setFile(b);
    setPreview(URL.createObjectURL(b));
  };
  const submit = async () => {
    if (!text.trim() && !file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.set("body", text);
      if (file) form.set("image", file, "photo.jpg");
      await api.upload("/social/posts", form);
      setText(""); setFile(null); setPreview(null);
      onPosted();
      toast("success", "Posted");
    } catch (e) { toast("error", "Post failed", errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="composer">
      <Avatar user={me} size={38} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <textarea className="input" dir="auto" rows={2} maxLength={2200} placeholder={tr("Share an idea, a chart or a trade thesis… use $EMAAR.AE and #tags")} value={text} onChange={(e) => setText(e.target.value)} />
        {preview && <div className="composer-preview"><img src={preview} alt="" /><button className="icon-btn" aria-label="Remove photo" onClick={() => { setFile(null); setPreview(null); }}>✕</button></div>}
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
          <div className="row" style={{ gap: 6 }}>
            <input ref={input} type="file" accept="image/*" hidden onChange={(e) => void pick(e.target.files?.[0])} />
            <button className="btn sm" onClick={() => input.current?.click()}>{tr("📷 Photo")}</button>
            <span className="xs muted">{text.length}/2200</span>
          </div>
          <button className="btn primary sm" disabled={busy || (!text.trim() && !file)} onClick={submit}>{busy ? tr("Posting…") : tr("Post")}</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ rail

function Rail() {
  const { t: tr } = useT();
  const qc = useQueryClient();
  const { gate } = useGate();
  const t = useQuery({ queryKey: ["social", "trending"], queryFn: () => api.get<AnyObj>("/social/trending"), refetchInterval: 120_000 });
  if (!t.data) return <aside className="insta-rail" />;
  const follow = (u: AnyObj) => gate("Sign in to follow pages", async () => {
    try { await api.post(`/social/users/${u.username}/follow`); qc.invalidateQueries({ queryKey: ["social"] }); } catch (e) { toast("error", "Follow failed", errorMessage(e)); }
  });
  return (
    <aside className="insta-rail">
      <div className="rail-card"><div className="rail-title">{tr("News pages to follow")}</div>
        {t.data.pages.map((u: AnyObj) => (
          <div key={u.id} className="rail-row">
            <Link to={`/finstagram/u/${u.username}`} className="row" style={{ gap: 8, minWidth: 0, flex: 1 }}><Avatar user={u} size={30} />
              <span style={{ minWidth: 0 }}><b className="small">{u.username}</b><div className="xs muted ellipsis">{u.display_name}</div></span></Link>
            <button className={`link-btn xs ${u.followed_by_me ? "muted" : ""}`} onClick={() => follow(u)}>{u.followed_by_me ? tr("Following") : tr("Follow")}</button>
          </div>
        ))}
      </div>
      {t.data.symbols.length > 0 && (
        <div className="rail-card"><div className="rail-title">{tr("Trending tickers · 7d")}</div>
          {t.data.symbols.map((s: AnyObj) => (
            <Link key={s.symbol} to={`/finstagram/s/${encodeURIComponent(s.symbol)}`} className="rail-row">
              <span className="mono">${s.symbol}</span><Change pct={s.change_pct} /><span className="xs muted">{s.posts} posts</span>
            </Link>
          ))}
        </div>
      )}
      {t.data.tags.length > 0 && (
        <div className="rail-card"><div className="rail-title">{tr("Hashtags")}</div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{t.data.tags.map((x: AnyObj) => <Link key={x.tag} className="chip" to={`/finstagram/t/${encodeURIComponent(x.tag)}`}>#{x.tag}</Link>)}</div>
        </div>
      )}
      <div className="xs muted" style={{ padding: "0 4px" }}>News pages are automated and link to the original publishers. Posts are opinions, not investment advice.</div>
    </aside>
  );
}

// ------------------------------------------------------------------ feeds & pages

function FeedList({ params, gate, me }: { params: Record<string, string>; gate: Gate; me: AnyObj | null }) {
  const q = useInfiniteQuery({
    queryKey: ["social", "feed", params],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/social/feed", { ...params, before: pageParam ?? undefined }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting && q.hasNextPage && !q.isFetchingNextPage) void q.fetchNextPage(); }, { rootMargin: "800px" });
    io.observe(el);
    return () => io.disconnect();
  }, [q]);
  if (q.isLoading) return <div className="feed">{[0, 1, 2].map((i) => <div key={i} className="post skeleton-post"><div className="sk-line" /><div className="sk-media" /><div className="sk-line short" /></div>)}</div>;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const note = q.data?.pages[0]?.note;
  if (!items.length) return <div className="insta-empty">{note ?? "Nothing here yet."}</div>;
  return (
    <div className="feed">
      {items.map((p: AnyObj) => <PostCard key={p.id} post={p} gate={gate} me={me} />)}
      <div ref={sentinel} />
      {q.isFetchingNextPage && <Loading label="Loading more" />}
      {!q.hasNextPage && items.length > 5 && <div className="insta-empty small">You're all caught up ✓</div>}
    </div>
  );
}

function TopBar() {
  const { me } = useGate();
  return (
    <div className="insta-top">
      <Link to="/finstagram" className="insta-logo">Finstagram</Link>
      <SearchBar />
      {me && <Link to={`/finstagram/u/${me.username}`} aria-label="My profile"><Avatar user={me} size={30} /></Link>}
    </div>
  );
}

export default function Finstagram() {
  const { t: tr } = useT();
  const [sp, setSp] = useSearchParams();
  const qc = useQueryClient();
  const { me, gate } = useGate();
  const nav = useNavigate();
  const mode = (sp.get("mode") as "latest" | "following" | "trending" | "saved") || "latest";
  const compose = sp.get("compose") ?? undefined;
  const legacySymbol = sp.get("symbol"), legacyTag = sp.get("tag");
  useEffect(() => {
    if (legacySymbol) nav(`/finstagram/s/${encodeURIComponent(legacySymbol)}`, { replace: true });
    else if (legacyTag) nav(`/finstagram/t/${encodeURIComponent(legacyTag)}`, { replace: true });
  }, [legacySymbol, legacyTag, nav]);
  const tabs = [["latest", "For you"], ["following", "Following"], ["trending", "Trending"], ["saved", "Saved"]] as const;
  return (
    <div className="insta">
      <div className="insta-main">
        <TopBar />
        <div className="insta-tabs" role="tablist">
          {tabs.map(([m, label]) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => setSp(m === "latest" ? {} : { mode: m })}>{tr(label)}</button>
          ))}
        </div>
        {me ? <Composer me={me} initial={compose} onPosted={() => qc.invalidateQueries({ queryKey: ["social"] })} />
          : <button className="composer ghost" onClick={() => nav("/login?mode=signup&next=/finstagram")}><span className="text2">{tr("Join Finstagram to post ideas and charts, save posts, follow stocks and news pages, and tune your feed.")}</span></button>}
        <FeedList params={{ mode }} gate={gate} me={me} />
      </div>
      <Rail />
    </div>
  );
}

export function TopicPage({ kind }: { kind: "symbol" | "tag" }) {
  const params = useParams();
  const value = decodeURIComponent((kind === "symbol" ? params.symbol : params.tag) ?? "");
  const qc = useQueryClient();
  const { me, gate } = useGate();
  const t = useQuery({ queryKey: ["social", "topic", kind, value], queryFn: () => api.get<AnyObj>(`/social/topics/${kind}/${encodeURIComponent(value)}`) });
  const follow = () => gate(`Sign in to follow ${kind === "symbol" ? "stocks" : "hashtags"}`, async () => {
    try { await api.post("/social/topics/follow", { kind, value }); qc.invalidateQueries({ queryKey: ["social"] }); } catch (e) { toast("error", "Follow failed", errorMessage(e)); }
  });
  const inst = t.data?.instrument;
  return (
    <div className="insta">
      <div className="insta-main">
        <TopBar />
        <div className="topic-head">
          {kind === "symbol" ? (
            <>
              <div className="topic-icon">{(inst?.symbol ?? value).replace(/\..*$/, "").slice(0, 4)}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <h2 style={{ margin: 0 }}>{inst?.name ?? value}</h2>
                <div className="small text2">{value}{inst?.exchange ? ` · ${inst.exchange}` : ""}{inst?.sector ? ` · ${inst.sector}` : ""}</div>
                {inst?.quote && (
                  <div className="topic-price"><span className="num">{fmtPrice(inst.quote.price, inst.currency)}</span> <Change pct={inst.quote.change_pct} /></div>
                )}
                {inst?.analysts?.count > 0 && (
                  <div className="small text2">Analysts: <b>{(inst.analysts.recommendation ?? "").replace("_", " ")}</b> ({inst.analysts.count}) · mean target {fmtPrice(inst.analysts.target_mean)}</div>
                )}
                {inst?.summary && <p className="small" style={{ marginTop: 8, lineHeight: 1.5 }}>{inst.summary}{inst.summary.length >= 400 ? "…" : ""}</p>}
                <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <button className={`btn sm ${t.data?.following ? "" : "primary"}`} onClick={follow}>{t.data?.following ? "Following" : "Follow"}</button>
                  <Link className="btn sm" to={`/markets/${encodeURIComponent(value)}`}>Full details & chart</Link>
                  <Link className="btn sm" to={`/advisor?q=${encodeURIComponent(`What do you think about ${inst?.name ?? value} (${value}) right now?`)}`}>Ask the AI advisor</Link>
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="topic-icon">#</div>
              <div style={{ flex: 1 }}>
                <h2 style={{ margin: 0 }}>#{value}</h2>
                <button className={`btn sm ${t.data?.following ? "" : "primary"}`} style={{ marginTop: 10 }} onClick={follow}>{t.data?.following ? "Following" : "Follow"}</button>
              </div>
            </>
          )}
        </div>
        {me && <Composer me={me} initial={kind === "symbol" ? `$${value} ` : `#${value} `} onPosted={() => qc.invalidateQueries({ queryKey: ["social"] })} />}
        <FeedList params={kind === "symbol" ? { symbol: value } : { tag: value }} gate={gate} me={me} />
      </div>
      <Rail />
    </div>
  );
}

export function PostPage() {
  const { id = "" } = useParams();
  const { me, gate } = useGate();
  const q = useQuery({ queryKey: ["social", "post", id], queryFn: () => api.get<AnyObj>(`/social/posts/${id}`) });
  return (
    <div className="insta">
      <div className="insta-main">
        <TopBar />
        {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : <PostCard post={q.data!} gate={gate} me={me} openComments />}
      </div>
      <Rail />
    </div>
  );
}

function EditProfile({ me, onClose }: { me: AnyObj; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ display_name: me.display_name ?? "", bio: me.bio ?? "", links: { ...(me.links ?? {}) } as Record<string, string> });
  const [busy, setBusy] = useState(false);
  const avatar = useRef<HTMLInputElement>(null);
  const save = async () => {
    setBusy(true);
    try { await api.patch("/auth/me", f); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["social"] }); onClose(); }
    catch (e) { toast("error", "Could not save", errorMessage(e)); } finally { setBusy(false); }
  };
  const upload = async (file?: File) => {
    if (!file) return;
    const form = new FormData();
    form.set("image", await shrink(file), "avatar.jpg");
    try { await api.upload("/auth/me/avatar", form); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["social"] }); toast("success", "Photo updated"); }
    catch (e) { toast("error", "Upload failed", errorMessage(e)); }
  };
  const removeAccount = async () => {
    const pw = window.prompt(me.auth_provider && me.auth_provider !== "password"
      ? "This permanently deletes your account, posts, photos, comments and likes. Type DELETE to confirm:"
      : "This permanently deletes your account, posts, photos, comments and likes. Enter your password to confirm:");
    if (!pw) return;
    try {
      await api.post("/auth/me/delete", { password: pw });
      qc.invalidateQueries({ queryKey: ["me"] });
      toast("success", "Account deleted");
      window.location.assign("/finstagram");
    } catch (e) { toast("error", "Could not delete the account", errorMessage(e)); }
  };
  return (
    <div className="modal-scrim" onMouseDown={onClose}>
      <div className="modal popover-in" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head"><b>Edit profile</b><button className="icon-btn" aria-label="Close" onClick={onClose}>✕</button></div>
        <div className="row" style={{ gap: 12, marginBottom: 10 }}>
          <Avatar user={me} size={56} />
          <input ref={avatar} type="file" accept="image/*" hidden onChange={(e) => void upload(e.target.files?.[0])} />
          <button className="btn sm" onClick={() => avatar.current?.click()}>Change photo</button>
        </div>
        <label className="fld"><span>Display name</span><input className="input" value={f.display_name} maxLength={60} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></label>
        <label className="fld"><span>Bio</span><textarea className="input" rows={3} maxLength={300} dir="auto" value={f.bio} onChange={(e) => setF({ ...f, bio: e.target.value })} /></label>
        <div className="small text2" style={{ margin: "8px 0 4px" }}>Your other profiles (shown as shortcuts on your page)</div>
        <div className="form-grid">
          {Object.entries(SOCIAL_LINKS).map(([k, s]) => (
            <label key={k} className="fld"><span>{s.label}</span>
              <input className="input" placeholder={k === "website" ? "https://…" : "handle"} value={f.links[k] ?? ""} onChange={(e) => setF({ ...f, links: { ...f.links, [k]: e.target.value } })} /></label>
          ))}
        </div>
        <button className="btn primary block" style={{ marginTop: 12 }} disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        <button className="link-btn xs neg" style={{ marginTop: 12 }} onClick={removeAccount}>Delete my account and all my posts</button>
      </div>
    </div>
  );
}

export function ProfilePage() {
  const { username = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { me, gate } = useGate();
  const [edit, setEdit] = useState(false);
  const [view, setView] = useState<"grid" | "list">("grid");
  const prof = useQuery({ queryKey: ["social", "profile", username], queryFn: () => api.get<AnyObj>(`/social/users/${username}`) });
  const posts = useQuery({ queryKey: ["social", "user-posts", username], queryFn: () => api.get<AnyObj>("/social/feed", { user: username }) });
  const follow = () => gate("Sign in to follow pages and people", async () => {
    try { await api.post(`/social/users/${username}/follow`); qc.invalidateQueries({ queryKey: ["social"] }); } catch (e) { toast("error", "Follow failed", errorMessage(e)); }
  });
  const logout = async () => { await api.post("/auth/logout"); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["social"] }); nav("/finstagram"); };
  if (prof.isLoading) return <Loading />;
  if (prof.error) return <ErrorState error={prof.error} />;
  const u = prof.data!;
  const items: AnyObj[] = posts.data?.items ?? [];
  let extra: ReactNode = null;
  if (Object.keys(u.links ?? {}).length > 0 || u.linked?.length) {
    extra = (
      <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        {(u.linked as AnyObj[] ?? []).map((a) => (
          <a key={`v-${a.provider}`} className="chip" href={a.url} target="_blank" rel="noreferrer noopener me" title="Linked by signing in — verified">
            <span className={`src-mark ${a.provider}`} aria-hidden style={{ width: 14, height: 14, fontSize: 8, marginInlineEnd: 5 }}>{a.provider === "x" ? "𝕏" : "r/"}</span>
            {a.provider === "x" ? "@" : "u/"}{a.username} · verified ↗
          </a>
        ))}
        {Object.entries(u.links as Record<string, string>).map(([k, h]) => SOCIAL_LINKS[k] && (
          <a key={k} className="chip" href={SOCIAL_LINKS[k].url(h)} target="_blank" rel="noreferrer noopener me">{SOCIAL_LINKS[k].label}{k !== "website" ? ` · @${h}` : ""} ↗</a>
        ))}
      </div>
    );
  }
  return (
    <div className="insta">
      <div className="insta-main">
        <TopBar />
        <div className="profile">
          <Avatar user={u} size={88} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0 }}>{u.username}</h2>
              {u.kind === "page" && <span className="page-badge">✓ News page</span>}
              {u.is_me ? <><button className="btn sm" onClick={() => setEdit(true)}>Edit profile</button><button className="btn sm" onClick={logout}>Sign out</button></>
                : <button className={`btn sm ${u.followed_by_me ? "" : "primary"}`} onClick={follow}>{u.followed_by_me ? "Following" : "Follow"}</button>}
            </div>
            <div className="profile-counts"><span><b>{u.posts}</b> posts</span><span><b>{u.following}</b> following</span></div>
            <div><b>{u.display_name}</b></div>
            {u.bio && <div className="small" dir="auto" style={{ whiteSpace: "pre-wrap" }}><Rich text={u.bio} /></div>}
            {extra}
          </div>
        </div>
        <div className="insta-tabs"><button className={view === "grid" ? "on" : ""} onClick={() => setView("grid")}>Grid</button><button className={view === "list" ? "on" : ""} onClick={() => setView("list")}>Posts</button></div>
        {items.length === 0 ? <div className="insta-empty">No posts yet.</div> : view === "grid" ? (
          <div className="insta-grid">
            {items.map((p) => {
              const vid = ytId(p.link?.url);
              const img = p.image_url ?? (vid ? `https://i.ytimg.com/vi/${vid}/hqdefault.jpg` : p.link?.image);
              return (
                <Link key={p.id} to={`/finstagram/p/${p.id}`} className="grid-cell">
                  {img ? <img src={img} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <div className="grid-text" dir="auto" style={p.link ? { background: gradientFor(p.link.source ?? ""), color: "#fff" } : undefined}>{(p.link?.title ?? p.body).slice(0, 120)}</div>}
                  {vid && <span className="grid-video" aria-hidden>▶</span>}
                  <span className="grid-hover">♥ {p.like_count} · 💬 {p.comment_count}</span>
                </Link>
              );
            })}
          </div>
        ) : <div className="feed">{items.map((p) => <PostCard key={p.id} post={p} gate={gate} me={me} />)}</div>}
      </div>
      <Rail />
      {edit && me && <EditProfile me={{ ...me, ...u }} onClose={() => setEdit(false)} />}
    </div>
  );
}
