"""Детектор заголовков для DOCX документов."""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from typing import Any

from docx.oxml.ns import qn
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph

logger = logging.getLogger("clinnexus")


@dataclass
class HeadingHit:
    """Результат детекции заголовка."""
    
    is_heading: bool
    level: int | None  # 1..9
    confidence: float  # 0..1
    mode: str  # style, outline, numbering, visual, none
    normalized_title: str


def normalize_title(text: str) -> str:
    """
    Нормализует текст заголовка для section_path.
    
    Правила:
    - strip
    - collapse whitespace to single space
    - remove zero-width chars
    - trim trailing colon (опционально)
    - ограничить длину до 120 символов
    
    Args:
        text: Исходный текст
        
    Returns:
        Нормализованный текст
    """
    if not text:
        return ""
    
    # Удаляем zero-width chars
    normalized = re.sub(r'[\u200b-\u200f\ufeff]', '', text)
    
    # Collapse whitespace
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Strip
    normalized = normalized.strip()
    
    # Trim trailing colon (опционально)
    if normalized.endswith(':'):
        normalized = normalized[:-1].strip()
    
    # Ограничиваем длину до 120 символов
    if len(normalized) > 120:
        normalized = normalized[:120].rstrip()
    
    return normalized


@dataclass
class DocStats:
    """Статистика документа для visual fallback."""
    
    median_font_size: float | None = None
    bold_ratio: float = 0.0
    total_paragraphs: int = 0


