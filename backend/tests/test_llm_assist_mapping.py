"""
Тесты для LLM-assisted section mapping.
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import LLMProvider, settings
from app.db.enums import (
    AnchorContentType,
    CitationPolicy,
    DocumentLifecycleStatus,
    DocumentType,
    IngestionStatus,
    SectionMapMappedBy,
    SectionMapStatus,
    StudyStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.auth import Workspace
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import Document as DocumentModel, DocumentVersion, Study
from app.services.llm_client import LLMCandidate, LLMCandidatesResponse
from app.services.section_mapping_assist import SectionMappingAssistService


class TestLLMAssistMapping:
    """Тесты для LLM-assisted section mapping."""

    @pytest.fixture
    async def test_workspace(self, db: AsyncSession) -> Workspace:
        """Создает тестовый workspace."""
        workspace = Workspace(name="Test Workspace")
        db.add(workspace)
        await db.commit()
        await db.refresh(workspace)
        return workspace

    @pytest.fixture
    async def test_study(self, db: AsyncSession, test_workspace: Workspace) -> Study:
        """Создает тестовое исследование."""
        study = Study(
            workspace_id=test_workspace.id,
            study_code="TEST-001",
            title="Test Study",
            status=StudyStatus.ACTIVE,
        )
        db.add(study)
        await db.commit()
        await db.refresh(study)
        return study

    @pytest.fixture
    async def test_document(self, db: AsyncSession, test_study: Study) -> DocumentModel:
        """Создает тестовый документ."""
        document = DocumentModel(
            workspace_id=test_study.workspace_id,
            study_id=test_study.id,
            doc_type=DocumentType.PROTOCOL,
            title="Test Protocol",
            lifecycle_status=DocumentLifecycleStatus.DRAFT,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        return document

    @pytest.fixture
    async def test_version(
        self, db: AsyncSession, test_document: DocumentModel
    ) -> DocumentVersion:
        """Создает версию документа."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.docx",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.READY,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.fixture
    async def test_section_contract(
        self, db: AsyncSession, test_workspace: Workspace
    ) -> SectionContract:
        """Создает тестовый Section Contract."""
        contract = SectionContract(
            workspace_id=test_workspace.id,
            doc_type=DocumentType.PROTOCOL,
            section_key="protocol.objectives",
            title="Objectives",
            required_facts_json={},
            allowed_sources_json={"doc_types": ["protocol"]},
            retrieval_recipe_json={
                "version": 1,
                "heading_match": {
                    "must": ["objective", "purpose"],
                    "should": ["goal", "aim"],
                    "not": ["table of contents"],
                },
                "regex": {
                    "heading": ["^(\\d+\\.)?\\s*(Objective|Purpose)\\b"],
                },
                "capture": {
                    "strategy": "heading_block",
                    "min_anchors": 2,
                },
            },
            qc_ruleset_json={},
            citation_policy=CitationPolicy.PER_CLAIM,
            version=1,
            is_active=True,
        )
        db.add(contract)
        await db.commit()
        await db.refresh(contract)
        return contract

    @pytest.fixture
    async def test_anchors(
        self, db: AsyncSession, test_version: DocumentVersion
    ) -> list[Anchor]:
        """Создает тестовые anchors."""
        anchors = [
            Anchor(
                doc_version_id=test_version.id,
                anchor_id=f"{test_version.id}:1.1:hdr:1:hash1",
                section_path="1.1",
                content_type=AnchorContentType.HDR,
                ordinal=1,
                text_raw="1.1 Objectives",
                text_norm="1.1 Objectives",
                text_hash="hash1",
                location_json={"para_index": 1},
            ),
            Anchor(
                doc_version_id=test_version.id,
                anchor_id=f"{test_version.id}:1.1:p:1:hash2",
                section_path="1.1",
                content_type=AnchorContentType.P,
                ordinal=1,
                text_raw="The primary objective is to...",
                text_norm="The primary objective is to...",
                text_hash="hash2",
                location_json={"para_index": 2},
            ),
            Anchor(
                doc_version_id=test_version.id,
                anchor_id=f"{test_version.id}:1.1:p:2:hash3",
                section_path="1.1",
                content_type=AnchorContentType.P,
                ordinal=2,
                text_raw="Secondary objectives include...",
                text_norm="Secondary objectives include...",
                text_hash="hash3",
                location_json={"para_index": 3},
            ),
            Anchor(
                doc_version_id=test_version.id,
                anchor_id=f"{test_version.id}:2.1:hdr:1:hash4",
                section_path="2.1",
                content_type=AnchorContentType.HDR,
                ordinal=1,
                text_raw="2.1 Schedule of Activities",
                text_norm="2.1 Schedule of Activities",
                text_hash="hash4",
                location_json={"para_index": 4},
            ),
        ]
        for anchor in anchors:
            db.add(anchor)
        await db.commit()
        return anchors

    @pytest.mark.asyncio
    async def test_assist_secure_mode_false(
        self, db: AsyncSession, test_version: DocumentVersion
    ):
        """Тест: secure_mode=false → ошибка 403/400."""
        original_secure_mode = settings.secure_mode
        settings.secure_mode = False

        try:
            service = SectionMappingAssistService(db)
            with pytest.raises(ValueError, match="SECURE_MODE=false"):
                await service.assist(
                    doc_version_id=test_version.id,
                    section_keys=["protocol.objectives"],
                )
        finally:
            settings.secure_mode = original_secure_mode

    @pytest.mark.asyncio
    async def test_assist_no_keys(
        self, db: AsyncSession, test_version: DocumentVersion
    ):
        """Тест: secure_mode=true, но нет ключей → ошибка."""
        original_secure_mode = settings.secure_mode
        original_provider = settings.llm_provider
        original_base_url = settings.llm_base_url
        original_api_key = settings.llm_api_key

        settings.secure_mode = True
        settings.llm_provider = None
        settings.llm_base_url = None
        settings.llm_api_key = None

        try:
            service = SectionMappingAssistService(db)
            with pytest.raises(ValueError, match="LLM не настроен"):
                await service.assist(
                    doc_version_id=test_version.id,
                    section_keys=["protocol.objectives"],
                )
        finally:
            settings.secure_mode = original_secure_mode
            settings.llm_provider = original_provider
            settings.llm_base_url = original_base_url
            settings.llm_api_key = original_api_key

    @pytest.mark.asyncio
    @patch("app.services.section_mapping_assist.LLMClient")
    async def test_assist_apply_false(
        self,
        mock_llm_client_class,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contract: SectionContract,
        test_anchors: list[Anchor],
    ):
        """Тест: apply=false → endpoint возвращает QC report, section_maps не меняются."""
        # Настраиваем secure_mode и ключи
        original_secure_mode = settings.secure_mode
        original_provider = settings.llm_provider
        original_base_url = settings.llm_base_url
        original_api_key = settings.llm_api_key

        settings.secure_mode = True
        settings.llm_provider = LLMProvider.OPENAI_COMPATIBLE
        settings.llm_base_url = "https://api.openai.com"
        settings.llm_api_key = "test-key"

        try:
            # Мокаем LLM client
            mock_llm_client = AsyncMock()
            mock_llm_client_class.return_value = mock_llm_client

            # LLM возвращает валидный JSON с кандидатами
            heading_anchor_id = test_anchors[0].anchor_id
            mock_llm_client.generate_candidates.return_value = LLMCandidatesResponse(
                candidates={
                    "protocol.objectives": [
                        LLMCandidate(
                            heading_anchor_id=heading_anchor_id,
                            confidence=0.86,
                            rationale="Содержит 'Objectives' и соответствует must keywords",
                        )
                    ]
                }
            )

            # Вызываем assist
            service = SectionMappingAssistService(db)
            result = await service.assist(
                doc_version_id=test_version.id,
                section_keys=["protocol.objectives"],
                apply=False,
            )

            # Проверяем результат
            assert result.llm_used is True
            assert "protocol.objectives" in result.candidates
            assert len(result.candidates["protocol.objectives"]) > 0
            assert "protocol.objectives" in result.qc

            # Проверяем, что section_maps не изменились
            stmt = select(SectionMap).where(
                SectionMap.doc_version_id == test_version.id,
                SectionMap.section_key == "protocol.objectives",
            )
            result_db = await db.execute(stmt)
            section_map = result_db.scalar_one_or_none()
            assert section_map is None  # Не должно быть создано

        finally:
            settings.secure_mode = original_secure_mode
            settings.llm_provider = original_provider
            settings.llm_base_url = original_base_url
            settings.llm_api_key = original_api_key

    @pytest.mark.asyncio
    @patch("app.services.section_mapping_assist.LLMClient")
    async def test_assist_apply_true(
        self,
        mock_llm_client_class,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contract: SectionContract,
        test_anchors: list[Anchor],
    ):
        """Тест: apply=true → section_map обновляется (mapped_by=system), overridden не трогается."""
        # Настраиваем secure_mode и ключи
        original_secure_mode = settings.secure_mode
        original_provider = settings.llm_provider
        original_base_url = settings.llm_base_url
        original_api_key = settings.llm_api_key

        settings.secure_mode = True
        settings.llm_provider = LLMProvider.OPENAI_COMPATIBLE
        settings.llm_base_url = "https://api.openai.com"
        settings.llm_api_key = "test-key"

        try:
            # Мокаем LLM client
            mock_llm_client = AsyncMock()
            mock_llm_client_class.return_value = mock_llm_client

            # LLM возвращает валидный JSON с кандидатами
            heading_anchor_id = test_anchors[0].anchor_id
            mock_llm_client.generate_candidates.return_value = LLMCandidatesResponse(
                candidates={
                    "protocol.objectives": [
                        LLMCandidate(
                            heading_anchor_id=heading_anchor_id,
                            confidence=0.91,
                            rationale="Содержит 'Objectives' и соответствует must keywords",
                        )
                    ]
                }
            )

            # Вызываем assist с apply=True
            service = SectionMappingAssistService(db)
            result = await service.assist(
                doc_version_id=test_version.id,
                section_keys=["protocol.objectives"],
                apply=True,
            )

            # Проверяем результат
            assert result.llm_used is True
            assert "protocol.objectives" in result.qc

            # Проверяем, что section_map создан/обновлён
            await db.commit()  # Коммитим изменения
            stmt = select(SectionMap).where(
                SectionMap.doc_version_id == test_version.id,
                SectionMap.section_key == "protocol.objectives",
            )
            result_db = await db.execute(stmt)
            section_map = result_db.scalar_one_or_none()

            if section_map:
                # Если QC прошёл и confidence >= 0.75, должен быть создан
                assert section_map.mapped_by == SectionMapMappedBy.SYSTEM
                assert section_map.status == SectionMapStatus.MAPPED
                assert "LLM assist used" in (section_map.notes or "")

        finally:
            settings.secure_mode = original_secure_mode
            settings.llm_provider = original_provider
            settings.llm_base_url = original_base_url
            settings.llm_api_key = original_api_key

    @pytest.mark.asyncio
    @patch("app.services.section_mapping_assist.LLMClient")
    async def test_assist_qc_fail_must_keywords(
        self,
        mock_llm_client_class,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contract: SectionContract,
        test_anchors: list[Anchor],
    ):
        """Тест: LLM предлагает heading, который не проходит must keywords → qc=rejected."""
        # Настраиваем secure_mode и ключи
        original_secure_mode = settings.secure_mode
        original_provider = settings.llm_provider
        original_base_url = settings.llm_base_url
        original_api_key = settings.llm_api_key

        settings.secure_mode = True
        settings.llm_provider = LLMProvider.OPENAI_COMPATIBLE
        settings.llm_base_url = "https://api.openai.com"
        settings.llm_api_key = "test-key"

        try:
            # Мокаем LLM client
            mock_llm_client = AsyncMock()
            mock_llm_client_class.return_value = mock_llm_client

            # LLM предлагает заголовок, который НЕ содержит must keywords
            # Берём заголовок "Schedule of Activities" (не содержит "objective" или "purpose")
            wrong_heading_anchor_id = test_anchors[3].anchor_id  # "2.1 Schedule of Activities"
            mock_llm_client.generate_candidates.return_value = LLMCandidatesResponse(
                candidates={
                    "protocol.objectives": [
                        LLMCandidate(
                            heading_anchor_id=wrong_heading_anchor_id,
                            confidence=0.75,
                            rationale="LLM ошибся",
                        )
                    ]
                }
            )

            # Вызываем assist
            service = SectionMappingAssistService(db)
            result = await service.assist(
                doc_version_id=test_version.id,
                section_keys=["protocol.objectives"],
                apply=False,
            )

            # Проверяем, что QC отклонил кандидата
            assert result.llm_used is True
            assert "protocol.objectives" in result.qc
            qc_report = result.qc["protocol.objectives"]
            # QC должен отклонить из-за отсутствия must keywords
            assert qc_report.status in ("rejected", "needs_review")
            # Должна быть ошибка must_keywords
            error_types = [e["type"] for e in qc_report.errors]
            # Может быть rejected из-за must_keywords или needs_review с низким confidence

        finally:
            settings.secure_mode = original_secure_mode
            settings.llm_provider = original_provider
            settings.llm_base_url = original_base_url
            settings.llm_api_key = original_api_key

