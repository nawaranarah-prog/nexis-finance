import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Change, AuthDialog, Avatar, fmtPrice, useMe } from "../components/market";
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
      if (/^\$[A-Za-z]/.test(p)) return <Link key={i} className="cashtag" to={`/markets/${encodeURIComponent(p.slice(1).toUpperCase())}`}>{p.toUpperCase()}</Link>;
      if (p.startsWith("#") && p.length > 2) return <Link key={i} className="hashtag" to={`/social?tag=${encodeURIComponent(p.slice(1))}`}>{p}</Link>;
      if (p.startsWith("@") && p.length > 3) return <Link key={i} className="mention" to={`/social/u/${p.slice(1)}`}>{p}</Link>;
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

const SOCIAL_LINKS: Record<string, { label: string; url: (h: string) => string }> = {
  instagram: { label: "Instagram", url: (h) => `https://www.instagram.com/${h}` },
  x: { label: "X", url: (h) => `https://x.com/${h}` },
  linkedin: { label: "LinkedIn", url: (h) => `https://www.linkedin.com/in/${h}` },
  tiktok: { label: "TikTok", url: (h) => `https://www.tiktok.com/@${h}` },
  youtube: { label: "YouTube", url: (h) => `https://www.youtube.com/@${h}` },
  website: { label: "Website", url: (h) => h },
};

function useAuthGate() {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<ReactNode>(null);
  const gate = (why: string, fn: () => void) => {
    if (me.data?.user) fn();
    else { setReason(why); setOpen(true); }
  };
  const dialog = <AuthDialog open={open} onClose={() => setOpen(false)} initial="register" reason={reason} />;
  return { me: me.data?.user ?? null, gate, dialog, openAuth: () => { setReason(null); setOpen(true); } };
}

// ------------------------------------------------------------------ share

