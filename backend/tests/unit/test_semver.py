# backend/tests/unit/test_semver.py
import pytest

from app.core.versioning.semver import Semver, BumpType, bump


def test_bump_minor_resets_patch():
    assert bump(Semver(1, 2, 3), BumpType.minor) == Semver(1, 3, 0)


def test_bump_major_resets_minor_patch():
    assert bump(Semver(1, 2, 3), BumpType.major) == Semver(2, 0, 0)


def test_semver_str():
    assert str(Semver(1, 1, 1)) == "1.1.1"


# Additional completeness tests
def test_bump_patch_increments_patch_only():
    assert bump(Semver(1, 2, 3), BumpType.patch) == Semver(1, 2, 4)


def test_semver_ordering():
    assert Semver(1, 0, 0) < Semver(1, 1, 0)


def test_rejects_negative():
    with pytest.raises(ValueError):
        Semver(-1, 0, 0)


def test_rejects_non_int():
    with pytest.raises(ValueError):
        Semver(1.0, 0, 0)
    with pytest.raises(ValueError):
        Semver(True, 0, 0)


def test_ordering_major_dominates():
    assert Semver(1, 9, 9) < Semver(2, 0, 0)
    assert Semver(1, 0, 0) < Semver(1, 1, 0)
