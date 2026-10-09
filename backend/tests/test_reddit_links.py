"""Reddit in Pulse, the authorised no-cost way: member-shared links, shown by Reddit's own embed. Nexis never fetches
or stores Reddit content — only the validated link."""

from __future__ import annotations

import random

import httpx
import pytest

from app.services import reddit_links
from tests.helpers import TERMS


def test_parse_accepts_post_and_comment_links_only():
    p = reddit_links.parse("https://old.reddit.com/r/stocks/comments/1abc23/is_nvidia_still_cheap/?utm_source=share")
    assert p == {"url": "https://www.reddit.com/r/stocks/comments/1abc23/is_nvidia_still_cheap/", "subreddit": "stocks", "post_id": "1abc23",
                 "comment_id": None, "label": "r/stocks", "title": "Discussion on Reddit · r/stocks"}  # fmt: skip
    c = reddit_links.parse("https://www.reddit.com/r/investing/comments/1abc23/title/kx9y8z7/")
    assert c and c["comment_id"] == "kx9y8z7" and c["url"].endswith("/title/kx9y8z7/")
    assert (
        reddit_links.parse("https://reddit.com/r/stocks/comments/1abc23")["url"]
        == "https://www.reddit.com/r/stocks/comments/1abc23/"
    )
    for bad in (
        "https://evil.com/r/stocks/comments/1abc23/",
        "https://reddit.com.evil.com/r/stocks/comments/1abc23/",
        "javascript:alert(1)",
        "https://www.reddit.com/r/stocks/",
        "https://redd.it/1abc23",
        "https://www.reddit.com/r/stocks/s/AbCdEf",
        "https://user:pw@www.reddit.com/r/stocks/comments/1abc23/",
        "ftp://www.reddit.com/r/stocks/comments/1abc23/",
        "",
        None,
    ):
        assert reddit_links.parse(bad) is None, bad
    assert (
        reddit_links.first_in("see https://www.reddit.com/r/wallstreetbets/comments/9zzz11/yolo/ for the bull case.")["subreddit"]
        == "wallstreetbets"
    )


@pytest.fixture
def no_network(monkeypatch):  # type: ignore[no-untyped-def]
    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("Nexis must not request Reddit from the server")

    for name in ("get", "post", "request"):
        monkeypatch.setattr(httpx, name, boom)
    monkeypatch.setattr(httpx.Client, "send", boom)


def _signup(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/api/auth/logout")
    client.cookies.clear()
    mail = f"rl{random.randint(10**6, 10**7)}@example.com"
    assert client.post("/api/auth/register", json={**TERMS, "email": mail, "password": "reddit-link-1"}).status_code == 201


def test_members_share_a_reddit_post_and_everyone_can_read_it(client, no_network):
    _signup(client)
    r = client.post("/api/pulse/discussions", json={"title": "What do you make of this Reddit thread on Nvidia margins?",
                                                     "reddit_url": "https://www.reddit.com/r/stocks/comments/1abc23/nvidia_margins/?share_id=x"})  # fmt: skip
    assert r.status_code == 201, r.text
    d = r.json()
    assert (
        d["reddit"]["url"] == "https://www.reddit.com/r/stocks/comments/1abc23/nvidia_margins/"
        and d["reddit"]["label"] == "r/stocks"
    )
    assert [s["publisher"] for s in d["sources"]] == ["Reddit"] and d["author"][
        "type"
    ] != "public"  # a member post, not Reddit content
    client.post("/api/auth/logout")
    client.cookies.clear()
    full = client.get(f"/api/pulse/discussions/{d['id']}").json()  # visitors (any plan) see it too
    assert full["reddit"]["url"] == d["reddit"]["url"]
    card = next(x for x in client.get("/api/pulse/feed", params={"sort": "latest"}).json()["items"] if x["id"] == d["id"])
    assert card["reddit"]["subreddit"] == "stocks"


def test_a_link_in_the_text_is_picked_up_and_bad_links_are_refused(client, no_network):
    _signup(client)
    d = client.post("/api/pulse/discussions", json={"title": "Bank margins debate worth reading today",
                                                     "body": "Good thread: https://old.reddit.com/r/investing/comments/7xyz12/bank_margins/ — thoughts?"}).json()  # fmt: skip
    assert d["reddit"]["subreddit"] == "investing"
    r = client.post(
        "/api/pulse/discussions", json={"title": "Another thread about bank margins", "reddit_url": "https://redd.it/7xyz12"}
    )
    assert r.status_code == 422 and "Reddit post" in r.json()["error"]["message"]
    plain = client.post("/api/pulse/discussions", json={"title": "No Reddit here, just a question on rates"}).json()
    assert plain["reddit"] is None
