# backend/tests/unit/test_auth.py
import uuid

from app.auth.jwt import create_access_token, decode_token
from app.auth.rbac import require_roles, tenant_context
from app.tenant import use_org, get_current_org
import pytest
from fastapi import HTTPException

def test_token_roundtrip():
    tok = create_access_token(sub="u1", role="qa_lead")
    claims = decode_token(tok)
    assert claims["sub"] == "u1" and claims["role"] == "qa_lead"


def test_token_without_org_omits_claim():
    """When no org is passed, the decoded token has no 'org' claim."""
    tok = create_access_token(sub="u1", role="qa_lead")
    claims = decode_token(tok)
    assert "org" not in claims


def test_token_with_org_includes_claim():
    """When org is passed, the decoded token carries it as a string claim."""
    org = uuid.uuid4()
    tok = create_access_token(sub="u1", role="qa_lead", org=org)
    claims = decode_token(tok)
    assert claims["org"] == str(org)


def test_token_with_org_none_omits_claim():
    """Explicitly passing org=None still omits the claim (existing-caller safety)."""
    tok = create_access_token(sub="u1", role="qa_lead", org=None)
    claims = decode_token(tok)
    assert "org" not in claims


def test_token_with_org_name_includes_claim():
    """org_name is carried as a display-only claim when provided."""
    tok = create_access_token(
        sub="u1", role="qa_lead", org=uuid.uuid4(), org_name="Career Forge"
    )
    claims = decode_token(tok)
    assert claims["org_name"] == "Career Forge"


def test_token_without_org_name_omits_claim():
    """No org_name passed -> no org_name claim (the id `org` stays authoritative)."""
    tok = create_access_token(sub="u1", role="qa_lead", org=uuid.uuid4())
    claims = decode_token(tok)
    assert "org_name" not in claims

# Tests the role-check logic in isolation; the full dependency chain (JWT decode → role guard) is covered by the integration tests.
def test_require_roles_rejects_wrong_role():
    dep = require_roles("architect", "program_manager")
    with pytest.raises(HTTPException) as e:
        dep(current={"sub": "u1", "role": "instructor"})
    assert e.value.status_code == 403


async def test_tenant_context_sets_contextvar_and_resets():
    """tenant_context (an async yield dependency) binds org during the request
    and resets the ContextVar to its prior value on exit."""
    org = uuid.uuid4()
    outer = uuid.uuid4()
    with use_org(outer):
        claims = {"sub": "u1", "role": "instructor", "org": str(org)}
        gen = tenant_context(current=claims)
        result = await anext(gen)  # runs up to the yield: sets the ContextVar
        assert result is claims
        assert get_current_org() == org
        # Exhaust the generator -> finally: current_org.reset(token)
        with pytest.raises(StopAsyncIteration):
            await anext(gen)
        assert get_current_org() == outer  # restored to the outer context


async def test_tenant_context_rejects_missing_org():
    """tenant_context raises 401 when the org claim is absent (on first step)."""
    with pytest.raises(HTTPException) as e:
        await anext(tenant_context(current={"sub": "u1", "role": "instructor"}))
    assert e.value.status_code == 401


async def test_tenant_context_rejects_malformed_org():
    """tenant_context raises 401 when the org claim is not a valid UUID."""
    with pytest.raises(HTTPException) as e:
        await anext(tenant_context(current={"sub": "u1", "role": "instructor", "org": "not-a-uuid"}))
    assert e.value.status_code == 401
