import smtplib
from unittest.mock import MagicMock, patch
from app.integrations.email import send_email


def _send(**kw):
    with patch("app.integrations.email.smtplib.SMTP") as SMTP:
        smtp = MagicMock()
        SMTP.return_value.__enter__.return_value = smtp
        send_email(host="smtp.test", port=587, user="resend", password="x",
                   to="dest@example.com", subject="s", body="b", **kw)
        return smtp.sendmail.call_args


def test_from_email_used_when_given():
    args = _send(from_email="noreply@career-forge.org")
    assert args.args[0] == "noreply@career-forge.org"
    assert "From: noreply@career-forge.org" in args.args[2]


def test_falls_back_to_user_when_from_email_absent():
    args = _send()
    assert args.args[0] == "resend"
