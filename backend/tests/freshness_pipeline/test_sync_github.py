"""Tests for the GitHub PR adapter (app.freshness_pipeline.sync_github).

All four cases use httpx.MockTransport — no live network calls.

Cases
-----
1. happy_path          — correct call sequence; existing-file sha in PUT body;
                         base_branch used for ref GET and PR base.
2. new_file            — contents GET 404 → PUT body must NOT contain sha key.
3. existing_branch_422 — refs POST 422 → continues to content PUTs; returns URL.
4. existing_pr_422     — pulls POST 422 → GET pulls?head=owner:branch → return
                         the open PR's html_url.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.freshness_pipeline import sync_github


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_call_sequence(monkeypatch):
    """Correct call sequence; base_branch (not 'main') used for ref GET and PR base;
    existing sha included in PUT body; branch name correct in PUT body."""
    calls: list[tuple[str, str]] = []
    put_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))

        if "/git/ref/heads/release" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json={"sha": "existingsha"})
            put_bodies.append(json.loads(request.content))
            return httpx.Response(201, json={})
        if request.url.path.endswith("/pulls") and request.method == "POST":
            body = json.loads(request.content)
            assert body["base"] == "release"
            return httpx.Response(201, json={"html_url": "https://github.com/x/y/pull/1"})
        return httpx.Response(404)

    monkeypatch.setattr(sync_github, "_transport", httpx.MockTransport(handler))

    url = await sync_github.open_content_pr(
        repo="x/y",
        token="t",
        base_branch="release",
        branch="curricmesh-sync/agentic-v1.0.0",
        files={"modules/M0.1-llm-mental-model.md": "# LLM mental model\n"},
        title="sync: agentic v1.0.0",
        body="body text",
    )

    assert url == "https://github.com/x/y/pull/1"
    assert ("GET", "/repos/x/y/git/ref/heads/release") in calls
    assert ("POST", "/repos/x/y/git/refs") in calls
    assert ("GET", "/repos/x/y/contents/modules/M0.1-llm-mental-model.md") in calls
    assert ("PUT", "/repos/x/y/contents/modules/M0.1-llm-mental-model.md") in calls
    assert ("POST", "/repos/x/y/pulls") in calls
    assert len(put_bodies) == 1
    assert put_bodies[0]["sha"] == "existingsha"
    assert put_bodies[0]["branch"] == "curricmesh-sync/agentic-v1.0.0"


# ---------------------------------------------------------------------------
# 2. New-file path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_file_no_sha_in_put(monkeypatch):
    """Contents GET 404 → PUT body must NOT contain a 'sha' key."""
    put_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(404, json={"message": "Not Found"})
            put_bodies.append(json.loads(request.content))
            return httpx.Response(201, json={})
        if request.url.path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"html_url": "https://github.com/x/y/pull/2"})
        return httpx.Response(404)

    monkeypatch.setattr(sync_github, "_transport", httpx.MockTransport(handler))

    url = await sync_github.open_content_pr(
        repo="x/y",
        token="t",
        base_branch="main",
        branch="curricmesh-sync/new-v1.0.0",
        files={"modules/new-module.md": "# brand new\n"},
        title="sync: new module",
        body="body",
    )

    assert url == "https://github.com/x/y/pull/2"
    assert len(put_bodies) == 1
    assert "sha" not in put_bodies[0]
    assert "content" in put_bodies[0]
    assert put_bodies[0]["branch"] == "curricmesh-sync/new-v1.0.0"


# ---------------------------------------------------------------------------
# 3. Existing-branch 422 on refs POST → continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_branch_422_continues(monkeypatch):
    """Branch-create 422 → log + continue; content PUTs still happen; PR URL returned."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))

        if "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if request.url.path.endswith("/git/refs"):
            return httpx.Response(422, json={"message": "Reference already exists"})
        if "/contents/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json={"sha": "sha1"})
            return httpx.Response(200, json={})
        if request.url.path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(201, json={"html_url": "https://github.com/x/y/pull/3"})
        return httpx.Response(404)

    monkeypatch.setattr(sync_github, "_transport", httpx.MockTransport(handler))

    url = await sync_github.open_content_pr(
        repo="x/y",
        token="t",
        base_branch="main",
        branch="curricmesh-sync/existing-v2.0.0",
        files={"modules/M1.1-agents.md": "# agents\n"},
        title="sync: retry existing branch",
        body="body",
    )

    assert url == "https://github.com/x/y/pull/3"
    # Content PUT still happened despite 422 on branch create.
    assert ("PUT", "/repos/x/y/contents/modules/M1.1-agents.md") in calls
    assert ("POST", "/repos/x/y/pulls") in calls


# ---------------------------------------------------------------------------
# 4. Existing-PR 422 on pulls POST → fetch open PR URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_pr_422_returns_open_pr_url(monkeypatch):
    """PR-create 422 → GET /pulls?head=owner:branch&state=open → return existing html_url."""
    calls: list[tuple[str, str]] = []
    get_pulls_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))

        if "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(200, json={"sha": "sha1"})
            return httpx.Response(200, json={})
        if request.url.path.endswith("/pulls"):
            if request.method == "POST":
                return httpx.Response(422, json={"message": "A pull request already exists"})
            # GET for the existing open PR — capture params for assertion.
            get_pulls_params.update(dict(request.url.params))
            return httpx.Response(
                200, json=[{"html_url": "https://github.com/x/y/pull/99"}]
            )
        return httpx.Response(404)

    monkeypatch.setattr(sync_github, "_transport", httpx.MockTransport(handler))

    url = await sync_github.open_content_pr(
        repo="x/y",
        token="t",
        base_branch="main",
        branch="curricmesh-sync/dup-pr-v3.0.0",
        files={"modules/M2.1-rag.md": "# RAG\n"},
        title="sync: duplicate PR",
        body="body",
    )

    assert url == "https://github.com/x/y/pull/99"
    # GET /pulls must have been called to find the open PR.
    assert ("GET", "/repos/x/y/pulls") in calls
    assert get_pulls_params.get("head") == "x:curricmesh-sync/dup-pr-v3.0.0"
    assert get_pulls_params.get("state") == "open"


async def test_put_failure_raises_no_silent_partial_pr(monkeypatch):
    """A failing content PUT raises instead of proceeding to open a PR — a
    silently-partial PR logged as success is the worst outcome (final review)."""
    pr_opened: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/ref/heads/" in request.url.path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if request.url.path.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in request.url.path:
            if request.method == "GET":
                return httpx.Response(404)
            return httpx.Response(409, json={"message": "conflict"})  # PUT fails
        if request.url.path.endswith("/pulls"):
            pr_opened.append(request.url.path)
            return httpx.Response(201, json={"html_url": "https://x/pr/1"})
        return httpx.Response(404)

    monkeypatch.setattr(sync_github, "_transport", httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="content PUT failed"):
        await sync_github.open_content_pr(
            repo="x/y", token="t", base_branch="main",
            branch="curricmesh-sync/test-v1.1.0",
            files={"docs/a.md": "content"},
            title="t", body="b",
        )
    assert pr_opened == []  # never reached the PR step
