"""App-layer tenant scoping (MT5) — isolation that does NOT rely on RLS.

These tests run on the default fixture connection, which authenticates as a
Postgres *superuser*. Superusers bypass RLS even under FORCE (see
``docs/AGENT_LESSONS.md`` P-001), so any isolation observed here is proof that
the application-level ``do_orm_execute`` auto-filter — not the DB policy — is
doing the work.

The auto-filter adds ``organization_id == current_org`` to every ORM SELECT
touching a ``TenantScoped`` entity whenever a tenant context is set. The fixture
pins the context to DEFAULT_ORG, so:

* a row written under DEFAULT_ORG is visible,
* a row written under a *different* org (via ``use_org``) is invisible,

all while connected as the superuser.

Non-tenant entities (``SotaSource``) carry no ``organization_id`` and must never
be filtered.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, text

from app.models.curriculum import Curriculum
from app.models.cohort import Cohort
from app.models.version import Version
from app.models.sota import SotaFinding, SotaSource
from app.tenant import current_org, set_current_org, use_org
from tests.conftest import DEFAULT_ORG_ID


def _pin_default_org(session):
    """Re-assert the fixture's DEFAULT_ORG session GUC (returns a coroutine)."""
    return session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(DEFAULT_ORG_ID)},
    )


async def _make_other_org(session) -> uuid.UUID:
    """Insert a second organization row (organizations is not RLS-scoped)."""
    other = uuid.uuid4()
    await session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(other), "n": "Other Org"},
    )
    return other


async def test_select_isolates_curriculum_under_superuser(db_session):
    """select(Curriculum) returns only the DEFAULT_ORG row, even as superuser.

    Writes one curriculum in the ambient DEFAULT_ORG context and another under
    ``use_org(other_org)``, then proves the cross-org row is invisible via three
    access paths: a plain select, a by-id select, and ``session.get``.
    """
    other_org = await _make_other_org(db_session)

    # 1. DEFAULT_ORG curriculum (ambient context).
    mine = Curriculum(name="Mine", slug=f"mine-{uuid.uuid4().hex[:8]}")
    db_session.add(mine)
    await db_session.flush()
    mine_id = mine.id

    # 2. Other-org curriculum.
    with use_org(other_org):
        theirs = Curriculum(name="Theirs", slug=f"theirs-{uuid.uuid4().hex[:8]}")
        db_session.add(theirs)
        await db_session.flush()
        theirs_id = theirs.id
        assert theirs.organization_id == other_org

    # Expunge everything from the identity map so every read below MUST hit the
    # DB and pass through the do_orm_execute auto-filter. (``session.get`` and
    # column-refresh loads short-circuit on identity-map hits, which is correct
    # for a real request — a request's session never holds another tenant's row.
    # Expunging here faithfully reproduces that fresh-session condition.)
    db_session.expunge_all()

    # 3a. Plain select sees only the DEFAULT_ORG row.
    rows = (await db_session.execute(select(Curriculum))).scalars().all()
    ids = {c.id for c in rows}
    assert mine_id in ids
    assert theirs_id not in ids
    assert all(c.organization_id == DEFAULT_ORG_ID for c in rows)

    # 3b. Targeted select for the other org's row → None (filtered out).
    found = (
        await db_session.execute(
            select(Curriculum).where(Curriculum.id == theirs_id)
        )
    ).scalar_one_or_none()
    assert found is None

    # 3c. session.get for the other org's row → None.
    got = await db_session.get(Curriculum, theirs_id)
    assert got is None

    # Sanity: my own row is still reachable by id.
    mine_again = await db_session.get(Curriculum, mine_id)
    assert mine_again is not None


async def test_filter_is_general_across_tenant_entities(db_session):
    """The mixin-based filter applies to every TenantScoped entity, not just one.

    Repeats the isolation proof for Cohort (a second tenant entity), confirming
    ``with_loader_criteria(TenantScoped, ...)`` targets the shared mixin and thus
    all 13 subclasses.
    """
    other_org = await _make_other_org(db_session)

    # A curriculum + version are needed for cohort FKs; create one pair per org.
    # DEFAULT_ORG cohort.
    cur_mine = Curriculum(name="CM", slug=f"cm-{uuid.uuid4().hex[:8]}")
    db_session.add(cur_mine)
    await db_session.flush()
    ver_mine = Version(curriculum_id=cur_mine.id, major=1, minor=0, patch=0)
    db_session.add(ver_mine)
    await db_session.flush()
    coh_mine = Cohort(
        curriculum_id=cur_mine.id, version_id=ver_mine.id, name="Mine Cohort"
    )
    db_session.add(coh_mine)
    await db_session.flush()
    mine_id = coh_mine.id

    # Other-org cohort.
    with use_org(other_org):
        cur_other = Curriculum(name="CO", slug=f"co-{uuid.uuid4().hex[:8]}")
        db_session.add(cur_other)
        await db_session.flush()
        ver_other = Version(curriculum_id=cur_other.id, major=1, minor=0, patch=0)
        db_session.add(ver_other)
        await db_session.flush()
        coh_other = Cohort(
            curriculum_id=cur_other.id, version_id=ver_other.id, name="Other Cohort"
        )
        db_session.add(coh_other)
        await db_session.flush()
        other_id = coh_other.id
        assert coh_other.organization_id == other_org

    # Drop the identity map so reads go to the DB through the auto-filter.
    db_session.expunge_all()

    rows = (await db_session.execute(select(Cohort))).scalars().all()
    ids = {c.id for c in rows}
    assert mine_id in ids
    assert other_id not in ids
    assert (await db_session.get(Cohort, other_id)) is None


