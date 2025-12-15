from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.db.models.change import Task
from app.schemas.impact import ImpactItemOut
from app.schemas.tasks import TaskOut
from app.services.impact import ImpactService

router = APIRouter()


@router.get(
    "/impact",
    response_model=list[ImpactItemOut],
)
async def list_impact(
    study_id: UUID = Query(...),
    change_event_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[ImpactItemOut]:
    """Список элементов воздействия для исследования."""
    if change_event_id:
        impact_service = ImpactService(db)
        impact_items = await impact_service.compute_impact(change_event_id)
    else:
        # TODO: получить impact_items по study_id
        impact_items = []
    return impact_items


@router.get(
    "/tasks",
    response_model=list[TaskOut],
)
async def list_tasks(
    study_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> list[TaskOut]:
    """Список задач для исследования."""
    stmt = select(Task).where(Task.study_id == study_id)
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    return [TaskOut.model_validate(t) for t in tasks]



