from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db
from app.core.audit import log_audit
from app.core.errors import NotFoundError, ValidationError
from app.db.enums import EvidenceRole
from app.db.models.studies import Document, DocumentVersion, Study
from app.db.models.facts import Fact, FactEvidence
from app.db.models.core_facts import StudyCoreFacts
from app.db.models.auth import Workspace
from app.schemas.studies import StudyCreate, StudyOut
from app.schemas.documents import DocumentOut, DocumentVersionOut
from app.schemas.facts import FactOut, FactEvidenceOut
from app.services.core_facts_extractor import CoreFactsExtractor

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
    # Проверяем существование workspace
    workspace = await db.get(Workspace, payload.workspace_id)
    if not workspace:
        raise ValidationError(
            f"Workspace с id {payload.workspace_id} не найден",
            details={"workspace_id": str(payload.workspace_id)},
        )
    
    try:
        study = Study(
            workspace_id=payload.workspace_id,
            study_code=payload.study_code,
            title=payload.title,
            status=payload.status,
        )
        db.add(study)
        await db.commit()
        await db.refresh(study)
        
        # Audit logging
        await log_audit(
            db=db,
            workspace_id=payload.workspace_id,
            action="create",
            entity_type="study",
            entity_id=str(study.id),
            after_json={
                "study_code": payload.study_code,
                "title": payload.title,
                "status": payload.status.value,
            },
        )
        await db.commit()
        
        return StudyOut.model_validate(study)
    except IntegrityError as e:
        await db.rollback()
        # Проверяем, является ли это ошибкой уникальности
        if "unique" in str(e.orig).lower() or "duplicate" in str(e.orig).lower():
            raise ValidationError(
                "Исследование с таким кодом уже существует",
                details={"study_code": payload.study_code},
            )
        # Другие ошибки целостности
        raise ValidationError(
            "Ошибка при создании исследования. Проверьте корректность данных.",
            details={"error": str(e.orig)},
        )


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
    "/studies/{study_id}/documents",
    response_model=list[DocumentOut],
)
async def list_study_documents(
    study_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentOut]:
    """Список документов исследования."""
    # Проверяем существование study
    study = await db.get(Study, study_id)
    if not study:
        raise NotFoundError("Study", str(study_id))
    
    # Получаем документы
    stmt = select(Document).where(Document.study_id == study_id)
    result = await db.execute(stmt)
    documents = result.scalars().all()
    return [DocumentOut.model_validate(d) for d in documents]


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionOut],
)
async def list_document_versions(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentVersionOut]:
    """Список версий документа."""
    # Проверяем существование document
    document = await db.get(Document, document_id)
    if not document:
        raise NotFoundError("Document", str(document_id))
    
    # Получаем версии
    stmt = select(DocumentVersion).where(DocumentVersion.document_id == document_id)
    result = await db.execute(stmt)
    versions = result.scalars().all()
    return [DocumentVersionOut.model_validate(v) for v in versions]


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
        # Если у evidence отсутствует роль, проставляем PRIMARY по умолчанию для совместимости.
        fact_out.evidence = []
        for e in evidence_list:
            # Убеждаемся, что evidence_role установлен (дефолт PRIMARY)
            evidence_role = getattr(e, "evidence_role", None) or EvidenceRole.PRIMARY
            # Создаём словарь с правильными ключами для Pydantic (используем alias)
            evidence_data = {
                "anchor_id": e.anchor_id,
                "role": evidence_role,  # Используем имя поля из схемы (alias)
            }
            fact_out.evidence.append(FactEvidenceOut.model_validate(evidence_data))
        facts_out.append(fact_out)

    return facts_out


@router.get(
    "/studies/{study_id}/core-facts",
    response_model=dict,
)
async def get_study_core_facts(
    study_id: UUID,
    version: int | None = Query(None, description="Номер версии core facts (если не указан, возвращается latest)"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Получение основных фактов исследования (Core Study Facts).
    
    Args:
        study_id: UUID исследования
        version: Опциональный номер версии (если не указан, возвращается latest)
    
    Returns:
        Словарь с core facts (facts_json) и метаданными
    """
    # Проверяем существование study
    study = await db.get(Study, study_id)
    if not study:
        raise NotFoundError("Study", str(study_id))

    # Получаем core facts
    extractor = CoreFactsExtractor(db)
    core_facts = await extractor.get_latest_core_facts(study_id, version=version)

    if not core_facts:
        return {
            "study_id": str(study_id),
            "version": None,
            "facts_json": {},
            "derived_from_doc_version_id": None,
            "created_at": None,
        }

    return {
        "study_id": str(core_facts.study_id),
        "version": core_facts.facts_version,
        "facts_json": core_facts.facts_json,
        "derived_from_doc_version_id": str(core_facts.derived_from_doc_version_id) if core_facts.derived_from_doc_version_id else None,
        "doc_version_id": str(core_facts.doc_version_id) if core_facts.doc_version_id else None,
        "created_at": core_facts.created_at.isoformat() if core_facts.created_at else None,
    }



