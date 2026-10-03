"""Nexis Pulse discussion engine: real events → relevant personas → distinct views → a natural thread.

Pipeline per event:

1. a real ``SourceEvent`` (news article, market move, Nexis Research note) with verified facts;
2. assets, sectors and topics from the event;
3. 4–6 personas relevant to it, with different archetypes (``personas.pick``);
4. one model call for each persona's independent perspective (stance, angle, doubt), using their memory;
5. a second call that turns those perspectives into a thread — opener, disagreement, questions and answers,
   partial concessions, short replies;
6. quality checks: unknown personas, stock AI phrasing, near-duplicates and any number that does not appear in
   the facts are removed; threads that end up too thin are skipped;
7. publish as ``source = "generated"`` posts and ``generated = True`` comments, linked to the event.

Generation is bounded by ``pulse_daily_threads`` and throttled by ``pulse_tick_minutes``. Without a configured
model nothing is generated — Pulse never fills itself with invented content.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NexisError
from app.db.base import utcnow
from app.models import Comment, MarketCache, Persona, Post, PostTopic, SourceEvent, User
from app.services import events, llm, markets, personas, pulse

SOURCE = "generated"
STATE_KEY = "pulse-engine:state"
BANNED = re.compile(
    r"\b(as an ai|as a language model|according to my analysis|based on the available data|i believe the market may|"
    r"great point|good point|fair point|in conclusion|it is important to note|in today's rapidly|double-edged sword|"
    r"only time will tell|line in the sand|traffic jam|\b(?:is|acts|works|feels) like an? \w+'s|"
    r"\b(?:is|acts|works) like an? (?:engine|fuel|wall|anchor|balloon|shop|leak|rollercoaster)\b|^exactly\b|^i agree\b|"
    r"as a (long-term |dividend |value |growth |macro |"
    r"quant |technical |risk-focused |passive |beginner )?investor,)|[\U0001F300-\U0001FAFF]|#\w",
    re.I,
)
OFF_TOPIC = re.compile(
    r"\b(smuggl\w*|arrest\w*|police|murder\w*|killed|celebrit\w*|recipe|weather|football|cricket|wedding|horoscope|lottery)\b",
    re.I,
)
NUMBER = re.compile(r"(?<![A-Za-z])\$?\d[\d,]*(?:\.\d+)?%?")


# ------------------------------------------------------------------ state


def _state(db: Session) -> dict[str, Any]:
    row = db.get(MarketCache, STATE_KEY)
    return dict(row.payload.get("v", {})) if row else {}


def _save_state(db: Session, **kw: Any) -> None:
    row = db.get(MarketCache, STATE_KEY)
    v = {**(_state(db)), **kw}
    if row is None:
        db.add(MarketCache(key=STATE_KEY, payload={"v": v}, fetched_at=utcnow()))
    else:
        row.payload, row.fetched_at = {"v": v}, utcnow()
    db.commit()


def generated_today(db: Session) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(select(func.count(Post.id)).where(Post.source == SOURCE, Post.created_at >= start - timedelta(hours=12))) or 0
    )


def status(db: Session) -> dict[str, Any]:
    s = get_settings()
    st = _state(db)
    llm_status = llm.status()
    return {
        "model_configured": bool(llm_status.get("configured")),
        "models": [m.strip() for m in s.pulse_model.split(",") if m.strip()],
        "daily_limit": s.pulse_daily_threads,
        "generated_last_24h": generated_today(db),
        "personas": db.scalar(select(func.count(Persona.id)).where(Persona.active.is_(True))) or 0,
        "last_run": st.get("last_run"),
        "last_result": st.get("last_result"),
        "last_error": st.get("last_error"),
        "providers": [
            {"key": p.key, "label": p.label, "kind": p.kind, "connected": p.connected, "note": p.note}
            for p in events.PROVIDERS.values()
        ],
    }


# ------------------------------------------------------------------ model calls


def _models() -> list[str]:
    return [m.strip() for m in get_settings().pulse_model.split(",") if m.strip()]


def _ask(system: str, user: str, max_tokens: int) -> dict[str, Any]:
    msg = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=max_tokens,
                   temperature=0.85, models=_models())  # fmt: skip
    m = re.search(r"\{.*\}", msg.get("content") or "", re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        out = None
    if not isinstance(out, dict):
        raise NexisError("the model returned something that wasn't a thread")
    out["_model"] = msg.get("_model")
    return out


SYSTEM = (
    "You write discussions for Nexis Pulse, a financial discussion platform. The participants are fictional "
    "Nexis-generated investor personas; readers are told they are generated. Ground everything in the FACTS given: "
    "never invent prices, figures, dates, quotes, filings, analyst views or events, and never contradict the facts. "
    "If something isn't in the facts, a persona can say they don't know it or what they'd want to see. Personas give "
    "opinions and reasoning, never instructions to readers to buy or sell. Output valid JSON only."
)


def _facts_text(event: SourceEvent, names: dict[str, str], extra: list[str]) -> str:
    f = event.facts or {}
    lines = [f"Event type: {event.kind.replace('_', ' ')}", f"Source: {event.publisher or 'unknown publisher'}",
             f"Published: {event.published_at:%d %B %Y}", f"Headline: {f.get('headline') or event.title}"]  # fmt: skip
    if f.get("summary"):
        lines.append(f"Summary from the source: {f['summary']}")
    if event.assets:
        lines.append("Assets: " + ", ".join(f"{a} ({names.get(a, a)})" for a in event.assets))
    lines += extra
    return "\n".join(lines)


def _persona_card(p: Persona, u: User, assets: list[str]) -> str:
    t = p.traits
    mem = {a: (p.memory or {}).get(a) for a in assets if (p.memory or {}).get(a)}
    card = {
        "handle": u.username, "investor_type": t["label"], "experience": t["experience"], "risk": t["risk_tolerance"],
        "lens": t["framework"], "traits": t["traits"], "habit": t["habit"], "bias": t["bias"],
        "writes": f"{t['voice']['length']} — {t['voice']['style']}; {t['vocabulary']}",
    }  # fmt: skip
    if mem:
        card["previously_argued"] = mem
    return json.dumps(card, ensure_ascii=False)


PERSPECTIVES = """FACTS
{facts}

