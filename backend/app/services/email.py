"""Outgoing email (account verification and password reset), through any SMTP server you configure.

Nothing is sent and nothing pretends to be sent until ``NEXIS_SMTP_HOST`` and ``NEXIS_SMTP_FROM`` are set: callers
check :func:`available` and tell the member plainly that email isn't set up yet. Message bodies are plain text and
contain only the link the member asked for; they're never logged.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.errors import NexisError
from app.core.logging import get_logger

log = get_logger(__name__)


class EmailUnavailable(NexisError):
    status_code = 503
    code = "email_unavailable"


def available() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def send(to: str, subject: str, text: str) -> None:
    s = get_settings()
    if not available():
        raise EmailUnavailable("Email isn't set up on Nexis yet, so this can't be sent. Please try again later.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(text)
    try:
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ssl.create_default_context(), timeout=20) as smtp:
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password or "")
                smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("email send failed: %s", exc.__class__.__name__)  # never the address or the body
        raise EmailUnavailable("We couldn't send the email just now. Please try again in a few minutes.") from exc
