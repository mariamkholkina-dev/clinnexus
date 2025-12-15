from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.conflicts import ConflictOut
from app.services.conflicts import ConflictService

router = APIRouter()


@router.get(
    "/conflicts",
    response_model=list[ConflictOut],
)
async def list_conflicts(
    study_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> list[ConflictOut]:
    """Список конфликтов для исследования."""
    conflict_service = ConflictService(db)
    conflicts = await conflict_service.detect_structured(study_id)
    return conflicts



