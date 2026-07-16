# CurricMesh — Version Control for Curriculum

**Git, for curriculum.** An immutable, content-addressed version model — append-only content blobs, lightweight curriculum-version manifests with structural sharing, and an executable "merge the PR" release — wrapped in an enforced change-control workflow, a computed dependency cascade, and an AI layer with *measured* quality.

[Live Demo](#live-demo) • [The Problem](#the-problem) • [What It Does](#what-curricmesh-does) • [The Immutable Model](#the-immutable-content-addressed-model) • [Measured AI Quality](#measured-ai-quality) • [Architecture](#architecture) • [Workflow](#the-change-control-workflow) • [Quick Start](#quick-start) • [Demo](#demo-walkthrough) • [About](#about-the-author)

[![Live demo](https://img.shields.io/badge/live%20demo-curricmesh.vercel.app-000000?logo=vercel&logoColor=white)](https://curricmesh.vercel.app)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)
![Tests](https://img.shields.io/badge/tests-418%20backend%20%2B%2067%20frontend-brightgreen)
![AI Eval](https://img.shields.io/badge/AI%20eval-gap%200.714%20%C2%B7%20QA%200.875-blueviolet)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue?logo=python&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-61dafb?logo=react&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-opus--4--8-d97757?logo=anthropic&logoColor=white)
![Data: synthetic](https://img.shields.io/badge/data-synthetic%20only-orange)
![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-lightgrey)

> **Synthetic data only.** Every seed record, the entire industry corpus, and all metrics in this repo are fabricated for portfolio demonstration. No real curriculum, student records, organizational data, or secrets are included. See [SECURITY.md](SECURITY.md).

---

## Live Demo

**▶ [curricmesh.vercel.app](https://curricmesh.vercel.app)** — frontend on Vercel, API on Render.

🎬 **[Watch the 47-second demo](https://github.com/drdgreed/curricmesh-public/blob/main/docs/curricmesh-demo.mp4)** — the full path: login → course browser → dependency graph → alignment → release diff → AI impact analysis → review **approve + merge** (executable release to v1.2.0). No narration needed; the on-screen actions tell the story.

> **Cold start.** The API runs on Render's free tier and sleeps when idle, so the **first** request after a quiet period can take **~30–60 s** to wake. Give the login a moment on the first load; subsequent requests are fast.

**Demo logins** — emails `<role>@careerforge.demo` and `<role>@acme.demo`; password **`demo-pass-123`**. Roles: `architect`, `program_manager`, `instructor_lead`, `instructor`, `qa_lead`, `devops`. The two orgs (Career Forge + Acme) prove tenant isolation — each login sees only its own curriculum. Start as **`architect@careerforge.demo`** and explore the [live demo](https://curricmesh.vercel.app).

The 2-minute path: **Course** (browse the calendar, open an asset) → **Dependency Graph** → **Dashboard** → **Changes** (the `v1.0.0 → v1.1.0` release diff with structural sharing) → **Propose Change** (stage a change, **Analyze impact (AI)**) → **Review** (a seeded mid-review change request: **Approve** to add the 2nd approval, then **Merge** to run the executable release live and watch the new version appear across Course / Graph / Changes).

---

## The Problem

**Technical curriculum is expensive to build, and it expires fast.** Industry benchmarks put the fully-loaded cost of one *finished hour* of e-learning content at roughly **$20,000 on average** — up to $33,000+ for interactive or lab-heavy material — built at **30–40 developer-hours per instructional hour**, and far more for hands-on labs ([bluecarrot.io](https://bluecarrot.io/blog/e-learning-content-development-cost-per-hour-explained/), [elearningart.com](https://elearningart.com/development-calculator/)). A single 12-week technical bootcamp is hundreds of thousands of dollars of investment. Yet the **half-life of the skills it teaches has compressed to 2.5–5 years** — shorter still for AI, cloud, and security, where frameworks and tooling turn over far faster ([skillable.com](https://www.skillable.com/resources/hands-on-learning/half-life-of-skills-is-shortening/)). The asset is costly to produce, costly to keep current, and quietly worthless the moment it falls behind the field.

**When curriculum falls behind, the cost lands on the graduate — at the worst possible time.** The entry-level technical market of 2025–2026 is brutal for candidates whose skills are even slightly off-target: recent-graduate **underemployment hit 42.5%** (Q4 2025) and US **software-engineer job postings are down ~49%** from early 2020, with **junior** dev/data postings down **up to 67%** since 2023 ([understandingai.org](https://www.understandingai.org/p/new-evidence-strongly-suggest-ai), [restofworld.org](https://restofworld.org/2025/engineering-graduates-ai-job-losses/)). Meanwhile **70% of hiring managers** believe AI can already do intern-level work and **57% trust AI output over a recent grad's** ([stackoverflow.blog](https://stackoverflow.blog/2025/12/26/ai-vs-gen-z/)) — making junior candidates with stale or generic skills the most automatable and least differentiated applicants in the pool. The gap between what a curriculum teaches and what the field hires for is the difference between a callback and silence.

**And staying current is hard to do *safely*.** Curriculum is a dependency graph, not a folder of files: change a Week-3 learning objective and the matching assessment, rubric, slides, and downstream project quietly fall out of alignment. Teams manage this in spreadsheets and shared docs — no enforced review, no version history, no answer to *"which version is this cohort running?"* or *"where have we fallen behind the field?"* So curriculum is too often left stale rather than risk breaking it.

We wrote a **white paper** — *The Curriculum Versioning Framework & Repository Design* (116 pp; proprietary, not included in this public mirror) — that specifies the solution: apply **software version-control discipline** (semantic versioning, change requests, gated QA/approval, dependency rules) to curriculum. The white paper designs the rules. It does not enforce them.

**CurricMesh is the implementation.** It turns that white paper into a database-backed, authenticated, role-based application that *enforces* the workflow, *computes* the dependency cascade the paper only specifies in prose, *diffs* curriculum versions, and adds an AI layer that compares a curriculum to a curated snapshot of the field's state of the art, drafts the change requests needed to close the gap — and then **measures its own accuracy against planted ground truth.**

---

## What CurricMesh Does

- **Versions curriculum like git.** An **immutable, content-addressed model** (below): append-only content blobs addressed by sha256 `content_hash`, lightweight per-version manifests with **structural sharing**, and an **executable `fork()` release** — "merge a change like a PR" in O(changes) instead of deep-copy-and-remap.
- **Enforces a change-control workflow.** A Change Control Request (CCR) carries a structured, executable change-set and must clear a six-dimension QA review and a two-approver release gate before it can **Merge**. The gate is enforced **in the engine, not the UI** — you cannot click around it; **Merge** replays the change-set through `fork()` to produce + activate a new immutable version.
- **Computes the dependency cascade + precise staleness.** A cycle-safe BFS engine answers "what else breaks if I touch this?"; `validated_against_seq` edge provenance upgrades alignment from a timestamp guess to a **revision-delta** ("the prerequisite has advanced N revisions since this dependency was validated"), with graceful timestamp fallback.
- **Diffs versions — asset-level and release-level.** Prose diffs with `difflib`; rubrics and learning objectives diff as structured JSON; the **Changes** page shows a GitHub-PR-style **release diff** (added / removed / changed / edge-delta) between a version and its parent.
- **Estimates change impact with AI (advisory).** At authoring time, Claude estimates a change-set's impact on **learning objectives**, **instructional duration**, and **student cognitive load** — behind the same mockable seam, advisory only (graceful "not configured" notice when no API key is set).
- **Authors and publishes new courses with an AI co-pilot.** A **Course Builder** (left nav) walks an author from objectives → quick-capture content (auto-categorized by kind/effort/objective) → weekly-load warnings, then **publishes** the draft as a versioned curriculum (v1.0.0 in the immutable model). A right-rail **AI co-pilot** offers andragogy-grounded guidance ("Get AI guidance"), infers prerequisite edges ("Suggest prerequisites"), and per-item AI categorization — all advisory, cycle-safe, never published until accepted. 503-graceful when no API key is set.
- **Observes AI spend.** Every AI call is logged with tokens, latency, and **USD cost** (from model pricing), persisted to a durable `ai_call_events` table (survives restarts). A staff-gated endpoint `GET /api/v1/internal/ai-usage` surfaces in-process + per-org totals; Architect/Program-Manager users see a **14-day cost sparkline** tile on the Dashboard.
- **Browses the course + dependency graph.** A calendar/course view with an asset drawer (content, source link, prerequisites, revision history), a `reactflow` dependency graph, dark mode, and a branded login.
- **Models the full lifecycle.** Versions move through a state machine — `draft → review → approved → active → archived → sunset` — with server-derived cohort state and an audit timeline.
- **Adds an AI layer with three jobs:**
  - **SOTA-gap researcher** reads a curated synthetic industry corpus (40 entries: 20 job postings + 20 vendor docs) plus the curriculum's covered topics, and **drafts CCRs** for under-covered topics — authored by a system "AI Researcher" actor. Drafts enter the *normal* QA/approval flow and **never bypass the gate**. A **live SOTA-research adapter** (Anthropic `web_search` server tool) sits behind this curated default: set `LIVE_SOTA_ENABLED=true` (with `ANTHROPIC_API_KEY`) and trigger research with `?live=true` to pull current (2026+) job-market signal — persisted as `kind="live_search"` corpus rows with source provenance. Default stays curated, so CI and the demo run offline.
  - **LLM-as-judge QA** pre-scores the six QA dimensions (1–5 with evidence) as an **advisory draft** that *can never satisfy the release gate*. A human QA Lead reviews and promotes it.
  - **Evaluation harness** measures the AI against ground truth and publishes the numbers below.
- **Notifies on commit.** Slack/email notifications fire after commit, non-blocking, surface-don't-swallow.

---

## The Immutable, Content-Addressed Model

The interesting part of CurricMesh is its data model. Most "versioned content" systems copy the whole tree on every version and renumber every identity — so adding one asset on release is an expensive deep-copy-and-remap. CurricMesh instead borrows git's object model, so a release is a cheap, structural fork.

| git | CurricMesh |
|---|---|
| blob (immutable, addressed by hash) | **`ContentVersion`** — append-only content, sha256 `content_hash`, monotonic `seq` per asset |
| file path / lineage | **`LineageAsset`** — a stable logical asset (kind + key), version-independent |
| tree / commit | **`CurriculumVersion` + manifest** — a lightweight snapshot |
| structural sharing | **`VersionMember`**s point at *shared* `ContentVersion`s by reference |
| commit parent | `CurriculumVersion.parent_version_id` |
| non-fast-forward rejection | optimistic compare-and-swap on the active pointer at release |

Three properties fall out of this, **by construction** rather than by validation:

- **Immutability.** `ContentVersion`s are append-only — a SQLAlchemy `before_update` guard *refuses* any UPDATE; a new revision is a new row. A released manifest is frozen.
- **Structural sharing.** A fork that changes 1 of 41 assets writes exactly **1** new `ContentVersion` (+ small member/edge pointer rows); the other 40 are *referenced, not copied*. Identical content even deduplicates by `content_hash`.
- **No identity remapping.** `VersionEdge`s reference *logical* `LineageAsset` ids (stable across content changes), so prerequisites never need remapping when content moves — and `validated_against_seq` records the exact prerequisite revision a dependency was last validated against, for precise revision-delta staleness.

**The whole release surface is one function — `fork(curriculum, bump, changes)`** — which is why a rigorous suite (fork invariants, structural-sharing row-count assertions, content-hash `fsck`, property-based fuzzing, concurrency CAS) can cover it. It runs inside a single SAVEPOINT, validates acyclicity + referential validity + placement, then activates via an optimistic compare-and-swap (fail-closed rollback).

```mermaid
flowchart TD
    subgraph V1["CurriculumVersion v1.0.0 (manifest)"]
        M1["VersionMember → C1"]
        M2["VersionMember → C2"]
        M3["VersionMember → C3"]
    end

    subgraph V2["CurriculumVersion v1.1.0 = fork(minor, {change A, add D})"]
        N1["VersionMember → C1  (shared)"]
        N2["VersionMember → C2' (new revision)"]
        N3["VersionMember → C3  (shared)"]
        N4["VersionMember → C4  (new asset)"]
    end

    C1["ContentVersion C1  seq1"]
    C2["ContentVersion C2  seq1"]
    C2b["ContentVersion C2' seq2  (only new content row)"]
    C3["ContentVersion C3  seq1"]
    C4["ContentVersion C4  seq1  (new asset)"]

    M1 --> C1
    M2 --> C2
    M3 --> C3
    N1 --> C1
    N2 --> C2b
    N3 --> C3
    N4 --> C4

    V1 -. parent_version_id .-> V2
```

> A `v1.1.0` that changes one asset and adds one writes **two** new content rows (`C2'`, `C4`) — everything else is shared by reference. The "Changes" page renders exactly this delta. See `docs/specs/2026-06-06-immutable-version-model-design.md` and `backend/app/core/fork.py`.

---

## Measured AI Quality

Most AI portfolio projects *claim* their model is good. CurricMesh **measures it** — in CI, against planted ground truth — and publishes the report as a build artifact. This is the differentiator.

The harness scores two things: **gap-detection precision/recall**, now aggregated across **two** curricula with deliberately planted gaps, and **QA-judge agreement** against a rater consensus (within ±1 on a 1–5 scale) — reported against a **human inter-rater baseline** so the AI's agreement has a yardstick.

| Metric | Value | What it means |
|---|---|---|
| **Gap detection — precision (aggregate)** | **0.714** | Micro-averaged across 2 curricula: 5 of 7 surfaced gaps are real (TP=5, FP=2). |
| **Gap detection — recall (aggregate)** | **0.833** | Finds 5 of 6 planted gaps across both curricula (TP=5, FN=1). |
| ↳ per-curriculum | **0.667 / 0.667 · 0.750 / 1.000** | *Agentic AI Architecture in Production* (P/R) · *Cloud Platform Engineering* (P/R). |
| ↳ recall on *critical-signal* gaps | **1.000** | Catches **every** gap that saturates a corpus. |
| ↳ recall on *moderate-signal* gaps | **0.500** | Catches one of two low-signal gaps; honestly misses the other. |
| **QA-judge agreement (AI vs consensus)** | **0.875** | 21 of 24 dimension scores land within ±1 of the rater consensus (quadratic-weighted κ = 0.390). |
| **Human inter-rater baseline** | **0.861** | The synthetic raters agree with *each other* within ±1 this often (pairwise weighted κ = 0.277). |

**The AI agrees with the consensus about as well as the humans agree with each other.** QA-judge agreement (0.875 within ±1) edges out the human inter-rater baseline (0.861), and on the chance-corrected, ordinal-appropriate **quadratic-weighted κ** the AI (0.390) actually exceeds the human inter-rater κ (0.277). That's the credible "at or above the human-agreement ceiling" framing — with the honest caveat that the raters are **synthetic** and this remains a benchmark, not production.

**Why recall isn't 1.0 — and why that's the point.** Aggregate recall is 0.833 because gaps are planted at varied signal strength across both curricula. Every *critical* gap (saturating its corpus) is caught — critical recall is 1.000. The *moderate* gaps are a real but non-dominant signal: the researcher now surfaces the moderate gap in *Cloud Platform Engineering* but still ranks *Agentic AI*'s moderate gap below threshold, leaving moderate recall at 0.500. Reporting honest recall at lower signal is the senior move: a detector that claimed 1.0 here would be overfit to its own answer key.

The project also ships a **DeepEval-based semantic eval** that grades the AI co-pilot's advisory free-text on andragogy-groundedness, actionability, non-hallucination, conciseness, advisory-framing, and a DAG metric — real-output capture with a regression baseline. This is an on-demand, non-CI complement to the deterministic snapshot-replay eval (which stays the CI gate). And every live AI call is captured with **per-call cost and latency observability** — so quality is graded and cost is measured, not asserted.

**Reproduce it** (no API key, no database, deterministic — runs against recorded snapshots):

```bash
cd backend && python -m app.ai.eval.run_eval
```

It runs as a **non-blocking CI job** that publishes a markdown report artifact. CI is deterministic via recorded snapshots; set `ANTHROPIC_API_KEY` to run it live against Claude.

---

## Architecture

React/MUI talks to thin FastAPI routers; all the real logic lives in pure engines and the AI layer, which sit in front of PostgreSQL. Claude and Slack/email are at the edges.

**Read path.** Graph, course, dashboard, diff, and alignment all read through one shared pure layer — `app/core/manifest.py` — over the immutable model (`curriculum_versions` / `version_members` / `version_edges` / `content_versions` / `lineage_assets`). The read path was cut over from the legacy structure via a strangler migration, each endpoint backed by a golden-output equivalence test (old vs new path produce identical graph/diff/alignment for the seeded data).

```mermaid
flowchart LR
    subgraph FE["Frontend — React 19 + MUI + Vite"]
        UI["Dashboard · Dependency Graph (reactflow)<br/>Version Diff · AI Findings Inbox"]
    end

    subgraph API["FastAPI — thin routers"]
        R["auth · curricula · ccr · qa<br/>approvals · release · ai"]
    end

    subgraph CORE["Core Engines (pure)"]
        WF["Workflow + Release Gate"]
        CAS["Dependency Cascade (BFS)"]
        DIFF["Version Diff"]
        LC["Lifecycle State Machine"]
    end

    subgraph AI["AI Layer (mockable Protocol seam)"]
        RES["SOTA-gap Researcher"]
        JUDGE["LLM-as-judge QA"]
        EVAL["Eval Harness"]
    end

    DB[("PostgreSQL 16<br/>SQLAlchemy 2.0 async")]
    CLAUDE["Claude API<br/>claude-opus-4-8"]
    NOTIFY["Slack / Email"]

    UI -->|React Query / client.ts| R
    R --> WF & CAS & DIFF & LC
    R --> RES & JUDGE & EVAL
    WF & CAS & DIFF & LC --> DB
    RES & JUDGE & EVAL --> DB
    RES -.-> CLAUDE
    JUDGE -.-> CLAUDE
    WF -. after commit .-> NOTIFY
```

The AI sits *behind a mockable Protocol seam*, so CI is deterministic and offline; flip in `ANTHROPIC_API_KEY` for live runs.

---

## The Change-Control Workflow

A version cannot go live by accident. A CCR is authored (by a human **or** the AI Researcher), runs a six-dimension QA review, then clears a two-approver release gate before the lifecycle machine promotes it to `active`.

**The six QA dimensions:** `content_accuracy`, `alignment`, `prerequisites`, `consistency`, `instructor_support`, `student_experience` — each scored 1–5 with evidence.

**The release gate** (enforced atomically in the engine) requires:
1. at least one QA review with a **passing** verdict, **and**
2. **≥2 approvals from distinct users**, of which **≥1 is an instructor** (`instructor` or `instructor_lead`),
3. with **no author self-approval** and **no duplicate approvers**.

The AI-QA judge writes a **sentinel verdict that can never satisfy rule 1** — its pre-score is advisory until a human QA Lead promotes it.

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> review: submit CCR + QA
    review --> draft: QA bounce-back
    review --> approved: QA pass + 2 approvals<br/>(>=1 instructor, no self-approval)
    approved --> active: release (atomic gate)
    active --> archived: superseded by new active
    archived --> sunset: retire
    sunset --> [*]
```

The release path uses `SELECT ... FOR UPDATE` plus an idempotency guard, so a double-fire can't double-activate, and a `UniqueConstraint` makes duplicate approvals impossible at the database level.

---

## Engineering Integrity

The senior signal isn't the feature list — it's *where the rules are enforced*:

- **The gate lives in the engine, not the UI.** `can_release` is the single source of truth; the frontend only reflects it.
- **The AI can never auto-pass.** The judge emits a sentinel verdict, and the gate filters on `"pass"` only — advisory AI scores are structurally incapable of releasing a version.
- **Constant-time auth** on login avoids email-enumeration timing leaks.
- **Atomic release.** `SELECT FOR UPDATE` + an idempotency guard means no double activation under concurrent fire.
- **A `UniqueConstraint`** prevents duplicate approvals; **author self-approval is rejected** explicitly.
- **Cohort state is server-derived** — never trusted from the client.
- **CI verifies the Alembic migration chain round-trips** (`upgrade → downgrade`), runs backend `pytest` against a **real PostgreSQL 16**, and runs `npm ci` against a Linux-generated lockfile behind an `npm audit` high/critical gate.
- **CI tokens are read-only** (`contents: read`); the AI-eval job is `continue-on-error` so a measurement run can never fail the build.

### Multi-tenant isolation (two layers, DB-enforced + app-enforced)

Tenant isolation is enforced in **two independent layers**, so it holds even if either one is bypassed:

- **Layer 1 — Postgres Row-Level Security.** The **20 tenant-scoped tables** carry `FORCE ROW LEVEL SECURITY` with fail-closed policies keyed on a per-transaction `app.current_org` GUC. For any **non-superuser** DB role, the database itself rejects cross-tenant rows: cross-tenant reads return nothing and cross-tenant writes fail with `SQLSTATE 42501`. Tests prove this by dropping to a throwaway `NOSUPERUSER` role inside the transaction and asserting exactly that.
- **Layer 2 — application-layer auto-filter.** A SQLAlchemy `do_orm_execute` hook plus a `TenantScoped` mixin inject `with_loader_criteria(... organization_id == current_org)` into **every ORM read**, scoping it to the request's org **independent of the DB role**. This is the load-bearing layer in the demo (see caveat below). It is proven end-to-end: a JWT scoped to org A gets **404** for org B's curriculum.
- **Context flow.** Tenant context comes from the JWT `org` claim → a `current_org` `ContextVar`, which both write-stamps new rows (`organization_id` column default → fail-closed if unset) and drives the RLS GUC via `SELECT set_config('app.current_org', …)`.
- **Honest caveat.** RLS only enforces for a **non-superuser, non-`BYPASSRLS`** role; the dev/demo default connects as a **superuser, which bypasses RLS even under `FORCE ROW LEVEL SECURITY`**. So in the demo the **app-layer filter (Layer 2) is the load-bearing layer** — and it is itself proven by the end-to-end 404 tests. Production isolation requires connecting as a least-privilege role so Layer 1 also engages. (See `docs/AGENT_LESSONS.md` P-001.)

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python 3.11) |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Auth | JWT (PyJWT) + bcrypt (passlib), constant-time login |
| AI | Anthropic Claude (`claude-opus-4-8`), adaptive thinking, structured outputs, behind a mockable Protocol seam |
| Frontend | React 19 + Material UI + Vite + React Query + react-router |
| Graph viz | reactflow (dependency graph) |
| DB driver | asyncpg (app) · psycopg2 (Alembic) |
| Testing (backend) | pytest + pytest-asyncio (~418 tests, real PostgreSQL) |
| Testing (frontend) | Vitest (67 tests) |
| CI | GitHub Actions (backend · ai-eval · frontend) |
| Notifications | Slack / email (post-commit, non-blocking) |

---

## Quick Start

### Backend

```bash
git clone https://github.com/drdgreed/curricmesh-public.git
cd curricmesh

# Start Postgres (docker-compose maps it to localhost:5432)
docker-compose up -d postgres

cd backend
python -m venv venv
source venv/bin/activate                 # Windows: venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env
# .env.example points DATABASE_URL at localhost:5432 (user/db: curricmesh) — the docker-compose default; no edits needed.
# Non-docker local dev: edit backend/.env and set DATABASE_URL (and DATABASE_URL_SYNC for Alembic) to your
# local Postgres port, e.g. postgresql+asyncpg://curricmesh:curricmesh@localhost:5433/curricmesh.

# Run seeds/migrations with the venv interpreter (system/conda python can trip a passlib/bcrypt mismatch).
venv/bin/python -m alembic upgrade head
venv/bin/python -m seed.bootcamp_curriculum   # two demo orgs (Career Forge + Acme), users, curricula, cohorts,
                                              # back-fill of the immutable model, a v1.1.0 release, and a mid-review CCR
venv/bin/python -m seed.load_sota             # synthetic SOTA corpus (powers the AI researcher / AI Inbox)

uvicorn app.main:app --reload
# API at http://localhost:8000  ·  interactive docs at http://localhost:8000/docs
```

### Frontend

```bash
cd curricmesh/frontend
npm install
npx vite                                  # dev server on http://localhost:3000 (same as `npm run dev`)
# The frontend reads VITE_API_URL (defaults to http://localhost:8000/api/v1).
# Point it elsewhere with: VITE_API_URL=http://localhost:8000/api/v1 npx vite
```

### AI eval (optional, deterministic, no key needed)

```bash
cd backend && venv/bin/python -m app.ai.eval.run_eval
```

> **The AI features need an `ANTHROPIC_API_KEY`** (in `backend/.env`, gitignored) to run live. **The app and the entire test suite run fully without it** — the AI sits behind a mockable Protocol seam, and the eval harness replays recorded snapshots.

### Demo Logins

The seed loads **two organizations** so the demo visibly shows tenant isolation: **logging in as each org's users shows only that org's data.** All demo accounts share the password `demo-pass-123`. Email login is **case-insensitive and whitespace-tolerant** (normalized server-side).

**Org A — Career Forge** (`<role>@careerforge.demo`) · the full 12-week "Agentic AI Architecture in Production" bootcamp:

| Email | Role |
|---|---|
| `architect@careerforge.demo` | architect |
| `program_manager@careerforge.demo` | program_manager |
| `instructor_lead@careerforge.demo` | instructor_lead |
| `instructor@careerforge.demo` | instructor |
| `qa_lead@careerforge.demo` | qa_lead |
| `devops@careerforge.demo` | devops |

**Org B — Acme Academy** (`<role>@acme.demo`) · a lightweight "Cloud Data Engineering Essentials" curriculum:

| Email | Role |
|---|---|
| `architect@acme.demo` | architect |
| `program_manager@acme.demo` | program_manager |
| `instructor_lead@acme.demo` | instructor_lead |
| `instructor@acme.demo` | instructor |
| `qa_lead@acme.demo` | qa_lead |
| `devops@acme.demo` | devops |

The seed loads, per org, **6 role users + a curriculum on version 1.0.0 + an active cohort**: Org A gets the 12-week bootcamp (4 projects, 41 assets, Career Forge Cohort 2026-Q2); Org B gets a trimmed 4-module curriculum (2 projects, 16 assets, Acme Cohort 2026-Spring). Each login's dashboard is scoped to its own org — `architect@careerforge.demo` sees only `agentic-ai`, `architect@acme.demo` sees only `cloud-data-eng`.

---

## Demo Walkthrough

> For the **hosted demo's** 2-minute click path (Course → Graph → Dashboard → Changes → Propose → **Review → Merge**, running an executable release live), see the **[live demo](https://curricmesh.vercel.app)**. The story below is the complementary AI-research-centric flow.

The end-to-end story a recruiter can follow — *AI proposes, humans dispose, and the gate holds.*

1. **Trigger SOTA research.** The AI Researcher reads the synthetic corpus and the bootcamp's covered topics and reports under-covered areas — e.g. *Model Context Protocol* and *agent observability / distributed tracing* — then **drafts CCRs** to close them, authored by the system "AI Researcher" actor.
2. **AI-QA pre-scores the drafts.** The LLM-as-judge scores all six QA dimensions (1–5 with evidence) as an **advisory draft** carrying a sentinel verdict — it cannot satisfy the gate on its own.
3. **A human QA Lead reviews and promotes.** Log in as `qa_lead@careerforge.demo`, open the AI Findings Inbox, review the pre-scores, and submit a passing QA review.
4. **Two approvals clear the gate.** Approve as `instructor_lead`, then as `program_manager`. The engine rejects self-approval and duplicate approvers; the gate requires ≥1 instructor.
5. **Release.** Trigger the release; the version transitions to `active`, `current_version_id` updates, and a `version_active` event lands in the History timeline.
6. **Check the scoreboard.** Run `venv/bin/python -m app.ai.eval.run_eval` to see the AI graded against ground truth: gap precision/recall and QA-judge agreement.

---

## Screenshots

The four screens below render with **synthetic demo data** after you run the [Quick Start](#quick-start).

### Dashboard

The active cohort and its live version, recent CCRs and their lifecycle status, and the audit timeline of `version_active` / QA / approval events.

![CurricMesh dashboard — the active curriculum with its archived and live versions and a live cohort](docs/assets/dashboard.png)

### Dependency Graph

A `reactflow` view of the curriculum's asset graph; selecting a node highlights the downstream assets a change would cascade into and the version bumps it forces.

![CurricMesh dependency graph — reactflow asset nodes with version and lifecycle status](docs/assets/graph.png)

### Version Diff

A diff of two asset versions: prose changes via `difflib`, rubrics and learning objectives as a structured JSON diff.

![CurricMesh version diff — unified text diff between two asset versions with From/To pickers](docs/assets/diff.png)

### AI Findings Inbox

The AI-drafted CCRs with the judge's six-dimension pre-scores and evidence, where a QA Lead reviews, edits, and promotes a finding into the human-gated workflow.

![CurricMesh AI Findings Inbox — AI-drafted change requests with coverage status and citations](docs/assets/ai-inbox.png)

---

## Project Layout

```
backend/
  app/
    main.py                 # FastAPI app — the full router list = the feature surface
    core/
      fork.py               # the fork() primitive — executable, O(changes) release
      manifest.py           # pure read layer over the immutable manifest (+ staleness)
      content_hash.py       # sha256 content addressing
      workflow/             # CCR submit, can_release gate, release_ccr
      diff/ · version_diff.py · cascade/   # asset & release diff, dependency cascade
    models/content_model.py # the immutable model: LineageAsset / ContentVersion /
                            #   CurriculumVersion / VersionMember / VersionEdge
    db/rls.py · database.py · tenant_scope.py   # two-layer multi-tenancy (RLS + app filter)
    ai/                     # impact · qa_judge · sota_researcher · eval harness (seam)
    routers/                # thin HTTP routers (releases, approvals/merge, impact, course, …)
  seed/bootcamp_curriculum.py   # two-org demo seed (+ back-fill, v1.1.0, mid-review CCR)
  alembic/versions/         # migration chain (from-base, CI-verified round-trip)
  tests/                    # ~418 tests: fork, golden equivalence, integration, impact, AI eval
frontend/
  src/pages/                # Dashboard · Course · Graph · Changes · AuthorChange ·
                            #   Review · AIInbox · Analytics · History · Diff · Login
  src/components/Layout.tsx # the nav = the user-facing feature map
  src/api/client.ts         # axios client (VITE_API_URL)
docs/                       # demo video + UI screenshots (internal design docs omitted from this mirror)
```

---

## Project Docs

This repository is a **public portfolio mirror**. The full internal design artifacts — PRDs, design specs, implementation plans, the white paper, and operational runbooks — and the production curriculum content are proprietary and intentionally omitted here. What remains is the engineering: the immutable versioning core, the change-control workflow, the retrieval/tutor layer, and the measured-AI harness — all running on synthetic data.

- [Synthetic SOTA Corpus](backend/seed/sota_corpus/README.md) — the fabricated industry corpus the gap-detection eval runs against.

---

## About the Author

**David Reed, Ph.D.** — Head of AI/ML & Agentic Delivery at Interview Kickstart. PhD in Computer Science, MBA, PMP, Wharton AI Fellow. Sole inventor of [US Patent 6,850,988](https://patents.google.com/patent/US6850988) — the foundational recommendation-engine architecture later widely deployed in commerce. Formerly Master Technologist at Hewlett-Packard and Principal TPM-AI at Microsoft. 35+ years across enterprise AI/ML and edtech, including leading a $70M data-science curriculum portfolio across R1 universities.

I built CurricMesh to demonstrate end-to-end engineering on a problem I know firsthand from running instruction at scale: **keeping technical curriculum current is a version-control problem, not a documentation problem.** The interesting work is in making the rules *enforceable* — the release gate lives in the engine, the AI can never auto-pass, and the AI layer is held to a number measured against planted ground truth rather than asserted in prose.

[Portfolio](https://drdavidreed.com) · [LinkedIn](https://linkedin.com/in/drdgreed) · drdgreed@gmail.com

---

## Contributing

Setup, branching, and the PR workflow are in [CONTRIBUTING.md](CONTRIBUTING.md). Issues use the templates under [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/); for security reports follow [SECURITY.md](SECURITY.md) rather than opening a public issue. By contributing you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) — Copyright (c) 2026 David Reed.

---

**CurricMesh** — Version Control for Curriculum
*David Reed, PhD · 2026 · synthetic data only*
