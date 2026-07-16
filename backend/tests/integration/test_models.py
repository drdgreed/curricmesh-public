import pytest
from app.models.curriculum import Curriculum
from app.models.version import Version, LifecycleStatus


async def test_create_curriculum_with_version(db_session):
    cur = Curriculum(name="Agentic AI in Production", slug="agentic-ai")
    db_session.add(cur)
    await db_session.flush()
    v = Version(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.draft,
    )
    db_session.add(v)
    await db_session.commit()
    assert v.id is not None and v.status == LifecycleStatus.draft
