"""Integration tests for POST /api/v1/auth/login and GET /api/v1/auth/me."""

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token, decode_token
from app.auth.passwords import hash_password
from app.database import get_db
from app.main import app
from app.models.org import Organization
from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _make_transport(session: AsyncSession):
    """Yield an httpx AsyncClient wired to the app with a DB override.

    The dependency override is always cleared in the finally block (M1:
    exception-safe teardown) so overrides never leak between tests.
    """
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_login_success(db_session: AsyncSession):
    """Valid credentials → 200 + access_token."""
    user = User(
        email="alice@example.com",
        role="instructor",
        password_hash=hash_password("secret123"),
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "secret123"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


async def test_login_wrong_password(db_session: AsyncSession):
    """Wrong password → 401."""
    user = User(
        email="bob@example.com",
        role="qa_lead",
        password_hash=hash_password("rightpass"),
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "bob@example.com", "password": "wrongpass"},
        )

    assert resp.status_code == 401


async def test_me_with_valid_token(db_session: AsyncSession):
    """Token from login → GET /me returns sub and role."""
    user = User(
        email="carol@example.com",
        role="architect",
        password_hash=hash_password("pw"),
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "carol@example.com", "password": "pw"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["role"] == "architect"
    assert body["sub"] == str(user.id)


async def test_me_with_expired_token(db_session: AsyncSession):
    """An expired JWT → GET /me returns 401 (exercises token expiry path)."""
    # expires_minutes=-1 makes the token expire in the past immediately.
    expired_token = create_access_token(sub="u-expired", role="instructor", expires_minutes=-1)

    async with _make_transport(db_session) as client:
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

    assert resp.status_code == 401


async def test_login_token_carries_org_claim(db_session: AsyncSession):
    """A user with an organization_id → login token's 'org' claim matches it."""
    org = Organization(name="Acme U")
    db_session.add(org)
    await db_session.flush()
    org_id = org.id
    user = User(
        email="dave@example.com",
        role="instructor",
        password_hash=hash_password("pw"),
        organization_id=org_id,
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "dave@example.com", "password": "pw"},
        )

    assert resp.status_code == 200
    token = resp.json()["access_token"]
    claims = decode_token(token)
    assert claims["org"] == str(org_id)


async def test_me_returns_org(db_session: AsyncSession):
    """GET /me surfaces the org from the token for an org-scoped user."""
    org = Organization(name="Beta College")
    db_session.add(org)
    await db_session.flush()
    org_id = org.id
    user = User(
        email="erin@example.com",
        role="architect",
        password_hash=hash_password("pw"),
        organization_id=org_id,
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "erin@example.com", "password": "pw"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]

        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["org"] == str(org_id)


async def test_me_org_is_none_for_orgless_user(db_session: AsyncSession):
    """A user without an organization_id → /me returns org=None (back-compat)."""
    user = User(
        email="frank@example.com",
        role="instructor",
        password_hash=hash_password("pw"),
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "frank@example.com", "password": "pw"},
        )
        token = login_resp.json()["access_token"]
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert me_resp.status_code == 200
    assert me_resp.json()["org"] is None


async def test_login_email_is_normalized(db_session: AsyncSession):
    """Mixed-case / whitespace-padded email still logs in (browser auto-capitalize).

    Emails are stored lowercase; the login handler trims + lowercases the input
    so "Architect@…" or a stray trailing space doesn't reject valid credentials.
    The password is still matched verbatim.
    """
    user = User(
        email="grace@example.com",
        role="architect",
        password_hash=hash_password("secret123"),
    )
    db_session.add(user)
    await db_session.commit()

    async with _make_transport(db_session) as client:
        for variant in (
            "Grace@example.com",      # browser-capitalized first letter
            "GRACE@EXAMPLE.COM",      # all caps
            "  grace@example.com  ",  # whitespace padding
        ):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": variant, "password": "secret123"},
            )
            assert resp.status_code == 200, f"variant {variant!r} should log in"
            assert "access_token" in resp.json()

        # The password is NOT normalized — a padded password still fails.
        bad = await client.post(
            "/api/v1/auth/login",
            json={"email": "grace@example.com", "password": "secret123 "},
        )
        assert bad.status_code == 401


async def test_login_unknown_email(db_session: AsyncSession):
    """Unknown email → 401 with 'Invalid credentials' (exercises I1 constant-time path)."""
    async with _make_transport(db_session) as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "doesntmatter"},
        )

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid credentials"}
