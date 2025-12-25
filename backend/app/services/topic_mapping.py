"""Сервис для автоматического маппинга блоков заголовков на топики (новая архитектура).

В новой архитектуре:
- Блоки (heading blocks) маппятся напрямую на топики
- Кластеризация опциональна и используется только как prior
- topic_evidence строится из block assignments напрямую
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

from app.core.config import LLMProvider, settings
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
    embedding_similarity_score: float = 0.0  # Оценка семантического сходства
    neighbor_bonus: float = 0.0  # Бонус от соседнего блока
    zone_penalty: float = 1.0  # Штраф за несовместимую зону (множитель)
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
        confidence_threshold: float = 0.55,
        zone_match_threshold_boost: float = 0.15,  # Снижение threshold при совпадении по source_zone
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

        # Подсчитываем топики с эмбеддингами для логирования
        topics_with_embeddings = sum(1 for topic in topics if topic.topic_embedding is not None)
        logger.debug(
            f"Direct mapping start. Blocks: {len(blocks)}, "
            f"Topics with embeddings: {topics_with_embeddings}/{len(topics)}"
        )

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

        # 5. Маппим каждый блок на топики (с отслеживанием предыдущего замаппленного блока)
        assignments: list[HeadingBlockTopicAssignment] = []
        all_scores: list[list[BlockTopicScore]] = []
        unmapped_headings: list[str] = []
        previous_mapped_topic: str | None = None  # Для бонуса соседства

        for block in blocks:
            block_scores = await self._score_topics_for_block(
                block, topics, doc_type, zone_priors_by_topic, topic_repo, 
                cluster_prior_map.get(block.heading_block_id),
                previous_mapped_topic,
            )
            all_scores.append(block_scores)

            if block_scores:
                best_score = block_scores[0]
                
                # Динамический threshold: если source_zone блока СОВПАДАЕТ с разрешенными зонами топика,
                # снижаем порог до 0.45 (мы доверяем классификатору зон)
                effective_threshold = confidence_threshold
                # Проверяем has_strong_zone_match из signals_json (устанавливается при совпадении source_zone с topic_zones)
                if best_score.signals_json and best_score.signals_json.get("has_strong_zone_match", False):
                    effective_threshold = 0.45

                if best_score.final_score >= effective_threshold:
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
                                        "embedding_similarity_score": s.embedding_similarity_score,
                                        "neighbor_bonus": s.neighbor_bonus,
                                        "zone_penalty": s.zone_penalty,
                                        "signals": s.signals_json,
                                    }
                                    for s in block_scores[:3]
                                ],
                                "signals": best_score.signals_json,
                            },
                        )
                        assignments.append(assignment)
                        # Обновляем предыдущий замаппленный топик для бонуса соседства
                        previous_mapped_topic = best_score.topic_key
                else:
                    title_preview = block.heading_text[:40] + "..." if len(block.heading_text) > 40 else block.heading_text
                    logger.debug(
                        f"Block '{title_preview}' rejected. "
                        f"Best score {best_score.final_score:.3f} < threshold {effective_threshold:.3f} "
                        f"(base: {confidence_threshold})"
                    )
                    unmapped_headings.append(block.heading_text)
                    previous_mapped_topic = None  # Сбрасываем, если блок не замапплен
            else:
                unmapped_headings.append(block.heading_text)
                previous_mapped_topic = None  # Сбрасываем, если блок не замапплен

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
        previous_mapped_topic: str | None = None,
    ) -> list[BlockTopicScore]:
        """Вычисляет score блока против всех топиков."""
        scores: list[BlockTopicScore] = []

        # Получаем текст для анализа
        heading_text = block.heading_text
        text_preview = block.text_preview
        
        # Получаем первые два предложения текста блока для расширенного семантического сравнения
        first_two_sentences = await self._get_first_two_sentences(block)

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

            # 0. Проверка отрицательных паттернов (исключений)
            if self._check_exclude_patterns(heading_text, effective_profile, block.language):
                # Если найден исключающий паттерн, пропускаем этот топик
                continue

            # 1. Heading match score (aliases/regex)
            # При language='ru' также используем topic.title_ru для нечеткого поиска
            heading_match_score, heading_signals = self._calculate_heading_match_score(
                heading_text, effective_profile, block.language, topic.title_ru
            )

            # 2. Text keywords match score
            # При language='ru' также проверяем слова из topic.title_ru
            text_keywords_score, keywords_signals = self._calculate_text_keywords_score(
                heading_text, text_preview, effective_profile, block.language, topic.title_ru
            )

            # 3. Source zone prior и штраф за зону
            zone_prior = 0.5
            zone_penalty = 1.0  # Множитель штрафа (1.0 = без штрафа)
            has_strong_zone_match = False  # Флаг для динамического threshold
            topic_priors = zone_priors_by_topic.get(topic.topic_key, {})
            # Получаем разрешенные зоны из topic_profile для использования в бусте
            topic_zones = effective_profile.get("source_zones", [])
            dissimilar_zones = effective_profile.get("dissimilar_zones", [])
            
            if block.source_zone.value in topic_priors:
                zone_prior = topic_priors[block.source_zone.value]
                # Если weight высокий (>= 0.7), это сильный сигнал для снижения threshold
                if zone_prior >= 0.7:
                    has_strong_zone_match = True
                # Если weight очень низкий (< 0.2), применяем штраф 80% (умножаем confidence на 0.2)
                if zone_prior < 0.2:
                    zone_penalty = 0.2  # Снижаем confidence на 80%
            else:
                # Используем старую логику из topic_profile
                if block.source_zone.value in dissimilar_zones:
                    zone_prior = 0.2
                    zone_penalty = 0.2  # Штраф за несовместимую зону
                elif block.source_zone.value in topic_zones:
                    zone_prior = 0.8
                    has_strong_zone_match = True  # Сильный сигнал от source_zone
            
            # Буст для MVP: если source_zone блока СОВПАДАЕТ с разрешенными зонами топика
            # (из topic_profile_json или topic_priors), даем значительный буст к итоговому score (0.3 -> 0.5)
            # Это компенсирует возможный низкий embedding similarity из-за разницы языков
            if block.source_zone.value in topic_zones or (block.source_zone.value in topic_priors and topic_priors[block.source_zone.value] >= 0.3):
                if zone_prior < 0.5:
                    zone_prior = 0.5

            # 4. Cluster prior (если есть)
            cluster_prior = 0.0
            if cluster_prior_topic_key == topic.topic_key:
                cluster_prior = 0.3  # Буст от кластеризации

            # 5. Embedding similarity (сравнение с первыми двумя предложениями)
            # Проверка наличия эмбеддинга перед вычислением
            if topic.topic_embedding is None:
                logger.warning(
                    f"Missing embedding for Topic '{topic.topic_key}'. "
                    f"Semantic matching skipped for Block '{heading_text[:40]}...'"
                )
            embedding_similarity_score = await self._calculate_embedding_similarity(
                topic, heading_text, first_two_sentences
            )

            # 6. Бонус соседства
            neighbor_bonus = 0.0
            if previous_mapped_topic == topic.topic_key:
                # Если заголовок неясный (низкий heading_match_score и text_keywords_score)
                if heading_match_score < 0.4 and text_keywords_score < 0.4:
                    neighbor_bonus = 0.2  # Даем бонус +0.2 к скору

            # Финальный score с новыми весами для Master-топиков:
            # - Embedding Similarity (семантика): 0.5 (если доступен)
            # - Alias Match (ключевые слова): 0.3
            # - Source Zone Prior (соответствие зоне): 0.2
            # Если embedding similarity недоступен (0.0), перераспределяем веса на другие компоненты
            if embedding_similarity_score > 0:
                # Нормальный режим с эмбеддингами
                base_score = (
                    0.5 * embedding_similarity_score +
                    0.3 * max(heading_match_score, text_keywords_score * 0.7) +
                    0.2 * zone_prior +
                    0.0 * cluster_prior  # Cluster prior больше не используется в базовом скоре
                )
            else:
                # Режим без эмбеддингов: перераспределяем веса
                # - Alias Match: 0.5 (вместо 0.3)
                # - Source Zone Prior: 0.3 (вместо 0.2)
                # - Cluster Prior: 0.2 (если есть)
                cluster_weight = 0.2 if cluster_prior > 0 else 0.0
                alias_weight = 0.5 if cluster_weight == 0 else 0.4
                zone_weight = 0.3 if cluster_weight == 0 else 0.3
                base_score = (
                    alias_weight * max(heading_match_score, text_keywords_score * 0.7) +
                    zone_weight * zone_prior +
                    cluster_weight * cluster_prior
                )
            
            # Добавляем бонус соседства
            final_score = min(base_score + neighbor_bonus, 1.0)

            # Буст для сильного heading match (применяем до штрафа)
            if heading_match_score > 0.7:
                final_score = min(final_score * 1.2, 1.0)
            
            # Применяем штраф за зону (в конце, чтобы снизить confidence на 80% при несовместимых зонах)
            final_score = final_score * zone_penalty

            signals_json = {
                "heading_match": heading_signals,
                "keywords_match": keywords_signals,
                "source_zone": block.source_zone.value,
                "zone_prior": zone_prior,
                "zone_penalty": zone_penalty,
                "has_strong_zone_match": has_strong_zone_match,
                "cluster_prior": cluster_prior,
                "embedding_similarity": embedding_similarity_score,
                "neighbor_bonus": neighbor_bonus,
            }
            
            scores.append(BlockTopicScore(
                topic_key=topic.topic_key,
                final_score=final_score,
                heading_match_score=heading_match_score,
                text_keywords_match_score=text_keywords_score,
                source_zone_prior=zone_prior,
                cluster_prior=cluster_prior,
                embedding_similarity_score=embedding_similarity_score,
                neighbor_bonus=neighbor_bonus,
                zone_penalty=zone_penalty,
                signals_json=signals_json,
            ))

        # Сортируем по final_score (убывание)
        scores.sort(key=lambda s: s.final_score, reverse=True)
        
        # Логируем ТОП-3 кандидатов для каждого блока
        title_preview = heading_text[:40] + "..." if len(heading_text) > 40 else heading_text
        for idx, score in enumerate(scores[:3], 1):
            logger.debug(
                f"Mapping Block '{title_preview}' -> TOP{idx} Candidate '{score.topic_key}': "
                f"[Final: {score.final_score:.3f}] "
                f"(Semantic: {score.embedding_similarity_score:.3f}, "
                f"Alias: {score.heading_match_score:.2f}, "
                f"Zone: {score.source_zone_prior:.2f})"
            )
        
        # Специальное логирование для заголовка "Дизайн исследования"
        heading_normalized = normalize_for_match(heading_text).strip()
        if heading_normalized == normalize_for_match("Дизайн исследования").strip():
            logger.info("=" * 80)
            logger.info(f"ТОП-3 кандидата-топика для заголовка 'Дизайн исследования':")
            for idx, score in enumerate(scores[:3], 1):
                logger.info(
                    f"  {idx}. Топик: {score.topic_key} | "
                    f"Итоговый score: {score.final_score:.3f} | "
                    f"heading_score: {score.heading_match_score:.3f} | "
                    f"keyword_score: {score.text_keywords_match_score:.3f} | "
                    f"embedding_score: {score.embedding_similarity_score:.3f} | "
                    f"zone_prior: {score.source_zone_prior:.3f}"
                )
            logger.info("=" * 80)
        
        return scores

    def _calculate_heading_match_score(
        self,
        heading_text: str,
        topic_profile: dict[str, Any],
        language: DocumentLanguage,
        topic_title_ru: str | None = None,
    ) -> tuple[float, dict[str, Any]]:
        """
        Вычисляет score по exact/fuzzy match заголовка со aliases.
        
        При language='ru' также проверяет нечеткое совпадение с topic.title_ru,
        если aliases не дали результата или дали слабый результат.
        """
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

        # Дополнительная проверка: если language='ru' и title_ru существует,
        # делаем нечеткий поиск по title_ru (если aliases не дали хорошего результата)
        if (
            language in (DocumentLanguage.RU, DocumentLanguage.MIXED)
            and topic_title_ru
            and best_match_ratio < 0.7  # Используем title_ru только если aliases не дали сильного совпадения
        ):
            title_ru_norm = normalize_for_match(topic_title_ru)
            if heading_norm == title_ru_norm:
                title_ratio = 1.0
            else:
                matcher = SequenceMatcher(None, heading_norm, title_ru_norm)
                title_ratio = matcher.ratio()
            
            # Используем title_ru только если он дает лучший результат
            if title_ratio > best_match_ratio:
                best_match_ratio = title_ratio
                best_match_alias = topic_title_ru

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
        topic_title_ru: str | None = None,
    ) -> tuple[float, dict[str, Any]]:
        """
        Вычисляет score по keyword match в тексте.
        
        При language='ru' также проверяет наличие слов из topic.title_ru в тексте,
        если keywords не дали результата.
        """
        keywords_ru = topic_profile.get("keywords_ru", [])
        keywords_en = topic_profile.get("keywords_en", [])

        keywords_to_check = []
        if language in (DocumentLanguage.RU, DocumentLanguage.MIXED):
            keywords_to_check.extend(keywords_ru)
        if language in (DocumentLanguage.EN, DocumentLanguage.MIXED):
            keywords_to_check.extend(keywords_en)

        # Если keywords пустые, но есть title_ru для русского языка, используем его как fallback
        if not keywords_to_check and language in (DocumentLanguage.RU, DocumentLanguage.MIXED) and topic_title_ru:
            # Разбиваем title_ru на слова (игнорируем стоп-слова)
            title_words = [w for w in normalize_for_match(topic_title_ru).split() if len(w) > 3]
            keywords_to_check.extend(title_words)

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

    def _check_exclude_patterns(
        self,
        heading_text: str,
        topic_profile: dict[str, Any],
        language: DocumentLanguage,
    ) -> bool:
        """
        Проверяет отрицательные паттерны (исключения) в заголовке.
        
        Возвращает True, если найден исключающий паттерн (топик НЕ должен использоваться).
        """
        exclude_patterns_ru = topic_profile.get("exclude_patterns_ru", [])
        exclude_patterns_en = topic_profile.get("exclude_patterns_en", [])

        patterns_to_check = []
        if language in (DocumentLanguage.RU, DocumentLanguage.MIXED):
            patterns_to_check.extend(exclude_patterns_ru)
        if language in (DocumentLanguage.EN, DocumentLanguage.MIXED):
            patterns_to_check.extend(exclude_patterns_en)

        if not patterns_to_check:
            return False

        heading_norm = normalize_for_match(heading_text.lower())

        for pattern in patterns_to_check:
            pattern_norm = normalize_for_match(pattern.lower())
            # Проверяем точное вхождение или подстроку
            if pattern_norm in heading_norm:
                return True

        return False

    async def _get_first_two_sentences(self, block: HeadingBlock) -> str:
        """
        Извлекает первые два предложения из текста блока.
        
        Использует content_anchor_ids для получения текста из anchors.
        """
        if not block.content_anchor_ids:
            return ""

        # Загружаем anchors блока (только первые несколько для производительности)
        anchor_ids_to_load = block.content_anchor_ids[:5]  # Берем первые 5 anchors
        stmt = select(Anchor).where(Anchor.anchor_id.in_(anchor_ids_to_load))
        result = await self.db.execute(stmt)
        anchors = list(result.scalars().all())

        if not anchors:
            return ""

        # Собираем весь текст из anchors
        full_text = " ".join(anchor.text_raw for anchor in anchors if anchor.text_raw)

        # Извлекаем первые два предложения
        # Используем простое регулярное выражение для разделения по предложениям
        sentences = re.split(r'[.!?]+\s+', full_text)
        first_two = " ".join(sentences[:2]).strip()
        
        # Добавляем точку в конце, если её нет
        if first_two and not first_two[-1] in '.!?':
            first_two += "."

        return first_two

    async def _calculate_embedding_similarity(
        self,
        topic: Topic,
        heading_text: str,
        first_two_sentences: str,
    ) -> float:
        """
        Вычисляет семантическое сходство через embedding.
        
        Сравнивает topic_embedding с заголовком и первыми двумя предложениями текста блока.
        
        Используется модель text-embedding-3-small (OpenAI), которая является мультиязычной.
        Однако, если topic_embedding был создан из английского текста, а текст блока на русском,
        сходство может быть ниже. Рекомендуется создавать topic_embedding из описаний на том же языке,
        что и целевые документы, или использовать мультиязычные описания.
        
        Возвращает score от 0.0 до 1.0.
        """
        # Если у топика нет embedding, возвращаем 0.0
        # (WARNING уже выведен в _score_topics_for_block перед вызовом)
        if topic.topic_embedding is None:
            return 0.0
        
        # Проверяем, что topic_embedding - это список float
        if not isinstance(topic.topic_embedding, list):
            logger.warning(
                f"topic_embedding для топика {topic.topic_key} имеет неожиданный тип: "
                f"{type(topic.topic_embedding)}. Semantic matching skipped."
            )
            return 0.0
        
        # Генерируем эмбеддинг для заголовка и первых двух предложений
        text_to_embed = f"{heading_text}. {first_two_sentences}".strip()
        if not text_to_embed:
            logger.warning(
                f"Missing text for Block '{heading_text[:40]}...'. "
                f"Cannot generate embedding. Semantic matching skipped for Topic '{topic.topic_key}'."
            )
            return 0.0
        
        try:
            cluster_embedding = await self._generate_embedding(text_to_embed)
            if cluster_embedding is None:
                return 0.0
            
            # Вычисляем cosine similarity между topic_embedding и cluster_embedding
            similarity = self._cosine_similarity(topic.topic_embedding, cluster_embedding)
            return similarity
        except Exception as e:
            logger.warning(f"Ошибка при вычислении embedding similarity для топика {topic.topic_key}: {e}")
            return 0.0
    
    def _get_embedding_dimension(self, model: str, provider: LLMProvider) -> int:
        """
        Определяет размерность эмбеддинга для модели.
        
        Args:
            model: Название модели
            provider: Провайдер LLM
        
        Returns:
            Размерность эмбеддинга (по умолчанию 1536)
        """
        # YandexGPT использует 256 измерений
        if provider == LLMProvider.YANDEXGPT:
            return 256
        # OpenAI text-embedding-3-small использует 1536 измерений
        if "text-embedding-3-small" in model:
            return 1536
        # OpenAI text-embedding-3-large использует 3072 измерения
        if "text-embedding-3-large" in model:
            return 3072
        # По умолчанию 1536 (для text-embedding-ada-002 и других)
        return 1536

    async def _generate_embedding(
        self,
        text: str,
        model: str = "text-embedding-3-small",
    ) -> list[float] | None:
        """
        Генерирует эмбеддинг текста через OpenAI API или совместимый сервис.
        
        Args:
            text: Текст для векторизации
            model: Модель для эмбеддингов (по умолчанию text-embedding-3-small)
                  Для YandexGPT: используется OpenAI-совместимый API
                  https://yandex.cloud/ru/docs/ai-studio/concepts/openai-compatibility
                  Модель автоматически преобразуется в формат gpt://folder-id/text-embedding-ada-002
                  на основе folder-id из настройки LLM_MODEL
        
        Returns:
            Список float значений эмбеддинга или нулевой вектор при ошибке после всех попыток.
            Размерность зависит от модели:
            - OpenAI text-embedding-3-small: 1536 измерений
            - YandexGPT text-search-doc/latest: 256 измерений
        """
        if not settings.llm_provider or not settings.llm_base_url or not settings.llm_api_key:
            logger.debug("LLM настройки не заданы, пропускаем генерацию эмбеддинга")
            return None
        
        # Определяем размерность для нулевого вектора при ошибке
        embedding_dim = self._get_embedding_dimension(model, settings.llm_provider)
        
        # Нормализуем base_url
        base_url = settings.llm_base_url.rstrip("/")
        if settings.llm_provider == LLMProvider.OPENAI_COMPATIBLE and base_url.endswith("/v1"):
            base_url = base_url[: -len("/v1")]
        
        # Формируем URL и заголовки в зависимости от провайдера
        if settings.llm_provider == LLMProvider.AZURE_OPENAI:
            url = f"{base_url}/openai/deployments/{model}/embeddings"
            headers = {
                "api-key": settings.llm_api_key,
                "Content-Type": "application/json",
            }
            payload = {"input": text}
        elif settings.llm_provider == LLMProvider.YANDEXGPT:
            # YandexGPT поддерживает OpenAI-совместимый API для эмбеддингов
            # https://yandex.cloud/ru/docs/ai-studio/concepts/openai-compatibility
            # Endpoint: https://llm.api.cloud.yandex.net/v1/embeddings
            if not base_url or base_url == "https://llm.api.cloud.yandex.net":
                url = "https://llm.api.cloud.yandex.net/v1/embeddings"
            else:
                url = f"{base_url.rstrip('/')}/v1/embeddings"
            headers = {
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
            }
            # Для YandexGPT используем модель в формате emb://folder-id/text-search-doc/latest
            # Согласно документации YandexGPT:
            # - Для документов: emb://{folder-id}/text-search-doc/latest
            # - Для запросов: emb://{folder-id}/text-search-query/latest
            # Используем text-search-doc для генерации эмбеддингов топиков и кластеров
            model_uri = None
            
            if model.startswith("emb://"):
                # Уже в правильном формате emb://folder-id/text-search-doc/latest или emb://folder-id/text-search-query/latest
                model_uri = model
            elif model.startswith("gpt://"):
                # Формат gpt://folder-id/model-name - извлекаем folder-id и используем схему emb
                folder_id = model.replace("gpt://", "").split("/")[0]
                # Используем text-search-doc для документов (топики, кластеры)
                model_uri = f"emb://{folder_id}/text-search-doc/latest"
            elif "/" in model and not model.startswith("text-embedding"):
                # Модель в формате folder-id/model-name (например, folder-id/yandexgpt/latest)
                # Извлекаем folder-id и используем для эмбеддингов
                folder_id = model.split("/")[0]
                # Используем text-search-doc для документов
                model_uri = f"emb://{folder_id}/text-search-doc/latest"
            elif model.startswith("text-embedding"):
                # Модель эмбеддингов без folder-id - пытаемся извлечь из настроек llm_model
                if settings.llm_model:
                    # Проверяем разные форматы llm_model
                    if settings.llm_model.startswith("gpt://"):
                        # Формат gpt://folder-id/yandexgpt/latest
                        folder_id = settings.llm_model.replace("gpt://", "").split("/")[0]
                        model_uri = f"emb://{folder_id}/text-search-doc/latest"
                    elif settings.llm_model.startswith("emb://"):
                        # Уже в формате emb://folder-id/...
                        model_uri = settings.llm_model
                    elif "/" in settings.llm_model:
                        # Формат folder-id/yandexgpt/latest
                        folder_id = settings.llm_model.split("/")[0]
                        model_uri = f"emb://{folder_id}/text-search-doc/latest"
                    else:
                        # Предполагаем, что llm_model - это folder-id
                        model_uri = f"emb://{settings.llm_model}/text-search-doc/latest"
                
                if not model_uri:
                    logger.error(
                        f"Не удалось определить folder-id для YandexGPT эмбеддингов. "
                        f"Модель: {model}, LLM_MODEL: {settings.llm_model}. "
                        f"Убедитесь, что LLM_MODEL содержит folder-id в формате 'folder-id/yandexgpt/latest' "
                        f"или 'gpt://folder-id/yandexgpt/latest'"
                    )
                    return None
            else:
                # Предполагаем, что это folder-id, используем формат emb://folder-id/text-search-doc/latest
                model_uri = f"emb://{model}/text-search-doc/latest"
            
            payload = {
                "model": model_uri,
                "input": text,
            }
            
            # Логируем для отладки
            logger.debug(
                f"YandexGPT embeddings: model={model}, llm_model={settings.llm_model}, "
                f"model_uri={model_uri}"
            )
        elif settings.llm_provider in (LLMProvider.OPENAI_COMPATIBLE, LLMProvider.LOCAL):
            url = f"{base_url}/v1/embeddings"
            headers = {"Content-Type": "application/json"}
            if settings.llm_api_key:
                headers["Authorization"] = f"Bearer {settings.llm_api_key}"
            payload = {
                "model": model,
                "input": text,
            }
        else:
            logger.debug(f"Провайдер {settings.llm_provider} не поддерживает генерацию эмбеддингов")
            return None
        
        # Внутренняя функция для выполнения HTTP-запроса с retry
        # Определяем после формирования url, headers, payload
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type((
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.NetworkError,
            )),
            reraise=False,
        )
        async def _make_embedding_request() -> list[float] | None:
            """Выполняет HTTP-запрос для генерации эмбеддинга с автоматическими повторами."""
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                
                # Извлекаем эмбеддинг из ответа (OpenAI-совместимый формат для всех провайдеров)
                # https://yandex.cloud/ru/docs/ai-studio/concepts/openai-compatibility
                if "data" in data and len(data["data"]) > 0:
                    embedding = data["data"][0]["embedding"]
                    if isinstance(embedding, list) and len(embedding) > 0:
                        return embedding
                    else:
                        logger.warning(f"Неожиданный формат эмбеддинга: {type(embedding)}")
                        return None
                else:
                    logger.warning(f"Пустой ответ от API: {data}")
                    return None
        
        try:
            # Выполняем запрос с автоматическими повторами
            embedding = await _make_embedding_request()
            if embedding is not None:
                return embedding
        except RetryError as e:
            # Все попытки исчерпаны - логируем и возвращаем нулевой вектор
            logger.error(
                f"Все попытки генерации эмбеддинга исчерпаны после 3 попыток "
                f"(provider={settings.llm_provider}, model={model}): {e.last_attempt.exception()}"
            )
        except httpx.HTTPStatusError as e:
            # HTTP ошибки (4xx, 5xx) не повторяем, но возвращаем нулевой вектор
            error_body = ""
            try:
                if e.response is not None:
                    error_body = e.response.text[:1000]
            except Exception:
                pass
            logger.warning(
                f"Ошибка HTTP {e.response.status_code if e.response else 'unknown'} при генерации эмбеддинга "
                f"(provider={settings.llm_provider}, model={model}): {error_body}"
            )
            # Для YandexGPT логируем также payload для отладки (без секретных данных)
            if settings.llm_provider == LLMProvider.YANDEXGPT:
                payload_model = payload.get('model') if isinstance(payload, dict) else None
                logger.debug(
                    f"YandexGPT embeddings request details: url={url}, "
                    f"model={payload_model}, "
                    f"input_length={len(text) if text else 0}, "
                    f"payload_keys={list(payload.keys()) if isinstance(payload, dict) else 'N/A'}"
                )
                if not payload_model:
                    logger.error(
                        f"YandexGPT embeddings: model_uri не сформирован! "
                        f"model={model}, llm_model={settings.llm_model}, payload={payload}"
                    )
        except Exception as e:
            # Другие неожиданные ошибки - логируем и возвращаем нулевой вектор
            logger.warning(f"Неожиданная ошибка при генерации эмбеддинга: {e}")
        
        # Возвращаем нулевой вектор вместо None, чтобы не прерывать процесс ингестии
        logger.info(
            f"Возвращаю нулевой вектор размерности {embedding_dim} "
            f"для текста длиной {len(text)} символов"
        )
        return [0.0] * embedding_dim
    
    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """
        Вычисляет cosine similarity между двумя векторами.
        
        Args:
            vec_a: Первый вектор
            vec_b: Второй вектор
        
        Returns:
            Cosine similarity от 0.0 до 1.0
        
        Note:
            Если размерности не совпадают (например, 1536 vs 256 для разных моделей эмбеддингов),
            возвращается 0.0. Это может происходить при переходе с OpenAI (1536) на YandexGPT (256).
            Рекомендуется пересоздать эмбеддинги топиков с новой моделью для корректной работы.
        """
        if len(vec_a) != len(vec_b):
            # Логируем только один раз для каждого уникального сочетания размерностей
            # чтобы не засорять логи повторяющимися сообщениями
            if not hasattr(self, '_dimension_warnings'):
                self._dimension_warnings: set[tuple[int, int]] = set()
            
            dim_pair = tuple(sorted([len(vec_a), len(vec_b)]))
            if dim_pair not in self._dimension_warnings:
                self._dimension_warnings.add(dim_pair)
                logger.warning(
                    f"Размерности векторов не совпадают: {len(vec_a)} != {len(vec_b)}. "
                    f"Это может быть из-за использования разных моделей эмбеддингов "
                    f"(OpenAI: 1536, YandexGPT: 256). Semantic similarity будет 0.0. "
                    f"Рекомендуется пересоздать эмбеддинги топиков с текущей моделью."
                )
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        
        similarity = dot_product / (norm_a * norm_b)
        # Ограничиваем значение в диапазоне [0, 1]
        return max(0.0, min(1.0, similarity))

    def _convert_to_json_serializable(self, obj: Any) -> Any:
        """
        Конвертирует numpy типы и другие не-JSON-сериализуемые типы в стандартные Python типы.
        
        Args:
            obj: Объект для конвертации
            
        Returns:
            JSON-сериализуемый объект
        """
        try:
            import numpy as np
            
            if isinstance(obj, (np.integer, np.floating)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        
        if isinstance(obj, dict):
            return {key: self._convert_to_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_to_json_serializable(item) for item in obj]
        elif isinstance(obj, float):
            # Обработка NaN и Infinity
            if obj != obj or obj == float('inf') or obj == float('-inf'):
                return None
            return obj
        else:
            return obj

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

        # Конвертируем confidence и debug_json в JSON-сериализуемые типы
        confidence_serializable = float(confidence)
        debug_json_serializable = self._convert_to_json_serializable(debug_json) if debug_json else None

        stmt = pg_insert(HeadingBlockTopicAssignment).values(
            doc_version_id=doc_version_id,
            heading_block_id=heading_block_id,
            topic_key=topic_key,
            confidence=confidence_serializable,
            debug_json=debug_json_serializable,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_heading_block_assignments_doc_version_block",
            set_=dict(
                topic_key=stmt.excluded.topic_key,
                confidence=stmt.excluded.confidence,
                debug_json=stmt.excluded.debug_json,
            ),
        ).returning(HeadingBlockTopicAssignment)
        
        # Примечание: в on_conflict_do_update мы используем stmt.excluded, который уже содержит
        # сериализуемые значения из .values(), поэтому дополнительная конвертация не нужна

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

