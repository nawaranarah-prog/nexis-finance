"""Public discussions from other sites: no accounts, labelled by source, filtered, linked, never Nexis activity."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.models import PulseDiscussion
from app.services import public_discussions as pd
from app.services import pulse

POST = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/proudmelon</name></author><id>t3_abc123</id>
<link href="https://www.reddit.com/r/stocks/comments/abc123/is_nvidia_still_worth_it/"/>
<published>2026-10-04T08:00:00+00:00</published><title>Is Nvidia still worth buying at this valuation?</title>
<content type="html">&lt;div class="md"&gt;&lt;p&gt;Revenue keeps growing but the multiple is huge. Thoughts? Thanks to u/someone_else for the chart.&lt;/p&gt;&lt;/div&gt;
&amp;#32; submitted by &amp;#32; &lt;a href="https://www.reddit.com/user/proudmelon"&gt; /u/proudmelon &lt;/a&gt;</content></entry></feed>"""

COMMENTS = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/proudmelon</name></author><id>t3_abc123</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/"/>
<title>post</title><content type="html">the post itself</content></entry>
<entry><author><name>/u/bull_guy</name></author><id>t1_c1</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/c1/"/>
<published>2026-10-04T09:00:00+00:00</published><title>c</title><content type="html">I agree with @bull_guy here, data centre demand is still growing and margins look solid for the next two years.</content></entry>
<entry><author><name>/u/bear_gal</name></author><id>t1_c2</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/c2/"/>
<published>2026-10-04T09:30:00+00:00</published><title>c</title><content type="html">I disagree, the expectations are already priced in and any slowdown in hyperscaler capex will hit it hard.</content></entry>
<entry><author><name>/u/asker</name></author><id>t1_c3</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/c3/"/>
<published>2026-10-04T10:00:00+00:00</published><title>c</title><content type="html">What happens to the multiple if AMD actually catches up on inference chips over the next year?</content></entry>
<entry><author><name>/u/spammer</name></author><id>t1_c4</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/c4/"/>
<published>2026-10-04T10:10:00+00:00</published><title>c</title><content type="html">Guaranteed 50% returns every month, DM me on telegram to join my VIP signals group today friends.</content></entry>
<entry><author><name>/u/hype</name></author><id>t1_c5</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/c5/"/>
<published>2026-10-04T10:20:00+00:00</published><title>c</title><content type="html">TO THE MOON!!! NVDA WILL PUMP ALL WEEK LONG, GET YOUR CALLS IN ORDER RIGHT NOW EVERYONE!!!</content></entry>
<entry><author><name>/u/short</name></author><id>t1_c6</id><link href="https://www.reddit.com/r/stocks/comments/abc123/x/c6/"/>
<published>2026-10-04T10:30:00+00:00</published><title>c</title><content type="html">lol</content></entry>
</feed>"""


@pytest.fixture
def db(client):  # type: ignore[no-untyped-def]
    from app.db import session as db_session

    with db_session.SessionLocal() as s:
        yield s


@pytest.fixture
def feeds(monkeypatch):  # type: ignore[no-untyped-def]
    import xml.etree.ElementTree as ET

    class R:
        def __init__(self, text: str) -> None:
            self.status_code, self.content = 200, text.encode()

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, params=None):  # type: ignore[no-untyped-def]
        return R(COMMENTS if "/comments/" in url else POST)

    monkeypatch.setattr(pd, "_get", fake_get)
    monkeypatch.setattr(pd.time, "sleep", lambda s: None)
    from app.services import market_pulse

    monkeypatch.setattr(market_pulse, "_ai_available", lambda db: False)  # word rules in tests
    return ET


def test_reddit_thread_is_collected_without_any_account(client, db, feeds):
    out = pd.collect_reddit(db, ["stocks"], posts_per_sub=5, with_comments=2)
    assert out == {"threads": 1, "skipped": 0}
    d = db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == "reddit:abc123")).one()
    assert d.kind == "public" and d.author_id is None and d.origin["label"] == "Reddit · r/stocks"
    texts = " ".join([d.title, d.body] + [q["text"] for q in d.quotes])
    for name in ("proudmelon", "someone_else", "bull_guy", "bear_gal", "asker", "spammer", "submitted by"):
        assert name not in texts
    assert "[user]" in d.body  # mentions are replaced, not kept
    stances = {q["text"][:12]: q["stance"] for q in d.quotes}
    assert len(d.quotes) == 3  # the scam, the shouting hype and "lol" were dropped
    assert sorted(stances.values()) == ["pushback", "question", "support"]
    assert all(q["url"].startswith("https://www.reddit.com/r/stocks/comments/abc123/") for q in d.quotes)
    assert "NVDA" in d.symbols and d.comment_count == 0 and d.participant_count == 0

    # public API: labelled by source, quotes linked, no accounts
    _ = client.post("/api/auth/logout")
    full = client.get(f"/api/pulse/discussions/{d.public_id}").json()
    assert full["author"] == {"display_name": "Reddit · r/stocks", "type": "public", "platform": "reddit"}
    assert full["origin"]["quotes"] == 3 and full["public"]["quotes"][0]["url"] and "Usernames are removed" in full["public"]["disclosure"]
    assert "proudmelon" not in str(full) and "bull_guy" not in str(full)
    feed = client.get("/api/pulse/feed", params={"kind": "public"}).json()["items"]
    assert d.public_id in [x["id"] for x in feed]
    assert d.public_id not in [x["id"] for x in client.get("/api/pulse/feed", params={"kind": "community"}).json()["items"]]

    # refreshing doesn't duplicate; a moderator's removal sticks
    pd.collect_reddit(db, ["stocks"], posts_per_sub=5, with_comments=2)
    assert db.scalars(select(PulseDiscussion).where(PulseDiscussion.editorial_key == "reddit:abc123")).one().id == d.id
    d.status = "removed"
    db.commit()
    d.content_updated_at = utcnow().replace(year=2000)
    db.commit()
    pd.collect_reddit(db, ["stocks"], posts_per_sub=5, with_comments=2)
    assert db.get(PulseDiscussion, d.id).status == "removed"


def test_threads_need_real_arguments_and_clean_titles(client, db):
    few = [{"text": "One real argument about valuation and the data centre cycle here.", "stance": None, "url": "https://x/1", "at": None}]
    assert pd.upsert(db, "hn:1", platform="hn", community=None, url="https://news.ycombinator.com/item?id=1", title="Fed holds rates",
                     body="", posted_at=None, quotes=few) is None  # fmt: skip
    many = few * 3
    assert pd.upsert(db, "hn:2", platform="hn", community=None, url="https://x", title="Guaranteed 40% returns, DM me on telegram",
                     body="", posted_at=None, quotes=[dict(q, url=f"https://x/{i}") for i, q in enumerate(many)]) is None  # fmt: skip


def test_cleaning_and_rules():
    assert pd.clean("Thanks /u/abc and u/def_g and @someone — see https://x.com/a") == "Thanks [user] and [user] and [user] — see [link]"
    assert pd.rule_stance("Why would anyone pay 40x sales?") == "question"
    assert pd.rule_stance("This is already priced in, I doubt it goes higher.") == "pushback"
    assert pd.rule_stance("Bought more today, margins look strong.") == "support"
    assert not pd.usable("WE GONNA PUMP THIS ALL WEEK LONG, LOAD UP ON CALLS NOW EVERYONE")
    assert not pd.usable("$NVDA $AMD $TSLA $SPY")
    assert pd.usable("Revenue growth is strong but the valuation already assumes years of it continuing.")
    assert pulse.TOPICS  # topics module still importable alongside


def test_profanity_is_masked_and_old_quotes_are_rechecked(client, db):
    assert pd.tidy("This IPO is a fucking doozy") == "This IPO is a f****** doozy" and pd.tidy("a classic asset class") == "a classic asset class"
    good = [{"text": f"Margins look solid and data centre demand keeps growing, point {i}.", "stance": "support", "url": f"https://x/{i}", "at": None} for i in range(3)]
    stale = {"text": "No Pump For you only dump, rich dads need to sell now before monday", "stance": "bearish", "url": "https://x/old", "at": None}
    d = pd.upsert(db, "hn:recheck", platform="hn", community=None, url="https://x", title="Nvidia margins debate", body="", posted_at=None, quotes=good)
    d.quotes = [*d.quotes, stale]  # stored by an older, looser filter
    db.commit()
    d = pd.upsert(db, "hn:recheck", platform="hn", community=None, url="https://x", title="Nvidia margins debate", body="", posted_at=None, quotes=good)
    assert all("Pump" not in q["text"] for q in d.quotes) and len(d.quotes) == 3