PERSONAS
{cards}

Each persona reacts to these facts independently, from their own lens and history (stay consistent with anything
they previously argued unless the facts give a reason to shift). Make the views genuinely different: a mix of
positive, sceptical and undecided where the facts allow, each with a distinct angle (not the same point reworded).

Return JSON: {{"perspectives": [{{"handle": "...", "stance": "bullish|neutral|bearish", "angle": "a few words",
"argument": "one or two sentences in plain words", "doubt": "what they are unsure about or would want to know"}}],
"title": "a specific discussion title in sentence case (max 100 characters), written the way an investor would
start a thread — a claim or a question about what this means, not a copy or paraphrase of the headline"}}"""

THREAD = """FACTS
{facts}

PERSPECTIVES (private planning notes — do not quote them verbatim)
{perspectives}

VOICES
{voices}

Write the discussion as it would unfold on a forum. The opening post is by @{opener}. Then {n} replies.
Rules for a natural thread:
- replies answer specific earlier messages (reply_to = 0 for the opening post, otherwise the reply's id);
  most replies are not to the opening post — conversations branch and come back;
- include at least one blunt disagreement, one question that someone else answers, one partial concession or
  change of mind, at least two very short replies (under 12 words), and one reply that refers back to an earlier
  point by @handle;
- write like real investors on a forum, not like an assistant: start with the point itself. Do NOT open replies with
  "Exactly", "Good point", "Great point", "Fair point", "True", "Right", "I agree", "That's a good point", or by
  restating what the previous person said; at most two replies may start with an @mention;
- no similes, metaphors or clichés ("slow leak", "choppy waters", "anchor", "double-edged sword", "only time will
  tell"); no hedging boilerplate like "I remain cautious" or "it depends"; be concrete about what in the FACTS drives
  the view;
- each persona keeps the length and style in VOICES; lengths should vary a lot; fragments and lowercase are fine for
  casual voices; people don't introduce themselves, name their investing style, thank each other, or summarise;
- when someone addresses another participant, they use their @handle — never a first name or a word from the handle;
- plain text only: no headings, lists, hashtags, emojis or sign-offs;
- numbers may only be ones that appear in FACTS; otherwise talk qualitatively;
- nobody tells readers what to do; personal views like "I'd want to see…" are fine.

Return JSON: {{"opening": {{"handle": "{opener}", "body": "40-170 words"}},
"replies": [{{"id": 1, "handle": "...", "reply_to": 0, "body": "..."}}]}}"""

FOLLOW_UP = """FACTS
{facts}

THREAD SO FAR (oldest first; ids in brackets)
{thread}

VOICES
{voices}

Continue this discussion with {n} new replies from these personas, numbered from {start}. Pick up loose threads: answer an open question,
push back on something, or reconsider a view in light of what others said. Same rules: reply_to must be an
existing id (0 = the opening post), keep each voice, plain text, numbers only from FACTS, no advice to readers,
no summarising. Return JSON: {{"replies": [{{"id": 1, "handle": "...", "reply_to": 0, "body": "..."}}]}}"""


# ------------------------------------------------------------------ quality checks


def _allowed_numbers(facts: str) -> set[str]:
    return {n.replace(",", "").lstrip("$").rstrip("%") for n in NUMBER.findall(facts)}


YIELD_SYMBOLS = {"^TNX": "US 10-year Treasury yield", "^FVX": "US 5-year Treasury yield", "^TYX": "US 30-year Treasury yield",
                 "^IRX": "US 13-week Treasury bill yield"}  # fmt: skip
_ACK = re.compile(
    r"^(?:@\w[\w.]*[,:]?\s+)?(?:exactly|true|right|agreed|agree with you|i agree|good point|great point|fair point|"
    r"interesting (?:angle|point)|that's (?:a good|an interesting|a valid|a fair) (?:point|angle|concern|question)|"
    r"good question|makes sense|totally|absolutely|yeah|yep|yes)\b[\s,.!:;—-]*",
    re.I,
)


def _quote_fact(symbol: str, q: dict[str, Any]) -> str:
    if symbol in YIELD_SYMBOLS and q.get("change") is not None:
        return f"{YIELD_SYMBOLS[symbol]}: {q['price']:.2f}% (change on the day: {q['change'] * 100:+.1f} basis points; delayed data)"
    return f"Latest delayed quote for {symbol}: {q['price']:,.2f} {q.get('currency') or ''} ({q['change_pct'] * 100:+.2f}% on the day)".strip()


def _clean(body: Any) -> str:
    text = re.sub(r"\s+\n", "\n", str(body or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Drop stock acknowledgements ("Exactly, …", "True, Gulfslow.") so replies start with their point.
    stripped = _ACK.sub("", text, count=1).strip()
    stripped = re.sub(r"^[A-Z][a-z]{2,15}[,.]\s+(?=[A-Za-z])", "", stripped) if stripped != text else stripped
    if stripped and stripped != text and len(stripped) >= 8:
        text = stripped[0].upper() + stripped[1:]
    return text


def _ok(body: str, allowed: set[str]) -> bool:
    if len(body) < 2 or len(body) > 1600 or BANNED.search(body):
        return False
    for n in NUMBER.findall(body):
        v = n.replace(",", "").lstrip("$").rstrip("%")
        if v in allowed:
            continue
        try:
            if float(v) <= 10 and "." not in v and "%" not in n and "$" not in n:
                continue  # small counting words ("2 things", "3 quarters")
        except ValueError:
            pass
        return False
    return True


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z']+", s.lower()))


def _filter_replies(raw: list[Any], handles: dict[str, Persona], allowed: set[str], known_ids: set[int]) -> list[dict[str, Any]]:
    """Drop bad replies; replies to a dropped reply are re-attached to its parent."""
    out: list[dict[str, Any]] = []
    parent_of: dict[int, int] = {}
    seen_text: list[set[str]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        try:
            rid, to = int(r.get("id")), int(r.get("reply_to", 0))
        except (TypeError, ValueError):
            continue
        handle = str(r.get("handle", "")).lstrip("@")
        body = _clean(r.get("body"))
        while to in parent_of:
            to = parent_of[to]
        valid_parent = to == 0 or to in known_ids or any(o["id"] == to for o in out)
        words = _words(body)
        dup = any(len(words & w) / max(1, len(words | w)) > 0.7 for w in seen_text)
        if handle not in handles or not valid_parent or dup or not _ok(body, allowed):
            parent_of[rid] = to if valid_parent else 0
            continue
        out.append({"id": rid, "handle": handle, "reply_to": to, "body": body})
        seen_text.append(words)
    return out


# ------------------------------------------------------------------ generation


def _names(db: Session, assets: list[str]) -> dict[str, dict[str, Any]]:
    if not assets:
        return {}
    try:
        return {q["symbol"]: q for q in markets.quotes(db, assets)}
    except NexisError:
        return {}


def _topics_for(event: SourceEvent) -> list[str]:
    keys = [t for t in (event.topics or []) if t in pulse.TOPICS]
    if not keys:
        keys = [t for t in events.classify_text(event.title)["topics"] if t in pulse.TOPICS]  # type: ignore[union-attr]
    return list(dict.fromkeys(keys))[:3]


def _times(start: datetime, n: int, rng: random.Random) -> list[datetime]:
    """Plausible, increasing timestamps after ``start`` that never run into the future."""
    now = utcnow() - timedelta(minutes=2)
    t = min(start, now)
    out = []
    for _ in range(n):
        t = t + timedelta(minutes=rng.randint(3, 75))
        out.append(min(t, now))
    return out


def generate_thread(db: Session, event: SourceEvent) -> Post | None:
    rng = random.Random(f"thread-{event.id}")
    quotes = _names(db, event.assets or [])
    names = {a: (quotes.get(a) or {}).get("name") or a for a in event.assets or []}
    extra = []
    for a in event.assets or []:
        q = quotes.get(a)
        if q and q.get("price") is not None and q.get("change_pct") is not None:
            extra.append(_quote_fact(a, q))
    facts = _facts_text(event, names, extra)
    allowed = _allowed_numbers(facts)

    assets = event.assets or []
    classes = list({personas.classify(a)["class"] for a in assets}) or (
        ["bond", "fx", "equity"] if event.kind in events.MACRO_KINDS else ["equity"]
    )
    markets_ = list({personas.classify(a)["market"] for a in assets}) or ["us", "uae"]
    sectors = events.sectors_for(assets, f"{event.title} {(event.facts or {}).get('summary', '')}") or (
        ["banks", "real_estate"] if event.kind in events.MACRO_KINDS else []
    )
    chosen = personas.pick(db, sectors, classes, markets_, event.topics or [], k=rng.randint(4, 6), seed=f"event-{event.id}")
    if len(chosen) < 3:
        event.status, event.facts = "skipped", {**(event.facts or {}), "skip": "too few relevant personas"}
        db.commit()
        return None
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([p.user_id for p in chosen])))}
    handles = {users[p.user_id].username: p for p in chosen}
    cards = "\n".join(_persona_card(p, users[p.user_id], assets) for p in chosen)

    views = _ask(SYSTEM, PERSPECTIVES.format(facts=facts, cards=cards), 1100)
    persp = [
        v for v in views.get("perspectives") or [] if isinstance(v, dict) and str(v.get("handle", "")).lstrip("@") in handles
    ]
    if len(persp) < 3:
        raise NexisError("the model returned too few perspectives")
    title = _clean(views.get("title"))[:140]
    if len(title) < 8 or BANNED.search(title) or not _ok(title, allowed):
        title = event.title[:140]
    opener = str(persp[0]["handle"]).lstrip("@")
    voices = "\n".join(
        f"@{h}: {p.traits['voice']['length']} — {p.traits['voice']['style']}; {p.traits['vocabulary']}"
        for h, p in handles.items()
    )
    n = rng.randint(6, 10)
    thread = _ask(
        SYSTEM,
        THREAD.format(facts=facts, perspectives=json.dumps(persp, ensure_ascii=False), voices=voices, opener=opener, n=n),
        2400,
    )
    opening = _clean((thread.get("opening") or {}).get("body"))
    if not _ok(opening, allowed) or len(opening) < 40:
        raise NexisError("the opening post failed the quality checks")
    replies = _filter_replies(thread.get("replies") or [], handles, allowed, set())
    if len(replies) < 4:
        event.status, event.facts = "skipped", {**(event.facts or {}), "skip": "thread too thin after quality checks"}
        db.commit()
        return None

    stance = {str(v["handle"]).lstrip("@"): v.get("stance") for v in persp}
    primary = assets[0] if assets else None
    asset_sym = primary or ("^GSPC" if event.kind in events.MACRO_KINDS else None)
    if asset_sym is None:
        event.status = "skipped"
        db.commit()
        return None
    t_open = event.published_at + timedelta(minutes=rng.randint(20, 150))
    stamps = _times(t_open, len(replies) + 1, rng)
    post = Post(
        user_id=handles[opener].user_id, body=opening, symbols=list(dict.fromkeys([asset_sym, *assets]))[:10], tags=[],
        source=SOURCE, asset=asset_sym, asset_name=(names.get(asset_sym) or (quotes.get(asset_sym) or {}).get("name") or asset_sym)[:160],
        title=title, sentiment=stance.get(opener) if stance.get(opener) in pulse.SENTIMENTS else None, event_id=event.id,
        created_at=stamps[0], comment_count=len(replies),
    )  # fmt: skip
    db.add(post)
    db.flush()
    db.add_all(PostTopic(post_id=post.id, topic=k) for k in _topics_for(event))
    ids: dict[int, int] = {}
    for r, at in zip(replies, stamps[1:], strict=True):
        c = Comment(post_id=post.id, user_id=handles[r["handle"]].user_id, body=r["body"][:1000],
                    parent_id=ids.get(r["reply_to"]), generated=True, created_at=at)  # fmt: skip
        db.add(c)
        db.flush()
        ids[r["id"]] = c.id
    for v in persp:
        h = str(v["handle"]).lstrip("@")
        if h in handles and asset_sym and v.get("stance") in pulse.SENTIMENTS:
            personas.remember(handles[h], asset_sym, v["stance"], str(v.get("angle") or v.get("argument") or ""))
    event.status = "discussed"
    db.commit()
    return post


def follow_up(db: Session) -> int:
    """Add a few replies to a recent generated thread that has gone quiet. Returns how many were added."""
    since = utcnow() - timedelta(days=3)
    posts = list(
        db.scalars(select(Post).where(Post.source == SOURCE, Post.created_at >= since, Post.event_id.is_not(None)).limit(60))
    )
    if not posts:
        return 0
    last_reply = dict(
        db.execute(
            select(Comment.post_id, func.max(Comment.created_at))
            .where(Comment.post_id.in_([p.id for p in posts]))
            .group_by(Comment.post_id)
        ).all()
    )
    quiet = sorted(posts, key=lambda p: last_reply.get(p.id) or p.created_at)
    post = next((p for p in quiet if (last_reply.get(p.id) or p.created_at) < utcnow() - timedelta(hours=3)), None)
    if post is None:
        return 0
    event = db.get(SourceEvent, post.event_id)
    if event is None:
        return 0
    rng = random.Random(f"follow-{post.id}-{utcnow():%Y%m%d%H}")
    comments = list(db.scalars(select(Comment).where(Comment.post_id == post.id).order_by(Comment.created_at)))
    authors = {u.id: u for u in db.scalars(select(User).where(User.id.in_({c.user_id for c in comments} | {post.user_id})))}
    in_thread = [p for p in db.scalars(select(Persona).where(Persona.user_id.in_(list(authors))))]
    if not in_thread:
        return 0
    cast = rng.sample(in_thread, min(len(in_thread), rng.randint(2, 3)))
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([p.user_id for p in cast])))}
    handles = {users[p.user_id].username: p for p in cast}
    names = {a: a for a in event.assets or []}
    facts = _facts_text(event, names, [])
    allowed = _allowed_numbers(facts)
    id_of = {c.id: i + 1 for i, c in enumerate(comments)}
    lines = [f"[0] @{authors[post.user_id].username}: {post.title} — {post.body[:600]}"]
    for c in comments[-14:]:
        u = authors.get(c.user_id)
        to = id_of.get(c.parent_id, 0) if c.parent_id else 0
        lines.append(f"[{id_of[c.id]}] @{u.username if u else 'member'} (replying to [{to}]): {c.body[:400]}")
    voices = "\n".join(f"@{h}: {p.traits['voice']['length']} — {p.traits['voice']['style']}" for h, p in handles.items())
    start_id = len(comments) + 1
    out = _ask(
        SYSTEM, FOLLOW_UP.format(facts=facts, thread="\n".join(lines), voices=voices, n=rng.randint(2, 4), start=start_id), 1200
    )
    out["replies"] = [
        r for r in out.get("replies") or [] if isinstance(r, dict) and isinstance(r.get("id"), int) and r["id"] >= start_id
    ]
    replies = _filter_replies(out.get("replies") or [], handles, allowed, set(id_of.values()))
    if not replies:
        return 0
    back = {v: k for k, v in id_of.items()}
    start = max(last_reply.get(post.id) or post.created_at, utcnow() - timedelta(hours=2))
    stamps = _times(start, len(replies), rng)
    new_ids: dict[int, int] = {}
    for r, at in zip(replies, stamps, strict=True):
        parent = new_ids.get(r["reply_to"]) if r["reply_to"] >= start_id else back.get(r["reply_to"])
        c = Comment(
            post_id=post.id,
            user_id=handles[r["handle"]].user_id,
            body=r["body"][:1000],
            parent_id=parent,
            generated=True,
            created_at=at,
        )
        db.add(c)
        db.flush()
        new_ids[r["id"]] = c.id
    post.comment_count = db.scalar(select(func.count(Comment.id)).where(Comment.post_id == post.id)) or 0
    db.commit()
    return len(replies)


