"""Linking Reddit / X identities to a Nexis account, and signing in with them. Every outside service is mocked."""

from __future__ import annotations

import random
from urllib.parse import parse_qs, urlparse

import httpx

from app.services import oauth
from tests.helpers import TERMS


def _configure(monkeypatch):  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    s = get_settings()
    for k, v in {"reddit_client_id": "rid", "reddit_client_secret": "rsecret", "x_client_id": "xid", "x_client_secret": "xsecret",
                 "public_url": "http://testserver"}.items():  # fmt: skip
        monkeypatch.setattr(s, k, v)


def _fake_identity(monkeypatch, reddit_user, x_id="x1", x_user="nexis_fan"):  # type: ignore[no-untyped-def]
    def post(url, data=None, auth=None, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        assert data["code"] == "the-code" and auth[0] in ("rid", "xid")
        if "x.com" in url:
            assert data["code_verifier"]
        return httpx.Response(200, json={"access_token": "tok"})

    def get(url, params=None, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        assert headers["Authorization"] == "Bearer tok"
        if "reddit.com" in url:
            return httpx.Response(200, json={"id": "r1", "name": reddit_user, "icon_img": "https://i.redd.it/a.png?x=1"})
        return httpx.Response(200, json={"data": {"id": x_id, "username": x_user, "name": "Nexis Fan"}})

    monkeypatch.setattr(oauth.httpx, "post", post)
    monkeypatch.setattr(oauth.httpx, "get", get)


def _callback(client, provider, **start):  # type: ignore[no-untyped-def]
    r = client.get(f"/api/auth/oauth/{provider}/start", params=start, follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get(f"/api/auth/oauth/{provider}/callback", params={"code": "the-code", "state": state}, follow_redirects=False)


def test_link_reddit_and_x_then_sign_in_with_reddit(client, monkeypatch):
    _configure(monkeypatch)
    user = f"redditor_{random.randint(1000, 9999)}"
    _fake_identity(monkeypatch, user)
    providers = client.get("/api/auth/providers").json()
    assert providers["reddit"] and providers["x"]

    client.cookies.clear()
    anon = client.get("/api/auth/oauth/reddit/start", params={"mode": "link", "next": "/settings"}, follow_redirects=False)
    assert anon.headers["location"].startswith("/settings?error=")  # linking needs a Nexis account

    mail = f"link{random.randint(10000, 99999)}@example.com"
    assert client.post("/api/auth/register", json={**TERMS, "email": mail, "password": "linking-pass-1"}).status_code == 201
    r = client.get("/api/auth/oauth/reddit/start", params={"mode": "link", "next": "/settings"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith("https://www.reddit.com/api/v1/authorize") and q["scope"] == ["identity"]
    ok = _callback(client, "reddit", mode="link", next="/settings")
    assert ok.headers["location"] == "/settings?linked=reddit"
    x = _callback(client, "x", mode="link", next="/settings")
    assert x.headers["location"] == "/settings?linked=x"
    me = client.get("/api/auth/me").json()["user"]
    assert {(a["provider"], a["username"], a["url"]) for a in me["linked"]} == {
        ("reddit", user, f"https://www.reddit.com/user/{user}"),
        ("x", "nexis_fan", "https://x.com/nexis_fan"),
    }
    # the links show publicly on the profile too
    assert {a["provider"] for a in client.get(f"/api/social/users/{me['username']}").json()["linked"]} == {"reddit", "x"}

    # signing in with the linked Reddit account opens the same Nexis account
    client.post("/api/auth/logout")
    client.cookies.clear()
    back = _callback(client, "reddit", next="/finstagram")
    assert back.headers["location"] == "/finstagram"
    assert client.get("/api/auth/me").json()["user"]["id"] == me["id"]

    assert client.delete("/api/auth/me/linked/x").status_code == 204
    assert [a["provider"] for a in client.get("/api/auth/me").json()["user"]["linked"]] == ["reddit"]
    client.post("/api/auth/logout")
    client.cookies.clear()


def test_new_account_from_x_cannot_unlink_its_only_sign_in(client, monkeypatch):
    _configure(monkeypatch)
    _fake_identity(monkeypatch, "unused", x_id=f"x{random.randint(10**6, 10**7)}", x_user="solo_x_user")
    client.cookies.clear()
    r = _callback(client, "x", next="/")
    assert r.headers["location"] == "/"
    me = client.get("/api/auth/me").json()["user"]
    assert me["auth_provider"] == "x" and me["linked"][0]["username"] == "solo_x_user"
    blocked = client.delete("/api/auth/me/linked/x")
    assert blocked.status_code == 422 and "set a password" in blocked.json()["error"]["message"]
    client.post("/api/auth/logout")
    client.cookies.clear()
