"""Pydantic v2 schemas for the external-sync API (V3-C)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    curriculum_id: uuid.UUID
    # Nullable since Phase 4: new-model releases have no legacy Version row.
    version_id: uuid.UUID | None = None
    target: str
    status: str
    detail: dict
    created_at: datetime
