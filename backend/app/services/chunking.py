"""Шаг 6: создание chunks (Narrative Index) из anchors.

Требования:
- Группировка по section_path (структура документа), НЕ по section_key
- В chunk сохраняем anchor_ids[]
- embedding: детерминированный локальный вектор 1536 (feature hashing), без внешних API
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType
from app.db.models.anchors import Anchor, Chunk


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[^\sA-Za-z0-9]", re.UNICODE)


def _iter_tokens(text: str) -> list[str]:
    # Простая токенизация: слова/числа + одиночные знаки пунктуации.
    # Для feature hashing важна стабильность, а не лингвистическая точность.
    return [t.lower() for t in _TOKEN_RE.findall(text) if t.strip()]


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _hash_embedding_v1(text_norm: str, dims: int = 1536) -> list[float]:
    """Feature hashing в фиксированное пространство dims + L2 normalize.

    bucket = sha256(token) -> int -> % dims
    sign = следующий бит для симметрии (±1)
    """
    vec = [0.0] * dims
    tokens = _iter_tokens(text_norm)
    if not tokens:
        # Нулевая строка -> нулевой вектор (но NOT NULL). Нормализовать нельзя.
        return vec

    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        bucket = int.from_bytes(h[:8], "big") % dims
        sign = -1.0 if (h[8] & 1) else 1.0
        vec[bucket] += sign

    # L2 normalize
    norm_sq = sum(x * x for x in vec)
    if norm_sq <= 0.0:
        return vec
    inv = 1.0 / math.sqrt(norm_sq)
    return [x * inv for x in vec]


class ChunkingService:
    """Сервис rebuild chunk-ов для doc_version."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def rebuild_chunks_for_doc_version(self, doc_version_id: UUID, max_tokens: int = 450) -> int:
        logger.debug(
            "Chunking: старт rebuild_chunks_for_doc_version "
            f"(doc_version_id={doc_version_id}, max_tokens={max_tokens})"
        )
        # 1) Идемпотентность: удаляем существующие chunks
        del_res = await self.db.execute(delete(Chunk).where(Chunk.doc_version_id == doc_version_id))
        await self.db.flush()
        try:
            deleted = int(getattr(del_res, "rowcount", 0) or 0)
        except Exception:  # noqa: BLE001
            deleted = 0
        logger.debug(f"Chunking: удалено старых chunks={deleted} для doc_version_id={doc_version_id}")

        # 2) Загружаем anchors нужных типов (исключая cell)
        allowed_types = {
            AnchorContentType.HDR,
            AnchorContentType.P,
            AnchorContentType.LI,
            AnchorContentType.FN,
            AnchorContentType.TBL,
        }

        anchors_stmt = (
            select(Anchor)
            .where(Anchor.doc_version_id == doc_version_id)
            .where(Anchor.content_type.in_(allowed_types))
        )
        anchors = (await self.db.execute(anchors_stmt)).scalars().all()
        if not anchors:
            logger.info(f"Chunking: нет anchors для doc_version_id={doc_version_id}")
            return 0
        logger.debug(
            "Chunking: загружены anchors "
            f"(doc_version_id={doc_version_id}, count={len(anchors)}, "
            f"allowed_types={[t.value for t in sorted(allowed_types, key=lambda x: x.value)]})"
        )

        # 3) Группируем по section_path
        by_section: dict[str, list[Anchor]] = defaultdict(list)
        for a in anchors:
            by_section[a.section_path].append(a)
        logger.debug(
            "Chunking: сгруппировано по section_path "
            f"(sections={len(by_section)}, doc_version_id={doc_version_id})"
        )

        chunk_objects: list[Chunk] = []

        # 4) Внутри каждой секции собираем chunks по rough token estimate (chars/4)
        for section_path, sec_anchors in by_section.items():
            logger.debug(
                "Chunking: секция "
                f"(section_path={section_path!r}, anchors_in_section={len(sec_anchors)})"
            )
            def sort_key(a: Anchor):
                para_index = None
                try:
                    para_index = int(a.location_json.get("para_index")) if a.location_json else None
                except Exception:  # noqa: BLE001
                    para_index = None
                return (para_index if para_index is not None else 10**9, a.ordinal, a.anchor_id)

            sec_anchors_sorted = sorted(sec_anchors, key=sort_key)
            if logger.isEnabledFor(10):  # DEBUG
                sample = sec_anchors_sorted[:10]
                logger.debug(
                    "Chunking: порядок anchors (первые 10) "
                    + ", ".join(
                        [
                            f"[para_index={a.location_json.get('para_index') if isinstance(a.location_json, dict) else None}, "
                            f"type={a.content_type.value}, ord={a.ordinal}, id={a.anchor_id}]"
                            for a in sample
                        ]
                    )
                )

            cur_text_parts: list[str] = []
            cur_anchor_ids: list[str] = []
            cur_chars = 0
            chunk_ordinal = 0
            chunks_in_section = 0

            def flush_chunk() -> None:
                nonlocal chunk_ordinal, cur_text_parts, cur_anchor_ids, cur_chars, chunks_in_section
                if not cur_text_parts or not cur_anchor_ids:
                    cur_text_parts = []
                    cur_anchor_ids = []
                    cur_chars = 0
                    return

                chunk_ordinal += 1
                chunks_in_section += 1
                text = "\n".join(cur_text_parts).strip()
                text_norm = _normalize_text(text)
                text_hash16 = _sha256_hex(text_norm)[:16]
                chunk_id = f"{doc_version_id}:{section_path}:{chunk_ordinal}:{text_hash16}"

                token_estimate = max(1, int(len(text_norm) / 4)) if text_norm else 0
                embedding = _hash_embedding_v1(text_norm, dims=1536)

                metadata: dict[str, Any] = {
                    "token_estimate": token_estimate,
                    "anchor_count": len(cur_anchor_ids),
                    "embedding_type": "hash_v1",
                }

                logger.debug(
                    "Chunking: flush_chunk "
                    f"(section_path={section_path!r}, chunk_ordinal={chunk_ordinal}, "
                    f"token_estimate={token_estimate}, anchors={len(cur_anchor_ids)}, "
                    f"text_chars={len(text)}, chunk_id={chunk_id})"
                )

                chunk_objects.append(
                    Chunk(
                        doc_version_id=doc_version_id,
                        chunk_id=chunk_id,
                        section_path=section_path,
                        text=text,
                        anchor_ids=cur_anchor_ids,
                        embedding=embedding,
                        metadata_json=metadata,
                    )
                )

                cur_text_parts = []
                cur_anchor_ids = []
                cur_chars = 0

            for a in sec_anchors_sorted:
                piece = (a.text_norm or "").strip()
                if not piece:
                    logger.debug(
                        "Chunking: пропуск пустого anchor.text_norm "
                        f"(anchor_id={a.anchor_id}, type={a.content_type.value})"
                    )
                    continue

                # Добавляем разделитель между anchors
                addition = piece if not cur_text_parts else "\n" + piece
                new_chars = cur_chars + len(addition)
                new_token_est = int(new_chars / 4)

                if cur_text_parts and new_token_est > max_tokens:
                    logger.debug(
                        "Chunking: превышен max_tokens -> flush "
                        f"(section_path={section_path!r}, cur_chars={cur_chars}, "
                        f"new_chars={new_chars}, new_token_est={new_token_est}, max_tokens={max_tokens}, "
                        f"next_anchor_id={a.anchor_id})"
                    )
                    flush_chunk()

                # После flush можем снова попытаться добавить
                if not cur_text_parts:
                    addition = piece
                    new_chars = len(addition)

                cur_text_parts.append(piece)
                cur_anchor_ids.append(a.anchor_id)
                cur_chars = new_chars

            flush_chunk()
            logger.debug(
                "Chunking: секция завершена "
                f"(section_path={section_path!r}, chunks_in_section={chunks_in_section})"
            )

        if chunk_objects:
            self.db.add_all(chunk_objects)
            await self.db.flush()

        logger.info(
            f"Chunking: создано {len(chunk_objects)} chunks для doc_version_id={doc_version_id}"
        )
        logger.debug(
            "Chunking: готово rebuild_chunks_for_doc_version "
            f"(doc_version_id={doc_version_id}, chunks_created={len(chunk_objects)})"
        )
        return len(chunk_objects)


