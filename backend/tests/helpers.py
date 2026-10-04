"""Shared test values."""

from app.core import legal

# What the sign-up form sends when the person ticks "I agree to the Terms of Use and acknowledge the Privacy Policy".
TERMS = {"accept_terms": True, "terms_version": legal.TERMS_VERSION, "privacy_version": legal.PRIVACY_VERSION}