def _candidates(db: Session, limit: int) -> list[SourceEvent]:
    if limit <= 0:
        return []
    since = utcnow() - timedelta(hours=72)
    rows = list(db.scalars(select(SourceEvent).where(SourceEvent.status == "new", SourceEvent.published_at >= since)
                           .order_by(SourceEvent.importance.desc(), SourceEvent.published_at.desc()).limit(80)))  # fmt: skip
    recent_assets = set(
        db.scalars(select(Post.asset).where(Post.source == SOURCE, Post.created_at >= utcnow() - timedelta(hours=18)))
    )
    out: list[SourceEvent] = []
    used: set[str] = set()
    for e in rows:
        assets = set(e.assets or [])
        if not assets and e.kind not in events.MACRO_KINDS:
            continue
        if OFF_TOPIC.search(e.title):
            continue
        if e.kind == "news" and e.provider == "newsfeed" and not any(
            events.mentioned(a, (e.facts or {}).get("names", {}).get(a), e.title) for a in assets
        ):
            continue  # the asset only appears in the summary: probably not what the story is about
        if assets & (recent_assets | used):
            continue
        out.append(e)
        used |= assets
        if len(out) >= limit:
            break
    return out


def tick(db: Session, max_threads: int = 1, force: bool = False) -> dict[str, Any]:
    """One engine step: ingest events, alert users, generate up to ``max_threads`` threads, maybe a follow-up."""
    s = get_settings()
    st = _state(db)
    last = st.get("last_run")
    if not force and last and datetime.fromisoformat(last) > utcnow() - timedelta(minutes=s.pulse_tick_minutes):
        return {"skipped": True, "reason": "ran recently", "last_run": last}
    _save_state(db, last_run=utcnow().isoformat())
    result: dict[str, Any] = {"personas": personas.ensure(db, s.pulse_personas)["total"], "events": events.ingest(db)}
    from app.services import alerts

    result["alerts"] = alerts.dispatch(db)
    if not llm.status().get("configured"):
        result["generated"] = 0
        result["note"] = "no AI model configured — nothing generated"
        _save_state(db, last_result=result, last_error=None)
        return result
    room = max(0, s.pulse_daily_threads - generated_today(db))
    made, errors = 0, []
    for e in _candidates(db, min(max_threads, room)):
        try:
            if generate_thread(db, e):
                made += 1
        except (NexisError, llm.LLMUnavailable) as exc:
            db.rollback()
            errors.append(f"event {e.id}: {getattr(exc, 'message', None) or getattr(exc, 'reason', None) or exc}")
            if isinstance(exc, llm.LLMUnavailable):
                break
    result["generated"] = made
    try:
        result["follow_up_replies"] = follow_up(db) if room > made else 0  # follow-ups count against the cap too
    except (NexisError, llm.LLMUnavailable) as exc:
        db.rollback()
        errors.append(f"follow-up: {getattr(exc, 'message', None) or exc}")
    _save_state(db, last_result=result, last_error="; ".join(errors)[:500] or None)
    return result
