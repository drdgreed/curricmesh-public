"""Pydantic v2 schemas for Curriculum and Version resources."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import LifecycleStatus


# ---------------------------------------------------------------------------
# Curriculum schemas
# ---------------------------------------------------------------------------


class CurriculumCreate(BaseModel):
    name: str
    slug: str


class CurriculumOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    current_version_id: uuid.UUID | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Version schemas
# ---------------------------------------------------------------------------


class VersionCreate(BaseModel):
    major: int
    minor: int
    patch: int
    notes: str | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    curriculum_id: uuid.UUID
    major: int
    minor: int
    patch: int
    status: LifecycleStatus
    notes: str | None
    created_at: datetime


class TransitionRequest(BaseModel):
    to_status: LifecycleStatus
