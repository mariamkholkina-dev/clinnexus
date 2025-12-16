"""
Тесты для DOCX ingestion (Шаг 4).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

import pytest
from docx import Document as DocxDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import AnchorContentType, DocumentLifecycleStatus, DocumentType, IngestionStatus, StudyStatus
from app.db.models.anchors import Anchor
from app.db.models.auth import Workspace
from app.db.models.studies import Document, DocumentVersion, Study
from app.services.ingestion import IngestionService


class TestDocxIngestion:
    """Тесты для DOCX ingestion."""
    
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
    def sample_docx_file(self) -> Path:
        """
        Создает тестовый DOCX файл с структурой:
        - Heading 1 "Introduction"
        - Paragraph "This is paragraph 1."
        - Heading 2 "Objectives"
        - List item 1
        - List item 2
        """
        # Создаем временный файл
        tmp_file = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp_path = Path(tmp_file.name)
        tmp_file.close()
        
        # Создаем документ через python-docx
        doc = DocxDocument()
        
        # Heading 1
        heading1 = doc.add_paragraph("Introduction", style="Heading 1")
        
        # Paragraph
        para1 = doc.add_paragraph("This is paragraph 1.")
        
        # Heading 2
        heading2 = doc.add_paragraph("Objectives", style="Heading 2")
        
        # List items
        list_item1 = doc.add_paragraph("First objective", style="List Bullet")
        list_item2 = doc.add_paragraph("Second objective", style="List Bullet")
        
        # Сохраняем документ
        doc.save(str(tmp_path))
        
        return tmp_path
    
    @pytest.fixture
    async def test_version_with_docx(
        self, db: AsyncSession, test_document: Document, sample_docx_file: Path
    ) -> DocumentVersion:
        """Создает версию документа с DOCX файлом."""
        # Используем абсолютный путь как file:// URI
        # На Windows: file:///C:/path/to/file
        # На Unix: file:///path/to/file
        abs_path = sample_docx_file.resolve()
        if abs_path.as_posix().startswith("/"):
            # Unix-подобная система
            file_uri = f"file://{abs_path.as_posix()}"
        else:
            # Windows: C:/path/to/file -> file:///C:/path/to/file
            file_uri = f"file:///{abs_path.as_posix()}"
        
        version = DocumentVersion(
            document_id=test_document.id,
            version_label="v1.0",
            source_file_uri=file_uri,
            source_sha256="test_hash",
            ingestion_status=IngestionStatus.UPLOADED,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_creates_anchors(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест, что DOCX ingestion создаёт anchors."""
        service = IngestionService(db)
        result = await service.ingest(test_version_with_docx.id)
        
        # Проверяем результат
        assert result.anchors_created > 0
        assert result.doc_version_id == test_version_with_docx.id
        
        # Проверяем, что anchors созданы в БД
        stmt = select(Anchor).where(Anchor.doc_version_id == test_version_with_docx.id)
        anchors_result = await db.execute(stmt)
        anchors = anchors_result.scalars().all()
        
        assert len(anchors) == result.anchors_created
        assert len(anchors) > 0
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_content_types(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест, что создаются anchors разных типов (hdr, p, li)."""
        service = IngestionService(db)
        await service.ingest(test_version_with_docx.id)
        await db.commit()
        
        # Проверяем наличие разных типов
        stmt = select(Anchor).where(
            Anchor.doc_version_id == test_version_with_docx.id
        )
        anchors_result = await db.execute(stmt)
        anchors_by_type = {}
        for anchor in anchors_result.scalars().all():
            content_type = anchor.content_type
            anchors_by_type[content_type] = anchors_by_type.get(content_type, 0) + 1
        
        # Должны быть заголовки (hdr)
        assert AnchorContentType.HDR in anchors_by_type
        assert anchors_by_type[AnchorContentType.HDR] >= 2  # Introduction и Objectives
        
        # Должен быть параграф (p)
        assert AnchorContentType.P in anchors_by_type
        assert anchors_by_type[AnchorContentType.P] >= 1  # "This is paragraph 1."
        
        # Должны быть элементы списка (li)
        assert AnchorContentType.LI in anchors_by_type
        assert anchors_by_type[AnchorContentType.LI] >= 2  # Два элемента списка
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_section_path(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест, что section_path корректно отражает структуру документа."""
        service = IngestionService(db)
        await service.ingest(test_version_with_docx.id)
        await db.commit()
        
        # Получаем все anchors
        stmt = select(Anchor).where(Anchor.doc_version_id == test_version_with_docx.id)
        anchors_result = await db.execute(stmt)
        anchors = anchors_result.scalars().all()
        
        # Собираем уникальные section_path
        section_paths = set(anchor.section_path for anchor in anchors)
        
        # Должны быть:
        # - "Introduction" (или "ROOT" если нет заголовков выше)
        # - "Introduction/Objectives" (заголовок второго уровня)
        
        # Проверяем наличие "Introduction" в путях
        intro_paths = [p for p in section_paths if "Introduction" in p]
        assert len(intro_paths) > 0, f"Не найдено 'Introduction' в путях: {section_paths}"
        
        # Проверяем наличие "Introduction/Objectives" (если заголовки правильно обработаны)
        objectives_paths = [p for p in section_paths if "Objectives" in p]
        assert len(objectives_paths) > 0, f"Не найдено 'Objectives' в путях: {section_paths}"
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_anchor_id_format(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест формата anchor_id."""
        service = IngestionService(db)
        await service.ingest(test_version_with_docx.id)
        await db.commit()
        
        # Получаем anchors
        stmt = select(Anchor).where(Anchor.doc_version_id == test_version_with_docx.id)
        anchors_result = await db.execute(stmt)
        anchors = anchors_result.scalars().all()
        
        doc_version_id_str = str(test_version_with_docx.id)
        
        for anchor in anchors:
            # Формат: {doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash}
            parts = anchor.anchor_id.split(":")
            assert len(parts) == 5, f"Неверный формат anchor_id: {anchor.anchor_id}"
            
            anchor_doc_version_id, section_path, content_type_str, ordinal_str, text_hash = parts
            
            # Проверяем doc_version_id
            assert anchor_doc_version_id == doc_version_id_str
            
            # Проверяем content_type
            assert content_type_str in ["p", "li", "hdr"]
            assert anchor.content_type.value == content_type_str
            
            # Проверяем ordinal (должен быть числом)
            assert ordinal_str.isdigit()
            assert int(ordinal_str) == anchor.ordinal
            
            # Проверяем hash (64 символа hex)
            assert len(text_hash) == 64
            assert all(c in "0123456789abcdef" for c in text_hash)
            
            # Проверяем section_path
            assert anchor.section_path == section_path
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_summary_counts(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест, что ingestion_summary_json содержит counts."""
        # Запускаем через endpoint для получения полного summary
        from app.api.v1.documents import start_ingestion
        
        result = await start_ingestion(test_version_with_docx.id, force=False, db=db)
        
        # Обновляем версию из БД
        await db.refresh(test_version_with_docx)
        
        summary = test_version_with_docx.ingestion_summary_json
        assert summary is not None
        
        # Проверяем наличие counts_by_type
        assert "counts_by_type" in summary
        counts_by_type = summary["counts_by_type"]
        assert isinstance(counts_by_type, dict)
        assert "hdr" in counts_by_type
        assert "p" in counts_by_type
        assert "li" in counts_by_type
        
        # Проверяем num_sections
        assert "num_sections" in summary
        assert summary["num_sections"] > 0
        
        # Проверяем sections
        assert "sections" in summary
        assert isinstance(summary["sections"], list)
        assert len(summary["sections"]) == summary["num_sections"]
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_re_ingest(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест, что re-ingest пересоздаёт anchors без ошибок."""
        service = IngestionService(db)
        
        # Первый ingestion
        result1 = await service.ingest(test_version_with_docx.id)
        await db.commit()
        
        anchors_count_1 = result1.anchors_created
        assert anchors_count_1 > 0
        
        # Второй ingestion (re-ingest)
        result2 = await service.ingest(test_version_with_docx.id)
        await db.commit()
        
        # Проверяем, что количество anchors не изменилось (пересозданы)
        assert result2.anchors_created == anchors_count_1
        
        # Проверяем, что в БД правильное количество
        stmt = select(Anchor).where(Anchor.doc_version_id == test_version_with_docx.id)
        anchors_result = await db.execute(stmt)
        anchors = anchors_result.scalars().all()
        assert len(anchors) == anchors_count_1
    
    @pytest.mark.asyncio
    async def test_docx_ingestion_location_json(
        self, db: AsyncSession, test_version_with_docx: DocumentVersion
    ):
        """Тест, что location_json содержит необходимые поля."""
        service = IngestionService(db)
        await service.ingest(test_version_with_docx.id)
        await db.commit()
        
        # Получаем anchors
        stmt = select(Anchor).where(Anchor.doc_version_id == test_version_with_docx.id)
        anchors_result = await db.execute(stmt)
        anchors = anchors_result.scalars().all()
        
        for anchor in anchors:
            location = anchor.location_json
            assert isinstance(location, dict)
            assert "para_index" in location
            assert "style" in location
            assert "section_path" in location
            assert isinstance(location["para_index"], int)
            assert location["para_index"] > 0
            assert location["section_path"] == anchor.section_path

