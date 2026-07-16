"""Unit tests for the outbound PII redactor (Phase B, B3 — D5 defense-in-depth).

Pure-function tests, no DB. Proves the structured-identifier cases the regex
DOES catch, and documents (asserts) the honest limitation: free-form names are
NOT redacted.
"""

from __future__ import annotations

import pytest

from app.core.tutor.redact import (
    EMAIL_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    URL_PLACEHOLDER,
    redact_pii,
)


@pytest.mark.parametrize(
    "raw, needle",
    [
        ("email me at jane.doe+work@example.co.uk please", "jane.doe+work@example.co.uk"),
        ("see https://example.com/path?x=1 for details", "https://example.com/path"),
        ("visit www.example.org today", "www.example.org"),
        ("call +1 (555) 123-4567 tomorrow", "555"),
        ("my cell is 555.123.4567", "123"),
    ],
)
def test_structured_pii_is_removed(raw: str, needle: str):
    out = redact_pii(raw)
    assert needle not in out


def test_email_becomes_email_placeholder():
    assert redact_pii("ping bob@corp.com") == "ping " + EMAIL_PLACEHOLDER


def test_url_becomes_url_placeholder():
    assert redact_pii("go to https://a.b/c") == "go to " + URL_PLACEHOLDER


def test_phone_becomes_phone_placeholder():
    out = redact_pii("dial +1-555-987-6543 now")
    assert PHONE_PLACEHOLDER in out
    assert "555" not in out


def test_multiple_identifiers_in_one_string():
    out = redact_pii("I'm jane@x.com, +1 555 111 2222, http://y.com")
    assert "jane@x.com" not in out
    assert "http://y.com" not in out
    assert "555" not in out
    assert EMAIL_PLACEHOLDER in out
    assert URL_PLACEHOLDER in out
    assert PHONE_PLACEHOLDER in out


def test_non_pii_text_is_untouched():
    q = "How does retrieval-augmented generation ground answers in course content?"
    assert redact_pii(q) == q


def test_empty_string():
    assert redact_pii("") == ""


def test_honest_limitation_names_not_redacted():
    """Documented limit: free-form personal names are NOT caught (no NER in v1).

    This test PINS the known gap so it is a conscious, reviewed limitation
    rather than a silent surprise. The primary D5 control (identity separation)
    is what actually keeps the learner's identity from the model — redaction is
    only defense-in-depth for identifiers typed into the question.
    """
    out = redact_pii("My name is Jonathan Q. Learner and I have a question")
    assert "Jonathan Q. Learner" in out
