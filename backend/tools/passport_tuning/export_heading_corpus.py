"""
Оффлайн-утилита для экспорта heading corpus из базы данных.

Экспортирует записи заголовков (heading records) для document_version
со статусом ready в формате JSONL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Добавляем путь к backend для импорта модулей приложения
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.config import settings
from app.db.enums import AnchorContentType, DocumentType, IngestionStatus


def extract_heading_level(location_json: dict[str, Any] | None) -> int | None:
    """Извлекает уровень заголовка из location_json.
    
    Сначала пытается извлечь из style (например, "Heading 1" -> 1),
    затем из section_path (количество "/" + 1).
    """
    if not location_json or not isinstance(location_json, dict):
        return None
    
    # 1) Пытаемся извлечь из style
    style = location_json.get("style")
    if isinstance(style, str):
        m = re.match(r"^\s*Heading\s+(\d+)\s*$", style, flags=re.IGNORECASE)
        if m:
            try:
                level = int(m.group(1))
                if 1 <= level <= 9:
                    return level
            except ValueError:
                pass
    
    return None


def extract_para_index(location_json: dict[str, Any] | None) -> int | None:
    """Извлекает para_index из location_json."""
    if not location_json or not isinstance(location_json, dict):
        return None
    para_index = location_json.get("para_index")
    if isinstance(para_index, int):
        return para_index
    return None


def compute_window_stats(window_anchors: list[dict[str, Any]]) -> dict[str, Any]:
    """Вычисляет статистику для окна под заголовком.
    
    Args:
        window_anchors: Список словарей с полями content_type и text_norm
        
    Returns:
        Словарь с полями:
        - content_type_counts: dict[str, int] - количество по content_type
        - total_chars: int - общее количество символов
        - sample_text: str - конкат первых 5 anchor.text_norm (обрезать до 500 символов)
    """
    content_type_counts: dict[str, int] = {}
    total_chars = 0
    sample_texts: list[str] = []
    
    for anchor in window_anchors[:50]:  # Ограничиваем до 50
        content_type = anchor.get("content_type", "")
        if content_type:
            content_type_counts[content_type] = content_type_counts.get(content_type, 0) + 1
        
        text_norm = anchor.get("text_norm", "")
        if text_norm:
            total_chars += len(text_norm)
            if len(sample_texts) < 5:
                sample_texts.append(text_norm)
    
    # Конкатенируем первые 5 текстов и обрезаем до 500 символов
    sample_text = " ".join(sample_texts)[:500]
    
    return {
        "content_type_counts": content_type_counts,
        "total_chars": total_chars,
        "sample_text": sample_text,
    }


def fetch_heading_records(
    engine: Engine,
    workspace_id: UUID | None,
    doc_type: DocumentType | None,
    limit_docs: int | None,
) -> list[dict[str, Any]]:
    """Получает heading records из базы данных.
    
    Использует один оптимизированный SQL запрос с CTE для минимизации
    количества запросов к БД.
    
    Args:
        engine: SQLAlchemy engine для подключения к БД
        workspace_id: UUID workspace для фильтрации (опционально)
        doc_type: Тип документа для фильтрации (опционально)
        limit_docs: Ограничение количества документов (опционально)
        
    Returns:
        Список словарей с heading records
    """
    # Базовый CTE для получения document_versions со статусом ready
    base_cte = """
    WITH ready_versions AS (
        SELECT 
            dv.id AS doc_version_id,
            dv.document_id,
            dv.document_language,
            d.doc_type,
            d.workspace_id
        FROM document_versions dv
        INNER JOIN documents d ON d.id = dv.document_id
        WHERE dv.ingestion_status = :ingestion_status_ready
    """
    
    params: dict[str, Any] = {
        "ingestion_status_ready": IngestionStatus.READY.value,
    }
    
    # Добавляем фильтры
    filters = []
    if workspace_id:
        filters.append("d.workspace_id = :workspace_id")
        params["workspace_id"] = str(workspace_id)
    
    if doc_type:
        filters.append("d.doc_type = :doc_type")
        params["doc_type"] = doc_type.value
    
    if filters:
        base_cte += " AND " + " AND ".join(filters)
    
    # Ограничение количества документов
    if limit_docs:
        base_cte += f"\n        LIMIT {limit_docs}"
    
    base_cte += "\n    )"
    
    # Основной запрос: получаем заголовки и окно под ними
    query = f"""
    {base_cte},
    headings AS (
        SELECT 
            h.id AS hdr_anchor_id,
            h.doc_version_id,
            h.section_path,
            h.text_raw AS heading_text_raw,
            h.text_norm AS heading_text_norm,
            h.location_json AS heading_location_json,
            h.ordinal AS heading_ordinal,
            rv.document_id,
            rv.doc_type,
            rv.document_language
        FROM anchors h
        INNER JOIN ready_versions rv ON rv.doc_version_id = h.doc_version_id
        WHERE h.content_type = :content_type_hdr
    ),
    heading_windows AS (
        SELECT 
            h.hdr_anchor_id,
            h.doc_version_id,
            h.document_id,
            h.doc_type,
            h.document_language,
            h.section_path,
            h.heading_text_raw,
            h.heading_text_norm,
            h.heading_location_json,
            h.heading_ordinal,
            -- Получаем первые 50 anchors после заголовка в том же section_path
            (
                SELECT COALESCE(
                    json_agg(
                        json_build_object(
                            'content_type', a.content_type::text,
                            'text_norm', a.text_norm,
                            'ordinal', a.ordinal
                        )
                    ),
                    '[]'::json
                )
                FROM (
                    SELECT a.content_type, a.text_norm, a.ordinal
                    FROM anchors a
                    WHERE a.doc_version_id = h.doc_version_id
                        AND a.section_path = h.section_path
                        AND a.ordinal > h.heading_ordinal
                        AND a.content_type != :content_type_hdr
                    ORDER BY a.ordinal
                    LIMIT 50
                ) a
            ) AS window_anchors
        FROM headings h
    )
    SELECT 
        hw.doc_version_id,
        hw.document_id,
        hw.doc_type::text,
        hw.document_language::text,
        hw.hdr_anchor_id,
        hw.heading_text_raw,
        hw.heading_text_norm,
        hw.section_path,
        hw.heading_location_json,
        hw.window_anchors
    FROM heading_windows hw
    ORDER BY hw.doc_version_id, hw.heading_ordinal
    """
    
    params["content_type_hdr"] = AnchorContentType.HDR.value
    
    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()
    
    # Преобразуем результаты в список словарей
    records = []
    for row in rows:
        heading_location_json = row.heading_location_json or {}
        
        # Обрабатываем window_anchors: может быть JSON массив или уже список
        window_anchors_raw = row.window_anchors
        if window_anchors_raw is None:
            window_anchors = []
        elif isinstance(window_anchors_raw, str):
            # Если это строка JSON, парсим её
            window_anchors = json.loads(window_anchors_raw)
        else:
            # Уже список или другой итерируемый объект
            window_anchors = list(window_anchors_raw) if window_anchors_raw else []
        
        # Извлекаем heading_level и para_index
        heading_level = extract_heading_level(heading_location_json)
        para_index = extract_para_index(heading_location_json)
        
        # Вычисляем статистику окна
        window_stats = compute_window_stats(window_anchors)
        
        record = {
            "doc_version_id": str(row.doc_version_id),
            "document_id": str(row.document_id),
            "doc_type": row.doc_type,
            "detected_language": row.document_language,
            "hdr_anchor_id": str(row.hdr_anchor_id),
            "heading_text_raw": row.heading_text_raw,
            "heading_text_norm": row.heading_text_norm,
            "heading_level": heading_level,
            "para_index": para_index,
            "section_path": row.section_path,
            "window": window_stats,
        }
        records.append(record)
    
    return records


def export_to_jsonl(records: list[dict[str, Any]], output_path: Path) -> None:
    """Экспортирует записи в JSONL файл.
    
    Args:
        records: Список словарей с heading records
        output_path: Путь к выходному файлу
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            json_line = json.dumps(record, ensure_ascii=False)
            f.write(json_line + "\n")


def main() -> None:
    """Главная функция CLI."""
    parser = argparse.ArgumentParser(
        description="Экспорт heading corpus из базы данных в JSONL формат"
    )
    parser.add_argument(
        "--workspace-id",
        type=str,
        help="UUID workspace для фильтрации документов",
    )
    parser.add_argument(
        "--doc-type",
        type=str,
        default="protocol",
        help="Тип документа для фильтрации (по умолчанию: protocol)",
    )
    parser.add_argument(
        "--limit-docs",
        type=int,
        help="Ограничение количества документов для обработки",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Путь к выходному JSONL файлу",
    )
    
    args = parser.parse_args()
    
    # Валидация аргументов
    workspace_id: UUID | None = None
    if args.workspace_id:
        try:
            workspace_id = UUID(args.workspace_id)
        except ValueError:
            print(f"Ошибка: неверный формат workspace-id: {args.workspace_id}", file=sys.stderr)
            sys.exit(1)
    
    doc_type: DocumentType | None = None
    if args.doc_type:
        try:
            doc_type = DocumentType(args.doc_type)
        except ValueError:
            print(f"Ошибка: неверный тип документа: {args.doc_type}", file=sys.stderr)
            print(f"Допустимые значения: {[e.value for e in DocumentType]}", file=sys.stderr)
            sys.exit(1)
    
    output_path = Path(args.out)
    
    # Создаём синхронный engine для подключения к БД
    engine = create_engine(settings.sync_database_url, echo=False)
    
    try:
        # Получаем heading records
        print(f"Загрузка heading records...", file=sys.stderr)
        records = fetch_heading_records(engine, workspace_id, doc_type, args.limit_docs)
        print(f"Найдено {len(records)} heading records", file=sys.stderr)
        
        # Экспортируем в JSONL
        print(f"Экспорт в {output_path}...", file=sys.stderr)
        export_to_jsonl(records, output_path)
        print(f"Экспорт завершён. Записано {len(records)} записей.", file=sys.stderr)
        
    except Exception as e:
        print(f"Ошибка при выполнении: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

