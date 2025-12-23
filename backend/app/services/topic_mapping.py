"""Сервис для автоматического маппинга блоков заголовков на топики (новая архитектура).

В новой архитектуре:
- Блоки (heading blocks) маппятся напрямую на топики
- Кластеризация опциональна и используется только как prior
- topic_evidence строится из block assignments напрямую
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.enums import DocumentLanguage, DocumentType, SourceZone
from app.db.models.anchors import Anchor
from app.db.models.studies import Document, DocumentVersion
from app.db.models.topics import (
    HeadingBlockTopicAssignment,
    HeadingCluster,
    Topic,
    TopicMappingRun,
)
from app.services.heading_block_builder import HeadingBlock, HeadingBlockBuilder
from app.services.heading_clustering import HeadingClusteringService
from app.services.source_zone_classifier import get_classifier
from app.services.text_normalization import normalize_for_match
from app.services.topic_repository import TopicRepository


@dataclass
class BlockTopicScore:
    """Оценка соответствия блока топику."""

    topic_key: str
    final_score: float
    heading_match_score: float = 0.0
    text_keywords_match_score: float = 0.0
    source_zone_prior: float = 0.0
    cluster_prior: float = 0.0  # Опциональный prior от кластеризации
    signals_json: dict[str, Any] | None = None


@dataclass
class MappingMetrics:
    """Метрики качества маппинга."""

    blocks_total: int
    blocks_mapped: int
    mapped_rate: float
    low_confidence_rate: float
    unmapped_top_headings: list[str]
    topic_coverage_topN: list[dict[str, Any]]  # Топ N топиков с наибольшим количеством блоков
    evidence_by_zone: dict[str, int]  # Количество блоков по source_zone
    clustering_enabled: bool
    clusters_total: int | None = None
    clusters_labeled: int | None = None
    avg_cluster_size: float | None = None


class TopicMappingService:
    """Сервис для маппинга блоков заголовков на топики."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.source_zone_classifier = get_classifier()

    async def map_topics_for_doc_version(
        self,
        doc_version_id: UUID,
        mode: str = "auto",
        apply: bool = True,
        confidence_threshold: float = 0.65,
    ) -> tuple[list[HeadingBlockTopicAssignment], MappingMetrics]:
        """
        Выполняет маппинг блоков на топики для версии документа.

        Args:
            doc_version_id: ID версии документа
            mode: Режим маппинга ("auto" или "assist")
            apply: Сохранять ли результаты в БД
            confidence_threshold: Минимальный confidence для маппинга

        Returns:
            Кортеж (список назначений, метрики)
        """
        logger.info(
            f"Начало маппинга топиков для doc_version_id={doc_version_id}, mode={mode}, apply={apply}"
        )

        # Получаем doc_type
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")

        doc_type = document.doc_type

        # 1. Строим heading blocks
        block_builder = HeadingBlockBuilder(self.db)
        blocks = await block_builder.build_blocks_for_doc_version(doc_version_id, doc_type)

        if not blocks:
            logger.warning(f"Не найдено блоков для doc_version_id={doc_version_id}")
            return [], MappingMetrics(
                blocks_total=0,
                blocks_mapped=0,
                mapped_rate=0.0,
                low_confidence_rate=0.0,
                unmapped_top_headings=[],
                topic_coverage_topN=[],
                evidence_by_zone={},
                clustering_enabled=False,
            )

        logger.info(f"Найдено {len(blocks)} блоков")

        # 2. Загружаем активные топики
        topic_repo = TopicRepository(self.db)
        all_topics = await topic_repo.list_topics(
            workspace_id=document.workspace_id, is_active=True
        )

        # Фильтруем топики по applicable_to
        topics = []
        for topic in all_topics:
            applicable_to = topic.applicable_to_json or []
            if not applicable_to or doc_type.value in applicable_to:
                topics.append(topic)

        logger.info(f"Найдено {len(topics)} применимых топиков из {len(all_topics)} активных")

        # 3. Загружаем zone priors
        zone_priors_by_topic: dict[str, dict[str, float]] = {}
        for topic in topics:
            priors = await topic_repo.get_zone_priors(topic.topic_key, doc_type)
            zone_priors_by_topic[topic.topic_key] = {
                prior.zone_key: prior.weight for prior in priors
            }

        # 4. Опциональная кластеризация (если включена)
        cluster_prior_map: dict[str, str] = {}  # heading_block_id -> topic_key
        clustering_enabled = settings.topic_mapping_use_clustering

        if clustering_enabled:
            logger.info("Кластеризация включена, выполняется кластеризация заголовков...")
            try:
                clustering_service = HeadingClusteringService(self.db)
                clusters = await clustering_service.cluster_headings_for_doc_version(
                    doc_version_id=doc_version_id,
                    threshold=0.22,
                    min_size=3,
                    embedding_threshold=0.15,
                )

                # Загружаем cluster assignments (если есть)
                from app.db.models.topics import ClusterAssignment

                if clusters:
                    stmt = select(ClusterAssignment).where(
                        ClusterAssignment.doc_version_id == doc_version_id
                    )
                    result = await self.db.execute(stmt)
                    cluster_assignments = {ca.cluster_id: ca.topic_key for ca in result.scalars().all()}

                    # Строим маппинг heading_block_id -> topic_key через cluster_id
                    # Для этого нужно найти, к какому кластеру относится заголовок блока
                    for block in blocks:
                        # Ищем кластер, который содержит этот заголовок
                        # (предполагаем, что cluster_id хранится в location_json заголовка)
                        heading_anchor = await self._get_heading_anchor(block.heading_anchor_id)
                        if heading_anchor:
                            cluster_id = heading_anchor.location_json.get("cluster_id")
                            if cluster_id is not None and cluster_id in cluster_assignments:
                                cluster_prior_map[block.heading_block_id] = cluster_assignments[cluster_id]

                logger.info(f"Создано {len(clusters)} кластеров, {len(cluster_prior_map)} блоков получили cluster prior")
            except Exception as e:
                logger.warning(f"Ошибка при кластеризации: {e}, продолжаем без кластеризации")
                clustering_enabled = False

        # 5. Маппим каждый блок на топики
        assignments: list[HeadingBlockTopicAssignment] = []
        all_scores: list[list[BlockTopicScore]] = []
        unmapped_headings: list[str] = []

        for block in blocks:
            block_scores = await self._score_topics_for_block(
                block, topics, doc_type, zone_priors_by_topic, topic_repo, cluster_prior_map.get(block.heading_block_id)
            )
            all_scores.append(block_scores)

            if block_scores:
                best_score = block_scores[0]

                if best_score.final_score >= confidence_threshold:
                    if apply:
                        assignment = await self._create_block_assignment(
                            doc_version_id=doc_version_id,
                            heading_block_id=block.heading_block_id,
                            topic_key=best_score.topic_key,
                            confidence=best_score.final_score,
                            debug_json={
                                "top3_candidates": [
                                    {
                                        "topic_key": s.topic_key,
                                        "final_score": s.final_score,
                                        "heading_match_score": s.heading_match_score,
                                        "text_keywords_match_score": s.text_keywords_match_score,
                                        "source_zone_prior": s.source_zone_prior,
                                        "cluster_prior": s.cluster_prior,
                                        "signals": s.signals_json,
                                    }
                                    for s in block_scores[:3]
                                ],
                                "signals": best_score.signals_json,
                            },
                        )
                        assignments.append(assignment)
                else:
                    unmapped_headings.append(block.heading_text)
            else:
                unmapped_headings.append(block.heading_text)

        # 6. Вычисляем метрики
        metrics = self._calculate_metrics(
            blocks, all_scores, assignments, confidence_threshold, clustering_enabled
        )

        # 7. Сохраняем TopicMappingRun
        if apply:
            await self._save_mapping_run(
                doc_version_id=doc_version_id,
                mode=mode,
                metrics=metrics,
            )

        logger.info(
            f"Маппинг завершен: {len(assignments)} назначений, "
            f"mapped_rate={metrics.mapped_rate:.2%}"
        )

        return assignments, metrics

    async def _score_topics_for_block(
        self,
        block: HeadingBlock,
        topics: list[Topic],
        doc_type: DocumentType,
        zone_priors_by_topic: dict[str, dict[str, float]],
        topic_repo: TopicRepository,
        cluster_prior_topic_key: str | None = None,
    ) -> list[BlockTopicScore]:
        """Вычисляет score блока против всех топиков."""
        scores: list[BlockTopicScore] = []

        # Получаем текст для анализа
        heading_text = block.heading_text
        text_preview = block.text_preview

        for topic in topics:
            # Получаем профиль топика
            topic_profile = topic.topic_profile_json or {}
            profiles_by_doc_type = topic_profile.get("profiles_by_doc_type", {})
            doc_type_profile = profiles_by_doc_type.get(doc_type.value, {})

            # Объединяем базовый профиль с doc_type-специфичным
            effective_profile = {**topic_profile}
            if doc_type_profile:
                for key in ["aliases_ru", "aliases_en", "keywords_ru", "keywords_en", "headings_ru", "headings_en"]:
                    base_list = effective_profile.get(key, []) or []
                    doc_type_list = doc_type_profile.get(key, []) or []
                    effective_profile[key] = list(set(base_list + doc_type_list))
                for key in doc_type_profile:
                    if key not in ["aliases_ru", "aliases_en", "keywords_ru", "keywords_en", "headings_ru", "headings_en"]:
                        effective_profile[key] = doc_type_profile[key]

            # 1. Heading match score (aliases/regex)
            heading_match_score, heading_signals = self._calculate_heading_match_score(
                heading_text, effective_profile, block.language
            )

            # 2. Text keywords match score
            text_keywords_score, keywords_signals = self._calculate_text_keywords_score(
                heading_text, text_preview, effective_profile, block.language
            )

            # 3. Source zone prior
            zone_prior = 0.5
            topic_priors = zone_priors_by_topic.get(topic.topic_key, {})
            if block.source_zone.value in topic_priors:
                zone_prior = topic_priors[block.source_zone.value]
            else:
                # Используем старую логику из topic_profile
                topic_zones = effective_profile.get("source_zones", [])
                dissimilar_zones = effective_profile.get("dissimilar_zones", [])
                if block.source_zone.value in dissimilar_zones:
                    zone_prior = 0.2
                elif block.source_zone.value in topic_zones:
                    zone_prior = 0.8

            # 4. Cluster prior (если есть)
            cluster_prior = 0.0
            if cluster_prior_topic_key == topic.topic_key:
                cluster_prior = 0.3  # Буст от кластеризации

            # Финальный score
            final_score = (
                0.5 * max(heading_match_score, text_keywords_score * 0.7) +
                0.3 * zone_prior +
                0.2 * cluster_prior
            )

            # Буст для сильного heading match
            if heading_match_score > 0.7:
                final_score = min(final_score * 1.2, 1.0)

            signals_json = {
                "heading_match": heading_signals,
                "keywords_match": keywords_signals,
                "source_zone": block.source_zone.value,
                "zone_prior": zone_prior,
                "cluster_prior": cluster_prior,
            }

            scores.append(BlockTopicScore(
                topic_key=topic.topic_key,
                final_score=final_score,
                heading_match_score=heading_match_score,
                text_keywords_match_score=text_keywords_score,
                source_zone_prior=zone_prior,
                cluster_prior=cluster_prior,
                signals_json=signals_json,
            ))

        # Сортируем по final_score (убывание)
        scores.sort(key=lambda s: s.final_score, reverse=True)
        return scores

    def _calculate_heading_match_score(
        self,
        heading_text: str,
        topic_profile: dict[str, Any],
        language: DocumentLanguage,
    ) -> tuple[float, dict[str, Any]]:
        """Вычисляет score по exact/fuzzy match заголовка со aliases."""
        aliases_ru = topic_profile.get("aliases_ru", []) or topic_profile.get("headings_ru", [])
        aliases_en = topic_profile.get("aliases_en", []) or topic_profile.get("headings_en", [])

        best_match_ratio = 0.0
        best_match_alias = None

        aliases_to_check = []
        if language in (DocumentLanguage.RU, DocumentLanguage.MIXED):
            aliases_to_check.extend([(alias, "ru") for alias in aliases_ru])
        if language in (DocumentLanguage.EN, DocumentLanguage.MIXED):
            aliases_to_check.extend([(alias, "en") for alias in aliases_en])

        heading_norm = normalize_for_match(heading_text)

        for alias, lang in aliases_to_check:
            alias_norm = normalize_for_match(alias)

            if heading_norm == alias_norm:
                ratio = 1.0
            else:
                matcher = SequenceMatcher(None, heading_norm, alias_norm)
                ratio = matcher.ratio()

            if ratio > best_match_ratio:
                best_match_ratio = ratio
                best_match_alias = alias

        return best_match_ratio, {
            "best_match_ratio": best_match_ratio,
            "matched_alias": best_match_alias,
        }

    def _calculate_text_keywords_score(
        self,
        heading_text: str,
        text_preview: str,
        topic_profile: dict[str, Any],
        language: DocumentLanguage,
    ) -> tuple[float, dict[str, Any]]:
        """Вычисляет score по keyword match в тексте."""
        keywords_ru = topic_profile.get("keywords_ru", [])
        keywords_en = topic_profile.get("keywords_en", [])

        keywords_to_check = []
        if language in (DocumentLanguage.RU, DocumentLanguage.MIXED):
            keywords_to_check.extend(keywords_ru)
        if language in (DocumentLanguage.EN, DocumentLanguage.MIXED):
            keywords_to_check.extend(keywords_en)

        if not keywords_to_check:
            return 0.0, {"reason": "no_keywords"}

        text_to_search = f"{heading_text} {text_preview}".lower()
        text_norm = normalize_for_match(text_to_search)

        matched_keywords = []
        for keyword in keywords_to_check:
            keyword_norm = normalize_for_match(keyword)
            if keyword_norm in text_norm:
                matched_keywords.append(keyword)

        if not matched_keywords:
            return 0.0, {"matched_keywords": []}

        match_ratio = len(matched_keywords) / len(keywords_to_check)
        score = min(match_ratio * 0.8, 0.8)

        return score, {
            "matched_keywords": matched_keywords,
            "match_ratio": match_ratio,
        }

    async def _get_heading_anchor(self, anchor_id: str) -> Anchor | None:
        """Получает anchor по anchor_id."""
        stmt = select(Anchor).where(Anchor.anchor_id == anchor_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _create_block_assignment(
        self,
        doc_version_id: UUID,
        heading_block_id: str,
        topic_key: str,
        confidence: float,
        debug_json: dict[str, Any] | None = None,
    ) -> HeadingBlockTopicAssignment:
        """Создает или обновляет HeadingBlockTopicAssignment."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(HeadingBlockTopicAssignment).values(
            doc_version_id=doc_version_id,
            heading_block_id=heading_block_id,
            topic_key=topic_key,
            confidence=confidence,
            debug_json=debug_json,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_heading_block_assignments_doc_version_block",
            set_=dict(
                topic_key=stmt.excluded.topic_key,
                confidence=stmt.excluded.confidence,
                debug_json=stmt.excluded.debug_json,
            ),
        ).returning(HeadingBlockTopicAssignment)

        result = await self.db.execute(stmt)
        await self.db.commit()
        assignment = result.scalar_one()
        await self.db.refresh(assignment)
        return assignment

    def _calculate_metrics(
        self,
        blocks: list[HeadingBlock],
        all_scores: list[list[BlockTopicScore]],
        assignments: list[HeadingBlockTopicAssignment],
        confidence_threshold: float,
        clustering_enabled: bool,
    ) -> MappingMetrics:
        """Вычисляет метрики качества маппинга."""
        blocks_total = len(blocks)
        blocks_mapped = len(assignments)
        mapped_rate = blocks_mapped / blocks_total if blocks_total > 0 else 0.0

        low_confidence_count = 0
        for block_scores in all_scores:
            if block_scores and block_scores[0].final_score > 0 and block_scores[0].final_score < confidence_threshold:
                low_confidence_count += 1
        low_confidence_rate = low_confidence_count / blocks_total if blocks_total > 0 else 0.0

        # Unmapped top headings
        unmapped_headings = []
        for i, block_scores in enumerate(all_scores):
            if not block_scores or (block_scores and block_scores[0].final_score < confidence_threshold):
                unmapped_headings.append(blocks[i].heading_text)
        unmapped_top_headings = unmapped_headings[:10]

        # Topic coverage topN
        topic_counts: Counter[str] = Counter()
        for assignment in assignments:
            topic_counts[assignment.topic_key] += 1
        topic_coverage_topN = [
            {"topic_key": topic_key, "blocks_count": count}
            for topic_key, count in topic_counts.most_common(10)
        ]

        # Evidence by zone
        evidence_by_zone: Counter[str] = Counter()
        for block in blocks:
            evidence_by_zone[block.source_zone.value] += 1

        return MappingMetrics(
            blocks_total=blocks_total,
            blocks_mapped=blocks_mapped,
            mapped_rate=mapped_rate,
            low_confidence_rate=low_confidence_rate,
            unmapped_top_headings=unmapped_top_headings,
            topic_coverage_topN=topic_coverage_topN,
            evidence_by_zone=dict(evidence_by_zone),
            clustering_enabled=clustering_enabled,
        )

    async def _save_mapping_run(
        self,
        doc_version_id: UUID,
        mode: str,
        metrics: MappingMetrics,
    ) -> TopicMappingRun:
        """Сохраняет запись о запуске маппинга."""
        from app.services.ingestion.metrics import get_git_sha, hash_configs

        mapping_run = TopicMappingRun(
            doc_version_id=doc_version_id,
            mode=mode,
            pipeline_version=get_git_sha(),
            pipeline_config_hash=hash_configs(),
            params_json={},
            metrics_json={
                "blocks_total": metrics.blocks_total,
                "blocks_mapped": metrics.blocks_mapped,
                "mapped_rate": metrics.mapped_rate,
                "low_confidence_rate": metrics.low_confidence_rate,
                "unmapped_top_headings": metrics.unmapped_top_headings,
                "topic_coverage_topN": metrics.topic_coverage_topN,
                "evidence_by_zone": metrics.evidence_by_zone,
                "clustering": {
                    "enabled": metrics.clustering_enabled,
                    "clusters_total": metrics.clusters_total,
                    "clusters_labeled": metrics.clusters_labeled,
                    "avg_cluster_size": metrics.avg_cluster_size,
                } if metrics.clustering_enabled else None,
            },
        )
        self.db.add(mapping_run)
        await self.db.commit()
        await self.db.refresh(mapping_run)
        return mapping_run

