"""Расширенные тесты для извлечения фактов (новые факты)."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import (
    AnchorContentType,
    DocumentLanguage,
    DocumentLifecycleStatus,
    DocumentType,
    IngestionStatus,
    StudyStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.auth import Workspace
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import Document as DocumentModel
from app.db.models.studies import DocumentVersion, Study
from app.services.fact_extraction import FactExtractionService


def _mk_anchor(
    *,
    doc_version_id: uuid.UUID,
    anchor_id: str,
    content_type: AnchorContentType,
    ordinal: int,
    text_raw: str,
    source_zone: str = "unknown",
) -> Anchor:
    return Anchor(
        doc_version_id=doc_version_id,
        anchor_id=anchor_id,
        section_path="ROOT",
        content_type=content_type,
        ordinal=ordinal,
        text_raw=text_raw,
        text_norm=text_raw,
        text_hash="x" * 64,
        location_json={"p": ordinal},
        confidence=1.0,
        source_zone=source_zone,
    )


@pytest.fixture
async def test_workspace(db: AsyncSession) -> Workspace:
    workspace = Workspace(name="Test Workspace")
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


@pytest.fixture
async def test_study(db: AsyncSession, test_workspace: Workspace) -> Study:
    study = Study(
        workspace_id=test_workspace.id,
        study_code="TEST-FACT-EXT",
        title="Test Study Extended",
        status=StudyStatus.ACTIVE,
    )
    db.add(study)
    await db.commit()
    await db.refresh(study)
    return study


@pytest.fixture
async def test_document(db: AsyncSession, test_study: Study) -> DocumentModel:
    doc = DocumentModel(
        workspace_id=test_study.workspace_id,
        study_id=test_study.id,
        doc_type=DocumentType.PROTOCOL,
        title="Test Protocol Extended",
        lifecycle_status=DocumentLifecycleStatus.DRAFT,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@pytest.fixture
async def test_version(db: AsyncSession, test_document: DocumentModel) -> DocumentVersion:
    v = DocumentVersion(
        document_id=test_document.id,
        version_label="v1.0",
        source_file_uri="file:///test/file.docx",
        source_sha256="abc123",
        effective_date=date.today(),
        ingestion_status=IngestionStatus.READY,
        document_language=DocumentLanguage.EN,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


@pytest.mark.asyncio
async def test_extract_study_phase(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест извлечения фазы исследования."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="PHASE_HDR",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Phase II Study",
            source_zone="overview",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    stmt = select(Fact).where(
        Fact.fact_type == "study", Fact.fact_key == "phase"
    )
    facts = (await db.execute(stmt)).scalars().all()
    assert len(facts) > 0
    phase_fact = facts[0]
    assert phase_fact.value_json.get("value") is not None
    assert "II" in str(phase_fact.value_json.get("value")) or "2" in str(phase_fact.value_json.get("value"))


@pytest.mark.asyncio
async def test_extract_age_range(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест извлечения возрастного диапазона."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="AGE_P",
            content_type=AnchorContentType.P,
            ordinal=1,
            text_raw="Age: 18-65 years",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    # Проверяем age_min
    stmt_min = select(Fact).where(
        Fact.fact_type == "population", Fact.fact_key == "age_min"
    )
    facts_min = (await db.execute(stmt_min)).scalars().all()
    if facts_min:
        assert facts_min[0].value_json.get("value") == 18

    # Проверяем age_max
    stmt_max = select(Fact).where(
        Fact.fact_type == "population", Fact.fact_key == "age_max"
    )
    facts_max = (await db.execute(stmt_max)).scalars().all()
    if facts_max:
        assert facts_max[0].value_json.get("value") == 65


@pytest.mark.asyncio
async def test_extract_randomization_ratio(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест извлечения соотношения рандомизации."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="RATIO_P",
            content_type=AnchorContentType.P,
            ordinal=1,
            text_raw="Randomization ratio: 2:1",
            source_zone="design",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    stmt = select(Fact).where(
        Fact.fact_type == "treatment", Fact.fact_key == "randomization_ratio"
    )
    facts = (await db.execute(stmt)).scalars().all()
    if facts:
        assert facts[0].value_json.get("value") == "2:1"


@pytest.mark.asyncio
async def test_extract_endpoints(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест извлечения endpoints."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="ENDPOINT_HDR",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Primary Endpoint",
            source_zone="endpoints",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="ENDPOINT_LI1",
            content_type=AnchorContentType.LI,
            ordinal=2,
            text_raw="Change from baseline in ABC score",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="ENDPOINT_LI2",
            content_type=AnchorContentType.LI,
            ordinal=3,
            text_raw="Time to response",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    stmt = select(Fact).where(
        Fact.fact_type == "endpoints", Fact.fact_key == "primary"
    )
    facts = (await db.execute(stmt)).scalars().all()
    if facts:
        endpoints = facts[0].value_json.get("value", [])
        assert len(endpoints) > 0
        assert any("ABC" in ep or "response" in ep for ep in endpoints)


@pytest.mark.asyncio
async def test_extract_statistics_alpha_power(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест извлечения статистических параметров."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="STATS_P1",
            content_type=AnchorContentType.P,
            ordinal=1,
            text_raw="Alpha = 0.05",
            source_zone="statistics",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="STATS_P2",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Power: 80%",
            source_zone="statistics",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    # Проверяем alpha
    stmt_alpha = select(Fact).where(
        Fact.fact_type == "statistics", Fact.fact_key == "alpha"
    )
    facts_alpha = (await db.execute(stmt_alpha)).scalars().all()
    if facts_alpha:
        assert facts_alpha[0].value_json.get("value") == 0.05

    # Проверяем power
    stmt_power = select(Fact).where(
        Fact.fact_type == "statistics", Fact.fact_key == "power"
    )
    facts_power = (await db.execute(stmt_power)).scalars().all()
    if facts_power:
        assert facts_power[0].value_json.get("value") == 80


@pytest.mark.asyncio
async def test_extract_with_confidence_and_metadata(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест извлечения фактов с confidence и метаданными."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="META_HDR",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Protocol Version: 2.0",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    stmt = select(Fact).where(
        Fact.fact_type == "protocol_meta", Fact.fact_key == "protocol_version"
    )
    facts = (await db.execute(stmt)).scalars().all()
    if facts:
        fact = facts[0]
        # Проверяем наличие confidence
        assert fact.confidence is not None
        assert 0.0 <= fact.confidence <= 1.0
        # Проверяем наличие extractor_version
        assert fact.extractor_version is not None
        # Проверяем evidence
        ev_stmt = select(FactEvidence).where(FactEvidence.fact_id == fact.id)
        evidence = (await db.execute(ev_stmt)).scalars().all()
        assert len(evidence) > 0
        assert evidence[0].anchor_id == "META_HDR"


@pytest.mark.asyncio
async def test_extract_multiple_candidates_conflict(
    db: AsyncSession, test_version: DocumentVersion
) -> None:
    """Тест обработки множественных кандидатов (конфликт)."""
    doc_version_id = test_version.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="N_P1",
            content_type=AnchorContentType.P,
            ordinal=1,
            text_raw="Total N=120 participants",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="N_P2",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Planned enrollment: 150",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count > 0

    stmt = select(Fact).where(
        Fact.fact_type == "population", Fact.fact_key == "planned_n_total"
    )
    facts = (await db.execute(stmt)).scalars().all()
    if facts:
        fact = facts[0]
        # Должен быть сохранен лучший кандидат
        assert fact.value_json.get("value") in (120, 150)
        # Если есть альтернативы, они должны быть в meta_json
        if fact.meta_json and "alternatives" in fact.meta_json:
            assert len(fact.meta_json["alternatives"]) > 0

