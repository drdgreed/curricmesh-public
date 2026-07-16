"""GitHub PR adapter for curriculum content sync.

Ports the career-foundry choreography (get base ref → create branch →
per-file GET sha / PUT contents → open PR) with three additions:

1. ``base_branch`` parameter — not hardcoded to "main".
2. Branch-create 422 (ref already exists) → log + continue to content PUTs
   (idempotent retry: the branch already has the right base, just update it).
3. PR-create 422 (PR already open for the branch) → fetch the open PR URL
   via ``GET /repos/{repo}/pulls?head={owner}:{branch}&state=open``
   and return it.

The module-level ``_transport`` seam allows tests to inject an
``httpx.MockTransport`` without any live network.
"""

from __future__ import annotations

import base64
import logging

import httpx

log = logging.getLogger(__name__)

_API = "https://api.github.com"
_transport: httpx.BaseTransport | None = None  # tests inject a MockTransport


def _client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_API,
        transport=_transport,
        timeout=30.0,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


async def open_content_pr(
    *,
    repo: str,
    token: str,
    base_branch: str,
    branch: str,
    files: dict[str, str],
    title: str,
    body: str,
) -> str:
    """Create *branch* off *base_branch*, write *files* (path → content),
    open a PR, and return its ``html_url``.

    Idempotent
    ----------
    - 422 on branch-create (branch already exists) → log and continue to
      content PUTs; the branch is reused as-is.
    - 422 on PR-create (PR already open for the branch) → fetch and return
      the existing PR's ``html_url``.
    """
    owner = repo.split("/")[0]

    async with _client(token) as c:
        # Step 1: resolve the base branch SHA.
        base_sha = (
            await c.get(f"/repos/{repo}/git/ref/heads/{base_branch}")
        ).json()["object"]["sha"]

        # Step 2: create the branch (idempotent — 422 = already exists).
        ref_resp = await c.post(
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if ref_resp.status_code == 422:
            log.info(
                "sync_github: branch %r already exists in %s — continuing to content PUTs",
                branch,
                repo,
            )
        elif ref_resp.status_code not in (200, 201):
            raise RuntimeError(
                f"sync_github: branch create failed for {branch!r} in {repo}: "
                f"HTTP {ref_resp.status_code}"
            )

        # Step 3: write each file (include existing sha when the file is present).
        for path, content in files.items():
            existing = await c.get(
                f"/repos/{repo}/contents/{path}", params={"ref": base_branch}
            )
            sha = existing.json().get("sha") if existing.status_code == 200 else None
            payload: dict = {
                "message": f"curricmesh-sync: update {path}",
                "branch": branch,
                "content": base64.b64encode(content.encode()).decode(),
            }
            if sha:
                payload["sha"] = sha
            put_resp = await c.put(f"/repos/{repo}/contents/{path}", json=payload)
            if put_resp.status_code not in (200, 201):
                # A silently-partial PR logged as success is the worst outcome —
                # fail loudly so sync_release records a failed SyncLog and the
                # sweep retries the same branch idempotently (final review).
                raise RuntimeError(
                    f"sync_github: content PUT failed for {path!r}: "
                    f"HTTP {put_resp.status_code}"
                )

        # Step 4: open the PR (idempotent — 422 = already open, fetch it).
        pr_resp = await c.post(
            f"/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": base_branch, "body": body},
        )
        if pr_resp.status_code == 422:
            log.info(
                "sync_github: PR already open for branch %r in %s — fetching existing PR URL",
                branch,
                repo,
            )
            existing_prs = await c.get(
                f"/repos/{repo}/pulls",
                params={"head": f"{owner}:{branch}", "state": "open"},
            )
            prs = existing_prs.json()
            if not prs:
                raise RuntimeError(
                    f"sync_github: PR create returned 422 for {branch!r} but no "
                    f"open PR found — cannot determine PR URL"
                )
            return prs[0]["html_url"]

        if pr_resp.status_code not in (200, 201):
            raise RuntimeError(
                f"sync_github: PR create failed for {branch!r}: HTTP {pr_resp.status_code}"
            )
        return pr_resp.json()["html_url"]
