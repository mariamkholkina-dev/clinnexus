"""Скрипт для массовой ингестии документов (кампания)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import platform
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.core.logging import logger
from app.core.storage import StoredFile, sanitize_filename
from app.db.enums import (
    AnchorContentType,
    DocumentLanguage,
    DocumentLifecycleStatus,
    DocumentType,
    IngestionStatus,
    StudyStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.ingestion_runs import IngestionRun
from app.db.models.studies import Document, DocumentVersion, Study
from app.db.models.topics import HeadingBlockTopicAssignment
from app.services.ingestion import IngestionService

# Разрешенные расширения файлов
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}


def find_document_files(path: Path) -> list[Path]:
    """
    Находит все документы в указанном пути (файл или директория с подкаталогами).
    
    Args:
        path: Путь к файлу или директории
        
    Returns:
        Список путей к найденным файлам
    """
    if not path.exists():
        raise FileNotFoundError(f"Путь не найден: {path}")
    
    files = []
    
    if path.is_file():
        # Один файл
        ext = path.suffix.lower()
        if ext in ALLOWED_EXTENSIONS:
            files.append(path)
        else:
            logger.warning(f"Пропущен файл с неподдерживаемым расширением: {path}")
    elif path.is_dir():
        # Директория - ищем рекурсивно
        for ext in ALLOWED_EXTENSIONS:
            files.extend(path.rglob(f"*{ext}"))
    
    return sorted(files)


async def save_file_from_path(file_path: Path, doc_version_id: UUID) -> StoredFile:
    """
    Сохраняет файл с диска в хранилище и вычисляет SHA256.
    
    Args:
        file_path: Путь к файлу на диске
        doc_version_id: UUID версии документа
        
    Returns:
        StoredFile с информацией о сохраненном файле
    """
    original_filename = file_path.name
    safe_filename = sanitize_filename(original_filename)
    
    # Создаём директорию для версии
    base_path = Path(settings.storage_base_path)
    version_dir = base_path / str(doc_version_id)
    version_dir.mkdir(parents=True, exist_ok=True)
    
    # Полный путь к файлу
    dest_file_path = version_dir / safe_filename
    
    # Инициализируем SHA256 хешер
    sha256_hash = hashlib.sha256()
    size_bytes = 0
    
    # Копируем файл и вычисляем SHA256 по стриму
    chunk_size = 8192  # 8KB chunks
    with open(file_path, "rb") as src, open(dest_file_path, "wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            sha256_hash.update(chunk)
            size_bytes += len(chunk)
    
    # Получаем hexdigest
    sha256 = sha256_hash.hexdigest()
    
    # Формируем URI
    if dest_file_path.is_absolute():
        uri = f"file:///{dest_file_path.as_posix()}"
    else:
        uri = str(dest_file_path)
    
    return StoredFile(
        uri=uri,
        sha256=sha256,
        size_bytes=size_bytes,
        original_filename=original_filename,
    )


async def get_or_create_study(
    db: AsyncSession,
    workspace_id: UUID,
    study_code: str | None = None,
    study_title: str | None = None,
    unique_suffix: str | None = None,
) -> Study:
    """
    Получает или создает Study.
    
    Args:
        db: Сессия БД
        workspace_id: ID рабочего пространства
        study_code: Код исследования (если не указан, создается новый)
        study_title: Название исследования
        unique_suffix: Уникальный суффикс для study_code (для генерации уникального кода)
        
    Returns:
        Study объект
    """
    # Если передан study_code, пытаемся найти существующее исследование
    if study_code:
        result = await db.execute(
            select(Study).where(
                Study.workspace_id == workspace_id,
                Study.study_code == study_code,
            )
        )
        study = result.scalar_one_or_none()
        if study:
            return study
    
    # Генерируем код исследования
    base_code = None
    if not study_code:
        base_code = f"CAMPAIGN-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if unique_suffix:
            study_code = f"{base_code}-{unique_suffix}"
        else:
            # Добавляем UUID для уникальности
            study_code = f"{base_code}-{uuid4().hex[:8]}"
    
    if not study_title:
        study_title = f"Campaign Upload {datetime.now().strftime('%Y-%m-%d')}"
    
    # Пытаемся создать исследование
    study = Study(
        workspace_id=workspace_id,
        study_code=study_code,
        title=study_title,
        status=StudyStatus.ACTIVE,
    )
    db.add(study)
    
    try:
        await db.commit()
        await db.refresh(study)
        return study
    except IntegrityError as e:
        await db.rollback()
        # Если ошибка уникальности, пытаемся найти существующее исследование
        if "unique" in str(e.orig).lower() or "duplicate" in str(e.orig).lower():
            result = await db.execute(
                select(Study).where(
                    Study.workspace_id == workspace_id,
                    Study.study_code == study_code,
                )
            )
            existing_study = result.scalar_one_or_none()
            if existing_study:
                return existing_study
            # Если не нашли, генерируем новый код с UUID и пытаемся снова
            if base_code:
                study_code = f"{base_code}-{uuid4().hex[:8]}"
            else:
                # Если base_code не был определен, генерируем полностью новый код
                study_code = f"CAMPAIGN-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
            study.study_code = study_code
            db.add(study)
            await db.commit()
            await db.refresh(study)
            return study
        # Другие ошибки пробрасываем дальше
        raise


async def upload_file_to_campaign(
    db: AsyncSession,
    file_path: Path,
    workspace_id: UUID,
    doc_type: DocumentType,
    study_id: UUID | None = None,
    study_code: str | None = None,
) -> DocumentVersion:
    """
    Загружает файл: создает Study/Document/DocumentVersion и сохраняет файл.
    
    Args:
        db: Сессия БД
        file_path: Путь к файлу для загрузки
        workspace_id: ID рабочего пространства
        doc_type: Тип документа
        study_id: ID исследования (если None, создается новое)
        study_code: Код исследования (используется при создании нового)
        
    Returns:
        DocumentVersion объект
    """
    # Проверяем расширение файла
    ext = file_path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый тип файла: {ext}. Разрешенные: {', '.join(ALLOWED_EXTENSIONS)}")
    
    # Получаем или создаем Study
    if study_id:
        study = await db.get(Study, study_id)
        if not study:
            raise ValueError(f"Study с id {study_id} не найден")
    else:
        # Генерируем уникальный суффикс на основе имени файла для уникальности
        file_suffix = uuid4().hex[:8]
        study = await get_or_create_study(db, workspace_id, study_code, unique_suffix=file_suffix)
        study_id = study.id
    
    # Создаем Document
    doc_title = file_path.stem
    document = Document(
        workspace_id=workspace_id,
        study_id=study_id,
        doc_type=doc_type,
        title=doc_title,
        lifecycle_status=DocumentLifecycleStatus.DRAFT,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    
    # Создаем DocumentVersion
    version = DocumentVersion(
        document_id=document.id,
        version_label="v1.0",
        ingestion_status=IngestionStatus.UPLOADED,
        document_language=DocumentLanguage.UNKNOWN,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    
    # Сохраняем файл
    stored_file = await save_file_from_path(file_path, version.id)
    
    # Обновляем версию
    version.source_file_uri = stored_file.uri
    version.source_sha256 = stored_file.sha256
    version.ingestion_summary_json = {
        "filename": stored_file.original_filename,
        "size_bytes": stored_file.size_bytes,
    }
    await db.commit()
    await db.refresh(version)
    
    logger.info(f"Файл загружен: {file_path.name} -> version_id={version.id}")
    return version


async def run_campaign(
    db: AsyncSession,
    workspace_id: UUID | None = None,
    doc_type: str | None = None,
    limit: int | None = None,
    since: str | None = None,
    dry_run: bool = False,
    concurrency: int = 1,
    output_dir: Path | None = None,
) -> None:
    """
    Запускает кампанию ингестии для множества документов.
    
    Args:
        db: Сессия базы данных
        workspace_id: ID workspace для фильтрации (опционально)
        doc_type: Тип документа для фильтрации (protocol, sap, any)
        limit: Максимальное количество документов для обработки
        since: Дата начала фильтрации (YYYY-MM-DD)
        dry_run: Режим проверки без реальной ингестии
        concurrency: Количество параллельных задач (по умолчанию 1)
        output_dir: Директория для сохранения отчетов
    """
    logger.info("Начало кампании ингестии")
    
    # Формируем запрос для поиска doc_versions
    query = select(DocumentVersion).join(Document)
    
    # Фильтры
    if workspace_id:
        query = query.where(Document.workspace_id == workspace_id)
    
    if doc_type and doc_type != "any":
        try:
            doc_type_enum = DocumentType(doc_type)
            query = query.where(Document.doc_type == doc_type_enum)
        except ValueError:
            logger.warning(f"Неизвестный doc_type: {doc_type}, игнорируем фильтр")
    
    if since:
        try:
            since_date = datetime.fromisoformat(since).date()
            query = query.where(DocumentVersion.created_at >= since_date)
        except ValueError:
            logger.warning(f"Неверный формат даты since: {since}, игнорируем фильтр")
    
    # Ограничение
    if limit:
        query = query.limit(limit)
    
    # Выполняем запрос
    result = await db.execute(query)
    doc_versions = result.scalars().all()
    
    logger.info(f"Найдено {len(doc_versions)} документов для обработки")
    
    if dry_run:
        logger.info("DRY RUN: документы не будут обработаны")
        for dv in doc_versions[:10]:  # Показываем первые 10
            doc = await db.get(Document, dv.document_id)
            logger.info(f"  - {dv.id} ({doc.doc_type.value if doc else 'unknown'})")
        return
    
    # Результаты кампании
    campaign_results: list[dict[str, Any]] = []
    total_docs = len(doc_versions)
    ok_count = 0
    failed_count = 0
    needs_review_count = 0
    
    # Агрегированные метрики
    soa_found_count = 0
    unknown_rates: list[float] = []
    mapping_coverages: list[float] = []
    topics_rates: list[float] = []
    warning_counts: defaultdict[str, int] = defaultdict(int)
    error_counts: defaultdict[str, int] = defaultdict(int)
    section_failures: defaultdict[str, int] = defaultdict(int)
    
    # Обрабатываем документы
    ingestion_service = IngestionService(db)
    
    async def process_doc_version(dv: DocumentVersion) -> dict[str, Any]:
        """Обрабатывает один документ."""
        nonlocal soa_found_count, unknown_rates, mapping_coverages, topics_rates, warning_counts, error_counts, section_failures
        
        # Сохраняем id до возможной ошибки, чтобы использовать в except блоке
        dv_id = str(dv.id)
        
        doc = await db.get(Document, dv.document_id)
        doc_type_str = doc.doc_type.value if doc else "unknown"
        
        try:
            logger.info(f"Обработка doc_version {dv_id} ({doc_type_str})")
            # Используем UUID из строки, чтобы избежать проблем с сессией после rollback
            dv_uuid = UUID(dv_id)
            result = await ingestion_service.ingest(dv_uuid, force=True)
            await db.commit()
            
            # Получаем последний ingestion_run
            run_query = select(IngestionRun).where(
                IngestionRun.doc_version_id == dv_uuid
            ).order_by(IngestionRun.started_at.desc()).limit(1)
            run_result = await db.execute(run_query)
            ingestion_run = run_result.scalar_one_or_none()
            
            # Извлекаем метрики
            summary = ingestion_run.summary_json if ingestion_run else {}
            quality = ingestion_run.quality_json if ingestion_run else {}
            
            anchors_metrics = summary.get("anchors", {})
            soa_metrics = summary.get("soa", {})
            section_maps_metrics = summary.get("section_maps", {})
            facts_metrics = summary.get("facts", {})
            
            unknown_rate = anchors_metrics.get("unknown_rate", 0.0)
            mapping_coverage = section_maps_metrics.get("coverage_rate", 0.0)
            soa_found = soa_metrics.get("found", False)
            needs_review = quality.get("needs_review", False)
            
            # Рассчитываем topics_rate: отношение блоков с назначенным топиком 
            # к общему количеству блоков (HeadingBlocks) в версии документа
            topics_rate = None
            try:
                # Общее количество HeadingBlocks в версии документа
                # = количество anchors с content_type='hdr' (каждый HDR anchor создает один heading block)
                total_blocks_stmt = select(func.count(Anchor.id)).where(
                    Anchor.doc_version_id == dv_uuid,
                    Anchor.content_type == AnchorContentType.HDR
                )
                total_blocks_result = await db.execute(total_blocks_stmt)
                total_blocks = total_blocks_result.scalar() or 0
                
                if total_blocks > 0:
                    # Количество уникальных HeadingBlocks с назначенным топиком
                    # (считаем уникальные heading_block_id из heading_block_topic_assignments)
                    assigned_blocks_stmt = select(func.count(func.distinct(HeadingBlockTopicAssignment.heading_block_id))).where(
                        HeadingBlockTopicAssignment.doc_version_id == dv_uuid
                    )
                    assigned_blocks_result = await db.execute(assigned_blocks_stmt)
                    assigned_blocks = assigned_blocks_result.scalar() or 0
                    
                    topics_rate = assigned_blocks / total_blocks
                else:
                    topics_rate = 0.0
            except Exception as e:
                logger.warning(f"Ошибка при расчете topics_rate для {dv_id}: {e}")
                topics_rate = None
            
            # Собираем статистику
            if soa_found:
                soa_found_count += 1
            unknown_rates.append(unknown_rate)
            mapping_coverages.append(mapping_coverage)
            if topics_rate is not None:
                topics_rates.append(topics_rate)
            
            # Подсчитываем предупреждения и ошибки
            for warning in ingestion_run.warnings_json if ingestion_run else []:
                warning_counts[warning[:100]] += 1  # Ограничиваем длину
            for error in ingestion_run.errors_json if ingestion_run else []:
                error_counts[error[:100]] += 1
            
            # Подсчитываем неудачные секции
            per_section = section_maps_metrics.get("per_target_section", {})
            for section, info in per_section.items():
                if info.get("status") in ("missing", "needs_review"):
                    section_failures[section] += 1
            
            return {
                "doc_version_id": dv_id,
                "status": "ok",
                "needs_review": needs_review,
                "unknown_rate": unknown_rate,
                "soa_found": soa_found,
                "mapping_coverage": mapping_coverage,
                "topics_rate": topics_rate,
                "facts_total": facts_metrics.get("total", 0),
                "missing_required_count": len(facts_metrics.get("missing_required", [])),
                "conflicting_count": facts_metrics.get("conflicting_count", 0),
                "duration_ms": ingestion_run.duration_ms if ingestion_run else None,
            }
        except Exception as e:
            logger.error(f"Ошибка при обработке {dv_id}: {e}", exc_info=True)
            await db.rollback()
            return {
                "doc_version_id": dv_id,
                "status": "failed",
                "needs_review": False,
                "unknown_rate": None,
                "soa_found": False,
                "mapping_coverage": None,
                "topics_rate": None,
                "facts_total": 0,
                "missing_required_count": 0,
                "conflicting_count": 0,
                "duration_ms": None,
                "error": str(e)[:200],
            }
    
    # Обрабатываем документы (последовательно для безопасности)
    for i, dv in enumerate(doc_versions, 1):
        logger.info(f"Обработка {i}/{total_docs}")
        result = await process_doc_version(dv)
        campaign_results.append(result)
        
        if result["status"] == "ok":
            ok_count += 1
            if result.get("needs_review"):
                needs_review_count += 1
        else:
            failed_count += 1
    
    # Формируем сводку
    campaign_summary = {
        "campaign_started_at": datetime.now().isoformat(),
        "total_docs": total_docs,
        "ok": ok_count,
        "failed": failed_count,
        "needs_review": needs_review_count,
        "soa_found_rate": soa_found_count / ok_count if ok_count > 0 else 0.0,
        "avg_unknown_rate": sum(unknown_rates) / len(unknown_rates) if unknown_rates else 0.0,
        "unknown_rate_above_10pct": sum(1 for r in unknown_rates if r > 0.10),
        "unknown_rate_above_25pct": sum(1 for r in unknown_rates if r > 0.25),
        "avg_mapping_coverage": sum(mapping_coverages) / len(mapping_coverages) if mapping_coverages else 0.0,
        "mapping_coverage_below_75pct": sum(1 for c in mapping_coverages if c < 0.75),
        "avg_topics_rate": sum(topics_rates) / len(topics_rates) if topics_rates else 0.0,
        "topics_rate_below_50pct": sum(1 for r in topics_rates if r < 0.50),
        "top_warnings": dict(sorted(warning_counts.items(), key=lambda x: x[1], reverse=True)[:20]),
        "top_errors": dict(sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:20]),
        "section_failures": dict(section_failures),
    }
    
    # Сохраняем результаты
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Сохраняем сводку
        summary_path = output_dir / "campaign_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(campaign_summary, f, indent=2, ensure_ascii=False)
        logger.info(f"Сводка сохранена в {summary_path}")
        
        # Сохраняем детали
        details_path = output_dir / "campaign_details.csv"
        with open(details_path, "w", encoding="utf-8", newline="") as f:
            if campaign_results:
                writer = csv.DictWriter(f, fieldnames=campaign_results[0].keys())
                writer.writeheader()
                writer.writerows(campaign_results)
        logger.info(f"Детали сохранены в {details_path}")
    else:
        # Выводим сводку в консоль
        print("\n=== СВОДКА КАМПАНИИ ===")
        print(json.dumps(campaign_summary, indent=2, ensure_ascii=False))
    
    logger.info("Кампания завершена")


async def main() -> None:
    """Точка входа скрипта."""
    parser = argparse.ArgumentParser(
        description="Запуск кампании ингестии документов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Обработать существующие документы
  python -m app.scripts.run_ingestion_campaign --doc-type protocol --workspace-id <UUID>
  
  # Загрузить один файл и обработать
  python -m app.scripts.run_ingestion_campaign --upload-file file.docx --workspace-id <UUID> --doc-type protocol
  
  # Загрузить все файлы из директории и обработать
  python -m app.scripts.run_ingestion_campaign --upload-dir ./documents --workspace-id <UUID> --doc-type protocol
        """
    )
    parser.add_argument("--workspace-id", type=str, help="ID workspace (обязательно при загрузке файлов)")
    parser.add_argument("--doc-type", type=str, default="protocol", choices=["protocol", "sap", "any"], help="Тип документа")
    parser.add_argument("--limit", type=int, help="Максимальное количество документов")
    parser.add_argument("--since", type=str, help="Дата начала фильтрации (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Режим проверки без реальной ингестии")
    parser.add_argument("--concurrency", type=int, default=1, help="Количество параллельных задач (по умолчанию 1)")
    parser.add_argument("--output", type=str, help="Путь к директории для сохранения отчетов")
    parser.add_argument("--upload-file", type=str, help="Путь к файлу для загрузки")
    parser.add_argument("--upload-dir", type=str, help="Путь к директории с файлами для загрузки (с подкаталогами)")
    parser.add_argument("--study-code", type=str, help="Код исследования (для создаваемых исследований)")
    
    args = parser.parse_args()
    
    # Парсим workspace_id
    workspace_id = None
    if args.workspace_id:
        try:
            workspace_id = UUID(args.workspace_id)
        except ValueError:
            logger.error(f"Неверный формат workspace_id: {args.workspace_id}")
            return
    
    # Парсим output_dir
    output_dir = None
    if args.output:
        output_dir = Path(args.output)
    
    # Парсим doc_type enum (если не "any")
    doc_type_enum = None
    if args.doc_type != "any":
        try:
            doc_type_enum = DocumentType(args.doc_type)
        except ValueError:
            logger.error(f"Неверный doc_type: {args.doc_type}")
            return
    
    # Создаём подключение к БД
    engine = create_async_engine(settings.async_database_url, echo=False)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as db:
        try:
            # Если указаны файлы для загрузки, сначала загружаем их
            uploaded_version_ids = []
            
            if args.upload_file or args.upload_dir:
                if not workspace_id:
                    logger.error("--workspace-id обязателен при загрузке файлов")
                    return
                
                if not doc_type_enum:
                    logger.error("--doc-type должен быть указан (protocol или sap) при загрузке файлов")
                    return
                
                # Находим файлы для загрузки
                files_to_upload: list[Path] = []
                
                if args.upload_file:
                    file_path = Path(args.upload_file)
                    files_to_upload.extend(find_document_files(file_path))
                
                if args.upload_dir:
                    dir_path = Path(args.upload_dir)
                    files_to_upload.extend(find_document_files(dir_path))
                
                if not files_to_upload:
                    logger.warning("Не найдено файлов для загрузки")
                else:
                    logger.info(f"Найдено {len(files_to_upload)} файлов для загрузки")
                    
                    # Загружаем файлы
                    for i, file_path in enumerate(files_to_upload, 1):
                        try:
                            logger.info(f"Загрузка файла {i}/{len(files_to_upload)}: {file_path.name}")
                            version = await upload_file_to_campaign(
                                db=db,
                                file_path=file_path,
                                workspace_id=workspace_id,
                                doc_type=doc_type_enum,
                                study_code=args.study_code,
                            )
                            uploaded_version_ids.append(version.id)
                            logger.info(f"Файл загружен: version_id={version.id}")
                        except IntegrityError as e:
                            await db.rollback()
                            logger.error(f"Ошибка целостности БД при загрузке файла {file_path}: {e}", exc_info=True)
                        except Exception as e:
                            await db.rollback()
                            logger.error(f"Ошибка при загрузке файла {file_path}: {e}", exc_info=True)
                    
                    logger.info(f"Загружено {len(uploaded_version_ids)} файлов")
            
            # Запускаем кампанию ингестии
            await run_campaign(
                db=db,
                workspace_id=workspace_id,
                doc_type=args.doc_type,
                limit=args.limit,
                since=args.since,
                dry_run=args.dry_run,
                concurrency=args.concurrency,
                output_dir=output_dir,
            )
        finally:
            await engine.dispose()


if __name__ == "__main__":
    # На Windows необходимо использовать SelectorEventLoop вместо ProactorEventLoop
    # для совместимости с psycopg (async драйвер PostgreSQL)
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())

