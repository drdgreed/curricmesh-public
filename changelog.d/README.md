# Changelog fragments

**Do NOT edit `CHANGELOG.md` directly in a feature PR.** With many parallel
branches all editing the same `## [Unreleased]` region, every merge re-conflicts
the others (the "changelog treadmill"). Instead, drop a small fragment file here —
separate files never conflict.

## How to add an entry

Create a file named:

    changelog.d/<short-slug>.<type>.md

`<type>` ∈ `added` · `changed` · `fixed` · `removed` · `deprecated` · `security`
(anything unrecognized → **Changed**). The body is one or more markdown bullets:

    # changelog.d/media-upload.added.md
    - **Media upload UI** — authors can upload owned assets directly from the browser.

## Releasing / folding into CHANGELOG.md

Run:

    python scripts/compile_changelog.py

It groups every fragment by type under `## [Unreleased]` in `CHANGELOG.md`, then
deletes the consumed fragments. `--check` (CI-friendly) exits non-zero if fragments
exist without compiling (does not modify files).
