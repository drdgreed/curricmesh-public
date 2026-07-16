"""Sync service: released CurriculumVersion → one GitHub PR.

``sync_release`` implements the 5-step contract from
``docs/plans/2026-07-06-freshness-pipeline-phase4-sync.md`` § Task 3:

1. Diff members: new_version vs parent (all members when root/no parent).
2. Map each changed member: source_url → path_prefix/source_url;
   else first matching path_rules entry; else → unmapped list.
3. No mappable files → SyncLog(status="skipped"), adapter NOT called.
4. open_content_pr → SyncLog(status="success", detail.url/files/unmapped).
5. Any exception → SyncLog(status="failed", detail.error), returned, never raised.

Content + source_url are fetched in a single 3-way join (VersionMember →
ContentVersion → LineageAsset) over the new version — the same join
``content_cards.py`` uses.  Member-level diff uses ``version_members``
(returns only the lightweight ManifestMember DTO) for the parent, which
avoids pulling full text for the previous release.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.config import settings
from app.core.manifest import version_members
from app.freshness_pipeline.sync_github import open_content_pr
from app.models.content_model import ContentVersion, LineageAsset, VersionMember
from app.models.sync import SyncLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.content_model import CurriculumVersion
    from app.models.curriculum import Curriculum
    from app.models.sync import SyncTarget

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PR body builder
# ---------------------------------------------------------------------------


def _build_pr_body(
    *,
    curriculum_name: str,
    semver: str,
    ccr,
    file_paths: list[str],
    lineage_keys: list[str],
    unmapped: list[str],
) -> str:
    """Compose the GitHub PR description."""
    lines: list[str] = [f"Released: **{curriculum_name} v{semver}**"]
    if ccr is not None:
        lines.append(f"Source CCR: {ccr.title} (id: {ccr.id})")
    lines.append("")
    lines.append("## Changed files")
    for path, key in zip(file_paths, lineage_keys):
        lines.append(f"- `{path}` ← `{key}`")
    if unmapped:
        lines.append("")
        lines.append("## Unmapped assets")
        lines.append(
            "The following assets have no configured path mapping and were "
            "NOT included in this PR:"
        )
        for key in unmapped:
            lines.append(f"- `{key}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Path mapper
# ---------------------------------------------------------------------------


def _map_path(
    *,
    source_url: str | None,
    kind_value: str,
    section: str,
    lineage_key: str,
    week_index: int,
    path_prefix: str,
    path_rules: list[dict],
) -> str | None:
    """Return a repo-relative file path, or None when unmapped.

    Priority:
    1. ``source_url`` present → ``{path_prefix}/{source_url}``
    2. First matching ``path_rules`` entry (kind + optional section_prefix)
       with ``{lineage_key}`` / ``{week_index}`` template substitution.
    3. None → caller adds to unmapped list.
    """
    if source_url:
        return f"{path_prefix}/{source_url}"

    for rule in path_rules:
        rule_kind = rule.get("kind")
        if rule_kind and rule_kind != kind_value:
            continue
        section_prefix = rule.get("section_prefix")
        if section_prefix and not section.startswith(section_prefix):
            continue
        template: str = rule.get("path_template", "")
        return template.format(lineage_key=lineage_key, week_index=week_index)

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def sync_release(
    session: "AsyncSession",
    *,
    curriculum: "Curriculum",
    new_version: "CurriculumVersion",
    target: "SyncTarget",
    ccr=None,
) -> SyncLog:
    """Diff *new_version* against its parent, map changed content, open a PR.

    Always returns a SyncLog.  Never raises — any exception is caught,
    recorded as ``status="failed"``, and the log is returned.

    Parameters
    ----------
    session:
        The ambient tenant-scoped AsyncSession.
    curriculum:
        The Curriculum whose version is being released.
    new_version:
        The CurriculumVersion just released (has major/minor/patch).
    target:
        The active SyncTarget carrying repo + mapping config.
    ccr:
        Optional ChangeRequest that triggered the release (title + id go in
        the PR body).
    """
    try:
        return await _do_sync(
            session,
            curriculum=curriculum,
            new_version=new_version,
            target=target,
            ccr=ccr,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "sync_release failed for curriculum %s version %s",
            curriculum.id,
            new_version.id,
        )
        failed_log = SyncLog(
            curriculum_id=curriculum.id,
            version_id=None,
            curriculum_version_id=new_version.id,
            target="github",
            status="failed",
            detail={"error": str(exc)},
        )
        session.add(failed_log)
        try:
            await session.flush()
        except Exception:  # noqa: BLE001
            pass  # if even the error-log flush fails, still return the object
        return failed_log


# ---------------------------------------------------------------------------
# Core implementation
# ---------------------------------------------------------------------------


async def _do_sync(
    session: "AsyncSession",
    *,
    curriculum: "Curriculum",
    new_version: "CurriculumVersion",
    target: "SyncTarget",
    ccr=None,
) -> SyncLog:
    # ------------------------------------------------------------------
    # Step 1 — Diff members: new vs parent
    # ------------------------------------------------------------------
    new_members = await version_members(session, new_version.id)

    parent_cv_map: dict[str, object] = {}  # lineage_key → content_version_id
    if new_version.parent_version_id is not None:
        parent_members = await version_members(session, new_version.parent_version_id)
        parent_cv_map = {m.lineage_key: m.content_version_id for m in parent_members}
    # Root version (no parent) → all members are changed (first sync ships everything).

    changed_keys: set[str] = {
        m.lineage_key
        for m in new_members
        if m.lineage_key not in parent_cv_map
        or parent_cv_map[m.lineage_key] != m.content_version_id
    }

    # ------------------------------------------------------------------
    # Fetch content + source_url for the new version in a single query
    # (mirrors content_cards.py — one 3-way join, no second round-trip).
    # ------------------------------------------------------------------
    rows = (
        await session.execute(
            select(VersionMember, ContentVersion, LineageAsset)
            .join(ContentVersion, VersionMember.asset_version_id == ContentVersion.id)
            .join(LineageAsset, VersionMember.asset_id == LineageAsset.id)
            .where(VersionMember.curriculum_version_id == new_version.id)
        )
    ).all()

    # Build lookup: lineage_key → (ContentVersion, LineageAsset)
    rows_by_key: dict[str, tuple] = {
        lineage.lineage_key: (content, lineage)
        for _, content, lineage in rows
    }

    # ------------------------------------------------------------------
    # Step 2 — Map each changed member to a repo path
    # ------------------------------------------------------------------
    config = target.config
    path_prefix = config.get("path_prefix", "").rstrip("/")
    path_rules: list[dict] = config.get("path_rules", [])

    # Preserve deterministic order (mirrors version_members sort).
    ordered_members = sorted(
        (m for m in new_members if m.lineage_key in changed_keys),
        key=lambda m: (m.week_index, m.order, m.lineage_key),
    )

    files: dict[str, str] = {}
    file_paths: list[str] = []  # ordered paths for PR body
    file_keys: list[str] = []   # matching lineage_keys for PR body
    unmapped: list[str] = []

    for member in ordered_members:
        content_row, lineage_row = rows_by_key[member.lineage_key]

        path = _map_path(
            source_url=lineage_row.source_url,
            kind_value=lineage_row.kind.value,
            section=member.section,
            lineage_key=member.lineage_key,
            week_index=member.week_index,
            path_prefix=path_prefix,
            path_rules=path_rules,
        )

        if path is not None:
            files[path] = content_row.content
            file_paths.append(path)
            file_keys.append(member.lineage_key)
        else:
            unmapped.append(member.lineage_key)

    # ------------------------------------------------------------------
    # Step 3 — No mappable files → skipped (no PR)
    # ------------------------------------------------------------------
    if not files:
        skipped_log = SyncLog(
            curriculum_id=curriculum.id,
            version_id=None,
            curriculum_version_id=new_version.id,
            target="github",
            status="skipped",
            detail={"reason": "no mappable files", "unmapped": unmapped},
        )
        session.add(skipped_log)
        await session.flush()
        return skipped_log

    # ------------------------------------------------------------------
    # Step 4 — Build branch / PR, call adapter
    # ------------------------------------------------------------------
    semver = f"{new_version.major}.{new_version.minor}.{new_version.patch}"
    branch = f"curricmesh-sync/{curriculum.slug}-v{semver}"
    title = f"curriculum sync: {curriculum.name} v{semver}"
    body = _build_pr_body(
        curriculum_name=curriculum.name,
        semver=semver,
        ccr=ccr,
        file_paths=file_paths,
        lineage_keys=file_keys,
        unmapped=unmapped,
    )

    repo: str = config["repo"]
    base_branch: str = config.get("base_branch", "main")

    pr_url = await open_content_pr(
        repo=repo,
        token=settings.SYNC_GITHUB_TOKEN,
        base_branch=base_branch,
        branch=branch,
        files=files,
        title=title,
        body=body,
    )

    success_log = SyncLog(
        curriculum_id=curriculum.id,
        version_id=None,
        curriculum_version_id=new_version.id,
        target="github",
        status="success",
        detail={"url": pr_url, "files": file_paths, "unmapped": unmapped},
    )
    session.add(success_log)
    await session.flush()
    return success_log
