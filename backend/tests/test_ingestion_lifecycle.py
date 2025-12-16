"""
Unit-тесты для жизненного цикла ингестии документов (ingestion lifecycle).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.documents import start_ingestion
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.enums import DocumentLifecycleStatus, DocumentType, IngestionStatus, StudyStatus
from app.db.models.auth import Workspace
from app.db.models.studies import Document, DocumentVersion, Study


class TestIngestionLifecycle:
    """Тесты для жизненного цикла ингестии."""

    @pytest.fixture
    async def test_workspace(self, db: AsyncSession) -> Workspace:
        """Создает тестовый workspace."""
        workspace = Workspace(
            name="Test Workspace",
        )
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
    async def test_document(self, db: AsyncSession, test_study: Study) -> Document:
        """Создает тестовый документ."""
        document = Document(
            workspace_id=test_study.workspace_id,
            study_id=test_study.id,
            doc_type=DocumentType.PROTOCOL,
            title="Test Document",
            lifecycle_status=DocumentLifecycleStatus.DRAFT,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        return document

    @pytest.fixture
    async def test_version_uploaded(
        self, db: AsyncSession, test_document: Document
    ) -> DocumentVersion:
        """Создает версию документа со статусом UPLOADED и файлом."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.pdf",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.UPLOADED,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.fixture
    async def test_version_no_file(
        self, db: AsyncSession, test_document: Document
    ) -> DocumentVersion:
        """Создает версию документа без файла."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri=None,
            source_sha256=None,
            effective_date=date.today(),
            ingestion_status=IngestionStatus.UPLOADED,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.fixture
    async def test_version_processing(
        self, db: AsyncSession, test_document: Document
    ) -> DocumentVersion:
        """Создает версию документа со статусом PROCESSING."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.pdf",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.PROCESSING,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.fixture
    async def test_version_failed(
        self, db: AsyncSession, test_document: Document
    ) -> DocumentVersion:
        """Создает версию документа со статусом FAILED."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.pdf",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.FAILED,
            ingestion_summary_json={"error": "Test error"},
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.fixture
    async def test_version_needs_review(
        self, db: AsyncSession, test_document: Document
    ) -> DocumentVersion:
        """Создает версию документа со статусом NEEDS_REVIEW."""
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri="file:///test/file.pdf",
            source_sha256="abc123",
            effective_date=date.today(),
            ingestion_status=IngestionStatus.NEEDS_REVIEW,
            ingestion_summary_json={"warnings": ["test warning"]},
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

    @pytest.mark.asyncio
    async def test_ingest_from_uploaded_success(
        self, db: AsyncSession, test_version_uploaded: DocumentVersion
    ):
        """Тест успешной ингестии из статуса UPLOADED."""
        result = await start_ingestion(test_version_uploaded.id, force=False, db=db)
        
        # Проверяем результат
        assert result["status"] in (IngestionStatus.READY.value, IngestionStatus.NEEDS_REVIEW.value)
        assert "version_id" in result
        assert "anchors_created" in result
        assert "chunks_created" in result
        
        # Проверяем, что статус обновлен в БД
        await db.refresh(test_version_uploaded)
        assert test_version_uploaded.ingestion_status in (
            IngestionStatus.READY,
            IngestionStatus.NEEDS_REVIEW,
        )
        assert test_version_uploaded.ingestion_summary_json is not None
        assert "anchors_created" in test_version_uploaded.ingestion_summary_json

    @pytest.mark.asyncio
    async def test_ingest_without_file_raises_error(
        self, db: AsyncSession, test_version_no_file: DocumentVersion
    ):
        """Тест, что ингестия без файла вызывает ошибку."""
        with pytest.raises(ValidationError) as exc_info:
            await start_ingestion(test_version_no_file.id, force=False, db=db)
        
        assert "файла" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_ingest_processing_raises_conflict(
        self, db: AsyncSession, test_version_processing: DocumentVersion
    ):
        """Тест, что ингестия при статусе PROCESSING вызывает ConflictError."""
        with pytest.raises(ConflictError) as exc_info:
            await start_ingestion(test_version_processing.id, force=False, db=db)
        
        assert "processing" in exc_info.value.message.lower() or "выполняется" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_ingest_failed_without_force_raises_conflict(
        self, db: AsyncSession, test_version_failed: DocumentVersion
    ):
        """Тест, что ингестия при статусе FAILED без force вызывает ConflictError."""
        with pytest.raises(ConflictError) as exc_info:
            await start_ingestion(test_version_failed.id, force=False, db=db)
        
        assert "force" in exc_info.value.message.lower() or "перезапуск" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_ingest_failed_with_force_succeeds(
        self, db: AsyncSession, test_version_failed: DocumentVersion
    ):
        """Тест, что ингестия при статусе FAILED с force=true успешна."""
        result = await start_ingestion(test_version_failed.id, force=True, db=db)
        
        # Проверяем результат
        assert result["status"] in (IngestionStatus.READY.value, IngestionStatus.NEEDS_REVIEW.value)
        
        # Проверяем, что статус обновлен в БД
        await db.refresh(test_version_failed)
        assert test_version_failed.ingestion_status in (
            IngestionStatus.READY,
            IngestionStatus.NEEDS_REVIEW,
        )

    @pytest.mark.asyncio
    async def test_ingest_needs_review_without_force_raises_conflict(
        self, db: AsyncSession, test_version_needs_review: DocumentVersion
    ):
        """Тест, что ингестия при статусе NEEDS_REVIEW без force вызывает ConflictError."""
        with pytest.raises(ConflictError) as exc_info:
            await start_ingestion(test_version_needs_review.id, force=False, db=db)
        
        assert "force" in exc_info.value.message.lower() or "перезапуск" in exc_info.value.message.lower()

    @pytest.mark.asyncio
    async def test_ingest_needs_review_with_force_succeeds(
        self, db: AsyncSession, test_version_needs_review: DocumentVersion
    ):
        """Тест, что ингестия при статусе NEEDS_REVIEW с force=true успешна."""
        result = await start_ingestion(test_version_needs_review.id, force=True, db=db)
        
        # Проверяем результат
        assert result["status"] in (IngestionStatus.READY.value, IngestionStatus.NEEDS_REVIEW.value)
        
        # Проверяем, что статус обновлен в БД
        await db.refresh(test_version_needs_review)
        assert test_version_needs_review.ingestion_status in (
            IngestionStatus.READY,
            IngestionStatus.NEEDS_REVIEW,
        )

    @pytest.mark.asyncio
    async def test_ingest_nonexistent_version_raises_not_found(self, db: AsyncSession):
        """Тест, что ингестия несуществующей версии вызывает NotFoundError."""
        fake_id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            await start_ingestion(fake_id, force=False, db=db)

    @pytest.mark.asyncio
    async def test_status_transition_uploaded_to_processing_to_ready(
        self, db: AsyncSession, test_version_uploaded: DocumentVersion
    ):
        """Тест перехода статусов: uploaded -> processing -> ready/needs_review."""
        # Проверяем начальный статус
        assert test_version_uploaded.ingestion_status == IngestionStatus.UPLOADED
        
        # Запускаем ингестию
        result = await start_ingestion(test_version_uploaded.id, force=False, db=db)
        
        # Проверяем финальный статус
        await db.refresh(test_version_uploaded)
        assert test_version_uploaded.ingestion_status in (
            IngestionStatus.READY,
            IngestionStatus.NEEDS_REVIEW,
        )
        
        # Проверяем, что ingestion_summary_json заполнен
        assert test_version_uploaded.ingestion_summary_json is not None
        assert "anchors_created" in test_version_uploaded.ingestion_summary_json
        assert "chunks_created" in test_version_uploaded.ingestion_summary_json
        assert "soa_detected" in test_version_uploaded.ingestion_summary_json
        assert "warnings" in test_version_uploaded.ingestion_summary_json
        assert "needs_review" in test_version_uploaded.ingestion_summary_json

