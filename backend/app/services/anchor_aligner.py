"""Сервис для выравнивания якорей между версиями документов.

Реализует детерминированный алгоритм матчинга якорей для diff/impact анализа.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType, DocumentLanguage
from app.db.models.anchors import Anchor, Chunk
from app.db.models.anchor_matches import AnchorMatch
from app.db.models.studies import DocumentVersion
from app.services.text_normalization import normalize_for_match


@dataclass
class AlignmentStats:
    """Статистика выравнивания якорей."""
    
    matched: int
    changed: int
    added: int
    removed: int
    total_from: int
    total_to: int


class AnchorAligner:
    """Сервис для выравнивания якорей между двумя версиями документа.
    
    Алгоритм:
    1. Фильтрует якоря по content_type (сравнивает только одинаковые типы)
    2. Генерирует кандидатов с учетом source_zone и language
    3. Вычисляет score для каждой пары (exact/fuzzy/embedding/hybrid)
    4. Выполняет жадное 1-to-1 матчинг по убыванию score
    5. Сохраняет результаты в anchor_matches
    """
    
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
    
    async def align(
        self,
        doc_version_a: UUID | DocumentVersion,
        doc_version_b: UUID | DocumentVersion,
        *,
        scope: str = "body",
        min_score: float = 0.78,
    ) -> AlignmentStats:
        """
        Выравнивает якоря между двумя версиями документа.
        
        Args:
            doc_version_a: UUID или DocumentVersion исходной версии
            doc_version_b: UUID или DocumentVersion целевой версии
            scope: Область сравнения ("body", "all") - пока не используется
            min_score: Минимальный score для матчинга (0.0-1.0)
            
        Returns:
            AlignmentStats со статистикой выравнивания
        """
        # Получаем DocumentVersion объекты
        if isinstance(doc_version_a, UUID):
            doc_version_a = await self.db.get(DocumentVersion, doc_version_a)
        if isinstance(doc_version_b, UUID):
            doc_version_b = await self.db.get(DocumentVersion, doc_version_b)
        
        if not doc_version_a or not doc_version_b:
            raise ValueError("Одна или обе версии документа не найдены")
        
        if doc_version_a.document_id != doc_version_b.document_id:
            raise ValueError("Версии должны принадлежать одному документу")
        
        logger.info(
            f"Выравнивание якорей: {doc_version_a.id} -> {doc_version_b.id} "
            f"(min_score={min_score})"
        )
        
        # Получаем все якоря для обеих версий
        anchors_a = await self._get_anchors(doc_version_a.id)
        anchors_b = await self._get_anchors(doc_version_b.id)
        
        # Получаем embeddings через chunks
        embeddings_a = await self._get_anchor_embeddings(doc_version_a.id, anchors_a)
        embeddings_b = await self._get_anchor_embeddings(doc_version_b.id, anchors_b)
        
        # Группируем якоря по content_type
        anchors_by_type_a = self._group_by_content_type(anchors_a)
        anchors_by_type_b = self._group_by_content_type(anchors_b)
        
        # Выполняем матчинг для каждого типа контента
        all_matches: list[tuple[Anchor, Anchor, float, str, dict[str, Any]]] = []
        
        for content_type in AnchorContentType:
            if content_type not in anchors_by_type_a or content_type not in anchors_by_type_b:
                continue
            
            type_anchors_a = anchors_by_type_a[content_type]
            type_anchors_b = anchors_by_type_b[content_type]
            
            matches = self._match_anchors(
                type_anchors_a,
                type_anchors_b,
                embeddings_a,
                embeddings_b,
                min_score=min_score,
            )
            all_matches.extend(matches)
        
        # Сохраняем матчи в БД
        await self._save_matches(
            doc_version_a.document_id,
            doc_version_a.id,
            doc_version_b.id,
            all_matches,
        )
        
        # Вычисляем статистику
        matched_anchor_ids_b = {match[1].anchor_id for match in all_matches}
        stats = AlignmentStats(
            matched=len(all_matches),
            changed=sum(1 for m in all_matches if m[2] < 1.0),
            added=len(anchors_b) - len(matched_anchor_ids_b),
            removed=len(anchors_a) - len(all_matches),
            total_from=len(anchors_a),
            total_to=len(anchors_b),
        )
        
        logger.info(
            f"Выравнивание завершено: matched={stats.matched}, "
            f"changed={stats.changed}, added={stats.added}, removed={stats.removed}"
        )
        
        return stats
    
    async def _get_anchors(self, doc_version_id: UUID) -> list[Anchor]:
        """Получает все якоря для версии документа."""
        stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
    
    async def _get_anchor_embeddings(
        self, doc_version_id: UUID, anchors: list[Anchor]
    ) -> dict[str, list[float]]:
        """Получает embeddings для якорей через chunks.
        
        Возвращает словарь {anchor_id: embedding_vector}.
        Если для якоря нет embedding (нет chunk или chunk без embedding), возвращает None.
        """
        anchor_ids = {a.anchor_id for a in anchors}
        
        # Получаем chunks, которые содержат эти anchor_ids
        stmt = select(Chunk).where(
            Chunk.doc_version_id == doc_version_id,
            Chunk.anchor_ids.overlap(list(anchor_ids)),  # type: ignore
        )
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()
        
        # Строим маппинг anchor_id -> embedding
        # Если якорь присутствует в нескольких chunks, берем первый
        embeddings: dict[str, list[float]] = {}
        for chunk in chunks:
            for anchor_id in chunk.anchor_ids:
                if anchor_id in anchor_ids and anchor_id not in embeddings:
                    # embedding может быть list[float] или vector
                    emb = chunk.embedding
                    if isinstance(emb, list):
                        embeddings[anchor_id] = emb
                    elif hasattr(emb, '__iter__'):
                        embeddings[anchor_id] = list(emb)
        
        return embeddings
    
    def _group_by_content_type(
        self, anchors: list[Anchor]
    ) -> dict[AnchorContentType, list[Anchor]]:
        """Группирует якоря по content_type."""
        grouped: dict[AnchorContentType, list[Anchor]] = defaultdict(list)
        for anchor in anchors:
            grouped[anchor.content_type].append(anchor)
        return grouped

    @staticmethod
    def _extract_hash_part(anchor_id: str) -> str:
        """
        Извлекает последнюю хеш-часть из anchor_id, игнорируя суффиксы вида :v2.
        Форматы:
          doc:ctype:hash
          doc:ctype:hash:v2
        """
        # Убираем суффикс версионности внутри одной версии (":v2")
        base_part = anchor_id
        if ":v" in anchor_id:
            parts = anchor_id.rsplit(":v", 1)
            if len(parts) == 2 and parts[1].isdigit():
                base_part = parts[0]
        return base_part.split(":")[-1]
    
    def _match_anchors(
        self,
        anchors_a: list[Anchor],
        anchors_b: list[Anchor],
        embeddings_a: dict[str, list[float]],
        embeddings_b: dict[str, list[float]],
        min_score: float,
    ) -> list[tuple[Anchor, Anchor, float, str, dict[str, Any]]]:
        """
        Выполняет матчинг якорей между двумя списками.
        
        Returns:
            Список кортежей (anchor_a, anchor_b, score, method, meta_json)
        """
        if not anchors_a or not anchors_b:
            return []
        # 0) Exact match по хеш-части anchor_id (новые стабильные ID)
        hash_map_b: dict[str, list[Anchor]] = defaultdict(list)
        for b in anchors_b:
            h = self._extract_hash_part(b.anchor_id)
            hash_map_b[h].append(b)

        matched: list[tuple[Anchor, Anchor, float, str, dict[str, Any]]] = []
        used_a: set[str] = set()
        used_b: set[str] = set()

        for a in anchors_a:
            h = self._extract_hash_part(a.anchor_id)
            if h in hash_map_b and hash_map_b[h]:
                b = hash_map_b[h].pop(0)
                matched.append(
                    (a, b, 1.0, "exact_hash", {"text_sim": 1.0, "path_sim": 1.0})
                )
                used_a.add(a.anchor_id)
                used_b.add(b.anchor_id)

        # Фильтруем оставшихся для fuzzy/semantic матчинга
        remaining_a = [a for a in anchors_a if a.anchor_id not in used_a]
        remaining_b = [b for b in anchors_b if b.anchor_id not in used_b]
        if not remaining_a or not remaining_b:
            return matched

        # Генерируем кандидатов с приоритетами
        candidates: list[tuple[Anchor, Anchor, float]] = []
        
        for anchor_a in remaining_a:
            for anchor_b in remaining_b:
                # Фильтруем по source_zone и language (приоритет, но не требование)
                zone_bonus = 0.0
                lang_bonus = 0.0
                
                if anchor_a.source_zone == anchor_b.source_zone:
                    zone_bonus = 0.05
                if anchor_a.language == anchor_b.language and anchor_a.language != DocumentLanguage.UNKNOWN:
                    lang_bonus = 0.05
                
                # Вычисляем score
                score, method, meta = self._compute_score(
                    anchor_a,
                    anchor_b,
                    embeddings_a.get(anchor_a.anchor_id),
                    embeddings_b.get(anchor_b.anchor_id),
                )
                
                # Добавляем бонусы к финальному score
                final_score = min(1.0, score + zone_bonus + lang_bonus)
                
                if final_score >= min_score:
                    candidates.append((anchor_a, anchor_b, final_score))
                    meta["zone_bonus"] = zone_bonus
                    meta["lang_bonus"] = lang_bonus
        
        # Сортируем по score (убывание)
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        # Жадный 1-to-1 матчинг
        matches: list[tuple[Anchor, Anchor, float, str, dict[str, Any]]] = matched
        
        for anchor_a, anchor_b, score in candidates:
            if anchor_a.anchor_id in used_a or anchor_b.anchor_id in used_b:
                continue
            
            # Пересчитываем score для финального матча
            _, method, meta = self._compute_score(
                anchor_a,
                anchor_b,
                embeddings_a.get(anchor_a.anchor_id),
                embeddings_b.get(anchor_b.anchor_id),
            )
            
            matches.append((anchor_a, anchor_b, score, method, meta))
            used_a.add(anchor_a.anchor_id)
            used_b.add(anchor_b.anchor_id)
        
        return matches
    
    def _compute_score(
        self,
        anchor_a: Anchor,
        anchor_b: Anchor,
        embedding_a: list[float] | None,
        embedding_b: list[float] | None,
    ) -> tuple[float, str, dict[str, Any]]:
        """
        Вычисляет score между двумя якорями.
        
        Returns:
            (score, method, meta_json)
        """
        meta: dict[str, Any] = {}
        
        # 1. Exact match
        text_a_norm = normalize_for_match(anchor_a.text_norm)
        text_b_norm = normalize_for_match(anchor_b.text_norm)
        
        if text_a_norm == text_b_norm:
            return (1.0, "exact", {"text_sim": 1.0})
        
        # 2. Fuzzy score (token-based similarity)
        fuzzy_score = self._fuzzy_score(text_a_norm, text_b_norm)
        meta["text_sim"] = fuzzy_score
        
        # 3. Embedding score
        emb_score = 0.0
        if embedding_a and embedding_b:
            emb_score = self._cosine_similarity(embedding_a, embedding_b)
            meta["emb_sim"] = emb_score
        else:
            meta["emb_sim"] = None
        
        # 4. Zone/path agreement
        zone_score = 0.0
        path_score = 0.0
        
        if anchor_a.source_zone == anchor_b.source_zone:
            zone_score = 1.0
        meta["zone_sim"] = zone_score
        
        # Сравниваем section_path (проверяем общих родителей)
        path_a_parts = anchor_a.section_path.split("/")
        path_b_parts = anchor_b.section_path.split("/")
        common_prefix_len = 0
        for i, (part_a, part_b) in enumerate(zip(path_a_parts, path_b_parts)):
            if part_a == part_b:
                common_prefix_len = i + 1
            else:
                break
        if max(len(path_a_parts), len(path_b_parts)) > 0:
            path_score = common_prefix_len / max(len(path_a_parts), len(path_b_parts))
        meta["path_sim"] = path_score
        
        # 5. Combined score (усиливаем вклад embedding) + штраф за "прыжок" по разделам
        if emb_score > 0:
            combined = 0.65 * emb_score + 0.25 * fuzzy_score + 0.10 * (0.6 * zone_score + 0.4 * path_score)
            method = "hybrid"
        else:
            combined = 0.60 * fuzzy_score + 0.40 * (0.5 * zone_score + 0.5 * path_score)
            method = "fuzzy"

        # Штраф за существенное расхождение section_path
        path_penalty = 0.15 * (1.0 - path_score)
        combined = max(0.0, combined - path_penalty)
        
        return (combined, method, meta)
    
    def _fuzzy_score(self, text_a: str, text_b: str) -> float:
        """Вычисляет fuzzy similarity между двумя текстами."""
        if not text_a or not text_b:
            return 0.0
        
        # Используем SequenceMatcher для базового fuzzy matching
        matcher = SequenceMatcher(None, text_a, text_b)
        ratio = matcher.ratio()
        
        # Дополнительно: token-based similarity
        tokens_a = set(text_a.split())
        tokens_b = set(text_b.split())
        
        if not tokens_a or not tokens_b:
            return ratio
        
        # Jaccard similarity по токенам
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        jaccard = intersection / union if union > 0 else 0.0
        
        # Комбинируем ratio и jaccard
        return 0.6 * ratio + 0.4 * jaccard
    
    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Вычисляет cosine similarity между двумя векторами."""
        if len(vec_a) != len(vec_b):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        
        return dot_product / (norm_a * norm_b)
    
    async def _save_matches(
        self,
        document_id: UUID,
        from_version_id: UUID,
        to_version_id: UUID,
        matches: list[tuple[Anchor, Anchor, float, str, dict[str, Any]]],
    ) -> None:
        """Сохраняет матчи в БД."""
        # Удаляем старые матчи для этой пары версий
        stmt = select(AnchorMatch).where(
            AnchorMatch.from_doc_version_id == from_version_id,
            AnchorMatch.to_doc_version_id == to_version_id,
        )
        result = await self.db.execute(stmt)
        old_matches = result.scalars().all()
        for old_match in old_matches:
            await self.db.delete(old_match)
        
        # Создаем новые матчи
        for anchor_a, anchor_b, score, method, meta in matches:
            match = AnchorMatch(
                document_id=document_id,
                from_doc_version_id=from_version_id,
                to_doc_version_id=to_version_id,
                from_anchor_id=anchor_a.anchor_id,
                to_anchor_id=anchor_b.anchor_id,
                score=score,
                method=method,
                meta_json=meta,
            )
            self.db.add(match)
        
        await self.db.commit()
        logger.info(f"Сохранено {len(matches)} матчей в БД")

