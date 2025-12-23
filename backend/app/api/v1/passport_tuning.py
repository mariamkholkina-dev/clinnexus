from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.core.logging import logger
from app.db.enums import DocumentType
from app.db.models.topics import ClusterAssignment
from app.db.models.studies import DocumentVersion
from app.schemas.passport_tuning import (
    Cluster,
    ClustersResponse,
    ClusterMappingItem,
    MappingMode,
    MappingResponse,
)
from app.services.topic_evidence_builder import TopicEvidenceBuilder
from sqlalchemy import select
from uuid import UUID

router = APIRouter(tags=["passport-tuning"])

# Lock для защиты от гонок при записи
_file_lock = threading.Lock()


def get_clusters_file_path() -> Path:
    """Возвращает путь к файлу clusters.json."""
    clusters_path = Path(settings.passport_tuning_clusters_path)
    if not clusters_path.is_absolute():
        # Относительный путь - относительно корня backend
        backend_root = Path(__file__).parent.parent.parent.parent
        clusters_path = backend_root / clusters_path
    return clusters_path


def get_mapping_file_path() -> Path:
    """Возвращает путь к файлу cluster_to_section_key.json."""
    mapping_path = Path(settings.passport_tuning_mapping_path)
    if not mapping_path.is_absolute():
        # Относительный путь - относительно корня backend
        backend_root = Path(__file__).parent.parent.parent.parent
        mapping_path = backend_root / mapping_path
    return mapping_path


@router.get("/clusters", response_model=ClustersResponse)
async def get_clusters(
    page: int = Query(1, ge=1, description="Номер страницы"),
    page_size: int = Query(100, ge=1, le=1000, description="Размер страницы"),
    search: str | None = Query(None, description="Поиск по top_titles_ru/en"),
) -> ClustersResponse:
    """Возвращает список кластеров из clusters.json."""
    clusters_file = get_clusters_file_path()

    if not clusters_file.exists():
        logger.warning(f"Файл clusters.json не найден: {clusters_file}")
        return ClustersResponse(items=[], total=0)

    try:
        with open(clusters_file, "r", encoding="utf-8") as f:
            clusters_data = json.load(f)

        # Преобразуем в список Cluster
        all_clusters: list[Cluster] = []
        for item in clusters_data:
            # Преобразуем cluster_id в строку
            cluster_id = str(item.get("cluster_id", ""))
            cluster = Cluster(
                cluster_id=cluster_id,
                top_titles_ru=item.get("top_titles_ru", []),
                top_titles_en=item.get("top_titles_en", []),
                examples=item.get("examples", []),
                stats=item.get("stats", {}),
                candidate_section_1=item.get("candidate_section_1"),
                candidate_section_2=item.get("candidate_section_2"),
                candidate_section_3=item.get("candidate_section_3"),
                default_section=item.get("default_section"),  # Для обратной совместимости
            )
            all_clusters.append(cluster)

        # Фильтрация по поисковому запросу
        if search and search.strip():
            search_lower = search.lower().strip()
            filtered_clusters = []
            for cluster in all_clusters:
                # Поиск в top_titles_ru и top_titles_en
                titles_ru = " ".join(cluster.top_titles_ru).lower()
                titles_en = " ".join(cluster.top_titles_en).lower()
                if search_lower in titles_ru or search_lower in titles_en:
                    filtered_clusters.append(cluster)
            all_clusters = filtered_clusters

        # Пагинация
        total = len(all_clusters)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_clusters = all_clusters[start_idx:end_idx]

        return ClustersResponse(items=paginated_clusters, total=total)

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга clusters.json: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка чтения clusters.json: {e}",
        )
    except Exception as e:
        logger.error(f"Ошибка при чтении clusters.json: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при чтении clusters.json: {e}",
        )


@router.get("/mapping", response_model=MappingResponse)
async def get_mapping() -> MappingResponse:
    """Возвращает текущий mapping из cluster_to_section_key.json.
    
    Поддерживает обратную совместимость: если mapping_mode отсутствует, 
    устанавливается "single", если notes отсутствует - пустая строка.
    """
    mapping_file = get_mapping_file_path()

    if not mapping_file.exists():
        return MappingResponse(mapping={})

    try:
        with open(mapping_file, "r", encoding="utf-8") as f:
            mapping_data = json.load(f)

        # Валидация и преобразование с обратной совместимостью
        validated_mapping: dict[str, dict[str, str | None]] = {}
        for cluster_id, item_data in mapping_data.items():
            try:
                # Обратная совместимость: добавляем дефолты для старых записей
                if "mapping_mode" not in item_data:
                    item_data["mapping_mode"] = MappingMode.SINGLE.value
                if "notes" not in item_data:
                    item_data["notes"] = None

                item = ClusterMappingItem.model_validate(item_data)
                validated_mapping[str(cluster_id)] = {
                    "doc_type": item.doc_type.value if item.doc_type else None,
                    "section_key": item.section_key,
                    "title_ru": item.title_ru if item.title_ru else None,
                    "mapping_mode": item.mapping_mode.value,
                    "notes": item.notes if item.notes else None,
                }
            except Exception as e:
                logger.warning(f"Ошибка валидации маппинга для cluster_id={cluster_id}: {e}")
                continue

        return MappingResponse(mapping=validated_mapping)

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга mapping файла: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка чтения mapping файла: {e}",
        )
    except Exception as e:
        logger.error(f"Ошибка при чтении mapping файла: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при чтении mapping файла: {e}",
        )


