"""Pydantic v2 schemas for the curriculum alignment-detail endpoint.

The alignment endpoint surfaces *precise staleness* (§3.1): each stale
dependency, why it is stale (``mode``), and — when the edge carries provenance —
exactly how many revisions behind the dependent is (``revision_delta``). Ids are
the **legacy Asset ids** (same contract as the graph endpoint), so the frontend
can navigate by them.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class AlignmentItem(BaseModel):
    dependent_id: uuid.UUID
    dependent_label: str
    prerequisite_id: uuid.UUID
    prerequisite_label: str
    mode: str  # "revision" | "timestamp"
    revision_delta: int | None  # revisions behind (revision mode) else null


class AlignmentOut(BaseModel):
    items: list[AlignmentItem]
