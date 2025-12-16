"""Парсер DOCX документов для создания anchors."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from docx import Document
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph

from app.db.enums import AnchorContentType
from app.services.ingestion.heading_detector import HeadingDetector, HeadingHit, DocStats


def normalize_text(text: str) -> str:
    """
    Нормализует текст для создания стабильного хеша.
    
    Правила нормализации:
    - trim (удаление пробелов по краям)
    - последовательности whitespace → один пробел
    - сохранение цифр/пунктуации
    - пустые строки возвращают пустую строку
    
    Args:
        text: Исходный текст
        
    Returns:
        Нормализованный текст
    """
    if not text:
        return ""
    
    # Заменяем последовательности whitespace на один пробел
    normalized = re.sub(r'\s+', ' ', text)
    # Удаляем пробелы по краям
    normalized = normalized.strip()
    
    return normalized


def get_text_hash(text_norm: str) -> str:
    """
    Вычисляет SHA256 хеш нормализованного текста.
    
    Args:
        text_norm: Нормализованный текст
        
    Returns:
        Hex-строка хеша (64 символа)
    """
    return hashlib.sha256(text_norm.encode('utf-8')).hexdigest()


def normalize_section_path(path_parts: list[str]) -> str:
    """
    Нормализует путь секции: trim + collapse spaces.
    
    Args:
        path_parts: Список частей пути (заголовков)
        
    Returns:
        Нормализованный путь (например "H1/H2/H3" или "ROOT")
    """
    if not path_parts:
        return "ROOT"
    
    # Нормализуем каждую часть (trim + collapse spaces)
    normalized_parts = []
    for part in path_parts:
        normalized = re.sub(r'\s+', ' ', part.strip())
        if normalized:
            normalized_parts.append(normalized)
    
    if not normalized_parts:
        return "ROOT"
    
    return "/".join(normalized_parts)


def is_list_item(paragraph: Paragraph) -> bool:
    """
    Проверяет, является ли параграф элементом списка.
    
    Проверяет:
    1. Наличие numPr (numbering properties) в XML представлении параграфа
    2. Стиль параграфа (если начинается с "List")
    
    Args:
        paragraph: Параграф из python-docx
        
    Returns:
        True, если параграф является элементом списка
    """
    # Проверяем стиль параграфа
    style_name = paragraph.style.name if paragraph.style else ""
    if style_name.startswith("List"):
        return True
    
    # Проверяем наличие numbering properties
    try:
        p_element: CT_P = paragraph._p
        if p_element.pPr is not None and p_element.pPr.numPr is not None:
            return True
    except (AttributeError, TypeError):
        pass
    
    return False


@dataclass
class AnchorCreate:
    """Данные для создания anchor в БД."""
    
    doc_version_id: UUID
    anchor_id: str
    section_path: str
    content_type: AnchorContentType
    ordinal: int
    text_raw: str
    text_norm: str
    text_hash: str
    location_json: dict[str, Any]


@dataclass
class DocxIngestResult:
    """Результат парсинга DOCX документа."""
    
    anchors: list[AnchorCreate]
    summary: dict[str, Any]
    warnings: list[str]


class DocxIngestor:
    """Парсер DOCX документов для создания anchors."""
    
    def ingest(self, file_path: str | Path, doc_version_id: UUID) -> DocxIngestResult:
        """
        Парсит DOCX документ и создаёт anchors.
        
        Args:
            file_path: Путь к DOCX файлу
            doc_version_id: UUID версии документа
            
        Returns:
            DocxIngestResult с anchors, summary и warnings
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        
        # Загружаем документ
        doc = Document(str(file_path))
        
        # Вычисляем статистику документа для visual fallback
        doc_stats = HeadingDetector.compute_doc_stats(list(doc.paragraphs))
        
        # Первый проход: детект только style/outline/numbering
        detector = HeadingDetector(enable_visual_fallback=False)
        detector.set_doc_stats(doc_stats)
        
        # Собираем hits для всех параграфов
        paragraph_hits: list[tuple[Paragraph, HeadingHit]] = []
        heading_count = 0
        
        for paragraph in doc.paragraphs:
            hit = detector.detect(paragraph)
            paragraph_hits.append((paragraph, hit))
            if hit.is_heading:
                heading_count += 1
        
        # Определяем, нужен ли visual fallback
        # Если заголовков мало (< 3 на документ > 50 параграфов) → rerun с visual fallback
        total_paragraphs = len([p for p in doc.paragraphs if normalize_text(p.text)])
        enable_visual = False
        
        if heading_count == 0 or (total_paragraphs > 50 and heading_count < 3):
            enable_visual = True
            detector = HeadingDetector(enable_visual_fallback=True)
            detector.set_doc_stats(doc_stats)
            
            # Пересчитываем hits с visual fallback
            paragraph_hits = []
            heading_count = 0
            for paragraph in doc.paragraphs:
                hit = detector.detect(paragraph)
                paragraph_hits.append((paragraph, hit))
                if hit.is_heading:
                    heading_count += 1
        
        # Стек заголовков для построения section_path
        # Каждый элемент: (level, normalized_title)
        heading_stack: list[tuple[int, str]] = []
        
        # Счётчики ordinal для каждого (section_path, content_type)
        # Ключ: (section_path, content_type)
        ordinal_counters: dict[tuple[str, AnchorContentType], int] = {}
        
        anchors: list[AnchorCreate] = []
        warnings: list[str] = []
        
        # Диагностика заголовков
        heading_detected_count = 0
        heading_levels_histogram: dict[str, int] = {}
        heading_detection_mode_counts: dict[str, int] = {}
        
        para_index = 0  # Счётчик всех параграфов для location_json
        
        # Проходим по всем параграфам документа с hits
        for paragraph, hit in paragraph_hits:
            para_index += 1
            
            # Получаем сырой текст
            text_raw = paragraph.text
            
            # Пропускаем пустые параграфы
            text_norm = normalize_text(text_raw)
            if not text_norm:
                continue
            
            # Определяем стиль
            style_name = paragraph.style.name if paragraph.style else "Normal"
            
            # Определяем content_type и обновляем стек заголовков
            content_type: AnchorContentType
            current_section_path: str
            
            if hit.is_heading:
                # Это заголовок
                content_type = AnchorContentType.HDR
                level = hit.level or 1
                
                # Обновляем стек заголовков
                # Удаляем все заголовки с уровнем >= текущего уровня
                heading_stack = [h for h in heading_stack if h[0] < level]
                # Добавляем текущий заголовок (используем normalized_title)
                heading_stack.append((level, hit.normalized_title))
                
                # Получаем текущий section_path
                path_parts = [h[1] for h in heading_stack]
                current_section_path = normalize_section_path(path_parts)
                
                # Обновляем диагностику
                heading_detected_count += 1
                level_str = str(level)
                heading_levels_histogram[level_str] = heading_levels_histogram.get(level_str, 0) + 1
                heading_detection_mode_counts[hit.mode] = heading_detection_mode_counts.get(hit.mode, 0) + 1
            else:
                # Обычный параграф или элемент списка
                if is_list_item(paragraph):
                    content_type = AnchorContentType.LI
                else:
                    content_type = AnchorContentType.P
                
                # Используем текущий стек заголовков для section_path
                path_parts = [h[1] for h in heading_stack]
                current_section_path = normalize_section_path(path_parts)
            
            # Получаем ordinal для данного (section_path, content_type)
            key = (current_section_path, content_type)
            ordinal = ordinal_counters.get(key, 0) + 1
            ordinal_counters[key] = ordinal
            
            # Вычисляем hash
            text_hash = get_text_hash(text_norm)
            
            # Формируем anchor_id: {doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash}
            doc_version_id_str = str(doc_version_id)
            anchor_id = f"{doc_version_id_str}:{current_section_path}:{content_type.value}:{ordinal}:{text_hash}"
            
            # Формируем location_json
            location_json = {
                "para_index": para_index,
                "style": style_name,
                "section_path": current_section_path,
            }
            
            # Создаём anchor
            anchor = AnchorCreate(
                doc_version_id=doc_version_id,
                anchor_id=anchor_id,
                section_path=current_section_path,
                content_type=content_type,
                ordinal=ordinal,
                text_raw=text_raw,
                text_norm=text_norm,
                text_hash=text_hash,
                location_json=location_json,
            )
            
            anchors.append(anchor)
        
        # Определяем качество заголовков
        heading_quality = "none"
        if heading_detected_count == 0:
            heading_quality = "none"
            warnings.append("No headings detected; section_path fallback to ROOT")
        elif heading_detected_count <= 2:
            heading_quality = "low"
        else:
            # Проверяем, не слишком ли много visual fallback
            visual_count = heading_detection_mode_counts.get("visual", 0)
            if visual_count > 0 and visual_count / heading_detected_count > 0.8:
                heading_quality = "low"
                warnings.append("Headings detected mostly via visual fallback; verify structure")
            else:
                heading_quality = "ok"
        
        # Формируем summary
        counts_by_type = {}
        for anchor in anchors:
            content_type_str = anchor.content_type.value
            counts_by_type[content_type_str] = counts_by_type.get(content_type_str, 0) + 1
        
        # Собираем уникальные section_path
        unique_sections = set(anchor.section_path for anchor in anchors)
        
        summary = {
            "anchors_count": len(anchors),
            "counts_by_type": counts_by_type,
            "num_sections": len(unique_sections),
            "sections": sorted(list(unique_sections)),
            # Диагностика заголовков
            "heading_detected_count": heading_detected_count,
            "heading_levels_histogram": heading_levels_histogram,
            "heading_detection_mode_counts": heading_detection_mode_counts,
            "heading_quality": heading_quality,
            "warnings": warnings,
        }
        
        return DocxIngestResult(
            anchors=anchors,
            summary=summary,
            warnings=warnings,
        )

