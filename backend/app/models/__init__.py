"""
Import all models so that Base.metadata is fully populated by a single
``import app.models``.

The import order matters for the circular FK between Curriculum and Version:
- Version must be defined before Curriculum so its table exists when
  Curriculum's use_alter FK is registered.
"""

from app.models.enums import AssetKind, LifecycleStatus  # noqa: F401
from app.models.org import Organization  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.version import Version  # noqa: F401
from app.models.curriculum import Curriculum  # noqa: F401
from app.models.structure import Asset, AssetVersion, Module, Project  # noqa: F401
from app.models.graph import DependencyEdge  # noqa: F401
from app.models.workflow import Approval, ChangeRequest, QAReview  # noqa: F401
from app.models.cohort import Cohort  # noqa: F401
from app.models.version_pin import VersionPin  # noqa: F401
from app.models.history import HistoryEvent  # noqa: F401
from app.models.sota import SotaFinding, SotaSource  # noqa: F401
from app.models.sync import SyncLog, SyncTarget  # noqa: F401
from app.models.freshness_pipeline import (  # noqa: F401
    GapAssessment,
    PipelineRun,
    PipelineSeen,
    SourceWatchItem,
    SyllabusSnapshot,
)

# Global AI-call telemetry (NOT tenant-scoped, NOT RLS'd) — persisted per-call
# usage records written best-effort off the AI hot path.
from app.models.ai_usage import AICallEvent  # noqa: F401

# Foundation: immutable, content-addressed version model (additive; nothing
# reads these yet — they sit alongside the legacy structure model).
from app.models.content_model import (  # noqa: F401
    ContentVersion,
    CurriculumVersion,
    LineageAsset,
    VersionEdge,
    VersionMember,
)

# Course Builder: mutable *draft* authoring model (additive; compiled into the
# immutable model on demand — nothing in the immutable read path reads these).
from app.builder import models as builder_models  # noqa: F401

# Authoring media (slice 1): tenant-scoped owned-media asset registry.
# Phase B (B2): MediaTranscript — extracted/transcribed text per asset.
from app.models.media import MediaAsset, MediaTranscript  # noqa: F401

# Learner delivery (Phase 2, Foundation 1): self-paced enrollment + progress +
# assessment submissions over the released, immutable content model.
from app.models.learner import (  # noqa: F401
    AssessmentSubmission,
    Enrollment,
    LearnerProgress,
)

# Retrieval infra (Phase B, Foundation 1): version-pinned embeddable chunks.
from app.models.retrieval import ContentChunk  # noqa: F401

# Tutor (Phase B, B3): RAG Q&A conversation store (D5 secure server-side record).
from app.models.tutor import (  # noqa: F401
    TutorConversation,
    TutorMessage,
)
# Async course generation: background-job tracking for POST /generate-course.
from app.models.generation_job import GenerationJob  # noqa: F401

# Slide System Port (S4): links a rendered deck's R2 artifacts to a released
# CurriculumVersion so the Player can serve them.
from app.models.deck_artifact import DeckArtifact  # noqa: F401
