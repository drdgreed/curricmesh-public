"""Slack notification transport (B6).

Design:
- If webhook_url is empty, silently no-op (debug log only).
- On HTTP or network failure: log at ERROR level and re-raise. Never swallow.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def send_slack(webhook_url: str, text: str) -> None:
    """POST a plain-text message to a Slack incoming webhook.

    Args:
        webhook_url: Slack incoming webhook URL. Empty string → no-op.
        text:        Message body to send.

    Raises:
        httpx.HTTPError:  On non-2xx response from Slack.
        httpx.RequestError: On network-level failure.
    """
    if not webhook_url:
        logger.debug("Slack webhook not configured — skipping notification")
        return

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(webhook_url, json={"text": text})
            response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.error("Failed to send Slack notification: %s", exc, exc_info=True)
            raise
