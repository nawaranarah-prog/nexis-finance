"""Shared test values."""

from app.core import legal

# What the sign-up form sends when the person ticks "I agree to the Terms of Use and acknowledge the Privacy Policy".
TERMS = {"accept_terms": True, "terms_version": legal.TERMS_VERSION, "privacy_version": legal.PRIVACY_VERSION}


def member(client, tag: str = "member") -> dict:  # type: ignore[no-untyped-def]
    """Sign up and sign in a fresh member (the AI, reports and saved research need an account)."""
    import random

    client.post("/api/auth/logout")
    client.cookies.clear()
    mail = f"{tag}{random.randint(10**6, 10**7)}@example.com"
    r = client.post("/api/auth/register", json={**TERMS, "email": mail, "password": "member-pass-123"})
    assert r.status_code == 201, r.text
    return {"identifier": mail, "password": "member-pass-123", **client.get("/api/auth/me").json()["user"]}
