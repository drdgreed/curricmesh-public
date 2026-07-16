"""Unit tests for app/integrations/ — Slack, email, and notifier.

All transports are mocked. No real network or SMTP connections.

Design verified:
- send_slack: posts JSON, no-ops on empty URL, re-raises+logs on failure.
- send_email: no-ops on empty host/to, re-raises+logs on failure.
- notify: surfaces per-channel failures (logged, exc visible) without raising
  to the caller, and continues to attempt remaining channels. This is the
  "surface, don't swallow" + "non-blocking" contract.
"""

from __future__ import annotations

import smtplib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations.email import send_email
from app.integrations.notifier import notify
from app.integrations.slack import send_slack


# ---------------------------------------------------------------------------
# send_slack tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_slack_posts_to_webhook():
    """send_slack POSTs {"text": ...} to the webhook URL."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.integrations.slack.httpx.AsyncClient", return_value=mock_client):
        await send_slack("https://hooks.slack.com/test", "hello world")

    mock_client.post.assert_called_once_with(
        "https://hooks.slack.com/test", json={"text": "hello world"}
    )
    mock_response.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_send_slack_empty_webhook_noop():
    """Empty webhook URL → no HTTP call attempted."""
    with patch("app.integrations.slack.httpx.AsyncClient") as mock_cls:
        await send_slack("", "should not send")
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_slack_failure_is_raised_and_logged(caplog):
    """On HTTP failure: send_slack logs at ERROR and re-raises."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(
        side_effect=httpx.RequestError("connection refused", request=MagicMock())
    )

    import logging

    with patch("app.integrations.slack.httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level(logging.ERROR, logger="app.integrations.slack"):
            with pytest.raises(httpx.RequestError):
                await send_slack("https://hooks.slack.com/test", "will fail")

    assert any("Failed to send Slack notification" in r.message for r in caplog.records)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1
    # exc_info should be set (the traceback is attached)
    assert error_records[0].exc_info is not None


# ---------------------------------------------------------------------------
# send_email tests
# ---------------------------------------------------------------------------


def test_send_email_noop_when_host_empty():
    """Empty host → no SMTP connection attempted."""
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp:
        send_email("", 587, "", "", "to@example.com", "subject", "body")
    mock_smtp.assert_not_called()


def test_send_email_noop_when_to_empty():
    """Empty to address → no SMTP connection attempted."""
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp:
        send_email("smtp.example.com", 587, "", "", "", "subject", "body")
    mock_smtp.assert_not_called()


def test_send_email_failure_is_raised_and_logged(caplog):
    """On SMTP failure: send_email logs at ERROR and re-raises."""
    import logging

    mock_smtp_instance = MagicMock()
    mock_smtp_instance.__enter__ = MagicMock(return_value=mock_smtp_instance)
    mock_smtp_instance.__exit__ = MagicMock(return_value=False)
    mock_smtp_instance.ehlo = MagicMock()
    mock_smtp_instance.starttls = MagicMock(side_effect=smtplib.SMTPException("TLS failed"))

    with patch("app.integrations.email.smtplib.SMTP", return_value=mock_smtp_instance):
        with caplog.at_level(logging.ERROR, logger="app.integrations.email"):
            with pytest.raises(smtplib.SMTPException):
                send_email(
                    "smtp.example.com", 587, "user", "pass",
                    "to@example.com", "subject", "body"
                )

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1
    assert any("Failed to send email notification" in r.message for r in error_records)
    assert error_records[0].exc_info is not None


# ---------------------------------------------------------------------------
# notify tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_surfaces_channel_failure_without_crashing(caplog):
    """A Slack failure is logged at ERROR but does NOT prevent email or raise.

    This is the core "surface, don't swallow" + "non-blocking" contract:
      - Slack raises → logged at ERROR with exc_info
      - Email is still attempted and succeeds
      - notify() returns without raising
    """
    import logging

    slack_error = httpx.RequestError("slack down", request=MagicMock())

    with (
        patch("app.integrations.notifier.settings") as mock_settings,
        patch("app.integrations.notifier.send_slack", side_effect=slack_error) as mock_slack,
        patch("app.integrations.notifier.send_email") as mock_email,
    ):
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.NOTIFY_EMAIL_TO = "admin@example.com"

        with caplog.at_level(logging.ERROR, logger="app.integrations.notifier"):
            result = await notify("ccr_created", {"title": "test CCR", "curriculum_id": "abc", "proposed_bump": "minor"})

    # Slack was attempted
    mock_slack.assert_called_once()

    # Email was still attempted despite Slack failure
    mock_email.assert_called_once()

    # Slack failure is surfaced in logs at ERROR with exc_info (traceback attached)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1
    slack_errors = [r for r in error_records if "Slack" in r.message]
    assert len(slack_errors) >= 1
    assert slack_errors[0].exc_info is not None, "exc_info must be set so the traceback is visible"

    # notify() did NOT raise — caller's request is unaffected
    # (the return value is a dict, not an exception)
    assert isinstance(result, dict)
    assert result["slack"] is False   # Slack failed
    assert result["email"] is True    # Email succeeded


@pytest.mark.asyncio
async def test_notify_surfaces_email_failure_without_crashing(caplog):
    """An email failure is logged at ERROR but does NOT prevent Slack or raise.

    Symmetric mirror of test_notify_surfaces_channel_failure_without_crashing:
      - Email raises → logged at ERROR with exc_info
      - Slack is still attempted and succeeds
      - notify() returns without raising
    """
    import logging
    import smtplib

    email_error = smtplib.SMTPException("smtp down")

    with (
        patch("app.integrations.notifier.settings") as mock_settings,
        patch("app.integrations.notifier.send_slack", return_value=None) as mock_slack,
        patch("app.integrations.notifier.send_email", side_effect=email_error) as mock_email,
    ):
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.NOTIFY_EMAIL_TO = "admin@example.com"

        with caplog.at_level(logging.ERROR, logger="app.integrations.notifier"):
            result = await notify("ccr_created", {"title": "test CCR", "curriculum_id": "abc", "proposed_bump": "minor"})

    # Email was attempted
    mock_email.assert_called_once()

    # Slack was still attempted despite email failure
    mock_slack.assert_called_once()

    # Email failure is surfaced in logs at ERROR with exc_info (traceback attached)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) >= 1
    email_errors = [r for r in error_records if "Email" in r.message]
    assert len(email_errors) >= 1
    assert email_errors[0].exc_info is not None, "exc_info must be set so the traceback is visible"

    # notify() did NOT raise — caller's request is unaffected
    assert isinstance(result, dict)
    assert result["slack"] is True    # Slack succeeded
    assert result["email"] is False   # Email failed


@pytest.mark.asyncio
async def test_notify_noop_when_unconfigured():
    """Empty settings → notify does nothing, no error raised."""
    with (
        patch("app.integrations.notifier.settings") as mock_settings,
        patch("app.integrations.notifier.send_slack") as mock_slack,
        patch("app.integrations.notifier.send_email") as mock_email,
    ):
        mock_settings.SLACK_WEBHOOK_URL = ""
        mock_settings.SMTP_HOST = ""
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = ""
        mock_settings.SMTP_PASSWORD = ""
        mock_settings.NOTIFY_EMAIL_TO = ""

        result = await notify("ccr_created", {"title": "test"})

    mock_slack.assert_not_called()
    mock_email.assert_not_called()
    assert result == {"slack": False, "email": False}


@pytest.mark.asyncio
async def test_notify_both_channels_succeed():
    """When both channels are configured and succeed, result shows both True."""
    with (
        patch("app.integrations.notifier.settings") as mock_settings,
        patch("app.integrations.notifier.send_slack", return_value=None) as mock_slack,
        patch("app.integrations.notifier.send_email", return_value=None) as mock_email,
    ):
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.NOTIFY_EMAIL_TO = "admin@example.com"

        result = await notify("qa_passed", {"ccr_id": "1234"})

    mock_slack.assert_called_once()
    mock_email.assert_called_once()
    assert result == {"slack": True, "email": True}


@pytest.mark.asyncio
async def test_notify_message_templates():
    """verify _build_message produces human-readable text for known event types."""
    from app.integrations.notifier import _build_message

    msg = _build_message("ccr_created", {"title": "Fix curriculum", "curriculum_id": "abc-123", "proposed_bump": "minor"})
    assert "Fix curriculum" in msg
    assert "minor" in msg

    msg = _build_message("qa_passed", {"ccr_id": "ccr-456"})
    assert "ccr-456" in msg

    msg = _build_message("version_activated", {"semver": "2.1.0", "curriculum_id": "cur-789"})
    assert "2.1.0" in msg
    assert "cur-789" in msg

    # Unknown event type falls back gracefully
    msg = _build_message("unknown_event", {})
    assert "unknown_event" in msg
