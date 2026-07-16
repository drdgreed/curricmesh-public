"""AI-test fixtures: keep the durable usage writer from touching the DB.

``AIClient._parse`` now schedules a fire-and-forget ``ai_call_events`` insert via
:func:`app.ai.usage_store.record_event`. The aggregator-focused tests exercise
``_parse`` with a fake transport but don't want (and don't set up) a DB write, so
we disable persistence for the whole ``tests/ai`` package by default. The
dedicated persistence test re-enables it locally where it has a real session.
"""

import pytest

from app.ai import usage_store


@pytest.fixture(autouse=True)
def _disable_usage_persistence():
    prev = usage_store.PERSIST_ENABLED
    usage_store.PERSIST_ENABLED = False
    try:
        yield
    finally:
        usage_store.PERSIST_ENABLED = prev
