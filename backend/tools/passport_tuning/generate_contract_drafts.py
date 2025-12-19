"""
Утилита для генерации черновиков section_contract из кластеров заголовков.

Читает clusters.json и cluster_to_section_key.json, генерирует contracts_seed.json
в формате, совместимом со схемой БД.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Добавляем путь к backend для импорта модулей приложения
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.db.enums import CitationPolicy, DocumentType


def sanitize_regex(pattern: str) -> str | None:
    """Санитизирует и валидирует regex паттерн.
    
    Args:
        pattern: Исходный regex паттерн
        
    Returns:
        Санитизированный паттерн или None, если невалидный
    """
    if not pattern or not isinstance(pattern, str):
        return None
    
    pattern = pattern.strip()
    if not pattern:
        return None
    
    # Экранируем специальные символы, которые могут быть проблематичными
    # Но сохраняем базовые regex конструкции
    try:
        # Пробуем скомпилировать для проверки валидности
        re.compile(pattern)
        return pattern
    except re.error:
        # Если невалидный, пытаемся экранировать как литерал
        try:
            escaped = re.escape(pattern)
            re.compile(escaped)
            return escaped
        except re.error:
            return None


def extract_keywords_from_titles(titles: list[str], max_count: int = 8) -> list[str]:
    """Извлекает ключевые слова из заголовков.
    
    Args:
        titles: Список заголовков
        max_count: Максимальное количество ключевых слов
        
    Returns:
        Список уникальных ключевых слов (нормализованных)
    """
    keywords: list[str] = []
    seen: set[str] = set()
    
    for title in titles:
        if not title:
            continue
        # Разбиваем на слова (удаляем пунктуацию, приводим к нижнему регистру)
        words = re.findall(r'\b\w{3,}\b', title.lower())
        for word in words:
            if word not in seen and len(word) >= 3:
                keywords.append(word)
                seen.add(word)
                if len(keywords) >= max_count:
                    break
        if len(keywords) >= max_count:
            break
    
    return keywords[:max_count]


def build_mapping_signals(
    cluster: dict[str, Any],
) -> dict[str, Any]:
    """Строит mapping.signals из кластера.
    
    Args:
        cluster: Данные кластера
        
    Returns:
        Словарь с signals для ru и en
    """
    signals: dict[str, Any] = {"lang": {}}
    
    # Извлекаем ключевые слова из заголовков
    titles_ru = cluster.get("top_titles_ru", [])
    titles_en = cluster.get("top_titles_en", [])
    
    # Must keywords: первые 2-4 наиболее частых слова
    must_ru = extract_keywords_from_titles(titles_ru[:5], max_count=4)
    must_en = extract_keywords_from_titles(titles_en[:5], max_count=4)
    
    # Should keywords: остальные слова
    should_ru = extract_keywords_from_titles(titles_ru[5:15], max_count=8)
    should_en = extract_keywords_from_titles(titles_en[5:15], max_count=8)
    
    # Not keywords: пусто по умолчанию (можно добавить анти-паттерны)
    not_ru: list[str] = []
    not_en: list[str] = []
    
    # Regex: извлекаем паттерны из заголовков (например, нумерация)
    regex_ru: list[str] = []
    regex_en: list[str] = []
    
    # Ищем паттерны нумерации в заголовках
    for title in titles_ru[:3]:
        # Паттерн типа "1.2.3 Заголовок"
        match = re.search(r'^\s*(\d+(?:\.\d+)*)', title)
        if match:
            pattern = f"^{re.escape(match.group(1))}"
            sanitized = sanitize_regex(pattern)
            if sanitized and sanitized not in regex_ru:
                regex_ru.append(sanitized)
    
    for title in titles_en[:3]:
        match = re.search(r'^\s*(\d+(?:\.\d+)*)', title)
        if match:
            pattern = f"^{re.escape(match.group(1))}"
            sanitized = sanitize_regex(pattern)
            if sanitized and sanitized not in regex_en:
                regex_en.append(sanitized)
    
    signals["lang"]["ru"] = {
        "must": must_ru,
        "should": should_ru,
        "not": not_ru,
        "regex": regex_ru,
    }
    
    signals["lang"]["en"] = {
        "must": must_en,
        "should": should_en,
        "not": not_en,
        "regex": regex_en,
    }
    
    return signals


def build_heading_levels(
    heading_level_histogram: dict[str, int],
) -> dict[str, int]:
    """Строит heading_levels из гистограммы.
    
    Args:
        heading_level_histogram: Гистограмма уровней заголовков
        
    Returns:
        Словарь с min_heading_level и max_heading_level
    """
    if not heading_level_histogram:
        return {"min_heading_level": 1, "max_heading_level": 3}
    
    levels = [int(k) for k in heading_level_histogram.keys() if k.isdigit()]
    if not levels:
        return {"min_heading_level": 1, "max_heading_level": 3}
    
    min_level = min(levels)
    max_level = max(levels)
    
    # Максимум минимум 3, если есть evidence (непустая гистограмма)
    if max_level < 3:
        max_level = 3
    
    # Clamp к допустимому диапазону
    min_level = max(1, min(9, min_level))
    max_level = max(1, min(9, max_level))
    
    if min_level > max_level:
        min_level, max_level = 1, 3
    
    return {
        "min_heading_level": min_level,
        "max_heading_level": max_level,
    }


def build_context_build(
    content_type_distribution: dict[str, int],
    avg_total_chars: float,
) -> dict[str, Any]:
    """Строит context_build из статистики кластера.
    
    Args:
        content_type_distribution: Распределение типов контента
        avg_total_chars: Среднее количество символов
        
    Returns:
        Словарь с context_build
    """
    # Вычисляем max_chars на основе avg_total_chars (с запасом)
    max_chars = int(avg_total_chars * 1.5) if avg_total_chars > 0 else 2000
    max_chars = max(1000, min(10000, max_chars))  # Clamp между 1000 и 10000
    
    # Определяем prefer_content_types на основе distribution
    prefer_content_types: list[str] = []
    
    if not content_type_distribution:
        return {
            "max_chars": max_chars,
            "prefer_content_types": None,
        }
    
    # Сортируем по частоте
    sorted_types = sorted(
        content_type_distribution.items(),
        key=lambda x: x[1],
        reverse=True,
    )
    
    # Если много li → prefer li
    if sorted_types[0][0] == "li" and sorted_types[0][1] >= 10:
        prefer_content_types.append("li")
    
    # Если много cell → prefer cell
    if sorted_types[0][0] == "cell" and sorted_types[0][1] >= 5:
        prefer_content_types.append("cell")
    
    # Если много p → prefer p
    if sorted_types[0][0] == "p" and sorted_types[0][1] >= 10:
        prefer_content_types.append("p")
    
    return {
        "max_chars": max_chars,
        "prefer_content_types": prefer_content_types if prefer_content_types else None,
    }


def build_fallback_search(
    cluster: dict[str, Any],
) -> dict[str, Any]:
    """Строит fallback_search с query_templates.
    
    Args:
        cluster: Данные кластера
        
    Returns:
        Словарь с fallback_search
    """
    titles_ru = cluster.get("top_titles_ru", [])
    titles_en = cluster.get("top_titles_en", [])
    
    # RU-first: берём первые заголовки на русском
    query_templates_ru = titles_ru[:5] if titles_ru else []
    
    # Добавляем синонимы (можно расширить)
    query_templates_en = titles_en[:5] if titles_en else []
    
    query_templates: dict[str, list[str]] = {}
    if query_templates_ru:
        query_templates["ru"] = query_templates_ru
    if query_templates_en:
        query_templates["en"] = query_templates_en
    
    return {
        "query_templates": query_templates if query_templates else None,
    }


def build_retrieval_recipe(
    cluster: dict[str, Any],
) -> dict[str, Any]:
    """Строит retrieval_recipe_json из кластера.
    
    Args:
        cluster: Данные кластера
        
    Returns:
        Словарь с retrieval_recipe_json
    """
    stats = cluster.get("stats", {})
    heading_level_histogram = stats.get("heading_level_histogram", {})
    content_type_distribution = stats.get("content_type_distribution", {})
    avg_total_chars = stats.get("avg_total_chars", 0.0)
    
    # Mapping signals
    mapping_signals = build_mapping_signals(cluster)
    heading_levels = build_heading_levels(heading_level_histogram)
    
    mapping = {
        "signals": mapping_signals,
        **heading_levels,
    }
    
    # Context build
    context_build = build_context_build(content_type_distribution, avg_total_chars)
    
    # Prefer content types (извлекаем из context_build)
    prefer_content_types = context_build.pop("prefer_content_types", None)
    
    # Fallback search
    fallback_search = build_fallback_search(cluster)
    
    recipe = {
        "version": 2,
        "language": {
            "mode": "auto",
        },
        "mapping": mapping,
        "context_build": context_build,
        "fallback_search": fallback_search,
        "security": {
            "secure_mode_required": True,
        },
    }
    
    # Добавляем prefer_content_types в корень, если есть
    if prefer_content_types:
        recipe["prefer_content_types"] = prefer_content_types
    
    return recipe


def build_qc_ruleset(
    content_type_distribution: dict[str, int],
) -> dict[str, Any]:
    """Строит qc_ruleset_json из статистики кластера.
    
    Args:
        content_type_distribution: Распределение типов контента
        
    Returns:
        Словарь с qc_ruleset_json
    """
    total_items = sum(content_type_distribution.values())
    
    # Вычисляем доли
    li_ratio = content_type_distribution.get("li", 0) / total_items if total_items > 0 else 0
    cell_ratio = content_type_distribution.get("cell", 0) / total_items if total_items > 0 else 0
    
    # Правила QC
    warnings: list[dict[str, Any]] = []
    
    # Если доля li высокая → prefer_list_items
    prefer_list_items = li_ratio >= 0.3
    
    # Если доля cell высокая → require_cell_anchors
    require_cell_anchors = cell_ratio >= 0.2
    
    if prefer_list_items:
        warnings.append({
            "type": "prefer_list_items",
            "message": "Секция содержит много элементов списка",
        })
    
    if require_cell_anchors:
        warnings.append({
            "type": "require_cell_anchors",
            "message": "Секция содержит таблицы, требуются cell anchors",
        })
    
    qc_ruleset = {
        "phases": ["input_qc", "citation_qc"],
        "gate_policy": {
            "on_missing_required_fact": "blocked",
            "on_low_mapping_confidence": "blocked",
            "on_citation_missing": "fail",
        },
        "warnings": warnings,
        "numbers_match_facts": False,
    }
    
    # Добавляем правила как прямые поля
    if prefer_list_items:
        qc_ruleset["prefer_list_items"] = True
    
    if require_cell_anchors:
        qc_ruleset["require_cell_anchors"] = True
    
    return qc_ruleset


def build_allowed_sources(doc_type: DocumentType) -> dict[str, Any]:
    """Строит allowed_sources_json в зависимости от типа документа.
    
    Args:
        doc_type: Тип документа
        
    Returns:
        Словарь с allowed_sources_json
    """
    if doc_type == DocumentType.PROTOCOL:
        return {
            "dependency_sources": [
                {
                    "doc_type": doc_type.value,
                    "section_keys": [],
                    "required": True,
                    "role": "primary",
                    "precedence": 0,
                    "min_mapping_confidence": 0.0,
                    "allowed_content_types": [],
                },
            ],
            "document_scope": {
                "same_study_only": True,
                "allow_superseded": False,
            },
        }
    elif doc_type == DocumentType.CSR:
        # Дефолты для CSR по аналогии
        return {
            "dependency_sources": [
                {
                    "doc_type": doc_type.value,
                    "section_keys": [],
                    "required": True,
                    "role": "primary",
                    "precedence": 0,
                    "min_mapping_confidence": 0.0,
                    "allowed_content_types": [],
                },
            ],
            "document_scope": {
                "same_study_only": True,
                "allow_superseded": False,
            },
        }
    else:
        # Дефолты для других типов
        return {
            "dependency_sources": [],
            "document_scope": {
                "same_study_only": True,
                "allow_superseded": False,
            },
        }


def generate_contract(
    cluster: dict[str, Any],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    """Генерирует один контракт из кластера и mapping.
    
    Args:
        cluster: Данные кластера
        mapping: Соответствие cluster_id -> section_key
        
    Returns:
        Словарь с контрактом
    """
    cluster_id = cluster.get("cluster_id")
    if cluster_id not in mapping:
        return None
    
    mapping_info = mapping[cluster_id]
    doc_type_str = mapping_info.get("doc_type", "protocol")
    section_key = mapping_info.get("section_key")
    title_ru = mapping_info.get("title_ru", "")
    
    if not section_key:
        return None
    
    try:
        doc_type = DocumentType(doc_type_str)
    except ValueError:
        print(f"Предупреждение: неверный doc_type '{doc_type_str}', используется protocol", file=sys.stderr)
        doc_type = DocumentType.PROTOCOL
    
    # Базовые поля
    contract = {
        "doc_type": doc_type.value,
        "section_key": section_key,
        "title": title_ru,
        "required_facts_json": {
            "facts": [],
        },
        "allowed_sources_json": build_allowed_sources(doc_type),
        "retrieval_recipe_json": build_retrieval_recipe(cluster),
        "qc_ruleset_json": build_qc_ruleset(cluster.get("stats", {}).get("content_type_distribution", {})),
        "citation_policy": CitationPolicy.PER_CLAIM.value,
    }
    
    return contract


def load_clusters(clusters_path: Path) -> list[dict[str, Any]]:
    """Загружает clusters.json.
    
    Args:
        clusters_path: Путь к clusters.json
        
    Returns:
        Список кластеров
    """
    with open(clusters_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_mapping(mapping_path: Path) -> dict[str, Any]:
    """Загружает cluster_to_section_key.json.
    
    Args:
        mapping_path: Путь к cluster_to_section_key.json
        
    Returns:
        Словарь соответствий cluster_id -> mapping_info
    """
    with open(mapping_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Преобразуем в словарь, если это список
    if isinstance(data, list):
        mapping = {}
        for item in data:
            cluster_id = item.get("cluster_id")
            if cluster_id is not None:
                mapping[cluster_id] = item
        return mapping
    
    return data if isinstance(data, dict) else {}


def validate_json(obj: Any) -> bool:
    """Валидирует, что объект может быть сериализован в валидный JSON.
    
    Args:
        obj: Объект для валидации
        
    Returns:
        True, если валидный
    """
    try:
        json_str = json.dumps(obj, ensure_ascii=False)
        json.loads(json_str)  # Проверяем, что можно распарсить обратно
        return True
    except (TypeError, ValueError):
        return False


def main() -> None:
    """Главная функция CLI."""
    parser = argparse.ArgumentParser(
        description="Генерация черновиков section_contract из кластеров"
    )
    parser.add_argument(
        "--clusters",
        type=str,
        default="clusters.json",
        help="Путь к clusters.json (по умолчанию: clusters.json)",
    )
    parser.add_argument(
        "--mapping",
        type=str,
        default="cluster_to_section_key.json",
        help="Путь к cluster_to_section_key.json (по умолчанию: cluster_to_section_key.json)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="drafts/contracts_seed.json",
        help="Путь к выходному файлу (по умолчанию: drafts/contracts_seed.json)",
    )
    
    args = parser.parse_args()
    
    clusters_path = Path(args.clusters)
    mapping_path = Path(args.mapping)
    output_path = Path(args.out)
    
    if not clusters_path.exists():
        print(f"Ошибка: файл {clusters_path} не найден", file=sys.stderr)
        sys.exit(1)
    
    if not mapping_path.exists():
        print(f"Ошибка: файл {mapping_path} не найден", file=sys.stderr)
        sys.exit(1)
    
    # Загружаем данные
    print(f"Загрузка кластеров из {clusters_path}...", file=sys.stderr)
    clusters = load_clusters(clusters_path)
    print(f"Загружено {len(clusters)} кластеров", file=sys.stderr)
    
    print(f"Загрузка mapping из {mapping_path}...", file=sys.stderr)
    mapping = load_mapping(mapping_path)
    print(f"Загружено {len(mapping)} соответствий", file=sys.stderr)
    
    # Генерируем контракты
    print("Генерация контрактов...", file=sys.stderr)
    contracts = []
    
    for cluster in clusters:
        contract = generate_contract(cluster, mapping)
        if contract:
            # Валидируем JSON
            if not validate_json(contract):
                print(f"Предупреждение: контракт для cluster_id={cluster.get('cluster_id')} содержит невалидный JSON", file=sys.stderr)
                continue
            contracts.append(contract)
    
    # Сортируем по doc_type, затем по section_key
    contracts.sort(key=lambda c: (c["doc_type"], c["section_key"]))
    
    print(f"Сгенерировано {len(contracts)} контрактов", file=sys.stderr)
    
    # Сохраняем результат
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(contracts, f, ensure_ascii=False, indent=2)
    
    print(f"Результат сохранён в {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