class HeadingDetector:
    """Детектор заголовков в DOCX документах."""
    
    def __init__(self, enable_visual_fallback: bool = False) -> None:
        """
        Инициализация детектора.
        
        Args:
            enable_visual_fallback: Включить визуальный детект (fallback)
        """
        self.enable_visual_fallback = enable_visual_fallback
        self._doc_stats: DocStats | None = None
    
    def set_doc_stats(self, doc_stats: DocStats) -> None:
        """Устанавливает статистику документа для visual fallback."""
        self._doc_stats = doc_stats
    
    def detect(self, paragraph: Paragraph, para_index: int | None = None) -> HeadingHit:
        """
        Определяет, является ли параграф заголовком.
        
        Порядок проверки:
        1. Style (Heading 1, Heading 2, ...)
        2. Outline level (w:outlineLvl в XML)
        3. Numbering (1.2.3 ...)
        4. Visual (если enable_visual_fallback=True)
        
        Args:
            paragraph: Параграф из python-docx
            para_index: Индекс параграфа для debug логирования (опционально)
            
        Returns:
            HeadingHit с результатом детекции
        """
        text = paragraph.text.strip()
        if not text:
            return HeadingHit(
                is_heading=False,
                level=None,
                confidence=0.0,
                mode="none",
                normalized_title="",
            )
        
        # 1. Проверка по стилю
        hit = self.detect_by_style(paragraph)
        if hit and hit.is_heading:
            return hit
        
        # 2. Проверка по outline level
        hit = self.detect_by_outline(paragraph)
        if hit and hit.is_heading:
            return hit
        
        # 3. Проверка по нумерации
        hit = self.detect_by_numbering(
            paragraph, para_index=para_index, rejection_counter=getattr(self, "_rejection_counter", None)
        )
        if hit and hit.is_heading:
            return hit
        
        # 4. Визуальный fallback (если включен)
        if self.enable_visual_fallback:
            hit = self.detect_by_visual(paragraph)
            if hit and hit.is_heading:
                return hit
        
        # Не заголовок
        return HeadingHit(
            is_heading=False,
            level=None,
            confidence=0.0,
            mode="none",
            normalized_title=normalize_title(text),
        )
    
    def get_outline_level(self, paragraph: Paragraph) -> int | None:
        """
        Читает w:outlineLvl из paragraph._p XML.
        
        Args:
            paragraph: Параграф из python-docx
            
        Returns:
            Уровень заголовка (1..9) или None
        """
        try:
            p_element: CT_P = paragraph._p
            if p_element.pPr is None:
                return None
            
            outline_elem = p_element.pPr.find(qn('w:outlineLvl'))
            if outline_elem is None:
                return None
            
            val_attr = outline_elem.get(qn('w:val'))
            if val_attr is None:
                return None
            
            # val в XML это 0-based, мы возвращаем 1-based
            level = int(val_attr) + 1
            if 1 <= level <= 9:
                return level
        except (AttributeError, TypeError, ValueError):
            pass
        
        return None
    
    def detect_by_style(self, paragraph: Paragraph, max_depth: int = 3) -> HeadingHit | None:
        """
        Детекция заголовка по стилю.
        
        Поддерживаемые стили:
        - Heading 1, Heading 2, ... (стандартные стили Word на английском)
        - Title 1, Title 2, ... (альтернативные стили заголовков на английском)
        - Заголовок 1, Заголовок 2, ... (стандартные стили Word на русском)
        - Название 1, Название 2, ... (альтернативные стили заголовков на русском)
        
        Args:
            paragraph: Параграф из python-docx
            max_depth: Максимальная глубина поиска стилей в иерархии (по умолчанию 3)
                      Ограничивает количество проверок базовых стилей для оптимизации производительности
            
        Returns:
            HeadingHit или None, если не заголовок
        """
        # Ограничение на глубину поиска стилей для оптимизации производительности
        depth = 0
        current_style = paragraph.style
        
        # Ищем стиль с ограничением глубины
        while current_style is not None and depth < max_depth:
            try:
                style_name = current_style.name if current_style else ""
            except (AttributeError, TypeError):
                break
            
            # Проверяем, является ли стиль заголовком (Heading, Title, Заголовок, Название)
            level = None
            
            # Английские стили
            if style_name.startswith('Heading'):
                # Извлекаем уровень из "Heading 1", "Heading 2", etc.
                level_str = style_name.replace('Heading', '').strip()
                if level_str.isdigit():
                    try:
                        level = int(level_str)
                    except ValueError:
                        pass
            elif style_name.startswith('Title'):
                # Извлекаем уровень из "Title 1", "Title 2", etc.
                level_str = style_name.replace('Title', '').strip()
                if level_str.isdigit():
                    try:
                        level = int(level_str)
                    except ValueError:
                        pass
            # Русские стили
            elif style_name.startswith('Заголовок'):
                # Извлекаем уровень из "Заголовок 1", "Заголовок 2", etc.
                level_str = style_name.replace('Заголовок', '').strip()
                if level_str.isdigit():
                    try:
                        level = int(level_str)
                    except ValueError:
                        pass
            elif style_name.startswith('Название'):
                # Извлекаем уровень из "Название 1", "Название 2", etc.
                level_str = style_name.replace('Название', '').strip()
                if level_str.isdigit():
                    try:
                        level = int(level_str)
                    except ValueError:
                        pass
            
            if level is not None and (1 <= level <= 9):
                text = paragraph.text.strip()
                return HeadingHit(
                    is_heading=True,
                    level=level,
                    confidence=0.95,
                    mode="style",
                    normalized_title=normalize_title(text),
                )
            
            # Переходим к базовому стилю (если доступен)
            depth += 1
            try:
                if hasattr(current_style, 'base_style') and current_style.base_style is not None:
                    current_style = current_style.base_style
                else:
                    break
            except (AttributeError, TypeError):
                break
        
        return None
    
    def detect_by_outline(self, paragraph: Paragraph) -> HeadingHit | None:
        """
        Детекция заголовка по outline level в XML.
        
        Args:
            paragraph: Параграф из python-docx
            
        Returns:
            HeadingHit или None, если не заголовок
        """
        level = self.get_outline_level(paragraph)
        if level is None:
            return None
        
        text = paragraph.text.strip()
        if not text:
            return None
        
        return HeadingHit(
            is_heading=True,
            level=level,
            confidence=0.90,
            mode="outline",
            normalized_title=normalize_title(text),
        )
    
    def detect_by_numbering(
        self,
        paragraph: Paragraph,
        para_index: int | None = None,
        rejection_counter: dict[str, int] | None = None,
    ) -> HeadingHit | None:
        """
        Детекция заголовка по нумерации (1.2.3 ...).
        
        Паттерн: ^\\d+(\\.\\d+)*[\\)\\.]?\\s+[A-ZА-ЯЁ]
        Level = count('.') + 1
        
        Анти-фильтры:
        - если paragraph является list item (numPr) → НЕ считать заголовком
        - если текст заканчивается точкой → скорее предложение, не заголовок
        - если текст содержит >= 2 предложений (". " встречается >= 2 раз) → отклоняем
        - если len(text) > 120 или word_count > 14 → слишком длинный для заголовка
        - если уровень 1 (без точек) → требуем дополнительные сигналы (ALL CAPS, bold, centered, большой шрифт)
        
        Args:
            paragraph: Параграф из python-docx
            para_index: Индекс параграфа для debug логирования (опционально)
            
        Returns:
            HeadingHit или None, если не заголовок
        """
        text = paragraph.text.strip()
        if not text:
            return None
        
        # Оптимизация: если параграф длиннее 500 символов, сразу возвращаем False
        # Это экономит время на сложных regex проверках для явно не-заголовков
        if len(text) > 500:
            if rejection_counter is not None:
                rejection_counter["too_long_early"] = rejection_counter.get("too_long_early", 0) + 1
            return None
        
        # Анти-фильтр: если это элемент списка, не считать заголовком
        # Проверяем стиль параграфа
        style_name = paragraph.style.name if paragraph.style else ""
        if style_name.startswith("List"):
            return None
        
        # Проверяем наличие numbering properties в XML
        try:
            p_element: CT_P = paragraph._p
            if p_element.pPr is not None and p_element.pPr.numPr is not None:
                return None
        except (AttributeError, TypeError):
            pass
        
        # Анти-фильтр: текст заканчивается точкой → скорее предложение
        if text.endswith('.'):
            if rejection_counter is not None:
                rejection_counter["ends_with_period"] = rejection_counter.get("ends_with_period", 0) + 1
            if para_index is not None:
                text_preview = text[:80] if len(text) > 80 else text
                logger.debug(
                    f"Отклонён параграф #{para_index} по numbering: заканчивается точкой. "
                    f"Текст: {text_preview!r}"
                )
            return None
        
        # Анти-фильтр: несколько предложений (>= 2 ". ")
        sentence_separators = text.count(". ")
        if sentence_separators >= 2:
            if rejection_counter is not None:
                rejection_counter["multiple_sentences"] = rejection_counter.get("multiple_sentences", 0) + 1
            if para_index is not None:
                text_preview = text[:80] if len(text) > 80 else text
                logger.debug(
                    f"Отклонён параграф #{para_index} по numbering: содержит {sentence_separators + 1} предложений. "
                    f"Текст: {text_preview!r}"
                )
            return None
        
        # Анти-фильтр: слишком длинный текст
        word_count = len(text.split())
        if len(text) > 120 or word_count > 14:
            if rejection_counter is not None:
                rejection_counter["too_long"] = rejection_counter.get("too_long", 0) + 1
            if para_index is not None:
                text_preview = text[:80] if len(text) > 80 else text
                logger.debug(
                    f"Отклонён параграф #{para_index} по numbering: слишком длинный "
                    f"(len={len(text)}, words={word_count}). Текст: {text_preview!r}"
                )
            return None
        
        # Строгий паттерн: после номера должен идти заглавный символ (латиница или кириллица)
        # Примеры: "3.2 Задачи исследования", "1.2 Study Objectives"
        # Отклоняем: "2 года. Не применять..." (после "2 " идёт строчная буква)
        pattern = r'^(\d+(?:\.\d+)*)[)\.]?\s+([A-ZА-ЯЁ])'
        match = re.match(pattern, text)
        if not match:
            return None
        
        # Вычисляем уровень по количеству точек
        numbering_part = match.group(1)
        dot_count = numbering_part.count('.')
        level = dot_count + 1
        
        # Ограничиваем уровень до 9
        if level > 9:
            return None
        
        # Анти-фильтр: отклоняем строки оглавления (заканчиваются на табуляцию + число)
        # Это должно быть проверено для всех уровней, но особенно важно для уровня 1
        if re.search(r'\t\d+$', text):
            if rejection_counter is not None:
                rejection_counter["toc_page_number"] = rejection_counter.get("toc_page_number", 0) + 1
            if para_index is not None:
                text_preview = text[:80] if len(text) > 80 else text
                logger.debug(
                    f"Отклонён параграф #{para_index} по numbering: строка оглавления (оканчивается на табуляцию + число). "
                    f"Текст: {text_preview!r}"
                )
            return None
        
        # Для уровня 1 (без точек) требуем дополнительные сигналы заголовка
        if level == 1:
            has_heading_signals = False
            
            # Проверка 1: ALL CAPS (>= 80% заглавных букв)
            # Извлекаем текст после номера
            text_after_number = text[match.end():].strip()
            if text_after_number:
                uppercase_count = sum(1 for c in text_after_number if c.isupper() and c.isalpha())
                total_alpha = sum(1 for c in text_after_number if c.isalpha())
                if total_alpha > 0:
                    uppercase_ratio = uppercase_count / total_alpha
                    if uppercase_ratio >= 0.8:
                        has_heading_signals = True
            
            # Проверка 2: bold-dominant (>= 80% runs bold)
            if not has_heading_signals:
                bold_count = 0
                total_runs = 0
                for run in paragraph.runs:
                    total_runs += 1
                    if run.bold:
                        bold_count += 1
                if total_runs > 0:
                    bold_ratio = bold_count / total_runs
                    if bold_ratio >= 0.8:
                        has_heading_signals = True
            
            # Проверка 3: centered alignment
            if not has_heading_signals:
                if paragraph.alignment is not None:
                    if paragraph.alignment == 1:  # CENTER
                        has_heading_signals = True
            
            # Проверка 4: font size > median + 2pt (если есть doc_stats)
            if not has_heading_signals and self._doc_stats and self._doc_stats.median_font_size:
                font_sizes = []
                for run in paragraph.runs:
                    if run.font and run.font.size is not None:
                        try:
                            size_pt = run.font.size.pt
                            if size_pt:
                                font_sizes.append(size_pt)
                        except (AttributeError, TypeError):
                            pass
                if font_sizes:
                    max_font_size = max(font_sizes)
                    if max_font_size > self._doc_stats.median_font_size + 2:
                        has_heading_signals = True
            
            # Проверка 5: ключевые слова канонических разделов
            # Если параграф содержит номер уровня 1 И ключевые слова канонических разделов,
            # принимаем его как заголовок, даже без других сигналов форматирования
            if not has_heading_signals and text_after_number:
                # Ключевые слова канонических разделов (регистронезависимый поиск)
                canonical_keywords = [
                    "план", "цель", "цели", "популяция", "препарат", "препараты",
                    "безопасность", "статистика", "процедур", "процедуры"
                ]
                text_lower = text_after_number.lower()
                has_canonical_keyword = any(keyword in text_lower for keyword in canonical_keywords)
                
                if has_canonical_keyword:
                    has_heading_signals = True
                    if para_index is not None:
                        text_preview = text[:80] if len(text) > 80 else text
                        logger.debug(
                            f"Принят параграф #{para_index} по numbering (уровень 1): содержит ключевое слово канонического раздела. "
                            f"Текст: {text_preview!r}"
                        )
            
            # Если нет дополнительных сигналов, отклоняем
            if not has_heading_signals:
                if rejection_counter is not None:
                    rejection_counter["level1_no_signals"] = rejection_counter.get("level1_no_signals", 0) + 1
                if para_index is not None:
                    text_preview = text[:80] if len(text) > 80 else text
                    logger.debug(
                        f"Отклонён параграф #{para_index} по numbering: уровень 1 без дополнительных сигналов заголовка. "
                        f"Текст: {text_preview!r}"
                    )
                return None
        
        return HeadingHit(
            is_heading=True,
            level=level,
            confidence=0.70,
            mode="numbering",
            normalized_title=normalize_title(text),
        )
    
    def detect_by_visual(self, paragraph: Paragraph) -> HeadingHit | None:
        """
        Детекция заголовка по визуальным признакам (fallback).
        
        Включается только если enable_visual_fallback=True и есть doc_stats.
        
        Scoring:
        +0.25 если почти весь текст bold
        +0.20 если center aligned
        +0.25 если font size > median + 2pt
        +0.10 если строка короткая (<= 80)
        -0.30 если строка слишком длинная (> 160)
        -0.30 если заканчивается точкой
        
        Порог: score >= 0.6
        
        Args:
            paragraph: Параграф из python-docx
            
        Returns:
            HeadingHit или None, если не заголовок
        """
        if not self.enable_visual_fallback or self._doc_stats is None:
            return None
        
        text = paragraph.text.strip()
        if not text:
            return None
        
        score = 0.0
        
        # Проверяем bold
        bold_count = 0
        total_runs = 0
        font_sizes = []
        
        for run in paragraph.runs:
            total_runs += 1
            if run.bold:
                bold_count += 1
            # Пытаемся получить размер шрифта
            if run.font and run.font.size is not None:
                try:
                    # run.font.size это Length объект, у него есть свойство .pt
                    size_pt = run.font.size.pt
                    if size_pt:
                        font_sizes.append(size_pt)
                except (AttributeError, TypeError):
                    pass
        
        if total_runs > 0:
            bold_ratio = bold_count / total_runs
            if bold_ratio >= 0.8:  # Почти весь текст bold
                score += 0.25
        
        # Проверяем alignment
        if paragraph.alignment is not None:
            # 1 = center в python-docx
            if paragraph.alignment == 1:  # CENTER
                score += 0.20
        
        # Проверяем font size
        if font_sizes and self._doc_stats.median_font_size:
            max_font_size = max(font_sizes)
            if max_font_size > self._doc_stats.median_font_size + 2:
                score += 0.25
        
        # Длина строки
        if len(text) <= 80:
            score += 0.10
        elif len(text) > 160:
            score -= 0.30
        
        # Заканчивается точкой
        if text.endswith('.'):
            score -= 0.30
        
        # Проверяем порог
        if score < 0.6:
            return None
        
        # Определяем уровень
        # Если начинается с нумерации, используем её
        numbering_hit = self.detect_by_numbering(paragraph)
        if numbering_hit and numbering_hit.is_heading:
            level = numbering_hit.level
        else:
            level = 1  # MVP: по умолчанию level 1
        
        # Clamp confidence
        confidence = min(max(score, 0.0), 1.0)
        
        return HeadingHit(
            is_heading=True,
            level=level,
            confidence=confidence,
            mode="visual",
            normalized_title=normalize_title(text),
        )
    
    @staticmethod
    def compute_doc_stats(paragraphs: list[Paragraph]) -> DocStats:
        """
        Вычисляет статистику документа для visual fallback.
        
        Args:
            paragraphs: Список всех параграфов документа
            
        Returns:
            DocStats со статистикой
        """
        font_sizes = []
        total_bold_runs = 0
        total_runs = 0
        
        for paragraph in paragraphs:
            for run in paragraph.runs:
                total_runs += 1
                if run.bold:
                    total_bold_runs += 1
                if run.font and run.font.size is not None:
                    try:
                        # run.font.size это Length объект, у него есть свойство .pt
                        size_pt = run.font.size.pt
                        if size_pt:
                            font_sizes.append(size_pt)
                    except (AttributeError, TypeError):
                        pass
        
        median_font_size = statistics.median(font_sizes) if font_sizes else None
        bold_ratio = total_bold_runs / total_runs if total_runs > 0 else 0.0
        
        return DocStats(
            median_font_size=median_font_size,
            bold_ratio=bold_ratio,
            total_paragraphs=len(paragraphs),
        )

