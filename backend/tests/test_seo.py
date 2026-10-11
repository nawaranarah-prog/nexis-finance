from fastapi.testclient import TestClient


def test_sitemap_lists_main_pages(client: TestClient) -> None:
    r = client.get("/api/seo/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<loc>https://nexis-finance-five.vercel.app/finstagram</loc>" in r.text
    assert "<loc>https://nexis-finance-five.vercel.app/advisor</loc>" in r.text


# ------------------------------------------------------------------ option A: server-rendered heads for public pages

SHELL = ('<!doctype html><html><head><title>Nexis Finance</title><meta name="description" content="generic" />'
         '<meta name="robots" content="index, follow" /><link rel="canonical" href="https://x/" />'
         '<meta property="og:title" content="t" /><meta property="og:description" content="d" /><meta property="og:url" content="u" />'
         '</head><body><div id="root"></div></body></html>')  # fmt: skip


def _shell(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.api.routes import seo

    monkeypatch.setattr(seo, "_shell", lambda: SHELL)


def test_public_pages_get_their_own_head_and_a_visible_summary(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _shell(monkeypatch)
    r = client.get("/api/seo/render/page/world-graph")
    assert r.status_code == 200 and "<title>World Graph: who owns and controls a company · Nexis Finance</title>" in r.text
    assert '/world-graph" />' in r.text and '<main class="seo-summary"><h1>World Graph' in r.text and "s-maxage" in r.headers["cache-control"]
    unknown = client.get("/api/seo/render/page/settings", follow_redirects=False)
    assert unknown.status_code == 302 and unknown.headers["location"] == "/settings?spa=1"  # never renders private pages


def test_instrument_pages_name_the_company_but_never_render_prices(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import markets

    _shell(monkeypatch)
    monkeypatch.setattr(markets, "details", lambda db, s: {"name": "Emaar Properties PJSC", "exchange": "DFM",
                                                           "quote": {"price": 11.38}, "profile": {"summary": "Provider text"}})  # fmt: skip
    r = client.get("/api/seo/render/markets/EMAAR.AE")
    assert r.status_code == 200 and "<title>Emaar Properties PJSC (EMAAR.AE) · Nexis Finance</title>" in r.text
    assert "on DFM" in r.text and "tickerSymbol" in r.text
    assert "11.38" not in r.text and "Provider text" not in r.text  # no unlicensed price data or provider text


def test_post_pages_follow_the_news_policy(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db import session as db_session
    from app.models import Post
    from app.services import newsfeed
    from tests.helpers import member

    _shell(monkeypatch)
    with db_session.SessionLocal() as db:
        page = newsfeed.ensure_pages(db)["uae.markets"]
        ok = Post(user_id=page.id, body="Emaar launches a tower", link_url="https://www.khaleejtimes.com/b", link_title="Emaar launches a tower",
                  link_source="Khaleej Times")  # fmt: skip
        blocked = Post(user_id=page.id, body="x", link_url="https://news.google.com/rss/articles/y", link_title="x", link_source="Reuters")
        db.add_all([ok, blocked])
        db.commit()
        ids = (ok.id, blocked.id)
    m = member(client, "seo")
    with db_session.SessionLocal() as db:
        mine = Post(user_id=m["id"], body="my old member post")
        db.add(mine)
        db.commit()
        mine_id = mine.id
    r = client.get(f"/api/seo/render/finstagram/p/{ids[0]}")
    assert r.status_code == 200 and "Emaar launches a tower — Khaleej Times" in r.text and 'href="https://www.khaleejtimes.com/b"' in r.text
    for pid in (ids[1], mine_id, 99999999):
        assert client.get(f"/api/seo/render/finstagram/p/{pid}").status_code == 404
