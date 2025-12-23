"""Unit тесты для CoreFactsExtractor."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.anchors import Anchor
from app.db.models.core_facts import StudyCoreFacts
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import Document, DocumentVersion, Study
from app.db.models.topics import TopicEvidence
from app.db.enums import AnchorContentType, DocumentLanguage, FactStatus
from app.services.core_facts_extractor import CoreFactsExtractor


@pytest.mark.asyncio
async def test_extract_sample_size_from_statistics_zone():
    """Тест: CoreFactsExtractor извлекает sample_size из statistics zone."""
    # Arrange
    db = AsyncMock(spec=AsyncSession)
    extractor = CoreFactsExtractor(db)

    doc_version_id = uuid.uuid4()
    study_id = uuid.uuid4()

    # Мокаем DocumentVersion
    doc_version = MagicMock(spec=DocumentVersion)
    doc_version.document = MagicMock(spec=Document)
    doc_version.document.study_id = study_id
    db.get = AsyncMock(return_value=doc_version)

    # Мокаем Anchor с statistics zone
    anchor = MagicMock(spec=Anchor)
    anchor.anchor_id = "test_anchor_1"
    anchor.text_norm = "Total N = 120 participants"
    anchor.text_raw = "Total N = 120 participants"
    anchor.source_zone = "statistics"
    anchor.ordinal = 1

    # Мокаем результат запроса anchors
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [anchor]
    db.execute = AsyncMock(return_value=result_mock)

    # Act
    facts = await extractor.build(doc_version_id)

    # Assert
    assert facts["sample_size"] is not None
    assert facts["sample_size"]["value"] == 120
    assert facts["sample_size"]["unit"] == "participants"
    assert "test_anchor_1" in facts["citations"]["sample_size"]


@pytest.mark.asyncio
async def test_extract_sample_size_from_facts_kb():
    """Тест: CoreFactsExtractor извлекает sample_size из Facts KB."""
    # Arrange
    db = AsyncMock(spec=AsyncSession)
    extractor = CoreFactsExtractor(db)

    doc_version_id = uuid.uuid4()
    study_id = uuid.uuid4()

    # Мокаем DocumentVersion
    doc_version = MagicMock(spec=DocumentVersion)
    doc_version.document = MagicMock(spec=Document)
    doc_version.document.study_id = study_id
    db.get = AsyncMock(return_value=doc_version)

    # Мокаем Study
    study = MagicMock(spec=Study)
    study.title = "Test Study"
    db.get = AsyncMock(side_effect=[doc_version, study])

    # Мокаем Fact с planned_n_total
    fact = MagicMock(spec=Fact)
    fact.id = uuid.uuid4()
    fact.value_json = {"value": 200}
    fact.unit = "participants"

    # Мокаем FactEvidence
    evidence = MagicMock(spec=FactEvidence)
    evidence.anchor_id = "fact_anchor_1"

    # Мокаем результаты запросов
    fact_result = MagicMock()
    fact_result.scalar_one_or_none.return_value = fact
    evidence_result = MagicMock()
    evidence_result.all.return_value = [(evidence.anchor_id,)]
    anchors_result = MagicMock()
    anchors_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[fact_result, evidence_result, anchors_result])

    # Act
    facts = await extractor.build(doc_version_id)

    # Assert
    assert facts["sample_size"] is not None
    assert facts["sample_size"]["value"] == 200
    assert "fact_anchor_1" in facts["citations"]["sample_size"]


@pytest.mark.asyncio
async def test_get_latest_core_facts():
    """Тест: получение последних core facts для исследования."""
    # Arrange
    db = AsyncMock(spec=AsyncSession)
    extractor = CoreFactsExtractor(db)

    study_id = uuid.uuid4()

    # Мокаем StudyCoreFacts
    core_facts = MagicMock(spec=StudyCoreFacts)
    core_facts.study_id = study_id
    core_facts.facts_version = 2
    core_facts.facts_json = {"sample_size": {"value": 150}}

    # Мокаем результат запроса
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = core_facts
    db.execute = AsyncMock(return_value=result_mock)

    # Act
    result = await extractor.get_latest_core_facts(study_id)

    # Assert
    assert result is not None
    assert result.facts_version == 2
    assert result.facts_json["sample_size"]["value"] == 150


@pytest.mark.asyncio
async def test_save_core_facts():
    """Тест: сохранение core facts в БД."""
    # Arrange
    db = AsyncMock(spec=AsyncSession)
    extractor = CoreFactsExtractor(db)

    study_id = uuid.uuid4()
    facts_json = {"sample_size": {"value": 100}}

    # Мокаем результат запроса max version
    max_result = MagicMock()
    max_result.scalar_one.return_value = 1
    db.execute = AsyncMock(return_value=max_result)
    db.flush = AsyncMock()

    # Act
    result = await extractor.save_core_facts(
        study_id=study_id,
        facts_json=facts_json,
    )

    # Assert
    assert result is not None
    assert result.facts_version == 2  # next version after 1
    assert result.facts_json == facts_json
    db.add.assert_called_once()
    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_extract_phase_from_headers():
    """Тест: извлечение phase из заголовков."""
    # Arrange
    db = AsyncMock(spec=AsyncSession)
    extractor = CoreFactsExtractor(db)

    doc_version_id = uuid.uuid4()
    study_id = uuid.uuid4()

    # Мокаем DocumentVersion
    doc_version = MagicMock(spec=DocumentVersion)
    doc_version.document = MagicMock(spec=Document)
    doc_version.document.study_id = study_id
    db.get = AsyncMock(return_value=doc_version)

    # Мокаем Study
    study = MagicMock(spec=Study)
    study.title = "Test Study"
    db.get = AsyncMock(side_effect=[doc_version, study])

    # Мокаем Anchor с заголовком Phase
    header = MagicMock(spec=Anchor)
    header.anchor_id = "header_1"
    header.text_norm = "Phase III Study"
    header.text_raw = "Phase III Study"
    header.content_type = AnchorContentType.HDR
    header.ordinal = 1

    # Мокаем результаты запросов
    fact_result = MagicMock()
    fact_result.scalar_one_or_none.return_value = None
    evidence_result = MagicMock()
    evidence_result.all.return_value = []
    headers_result = MagicMock()
    headers_result.scalars.return_value.all.return_value = [header]
    anchors_result = MagicMock()
    anchors_result.scalars.return_value.all.return_value = []

    db.execute = AsyncMock(side_effect=[fact_result, evidence_result, headers_result, anchors_result])

    # Act
    facts = await extractor.build(doc_version_id)

    # Assert
    assert facts["phase"] == "III"
    assert "header_1" in facts["citations"]["phase"]

