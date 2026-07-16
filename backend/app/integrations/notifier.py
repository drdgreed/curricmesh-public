"""Unified notification dispatcher (B6).

Design — surface-don't-swallow, non-blocking:
- Builds a human-readable message from the event type and details dict.
- Attempts BOTH channels (Slack + email) using settings from app.config.
- A failure in ONE channel is logged at ERROR level (with exc_info) but does
  NOT prevent the other channel from being attempted, and does NOT raise to
  the caller. Notifications are non-critical: they must never roll back a
  transaction or break a user request.
- Empty settings → no-op for that channel (no error, just a debug log).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.integrations.email import send_email
from app.integrations.slack import send_slack

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

_DEFAULT_MESSAGE = "CurricMesh event: {event_type}"

_TEMPLATES: dict[str, str] = {
    "ccr_created": "CCR submitted: {title} (curriculum: {curriculum_id}, bump: {proposed_bump})",
    "qa_passed": "QA passed for CCR {ccr_id}",
    "version_activated": "Version {semver} activated for curriculum {curriculum_id}",
}


def _build_message(event_type: str, details: dict[str, Any]) -> str:
    """Build a human-readable notification message.

    Uses a template keyed by event_type; falls back to a generic format.
    Template variables are filled from *details* (missing keys silently become
    the literal placeholder string from str.format_map with a default-dict).
    """
    template = _TEMPLATES.get(event_type, _DEFAULT_MESSAGE)

    class _DefaultDict(dict):  # type: ignore[type-arg]
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"

    payload = _DefaultDict(details)
    payload["event_type"] = event_type
    payload["ccr_id"] = details.get("ccr_id", details.get("id", "<unknown>"))
    return template.format_map(payload)


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


async def notify(event_type: str, details: dict[str, Any]) -> dict[str, bool]:
    """Dispatch a notification for a workflow event to all configured channels.

    Args:
        event_type: One of the EventType string values (e.g. "ccr_created").
        details:    Dict with event-specific context used to build the message.

    Returns:
        A dict {"slack": bool, "email": bool} indicating which channels
        succeeded (True) or were skipped/failed (False).

    Design:
        Each channel is wrapped in its own try/except. A failure logs at ERROR
        with exc_info (so the exception is visible in logs) but does NOT
        propagate to the caller. Both channels are always attempted.
    """
    if not settings.SLACK_WEBHOOK_URL and (not settings.SMTP_HOST or not settings.NOTIFY_EMAIL_TO):
        logger.debug("No notification channels configured — skipping for event %s", event_type)
        return {"slack": False, "email": False}

    message = _build_message(event_type, details)
    subject = f"[CurricMesh] {event_type.replace('_', ' ').title()}"

    results: dict[str, bool] = {"slack": False, "email": False}

    # --- Slack ---
    try:
        await send_slack(settings.SLACK_WEBHOOK_URL, message)
        results["slack"] = True
    except Exception:
        logger.error(
            "Slack notification failed for event %r — continuing with other channels",
            event_type,
            exc_info=True,
        )

    # --- Email (run in executor so sync smtplib doesn't block the event loop) ---
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            send_email,
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            settings.SMTP_USER,
            settings.SMTP_PASSWORD,
            settings.NOTIFY_EMAIL_TO,
            subject,
            message,
        )
        results["email"] = True
    except Exception:
        logger.error(
            "Email notification failed for event %r — continuing",
            event_type,
            exc_info=True,
        )

    return results
