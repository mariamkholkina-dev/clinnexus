"""Модуль для вычисления pipeline_config_hash на основе конфигурации пайплайна.

pipeline_config_hash используется для отслеживания изменений конфигурации между запусками ингестии.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.core.logging import logger


def sha256_bytes(data: bytes) -> str:
    """Вычисляет SHA256 хеш байтов.
    
    Args:
        data: Байты для хеширования
        
    Returns:
        Hex-строка хеша (64 символа)
    """
    return hashlib.sha256(data).hexdigest()


def sha256_file(file_path: Path) -> str:
    """Вычисляет SHA256 хеш содержимого файла.
    
    Args:
        file_path: Путь к файлу
        
    Returns:
        Hex-строка хеша (64 символа) или пустая строка, если файл не существует
    """
    if not file_path.exists():
        return ""
    try:
        with open(file_path, "rb") as f:
            content = f.read()
        return sha256_bytes(content)
    except Exception as e:
        logger.warning(f"Ошибка при чтении файла {file_path}: {e}")
        return ""


def tree_hash(
    dir_path: Path,
    include_patterns: list[str] | None = None,
) -> str:
    """Вычисляет tree hash директории.
    
    Рекурсивно обходит директорию, для каждого подходящего файла:
    - записывает относительный путь + sha256(содержимое)
    - сортирует записи по относительному пути
    - конкатенирует "relpath:hash" строки
    - возвращает sha256 от конкатенации
    
    Args:
        dir_path: Путь к директории
        include_patterns: Список паттернов для включения (например, ['*.json', '*.yaml'])
                         По умолчанию: ['*.json', '*.yaml', '*.yml', '*.txt', '*.py']
        
    Returns:
        Hex-строка хеша (64 символа) или пустая строка, если директория не существует
    """
    if include_patterns is None:
        include_patterns = ["*.json", "*.yaml", "*.yml", "*.txt", "*.py"]
    
    if not dir_path.exists() or not dir_path.is_dir():
        return ""
    
    try:
        import fnmatch
        
        entries: list[tuple[str, str]] = []  # [(relpath, hash), ...]
        
        # Рекурсивно обходим директорию
        for root, dirs, files in os.walk(dir_path):
            # Пропускаем скрытые директории (начинающиеся с .)
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            
            root_path = Path(root)
            for file in files:
                file_path = root_path / file
                # Проверяем паттерн
                matches = any(fnmatch.fnmatch(file, pattern) for pattern in include_patterns)
                if matches:
                    relpath = file_path.relative_to(dir_path).as_posix()  # Используем / как разделитель
                    file_hash = sha256_file(file_path)
                    entries.append((relpath, file_hash))
        
        # Сортируем по относительному пути
        entries.sort(key=lambda x: x[0])
        
        # Конкатенируем "relpath:hash\n"
        content = "\n".join(f"{relpath}:{file_hash}" for relpath, file_hash in entries)
        
        if not content:
            return ""
        
        # Возвращаем SHA256 от конкатенации
        return sha256_bytes(content.encode("utf-8"))
    except Exception as e:
        logger.warning(f"Ошибка при вычислении tree hash для {dir_path}: {e}")
        return ""


def _find_repo_root() -> Path | None:
    """Находит корень репозитория (папку с .git или pyproject.toml).
    
    Ищет вверх от текущего файла до тех пор, пока не найдёт .git или pyproject.toml.
    Также проверяет переменную окружения PROJECT_ROOT.
    
    Returns:
        Path к корню репозитория или None, если не найден
    """
    # Проверяем переменную окружения
    project_root_env = os.environ.get("PROJECT_ROOT")
    if project_root_env:
        root_path = Path(project_root_env).resolve()
        if root_path.exists():
            return root_path
    
    # Ищем .git или pyproject.toml, начиная с текущего файла
    current = Path(__file__).resolve()
    for parent in [current.parent] + list(current.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
        # Проверяем, дошли ли до корня файловой системы
        if parent == parent.parent:
            break
    
    # Если не нашли, используем backend/ как корень (4 уровня вверх от app/utils/config_hash.py)
    backend_root = current.parent.parent.parent
    if backend_root.name == "backend" and (backend_root.parent / "pyproject.toml").exists():
        return backend_root.parent
    
    return None


def _get_component_version(module_path: str, version_attr: str = "VERSION") -> str:
    """Получает версию компонента из модуля.
    
    Args:
        module_path: Путь к модулю (например, 'app.services.ingestion.docx_ingestor')
        version_attr: Имя атрибута версии (по умолчанию 'VERSION')
        
    Returns:
        Версия компонента или пустая строка, если не найдена
    """
    try:
        module = __import__(module_path, fromlist=[version_attr])
        version = getattr(module, version_attr, None)
        if version is not None:
            return str(version)
    except Exception as e:
        logger.debug(f"Не удалось получить версию из {module_path}: {e}")
    return ""


def get_pipeline_config_hash() -> str:
    """Вычисляет pipeline_config_hash для текущей конфигурации пайплайна.
    
    Включает:
    - source_zone_rules.yaml
    - seed/contracts/ (tree hash)
    - seed/taxonomy/ (tree hash) 
    - app/services/facts/rules/ (tree hash правил извлечения фактов)
    - Версии компонентов (SoAExtractor, Chunker, DocxIngestor, SectionMappingService, FactExtractor)
    
    Returns:
        Hex-строка SHA256 хеша конфигурации (64 символа)
    """
    repo_root = _find_repo_root()
    
    # Словарь для канонического JSON
    config_dict: dict[str, Any] = {}
    
    # 1. source_zone_rules.yaml
    if repo_root:
        source_zone_rules_path = repo_root / "backend" / "app" / "data" / "source_zone_rules.yaml"
        if not source_zone_rules_path.exists():
            # Пробуем альтернативный путь (относительно backend)
            alt_path = Path(__file__).parent.parent / "data" / "source_zone_rules.yaml"
            if alt_path.exists():
                source_zone_rules_path = alt_path
    else:
        source_zone_rules_path = Path(__file__).parent.parent / "data" / "source_zone_rules.yaml"
    
    config_dict["source_zone_rules"] = sha256_file(source_zone_rules_path)
    
    # 2. seed/contracts/ (tree hash)
    if repo_root:
        contracts_seed_path = repo_root / "contracts" / "seed"
    else:
        # Пробуем найти contracts/seed относительно backend
        backend_root = Path(__file__).parent.parent.parent
        contracts_seed_path = backend_root.parent / "contracts" / "seed"
    
    config_dict["section_contracts_seed"] = tree_hash(contracts_seed_path)
    
    # 3. Taxonomy seed удален (миграция 0020)
    # Структура документов определяется через templates и target_section_contracts
    
    # 4. fact_rules (tree hash правил извлечения фактов)
    # Правила находятся в app/services/fact_extraction_rules.py
    # В будущем может быть директория app/services/facts/rules/
    if repo_root:
        fact_rules_dir = repo_root / "backend" / "app" / "services" / "facts" / "rules"
        if fact_rules_dir.exists() and fact_rules_dir.is_dir():
            config_dict["fact_rules"] = tree_hash(fact_rules_dir)
        else:
            # Используем файл fact_extraction_rules.py
            fact_rules_path = repo_root / "backend" / "app" / "services" / "fact_extraction_rules.py"
            config_dict["fact_rules"] = sha256_file(fact_rules_path)
    else:
        fact_rules_path = Path(__file__).parent.parent / "services" / "fact_extraction_rules.py"
        config_dict["fact_rules"] = sha256_file(fact_rules_path)
    
    # 5. Версии компонентов
    # SoAExtractor (SoAExtractionService)
    soa_version = _get_component_version("app.services.soa_extraction", "VERSION")
    config_dict["soa_extractor_version"] = soa_version if soa_version else ""
    
    # Chunker (ChunkingService)
    chunker_version = _get_component_version("app.services.chunking", "VERSION")
    config_dict["chunker_version"] = chunker_version if chunker_version else ""
    
    # DocxIngestor
    ingestor_version = _get_component_version("app.services.ingestion.docx_ingestor", "VERSION")
    config_dict["ingestor_version"] = ingestor_version if ingestor_version else ""
    
    # SectionMappingService
    mapping_version = _get_component_version("app.services.section_mapping", "VERSION")
    config_dict["mapping_version"] = mapping_version if mapping_version else ""
    
    # FactExtractor (EXTRACTOR_VERSION из fact_extraction_rules)
    fact_extractor_version = _get_component_version("app.services.fact_extraction_rules", "EXTRACTOR_VERSION")
    if not fact_extractor_version:
        # Пробуем альтернативное имя
        fact_extractor_version = _get_component_version("app.services.fact_extraction_rules", "VERSION")
    config_dict["fact_extractor_version"] = fact_extractor_version if fact_extractor_version else ""
    
    # Создаём канонический JSON (sorted keys, no whitespace variance)
    canonical_json = json.dumps(config_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    
    # Вычисляем SHA256
    return sha256_bytes(canonical_json.encode("utf-8"))


__all__ = [
    "sha256_bytes",
    "sha256_file",
    "tree_hash",
    "get_pipeline_config_hash",
]

