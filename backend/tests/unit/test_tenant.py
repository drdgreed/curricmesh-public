"""Unit tests for the tenant ContextVar primitive (app.tenant)."""

import uuid

import pytest

from app.tenant import (
    current_org,
    get_current_org,
    require_org,
    set_current_org,
    use_org,
)


def test_get_current_org_defaults_to_none():
    """With no context set, get_current_org returns None."""
    # The default ContextVar value is None; nothing has been set in this context.
    assert get_current_org() is None


def test_set_and_get_current_org():
    """set_current_org sets the value; get_current_org reads it back."""
    org = uuid.uuid4()
    token = set_current_org(org)
    try:
        assert get_current_org() == org
    finally:
        # Restore so we don't leak into other tests sharing this context.
        current_org.reset(token)


def test_use_org_sets_and_resets():
    """use_org sets the org for the block and restores the prior value after."""
    assert get_current_org() is None
    org = uuid.uuid4()
    with use_org(org):
        assert get_current_org() == org
    # After the block the context returns to its prior value (None).
    assert get_current_org() is None


def test_use_org_resets_to_prior_non_none_value():
    """use_org restores a previously-set org, not just None."""
    outer = uuid.uuid4()
    inner = uuid.uuid4()
    with use_org(outer):
        assert get_current_org() == outer
        with use_org(inner):
            assert get_current_org() == inner
        # Inner block exited → restored to outer, not None.
        assert get_current_org() == outer
    # Outer block exited → restored to None.
    assert get_current_org() is None


def test_require_org_raises_when_unset():
    """require_org raises a clear RuntimeError when no tenant context is set."""
    assert get_current_org() is None
    with pytest.raises(RuntimeError, match="No tenant context"):
        require_org()


def test_require_org_returns_org_when_set():
    """require_org returns the current org when one is set."""
    org = uuid.uuid4()
    with use_org(org):
        assert require_org() == org


def test_tenant_module_imports_only_stdlib():
    """app.tenant must not import anything under 'app' (avoids import cycle).

    models will import tenant, so tenant importing models (or any app module)
    would create a cycle. Inspect actual import statements via AST rather than
    raw text, so docstrings mentioning 'app.models' don't trip the check.
    """
    import ast

    import app.tenant as tenant_mod

    tree = ast.parse(open(tenant_mod.__file__).read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert not any(name == "app" or name.startswith("app.") for name in imported), (
        f"app.tenant must import stdlib only; found app imports: {imported}"
    )
