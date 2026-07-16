"""Semver value object and bump rules.

Supports major/minor/patch components only — no pre-release or build metadata.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Semver:
    """Immutable, comparable semantic version (major.minor.patch)."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for field, value in (("major", self.major), ("minor", self.minor), ("patch", self.patch)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer, got {value!r}")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


class BumpType(enum.Enum):
    major = "major"
    minor = "minor"
    patch = "patch"


def bump(v: Semver, kind: BumpType) -> Semver:
    """Return a new Semver with the given component incremented and lower components reset to 0."""
    if kind is BumpType.major:
        return Semver(v.major + 1, 0, 0)
    if kind is BumpType.minor:
        return Semver(v.major, v.minor + 1, 0)
    return Semver(v.major, v.minor, v.patch + 1)
