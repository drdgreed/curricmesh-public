# Contributing to CurricMesh

Thanks for your interest. This document covers local setup, branching, and the pull-request workflow.

## Local setup

```bash
git clone https://github.com/drdgreed/curricmesh-public.git
cd curricmesh

# Start Postgres (docker-compose maps it to localhost:5432)
docker-compose up -d postgres

# Backend
cd backend
python -m venv venv
source venv/bin/activate                 # Windows: venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                      # DATABASE_URL + optional ANTHROPIC_API_KEY

alembic upgrade head
python -m seed.bootcamp_curriculum        # demo org, users, bootcamp, active cohort
python -m seed.load_sota                  # synthetic SOTA corpus (powers the AI researcher)

uvicorn app.main:app --reload             # API at http://localhost:8000, docs at /docs

# Frontend (separate terminal)
cd ../frontend && npm install && npm run dev   # UI at http://localhost:3000
```

You need a local **PostgreSQL 16** and (only for **live** AI features) an [Anthropic API key](https://console.anthropic.com/). The app and the full test suite run **without** a key — the AI sits behind a mockable Protocol seam and the eval harness replays recorded snapshots. Never commit `.env` or any real key — see [SECURITY.md](SECURITY.md).

## Tests

```bash
# Backend — 213 tests against a real PostgreSQL instance
cd backend && pytest

# AI eval harness — deterministic, no key or DB needed
cd backend && python -m app.ai.eval.run_eval

# Frontend — 45 Vitest tests
cd frontend && npm test
```

A change must keep the backend suite green and the frontend type-checking and building (`npm run build` must succeed). The backend tests use real PostgreSQL fixtures, so a local database is required to run them.

## Branching

- Branch from `main` (or the active milestone branch).
- Use a descriptive prefix: `feat/`, `fix/`, `docs/`, `refactor/`, `test/`, `chore/`.
- Example: `feat/cascade-cycle-guard`, `fix/approval-self-approve-check`.

## Commits

- Imperative mood, present tense: "Add cascade cycle guard" not "Added".
- One logical change per commit when feasible.
- Reference issues with `Refs #123` or `Closes #123`.

## Pull requests

Before opening a PR:

1. Rebase on the latest base branch.
2. Run the backend tests and confirm the frontend builds.
3. Update the README or relevant docs if behavior changed.

Then open the PR using the [template](.github/PULL_REQUEST_TEMPLATE.md). Reviewers look for a clear *what* and *why*, evidence the change was tested, and a note on any user-visible behavior change.

## Code style

- **Python:** type hints on public functions; keep **routers thin and engines pure** — business rules (the release gate, the cascade, the diff, the lifecycle machine) belong in the engine layer, not in route handlers. Format with Ruff/Black conventions if configured.
- **TypeScript / React:** functional components with hooks, no class components; data fetching through **React Query**; API calls go through `src/api/client.ts`, not inline in components.
- **Naming:** modules in `snake_case`, classes in `PascalCase`, constants `UPPER_SNAKE_CASE`.
- **Database changes** go through Alembic migrations; CI verifies the chain round-trips (`upgrade → downgrade`).

## Data and secrets

- **Synthetic data only.** Issue reports, test cases, screenshots, and seed content must use fabricated data. Never include real curriculum, student, or organizational data.
- **Never commit secrets.** API keys and DB credentials live in `.env` (gitignored). Run `git ls-files | grep -i secret` before pushing.

## Reporting issues

Use the templates under [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/). For security issues, follow [SECURITY.md](SECURITY.md) — do not open a public issue.

## Code of Conduct

By contributing, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
