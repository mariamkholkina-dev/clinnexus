"""Сервис для кластеризации заголовков документа."""
from __future__ import annotations

import numpy as np
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.core.config import settings
from app.db.enums import AnchorContentType, DocumentLanguage
from app.db.models.anchors import Anchor, Chunk
from app.db.models.topics import HeadingCluster
from app.services.ingestion.docx_ingestor import normalize_text
from app.services.ingestion.heading_detector import normalize_title
from app.services.text_normalization import normalize_for_match
from sklearn.cluster import AgglomerativeClustering, DBSCAN
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_distances


def normalize_heading_text(text: str) -> str:
    """Нормализует текст заголовка для кластеризации."""
    if not text:
        return ""
    # Сначала применяем normalize_title (более агрессивная нормализация для заголовков)
    normalized = normalize_title(text)
    # Затем применяем normalize_text для дополнительной нормализации
    normalized = normalize_text(normalized)
    return normalized


class HeadingClusteringService:
    """Сервис для кластеризации заголовков документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def cluster_headings_for_doc_version(
        self,
        doc_version_id: UUID,
        threshold: float = 0.22,
        min_size: int = 3,
        embedding_threshold: float = 0.15,
    ) -> list[HeadingCluster]:
        """
        Кластеризует заголовки документа и сохраняет результаты.

        Args:
            doc_version_id: ID версии документа
            threshold: Порог distance для кластеризации (по умолчанию 0.22)
            min_size: Минимальный размер кластера (по умолчанию 3)
            embedding_threshold: Порог для merge по embeddings (по умолчанию 0.15)

        Returns:
            Список созданных/обновленных кластеров
        """
        logger.info(f"Начало кластеризации заголовков для doc_version_id={doc_version_id}")

        # 1. Загружаем все HDR anchors для версии документа
        stmt = select(Anchor).where(
            Anchor.doc_version_id == doc_version_id,
            Anchor.content_type == AnchorContentType.HDR,
        ).order_by(Anchor.ordinal)
        
        result = await self.db.execute(stmt)
        hdr_anchors = list(result.scalars().all())

        if not hdr_anchors:
            logger.warning(f"Не найдено заголовков для doc_version_id={doc_version_id}")
            return []

        logger.info(f"Найдено {len(hdr_anchors)} заголовков")

        # 2. Подготавливаем данные для кластеризации
        headings_norm: list[str] = []
        anchor_data: list[dict[str, Any]] = []

        for anchor in hdr_anchors:
            heading_norm = normalize_heading_text(anchor.text_raw)
            headings_norm.append(heading_norm)
            # Безопасная обработка enum полей: могут быть enum или строка
            language_value = anchor.language.value if hasattr(anchor.language, 'value') else anchor.language
            source_zone_value = anchor.source_zone.value if hasattr(anchor.source_zone, 'value') else anchor.source_zone
            anchor_data.append({
                "anchor_id": anchor.anchor_id,
                "anchor_uuid": str(anchor.id),
                "section_path": anchor.section_path,
                "text_raw": anchor.text_raw,
                "language": language_value,
                "source_zone": source_zone_value,
            })

        # 3. TF-IDF векторизация
        if len(headings_norm) < 2:
            logger.warning("Недостаточно заголовков для кластеризации")
            return []

        vectorizer = TfidfVectorizer(
            max_features=5000,
            min_df=2 if len(headings_norm) > 2 else 1,
            max_df=0.95,
            ngram_range=(1, 2),
            lowercase=True,
            strip_accents='unicode',
        )
        
        try:
            tfidf_matrix = vectorizer.fit_transform(headings_norm).toarray()
        except ValueError as e:
            logger.error(f"Ошибка при векторизации: {e}")
            return []

        # 4. Кластеризация по TF-IDF
        if len(tfidf_matrix) < 2:
            labels = [0] * len(headings_norm)
        else:
            distances = cosine_distances(tfidf_matrix)
            clustering = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=threshold,
                metric='precomputed',
                linkage='average',
            )
            labels = clustering.fit_predict(distances)

        # 5. Фильтрация по минимальному размеру
        label_counts = Counter(labels)
        valid_clusters = {label for label, count in label_counts.items() if count >= min_size}

        # Переназначаем метки: невалидные кластеры получают -1 (шум)
        filtered_labels = []
        label_mapping: dict[int, int] = {}
        next_valid_id = 0

        for label in labels:
            if label in valid_clusters:
                if label not in label_mapping:
                    label_mapping[label] = next_valid_id
                    next_valid_id += 1
                filtered_labels.append(label_mapping[label])
            else:
                filtered_labels.append(-1)  # Шум

        # 6. Группируем по кластерам для merge по embeddings
        clusters_dict: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(filtered_labels):
            if label >= 0:
                clusters_dict[label].append(idx)

        clusters_list = [indices for indices in clusters_dict.values()]

        # 7. Опциональный merge по embeddings
        anchor_ids = [data["anchor_id"] for data in anchor_data]
        embeddings_map = await self._fetch_embeddings_for_headings(anchor_ids)

        if embeddings_map:
            logger.info(f"Загружено {len(embeddings_map)} embeddings, выполняется merge...")
            filtered_labels = self._merge_clusters_by_embeddings(
                clusters_list,
                embeddings_map,
                anchor_ids,
                threshold=embedding_threshold,
            )
            # Пересоздаем clusters_dict после merge
            clusters_dict = defaultdict(list)
            for idx, label in enumerate(filtered_labels):
                if label >= 0:
                    clusters_dict[label].append(idx)

        # 8. Формируем кластеры для сохранения
        clusters_to_save: list[HeadingCluster] = []

        for cluster_id, cluster_indices in sorted(clusters_dict.items()):
            # Группируем по языкам
            clusters_by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)

            for idx in cluster_indices:
                data = anchor_data[idx]
                lang = data["language"]
                clusters_by_lang[lang].append(data)

            # Сохраняем отдельный кластер для каждого языка
            for lang, cluster_records in clusters_by_lang.items():
                try:
                    lang_enum = DocumentLanguage(lang)
                except ValueError:
                    lang_enum = DocumentLanguage.UNKNOWN

                # Формируем данные кластера
                top_titles: list[str] = []
                examples: list[dict[str, Any]] = []
                heading_levels: list[int] = []
                content_type_counts: Counter[str] = Counter()
                total_chars_list: list[int] = []
                cluster_embeddings_list: list[list[float]] = []

                for record in cluster_records:
                    text_raw = record["text_raw"]
                    if text_raw and text_raw not in top_titles:
                        top_titles.append(text_raw)
                    
                    if len(examples) < 10:
                        examples.append({
                            "section_path": record["section_path"],
                            "heading_text_raw": text_raw,
                            "anchor_id": record["anchor_id"],
                        })

                    # Статистика (базовая, можно расширить при наличии window данных)
                    if len(text_raw) > 0:
                        total_chars_list.append(len(text_raw))
                    
                    # Собираем embeddings для кластера
                    anchor_id = record["anchor_id"]
                    if anchor_id in embeddings_map:
                        cluster_embeddings_list.append(embeddings_map[anchor_id])

                # Топ-20 заголовков
                top_titles_json = top_titles[:20]
                
                # Статистика
                heading_level_histogram = dict(Counter(heading_levels)) if heading_levels else {}
                content_type_distribution = dict(content_type_counts) if content_type_counts else {}
                avg_total_chars = float(np.mean(total_chars_list)) if total_chars_list else 0.0

                stats_json = {
                    "heading_level_histogram": heading_level_histogram,
                    "content_type_distribution": content_type_distribution,
                    "avg_total_chars": round(avg_total_chars, 2),
                    "size": len(cluster_records),
                }

                # Вычисляем средний embedding для кластера
                cluster_embedding: list[float] | None = None
                if cluster_embeddings_list:
                    cluster_embedding = list(np.mean(cluster_embeddings_list, axis=0))

                # Upsert кластера
                from app.services.topic_repository import HeadingClusterRepository
                repo = HeadingClusterRepository(self.db)
                
                cluster = await repo.upsert_cluster(
                    doc_version_id=doc_version_id,
                    cluster_id=cluster_id,
                    language=lang_enum.value,
                    top_titles_json=top_titles_json,
                    examples_json=examples,
                    stats_json=stats_json,
                    cluster_embedding=cluster_embedding,
                )

                clusters_to_save.append(cluster)

        logger.info(f"Создано {len(clusters_to_save)} кластеров для doc_version_id={doc_version_id}")
        return clusters_to_save

    async def _fetch_embeddings_for_headings(
        self,
        hdr_anchor_ids: list[str],
    ) -> dict[str, list[float]]:
        """Получает embeddings для заголовков из chunks."""
        embeddings_map: dict[str, list[float]] = {}

        try:
            # Ищем chunks, которые содержат данные anchor_ids
            # Используем contains() для проверки наличия anchor_id в массиве PostgreSQL
            for anchor_id in hdr_anchor_ids:
                # Проверяем, содержит ли массив anchor_ids наш anchor_id
                stmt = select(Chunk).where(
                    Chunk.anchor_ids.contains([anchor_id])  # type: ignore
                ).limit(1)
                
                result = await self.db.execute(stmt)
                chunk = result.scalar_one_or_none()
                
                if chunk and chunk.embedding is not None:
                    # embedding может быть уже списком или нужно конвертировать
                    embedding = chunk.embedding
                    if isinstance(embedding, (list, tuple)):
                        embedding_list = list(embedding)
                        if len(embedding_list) == 1536:
                            embeddings_map[anchor_id] = embedding_list

        except Exception as e:
            logger.warning(f"Ошибка при загрузке embeddings: {e}")

        return embeddings_map

    def _merge_clusters_by_embeddings(
        self,
        clusters: list[list[int]],
        embeddings_map: dict[str, list[float]],
        hdr_anchor_ids: list[str],
        threshold: float = 0.15,
    ) -> list[int]:
        """Объединяет кластеры на основе embeddings."""
        if not embeddings_map:
            # Возвращаем исходные метки
            labels = [-1] * len(hdr_anchor_ids)
            for cluster_id, cluster_indices in enumerate(clusters):
                for idx in cluster_indices:
                    labels[idx] = cluster_id
            return labels

        # Строим матрицу embeddings
        embedding_indices: dict[int, int] = {}
        embedding_vectors = []

        for idx, anchor_id in enumerate(hdr_anchor_ids):
            if anchor_id in embeddings_map:
                embedding_indices[idx] = len(embedding_vectors)
                embedding_vectors.append(embeddings_map[anchor_id])

        if len(embedding_vectors) < 2:
            labels = [-1] * len(hdr_anchor_ids)
            for cluster_id, cluster_indices in enumerate(clusters):
                for idx in cluster_indices:
                    labels[idx] = cluster_id
            return labels

        embedding_matrix = np.array(embedding_vectors)

        # Вычисляем средние embeddings для каждого кластера
        cluster_embeddings: dict[int, np.ndarray] = {}

        for cluster_id, cluster_indices in enumerate(clusters):
            cluster_emb_vecs = []
            for idx in cluster_indices:
                if idx in embedding_indices:
                    emb_idx = embedding_indices[idx]
                    cluster_emb_vecs.append(embedding_matrix[emb_idx])

            if cluster_emb_vecs:
                cluster_embeddings[cluster_id] = np.mean(cluster_emb_vecs, axis=0)

        # Объединяем близкие кластеры
        cluster_labels = list(range(len(clusters)))
        merged = set()

        for i, cluster_id_i in enumerate(cluster_labels):
            if cluster_id_i in merged:
                continue
            if cluster_id_i not in cluster_embeddings:
                continue

            for j, cluster_id_j in enumerate(cluster_labels):
                if i >= j or cluster_id_j in merged:
                    continue
                if cluster_id_j not in cluster_embeddings:
                    continue

                # Вычисляем cosine distance
                emb_i = cluster_embeddings[cluster_id_i]
                emb_j = cluster_embeddings[cluster_id_j]

                norm_i = np.linalg.norm(emb_i)
                norm_j = np.linalg.norm(emb_j)

                if norm_i == 0 or norm_j == 0:
                    continue

                cosine_sim = np.dot(emb_i, emb_j) / (norm_i * norm_j)
                cosine_dist = 1 - cosine_sim

                if np.isnan(cosine_dist):
                    continue

                if cosine_dist < threshold:
                    cluster_labels[j] = cluster_id_i
                    merged.add(cluster_id_j)

        # Переназначаем метки
        label_mapping: dict[int, int] = {}
        next_id = 0

        for label in cluster_labels:
            if label not in label_mapping:
                label_mapping[label] = next_id
                next_id += 1

        # Формируем финальные метки
        labels = [-1] * len(hdr_anchor_ids)
        for cluster_id, cluster_indices in enumerate(clusters):
            final_label = label_mapping[cluster_labels[cluster_id]]
            for idx in cluster_indices:
                labels[idx] = final_label

        return labels