@router.get("/sections")
async def get_sections(
    doc_type: DocumentType = Query(..., description="Тип документа"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Возвращает структуру секций для указанного doc_type.
    
    ПРИМЕЧАНИЕ: Таблицы taxonomy удалены. Структура документов определяется через templates.
    Возвращает пустой объект для обратной совместимости.
    """
    return {
        "nodes": [],
        "aliases": [],
        "related": [],
    }


@router.post("/mapping", status_code=status.HTTP_200_OK)
async def save_mapping(
    mapping_data: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Сохраняет полный mapping на диск.

    Валидирует все элементы и сохраняет атомарно (tmp -> rename).
    ПРИМЕЧАНИЕ: Нормализация через taxonomy удалена. section_key используется как есть.
    """
    mapping_file = get_mapping_file_path()

    # Создаем директорию, если не существует
    mapping_file.parent.mkdir(parents=True, exist_ok=True)

    # Валидация входящих данных
    validated_mapping: dict[str, ClusterMappingItem] = {}
    validation_errors: list[dict[str, str]] = []

    for cluster_id, item_data in mapping_data.items():
        try:
            # Добавляем дефолты для mapping_mode, если не указан
            if "mapping_mode" not in item_data:
                item_data["mapping_mode"] = MappingMode.SINGLE.value

            item = ClusterMappingItem.model_validate(item_data)
            
            # section_key используется как есть (нормализация через taxonomy удалена)
            validated_mapping[str(cluster_id)] = item

        except Exception as e:
            validation_errors.append(
                {
                    "cluster_id": str(cluster_id),
                    "error": str(e),
                }
            )
            continue

    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Ошибки валидации",
                "errors": validation_errors,
            },
        )

    # Подготовка данных для сохранения
    save_data: dict[str, dict[str, str | None]] = {}
    for cluster_id, item in validated_mapping.items():
        save_data[cluster_id] = {
            "doc_type": item.doc_type.value if item.doc_type else None,
            "section_key": item.section_key,
            "title_ru": item.title_ru if item.title_ru else None,
            "mapping_mode": item.mapping_mode.value,
            "notes": item.notes if item.notes else None,
        }

    # Атомарная запись с блокировкой
    with _file_lock:
        tmp_file = mapping_file.with_suffix(".json.tmp")

        try:
            # Записываем во временный файл
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            # Атомарное переименование
            tmp_file.replace(mapping_file)

            logger.info(f"Mapping сохранен: {mapping_file}, элементов: {len(save_data)}")

            return {
                "message": "Mapping успешно сохранен",
                "items_count": len(save_data),
            }

        except Exception as e:
            # Удаляем временный файл в случае ошибки
            if tmp_file.exists():
                tmp_file.unlink()
            logger.error(f"Ошибка при сохранении mapping: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка при сохранении mapping: {e}",
            )


@router.get("/mapping/download")
async def download_mapping() -> FileResponse:
    """Отдает готовый JSON как attachment."""
    mapping_file = get_mapping_file_path()

    if not mapping_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл mapping не найден",
        )

    return FileResponse(
        path=str(mapping_file),
        filename="cluster_to_section_key.json",
        media_type="application/json",
    )


