from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api_errors import register_exception_handlers
from app.auth.rbac import tenant_context
from app.config import settings
from app.routers import auth
from app.routers import curricula, versions, assets, ccr, qa, approvals, dashboard, graph, diff, research, ai_inbox, analytics, pins, sync, course, releases, alignment, version_diff, impact, ai_usage, enrich, freshness_watchlist, freshness_assessments, media, media_transcription, authoring_ai, learn, retrieval_admin, tutor, slides
from app.builder import router_advisor, router_course, router_publish

# Every authenticated DOMAIN router runs tenant_context as a router-level
# dependency — it binds the JWT's `org` claim to the current_org ContextVar
# BEFORE the endpoint's get_db reads it to set the Postgres RLS GUC. Auth
# (login is pre-auth, cross-tenant by nature) and /health are deliberately
# excluded.
_TENANT = [Depends(tenant_context)]

app = FastAPI(title="CurricMesh API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Domain exception → HTTP mapping (WorkflowError→400, PermissionDenied→403, IllegalTransition→409)
register_exception_handlers(app)

# Auth (existing)
app.include_router(auth.router)

# A8 routers — tenant-scoped (RLS) via the router-level tenant_context dependency
app.include_router(curricula.router, dependencies=_TENANT)
app.include_router(versions.router, dependencies=_TENANT)
app.include_router(assets.router, dependencies=_TENANT)
app.include_router(ccr.router, dependencies=_TENANT)
app.include_router(qa.router, dependencies=_TENANT)
app.include_router(approvals.router, dependencies=_TENANT)
app.include_router(dashboard.router, dependencies=_TENANT)
app.include_router(graph.router, dependencies=_TENANT)
app.include_router(diff.router, dependencies=_TENANT)

# Feature A: course-content browser (calendar/course view + asset detail)
app.include_router(course.router, dependencies=_TENANT)

# Phase C: executable release (fork() wired into a release endpoint)
app.include_router(releases.router, dependencies=_TENANT)

# Course Builder: DraftCourse + DraftObjective CRUD (mutable authoring surface)
app.include_router(router_course.router, dependencies=_TENANT)
# Course Builder: effort estimator + overload/density detector
app.include_router(router_publish.router, dependencies=_TENANT)
# Course Builder: AI co-pilot — item categorizer + andragogy advisor (Phase 2)
app.include_router(router_advisor.router, dependencies=_TENANT)
# Authoring Platform slice 3: per-aspect AI generators (objectives / item content
# / assessment) — advisory drafts, governed via AIClient
app.include_router(authoring_ai.router, dependencies=_TENANT)

# Precise staleness + release diff (immutable version model, §3.1)
app.include_router(alignment.router, dependencies=_TENANT)
app.include_router(version_diff.router, dependencies=_TENANT)

# C2: SOTA-gap researcher agent
app.include_router(research.router, dependencies=_TENANT)

# Enriched gap proposal: placement + draft frame on a gap CCR
app.include_router(enrich.router, dependencies=_TENANT)

# C5: AI-findings inbox
app.include_router(ai_inbox.router, dependencies=_TENANT)

# Milestone B: AI CCR-impact guidance (authoring-time impact estimate, advisory)
app.include_router(impact.router, dependencies=_TENANT)

# V3-A: change-velocity & time-in-state analytics (read-only, org-scoped)
app.include_router(analytics.router, dependencies=_TENANT)

# V3-B: student-portfolio version-pinning (two prefixes, same tenant group)
app.include_router(pins.router, dependencies=_TENANT)
app.include_router(pins.curriculum_router, dependencies=_TENANT)

# V3-C: external sync adapters (GitHub / LMS), simulated by default
app.include_router(sync.router, dependencies=_TENANT)

# Eval/observability: live in-process telemetry + a durable org-scoped persisted
# block (ai_call_events). Now reads the request DB session, so it needs the
# tenant_context dependency (sets current_org for get_db). Staff-gated.
app.include_router(ai_usage.router, dependencies=_TENANT)

# Freshness pipeline: watchlist CRUD (university syllabus pages to monitor)
app.include_router(freshness_watchlist.router, dependencies=_TENANT)

# Freshness pipeline: gap assessments (Monitor Queue) — Phase 2 Judge
app.include_router(freshness_assessments.router, dependencies=_TENANT)

# Authoring media subsystem: presigned direct-to-storage upload + confirm
app.include_router(media.router, dependencies=_TENANT)
# Phase B (B2): media transcription pipeline trigger.
app.include_router(media_transcription.router, dependencies=_TENANT)

# Phase 2, Foundation 1: learner delivery (self-paced) — catalog / enroll /
# course structure w/ presigned media / progress / submit. Learner-role gated.
app.include_router(learn.router, dependencies=_TENANT)

# Retrieval infra (Phase B): admin trigger to (re)build a version's index
app.include_router(retrieval_admin.router, dependencies=_TENANT)

# Phase B (B3): RAG Q&A tutor — ask + conversation history. Learner-role gated,
# enrollment-scoped; grounding gate + D5 anonymization live in core.tutor.answer.
app.include_router(tutor.router, dependencies=_TENANT)

# Slide System Port (S1): render a deck.md → PDF/PPTX/HTML and store in R2.
# Render + store only (no AI generation, no player UI — later slices).
app.include_router(slides.router, dependencies=_TENANT)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
