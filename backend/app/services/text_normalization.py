"""Утилиты для нормализации текста для матчинга."""
from __future__ import annotations

import re
import unicodedata


def normalize_for_match(text: str) -> str:
    """
    Нормализует текст для матчинга keywords.
    
    Применяет:
    - casefold() для приведения к нижнему регистру
    - Замену ё -> е
    - Удаление лишних пробелов
    - Замену тире/кавычек на пробелы
    - Минимальная очистка пунктуации вокруг слов
    
    Args:
        text: Исходный текст
        
    Returns:
        Нормализованный текст для матчинга
    """
    if not text:
        return ""
    
    # Приводим к нижнему регистру
    normalized = text.casefold()
    
    # Заменяем ё -> е (для русского)
    normalized = normalized.replace("ё", "е")
    normalized = normalized.replace("Ё", "е")
    
    # Заменяем различные тире и кавычки на пробелы
    # Unicode categories для тире и кавычек
    dash_chars = ['-', '–', '—', '―', '\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2015']
    quote_chars = ['"', '"', ''', ''', '«', '»', '„', '‚', '‹', '›']
    
    for char in dash_chars + quote_chars:
        normalized = normalized.replace(char, ' ')
    
    # Удаляем лишние пробелы (множественные пробелы -> один)
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Убираем пробелы в начале и конце
    normalized = normalized.strip()
    
    return normalized


def normalize_for_regex(text: str) -> str:
    """
    Нормализует текст для regex матчинга (менее агрессивная нормализация).
    
    Сохраняет пунктуацию, но:
    - Приводит к нижнему регистру
    - Заменяет ё -> е
    
    Args:
        text: Исходный текст
        
    Returns:
        Нормализованный текст для regex
    """
    if not text:
        return ""
    
    normalized = text.casefold()
    normalized = normalized.replace("ё", "е")
    normalized = normalized.replace("Ё", "е")
    
    return normalized.strip()