@router.get("/mapping/for_autotune")
async def get_mapping_for_autotune(
    include_needs_split: bool = Query(False, description="Включать кластеры с needs_split в included")
) -> dict[str, Any]:
    """Возвращает маппинг для автотюнинга паспортов.
    
    Исключает кластеры с mapping_mode="ambiguous" и mapping_mode="skip".
    По умолчанию также исключает "needs_split" (можно включить через параметр).
    
    Возвращает:
    - included: маппинг для использования в автотюнинге
    - excluded: списки исключенных кластеров по категориям
    """
    mapping_file = get_mapping_file_path()

    if not mapping_file.exists():
        return {
            "included": {},
            "excluded": {
                "ambiguous": [],
                "skip": [],
                "needs_split": [],
            },
        }

    try:
        with open(mapping_file, "r", encoding="utf-8") as f:
            mapping_data = json.load(f)

        included: dict[str, dict[str, str | None]] = {}
        excluded_ambiguous: list[dict[str, str | None]] = []
        excluded_skip: list[dict[str, str | None]] = []
        excluded_needs_split: list[dict[str, str | None]] = []

        for cluster_id, item_data in mapping_data.items():
            # Обратная совместимость
            if "mapping_mode" not in item_data:
                item_data["mapping_mode"] = MappingMode.SINGLE.value

            try:
                item = ClusterMappingItem.model_validate(item_data)
                mapping_mode = item.mapping_mode

                cluster_entry = {
                    "cluster_id": str(cluster_id),
                    "doc_type": item.doc_type.value if item.doc_type else None,
                    "section_key": item.section_key,
                    "title_ru": item.title_ru if item.title_ru else None,
                    "notes": item.notes if item.notes else None,
                }

                if mapping_mode == MappingMode.AMBIGUOUS:
                    excluded_ambiguous.append(cluster_entry)
                elif mapping_mode == MappingMode.SKIP:
                    excluded_skip.append(cluster_entry)
                elif mapping_mode == MappingMode.NEEDS_SPLIT:
                    if include_needs_split:
                        included[str(cluster_id)] = {
                            "doc_type": item.doc_type.value if item.doc_type else None,
                            "section_key": item.section_key,
                            "title_ru": item.title_ru if item.title_ru else None,
                            "mapping_mode": item.mapping_mode.value,
                            "notes": item.notes if item.notes else None,
                        }
                    else:
                        excluded_needs_split.append(cluster_entry)
                else:  # SINGLE
                    included[str(cluster_id)] = {
                        "doc_type": item.doc_type.value if item.doc_type else None,
                        "section_key": item.section_key,
                        "title_ru": item.title_ru if item.title_ru else None,
                        "mapping_mode": item.mapping_mode.value,
                        "notes": item.notes if item.notes else None,
                    }
            except Exception as e:
                logger.warning(f"Ошибка обработки маппинга для cluster_id={cluster_id}: {e}")
                continue

        return {
            "included": included,
            "excluded": {
                "ambiguous": excluded_ambiguous,
                "skip": excluded_skip,
                "needs_split": excluded_needs_split,
            },
        }

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга mapping файла: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка чтения mapping файла: {e}",
        )
    except Exception as e:
        logger.error(f"Ошибка при чтении mapping файла: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при чтении mapping файла: {e}",
        )


@router.post("/cluster-to-topic-mapping")
async def upload_cluster_to_topic_mapping(
    mapping: dict[str, str] = Body(..., description="Маппинг cluster_id -> topic_key (JSON)"),
    doc_version_id: UUID = Query(..., description="UUID версии документа"),
    mapped_by: str = Query("user", description="Кто создал маппинг (user/system)"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Загружает маппинг cluster_id -> topic_key для конкретного doc_version.
    
    Выполняет:
    1. Upsert в cluster_assignments
    2. Пересборку topic_evidence через TopicEvidenceBuilder
    
    Args:
        doc_version_id: UUID версии документа
        mapping: JSON объект вида {"cluster_id": "topic_key", ...}
        mapped_by: Кто создал маппинг (по умолчанию "user")
    
    Returns:
        Результат операции с количеством созданных/обновленных записей
    """
    # Проверяем, что doc_version существует
    stmt = select(DocumentVersion).where(DocumentVersion.id == doc_version_id)
    result = await db.execute(stmt)
    doc_version = result.scalar_one_or_none()
    
    if not doc_version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document version {doc_version_id} не найден",
        )
    
    try:
        # Преобразуем mapping: ключи могут быть строками или числами
        cluster_assignments_data: list[dict[str, Any]] = []
        for cluster_id_str, topic_key in mapping.items():
            try:
                cluster_id = int(cluster_id_str)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Некорректный cluster_id: {cluster_id_str}",
                )
            
            cluster_assignments_data.append({
                "cluster_id": cluster_id,
                "topic_key": topic_key,
            })
        
        # Upsert в cluster_assignments
        upserted_count = 0
        for item in cluster_assignments_data:
            # Проверяем, существует ли уже запись
            stmt = select(ClusterAssignment).where(
                ClusterAssignment.doc_version_id == doc_version_id,
                ClusterAssignment.cluster_id == item["cluster_id"],
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()
            
            if existing:
                # Обновляем существующую запись
                existing.topic_key = item["topic_key"]
                existing.mapped_by = mapped_by
                existing.confidence = None  # Можно вычислить позже
            else:
                # Создаем новую запись
                assignment = ClusterAssignment(
                    doc_version_id=doc_version_id,
                    cluster_id=item["cluster_id"],
                    topic_key=item["topic_key"],
                    mapped_by=mapped_by,
                    confidence=None,
                    notes=None,
                )
                db.add(assignment)
            
            upserted_count += 1
        
        await db.commit()
        
        # Пересобираем topic_evidence
        builder = TopicEvidenceBuilder(db)
        evidence_count = await builder.build_evidence_for_doc_version(doc_version_id)
        
        logger.info(
            f"Загружен маппинг для doc_version_id={doc_version_id}: "
            f"{upserted_count} cluster_assignments, {evidence_count} topic_evidence"
        )
        
        return {
            "doc_version_id": str(doc_version_id),
            "cluster_assignments_upserted": upserted_count,
            "topic_evidence_created": evidence_count,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Ошибка при загрузке маппинга: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при загрузке маппинга: {e}",
        )

