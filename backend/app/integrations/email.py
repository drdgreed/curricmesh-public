"""Email notification transport (B6) via stdlib smtplib.

Design:
- If host or to is empty, silently no-op (debug log only).
- On SMTP or network failure: log at ERROR level and re-raise. Never swallow.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email(
    host: str,
    port: int,
    user: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    *,
    from_email: str = "",
) -> None:
    """Send a plain-text email via SMTP with STARTTLS.

    Args:
        host:       SMTP server hostname. Empty string → no-op.
        port:       SMTP server port (typically 587 for STARTTLS).
        user:       SMTP authentication username.
        password:   SMTP authentication password.
        to:         Recipient email address. Empty string → no-op.
        subject:    Email subject line.
        body:       Plain-text message body.
        from_email: Envelope/header From address.  When set, used as-is.
                    Falls back to ``user`` then ``"curricmesh@noreply"``.
                    Necessary when the SMTP username is not an email address
                    (e.g. Resend's ``"resend"`` — passing that as the envelope
                    sender causes a ``501 Bad sender address syntax`` error).

    Raises:
        smtplib.SMTPException: On any SMTP-level failure.
        OSError:               On network-level failure.
    """
    if not host or not to:
        logger.debug("Email not configured (host=%r, to=%r) — skipping notification", host, to)
        return

    sender = from_email or user or "curricmesh@noreply"

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(sender, [to], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Failed to send email notification: %s", exc, exc_info=True)
        raise
