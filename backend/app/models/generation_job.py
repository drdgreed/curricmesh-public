"""GenerationJob — async course-generation job tracking (background runner).

The full-course orchestrator (``app.builder.course_generator.generate_course``)
makes ``1 + 2 * objectives_count`` sequential ~29s AI calls, then commits the
assembled ``DraftCourse`` at the very end. A large brief takes many minutes —
far too long to hold an HTTP request open (the UI spins, and the response can be
lost even though the course succeeded).

``POST /api/v1/builder/generate-course`` therefore creates one
``GenerationJob`` (``status="pending"``), schedules the real work on a FastAPI
``BackgroundTask``, and returns ``202`` + ``job_id`` immediately. The background
runner (``app.builder.generation_runner.run_generation``) marks the job
``running``, updates ``completed_steps``/``phase`` as each step lands (committing
each update so pollers see progress), and finishes either ``complete`` (with
``course_id`` set to the assembled draft) or ``failed`` (with ``error`` set and
no partial course leaked). Clients poll
``GET /api/v1/builder/generate-course/jobs/{job_id}``.

``TenantScoped`` → carries ``organization_id`` (write-stamped from the ambient
org context) and joins the fail-closed RLS regime (registered in
``app/db/rls.py``). The background runner runs OUTSIDE the request, so it must
set the org context on its own session (see ``generation_runner``); the RLS +
app-layer auto-filter then keep every read/write tenant-scoped.

Style mirrors ``app/models/freshness_pipeline.py::PipelineRun`` (a String status
column, no native enum, timezone-aware timestamps).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models._tenant import TenantScoped


class GenerationJob(TenantScoped, Base):
    """One async 'generate a full course from a brief' background run."""

    __tablename__ = "generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # pending | running | complete | failed
    # 1 + 2 * objectives_count — the total work units the runner reports against.
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_steps: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Human-readable label for the current step (e.g. "objectives", "item 3/12").
    phase: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Set only on success — the assembled, mutable DraftCourse the author refines.
    # SET NULL (not CASCADE): deleting the draft must not erase the job's history.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("draft_courses.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Set only on failure — the reason the run failed (no partial course committed).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The author who requested the generation; the status endpoint is owner-scoped.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
