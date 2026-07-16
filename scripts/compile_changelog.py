#!/usr/bin/env python3
"""Fold changelog.d/*.md fragments into CHANGELOG.md's [Unreleased] section.

Naming: changelog.d/<slug>.<type>.md  (type: added|changed|fixed|removed|
deprecated|security; unknown -> changed). Body = markdown bullet(s).

Usage:
    python scripts/compile_changelog.py          # compile + delete fragments
    python scripts/compile_changelog.py --check   # non-zero if fragments exist
"""
from __future__ import annotations
import sys
from pathlib import Path

ORDER = ["Added", "Changed", "Fixed", "Removed", "Deprecated", "Security"]
ROOT = Path(__file__).resolve().parent.parent
FRAG_DIR = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"


def _type_of(path: Path) -> str:
    parts = path.name.split(".")
    if len(parts) >= 3:
        t = parts[-2].capitalize()
        if t in ORDER:
            return t
    return "Changed"


def fragments() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for f in sorted(FRAG_DIR.glob("*.md")):
        if f.name == "README.md":
            continue
        body = f.read_text().strip("\n")
        if not body.strip():
            continue
        groups.setdefault(_type_of(f), []).append(body)
    return groups


def compile_into_changelog() -> int:
    groups = fragments()
    if not groups:
        print("No fragments to compile.")
        return 0
    lines = CHANGELOG.read_text().splitlines()
    # find the [Unreleased] header
    try:
        u = next(i for i, l in enumerate(lines) if l.strip().lower().startswith("## [unreleased]"))
    except StopIteration:
        # create one after the first top-level heading / at top
        insert_at = next((i + 1 for i, l in enumerate(lines) if l.startswith("# ")), 0)
        lines[insert_at:insert_at] = ["", "## [Unreleased]", ""]
        u = insert_at + 1
    # end of the [Unreleased] section = next "## " or EOF
    end = next((i for i in range(u + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    section = lines[u + 1:end]
    for typ in ORDER:
        if typ not in groups:
            continue
        entries = groups[typ]
        # find existing "### <typ>" within the section, else create at the end
        try:
            h = next(i for i, l in enumerate(section) if l.strip() == f"### {typ}")
            # insert right after the header (and any blank line)
            ins = h + 1
            block = entries + [""]
            section[ins:ins] = block
        except StopIteration:
            section += ["", f"### {typ}", ""] + entries
    section = [l for l in section]  # noop clarity
    lines[u + 1:end] = section
    CHANGELOG.write_text("\n".join(lines).rstrip("\n") + "\n")
    # delete consumed fragments
    n = 0
    for f in FRAG_DIR.glob("*.md"):
        if f.name != "README.md" and f.read_text().strip():
            f.unlink(); n += 1
    print(f"Compiled {sum(len(v) for v in groups.values())} fragment(s) into CHANGELOG.md; removed {n} file(s).")
    return 0


def check() -> int:
    frs = [f for f in FRAG_DIR.glob("*.md") if f.name != "README.md" and f.read_text().strip()]
    if frs:
        print(f"{len(frs)} uncompiled changelog fragment(s): " + ", ".join(f.name for f in frs))
        return 1
    print("No pending fragments.")
    return 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else compile_into_changelog())
