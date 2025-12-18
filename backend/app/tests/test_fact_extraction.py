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
        study_code="TEST-FACT-001",
        title="Test Study",
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
        title="Test Protocol",
        lifecycle_status=DocumentLifecycleStatus.DRAFT,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@pytest.fixture
async def test_version_en(db: AsyncSession, test_document: DocumentModel) -> DocumentVersion:
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


@pytest.fixture
async def test_version_ru(db: AsyncSession, test_document: DocumentModel) -> DocumentVersion:
    v = DocumentVersion(
        document_id=test_document.id,
        version_label="v1.0-ru",
        source_file_uri="file:///test/file-ru.docx",
        source_sha256="def456",
        effective_date=date.today(),
        ingestion_status=IngestionStatus.READY,
        document_language=DocumentLanguage.RU,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


@pytest.mark.asyncio
async def test_fact_extraction_en_creates_facts_and_real_evidence(
    db: AsyncSession, test_version_en: DocumentVersion
) -> None:
    doc_version_id = test_version_en.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_HDR_1",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Protocol Version: 2.0",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_2",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Amendment Date: 05 March 2021",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_3",
            content_type=AnchorContentType.P,
            ordinal=3,
            text_raw="Planned enrollment: Total N=120 participants",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    res = await svc.extract_and_upsert(doc_version_id)
    assert res.facts_count == 3

    stmt = select(Fact).where(Fact.created_from_doc_version_id == doc_version_id)
    facts = (await db.execute(stmt)).scalars().all()
    assert {(f.fact_type, f.fact_key) for f in facts} == {
        ("protocol_meta", "protocol_version"),
        ("protocol_meta", "amendment_date"),
        ("population", "planned_n_total"),
    }

    # Проверяем, что evidence ссылается на реальные anchor_id из anchors (и нет 'anchor_1')
    fact_ids = [f.id for f in facts]
    ev_stmt = select(FactEvidence).where(FactEvidence.fact_id.in_(fact_ids))
    evidence = (await db.execute(ev_stmt)).scalars().all()
    ev_anchor_ids = {e.anchor_id for e in evidence}
    assert ev_anchor_ids.issubset({a.anchor_id for a in anchors})
    assert "anchor_1" not in ev_anchor_ids
    assert all("anchor_1" not in e.anchor_id for e in evidence)

    # Статусы: все должны быть extracted
    assert all(f.status.value == "extracted" for f in facts)

    # Значения
    by_key = {(f.fact_type, f.fact_key): f for f in facts}
    assert by_key[("protocol_meta", "protocol_version")].value_json["value"] == "2.0"
    assert by_key[("protocol_meta", "amendment_date")].value_json["value"] == "2021-03-05"
    assert by_key[("population", "planned_n_total")].value_json["value"] == 120


@pytest.mark.asyncio
async def test_fact_extraction_ru_creates_facts_and_parses_date(
    db: AsyncSession, test_version_ru: DocumentVersion
) -> None:
    doc_version_id = test_version_ru.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="RU_HDR_1",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Версия протокола: 1.2",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="RU_P_2",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Дата внесения изменений: 05.03.2021",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="RU_LI_3",
            content_type=AnchorContentType.LI,
            ordinal=3,
            text_raw="Планируется включить 300 участников",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    await svc.extract_and_upsert(doc_version_id)

    stmt = select(Fact).where(Fact.created_from_doc_version_id == doc_version_id)
    facts = (await db.execute(stmt)).scalars().all()
    by_key = {(f.fact_type, f.fact_key): f for f in facts}

    assert by_key[("protocol_meta", "protocol_version")].value_json["value"] == "1.2"
    assert by_key[("protocol_meta", "amendment_date")].value_json["value"] == "2021-03-05"
    assert by_key[("population", "planned_n_total")].value_json["value"] == 300

    ev_stmt = select(FactEvidence)
    evidence = (await db.execute(ev_stmt)).scalars().all()
    assert {e.anchor_id for e in evidence}.issubset({a.anchor_id for a in anchors})
    assert "anchor_1" not in {e.anchor_id for e in evidence}


@pytest.mark.asyncio
async def test_fact_extraction_no_match_sets_needs_review_and_null_value(
    db: AsyncSession, test_version_en: DocumentVersion
) -> None:
    doc_version_id = test_version_en.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_X",
            content_type=AnchorContentType.P,
            ordinal=1,
            text_raw="This paragraph does not contain required fields.",
        )
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    await svc.extract_and_upsert(doc_version_id)

    stmt = select(Fact).where(Fact.created_from_doc_version_id == doc_version_id)
    facts = (await db.execute(stmt)).scalars().all()
    assert len(facts) == 3
    assert all(f.status.value == "needs_review" for f in facts)
    assert all(f.value_json.get("value") is None for f in facts)

    # Evidence может быть пустым, но если есть — только реальные anchor_id
    ev_stmt = select(FactEvidence)
    evidence = (await db.execute(ev_stmt)).scalars().all()
    assert all(e.anchor_id != "anchor_1" for e in evidence)


