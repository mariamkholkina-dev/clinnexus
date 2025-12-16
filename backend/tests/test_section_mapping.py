"""
Тесты для автоматического маппинга секций (Section Mapping).
"""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.enums import (
    AnchorContentType,
    CitationPolicy,
    DocumentLanguage,
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
from app.services.section_mapping import SectionMappingService


class TestSectionMapping:
    """Тесты для автоматического маппинга секций."""

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
    async def test_version(self, db: AsyncSession, test_document: DocumentModel) -> DocumentVersion:
        """Создает версию документа."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.docx",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.READY,
            document_language=DocumentLanguage.EN,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.fixture
    async def test_section_contracts(
        self, db: AsyncSession, test_workspace: Workspace
    ) -> list[SectionContract]:
        """Создает тестовые Section Contracts для протокола."""
        contracts = [
            SectionContract(
                workspace_id=test_workspace.id,
                doc_type=DocumentType.PROTOCOL,
                section_key="protocol.objectives",
                title="Objectives",
                required_facts_json={"primary_objective": {"type": "string"}},
                allowed_sources_json={"doc_types": ["protocol"]},
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["objective", "objectives"],
                        "should": [],
                        "not": [],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Objectives|Study Objectives)\\b"],
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={"required_fields": []},
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            SectionContract(
                workspace_id=test_workspace.id,
                doc_type=DocumentType.PROTOCOL,
                section_key="protocol.soa",
                title="Schedule of Activities",
                required_facts_json={"visits": []},
                allowed_sources_json={"doc_types": ["protocol"]},
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["schedule", "activities"],
                        "should": [],
                        "not": [],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Schedule of Activities|SoA)\\b"],
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={"required_fields": []},
                citation_policy=CitationPolicy.PER_CLAIM,
                version=1,
                is_active=True,
            ),
            SectionContract(
                workspace_id=test_workspace.id,
                doc_type=DocumentType.PROTOCOL,
                section_key="protocol.eligibility.inclusion",
                title="Inclusion Criteria",
                required_facts_json={"criteria": []},
                allowed_sources_json={"doc_types": ["protocol"]},
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["inclusion", "inclusion criteria"],
                        "should": [],
                        "not": ["exclusion"],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Inclusion Criteria|Inclusion)\\b"],
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={"required_fields": []},
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
        ]

        for contract in contracts:
            db.add(contract)
        await db.commit()

        for contract in contracts:
            await db.refresh(contract)

        return contracts

    @pytest.fixture
    async def test_anchors(
        self, db: AsyncSession, test_version: DocumentVersion
    ) -> list[Anchor]:
        """Создает тестовые anchors для документа."""
        anchors_data = [
            # Heading: Synopsis
            {
                "anchor_id": f"{test_version.id}:Synopsis:hdr:1:hash1",
                "section_path": "Synopsis",
                "content_type": AnchorContentType.HDR,
                "ordinal": 1,
                "text_raw": "Synopsis",
                "text_norm": "Synopsis",
                "text_hash": "hash1",
                "location_json": {"para_index": 1, "style": "Heading 1"},
            },
            {
                "anchor_id": f"{test_version.id}:Synopsis:p:1:hash2",
                "section_path": "Synopsis",
                "content_type": AnchorContentType.P,
                "ordinal": 1,
                "text_raw": "This is a synopsis paragraph.",
                "text_norm": "This is a synopsis paragraph.",
                "text_hash": "hash2",
                "location_json": {"para_index": 2, "style": "Normal"},
            },
            # Heading: Objectives
            {
                "anchor_id": f"{test_version.id}:Objectives:hdr:1:hash3",
                "section_path": "Objectives",
                "content_type": AnchorContentType.HDR,
                "ordinal": 1,
                "text_raw": "Study Objectives",
                "text_norm": "Study Objectives",
                "text_hash": "hash3",
                "location_json": {"para_index": 3, "style": "Heading 1"},
            },
            {
                "anchor_id": f"{test_version.id}:Objectives:p:1:hash4",
                "section_path": "Objectives",
                "content_type": AnchorContentType.P,
                "ordinal": 1,
                "text_raw": "The primary objective is to evaluate efficacy.",
                "text_norm": "The primary objective is to evaluate efficacy.",
                "text_hash": "hash4",
                "location_json": {"para_index": 4, "style": "Normal"},
            },
            {
                "anchor_id": f"{test_version.id}:Objectives:p:2:hash5",
                "section_path": "Objectives",
                "content_type": AnchorContentType.P,
                "ordinal": 2,
                "text_raw": "Secondary objectives include safety assessment.",
                "text_norm": "Secondary objectives include safety assessment.",
                "text_hash": "hash5",
                "location_json": {"para_index": 5, "style": "Normal"},
            },
            # Heading: Schedule of Activities
            {
                "anchor_id": f"{test_version.id}:Schedule of Activities:hdr:1:hash6",
                "section_path": "Schedule of Activities",
                "content_type": AnchorContentType.HDR,
                "ordinal": 1,
                "text_raw": "Schedule of Activities",
                "text_norm": "Schedule of Activities",
                "text_hash": "hash6",
                "location_json": {"para_index": 6, "style": "Heading 1"},
            },
            # Cell anchors для SoA таблицы
            {
                "anchor_id": f"{test_version.id}:Schedule of Activities:cell:1:hash7",
                "section_path": "Schedule of Activities",
                "content_type": AnchorContentType.CELL,
                "ordinal": 1,
                "text_raw": "Visit 1",
                "text_norm": "Visit 1",
                "text_hash": "hash7",
                "location_json": {"para_index": 7, "style": "Normal", "table_index": 0},
            },
            {
                "anchor_id": f"{test_version.id}:Schedule of Activities:cell:2:hash8",
                "section_path": "Schedule of Activities",
                "content_type": AnchorContentType.CELL,
                "ordinal": 2,
                "text_raw": "Day 1",
                "text_norm": "Day 1",
                "text_hash": "hash8",
                "location_json": {"para_index": 8, "style": "Normal", "table_index": 0},
            },
            # Heading: Inclusion Criteria
            {
                "anchor_id": f"{test_version.id}:Inclusion Criteria:hdr:1:hash9",
                "section_path": "Inclusion Criteria",
                "content_type": AnchorContentType.HDR,
                "ordinal": 1,
                "text_raw": "Inclusion Criteria",
                "text_norm": "Inclusion Criteria",
                "text_hash": "hash9",
                "location_json": {"para_index": 9, "style": "Heading 1"},
            },
            {
                "anchor_id": f"{test_version.id}:Inclusion Criteria:li:1:hash10",
                "section_path": "Inclusion Criteria",
                "content_type": AnchorContentType.LI,
                "ordinal": 1,
                "text_raw": "Age 18-65 years",
                "text_norm": "Age 18-65 years",
                "text_hash": "hash10",
                "location_json": {"para_index": 10, "style": "List Paragraph"},
            },
            {
                "anchor_id": f"{test_version.id}:Inclusion Criteria:li:2:hash11",
                "section_path": "Inclusion Criteria",
                "content_type": AnchorContentType.LI,
                "ordinal": 2,
                "text_raw": "Signed informed consent",
                "text_norm": "Signed informed consent",
                "text_hash": "hash11",
                "location_json": {"para_index": 11, "style": "List Paragraph"},
            },
            # Heading: Exclusion Criteria (не маппится, т.к. нет контракта)
            {
                "anchor_id": f"{test_version.id}:Exclusion Criteria:hdr:1:hash12",
                "section_path": "Exclusion Criteria",
                "content_type": AnchorContentType.HDR,
                "ordinal": 1,
                "text_raw": "Exclusion Criteria",
                "text_norm": "Exclusion Criteria",
                "text_hash": "hash12",
                "location_json": {"para_index": 12, "style": "Heading 1"},
            },
        ]

        anchors = []
        for data in anchors_data:
            anchor = Anchor(
                doc_version_id=test_version.id,
                **data,
            )
            anchors.append(anchor)
            db.add(anchor)

        await db.commit()

        for anchor in anchors:
            await db.refresh(anchor)

        return anchors

    @pytest.mark.asyncio
    async def test_map_sections_creates_mappings(
        self,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contracts: list[SectionContract],
        test_anchors: list[Anchor],
    ):
        """Тест, что маппинг создаёт SectionMap записи."""
        service = SectionMappingService(db)
        summary = await service.map_sections(test_version.id, force=False)

        # Проверяем, что маппинги созданы
        stmt = select(SectionMap).where(SectionMap.doc_version_id == test_version.id)
        result = await db.execute(stmt)
        section_maps = result.scalars().all()

        assert len(section_maps) > 0
        assert summary.sections_mapped_count > 0

        # Проверяем, что objectives маппинг создан
        objectives_map = next(
            (m for m in section_maps if m.section_key == "protocol.objectives"), None
        )
        assert objectives_map is not None
        assert objectives_map.status == SectionMapStatus.MAPPED
        assert objectives_map.mapped_by == SectionMapMappedBy.SYSTEM
        assert objectives_map.confidence >= 0.7
        assert objectives_map.anchor_ids is not None
        assert len(objectives_map.anchor_ids) > 0

        # Проверяем, что inclusion маппинг создан
        inclusion_map = next(
            (m for m in section_maps if m.section_key == "protocol.eligibility.inclusion"), None
        )
        assert inclusion_map is not None
        assert inclusion_map.status == SectionMapStatus.MAPPED
        assert inclusion_map.anchor_ids is not None
        assert len(inclusion_map.anchor_ids) > 0

        # Проверяем, что в inclusion маппинге есть list item anchors
        inclusion_anchor_ids = set(inclusion_map.anchor_ids)
        li_anchors = [
            a.anchor_id
            for a in test_anchors
            if a.content_type == AnchorContentType.LI
            and "Inclusion" in a.section_path
        ]
        assert any(li_id in inclusion_anchor_ids for li_id in li_anchors)

    @pytest.mark.asyncio
    async def test_map_sections_objectives_contains_correct_anchors(
        self,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contracts: list[SectionContract],
        test_anchors: list[Anchor],
    ):
        """Тест, что objectives маппинг содержит правильные anchors."""
        service = SectionMappingService(db)
        await service.map_sections(test_version.id, force=False)

        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == test_version.id,
            SectionMap.section_key == "protocol.objectives",
        )
        result = await db.execute(stmt)
        objectives_map = result.scalar_one_or_none()

        assert objectives_map is not None
        assert objectives_map.anchor_ids is not None

        # Проверяем, что в маппинге есть heading и параграфы из блока Objectives
        objectives_anchor_ids = set(objectives_map.anchor_ids)
        objectives_heading_id = next(
            (
                a.anchor_id
                for a in test_anchors
                if a.content_type == AnchorContentType.HDR
                and "Objectives" in a.section_path
            ),
            None,
        )
        assert objectives_heading_id is not None
        assert objectives_heading_id in objectives_anchor_ids

        # Проверяем, что параграфы из блока Objectives включены
        objectives_para_ids = [
            a.anchor_id
            for a in test_anchors
            if a.content_type == AnchorContentType.P
            and "Objectives" in a.section_path
        ]
        assert all(para_id in objectives_anchor_ids for para_id in objectives_para_ids)

    @pytest.mark.asyncio
    async def test_map_sections_soa_contains_cell_anchors(
        self,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contracts: list[SectionContract],
        test_anchors: list[Anchor],
    ):
        """Тест, что SoA маппинг содержит cell anchors."""
        service = SectionMappingService(db)
        await service.map_sections(test_version.id, force=False)

        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == test_version.id,
            SectionMap.section_key == "protocol.soa",
        )
        result = await db.execute(stmt)
        soa_map = result.scalar_one_or_none()

        assert soa_map is not None
        assert soa_map.anchor_ids is not None

        # Проверяем, что в маппинге есть cell anchors
        soa_anchor_ids = set(soa_map.anchor_ids)
        cell_anchors = [
            a.anchor_id
            for a in test_anchors
            if a.content_type == AnchorContentType.CELL
        ]
        assert any(cell_id in soa_anchor_ids for cell_id in cell_anchors)

    @pytest.mark.asyncio
    async def test_map_sections_confidence_based_status(
        self,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contracts: list[SectionContract],
        test_anchors: list[Anchor],
    ):
        """Тест, что статус маппинга зависит от confidence."""
        service = SectionMappingService(db)
        await service.map_sections(test_version.id, force=False)

        stmt = select(SectionMap).where(SectionMap.doc_version_id == test_version.id)
        result = await db.execute(stmt)
        section_maps = result.scalars().all()

        for section_map in section_maps:
            if section_map.confidence >= 0.7:
                assert section_map.status == SectionMapStatus.MAPPED
            else:
                assert section_map.status == SectionMapStatus.NEEDS_REVIEW

    @pytest.mark.asyncio
    async def test_map_sections_no_heading_match_creates_needs_review(
        self,
        db: AsyncSession,
        test_workspace: Workspace,
        test_version: DocumentVersion,
        test_anchors: list[Anchor],
    ):
        """Тест, что если нет совпадения заголовка, создаётся needs_review маппинг."""
        # Создаём контракт, для которого нет заголовка в документе
        contract = SectionContract(
            workspace_id=test_workspace.id,
            doc_type=DocumentType.PROTOCOL,
            section_key="protocol.treatments.dosing",
            title="Treatments and Dosing",
            required_facts_json={},
            allowed_sources_json={},
            retrieval_recipe_json={
                "version": 1,
                "heading_match": {
                    "must": ["treatment", "dosing"],
                    "should": [],
                    "not": [],
                },
                "regex": {
                    "heading": ["^(\\d+\\.)?\\s*(Treatment|Dosing)\\b"],
                },
                "capture": {
                    "strategy": "heading_block",
                },
            },
            qc_ruleset_json={},
            citation_policy=CitationPolicy.PER_SENTENCE,
            version=1,
            is_active=True,
        )
        db.add(contract)
        await db.commit()
        await db.refresh(contract)

        service = SectionMappingService(db)
        summary = await service.map_sections(test_version.id, force=False)

        # Проверяем, что создан needs_review маппинг
        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == test_version.id,
            SectionMap.section_key == "protocol.treatments.dosing",
        )
        result = await db.execute(stmt)
        dosing_map = result.scalar_one_or_none()

        assert dosing_map is not None
        assert dosing_map.status == SectionMapStatus.NEEDS_REVIEW
        assert "No heading match" in (dosing_map.notes or "")
        assert summary.sections_needs_review_count > 0

    @pytest.mark.asyncio
    async def test_map_sections_force_rebuilds_system_mappings(
        self,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contracts: list[SectionContract],
        test_anchors: list[Anchor],
    ):
        """Тест, что force=True пересоздаёт system маппинги."""
        service = SectionMappingService(db)

        # Первый маппинг
        await service.map_sections(test_version.id, force=False)

        # Получаем первый маппинг
        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == test_version.id,
            SectionMap.section_key == "protocol.objectives",
        )
        result = await db.execute(stmt)
        first_map = result.scalar_one_or_none()
        assert first_map is not None
        first_confidence = first_map.confidence

        # Второй маппинг с force=True
        await service.map_sections(test_version.id, force=True)

        # Проверяем, что маппинг обновлён
        await db.refresh(first_map)
        assert first_map.mapped_by == SectionMapMappedBy.SYSTEM

    @pytest.mark.asyncio
    async def test_map_sections_preserves_overridden(
        self,
        db: AsyncSession,
        test_version: DocumentVersion,
        test_section_contracts: list[SectionContract],
        test_anchors: list[Anchor],
    ):
        """Тест, что overridden маппинги не перезаписываются."""
        service = SectionMappingService(db)

        # Создаём initial маппинг
        await service.map_sections(test_version.id, force=False)

        # Создаём overridden маппинг
        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == test_version.id,
            SectionMap.section_key == "protocol.objectives",
        )
        result = await db.execute(stmt)
        objectives_map = result.scalar_one_or_none()
        assert objectives_map is not None

        # Переопределяем маппинг
        objectives_map.status = SectionMapStatus.OVERRIDDEN
        objectives_map.mapped_by = SectionMapMappedBy.USER
        objectives_map.anchor_ids = ["custom_anchor_1", "custom_anchor_2"]
        await db.commit()

        # Запускаем маппинг с force=True
        await service.map_sections(test_version.id, force=True)

        # Проверяем, что overridden маппинг не изменён
        await db.refresh(objectives_map)
        assert objectives_map.status == SectionMapStatus.OVERRIDDEN
        assert objectives_map.mapped_by == SectionMapMappedBy.USER
        assert objectives_map.anchor_ids == ["custom_anchor_1", "custom_anchor_2"]

    @pytest.mark.asyncio
    async def test_mapping_ru_headings(
        self,
        db: AsyncSession,
        test_document: DocumentModel,
        test_workspace: Workspace,
    ):
        """Тест маппинга русских заголовков."""
        # Создаём версию с русским языком
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.docx",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.READY,
            document_language=DocumentLanguage.RU,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)

        # Создаём контракт с RU keywords (v2 формат)
        contract = SectionContract(
            workspace_id=test_workspace.id,
            doc_type=DocumentType.PROTOCOL,
            section_key="protocol.objectives",
            title="Цели",
            required_facts_json={},
            allowed_sources_json={},
            retrieval_recipe_json={
                "version": 2,
                "lang": {
                    "ru": {
                        "must": ["цели", "задачи исследования"],
                        "should": ["конечные точки"],
                        "not": [],
                    },
                    "en": {
                        "must": ["objectives"],
                        "should": [],
                        "not": [],
                    },
                },
                "regex": {
                    "heading": {
                        "ru": ["^(\\d+\\.)?\\s*(Цели|Задачи исследования)\\b"],
                        "en": ["^(\\d+\\.)?\\s*(Objectives)\\b"],
                    },
                },
                "capture": {
                    "strategy": "heading_block",
                    "stop_at_same_or_higher_level": True,
                },
            },
            qc_ruleset_json={},
            citation_policy=CitationPolicy.PER_SENTENCE,
            version=2,
            is_active=True,
        )
        db.add(contract)
        await db.commit()
        await db.refresh(contract)

        # Создаём anchors с русскими заголовками
        anchors = [
            Anchor(
                doc_version_id=version.id,
                anchor_id=f"{version.id}:Цели:hdr:1:hash1",
                section_path="Цели",
                content_type=AnchorContentType.HDR,
                ordinal=1,
                text_raw="Цели исследования",
                text_norm="Цели исследования",
                text_hash="hash1",
                location_json={"para_index": 1},
            ),
            Anchor(
                doc_version_id=version.id,
                anchor_id=f"{version.id}:Цели:p:1:hash2",
                section_path="Цели",
                content_type=AnchorContentType.P,
                ordinal=1,
                text_raw="Основная цель исследования...",
                text_norm="Основная цель исследования...",
                text_hash="hash2",
                location_json={"para_index": 2},
            ),
        ]
        for anchor in anchors:
            db.add(anchor)
        await db.commit()

        # Запускаем маппинг
        service = SectionMappingService(db)
        summary = await service.map_sections(version.id, force=False)

        # Проверяем результат
        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == version.id,
            SectionMap.section_key == "protocol.objectives",
        )
        result = await db.execute(stmt)
        section_map = result.scalar_one_or_none()

        assert section_map is not None
        assert section_map.status == SectionMapStatus.MAPPED
        assert section_map.confidence >= 0.7
        assert len(section_map.anchor_ids or []) > 0

    @pytest.mark.asyncio
    async def test_mapping_en_headings(
        self,
        db: AsyncSession,
        test_document: DocumentModel,
        test_workspace: Workspace,
    ):
        """Тест маппинга английских заголовков."""
        # Создаём версию с английским языком
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.docx",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.READY,
            document_language=DocumentLanguage.EN,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)

        # Создаём контракт с EN keywords (v2 формат)
        contract = SectionContract(
            workspace_id=test_workspace.id,
            doc_type=DocumentType.PROTOCOL,
            section_key="protocol.objectives",
            title="Objectives",
            required_facts_json={},
            allowed_sources_json={},
            retrieval_recipe_json={
                "version": 2,
                "lang": {
                    "ru": {
                        "must": ["цели"],
                        "should": [],
                        "not": [],
                    },
                    "en": {
                        "must": ["objectives", "study objectives"],
                        "should": ["endpoint"],
                        "not": [],
                    },
                },
                "regex": {
                    "heading": {
                        "ru": ["^(\\d+\\.)?\\s*(Цели)\\b"],
                        "en": ["^(\\d+\\.)?\\s*(Objectives|Study Objectives)\\b"],
                    },
                },
                "capture": {
                    "strategy": "heading_block",
                    "stop_at_same_or_higher_level": True,
                },
            },
            qc_ruleset_json={},
            citation_policy=CitationPolicy.PER_SENTENCE,
            version=2,
            is_active=True,
        )
        db.add(contract)
        await db.commit()
        await db.refresh(contract)

        # Создаём anchors с английскими заголовками
        anchors = [
            Anchor(
                doc_version_id=version.id,
                anchor_id=f"{version.id}:Objectives:hdr:1:hash1",
                section_path="Objectives",
                content_type=AnchorContentType.HDR,
                ordinal=1,
                text_raw="Study Objectives",
                text_norm="Study Objectives",
                text_hash="hash1",
                location_json={"para_index": 1},
            ),
            Anchor(
                doc_version_id=version.id,
                anchor_id=f"{version.id}:Objectives:p:1:hash2",
                section_path="Objectives",
                content_type=AnchorContentType.P,
                ordinal=1,
                text_raw="The primary objective...",
                text_norm="The primary objective...",
                text_hash="hash2",
                location_json={"para_index": 2},
            ),
        ]
        for anchor in anchors:
            db.add(anchor)
        await db.commit()

        # Запускаем маппинг
        service = SectionMappingService(db)
        summary = await service.map_sections(version.id, force=False)

        # Проверяем результат
        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == version.id,
            SectionMap.section_key == "protocol.objectives",
        )
        result = await db.execute(stmt)
        section_map = result.scalar_one_or_none()

        assert section_map is not None
        assert section_map.status == SectionMapStatus.MAPPED
        assert section_map.confidence >= 0.7

    @pytest.mark.asyncio
    async def test_mixed_language_raises_threshold(
        self,
        db: AsyncSession,
        test_document: DocumentModel,
        test_workspace: Workspace,
    ):
        """Тест, что mixed/unknown язык повышает threshold и ограничивает confidence."""
        # Создаём версию со mixed языком
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.docx",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.READY,
            document_language=DocumentLanguage.MIXED,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)

        # Создаём контракт с RU+EN keywords (v2 формат)
        contract = SectionContract(
            workspace_id=test_workspace.id,
            doc_type=DocumentType.PROTOCOL,
            section_key="protocol.objectives",
            title="Objectives",
            required_facts_json={},
            allowed_sources_json={},
            retrieval_recipe_json={
                "version": 2,
                "lang": {
                    "ru": {
                        "must": ["цели"],
                        "should": [],
                        "not": [],
                    },
                    "en": {
                        "must": ["objectives"],
                        "should": [],
                        "not": [],
                    },
                },
                "regex": {
                    "heading": {
                        "ru": ["^(\\d+\\.)?\\s*(Цели)\\b"],
                        "en": ["^(\\d+\\.)?\\s*(Objectives)\\b"],
                    },
                },
                "capture": {
                    "strategy": "heading_block",
                    "stop_at_same_or_higher_level": True,
                },
            },
            qc_ruleset_json={},
            citation_policy=CitationPolicy.PER_SENTENCE,
            version=2,
            is_active=True,
        )
        db.add(contract)
        await db.commit()
        await db.refresh(contract)

        # Создаём anchors с русским заголовком (но язык mixed)
        anchors = [
            Anchor(
                doc_version_id=version.id,
                anchor_id=f"{version.id}:Цели:hdr:1:hash1",
                section_path="Цели",
                content_type=AnchorContentType.HDR,
                ordinal=1,
                text_raw="Цели исследования",
                text_norm="Цели исследования",
                text_hash="hash1",
                location_json={"para_index": 1},
            ),
            Anchor(
                doc_version_id=version.id,
                anchor_id=f"{version.id}:Цели:p:1:hash2",
                section_path="Цели",
                content_type=AnchorContentType.P,
                ordinal=1,
                text_raw="Основная цель...",
                text_norm="Основная цель...",
                text_hash="hash2",
                location_json={"para_index": 2},
            ),
        ]
        for anchor in anchors:
            db.add(anchor)
        await db.commit()

        # Запускаем маппинг
        service = SectionMappingService(db)
        summary = await service.map_sections(version.id, force=False)

        # Проверяем результат
        stmt = select(SectionMap).where(
            SectionMap.doc_version_id == version.id,
            SectionMap.section_key == "protocol.objectives",
        )
        result = await db.execute(stmt)
        section_map = result.scalar_one_or_none()

        assert section_map is not None
        # Для mixed должен быть confidence cap 0.8
        assert section_map.confidence <= 0.8

