"""
Оффлайн-утилита для оценки качества маппинга секций.

Для каждого section_contract и каждого подходящего document_version запускает
SectionMappingService и собирает метрики: coverage, confidence, evidence_health,
стабильность между версиями, confusion hints для failed маппингов.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

# Добавляем путь к backend для импорта модулей приложения
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.enums import (
    AnchorContentType,
    CitationPolicy,
    DocumentType,
    IngestionStatus,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import Document, DocumentVersion
from app.db.session import async_session_factory
from app.services.section_mapping import SectionMappingService


@dataclass
class SectionMetrics:
    """Метрики для одного section_key."""

    section_key: str
    coverage: dict[str, int] = field(default_factory=lambda: {"mapped": 0, "needs_review": 0, "failed": 0})
    confidence_values: list[float] = field(default_factory=list)
    evidence_health: dict[str, float] = field(
        default_factory=lambda: {
            "avg_anchors_count": 0.0,
            "avg_li_share": 0.0,
            "avg_cell_share": 0.0,
            "avg_tbl_share": 0.0,
        }
    )
    stability: dict[str, float] = field(default_factory=lambda: {"avg": 0.0, "p10": 0.0})
    confusion_hints: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvaluationReport:
    """Полный отчет об оценке маппинга."""

    workspace_id: str
    doc_type: str
    contracts_source: str
    total_contracts: int
    total_document_versions: int
    section_metrics: dict[str, SectionMetrics] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


async def load_contracts_from_db(
    db: AsyncSession, workspace_id: UUID, doc_type: DocumentType
) -> list[SectionContract]:
    """Загружает контракты из БД."""
    stmt = select(SectionContract).where(
        SectionContract.workspace_id == workspace_id,
        SectionContract.doc_type == doc_type,
        SectionContract.is_active == True,
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def load_contracts_from_seed(seed_path: Path) -> list[dict[str, Any]]:
    """Загружает контракты из seed.json файла."""
    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "contracts" in data:
        return data["contracts"]
    raise ValueError(f"Неверный формат seed файла: ожидается list или dict с ключом 'contracts'")


async def get_eligible_document_versions(
    db: AsyncSession, workspace_id: UUID, doc_type: DocumentType
) -> list[DocumentVersion]:
    """Получает подходящие document_version для оценки."""
    stmt = (
        select(DocumentVersion)
        .join(Document)
        .where(
            Document.workspace_id == workspace_id,
            Document.doc_type == doc_type,
            DocumentVersion.ingestion_status == IngestionStatus.READY,
        )
        .order_by(DocumentVersion.document_id, DocumentVersion.created_at)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def compute_percentile(values: list[float], percentile: float) -> float:
    """Вычисляет перцентиль для списка значений.
    
    Использует метод ближайшего ранга (nearest rank method):
    index = ceil((percentile / 100) * n) - 1
    где n - количество элементов.
    """
    if not values:
        return 0.0
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    # Метод ближайшего ранга: index = ceil((percentile / 100) * n) - 1
    index = math.ceil((percentile / 100.0) * n) - 1
    index = min(max(index, 0), n - 1)  # Ограничиваем диапазон
    return sorted_values[index]


def jaccard_similarity(set1: set[str], set2: set[str]) -> float:
    """Вычисляет коэффициент Жаккара для двух множеств."""
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def longest_common_prefix_ratio(path1: str, path2: str) -> float:
    """Вычисляет отношение длины наибольшего общего префикса к максимальной длине."""
    if not path1 or not path2:
        return 0.0
    if path1 == path2:
        return 1.0
    
    # Разбиваем пути по разделителям
    parts1 = path1.split("/") if "/" in path1 else path1.split(".")
    parts2 = path2.split("/") if "/" in path2 else path2.split(".")
    
    # Находим общий префикс
    common_length = 0
    for p1, p2 in zip(parts1, parts2):
        if p1 == p2:
            common_length += 1
        else:
            break
    
    max_length = max(len(parts1), len(parts2))
    return common_length / max_length if max_length > 0 else 0.0


def extract_anchor_hash(anchor_id: str) -> str:
    """Извлекает hash часть из anchor_id (убирает doc_version_id префикс)."""
    # Формат: {doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash}
    parts = anchor_id.split(":")
    if len(parts) >= 5:
        # Возвращаем hash (последняя часть)
        return parts[-1]
    # Fallback: возвращаем anchor_id без первого префикса
    if ":" in anchor_id:
        return ":".join(anchor_id.split(":")[1:])
    return anchor_id


async def compute_stability_metrics(
    db: AsyncSession, section_key: str, document_versions: list[DocumentVersion]
) -> dict[str, float]:
    """Вычисляет метрики стабильности между последовательными версиями документа."""
    # Группируем версии по document_id
    versions_by_doc: dict[UUID, list[DocumentVersion]] = defaultdict(list)
    for dv in document_versions:
        versions_by_doc[dv.document_id].append(dv)
    
    # Сортируем версии по created_at
    for doc_id in versions_by_doc:
        versions_by_doc[doc_id].sort(key=lambda v: v.created_at)
    
    stability_scores: list[float] = []
    
    for doc_id, versions in versions_by_doc.items():
        if len(versions) < 2:
            continue
        
        # Сравниваем последовательные пары версий
        for i in range(len(versions) - 1):
            v1, v2 = versions[i], versions[i + 1]
            
            # Получаем маппинги для обеих версий
            stmt1 = select(SectionMap).where(
                SectionMap.doc_version_id == v1.id,
                SectionMap.section_key == section_key,
            )
            stmt2 = select(SectionMap).where(
                SectionMap.doc_version_id == v2.id,
                SectionMap.section_key == section_key,
            )
            
            result1 = await db.execute(stmt1)
            result2 = await db.execute(stmt2)
            map1 = result1.scalar_one_or_none()
            map2 = result2.scalar_one_or_none()
            
            if not map1 or not map2:
                continue
            
            # Извлекаем anchor hashes (без doc_version_id префикса)
            hashes1 = {extract_anchor_hash(aid) for aid in (map1.anchor_ids or [])}
            hashes2 = {extract_anchor_hash(aid) for aid in (map2.anchor_ids or [])}
            
            # Jaccard similarity по anchor hashes
            jaccard = jaccard_similarity(hashes1, hashes2)
            
            # Section path similarity (если есть section_path в notes или metadata)
            # Для упрощения используем section_path из первого anchor, если доступен
            path_sim = 0.0
            if map1.anchor_ids and map2.anchor_ids:
                # Получаем anchors для извлечения section_path
                anchors_stmt1 = select(Anchor).where(Anchor.anchor_id.in_(map1.anchor_ids[:1]))
                anchors_stmt2 = select(Anchor).where(Anchor.anchor_id.in_(map2.anchor_ids[:1]))
                anchors1 = (await db.execute(anchors_stmt1)).scalars().first()
                anchors2 = (await db.execute(anchors_stmt2)).scalars().first()
                
                if anchors1 and anchors2:
                    path1 = anchors1.section_path or ""
                    path2 = anchors2.section_path or ""
                    path_sim = longest_common_prefix_ratio(path1, path2)
            
            # Комбинируем метрики (среднее)
            stability = (jaccard + path_sim) / 2.0
            stability_scores.append(stability)
    
    if not stability_scores:
        return {"avg": 0.0, "p10": 0.0}
    
    return {
        "avg": sum(stability_scores) / len(stability_scores),
        "p10": compute_percentile(stability_scores, 10.0),
    }


async def compute_evidence_health(
    db: AsyncSession, section_maps: list[SectionMap]
) -> dict[str, float]:
    """Вычисляет метрики evidence_health для списка маппингов."""
    if not section_maps:
        return {
            "avg_anchors_count": 0.0,
            "avg_li_share": 0.0,
            "avg_cell_share": 0.0,
            "avg_tbl_share": 0.0,
        }
    
    anchors_counts: list[int] = []
    li_shares: list[float] = []
    cell_shares: list[float] = []
    tbl_shares: list[float] = []
    
    for section_map in section_maps:
        if not section_map.anchor_ids:
            continue
        
        anchor_ids = section_map.anchor_ids
        anchors_counts.append(len(anchor_ids))
        
        # Получаем anchors для подсчета типов контента
        stmt = select(Anchor).where(Anchor.anchor_id.in_(anchor_ids))
        result = await db.execute(stmt)
        anchors = list(result.scalars().all())
        
        total = len(anchors)
        if total == 0:
            continue
        
        li_count = sum(1 for a in anchors if a.content_type == AnchorContentType.LI)
        cell_count = sum(1 for a in anchors if a.content_type == AnchorContentType.CELL)
        tbl_count = sum(1 for a in anchors if a.content_type == AnchorContentType.TBL)
        
        li_shares.append(li_count / total)
        cell_shares.append(cell_count / total)
        tbl_shares.append(tbl_count / total)
    
    return {
        "avg_anchors_count": sum(anchors_counts) / len(anchors_counts) if anchors_counts else 0.0,
        "avg_li_share": sum(li_shares) / len(li_shares) if li_shares else 0.0,
        "avg_cell_share": sum(cell_shares) / len(cell_shares) if cell_shares else 0.0,
        "avg_tbl_share": sum(tbl_shares) / len(tbl_shares) if tbl_shares else 0.0,
    }


async def generate_confusion_hints(
    db: AsyncSession,
    section_key: str,
    contract: SectionContract,
    failed_maps: list[SectionMap],
) -> list[dict[str, Any]]:
    """Генерирует confusion hints для failed маппингов."""
    if not failed_maps:
        return []
    
    hints: list[dict[str, Any]] = []
    
    for section_map in failed_maps[:10]:  # Ограничиваем до 10
        doc_version_id = section_map.doc_version_id
        
        # Получаем все заголовки из документа
        stmt = select(Anchor).where(
            Anchor.doc_version_id == doc_version_id,
            Anchor.content_type == AnchorContentType.HDR,
        )
        result = await db.execute(stmt)
        headings = list(result.scalars().all())
        
        # Извлекаем ключевые слова из контракта
        recipe = contract.retrieval_recipe_json or {}
        signals = recipe.get("mapping", {}).get("signals", {})
        lang_signals = signals.get("lang", {})
        
        # Собираем все keywords из всех языков
        keywords: set[str] = set()
        for lang_data in lang_signals.values():
            if isinstance(lang_data, dict):
                keywords.update(lang_data.get("must", []))
                keywords.update(lang_data.get("should", []))
        
        # Если keywords нет, используем title
        if not keywords:
            keywords = {contract.title.lower()}
        
        # Находим ближайшие заголовки по совпадению ключевых слов
        heading_scores: list[tuple[Anchor, int]] = []
        for heading in headings:
            text_lower = (heading.text_norm or "").lower()
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            if score > 0:
                heading_scores.append((heading, score))
        
        # Сортируем по score и берем top-10
        heading_scores.sort(key=lambda x: x[1], reverse=True)
        top_headings = [{"text": h.text_norm, "score": s} for h, s in heading_scores[:10]]
        
        hints.append(
            {
                "doc_version_id": str(doc_version_id),
                "section_key": section_key,
                "top_headings": top_headings,
            }
        )
    
    return hints


async def evaluate_mapping(
    db: AsyncSession,
    workspace_id: UUID,
    doc_type: DocumentType,
    contracts: list[SectionContract | dict[str, Any]],
    dry_run: bool = False,
) -> EvaluationReport:
    """Основная функция оценки маппинга."""
    logger.info(f"Начало оценки маппинга: workspace_id={workspace_id}, doc_type={doc_type.value}")
    
    # Получаем подходящие document_version
    document_versions = await get_eligible_document_versions(db, workspace_id, doc_type)
    logger.info(f"Найдено {len(document_versions)} подходящих document_version")
    
    if not document_versions:
        logger.warning("Нет подходящих document_version для оценки")
        return EvaluationReport(
            workspace_id=str(workspace_id),
            doc_type=doc_type.value,
            contracts_source="unknown",
            total_contracts=len(contracts),
            total_document_versions=0,
        )
    
    # Конвертируем dict контракты в SectionContract объекты (если нужно)
    section_contracts: list[SectionContract] = []
    for contract in contracts:
        if isinstance(contract, dict):
            # Создаем временный объект для работы (не сохраняем в БД)
            citation_policy_str = contract.get("citation_policy", "per_claim")
            try:
                citation_policy = CitationPolicy(citation_policy_str)
            except ValueError:
                citation_policy = CitationPolicy.PER_CLAIM
            
            section_contract = SectionContract(
                workspace_id=workspace_id,
                doc_type=doc_type,
                section_key=contract["section_key"],
                title=contract.get("title", ""),
                required_facts_json=contract.get("required_facts_json", {}),
                allowed_sources_json=contract.get("allowed_sources_json", {}),
                retrieval_recipe_json=contract.get("retrieval_recipe_json", {}),
                qc_ruleset_json=contract.get("qc_ruleset_json", {}),
                citation_policy=citation_policy,
                version=contract.get("version", 1),
                is_active=True,
            )
            section_contracts.append(section_contract)
        else:
            section_contracts.append(contract)
    
    # Инициализируем сервис маппинга
    mapping_service = SectionMappingService(db)
    
    # Собираем метрики по section_key
    section_metrics: dict[str, SectionMetrics] = {}
    
    # Запускаем маппинг для каждой комбинации контракт + document_version
    for contract in section_contracts:
        section_key = contract.section_key
        logger.info(f"Обработка section_key={section_key}")
        
        metrics = SectionMetrics(section_key=section_key)
        section_maps: list[SectionMap] = []
        failed_maps: list[SectionMap] = []
        
        for doc_version in document_versions:
            try:
                # Запускаем маппинг
                if not dry_run:
                    summary = await mapping_service.map_sections(doc_version.id, force=True)
                # В dry-run режиме пропускаем создание новых маппингов, только читаем существующие
                # (продолжаем выполнение, чтобы получить существующие маппинги)
                
                # Получаем созданный/обновленный маппинг
                stmt = select(SectionMap).where(
                    SectionMap.doc_version_id == doc_version.id,
                    SectionMap.section_key == section_key,
                )
                result = await db.execute(stmt)
                section_map = result.scalar_one_or_none()
                
                if section_map:
                    section_maps.append(section_map)
                    
                    # Обновляем coverage
                    if section_map.status == SectionMapStatus.MAPPED:
                        metrics.coverage["mapped"] += 1
                    elif section_map.status == SectionMapStatus.NEEDS_REVIEW:
                        metrics.coverage["needs_review"] += 1
                        # Считаем needs_review как failed для confusion hints, если нет anchor_ids
                        if not section_map.anchor_ids or len(section_map.anchor_ids) == 0:
                            failed_maps.append(section_map)
                    # OVERRIDDEN не учитываем в coverage
                    
                    # Собираем confidence
                    if section_map.confidence is not None:
                        metrics.confidence_values.append(section_map.confidence)
                
            except Exception as e:
                logger.error(f"Ошибка при маппинге {section_key} для doc_version_id={doc_version.id}: {e}")
                metrics.coverage["failed"] += 1
                # Создаем фиктивный failed map для confusion hints
                failed_map = SectionMap(
                    doc_version_id=doc_version.id,
                    section_key=section_key,
                    anchor_ids=[],
                    chunk_ids=None,
                    confidence=0.0,
                    status=SectionMapStatus.NEEDS_REVIEW,
                    mapped_by=SectionMapMappedBy.SYSTEM,
                    notes=f"Ошибка: {str(e)}",
                )
                failed_maps.append(failed_map)
        
        # Вычисляем метрики confidence
        if metrics.confidence_values:
            metrics.evidence_health = await compute_evidence_health(db, section_maps)
        
        # Вычисляем стабильность
        metrics.stability = await compute_stability_metrics(db, section_key, document_versions)
        
        # Генерируем confusion hints
        metrics.confusion_hints = await generate_confusion_hints(db, section_key, contract, failed_maps)
        
        section_metrics[section_key] = metrics
    
    # Формируем summary
    total_mapped = sum(m.coverage["mapped"] for m in section_metrics.values())
    total_needs_review = sum(m.coverage["needs_review"] for m in section_metrics.values())
    total_failed = sum(m.coverage["failed"] for m in section_metrics.values())
    
    all_confidence = []
    for m in section_metrics.values():
        all_confidence.extend(m.confidence_values)
    
    summary = {
        "total_mapped": total_mapped,
        "total_needs_review": total_needs_review,
        "total_failed": total_failed,
        "avg_confidence": sum(all_confidence) / len(all_confidence) if all_confidence else 0.0,
        "p50_confidence": compute_percentile(all_confidence, 50.0) if all_confidence else 0.0,
        "p90_confidence": compute_percentile(all_confidence, 90.0) if all_confidence else 0.0,
    }
    
    contracts_source = "DB" if isinstance(contracts[0], SectionContract) else "seed.json"
    
    return EvaluationReport(
        workspace_id=str(workspace_id),
        doc_type=doc_type.value,
        contracts_source=contracts_source,
        total_contracts=len(section_contracts),
        total_document_versions=len(document_versions),
        section_metrics=section_metrics,
        summary=summary,
    )


def report_to_dict(report: EvaluationReport) -> dict[str, Any]:
    """Конвертирует отчет в словарь для JSON."""
    return {
        "workspace_id": report.workspace_id,
        "doc_type": report.doc_type,
        "contracts_source": report.contracts_source,
        "total_contracts": report.total_contracts,
        "total_document_versions": report.total_document_versions,
        "summary": report.summary,
        "section_metrics": {
            section_key: {
                "section_key": metrics.section_key,
                "coverage": metrics.coverage,
                "confidence": {
                    "avg": sum(metrics.confidence_values) / len(metrics.confidence_values)
                    if metrics.confidence_values
                    else 0.0,
                    "p50": compute_percentile(metrics.confidence_values, 50.0),
                    "p90": compute_percentile(metrics.confidence_values, 90.0),
                },
                "evidence_health": metrics.evidence_health,
                "stability": metrics.stability,
                "confusion_hints": metrics.confusion_hints,
            }
            for section_key, metrics in report.section_metrics.items()
        },
    }


def print_readable_table(report: EvaluationReport) -> None:
    """Выводит читаемую таблицу с метриками."""
    print("\n" + "=" * 100)
    print("ОТЧЕТ ОБ ОЦЕНКЕ МАППИНГА СЕКЦИЙ")
    print("=" * 100)
    print(f"Workspace ID: {report.workspace_id}")
    print(f"Тип документа: {report.doc_type}")
    print(f"Источник контрактов: {report.contracts_source}")
    print(f"Всего контрактов: {report.total_contracts}")
    print(f"Всего версий документов: {report.total_document_versions}")
    print("\n" + "-" * 100)
    print("СВОДКА")
    print("-" * 100)
    print(f"Всего mapped: {report.summary.get('total_mapped', 0)}")
    print(f"Всего needs_review: {report.summary.get('total_needs_review', 0)}")
    print(f"Всего failed: {report.summary.get('total_failed', 0)}")
    print(f"Средний confidence: {report.summary.get('avg_confidence', 0.0):.3f}")
    print(f"P50 confidence: {report.summary.get('p50_confidence', 0.0):.3f}")
    print(f"P90 confidence: {report.summary.get('p90_confidence', 0.0):.3f}")
    print("\n" + "-" * 100)
    print("МЕТРИКИ ПО СЕКЦИЯМ")
    print("-" * 100)
    
    for section_key, metrics in sorted(report.section_metrics.items()):
        print(f"\n{section_key}:")
        print(f"  Coverage: mapped={metrics.coverage['mapped']}, "
              f"needs_review={metrics.coverage['needs_review']}, "
              f"failed={metrics.coverage['failed']}")
        
        if metrics.confidence_values:
            avg_conf = sum(metrics.confidence_values) / len(metrics.confidence_values)
            p50_conf = compute_percentile(metrics.confidence_values, 50.0)
            p90_conf = compute_percentile(metrics.confidence_values, 90.0)
            print(f"  Confidence: avg={avg_conf:.3f}, p50={p50_conf:.3f}, p90={p90_conf:.3f}")
        
        print(f"  Evidence Health:")
        print(f"    avg_anchors_count={metrics.evidence_health['avg_anchors_count']:.1f}")
        print(f"    avg_li_share={metrics.evidence_health['avg_li_share']:.3f}")
        print(f"    avg_cell_share={metrics.evidence_health['avg_cell_share']:.3f}")
        print(f"    avg_tbl_share={metrics.evidence_health['avg_tbl_share']:.3f}")
        
        print(f"  Stability: avg={metrics.stability['avg']:.3f}, p10={metrics.stability['p10']:.3f}")
        
        if metrics.confusion_hints:
            print(f"  Confusion Hints: {len(metrics.confusion_hints)} hints")
            for hint in metrics.confusion_hints[:3]:  # Показываем первые 3
                print(f"    - doc_version_id={hint['doc_version_id']}: "
                      f"{len(hint['top_headings'])} ближайших заголовков")


async def main() -> None:
    """Главная функция CLI."""
    parser = argparse.ArgumentParser(
        description="Оценка качества маппинга секций",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace-id",
        type=str,
        required=True,
        help="UUID workspace",
    )
    parser.add_argument(
        "--doc-type",
        type=str,
        required=True,
        choices=["protocol", "sap", "tfl", "csr", "ib", "icf", "other"],
        help="Тип документа",
    )
    parser.add_argument(
        "--contracts",
        type=str,
        required=True,
        help="Источник контрактов: 'DB' или путь к seed.json файлу",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Путь к выходному JSON файлу отчета",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Пропустить запись в БД (только чтение существующих маппингов)",
    )
    
    args = parser.parse_args()
    
    workspace_id = UUID(args.workspace_id)
    doc_type = DocumentType(args.doc_type)
    
    # Загружаем контракты
    if args.contracts.upper() == "DB":
        async with async_session_factory() as db:
            contracts = await load_contracts_from_db(db, workspace_id, doc_type)
    else:
        seed_path = Path(args.contracts)
        if not seed_path.exists():
            print(f"Ошибка: файл {seed_path} не найден", file=sys.stderr)
            sys.exit(1)
        contracts_data = load_contracts_from_seed(seed_path)
        contracts = contracts_data
    
    if not contracts:
        print("Ошибка: не найдено контрактов", file=sys.stderr)
        sys.exit(1)
    
    # Запускаем оценку
    async with async_session_factory() as db:
        report = await evaluate_mapping(db, workspace_id, doc_type, contracts, dry_run=args.dry_run)
    
    # Сохраняем JSON отчет
    report_dict = report_to_dict(report)
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, ensure_ascii=False, indent=2)
    
    # Выводим читаемую таблицу
    print_readable_table(report)
    
    print(f"\nОтчет сохранен в {output_path}")


if __name__ == "__main__":
    asyncio.run(main())

