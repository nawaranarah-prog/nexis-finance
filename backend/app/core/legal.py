"""Versions of the legal documents members accept.

The documents themselves are pages in the web app (``/terms``, ``/privacy``, ``/disclaimer``,
``/community-guidelines``, ``/ai-disclosure``). Bump a version here whenever the corresponding page changes in a way
members must re-accept; anyone whose stored acceptance is older is asked to accept again before they can post.
"""

from __future__ import annotations

TERMS_VERSION = "2026-10-04"
PRIVACY_VERSION = "2026-10-04"

DOCUMENTS = {
    "terms": {"title": "Terms of Use", "path": "/terms", "version": TERMS_VERSION},
    "privacy": {"title": "Privacy Policy", "path": "/privacy", "version": PRIVACY_VERSION},
    "disclaimer": {"title": "Financial Disclaimer", "path": "/disclaimer", "version": TERMS_VERSION},
    "community_guidelines": {"title": "Community Guidelines", "path": "/community-guidelines", "version": TERMS_VERSION},
    "ai_disclosure": {"title": "AI Disclosure", "path": "/ai-disclosure", "version": TERMS_VERSION},
}
