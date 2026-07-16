"""HTTP tests for the deck QA endpoint (S3).

POST /api/v1/slides/qa runs the mechanical + human-review gates over a supplied
``deck.md`` and returns the report. QA is stateless analysis — no storage, no
DB — so the transport mirrors the render API test (httpx.AsyncClient + JWT), and
no dependencies are overridden.

Matrix:
  1. author (instructor) + conforming exemplar → 200, passed=True, gates listed.
  2. degraded deck → 200, passed=False, the SPECIFIC failing gate named.
  3. wrong role (learner) → 403.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.auth.jwt import create_access_token
from app.main import app
from tests.conftest import DEFAULT_ORG_ID

EXEMPLAR = (Path(__file__).parent / "fixtures" / "exemplar_deck.md").read_text(encoding="utf-8")


@asynccontextmanager
async def _client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _auth(role: str, org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_qa_endpoint_conforming_deck_passes():
    async with _client() as client:
        resp = await client.post(
            "/api/v1/slides/qa",
            json={"deck_md": EXEMPLAR},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passed"] is True
    names = {g["name"] for g in body["gates"]}
    assert "slide_count" in names and "visual_regression_slides" in names
    # The human-review layer is surfaced but does not block ``passed``.
    assert any(g["status"] == "needs_human" for g in body["gates"])


@pytest.mark.asyncio
async def test_qa_endpoint_degraded_deck_names_failing_gate():
    degraded = EXEMPLAR.replace("theme: career-forge\n", "", 1)
    async with _client() as client:
        resp = await client.post(
            "/api/v1/slides/qa",
            json={"deck_md": degraded},
            headers=_auth("architect"),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passed"] is False
    failing = [g for g in body["gates"] if g["status"] == "fail"]
    assert [g["name"] for g in failing] == ["frontmatter_theme"]
    assert "theme" in failing[0]["detail"]


@pytest.mark.asyncio
async def test_qa_endpoint_wrong_role_forbidden():
    async with _client() as client:
        resp = await client.post(
            "/api/v1/slides/qa",
            json={"deck_md": EXEMPLAR},
            headers=_auth("learner"),
        )
    assert resp.status_code == 403, resp.text