async def test_no_context_adds_no_filter_and_does_not_crash(db_session):
    """With no tenant context, the auto-filter is a no-op (no crash).

    We don't assert leakage here — RLS + the write-time ``require_org`` default
    are the guards when context is unset. We only prove the listener short-circuits
    cleanly when ``get_current_org()`` is None. The query runs against an existing
    DEFAULT_ORG row written before we clear context.
    """
    cur = Curriculum(name="Pre", slug=f"pre-{uuid.uuid4().hex[:8]}")
    db_session.add(cur)
    await db_session.flush()

    token = set_current_org(None)
    try:
        assert current_org.get() is None
        # Should not raise; filter is simply not applied.
        result = await db_session.execute(select(Curriculum))
        _ = result.scalars().all()
    finally:
        current_org.reset(token)
        await db_session.rollback()
        await _pin_default_org(db_session)


async def test_sota_source_is_not_filtered(db_session):
    """SotaSource carries no organization_id, so the filter never touches it.

    Two SotaSources created under different org contexts are both visible
    regardless of the ambient org — proving non-tenant entities are exempt.
    """
    other_org = await _make_other_org(db_session)

    s1 = SotaSource(title="Global A", kind="paper")
    db_session.add(s1)
    await db_session.flush()
    id1 = s1.id

    # Even created under another org context, SotaSource has no org column —
    # write-stamping doesn't apply and it stays global.
    with use_org(other_org):
        s2 = SotaSource(title="Global B", kind="blog")
        db_session.add(s2)
        await db_session.flush()
        id2 = s2.id

    db_session.expire_all()

    rows = (await db_session.execute(select(SotaSource))).scalars().all()
    ids = {s.id for s in rows}
    assert id1 in ids
    assert id2 in ids, "SotaSource must NOT be tenant-filtered"


async def test_org_context_switch_rebinds_filter(db_session):
    """Switching the tenant context mid-session rebinds the filter per query.

    Makes the lambda-freshness guarantee OBSERVABLE: read as DEFAULT_ORG (see
    mine, not theirs), then switch to other_org (see theirs, not mine). A stale
    closure would fail this — the same rows would show under both contexts.
    """
    other_org = await _make_other_org(db_session)

    mine = Curriculum(name="Mine", slug=f"mine-{uuid.uuid4().hex[:8]}")
    db_session.add(mine)
    await db_session.flush()
    mine_id = mine.id

    with use_org(other_org):
        theirs = Curriculum(name="Theirs", slug=f"theirs-{uuid.uuid4().hex[:8]}")
        db_session.add(theirs)
        await db_session.flush()
        theirs_id = theirs.id

    db_session.expunge_all()

    # DEFAULT_ORG context: sees mine, not theirs.
    ids_a = {
        c.id for c in (await db_session.execute(select(Curriculum))).scalars().all()
    }
    assert mine_id in ids_a
    assert theirs_id not in ids_a

    # Switch to other_org context: sees theirs, not mine — proves per-query rebind.
    with use_org(other_org):
        db_session.expunge_all()
        ids_b = {
            c.id
            for c in (await db_session.execute(select(Curriculum))).scalars().all()
        }
    assert theirs_id in ids_b
    assert mine_id not in ids_b


async def test_sota_finding_is_tenant_filtered(db_session):
    """SotaFinding (a TenantScoped entity) IS isolated cross-tenant — completing
    the coverage beside the global SotaSource exemption."""
    other_org = await _make_other_org(db_session)

    cur = Curriculum(name="FindingCur", slug=f"fc-{uuid.uuid4().hex[:8]}")
    db_session.add(cur)
    await db_session.flush()
    mine = SotaFinding(curriculum_id=cur.id, topic="mine", coverage_status="absent")
    db_session.add(mine)
    await db_session.flush()
    mine_id = mine.id

    with use_org(other_org):
        cur2 = Curriculum(name="FindingCur2", slug=f"fc2-{uuid.uuid4().hex[:8]}")
        db_session.add(cur2)
        await db_session.flush()
        theirs = SotaFinding(
            curriculum_id=cur2.id, topic="theirs", coverage_status="absent"
        )
        db_session.add(theirs)
        await db_session.flush()
        theirs_id = theirs.id

    db_session.expunge_all()

    ids = {f.id for f in (await db_session.execute(select(SotaFinding))).scalars().all()}
    assert mine_id in ids
    assert theirs_id not in ids
    assert (await db_session.get(SotaFinding, theirs_id)) is None
