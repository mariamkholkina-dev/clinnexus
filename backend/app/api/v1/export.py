"""API эндпоинты для экспорта документов."""

from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import logger
from app.core.audit import log_audit
from app.db.models.generation import GeneratedTargetSection, GenerationRun
from app.db.models.studies import DocumentVersion, Document
from app.services.export.docx_assembler import assemble_document

router = APIRouter()


@router.get(
    "/document-versions/{version_id}/download",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def download_document_version(
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """
    Скачивает собранный документ для версии документа.
    
    Собирает все сгенерированные секции, опубликованные в эту версию,
    в единый DOCX файл.
    
    Args:
        version_id: ID версии документа
        
    Returns:
        FileResponse с DOCX файлом
        
    Raises:
        NotFoundError: Если версия документа не найдена или нет опубликованных секций
        ValidationError: Если нет секций для сборки
    """
    # Проверяем существование версии документа
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))
    
    # Получаем документ для workspace_id (для audit logging)
    document = await db.get(Document, version.document_id)
    if not document:
        raise NotFoundError("Document", str(version.document_id))
    
    # Получаем все опубликованные секции для этой версии
    # Используем join для получения связанных GenerationRun
    stmt = (
        select(GeneratedTargetSection, GenerationRun)
        .join(
            GenerationRun,
            GeneratedTargetSection.generation_run_id == GenerationRun.id
        )
        .where(
            GeneratedTargetSection.published_to_document_version_id == version_id
        )
        .order_by(GeneratedTargetSection.created_at)
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    # Получаем секции и их GenerationRun
    sections_with_runs: list[tuple[GeneratedTargetSection, GenerationRun]] = [
        (section, generation_run)
        for section, generation_run in rows
    ]
    
    if not sections_with_runs:
        raise ValidationError(
            f"Нет опубликованных секций для версии документа {version_id}. "
            "Сначала опубликуйте секции через генерацию."
        )
    
    # Создаём временный файл для сборки документа
    temp_dir = Path(tempfile.gettempdir())
    temp_file = temp_dir / f"document_version_{version_id}.docx"
    
    # Подготавливаем данные для assemble_document
    sections_only = [section for section, _ in sections_with_runs]
    generation_runs_dict = {
        section.id: gen_run
        for section, gen_run in sections_with_runs
    }
    
    try:
        # Собираем документ
        assemble_document(
            sections_only,
            temp_file,
            generation_runs=generation_runs_dict,
        )
        
        # Audit logging
        await log_audit(
            db=db,
            workspace_id=document.workspace_id,
            action="download",
            entity_type="document_version",
            entity_id=str(version_id),
            after_json={
                "sections_count": len(sections_only),
                "output_file": str(temp_file),
            },
        )
        await db.commit()
        
        # Формируем имя файла для скачивания
        # Используем название документа и версию, если доступны
        download_filename = f"document_{version.version_label or version_id}.docx"
        
        logger.info(
            f"Документ собран и готов к скачиванию: "
            f"version_id={version_id}, sections={len(sections_only)}, "
            f"filename={download_filename}"
        )
        
        # Возвращаем файл
        return FileResponse(
            path=str(temp_file),
            filename=download_filename,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            # ВАЖНО: удаляем файл после отправки
            background=None,  # FastAPI автоматически удалит временный файл
        )
        
    except Exception as e:
        logger.error(f"Ошибка при сборке документа для версии {version_id}: {e}")
        # Удаляем временный файл в случае ошибки
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:  # noqa: BLE001
                pass
        raise

