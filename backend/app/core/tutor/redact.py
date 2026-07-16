"""Outbound PII redaction (Phase B, B3 — D5 defense-in-depth).

``redact_pii`` scrubs a learner's free text (a tutor question) before it is
handed to the LLM: emails, URLs, and phone numbers become typed placeholders
(``[EMAIL]``, ``[URL]``, ``[PHONE]``). It is the SECOND D5 control — the primary
one is identity separation (the model never receives the learner's id/name/email
at all; see ``app/core/tutor/answer.py``). This layer scrubs identifiers a
learner might type *into* the question itself.

**Honest limitation (documented, not a guarantee).** This is regex-based and
best-effort. It reliably catches structured identifiers (email/URL/phone) but
does NOT catch free-form personal names, addresses, or unusual formats — that
needs NER, which is deliberately out of scope for v1 (the spec calls this out).
The FULL un-redacted text is still stored server-side (``TutorMessage.text``);
redaction only bounds what leaves the backend to the model provider. Order
matters: URLs-with-scheme and emails are redacted before the looser phone/URL
patterns so an email's domain is never mis-split.
"""

from __future__ import annotations

import re

EMAIL_PLACEHOLDER = "[EMAIL]"
URL_PLACEHOLDER = "[URL]"
PHONE_PLACEHOLDER = "[PHONE]"

# An email: local@domain.tld. Anchored on ``@`` so it wins over the URL pattern
# for the domain half.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")

# A URL: an explicit scheme (http/https/ftp) OR a leading ``www.``. Redacted
# after emails so ``jane@example.com`` is already gone before we look at bare
# ``www.`` hosts.
_URL_RE = re.compile(
    r"\b(?:(?:https?|ftp)://|www\.)[^\s<>()]+",
    re.IGNORECASE,
)

# A phone number: an optional ``+``, then 7+ digits with common separators
# (space, dash, dot, parens). Requires enough digits to avoid eating ordinary
# short numbers; best-effort by design.
_PHONE_RE = re.compile(
    r"(?<![\w.])\+?\d(?:[\d\s().-]{6,}\d)"
)


def redact_pii(text: str) -> str:
    """Return ``text`` with emails, URLs, and phone numbers replaced by
    typed placeholders. Best-effort (regex only); see the module docstring for
    the limitation. Idempotent-ish: placeholders contain no redactable tokens,
    so re-running does not double-redact.
    """
    if not text:
        return text
    # Order is load-bearing: URLs-with-scheme + emails first, then the looser
    # phone matcher last.
    text = _EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    text = _URL_RE.sub(URL_PLACEHOLDER, text)
    text = _PHONE_RE.sub(PHONE_PLACEHOLDER, text)
    return text
