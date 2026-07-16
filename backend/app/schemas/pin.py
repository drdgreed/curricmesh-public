"""Pydantic v2 schemas for VersionPin (V3-B student-portfolio version-pinning)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

PinStatus = Literal["active", "graduated", "withdrawn"]


class PinCreate(BaseModel):
    curriculum_id: uuid.UUID
    version_id: uuid.UUID
    student_label: str
    student_email: str | None = None
    cohort_id: uuid.UUID | None = None
    status: PinStatus = "active"


class PinOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    curriculum_id: uuid.UUID
    version_id: uuid.UUID
    cohort_id: uuid.UUID | None
    student_label: str
    student_email: str | None
    status: PinStatus
    pinned_at: datetime