function ShareMenu({ post, onClose }: { post: AnyObj; onClose: () => void }) {
  const url = `${window.location.origin}/social/p/${post.id}`;
  const text = `${post.author.display_name} on InstaFin: ${post.body.slice(0, 180)}`;
  const e = encodeURIComponent;
  const copy = async (then?: string) => {
    try { await navigator.clipboard.writeText(url); toast("success", "Link copied", then); } catch { toast("warning", "Copy failed", url); }
  };
  const native = async () => {
    try {
      const data: ShareData = { title: "InstaFin", text, url };
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
    { label: "Email", href: `mailto:?subject=${e("From InstaFin")}&body=${e(`${text}\n\n${url}`)}` },
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

function Comments({ post, me, gate }: { post: AnyObj; me: AnyObj | null; gate: (why: string, fn: () => void) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["social", "comments", post.id], queryFn: () => api.get<AnyObj[]>(`/social/posts/${post.id}/comments`) });
  const [text, setText] = useState("");
  const send = async () => {
    if (!text.trim()) return;
    try {
      await api.post(`/social/posts/${post.id}/comments`, { body: text });
      setText("");
      qc.invalidateQueries({ queryKey: ["social"] });
    } catch (e) { toast("error", "Comment failed", errorMessage(e)); }
  };
  return (
    <div className="comments">
      {q.data?.map((c) => (
        <div key={c.id} className="comment">
          <Link to={`/social/u/${c.author.username}`}><b>{c.author.username}</b></Link> <span dir="auto"><Rich text={c.body} /></span>
          <span className="xs muted"> · {ago(c.created_at)}</span>
          {(c.is_mine || post.is_mine) && <button className="link-btn xs" onClick={async () => { await api.del(`/social/comments/${c.id}`); qc.invalidateQueries({ queryKey: ["social"] }); }}>delete</button>}
        </div>
      ))}
      <form className="comment-form" onSubmit={(e) => { e.preventDefault(); gate("Sign in to comment.", () => void send()); }}>
        <input className="input" dir="auto" placeholder={me ? "Add a comment…" : "Sign in to comment"} value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} onFocus={() => !me && gate("Sign in to comment.", () => undefined)} />
        <button className="link-btn" disabled={!text.trim()}>Post</button>
      </form>
    </div>
  );
}

export function PostCard({ post, gate, me, openComments = false }: { post: AnyObj; gate: (why: string, fn: () => void) => void; me: AnyObj | null; openComments?: boolean }) {
  const qc = useQueryClient();
  const [liked, setLiked] = useState<boolean>(post.liked_by_me);
  const [likes, setLikes] = useState<number>(post.like_count);
  const [pop, setPop] = useState(false);
  const [showComments, setShowComments] = useState(openComments);
  const [share, setShare] = useState(false);
  const [more, setMore] = useState(false);
  useEffect(() => { setLiked(post.liked_by_me); setLikes(post.like_count); }, [post.liked_by_me, post.like_count]);
  const like = () => gate("Sign in to like posts.", async () => {
    setLiked(!liked); setLikes(likes + (liked ? -1 : 1));
    if (!liked) { setPop(true); window.setTimeout(() => setPop(false), 450); }
    try { const r = await api.post<AnyObj>(`/social/posts/${post.id}/like`); setLiked(r.liked); setLikes(r.like_count); }
    catch (e) { setLiked(liked); setLikes(likes); toast("error", "Like failed", errorMessage(e)); }
  });
  const remove = async () => {
    if (!window.confirm("Delete this post?")) return;
    try { await api.del(`/social/posts/${post.id}`); qc.invalidateQueries({ queryKey: ["social"] }); toast("success", "Post deleted"); } catch (e) { toast("error", "Delete failed", errorMessage(e)); }
  };
  const report = () => gate("Sign in to report posts.", async () => {
    try { const r = await api.post<AnyObj>(`/social/posts/${post.id}/report`, { reason: "reported from feed" }); toast("info", "Thanks — reported", r.hidden ? "The post has been hidden." : undefined); } catch (e) { toast("error", "Report failed", errorMessage(e)); }
    setMore(false);
  });
  return (
    <article className="post">
      <header className="post-head">
        <Link to={`/social/u/${post.author.username}`} className="row" style={{ gap: 10 }}>
          <Avatar user={post.author} size={34} />
          <span><b>{post.author.display_name}</b> <span className="xs muted">@{post.author.username} · {ago(post.created_at)}</span></span>
        </Link>
        <div className="post-more">
          <button className="icon-btn" aria-label="More" onClick={() => setMore(!more)}>⋯</button>
          {more && (
            <div className="share-menu popover-in" role="menu" onMouseLeave={() => setMore(false)}>
              <Link role="menuitem" to={`/social/p/${post.id}`}>Open post</Link>
              {post.is_mine ? <button role="menuitem" className="neg" onClick={remove}>Delete</button> : <button role="menuitem" onClick={report}>Report</button>}
            </div>
          )}
        </div>
      </header>
      {post.image_url && (
        <div className="post-media" onDoubleClick={() => !liked && like()}>
          <img src={post.image_url} alt="" loading="lazy" />
          {pop && <span className="heart-pop" aria-hidden>♥</span>}
        </div>
      )}
      <div className="post-actions">
        <button className={`act ${liked ? "liked" : ""}`} aria-pressed={liked} aria-label="Like" onClick={like}><span className={pop ? "beat" : ""}>{liked ? "♥" : "♡"}</span></button>
        <button className="act" aria-label="Comments" onClick={() => setShowComments(!showComments)}>💬</button>
        <div className="post-more">
          <button className="act" aria-label="Share" onClick={() => setShare(!share)}>↗</button>
          {share && <ShareMenu post={post} onClose={() => setShare(false)} />}
        </div>
        <span className="small text2" style={{ marginLeft: "auto" }}>{likes} {likes === 1 ? "like" : "likes"} · {post.comment_count} {post.comment_count === 1 ? "comment" : "comments"}</span>
      </div>
      {post.body && <div className="post-body" dir="auto"><Rich text={post.body} /></div>}
      {post.symbols?.length > 0 && (
        <div className="ticker-cards">
          {post.symbols.map((s: AnyObj) => (
            <Link key={s.symbol} className="ticker-card" to={`/markets/${encodeURIComponent(s.symbol)}`}>
              <span className="mono">{s.symbol}</span>
              <span className="num">{fmtPrice(s.price, s.currency)}</span>
              <Change pct={s.change_pct} />
            </Link>
          ))}
        </div>
      )}
      {showComments && <Comments post={post} me={me} gate={gate} />}
    </article>
  );
}

// ------------------------------------------------------------------ composer

function Composer({ me, initial, onPosted }: { me: AnyObj; initial?: string; onPosted: () => void }) {
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
        <textarea className="input" dir="auto" rows={3} maxLength={2200} placeholder="Share an idea, a chart or a trade thesis… use $EMAAR.AE and #tags" value={text} onChange={(e) => setText(e.target.value)} />
        {preview && <div className="composer-preview"><img src={preview} alt="" /><button className="icon-btn" aria-label="Remove photo" onClick={() => { setFile(null); setPreview(null); }}>✕</button></div>}
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
          <div className="row" style={{ gap: 6 }}>
            <input ref={input} type="file" accept="image/*" hidden onChange={(e) => void pick(e.target.files?.[0])} />
            <button className="btn sm" onClick={() => input.current?.click()}>📷 Photo</button>
            <span className="xs muted">{text.length}/2200</span>
          </div>
          <button className="btn primary sm" disabled={busy || (!text.trim() && !file)} onClick={submit}>{busy ? "Posting…" : "Post"}</button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ side rail

function Rail() {
  const t = useQuery({ queryKey: ["social", "trending"], queryFn: () => api.get<AnyObj>("/social/trending"), refetchInterval: 120_000 });
  if (!t.data) return null;
  return (
    <aside className="insta-rail">
      {t.data.symbols.length > 0 && (
        <div className="rail-card"><div className="rail-title">Trending tickers · 7d</div>
          {t.data.symbols.map((s: AnyObj) => (
            <Link key={s.symbol} to={`/social?symbol=${encodeURIComponent(s.symbol)}`} className="rail-row">
              <span className="mono">${s.symbol}</span><Change pct={s.change_pct} /><span className="xs muted">{s.posts} posts</span>
            </Link>
          ))}
        </div>
      )}
      {t.data.tags.length > 0 && (
        <div className="rail-card"><div className="rail-title">Hashtags</div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{t.data.tags.map((x: AnyObj) => <Link key={x.tag} className="chip" to={`/social?tag=${encodeURIComponent(x.tag)}`}>#{x.tag}</Link>)}</div>
        </div>
      )}
      {t.data.people.length > 0 && (
        <div className="rail-card"><div className="rail-title">New on InstaFin</div>
          {t.data.people.map((u: AnyObj) => <Link key={u.id} to={`/social/u/${u.username}`} className="rail-row"><Avatar user={u} size={26} /><span>{u.display_name}</span><span className="xs muted">@{u.username}</span></Link>)}
        </div>
      )}
      <div className="xs muted" style={{ padding: "0 4px" }}>Posts are opinions of their authors, not investment advice. Report anything abusive.</div>
    </aside>
  );
}

// ------------------------------------------------------------------ pages

function FeedList({ params, gate, me }: { params: Record<string, string>; gate: (why: string, fn: () => void) => void; me: AnyObj | null }) {
  const q = useInfiniteQuery({
    queryKey: ["social", "feed", params],
    queryFn: ({ pageParam }) => api.get<AnyObj>("/social/feed", { ...params, before: pageParam ?? undefined }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });
  if (q.isLoading) return <Loading label="Loading feed" />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const note = q.data?.pages[0]?.note;
  if (!items.length) return <div className="insta-empty">{note ?? "Nothing here yet — be the first to post."}</div>;
  return (
    <div className="feed">
      {items.map((p: AnyObj) => <PostCard key={p.id} post={p} gate={gate} me={me} />)}
      {q.hasNextPage && <button className="btn block" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? "Loading…" : "Load more"}</button>}
    </div>
  );
}

export default function InstaFin() {
  const [sp, setSp] = useSearchParams();
  const qc = useQueryClient();
  const { me, gate, dialog, openAuth } = useAuthGate();
  const mode = (sp.get("mode") as "latest" | "following" | "trending") || "latest";
  const symbol = sp.get("symbol");
  const tag = sp.get("tag");
  const compose = sp.get("compose") ?? undefined;
  const params: Record<string, string> = symbol ? { symbol } : tag ? { tag } : { mode };
  return (
    <div className="insta">
      <div className="insta-main">
        <div className="insta-top">
          <div className="insta-logo">InstaFin</div>
          {me ? (
            <Link to={`/social/u/${me.username}`} className="row" style={{ gap: 8 }}><Avatar user={me} size={28} /><span className="small">@{me.username}</span></Link>
          ) : <button className="btn primary sm" onClick={openAuth}>Sign in / Join</button>}
        </div>
        {(symbol || tag) ? (
          <div className="insta-filter">
            <span>{symbol ? <>Posts about <Link to={`/markets/${encodeURIComponent(symbol)}`} className="cashtag">${symbol.toUpperCase()}</Link></> : <>#{tag}</>}</span>
            <button className="link-btn" onClick={() => setSp({})}>clear</button>
          </div>
        ) : (
          <div className="insta-tabs" role="tablist">
            {(["latest", "following", "trending"] as const).map((m) => (
              <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => setSp(m === "latest" ? {} : { mode: m })}>{m === "latest" ? "For you" : m[0].toUpperCase() + m.slice(1)}</button>
            ))}
          </div>
        )}
        {me ? <Composer me={me} initial={compose ?? (symbol ? `$${symbol.toUpperCase()} ` : tag ? `#${tag} ` : "")} onPosted={() => qc.invalidateQueries({ queryKey: ["social"] })} />
          : <button className="composer ghost" onClick={openAuth}><span className="text2">Join InstaFin to post ideas, charts and trades — like, comment and follow other investors.</span></button>}
        <FeedList params={params} gate={gate} me={me} />
      </div>
      <Rail />
      {dialog}
    </div>
  );
}

export function PostPage() {
  const { id = "" } = useParams();
  const { me, gate, dialog } = useAuthGate();
  const q = useQuery({ queryKey: ["social", "post", id], queryFn: () => api.get<AnyObj>(`/social/posts/${id}`) });
  return (
    <div className="insta">
      <div className="insta-main">
        <div className="insta-top"><Link to="/social" className="insta-logo">InstaFin</Link></div>
        {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : <PostCard post={q.data!} gate={gate} me={me} openComments />}
      </div>
      <Rail />
      {dialog}
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
  const removeAccount = async () => {
    const pw = window.prompt("This permanently deletes your account, posts, photos, comments and likes. Enter your password to confirm:");
    if (!pw) return;
    try {
      await api.post("/auth/me/delete", { password: pw });
      qc.invalidateQueries({ queryKey: ["me"] });
      qc.invalidateQueries({ queryKey: ["social"] });
      toast("success", "Account deleted");
      window.location.assign("/social");
    } catch (e) { toast("error", "Could not delete the account", errorMessage(e)); }
  };
  const upload = async (file?: File) => {
    if (!file) return;
    const form = new FormData();
    form.set("image", await shrink(file), "avatar.jpg");
    try { await api.upload("/auth/me/avatar", form); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["social"] }); toast("success", "Photo updated"); }
    catch (e) { toast("error", "Upload failed", errorMessage(e)); }
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
  const { me, gate, dialog } = useAuthGate();
  const [edit, setEdit] = useState(false);
  const [view, setView] = useState<"grid" | "list">("grid");
  const prof = useQuery({ queryKey: ["social", "profile", username], queryFn: () => api.get<AnyObj>(`/social/users/${username}`) });
  const posts = useQuery({ queryKey: ["social", "user-posts", username], queryFn: () => api.get<AnyObj>("/social/feed", { user: username }) });
  const follow = () => gate("Sign in to follow people.", async () => {
    try { await api.post(`/social/users/${username}/follow`); qc.invalidateQueries({ queryKey: ["social"] }); } catch (e) { toast("error", "Follow failed", errorMessage(e)); }
  });
  const logout = async () => { await api.post("/auth/logout"); qc.invalidateQueries({ queryKey: ["me"] }); qc.invalidateQueries({ queryKey: ["social"] }); nav("/social"); };
  if (prof.isLoading) return <Loading />;
  if (prof.error) return <ErrorState error={prof.error} />;
  const u = prof.data!;
  const items: AnyObj[] = posts.data?.items ?? [];
  return (
    <div className="insta">
      <div className="insta-main">
        <div className="insta-top"><Link to="/social" className="insta-logo">InstaFin</Link></div>
        <div className="profile">
          <Avatar user={u} size={88} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0 }}>@{u.username}</h2>
              {u.is_me ? <><button className="btn sm" onClick={() => setEdit(true)}>Edit profile</button><button className="btn sm" onClick={logout}>Sign out</button></>
                : <button className={`btn sm ${u.followed_by_me ? "" : "primary"}`} onClick={follow}>{u.followed_by_me ? "Following" : "Follow"}</button>}
            </div>
            <div className="profile-counts"><span><b>{u.posts}</b> posts</span><span><b>{u.followers}</b> followers</span><span><b>{u.following}</b> following</span></div>
            <div><b>{u.display_name}</b></div>
            {u.bio && <div className="small" dir="auto" style={{ whiteSpace: "pre-wrap" }}><Rich text={u.bio} /></div>}
            {Object.keys(u.links ?? {}).length > 0 && (
              <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                {Object.entries(u.links as Record<string, string>).map(([k, h]) => SOCIAL_LINKS[k] && (
                  <a key={k} className="chip" href={SOCIAL_LINKS[k].url(h)} target="_blank" rel="noreferrer noopener me">{SOCIAL_LINKS[k].label}{k !== "website" ? ` · @${h}` : ""} ↗</a>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="insta-tabs"><button className={view === "grid" ? "on" : ""} onClick={() => setView("grid")}>Grid</button><button className={view === "list" ? "on" : ""} onClick={() => setView("list")}>Posts</button></div>
        {items.length === 0 ? <div className="insta-empty">No posts yet.</div> : view === "grid" ? (
          <div className="insta-grid">
            {items.map((p) => (
              <Link key={p.id} to={`/social/p/${p.id}`} className="grid-cell">
                {p.image_url ? <img src={p.image_url} alt="" loading="lazy" /> : <div className="grid-text" dir="auto">{p.body.slice(0, 120)}</div>}
                <span className="grid-hover">♥ {p.like_count} · 💬 {p.comment_count}</span>
              </Link>
            ))}
          </div>
        ) : <div className="feed">{items.map((p) => <PostCard key={p.id} post={p} gate={gate} me={me} />)}</div>}
      </div>
      <Rail />
      {edit && me && <EditProfile me={{ ...me, ...u }} onClose={() => setEdit(false)} />}
      {dialog}
    </div>
  );
}
