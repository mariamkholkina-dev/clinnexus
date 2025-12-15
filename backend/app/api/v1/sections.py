from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.core.errors import NotFoundError
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import DocumentVersion
from app.db.enums import DocumentType, SectionMapMappedBy, SectionMapStatus
from app.schemas.sections import (
    SectionContractCreate,
    SectionContractOut,
    SectionMapOut,
    SectionMapOverrideRequest,
)

router = APIRouter()


@router.get(
    "/section-contracts",
    response_model=list[SectionContractOut],
)
async def list_section_contracts(
    doc_type: DocumentType | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[SectionContractOut]:
    """Список контрактов секций."""
    stmt = select(SectionContract)
    if doc_type:
        stmt = stmt.where(SectionContract.doc_type == doc_type)
    if is_active is not None:
        stmt = stmt.where(SectionContract.is_active == is_active)

    result = await db.execute(stmt)
    contracts = result.scalars().all()
    return [SectionContractOut.model_validate(c) for c in contracts]


@router.post(
    "/section-contracts",
    response_model=SectionContractOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_section_contract(
    payload: SectionContractCreate,
    db: AsyncSession = Depends(get_db),
) -> SectionContractOut:
    """Создание нового контракта секции."""
    contract = SectionContract(
        workspace_id=payload.workspace_id,
        doc_type=payload.doc_type,
        section_key=payload.section_key,
        title=payload.title,
        required_facts_json=payload.required_facts_json,
        allowed_sources_json=payload.allowed_sources_json,
        retrieval_recipe_json=payload.retrieval_recipe_json,
        qc_ruleset_json=payload.qc_ruleset_json,
        citation_policy=payload.citation_policy,
        version=payload.version,
        is_active=payload.is_active,
    )
    db.add(contract)
    await db.commit()
    await db.refresh(contract)
    return SectionContractOut.model_validate(contract)


@router.get(
    "/document-versions/{version_id}/section-maps",
    response_model=list[SectionMapOut],
)
async def list_section_maps(
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[SectionMapOut]:
    """Список маппингов секций для версии документа."""
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    stmt = select(SectionMap).where(SectionMap.doc_version_id == version_id)
    result = await db.execute(stmt)
    maps = result.scalars().all()
    return [SectionMapOut.model_validate(m) for m in maps]


@router.post(
    "/document-versions/{version_id}/section-maps/{section_key}/override",
    response_model=SectionMapOut,
)
async def override_section_map(
    version_id: UUID,
    section_key: str,
    payload: SectionMapOverrideRequest,
    db: AsyncSession = Depends(get_db),
) -> SectionMapOut:
    """Переопределение маппинга секции пользователем."""
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    # Ищем существующий маппинг
    stmt = select(SectionMap).where(
        SectionMap.doc_version_id == version_id,
        SectionMap.section_key == section_key,
    )
    result = await db.execute(stmt)
    section_map = result.scalar_one_or_none()

    if section_map:
        # Обновляем существующий
        if payload.anchor_ids is not None:
            section_map.anchor_ids = payload.anchor_ids
        if payload.chunk_ids is not None:
            section_map.chunk_ids = payload.chunk_ids
        if payload.notes is not None:
            section_map.notes = payload.notes
        section_map.status = SectionMapStatus.OVERRIDDEN
        section_map.mapped_by = SectionMapMappedBy.USER
    else:
        # Создаём новый
        section_map = SectionMap(
            doc_version_id=version_id,
            section_key=section_key,
            anchor_ids=payload.anchor_ids,
            chunk_ids=payload.chunk_ids,
            confidence=1.0,  # Пользовательский маппинг имеет максимальный confidence
            status=SectionMapStatus.OVERRIDDEN,
            mapped_by=SectionMapMappedBy.USER,
            notes=payload.notes,
        )
        db.add(section_map)

    await db.commit()
    await db.refresh(section_map)
    return SectionMapOut.model_validate(section_map)

