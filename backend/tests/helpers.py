"""Shared test values."""

from app.core import legal

# What the sign-up form sends when the person ticks "I agree to the Terms of Use and acknowledge the Privacy Policy".
TERMS = {"accept_terms": True, "terms_version": legal.TERMS_VERSION, "privacy_version": legal.PRIVACY_VERSION}


def allow_posting(username: str) -> None:
    """Give an account the admin role, which may post on Finstagram (otherwise only news pages post). Unlike a news
    page, the account can still sign in, which the older end-to-end tests need."""
    from sqlalchemy import select

    from app.db import session as db_session
    from app.models import User

    with db_session.SessionLocal() as db:
        u = db.scalars(select(User).where(User.username == username.lower())).one()
        u.role = "admin"
        db.commit()


def make_page(username: str) -> None:
    """Turn an account into a news page (only news pages post on Finstagram)."""
    from sqlalchemy import select

    from app.db import session as db_session
    from app.models import User

    with db_session.SessionLocal() as db:
        u = db.scalars(select(User).where(User.username == username.lower())).one()
        u.kind = "page"
        db.commit()


def member(client, tag: str = "member") -> dict:  # type: ignore[no-untyped-def]
    """Sign up and sign in a fresh member (the AI, reports and saved research need an account)."""
    import random

    client.post("/api/auth/logout")
    client.cookies.clear()
    mail = f"{tag}{random.randint(10**6, 10**7)}@example.com"
    r = client.post("/api/auth/register", json={**TERMS, "email": mail, "password": "member-pass-123"})
    assert r.status_code == 201, r.text
    return {"identifier": mail, "password": "member-pass-123", **client.get("/api/auth/me").json()["user"]}
