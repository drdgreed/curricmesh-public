"""Router: GET /api/v1/assets/{asset_id}/diff — version diff endpoint (Task B4)."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import get_current_user
from app.database import get_db
from app.core.diff.service import DiffError, diff_versions
from app.schemas.assets import DiffOut, StructuredDiffOut, TextDiffOut

router = APIRouter(prefix="/api/v1/assets", tags=["diff"])


@router.get("/{asset_id}/diff", response_model=DiffOut)
async def get_asset_diff(
    asset_id: uuid.UUID,
    from_: uuid.UUID = Query(..., alias="from"),
    to: uuid.UUID = Query(...),
    current: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DiffOut:
    """Diff two versions of an asset.

    Query params:
        from: UUID of the "before" AssetVersion.
        to:   UUID of the "after" AssetVersion.

    Both versions must belong to asset_id — 404 otherwise.
    Returns a DiffOut with either .text or .structured populated.
    """
    try:
        result = await diff_versions(db, asset_id, from_, to)
    except DiffError as exc:
        # Malformed content in the stored version body — not a client routing error
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        # Not found or cross-asset membership failure
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Convert core dataclasses → Pydantic output schemas
    text_out = (
        TextDiffOut(
            added=result.text.added,
            removed=result.text.removed,
            unified=result.text.unified,
        )
        if result.text is not None
        else None
    )
    structured_out = (
        StructuredDiffOut(
            added=result.structured.added,
            removed=result.structured.removed,
            changed=result.structured.changed,
        )
        if result.structured is not None
        else None
    )

    return DiffOut(kind=result.kind, text=text_out, structured=structured_out)
