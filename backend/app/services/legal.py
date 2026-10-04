"""Acceptance of the Terms of Use and Privacy Policy.

Every acceptance is stored as its own row (user, document versions, time, how it was given), so the record shows
what each member agreed to and when. Posting in Pulse requires an acceptance of the current versions; reading never
does.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import legal
from app.core.errors import NexisError, ValidationFailed
from app.db.base import utcnow
from app.models import LegalAcceptance, User


class TermsRequired(NexisError):
    status_code = 403
    code = "terms_required"


def latest(db: Session, user: User) -> LegalAcceptance | None:
    return db.scalars(
        select(LegalAcceptance).where(LegalAcceptance.user_id == user.id).order_by(LegalAcceptance.accepted_at.desc(), LegalAcceptance.id.desc())
    ).first()


def is_current(db: Session, user: User) -> bool:
    a = latest(db, user)
    return a is not None and a.terms_version == legal.TERMS_VERSION and a.privacy_version == legal.PRIVACY_VERSION


def status(db: Session, user: User) -> dict[str, Any]:
    a = latest(db, user)
    return {
        "terms_version": legal.TERMS_VERSION,
        "privacy_version": legal.PRIVACY_VERSION,
        "accepted": a is not None and a.terms_version == legal.TERMS_VERSION and a.privacy_version == legal.PRIVACY_VERSION,
        "accepted_at": a.accepted_at.isoformat() + "Z" if a else None,
        "accepted_terms_version": a.terms_version if a else None,
    }


def accept(db: Session, user: User, terms_version: str, privacy_version: str, method: str = "prompt", commit: bool = True) -> dict[str, Any]:
    """Record acceptance. The versions must be the current ones, so nobody accepts a document they weren't shown."""
    if terms_version != legal.TERMS_VERSION or privacy_version != legal.PRIVACY_VERSION:
        raise ValidationFailed("the Terms or Privacy Policy changed — reload the page to see the current versions")
    if not is_current(db, user):
        db.add(LegalAcceptance(user_id=user.id, terms_version=terms_version, privacy_version=privacy_version, accepted_at=utcnow(),
                               method=method))  # fmt: skip
        if commit:
            db.commit()
    return status(db, user)


def require_current(db: Session, user: User) -> None:
    if not is_current(db, user):
        raise TermsRequired("accept the Terms of Use and Privacy Policy before posting")


def documents() -> dict[str, Any]:
    return {"terms_version": legal.TERMS_VERSION, "privacy_version": legal.PRIVACY_VERSION, "documents": legal.DOCUMENTS}
