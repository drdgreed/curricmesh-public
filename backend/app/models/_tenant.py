"""Shared tenant-scoping mixin for the curriculum-domain models.

Every RLS-protected domain table carries an identical ``organization_id``
column: NOT NULL, indexed, FK → organizations(id) ON DELETE CASCADE, and
**write-stamped** from the ambient tenant context (``app.tenant.require_org``)
so model constructors never pass it explicitly. Defined once here as a
declarative mixin and inherited so the column spec can't drift across the 13
domain tables.

``TenantScoped`` is also the marker the app-layer auto-filter
(``app.tenant_scope``) targets: ``with_loader_criteria(TenantScoped, ...)``
adds ``organization_id == current_org`` to every ORM SELECT touching any
subclass, isolating tenants in the application even when the DB role bypasses
Postgres RLS (the dev/CI superuser does).

This module must import only ``app.tenant`` + SQLAlchemy — ``app.models``
imports it, so it must never reach back into ``app.models`` (import cycle).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.tenant import require_org


class TenantScoped:
    """Declarative mixin: the tenant-scoping ``organization_id`` column.

    Write-stamped from the ambient org context (fail-closed: ``require_org()``
    raises if unset). Using ``@declared_attr`` makes SQLAlchemy materialize a
    *fresh, independent* ``mapped_column`` on each subclass table, so the DDL is
    identical to (and as separate as) the old per-model ``org_id_column()``.
    """

    @declared_attr
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
            default=lambda: require_org(),
        )
