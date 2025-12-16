from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import logger
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import DocumentVersion
from app.db.enums import DocumentType, SectionMapMappedBy, SectionMapStatus
from app.schemas.sections import (
    CandidateOut,
    SectionContractCreate,
    SectionContractOut,
    SectionMapOut,
    SectionMapOverrideRequest,
    SectionMappingAssistRequest,
    SectionMappingAssistResponse,
    SectionQCOut,
)
from app.services.section_mapping import SectionMappingService
from app.services.section_mapping_assist import SectionMappingAssistService

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
    # MVP: редактирование/создание паспортов через API запрещено по умолчанию.
    # Паспорта загружаются сидером из репозитория.
    if not settings.enable_contract_editing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Редактирование section contracts отключено в MVP. "
                "Используйте сидер паспортов из репозитория."
            ),
        )
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


@router.post(
    "/document-versions/{version_id}/section-maps/rebuild",
    status_code=status.HTTP_200_OK,
)
async def rebuild_section_maps(
    version_id: UUID,
    force: bool = Query(False, description="Пересоздать все маппинги (кроме overridden)"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Перезапуск маппинга секций для версии документа."""
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    logger.info(f"Перезапуск маппинга секций для version_id={version_id}, force={force}")

    # Запускаем маппинг
    section_mapping_service = SectionMappingService(db)
    mapping_summary = await section_mapping_service.map_sections(version_id, force=force)

    return {
        "version_id": str(version_id),
        "sections_mapped_count": mapping_summary.sections_mapped_count,
        "sections_needs_review_count": mapping_summary.sections_needs_review_count,
        "mapping_warnings": mapping_summary.mapping_warnings,
    }


@router.post(
    "/document-versions/{version_id}/section-maps/assist",
    response_model=SectionMappingAssistResponse,
    status_code=status.HTTP_200_OK,
)
async def assist_section_mapping(
    version_id: UUID,
    payload: SectionMappingAssistRequest,
    db: AsyncSession = Depends(get_db),
) -> SectionMappingAssistResponse:
    """
    LLM-assisted section mapping.

    LLM предлагает кандидатов заголовков, затем система прогоняет детерминированный QC Gate.
    Если apply=true и QC пройден, обновляет section_maps (не трогает overridden).
    """
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    # Проверяем secure_mode
    if not settings.secure_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "SECURE_MODE=false. LLM вызовы запрещены. "
                "Установите SECURE_MODE=true и настройте LLM ключи."
            ),
        )

    # Проверяем наличие ключей
    if not settings.llm_provider or not settings.llm_base_url or not settings.llm_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "LLM не настроен. Требуются: LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY. "
                "Убедитесь, что SECURE_MODE=true."
            ),
        )

    logger.info(
        f"LLM-assisted mapping для version_id={version_id}, "
        f"section_keys={payload.section_keys}, apply={payload.apply}"
    )

    # Вызываем assist service
    assist_service = SectionMappingAssistService(db)
    try:
        result = await assist_service.assist(
            doc_version_id=version_id,
            section_keys=payload.section_keys,
            max_candidates_per_section=payload.max_candidates_per_section,
            allow_visual_headings=payload.allow_visual_headings,
            apply=payload.apply,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        # Ошибка LLM/внешнего провайдера: это не ошибка запроса клиента, поэтому 502.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    # Преобразуем в response schema
    return SectionMappingAssistResponse(
        version_id=result.version_id,
        document_language=result.document_language,
        secure_mode=result.secure_mode,
        llm_used=result.llm_used,
        candidates={
            section_key: [
                CandidateOut(
                    heading_anchor_id=c["heading_anchor_id"],
                    confidence=c["confidence"],
                    rationale=c["rationale"],
                )
                for c in candidates
            ]
            for section_key, candidates in result.candidates.items()
        },
        qc={
            section_key: SectionQCOut(
                status=report.status,
                selected_heading_anchor_id=report.selected_heading_anchor_id,
                errors=report.errors,
            )
            for section_key, report in result.qc.items()
        },
    )

