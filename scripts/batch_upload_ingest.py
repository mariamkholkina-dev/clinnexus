#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Скрипт для массовой загрузки и ингестии документов.

Принимает на вход:
  - путь к одному файлу (.docx, .pdf, .xlsx)
  - путь к папке с файлами

Примечание: API поддерживает только .docx, .pdf, .xlsx. 
Файлы с расширением .doc (старый формат Word) будут пропущены с предупреждением.

Для каждого файла:
  1. Создаёт study/document/version (если нужно)
  2. Загружает файл через upload API
  3. Запускает процесс ингестии
  4. Ожидает завершения ингестии

В конце выводит статистику по обработанным файлам.

Опция --resume:
  Позволяет пропускать уже обработанные файлы (проверка по SHA256 в базе данных)
  и начинать обработку с первого необработанного файла. Полезно при прерывании
  массовой обработки - можно продолжить с места остановки.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import requests
except ImportError:
    print("ОШИБКА: Не установлена библиотека requests. Установите: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ALLOWED_EXTENSIONS = {".docx", ".pdf", ".xlsx"}


class HTTPError(Exception):
    """Исключение для HTTP ошибок."""
    pass


def die(msg: str, code: int = 1) -> None:
    """Завершает выполнение с ошибкой."""
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_dir(path: Path) -> None:
    """Создаёт директорию, если она отсутствует."""
    path.mkdir(parents=True, exist_ok=True)


def http_json(method: str, url: str, *, json_body: Any = None, params: Dict[str, Any] | None = None, timeout: int = 30, raise_on_error: bool = True) -> Any:
    """Выполняет HTTP запрос и возвращает JSON ответ."""
    headers = {"Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    r = requests.request(method, url, headers=headers, json=json_body, params=params, timeout=timeout)
    if r.status_code >= 400:
        try:
            body = r.json()
            error_msg = json.dumps(body, indent=2, ensure_ascii=False)
        except Exception:
            error_msg = r.text
        error_text = f"{method} {url} -> {r.status_code}\n{error_msg}"
        if raise_on_error:
            die(error_text)
        else:
            raise HTTPError(error_text)
    if r.text.strip() == "":
        return None
    return r.json()


def upload_file(api_base: str, version_id: str, file_path: Path) -> Dict[str, Any]:
    """Загружает файл для версии документа."""
    url = f"{api_base}/api/document-versions/{version_id}/upload"
    
    # Определяем content-type по расширению
    ext = file_path.suffix.lower()
    content_types = {
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    content_type = content_types.get(ext, "application/octet-stream")
    
    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, content_type)}
        r = requests.post(url, files=files, headers={"Accept": "application/json"}, timeout=300)
    
    if r.status_code >= 400:
        die(f"Ошибка загрузки файла {r.status_code}: {r.text}")
    
    return r.json()


def get_version_status(api_base: str, version_id: str) -> Dict[str, Any]:
    """Получает текущий статус версии документа."""
    url = f"{api_base}/api/document-versions/{version_id}"
    return http_json("GET", url, timeout=30)


def start_ingestion(api_base: str, version_id: str, force: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Запускает процесс ингестии для версии документа.
    
    Возвращает:
        (was_started, current_status) - была ли ингестия запущена и текущий статус
        Если ингестия уже выполняется, возвращает (False, "processing")
    """
    url = f"{api_base}/api/document-versions/{version_id}/ingest"
    if force:
        url += "?force=true"
    
    try:
        # Некоторые серверы требуют пустое тело или игнорируют его
        r = requests.post(url, json={}, headers={"Accept": "application/json"}, timeout=120)
    except Exception:
        # Повторная попытка без json
        r = requests.post(url, headers={"Accept": "application/json"}, timeout=120)
    
    if r.status_code == 409:
        # Конфликт: ингестия уже выполняется
        try:
            error_body = r.json()
            current_status = error_body.get("details", {}).get("current_status", "processing")
            return False, current_status
        except Exception:
            return False, "processing"
    
    if r.status_code >= 400:
        die(f"Ошибка запуска ингестии {r.status_code}: {r.text}")
    
    return True, None


def poll_ingestion_status(
    api_base: str, version_id: str, timeout_sec: int = 600
) -> Tuple[str, Dict[str, Any]]:
    """
    Ожидает завершения ингестии, периодически проверяя статус.
    
    Возвращает:
        (final_status, version_data)
    """
    deadline = time.time() + timeout_sec
    url = f"{api_base}/api/document-versions/{version_id}"
    
    last_status = None
    while True:
        try:
            v = http_json("GET", url, timeout=30)
            status = v.get("ingestion_status")
            
            # Выводим статус, если он изменился
            if status != last_status:
                print(f"    Статус ингестии: {status}")
                last_status = status
            
            # Проверяем финальные статусы
            if status in ("ready", "needs_review", "failed"):
                return status, v
            
            # Проверяем таймаут
            if time.time() > deadline:
                die(f"Таймаут ожидания завершения ингестии (>{timeout_sec} сек)")
            
            time.sleep(2)  # Проверяем статус каждые 2 секунды
            
        except KeyboardInterrupt:
            print("\nПрервано пользователем")
            sys.exit(1)
        except Exception as e:
            print(f"    Предупреждение при проверке статуса: {e}")
            time.sleep(2)


def create_study(api_base: str, workspace_id: str, study_code: str, title: str, *, raise_on_error: bool = True) -> str:
    """Создаёт новое исследование и возвращает study_id."""
    url = f"{api_base}/api/studies"
    body = {
        "workspace_id": workspace_id,
        "study_code": study_code,
        "title": title,
        "status": "active",
    }
    study = http_json("POST", url, json_body=body, timeout=30, raise_on_error=raise_on_error)
    return study["id"]


def create_document(api_base: str, study_id: str, doc_type: str, title: str) -> str:
    """Создаёт новый документ и возвращает document_id."""
    url = f"{api_base}/api/studies/{study_id}/documents"
    body = {
        "doc_type": doc_type,
        "title": title,
        "lifecycle_status": "draft",
    }
    doc = http_json("POST", url, json_body=body, timeout=30)
    return doc["id"]


def find_study_id_by_code(api_base: str, workspace_id: str, study_code: str) -> Optional[str]:
    """Возвращает id исследования по коду, если найдено."""
    try:
        studies = http_json(
            "GET",
            f"{api_base}/api/studies",
            params={"workspace_id": workspace_id},
            timeout=60,
            raise_on_error=False,
        )
        if isinstance(studies, list):
            for study in studies:
                if study.get("study_code") == study_code:
                    return study.get("id")
    except Exception as e:
        print(f"    [WARN] Не удалось получить исследования для поиска {study_code}: {e}")
    return None


def ensure_study(api_base: str, workspace_id: str, study_code: str, title: str) -> str:
    """Возвращает id исследования, создаёт при отсутствии."""
    existing = find_study_id_by_code(api_base, workspace_id, study_code)
    if existing:
        return existing
    try:
        return create_study(api_base, workspace_id, study_code, title, raise_on_error=True)
    except HTTPError as e:
        # На случай гонки: пробуем перечитать
        print(f"    [WARN] Создание исследования {study_code} вернуло ошибку: {e}. Пытаемся найти повторно.")
        retry = find_study_id_by_code(api_base, workspace_id, study_code)
        if retry:
            return retry
        raise


def find_protocol_document(api_base: str, study_id: str) -> Optional[str]:
    """Возвращает id протокольного документа, если найден."""
    try:
        documents = http_json("GET", f"{api_base}/api/studies/{study_id}/documents", timeout=60, raise_on_error=False)
        if isinstance(documents, list):
            for doc in documents:
                if doc.get("doc_type") == "protocol":
                    return doc.get("id")
    except Exception as e:
        print(f"    [WARN] Не удалось получить документы исследования {study_id}: {e}")
    return None


def ensure_protocol_document(api_base: str, study_id: str, title: str) -> str:
    """Возвращает id документа-протокола, создаёт при отсутствии."""
    existing = find_protocol_document(api_base, study_id)
    if existing:
        return existing
    return create_document(api_base, study_id, "protocol", title)


def download_json_to_file(url: str, target_path: Path) -> None:
    """Скачивает JSON и сохраняет в файл."""
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=120)
        if r.status_code >= 400:
            print(f"    [WARN] Не удалось скачать {url}: {r.status_code} {r.text}")
            return
        data = r.json()
        ensure_dir(target_path.parent)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"    [WARN] Ошибка загрузки {url}: {e}")


def save_benchmark_artifacts(
    api_base: str,
    project_root: Path,
    study_code: str,
    version_number: int,
    study_id: str,
    version_id: str,
) -> None:
    """Сохраняет артефакты фактов и SoA после успешной ингестии."""
    target_dir = project_root / "benchmark_results" / study_code
    ensure_dir(target_dir)
    facts_path = target_dir / f"v{version_number}_facts.json"
    soa_path = target_dir / f"v{version_number}_soa.json"
    
    download_json_to_file(f"{api_base}/api/studies/{study_id}/facts", facts_path)
    download_json_to_file(f"{api_base}/api/document-versions/{version_id}/soa", soa_path)


def create_document_version(api_base: str, document_id: str, version_label: str) -> str:
    """Создаёт новую версию документа и возвращает version_id."""
    url = f"{api_base}/api/documents/{document_id}/versions"
    body = {"version_label": version_label}
    ver = http_json("POST", url, json_body=body, timeout=30)
    return ver["id"]


def extract_detailed_stats(version_data: Dict[str, Any]) -> Dict[str, Any]:
    """Извлекает детальную статистику по шагам обработки из данных версии."""
    summary = version_data.get("ingestion_summary_json", {})
    
    # Статистика по SoA (Schedule of Activities)
    soa_stats = {
        "detected": False,
        "confidence": None,
        "visits_count": 0,
        "procedures_count": 0,
        "matrix_cells": 0,
    }
    
    # Проверяем наличие SoA фактов через ingestion_summary_json
    soa_facts = summary.get("soa_facts_written", {})
    if isinstance(soa_facts, dict):
        soa_stats["detected"] = (
            soa_facts.get("visits", False) or
            soa_facts.get("procedures", False) or
            soa_facts.get("matrix", False)
        )
        counts = soa_facts.get("counts", {})
        soa_stats["visits_count"] = counts.get("visits", 0)
        soa_stats["procedures_count"] = counts.get("procedures", 0)
        soa_stats["matrix_cells"] = counts.get("matrix", 0)
    
    # Проверяем также через метрики
    metrics = summary.get("metrics", {})
    soa_metrics = metrics.get("soa", {})
    if soa_metrics.get("found"):
        soa_stats["detected"] = True
        soa_stats["confidence"] = soa_metrics.get("table_score")
        if soa_stats["visits_count"] == 0:
            soa_stats["visits_count"] = soa_metrics.get("visits_count", 0)
        if soa_stats["procedures_count"] == 0:
            soa_stats["procedures_count"] = soa_metrics.get("procedures_count", 0)
    
    # Статистика по Chunks (Narrative Index)
    chunks_stats = {
        "created": summary.get("chunks_created", 0),
        "anchors_per_chunk_avg": None,
    }
    chunk_metrics = metrics.get("chunks", {})
    if chunk_metrics:
        chunks_stats["created"] = chunk_metrics.get("total", chunks_stats["created"])
        anchors_count = summary.get("anchors_created", 0)
        if chunks_stats["created"] > 0 and anchors_count > 0:
            chunks_stats["anchors_per_chunk_avg"] = round(anchors_count / chunks_stats["created"], 2)
    
    # Статистика по Facts (Rules-first)
    facts_stats = {
        "total_extracted": summary.get("facts_count", 0),
        "needs_review": [],
        "by_type": {},
    }
    facts_metrics = metrics.get("facts", {})
    if facts_metrics:
        facts_stats["total_extracted"] = facts_metrics.get("total", facts_stats["total_extracted"])
        facts_stats["needs_review"] = facts_metrics.get("needs_review_list", [])
        facts_stats["by_type"] = facts_metrics.get("by_type", {})
    
    # Статистика по Section Mapping
    mapping_stats = {
        "sections_mapped": summary.get("sections_mapped_count", 0),
        "needs_review": summary.get("sections_needs_review_count", 0),
        "warnings": summary.get("mapping_warnings", []),
    }
    
    # Также проверяем через docx_summary, если есть
    docx_summary = summary.get("docx_summary") or {}
    if docx_summary:
        if mapping_stats["sections_mapped"] == 0:
            mapping_stats["sections_mapped"] = docx_summary.get("sections_mapped_count", 0)
        if mapping_stats["needs_review"] == 0:
            mapping_stats["needs_review"] = docx_summary.get("sections_needs_review_count", 0)
        if not mapping_stats["warnings"]:
            mapping_stats["warnings"] = docx_summary.get("mapping_warnings", [])
    
    return {
        "soa": soa_stats,
        "chunks": chunks_stats,
        "facts": facts_stats,
        "section_mapping": mapping_stats,
    }


def calculate_file_sha256(file_path: Path) -> str:
    """Вычисляет SHA256 хеш файла."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Читаем файл блоками для экономии памяти
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_processed_sha256_set(api_base: str, workspace_id: str, debug: bool = False) -> Set[str]:
    """
    Получает множество SHA256 хешей всех обработанных файлов из базы данных.
    
    Возвращает set с SHA256 хешами (в нижнем регистре для сравнения).
    """
    processed_hashes: Set[str] = set()
    
    try:
        # Получаем все исследования в workspace
        url = f"{api_base}/api/studies"
        params = {"workspace_id": workspace_id}
        
        try:
            studies = http_json("GET", url, params=params, timeout=60, raise_on_error=False)
        except Exception as e:
            # Перехватываем HTTPError и другие исключения
            print(f"    Ошибка при получении списка исследований из API: {e}")
            print(f"    URL: {url}")
            print(f"    Параметры: {params}")
            print(f"    Продолжаем без проверки обработанных файлов")
            return processed_hashes
        
        if not isinstance(studies, list):
            print(f"    Предупреждение: неожиданный формат ответа при получении исследований")
            return processed_hashes
        
        print(f"    Найдено исследований: {len(studies)}")
        
        # Для каждого исследования получаем документы
        for study in studies:
            study_id = study.get("id")
            if not study_id:
                continue
            
            try:
                # Получаем документы исследования
                docs_url = f"{api_base}/api/studies/{study_id}/documents"
                documents = http_json("GET", docs_url, timeout=60)
                
                if not isinstance(documents, list):
                    continue
                
                # Для каждого документа получаем версии
                for doc in documents:
                    doc_id = doc.get("id")
                    if not doc_id:
                        continue
                    
                    try:
                        # Получаем версии документа
                        versions_url = f"{api_base}/api/documents/{doc_id}/versions"
                        versions = http_json("GET", versions_url, timeout=60)
                        
                        if not isinstance(versions, list):
                            continue
                        
                        # Извлекаем SHA256 из версий
                        # Пропускаем только файлы с завершенной ингестией (ready или needs_review)
                        for version in versions:
                            sha256 = version.get("source_sha256")
                            ingestion_status = version.get("ingestion_status")
                            
                            if debug:
                                version_id = version.get("id", "unknown")
                                print(f"      Версия {version_id}: SHA256={sha256[:16] if sha256 else 'None'}..., статус={ingestion_status}")
                            
                            # Пропускаем только файлы с успешно завершенной ингестией
                            # (ready или needs_review). Файлы со статусом failed или processing
                            # будут обработаны заново
                            if sha256 and ingestion_status in ("ready", "needs_review"):
                                processed_hashes.add(sha256.lower())
                                if debug:
                                    print(f"        -> Добавлен в список обработанных")
                            elif debug and sha256:
                                print(f"        -> Пропущен (статус {ingestion_status}, не ready/needs_review)")
                    
                    except Exception as e:
                        # Пропускаем ошибки при получении версий отдельного документа
                        continue
            
            except Exception as e:
                # Пропускаем ошибки при получении документов отдельного исследования
                continue
        
        print(f"    Найдено обработанных файлов (ready/needs_review): {len(processed_hashes)}")
        if debug and processed_hashes:
            print(f"    Примеры хешей из базы (первые 5):")
            for i, h in enumerate(list(processed_hashes)[:5], 1):
                print(f"      {i}. {h[:32]}...")
        return processed_hashes
    
    except Exception as e:
        print(f"    Ошибка при получении списка обработанных файлов: {e}")
        print(f"    Продолжаем без проверки обработанных файлов")
        return processed_hashes


def _warn_doc_files(files: List[Path]) -> None:
    if not files:
        return
    print(f"\nПРЕДУПРЕЖДЕНИЕ: Найдено {len(files)} файл(ов) с расширением .doc (старый формат Word):")
    for doc_file in files[:10]:
        print(f"  - {doc_file}")
    if len(files) > 10:
        print(f"  ... и ещё {len(files) - 10} файл(ов)")
    print("  API поддерживает только .docx, .pdf, .xlsx. Эти файлы будут пропущены.")
    print("  Рекомендуется конвертировать файлы в .docx перед загрузкой.\n")


def discover_study_files(path: Path) -> Dict[str, List[Path]]:
    """Строит словарь {study_code: [файлы]} c сортировкой по дате изменения."""
    studies: Dict[str, List[Path]] = {}
    
    if path.is_file():
        ext = path.suffix.lower()
        if ext == ".doc":
            _warn_doc_files([path])
            return {}
        if ext not in ALLOWED_EXTENSIONS:
            die(f"Неподдерживаемое расширение файла: {ext}. Поддерживаются: {', '.join(ALLOWED_EXTENSIONS)}")
        study_code = path.parent.name or path.stem
        studies[study_code] = [path]
        return studies
    
    if not path.is_dir():
        die(f"Путь не существует: {path}")
    
    # Основной сценарий: подкаталоги — это study_code
    for sub in sorted(path.iterdir()):
        if not sub.is_dir():
            continue
        study_code = sub.name
        study_files = [f for f in sub.rglob("*") if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS]
        doc_files = [f for f in sub.rglob("*.doc") if f.is_file()]
        
        if doc_files:
            _warn_doc_files(doc_files)
        
        if study_files:
            study_files.sort(key=os.path.getmtime)
            studies[study_code] = study_files
    
    # Фолбэк: если внутри базовой директории нет подкаталогов, но есть файлы
    if not studies:
        direct_files = [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS]
        doc_files = [f for f in path.rglob("*.doc") if f.is_file()]
        if doc_files:
            _warn_doc_files(doc_files)
        if direct_files:
            direct_files.sort(key=os.path.getmtime)
            studies[path.name] = direct_files
    
    return studies


def filter_processed_by_hash(study_files: Dict[str, List[Path]], processed_hashes: Set[str]) -> Tuple[Dict[str, List[Path]], int]:
    """Фильтрует уже обработанные файлы (ready/needs_review) по SHA256."""
    if not processed_hashes:
        return study_files, 0
    
    filtered: Dict[str, List[Path]] = {}
    skipped = 0
    
    for study_code, files in study_files.items():
        for file_path in files:
            try:
                file_hash = calculate_file_sha256(file_path)
            except Exception as e:
                print(f"  [WARN] Ошибка расчёта SHA256 для {file_path}: {e}. Файл будет обработан.")
                filtered.setdefault(study_code, []).append(file_path)
                continue
            
            if file_hash.lower() in processed_hashes:
                skipped += 1
                continue
            filtered.setdefault(study_code, []).append(file_path)
    
    filtered = {k: v for k, v in filtered.items() if v}
    return filtered, skipped


def extract_detailed_stats(version_data: Dict[str, Any]) -> Dict[str, Any]:
    """Извлекает детальную статистику по шагам обработки из данных версии."""
    summary = version_data.get("ingestion_summary_json", {})
    
    # Статистика по SoA (Schedule of Activities)
    soa_stats = {
        "detected": False,
        "confidence": None,
        "visits_count": 0,
        "procedures_count": 0,
        "matrix_cells": 0,
    }
    
    # Проверяем наличие SoA фактов через ingestion_summary_json
    soa_facts = summary.get("soa_facts_written", {})
    if isinstance(soa_facts, dict):
        soa_stats["detected"] = (
            soa_facts.get("visits", False) or
            soa_facts.get("procedures", False) or
            soa_facts.get("matrix", False)
        )
        counts = soa_facts.get("counts", {})
        soa_stats["visits_count"] = counts.get("visits", 0)
        soa_stats["procedures_count"] = counts.get("procedures", 0)
        soa_stats["matrix_cells"] = counts.get("matrix", 0)
    
    # Проверяем также через метрики
    metrics = summary.get("metrics", {})
    soa_metrics = metrics.get("soa", {})
    if soa_metrics.get("found"):
        soa_stats["detected"] = True
        soa_stats["confidence"] = soa_metrics.get("table_score")
        if soa_stats["visits_count"] == 0:
            soa_stats["visits_count"] = soa_metrics.get("visits_count", 0)
        if soa_stats["procedures_count"] == 0:
            soa_stats["procedures_count"] = soa_metrics.get("procedures_count", 0)
    
    # Статистика по Chunks (Narrative Index)
    chunks_stats = {
        "created": summary.get("chunks_created", 0),
        "anchors_per_chunk_avg": None,
    }
    chunk_metrics = metrics.get("chunks", {})
    if chunk_metrics:
        chunks_stats["created"] = chunk_metrics.get("total", chunks_stats["created"])
        anchors_count = summary.get("anchors_created", 0)
        if chunks_stats["created"] > 0 and anchors_count > 0:
            chunks_stats["anchors_per_chunk_avg"] = round(anchors_count / chunks_stats["created"], 2)
    
    # Статистика по Facts (Rules-first)
    facts_stats = {
        "total_extracted": summary.get("facts_count", 0),
        "needs_review": [],
        "by_type": {},
    }
    facts_metrics = metrics.get("facts", {})
    if facts_metrics:
        facts_stats["total_extracted"] = facts_metrics.get("total", facts_stats["total_extracted"])
        facts_stats["needs_review"] = facts_metrics.get("needs_review_list", [])
        facts_stats["by_type"] = facts_metrics.get("by_type", {})
    
    # Статистика по Section Mapping
    mapping_stats = {
        "sections_mapped": summary.get("sections_mapped_count", 0),
        "needs_review": summary.get("sections_needs_review_count", 0),
        "warnings": summary.get("mapping_warnings", []),
    }
    
    # Также проверяем через docx_summary, если есть
    docx_summary = summary.get("docx_summary") or {}
    if docx_summary:
        if mapping_stats["sections_mapped"] == 0:
            mapping_stats["sections_mapped"] = docx_summary.get("sections_mapped_count", 0)
        if mapping_stats["needs_review"] == 0:
            mapping_stats["needs_review"] = docx_summary.get("sections_needs_review_count", 0)
        if not mapping_stats["warnings"]:
            mapping_stats["warnings"] = docx_summary.get("mapping_warnings", [])
    
    return {
        "soa": soa_stats,
        "chunks": chunks_stats,
        "facts": facts_stats,
        "section_mapping": mapping_stats,
    }


def process_file(
    api_base: str,
    workspace_id: str,
    file_path: Path,
    study_code: Optional[str] = None,
    study_id: Optional[str] = None,
    document_id: Optional[str] = None,
    version_id: Optional[str] = None,
    create_new_study: bool = True,
    version_label: str = "v1.0",
    ingestion_timeout: int = 600,
) -> Tuple[str, bool, Dict[str, Any]]:
    """
    Обрабатывает один файл: создаёт структуру (если нужно), загружает и запускает ингестию.
    
    Возвращает:
        (version_id, success, stats)
    """
    file_name = file_path.name
    
    try:
        # Создаём структуру, если не указана
        if not version_id:
            if not study_id or create_new_study:
                # Создаём новое исследование для каждого файла
                actual_study_code = study_code or f"BATCH-{int(time.time())}-{file_path.stem[:30]}"
                study_title = f"Batch Upload: {file_path.stem}"
                study_id = create_study(api_base, workspace_id, actual_study_code, study_title)
                print(f"    Создано исследование: study_id={study_id}")
            
            if not document_id:
                # Создаём документ
                doc_title = file_path.stem
                document_id = create_document(api_base, study_id, "protocol", doc_title)
                print(f"    Создан документ: document_id={document_id}")
            
            # Создаём версию
            version_id = create_document_version(api_base, document_id, version_label)
            print(f"    Создана версия: version_id={version_id}")
        
        # Загружаем файл
        print(f"    Загрузка файла...")
        upload_result = upload_file(api_base, version_id, file_path)
        print(f"    Файл загружен: sha256={upload_result.get('sha256', '')[:16]}...")
        
        # Проверяем текущий статус перед запуском ингестии
        version_data = get_version_status(api_base, version_id)
        current_status = version_data.get("ingestion_status")
        
        # Запускаем ингестию (если она ещё не выполняется)
        if current_status == "processing":
            print(f"    Ингестия уже выполняется (статус: {current_status})")
            print(f"    Ожидание завершения существующей ингестии...")
        else:
            print(f"    Запуск процесса ингестии...")
            print(f"      - Извлечение структуры документа (Anchors)")
            print(f"      - Извлечение Schedule of Activities (SoA)")
            print(f"      - Создание Chunks (Narrative Index)")
            print(f"      - Извлечение фактов (Rules-first)")
            print(f"      - Маппинг секций (Section Mapping)")
            was_started, conflict_status = start_ingestion(api_base, version_id, force=True)
            
            if not was_started and conflict_status == "processing":
                print(f"    Ингестия уже выполняется, ожидание завершения...")
        
        # Ожидаем завершения
        print(f"    Ожидание завершения ингестии (таймаут: {ingestion_timeout} сек)...")
        final_status, version_data = poll_ingestion_status(api_base, version_id, ingestion_timeout)
        
        # Извлекаем детальную статистику
        detailed_stats = extract_detailed_stats(version_data)
        summary = version_data.get("ingestion_summary_json", {})
        
        # Формируем общую статистику
        stats = {
            "version_id": version_id,
            "version_label": version_label,
            "final_status": final_status,
            "anchors_created": summary.get("anchors_created", 0),
            "chunks_created": summary.get("chunks_created", 0),
            "warnings": summary.get("warnings", []),
            "detailed": detailed_stats,
            "ingestion_summary": summary,
            "matched_anchors": summary.get("matched_anchors", 0),
            "changed_anchors": summary.get("changed_anchors", 0),
        }
        
        # Выводим информацию о выполненных шагах
        print(f"    Детали обработки:")
        print(f"      ✓ Anchors создано: {stats['anchors_created']}")
        
        # SoA
        soa = detailed_stats["soa"]
        if soa["detected"]:
            print(f"      ✓ SoA извлечён: {soa['visits_count']} визитов, {soa['procedures_count']} процедур")
            if soa["confidence"]:
                print(f"        (уверенность: {soa['confidence']:.2f})")
        else:
            print(f"      ⚠ SoA не обнаружен")
        
        # Chunks
        chunks = detailed_stats["chunks"]
        print(f"      ✓ Chunks создано: {chunks['created']}")
        if chunks["anchors_per_chunk_avg"]:
            print(f"        (среднее anchors/chunk: {chunks['anchors_per_chunk_avg']})")
        
        # Facts
        facts = detailed_stats["facts"]
        print(f"      ✓ Фактов извлечено: {facts['total_extracted']}")
        if facts["needs_review"]:
            print(f"        (требуют проверки: {len(facts['needs_review'])})")
        
        # Section Mapping
        mapping = detailed_stats["section_mapping"]
        print(f"      ✓ Секций сопоставлено: {mapping['sections_mapped']}")
        if mapping["needs_review"] > 0:
            print(f"        (требуют проверки: {mapping['needs_review']})")
        
        if final_status == "ready":
            print(f"    ✓ Ингестия завершена успешно")
            return version_id, True, stats
        elif final_status == "needs_review":
            print(f"    ⚠ Ингестия завершена, требуется проверка")
            return version_id, True, stats
        else:  # failed
            print(f"    ✗ Ингестия завершилась с ошибкой")
            return version_id, False, stats
        
    except SystemExit:
        raise
    except Exception as e:
        print(f"    ✗ Ошибка при обработке: {e}")
        import traceback
        traceback.print_exc()
        return version_id if version_id else "", False, {}


def process_study(
    study_code: str,
    files: List[Path],
    api_base: str,
    workspace_id: str,
    ingestion_timeout: int,
    project_root: Path,
) -> List[Dict[str, Any]]:
    """Обрабатывает файлы одного исследования последовательно (версии в порядке mtime)."""
    print(f"\n{'='*80}")
    print(f"СТАРТ ИССЛЕДОВАНИЯ: {study_code} (файлов: {len(files)})")
    print(f"{'='*80}")
    
    study_title = f"Benchmark: {study_code}"
    study_id = ensure_study(api_base, workspace_id, study_code, study_title)
    document_id = ensure_protocol_document(api_base, study_id, f"{study_code} Protocol")
    
    study_results: List[Dict[str, Any]] = []
    
    for idx, file_path in enumerate(files, 1):
        version_label = f"v{idx}.0"
        print(f"\n--- Версия {version_label} ({file_path.name}) ---")
        started_at = time.time()
        
        version_id_result, success, stats = process_file(
            api_base=api_base,
            workspace_id=workspace_id,
            file_path=file_path,
            study_code=study_code,
            study_id=study_id,
            document_id=document_id,
            version_id=None,
            create_new_study=False,
            version_label=version_label,
            ingestion_timeout=ingestion_timeout,
        )
        
        duration = round(time.time() - started_at, 2)
        stats = stats or {}
        stats["processing_time_sec"] = duration
        
        if success and version_id_result:
            save_benchmark_artifacts(
                api_base=api_base,
                project_root=project_root,
                study_code=study_code,
                version_number=idx,
                study_id=study_id,
                version_id=version_id_result,
            )
        
        study_results.append(
            {
                "study_code": study_code,
                "file_name": file_path.name,
                "version": version_label,
                "status": stats.get("final_status", "failed" if not success else "unknown"),
                "anchors_count": stats.get("anchors_created", 0),
                "soa_confidence": (stats.get("detailed", {}) or {}).get("soa", {}).get("confidence"),
                "matched_anchors": stats.get("matched_anchors", 0),
                "changed_anchors": stats.get("changed_anchors", 0),
                "processing_time_sec": duration,
                "version_id": version_id_result,
                "success": success,
                "stats": stats,
            }
        )
    
    return study_results


def main() -> None:
    # Настройка кодировки для Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    
    # Загрузка переменных окружения
    if load_dotenv:
        script_dir = Path(__file__).parent.absolute()
        backend_dir = script_dir.parent / "backend"
        env_path = backend_dir / ".env"
        
        if env_path.exists():
            load_dotenv(env_path, override=False)
            print(f"Загружены переменные окружения из {env_path}")
    
    parser = argparse.ArgumentParser(
        description="Массовая загрузка и ингестия документов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Обработать один файл
  python batch_upload_ingest.py file.docx --workspace-id <UUID> --api http://localhost:8000
  
  # Обработать все файлы в папке
  python batch_upload_ingest.py ./documents --workspace-id <UUID>
  
  # Использовать существующие study/document/version
  python batch_upload_ingest.py file.docx --version-id <UUID>
  
  # Продолжить обработку с пропуском уже обработанных файлов
  python batch_upload_ingest.py ./documents --workspace-id <UUID> --resume
        """
    )
    
    parser.add_argument(
        "path",
        type=str,
        help="Путь к файлу или папке с файлами"
    )
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="Базовый URL API (по умолчанию: http://localhost:8000)"
    )
    parser.add_argument(
        "--workspace-id",
        default="",
        help="UUID рабочего пространства (обязательно, если не указан --version-id)"
    )
    parser.add_argument(
        "--version-id",
        default="",
        help="UUID существующей версии документа (если указан, файл будет загружен в эту версию)"
    )
    parser.add_argument(
        "--study-id",
        default="",
        help="UUID существующего исследования (опционально)"
    )
    parser.add_argument(
        "--document-id",
        default="",
        help="UUID существующего документа (опционально)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="Таймаут ожидания ингестии в секундах (по умолчанию: 600)"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Пропускать уже обработанные файлы (проверка по SHA256 в базе данных) и начинать с первого необработанного"
    )
    parser.add_argument(
        "--max-studies",
        type=int,
        default=0,
        help="Максимальное число обрабатываемых исследований (0 — без ограничений)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Выводить отладочную информацию (хеши файлов, статусы и т.д.)"
    )
    
    args = parser.parse_args()
    
    api_base = args.api.rstrip("/")
    
    # Проверка workspace_id
    workspace_id = args.workspace_id.strip()
    version_id = args.version_id.strip()
    
    if not version_id:
        if not workspace_id:
            workspace_id = input("Введите WORKSPACE_ID (UUID): ").strip()
        if len(workspace_id) != 36:
            die(f"Неверный формат WORKSPACE_ID (ожидается UUID из 36 символов): {workspace_id}")
    
    # Определяем путь
    input_path = Path(args.path)
    if not input_path.is_absolute():
        # Если путь относительный, делаем его относительно текущей директории
        input_path = Path.cwd() / input_path
    
    project_root = Path(__file__).resolve().parent.parent
    
    study_files = discover_study_files(input_path)
    if not study_files:
        die(f"Не найдено файлов для обработки в: {input_path}")
    
    processed_hashes: Set[str] = set()
    skipped_count = 0
    
    if args.resume:
        if not workspace_id:
            die("Для использования --resume необходимо указать --workspace-id")
        print(f"\n{'='*80}")
        print("ПРОВЕРКА ОБРАБОТАННЫХ ФАЙЛОВ")
        print(f"{'='*80}")
        print(f"Получение списка обработанных файлов из базы данных...")
        processed_hashes = get_processed_sha256_set(api_base, workspace_id, debug=args.debug)
        study_files, skipped_count = filter_processed_by_hash(study_files, processed_hashes)
        if not study_files:
            print("Все найденные файлы уже обработаны (ready/needs_review). Выход.")
            sys.exit(0)
    
    study_items = sorted(study_files.items(), key=lambda x: x[0])
    if args.max_studies and args.max_studies > 0:
        if args.max_studies < len(study_items):
            print(f"\nБудет обработано только первых {args.max_studies} исследований из {len(study_items)} (по алфавиту).")
        study_items = study_items[:args.max_studies]
    
    total_files = sum(len(v) for _, v in study_items)
    print(f"\n{'='*80}")
    print(f"НАЙДЕНЫ ИССЛЕДОВАНИЯ: {len(study_items)} (файлов: {total_files})")
    if skipped_count:
        print(f"Пропущено по --resume: {skipped_count}")
    print(f"{'='*80}")
    for study_code, files in study_items:
        print(f"  • {study_code}: {len(files)} файл(ов)")
        for idx, file_path in enumerate(files, 1):
            print(f"      {idx}. {file_path.name}")
    print()
    
    all_rows: List[Dict[str, Any]] = []
    
    for study_code, files in study_items:
        try:
            rows = process_study(
                study_code,
                files,
                api_base,
                workspace_id,
                args.timeout,
                project_root,
            )
            all_rows.extend(rows)
        except Exception as e:
            print(f"✗ Ошибка при обработке исследования {study_code}: {e}")
    
    # Запись расширенного отчёта
    summary_path = project_root / "benchmark_summary.csv"
    ensure_dir(summary_path.parent)
    fieldnames = [
        "study_code",
        "file_name",
        "version",
        "status",
        "anchors_count",
        "soa_confidence",
        "matched_anchors",
        "changed_anchors",
        "processing_time_sec",
    ]
    with open(summary_path, "w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    
    total_versions = len(all_rows)
    successful = sum(1 for r in all_rows if r.get("success"))
    failed = total_versions - successful
    
    print(f"\n{'='*80}")
    print("ИТОГОВАЯ СТАТИСТИКА")
    print(f"{'='*80}")
    print(f"Исследований обработано: {len(study_items)}")
    print(f"Всего версий: {total_versions}")
    print(f"Успешно (ready/needs_review): {successful}")
    print(f"С ошибками: {failed}")
    print(f"CSV отчёт: {summary_path}")
    
    if all_rows:
        print(f"\n{'='*80}")
        print("ДЕТАЛИ ПО ВЕРСИЯМ:")
        print(f"{'='*80}")
        for row in all_rows:
            status_icon = "✓" if row.get("success") else "✗"
            print(f"\n{status_icon} {row['study_code']} {row['version']} ({row['file_name']}): {row.get('status')}")
            print(f"  Anchors: {row.get('anchors_count', 0)}")
            print(f"  SoA confidence: {row.get('soa_confidence')}")
            print(f"  matched_anchors: {row.get('matched_anchors', 0)}, changed_anchors: {row.get('changed_anchors', 0)}")
            print(f"  processing_time_sec: {row.get('processing_time_sec')}")
    
    if failed > 0:
        sys.exit(1)
    else:
        print(f"\n{'='*80}")
        print("ВСЕ ВЕРСИИ ОБРАБОТАНЫ (без ошибок в статусе ready/needs_review)")
        print(f"{'='*80}")


if __name__ == "__main__":
    main()

