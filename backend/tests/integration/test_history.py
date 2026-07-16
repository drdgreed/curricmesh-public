"""Integration tests for app/core/history.py — audit history persistence helpers.

These tests hit the real PostgreSQL database via the db_session fixture.

Note on actor_id: history_events.actor_id is a nullable FK to users.id.
Tests that supply a non-null actor_id must insert a matching User row first.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.history import EventType, persist_event, record
from app.models.history import HistoryEvent
from app.models.user import User


async def _make_user(session) -> User:
    """Helper: insert a minimal User and flush so its PK is available."""
    user = User(email=f"{uuid.uuid4()}@test.local", role="instructor")
    session.add(user)
    await session.flush()
    return user


async def test_record_event_appends_with_actor_and_type(db_session):
    """record() persists a HistoryEvent row with the correct fields."""
    actor = await _make_user(db_session)
    actor_id = actor.id
    version_id = uuid.uuid4()
    target = f"version:{version_id}"
    details = {"from": "approved", "to": "active"}

    event = await record(
        db_session,
        actor_id=actor_id,
        event_type=EventType.version_activated,
        target=target,
        details=details,
    )

    # flush populated the PK
    assert event.id is not None
    assert event.created_at is not None

    # Verify via a fresh SELECT (not identity-map cache)
    result = await db_session.execute(
        select(HistoryEvent).where(HistoryEvent.id == event.id)
    )
    row = result.scalar_one()

    assert row.event_type == EventType.version_activated
    assert row.target == target
    assert row.actor_id == actor_id
    assert row.details == details


async def test_record_event_allows_null_actor(db_session):
    """record() accepts actor_id=None for system-generated events."""
    event = await record(
        db_session,
        actor_id=None,
        event_type=EventType.version_submitted,
        target="version:system",
    )

    assert event.id is not None
    assert event.actor_id is None

    result = await db_session.execute(
        select(HistoryEvent).where(HistoryEvent.id == event.id)
    )
    row = result.scalar_one()
    assert row.actor_id is None


async def test_record_event_defaults_empty_details(db_session):
    """record() defaults details to {} when not provided."""
    event = await record(
        db_session,
        actor_id=None,
        event_type=EventType.asset_updated,
        target="asset:abc",
    )

    result = await db_session.execute(
        select(HistoryEvent).where(HistoryEvent.id == event.id)
    )
    row = result.scalar_one()
    assert row.details == {}


async def test_persist_event_saves_prebuilt_event(db_session):
    """persist_event() accepts an already-constructed HistoryEvent and saves it."""
    actor = await _make_user(db_session)
    prebuilt = HistoryEvent(
        actor_id=actor.id,
        event_type=EventType.version_review,
        target="version:some-id",
        details={"from_status": "draft", "to_status": "review", "actor_role": "instructor"},
    )

    saved = await persist_event(db_session, prebuilt)

    # id and created_at populated by flush
    assert saved.id is not None
    assert saved.created_at is not None

    result = await db_session.execute(
        select(HistoryEvent).where(HistoryEvent.id == saved.id)
    )
    row = result.scalar_one()
    assert row.event_type == EventType.version_review
    assert row.details["from_status"] == "draft"
