from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.core.errors import NotFoundError
from app.db.models.studies import Study
from app.db.models.facts import Fact, FactEvidence
from app.schemas.studies import StudyCreate, StudyOut
from app.schemas.facts import FactOut, FactEvidenceOut

router = APIRouter()


@router.post(
    "/studies",
    response_model=StudyOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_study(
    payload: StudyCreate,
    db: AsyncSession = Depends(get_db),
) -> StudyOut:
    """Создание нового исследования."""
    study = Study(
        workspace_id=payload.workspace_id,
        study_code=payload.study_code,
        title=payload.title,
        status=payload.status,
    )
    db.add(study)
    await db.commit()
    await db.refresh(study)
    return StudyOut.model_validate(study)


@router.get(
    "/studies/{study_id}",
    response_model=StudyOut,
)
async def get_study(
    study_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> StudyOut:
    """Получение исследования по ID."""
    study = await db.get(Study, study_id)
    if not study:
        raise NotFoundError("Study", str(study_id))
    return StudyOut.model_validate(study)


@router.get(
    "/studies",
    response_model=list[StudyOut],
)
async def list_studies(
    workspace_id: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[StudyOut]:
    """Список исследований."""
    stmt = select(Study)
    if workspace_id:
        stmt = stmt.where(Study.workspace_id == workspace_id)
    result = await db.execute(stmt)
    studies = result.scalars().all()
    return [StudyOut.model_validate(s) for s in studies]


@router.get(
    "/studies/{study_id}/facts",
    response_model=list[FactOut],
)
async def list_study_facts(
    study_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[FactOut]:
    """Список фактов исследования."""
    # Проверяем существование study
    study = await db.get(Study, study_id)
    if not study:
        raise NotFoundError("Study", str(study_id))

    # Получаем факты
    stmt = select(Fact).where(Fact.study_id == study_id)
    result = await db.execute(stmt)
    facts = result.scalars().all()

    # Получаем evidence для каждого факта
    facts_out = []
    for fact in facts:
        evidence_stmt = select(FactEvidence).where(FactEvidence.fact_id == fact.id)
        evidence_result = await db.execute(evidence_stmt)
        evidence_list = evidence_result.scalars().all()

        fact_out = FactOut.model_validate(fact)
        fact_out.evidence = [FactEvidenceOut.model_validate(e) for e in evidence_list]
        facts_out.append(fact_out)

    return facts_out



