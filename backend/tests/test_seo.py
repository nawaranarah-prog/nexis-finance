from fastapi.testclient import TestClient


def test_sitemap_lists_main_pages(client: TestClient) -> None:
    r = client.get("/api/seo/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<loc>https://nexis-finance-five.vercel.app/finstagram</loc>" in r.text
    assert "<loc>https://nexis-finance-five.vercel.app/advisor</loc>" in r.text
