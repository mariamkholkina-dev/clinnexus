"""
Сидер Section Taxonomy из JSON файла.

Читает backend/app/data/section_taxonomy_{doc_type}.json и импортирует в БД.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import logger
from app.db.enums import DocumentType
from app.db.models.taxonomy import (
    SectionTaxonomyNode,
    SectionTaxonomyAlias,
    SectionTaxonomyRelated,
)


def _normalize_related_pair(a: str, b: str) -> tuple[str, str]:
    """Нормализует пару related: гарантирует, что a < b (лексикографически)."""
    if a < b:
        return (a, b)
    return (b, a)


async def seed_taxonomy(*, taxonomy_file: Path, clear_existing: bool = False) -> None:
    """Импортирует taxonomy из JSON файла."""
    if not taxonomy_file.exists():
        raise SystemExit(f"Файл не найден: {taxonomy_file}")

    with open(taxonomy_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    doc_type_str = data.get("doc_type")
    if not doc_type_str:
        raise SystemExit("В JSON отсутствует поле 'doc_type'")

    try:
        doc_type = DocumentType(doc_type_str)
    except ValueError:
        raise SystemExit(f"Неизвестный doc_type: {doc_type_str}")

    engine = create_async_engine(settings.async_database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Очистка существующих данных (опционально)
        if clear_existing:
            logger.info(f"Очистка существующей taxonomy для {doc_type.value}...")
            # Удаляем в правильном порядке (сначала related, потом aliases, потом nodes)
            await session.execute(
                select(SectionTaxonomyRelated).where(
                    SectionTaxonomyRelated.doc_type == doc_type
                )
            )
            related_to_delete = await session.execute(
                select(SectionTaxonomyRelated).where(
                    SectionTaxonomyRelated.doc_type == doc_type
                )
            )
            for rel in related_to_delete.scalars().all():
                await session.delete(rel)

            aliases_to_delete = await session.execute(
                select(SectionTaxonomyAlias).where(
                    SectionTaxonomyAlias.doc_type == doc_type
                )
            )
            for alias in aliases_to_delete.scalars().all():
                await session.delete(alias)

            nodes_to_delete = await session.execute(
                select(SectionTaxonomyNode).where(
                    SectionTaxonomyNode.doc_type == doc_type
                )
            )
            for node in nodes_to_delete.scalars().all():
                await session.delete(node)

            await session.commit()
            logger.info("Очистка завершена")

        # Импорт узлов
        nodes_data = data.get("nodes", [])
        logger.info(f"Импорт {len(nodes_data)} узлов...")
        for node_data in nodes_data:
            section_key = node_data.get("section_key")
            if not section_key:
                logger.warning("Пропущен узел без section_key")
                continue

            # Проверяем существование
            stmt = select(SectionTaxonomyNode).where(
                SectionTaxonomyNode.doc_type == doc_type,
                SectionTaxonomyNode.section_key == section_key,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            node = SectionTaxonomyNode(
                doc_type=doc_type,
                section_key=section_key,
                title_ru=node_data.get("title_ru", ""),
                parent_section_key=node_data.get("parent"),
                is_narrow=bool(node_data.get("is_narrow", False)),
                expected_content=node_data.get("expected_content"),
            )

            if existing:
                existing.title_ru = node.title_ru
                existing.parent_section_key = node.parent_section_key
                existing.is_narrow = node.is_narrow
                existing.expected_content = node.expected_content
                logger.info(f"UPDATED node: {section_key}")
            else:
                session.add(node)
                logger.info(f"ADDED node: {section_key}")

        await session.commit()

        # Импорт алиасов
        aliases_data = data.get("aliases", [])
        logger.info(f"Импорт {len(aliases_data)} алиасов...")
        for alias_data in aliases_data:
            alias_key = alias_data.get("alias_key")
            canonical_key = alias_data.get("canonical_key")
            if not alias_key or not canonical_key:
                logger.warning("Пропущен алиас без alias_key или canonical_key")
                continue

            stmt = select(SectionTaxonomyAlias).where(
                SectionTaxonomyAlias.doc_type == doc_type,
                SectionTaxonomyAlias.alias_key == alias_key,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            alias = SectionTaxonomyAlias(
                doc_type=doc_type,
                alias_key=alias_key,
                canonical_key=canonical_key,
                reason=alias_data.get("reason"),
            )

            if existing:
                existing.canonical_key = alias.canonical_key
                existing.reason = alias.reason
                logger.info(f"UPDATED alias: {alias_key} -> {canonical_key}")
            else:
                session.add(alias)
                logger.info(f"ADDED alias: {alias_key} -> {canonical_key}")

        await session.commit()

        # Импорт related
        related_data = data.get("related", [])
        logger.info(f"Импорт {len(related_data)} связей...")
        for related_item in related_data:
            a_key = related_item.get("a")
            b_key = related_item.get("b")
            if not a_key or not b_key:
                logger.warning("Пропущена связь без 'a' или 'b'")
                continue

            # Нормализуем порядок
            a_key, b_key = _normalize_related_pair(a_key, b_key)

            stmt = select(SectionTaxonomyRelated).where(
                SectionTaxonomyRelated.doc_type == doc_type,
                SectionTaxonomyRelated.a_section_key == a_key,
                SectionTaxonomyRelated.b_section_key == b_key,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            related = SectionTaxonomyRelated(
                doc_type=doc_type,
                a_section_key=a_key,
                b_section_key=b_key,
                reason=related_item.get("reason"),
            )

            if existing:
                existing.reason = related.reason
                logger.info(f"UPDATED related: {a_key} <-> {b_key}")
            else:
                session.add(related)
                logger.info(f"ADDED related: {a_key} <-> {b_key}")

        await session.commit()
        logger.info(f"Импорт taxonomy для {doc_type.value} завершен успешно")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт Section Taxonomy из JSON")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(__file__).parent.parent / "data" / "section_taxonomy_protocol.json",
        help="Путь к JSON файлу taxonomy",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Очистить существующую taxonomy перед импортом",
    )
    args = parser.parse_args()

    # Исправление для Windows: используем SelectorEventLoop вместо ProactorEventLoop
    # для совместимости с psycopg (асинхронный драйвер PostgreSQL)
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                seed_taxonomy(taxonomy_file=args.file, clear_existing=args.clear)
            )
        finally:
            loop.close()
    else:
        asyncio.run(seed_taxonomy(taxonomy_file=args.file, clear_existing=args.clear))


if __name__ == "__main__":
    main()

