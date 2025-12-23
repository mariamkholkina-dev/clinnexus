"""Сервис для автоматического маппинга кластеров заголовков на топики."""
from __future__ import annotations

import numpy as np
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import DocumentLanguage, SourceZone
from app.db.models.anchors import Anchor, Chunk
from app.db.models.topics import ClusterAssignment, HeadingCluster, Topic, TopicMappingRun
from app.services.source_zone_classifier import get_classifier
from app.services.text_normalization import normalize_for_match, normalize_for_regex


@dataclass
class TopicScore:
    """Оценка соответствия кластера топику."""
    
    topic_key: str
    final_score: float
    rule_score: float = 0.0
    alias_match_score: float = 0.0
    keyword_match_score: float = 0.0
    embedding_score: float = 0.0
    source_zone_prior: float = 0.0
    explanation: dict[str, Any] | None = None


@dataclass
class MappingMetrics:
    """Метрики качества маппинга."""
    
    coverage: float  # % кластеров с confidence >= threshold
    ambiguity: float  # % кластеров где top1-top2 < delta
    fallback_rate: float  # % кластеров где сработал только keyword-match
    conflict_rate: float  # % кластеров помеченных dissimilar-конфликтом


class TopicMappingService:
    """Сервис для маппинга кластеров заголовков на топики."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.source_zone_classifier = get_classifier()

    async def map_topics_for_doc_version(
        self,
        doc_version_id: UUID,
        mode: str = "auto",
        apply: bool = True,
        confidence_threshold: float = 0.65,
        ambiguity_delta: float = 0.08,
    ) -> tuple[list[ClusterAssignment], MappingMetrics]:
        """
        Выполняет маппинг кластеров на топики для версии документа.

        Args:
            doc_version_id: ID версии документа
            mode: Режим маппинга ("auto" или "assist")
            apply: Сохранять ли результаты в БД
            confidence_threshold: Порог confidence для coverage метрики
            ambiguity_delta: Минимальная разница между top1 и top2 для ambiguity метрики

        Returns:
            Кортеж (список назначений, метрики)
        """
        logger.info(
            f"Начало маппинга топиков для doc_version_id={doc_version_id}, mode={mode}, apply={apply}"
        )

        # 1. Загружаем кластеры заголовков
        stmt = select(HeadingCluster).where(
            HeadingCluster.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        clusters = list(result.scalars().all())

        if not clusters:
            logger.warning(f"Не найдено кластеров для doc_version_id={doc_version_id}")
            return [], MappingMetrics(coverage=0.0, ambiguity=0.0, fallback_rate=0.0, conflict_rate=0.0)

        logger.info(f"Найдено {len(clusters)} кластеров")

        # 2. Загружаем активные топики (для workspace документа)
        from app.db.models.studies import DocumentVersion
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найдена")

        # Получаем workspace_id из документа
        from app.db.models.studies import Document
        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")

        from app.services.topic_repository import TopicRepository
        topic_repo = TopicRepository(self.db)
        topics = await topic_repo.list_topics(workspace_id=document.workspace_id, is_active=True)

        logger.info(f"Найдено {len(topics)} активных топиков")

        # 3. Для каждого кластера считаем score для каждого топика
        assignments: list[ClusterAssignment] = []
        all_scores: list[list[TopicScore]] = []

        for cluster in clusters:
            cluster_scores = await self._score_cluster_against_topics(cluster, topics)
            all_scores.append(cluster_scores)

            # Берем топ-1 топик
            if cluster_scores:
                best_score = cluster_scores[0]
                
                if apply:
                    assignment = await self._create_assignment(
                        doc_version_id=doc_version_id,
                        cluster_id=cluster.cluster_id,
                        topic_key=best_score.topic_key,
                        confidence=best_score.final_score,
                        mapped_by=mode,
                        mapping_debug_json={
                            "top3_candidates": [
                                {
                                    "topic_key": s.topic_key,
                                    "final_score": s.final_score,
                                    "rule_score": s.rule_score,
                                    "alias_match_score": s.alias_match_score,
                                    "keyword_match_score": s.keyword_match_score,
                                    "embedding_score": s.embedding_score,
                                    "source_zone_prior": s.source_zone_prior,
                                    "explanation": s.explanation,
                                }
                                for s in cluster_scores[:3]
                            ],
                            "explanation": best_score.explanation,
                        },
                    )
                    assignments.append(assignment)

        # 4. Вычисляем метрики
        metrics = self._calculate_metrics(
            all_scores,
            confidence_threshold=confidence_threshold,
            ambiguity_delta=ambiguity_delta,
        )

        # 5. Сохраняем TopicMappingRun
        if apply:
            await self._save_mapping_run(
                doc_version_id=doc_version_id,
                mode=mode,
                metrics=metrics,
            )

        logger.info(
            f"Маппинг завершен: {len(assignments)} назначений, "
            f"coverage={metrics.coverage:.2%}, ambiguity={metrics.ambiguity:.2%}"
        )

        return assignments, metrics

    async def _score_cluster_against_topics(
        self,
        cluster: HeadingCluster,
        topics: list[Topic],
    ) -> list[TopicScore]:
        """Вычисляет score кластера против всех топиков."""
        scores: list[TopicScore] = []

        # Получаем основные данные кластера
        top_titles = cluster.top_titles_json or []
        cluster_lang = cluster.language.value
        examples = cluster.examples_json or []

        # Определяем source_zone кластера (из examples)
        cluster_source_zone = self._detect_cluster_source_zone(examples)

        # Получаем window текст для keyword matching (если доступно)
        window_text = await self._get_window_text_for_cluster(cluster)

        for topic in topics:
            topic_profile = topic.topic_profile_json or {}
            
            # 2.1 Rule-based score
            alias_score, alias_explanation = self._calculate_alias_match_score(
                top_titles, topic_profile, cluster_lang
            )
            
            keyword_score, keyword_explanation = self._calculate_keyword_match_score(
                top_titles, window_text, topic_profile, cluster_lang
            )
            
            rule_score = max(alias_score, keyword_score * 0.7)  # alias более важен

            # 2.2 Embedding score
            embedding_score = 0.0
            embedding_explanation: dict[str, Any] | None = None
            if cluster.cluster_embedding is not None and topic.topic_embedding is not None:
                embedding_score = self._calculate_embedding_similarity(
                    cluster.cluster_embedding, topic.topic_embedding
                )
                embedding_explanation = {"similarity": embedding_score}

            # 2.3 Source zone prior
            source_zone_prior, zone_explanation = self._calculate_source_zone_prior(
                cluster_source_zone, topic_profile, topic.topic_key
            )

            # Финальный score (взвешенная сумма)
            final_score = (
                0.4 * rule_score +
                0.3 * embedding_score +
                0.3 * source_zone_prior
            )
            
            # Буст для alias match
            if alias_score > 0.7:
                final_score = min(final_score * 1.2, 1.0)

            explanation = {
                "alias_match": alias_explanation,
                "keyword_match": keyword_explanation,
                "embedding": embedding_explanation,
                "source_zone_prior": zone_explanation,
            }

            scores.append(TopicScore(
                topic_key=topic.topic_key,
                final_score=final_score,
                rule_score=rule_score,
                alias_match_score=alias_score,
                keyword_match_score=keyword_score,
                embedding_score=embedding_score,
                source_zone_prior=source_zone_prior,
                explanation=explanation,
            ))

        # Сортируем по final_score (убывание)
        scores.sort(key=lambda s: s.final_score, reverse=True)
        return scores

    def _calculate_alias_match_score(
        self,
        top_titles: list[str],
        topic_profile: dict[str, Any],
        cluster_lang: str,
    ) -> tuple[float, dict[str, Any]]:
        """Вычисляет score по exact/fuzzy match заголовков со aliases."""
        aliases_ru = topic_profile.get("aliases_ru", []) or topic_profile.get("headings_ru", [])
        aliases_en = topic_profile.get("aliases_en", []) or topic_profile.get("headings_en", [])
        
        best_match_ratio = 0.0
        best_match_title = None
        best_match_alias = None

        # Проверяем заголовки кластера против aliases топика
        aliases_to_check = []
        if cluster_lang in ("ru", "mixed"):
            aliases_to_check.extend([(alias, "ru") for alias in aliases_ru])
        if cluster_lang in ("en", "mixed"):
            aliases_to_check.extend([(alias, "en") for alias in aliases_en])

        for title in top_titles[:10]:  # Проверяем топ-10 заголовков
            title_norm = normalize_for_match(title)
            
            for alias, lang in aliases_to_check:
                alias_norm = normalize_for_match(alias)
                
                # Exact match
                if title_norm == alias_norm:
                    ratio = 1.0
                else:
                    # Fuzzy match
                    matcher = SequenceMatcher(None, title_norm, alias_norm)
                    ratio = matcher.ratio()

                if ratio > best_match_ratio:
                    best_match_ratio = ratio
                    best_match_title = title
                    best_match_alias = alias

        explanation = {
            "best_match_ratio": best_match_ratio,
            "matched_title": best_match_title,
            "matched_alias": best_match_alias,
        }

        return best_match_ratio, explanation

    def _calculate_keyword_match_score(
        self,
        top_titles: list[str],
        window_text: str | None,
        topic_profile: dict[str, Any],
        cluster_lang: str,
    ) -> tuple[float, dict[str, Any]]:
        """Вычисляет score по keyword match."""
        keywords_ru = topic_profile.get("keywords_ru", [])
        keywords_en = topic_profile.get("keywords_en", [])

        keywords_to_check = []
        if cluster_lang in ("ru", "mixed"):
            keywords_to_check.extend(keywords_ru)
        if cluster_lang in ("en", "mixed"):
            keywords_to_check.extend(keywords_en)

        if not keywords_to_check:
            return 0.0, {"reason": "no_keywords"}

        # Нормализуем текст для поиска
        text_to_search = " ".join(top_titles[:10]).lower()
        if window_text:
            text_to_search += " " + window_text.lower()

        text_norm = normalize_for_match(text_to_search)

        # Считаем количество совпавших keywords
        matched_keywords = []
        for keyword in keywords_to_check:
            keyword_norm = normalize_for_match(keyword)
            if keyword_norm in text_norm:
                matched_keywords.append(keyword)

        if not matched_keywords:
            return 0.0, {"matched_keywords": []}

        # Score пропорционален доле совпавших keywords
        match_ratio = len(matched_keywords) / len(keywords_to_check)
        score = min(match_ratio * 0.8, 0.8)  # Максимум 0.8 для keyword-only match

        explanation = {
            "matched_keywords": matched_keywords,
            "match_ratio": match_ratio,
        }

        return score, explanation

    def _calculate_embedding_similarity(
        self,
        cluster_embedding: list[float],
        topic_embedding: list[float],
    ) -> float:
        """Вычисляет cosine similarity между embeddings."""
        try:
            vec1 = np.array(cluster_embedding)
            vec2 = np.array(topic_embedding)

            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            cosine_sim = np.dot(vec1, vec2) / (norm1 * norm2)
            
            # Преобразуем similarity в score (0..1)
            return max(0.0, min(1.0, (cosine_sim + 1) / 2))
        except Exception as e:
            logger.warning(f"Ошибка при вычислении embedding similarity: {e}")
            return 0.0

    def _calculate_source_zone_prior(
        self,
        cluster_source_zone: str,
        topic_profile: dict[str, Any],
        topic_key: str,
    ) -> tuple[float, dict[str, Any]]:
        """Вычисляет prior от source_zone."""
        # Базовая оценка 0.5 (нейтральная)
        prior = 0.5
        explanation: dict[str, Any] = {"cluster_zone": cluster_source_zone}

        if cluster_source_zone == "unknown":
            return prior, explanation

        # Проверяем, относится ли топик к "statistics family"
        topic_zones = topic_profile.get("source_zones", [])
        dissimilar_zones = topic_profile.get("dissimilar_zones", [])

        # Если топик помечен как dissimilar для этой зоны - штраф
        if cluster_source_zone in dissimilar_zones:
            prior = 0.2
            explanation["reason"] = "dissimilar_zone"
            explanation["dissimilar_zones"] = dissimilar_zones
            return prior, explanation

        # Если зона кластера в списке topic_zones - буст
        if cluster_source_zone in topic_zones:
            prior = 0.8
            explanation["reason"] = "matched_zone"
            explanation["matched_zones"] = topic_zones
        elif cluster_source_zone == "statistics" and "statistics" in topic_key.lower():
            # Эвристика: если source_zone=statistics и topic_key содержит "statistics"
            prior = 0.75
            explanation["reason"] = "statistics_family_heuristic"

        return prior, explanation

    def _detect_cluster_source_zone(self, examples: list[dict[str, Any]]) -> str:
        """Определяет source_zone кластера по examples."""
        if not examples:
            return "unknown"

        # Берем первый example и его section_path
        first_example = examples[0]
        section_path = first_example.get("section_path", "")
        heading_text = first_example.get("heading_text_raw", "")

        result = self.source_zone_classifier.classify(
            section_path=section_path,
            heading_text=heading_text,
        )

        return result.zone

    async def _get_window_text_for_cluster(self, cluster: HeadingCluster) -> str | None:
        """Получает текст окна рядом с заголовками кластера для keyword matching."""
        # Пока возвращаем None - можно расширить при наличии данных
        return None

    async def _create_assignment(
        self,
        doc_version_id: UUID,
        cluster_id: int,
        topic_key: str,
        confidence: float,
        mapped_by: str,
        mapping_debug_json: dict[str, Any] | None = None,
    ) -> ClusterAssignment:
        """Создает или обновляет ClusterAssignment."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(ClusterAssignment).values(
            doc_version_id=doc_version_id,
            cluster_id=cluster_id,
            topic_key=topic_key,
            mapped_by=mapped_by,
            confidence=confidence,
            mapping_debug_json=mapping_debug_json,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_cluster_assignments_doc_version_cluster",
            set_=dict(
                topic_key=stmt.excluded.topic_key,
                mapped_by=stmt.excluded.mapped_by,
                confidence=stmt.excluded.confidence,
                mapping_debug_json=stmt.excluded.mapping_debug_json,
            ),
        ).returning(ClusterAssignment)

        result = await self.db.execute(stmt)
        await self.db.commit()
        assignment = result.scalar_one()
        await self.db.refresh(assignment)
        return assignment

    def _calculate_metrics(
        self,
        all_scores: list[list[TopicScore]],
        confidence_threshold: float = 0.65,
        ambiguity_delta: float = 0.08,
    ) -> MappingMetrics:
        """Вычисляет метрики качества маппинга."""
        if not all_scores:
            return MappingMetrics(coverage=0.0, ambiguity=0.0, fallback_rate=0.0, conflict_rate=0.0)

        total_clusters = len(all_scores)
        coverage_count = 0
        ambiguity_count = 0
        fallback_count = 0
        conflict_count = 0

        for cluster_scores in all_scores:
            if not cluster_scores:
                continue

            best_score = cluster_scores[0]

            # Coverage: confidence >= threshold
            if best_score.final_score >= confidence_threshold:
                coverage_count += 1

            # Ambiguity: top1 - top2 < delta
            if len(cluster_scores) >= 2:
                top2_score = cluster_scores[1].final_score
                if best_score.final_score - top2_score < ambiguity_delta:
                    ambiguity_count += 1

            # Fallback: только keyword-match без alias и embeddings
            if (
                best_score.alias_match_score == 0.0
                and best_score.embedding_score == 0.0
                and best_score.keyword_match_score > 0.0
            ):
                fallback_count += 1

            # Conflict: dissimilar zone
            explanation = best_score.explanation or {}
            zone_explanation = explanation.get("source_zone_prior", {})
            if zone_explanation.get("reason") == "dissimilar_zone":
                conflict_count += 1

        coverage = coverage_count / total_clusters if total_clusters > 0 else 0.0
        ambiguity = ambiguity_count / total_clusters if total_clusters > 0 else 0.0
        fallback_rate = fallback_count / total_clusters if total_clusters > 0 else 0.0
        conflict_rate = conflict_count / total_clusters if total_clusters > 0 else 0.0

        return MappingMetrics(
            coverage=coverage,
            ambiguity=ambiguity,
            fallback_rate=fallback_rate,
            conflict_rate=conflict_rate,
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
                "coverage": metrics.coverage,
                "ambiguity": metrics.ambiguity,
                "fallback_rate": metrics.fallback_rate,
                "conflict_rate": metrics.conflict_rate,
            },
        )
        self.db.add(mapping_run)
        await self.db.commit()
        await self.db.refresh(mapping_run)
        return mapping_run