@pytest.mark.asyncio
async def test_fact_extraction_is_idempotent_for_fact_evidence(
    db: AsyncSession, test_version_en: DocumentVersion
) -> None:
    """Повторный прогон rules-first не должен увеличивать число fact_evidence для тех же фактов."""
    doc_version_id = test_version_en.id
    anchors = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_HDR_1",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Protocol Version: 2.0",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_2",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Amendment Date: 05 March 2021",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_3",
            content_type=AnchorContentType.P,
            ordinal=3,
            text_raw="Planned enrollment: Total N=120 participants",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors)  # type: ignore[method-assign]

    await svc.extract_and_upsert(doc_version_id)

    facts_stmt = select(Fact.id).where(Fact.created_from_doc_version_id == doc_version_id)
    fact_ids = (await db.execute(facts_stmt)).scalars().all()
    assert len(fact_ids) > 0

    ev_count_1 = len(
        (await db.execute(select(FactEvidence).where(FactEvidence.fact_id.in_(fact_ids)))).scalars().all()
    )
    assert ev_count_1 > 0

    # Второй прогон на тех же anchors
    await svc.extract_and_upsert(doc_version_id)

    facts_stmt2 = select(Fact.id).where(Fact.created_from_doc_version_id == doc_version_id)
    fact_ids2 = (await db.execute(facts_stmt2)).scalars().all()
    assert set(fact_ids2) == set(fact_ids)

    ev_count_2 = len(
        (await db.execute(select(FactEvidence).where(FactEvidence.fact_id.in_(fact_ids2)))).scalars().all()
    )
    assert ev_count_2 == ev_count_1
    assert all(e.anchor_id in {a.anchor_id for a in anchors} for e in evidence)


@pytest.mark.asyncio
async def test_fact_extraction_is_idempotent_replaces_evidence_on_update(
    db: AsyncSession, test_version_en: DocumentVersion
) -> None:
    doc_version_id = test_version_en.id

    anchors_v1 = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_HDR_V1",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Protocol Version: 1.0",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_DATE",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Amendment Date: 2021-03-05",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_N",
            content_type=AnchorContentType.P,
            ordinal=3,
            text_raw="Total N=100",
        ),
    ]
    anchors_v2 = [
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_HDR_V2",
            content_type=AnchorContentType.HDR,
            ordinal=1,
            text_raw="Protocol Version: 2.0",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_DATE",
            content_type=AnchorContentType.P,
            ordinal=2,
            text_raw="Amendment Date: 2021-03-05",
        ),
        _mk_anchor(
            doc_version_id=doc_version_id,
            anchor_id="A_P_N",
            content_type=AnchorContentType.P,
            ordinal=3,
            text_raw="Total N=120",
        ),
    ]

    svc = FactExtractionService(db)
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors_v1)  # type: ignore[method-assign]
    await svc.extract_and_upsert(doc_version_id)

    # Второй прогон с другим primary anchor_id для версии протокола
    svc._load_anchors_for_fact_extraction = AsyncMock(return_value=anchors_v2)  # type: ignore[method-assign]
    await svc.extract_and_upsert(doc_version_id)

    facts = (await db.execute(select(Fact))).scalars().all()
    assert len(facts) == 3  # upsert, не дублируем

    pv_fact = (
        await db.execute(select(Fact).where(Fact.fact_type == "protocol_meta", Fact.fact_key == "protocol_version"))
    ).scalar_one()
    assert pv_fact.value_json["value"] == "2.0"

    ev = (await db.execute(select(FactEvidence).where(FactEvidence.fact_id == pv_fact.id))).scalars().all()
    assert len(ev) == 1
    assert ev[0].anchor_id == "A_HDR_V2"


