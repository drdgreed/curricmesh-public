"""Shared enumerations for CurricMesh domain models."""

import enum


class LifecycleStatus(str, enum.Enum):
    draft = "draft"
    review = "review"
    approved = "approved"
    active = "active"
    archived = "archived"
    sunset = "sunset"


class AssetKind(str, enum.Enum):
    lesson_plan = "lesson_plan"
    slides = "slides"
    assessment = "assessment"
    rubric = "rubric"
    lab = "lab"
    spec = "spec"
    starter = "starter"
    references = "references"
    learning_objectives = "learning_objectives"
    # NOTE: `project` is a valid CCR `affected_kinds` value. No Alembic migration
    # is required to add it because `affected_kinds` is CCR metadata only — it
    # flows into the ChangeRequest `impact` JSONB (set in submit_ccr) and is never
    # written to the native `assetkind` PG enum column (`asset.kind`). The PG enum
    # type is therefore untouched. If a future change DOES persist `affected_kinds`
    # into a native-enum column, add `ALTER TYPE assetkind ADD VALUE 'project'`.
    project = "project"
