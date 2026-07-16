"""Simulated GitHub / LMS sync providers + the provider factory (V3-C).

Both providers are **simulated by default**: they format the incoming
``VersionManifest`` and return a ``SyncResult`` with a deterministic fake URL and
ZERO network. This is the demo/CI default — CI never makes a real API call.

A real integration (behind ``SYNC_GITHUB_*`` / ``SYNC_LMS_*`` config + creds) is
a documented TODO seam below; it is NOT implemented for V3-C (real network is out
of scope). Errors are surfaced, never swallowed — a provider that cannot publish
raises so the router can log a ``failed`` attempt and return a 4xx/5xx.
"""

from __future__ import annotations

from app.config import settings
from app.sync.base import SyncProvider, SyncResult, VersionManifest

# Deterministic placeholders for the simulated URLs. A real integration would
# derive these from SYNC_GITHUB_REPO / SYNC_LMS_BASE_URL.
_SIM_GITHUB_REPO = "curricmesh/curriculum"
_SIM_LMS_BASE = "https://lms.example.edu"


class GitHubSyncProvider:
    """Publish a released version as a GitHub commit/tag (simulated).

    Simulated mode formats the manifest as a release tag and returns the
    deterministic tag URL. When ``SYNC_GITHUB_ENABLED`` is True a real
    implementation would commit the manifest and create the tag via the GitHub
    API using ``SYNC_GITHUB_TOKEN`` / ``SYNC_GITHUB_REPO`` — TODO seam only,
    deliberately not built (no real network in V3-C).
    """

    async def publish(self, manifest: VersionManifest) -> SyncResult:
        if settings.SYNC_GITHUB_ENABLED:
            # TODO(real): commit manifest + create the release tag via the
            # GitHub API (SYNC_GITHUB_TOKEN / SYNC_GITHUB_REPO). Out of scope
            # for V3-C — real network is not implemented.
            raise NotImplementedError(
                "Real GitHub sync is not implemented; run in simulated mode "
                "(SYNC_GITHUB_ENABLED=False)."
            )

        url = (
            f"https://github.com/{_SIM_GITHUB_REPO}/releases/tag/{manifest.version}"
        )
        message = (
            f"Tagged {manifest.curriculum_name} {manifest.version} "
            f"({len(manifest.modules)} modules) at {url}"
        )
        return SyncResult(target="github", status="success", url=url, message=message)


class LmsSyncProvider:
    """Push a released version's modules to an LMS course (simulated).

    Simulated mode returns a deterministic course URL plus a per-module summary.
    A real implementation (behind ``SYNC_LMS_ENABLED`` + ``SYNC_LMS_*`` creds)
    would push each module to the LMS — TODO seam only, not built for V3-C.
    """

    async def publish(self, manifest: VersionManifest) -> SyncResult:
        if settings.SYNC_LMS_ENABLED:
            # TODO(real): push modules to the LMS course via SYNC_LMS_BASE_URL /
            # SYNC_LMS_TOKEN. Out of scope for V3-C — real network not built.
            raise NotImplementedError(
                "Real LMS sync is not implemented; run in simulated mode "
                "(SYNC_LMS_ENABLED=False)."
            )

        url = f"{_SIM_LMS_BASE}/courses/{manifest.curriculum_id}"
        module_summary = ", ".join(manifest.modules) or "(no modules)"
        message = (
            f"Pushed {len(manifest.modules)} module(s) of "
            f"{manifest.curriculum_name} {manifest.version} to {url}: "
            f"{module_summary}"
        )
        return SyncResult(target="lms", status="success", url=url, message=message)


# Registry of the known targets → provider. Add a target here to expose it.
_PROVIDERS: dict[str, type] = {
    "github": GitHubSyncProvider,
    "lms": LmsSyncProvider,
}


def get_sync_provider(target: str) -> SyncProvider:
    """Resolve a provider for ``target`` (``github``/``lms``).

    Raises ``ValueError`` on an unknown target — this is a pure seam (no FastAPI
    coupling, mirroring ``app/ai/corpus.py``). The router's dependency translates
    the ``ValueError`` into an HTTP 400.
    """
    provider_cls = _PROVIDERS.get(target)
    if provider_cls is None:
        raise ValueError(
            f"Unknown sync target '{target}'. "
            f"Valid targets: {', '.join(sorted(_PROVIDERS))}."
        )
    return provider_cls()
