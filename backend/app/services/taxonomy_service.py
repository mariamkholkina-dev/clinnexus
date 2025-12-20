"""Сервис для работы с Section Taxonomy (иерархия и связи секций)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import DocumentType
from app.db.models.taxonomy import (
    SectionTaxonomyNode,
    SectionTaxonomyAlias,
    SectionTaxonomyRelated,
)


class TaxonomyService:
    """Сервис для работы с taxonomy секций."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_node(
        self, doc_type: DocumentType, section_key: str
    ) -> SectionTaxonomyNode | None:
        """Получить узел taxonomy по doc_type и section_key."""
        stmt = select(SectionTaxonomyNode).where(
            SectionTaxonomyNode.doc_type == doc_type,
            SectionTaxonomyNode.section_key == section_key,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def resolve_alias(
        self, doc_type: DocumentType, key: str
    ) -> str:
        """
        Разрешить алиас в canonical_key.
        
        Если key является алиасом, возвращает canonical_key.
        Иначе возвращает key как есть.
        """
        stmt = select(SectionTaxonomyAlias).where(
            SectionTaxonomyAlias.doc_type == doc_type,
            SectionTaxonomyAlias.alias_key == key,
        )
        result = await self.db.execute(stmt)
        alias = result.scalar_one_or_none()
        
        if alias:
            return alias.canonical_key
        return key

    async def normalize_section_key(
        self, doc_type: DocumentType, key: str
    ) -> str:
        """
        Нормализовать section_key: применяет alias resolution.
        
        Это основной метод для нормализации ключей перед сохранением.
        """
        return await self.resolve_alias(doc_type, key)

    async def get_parent(
        self, doc_type: DocumentType, section_key: str
    ) -> SectionTaxonomyNode | None:
        """Получить родительский узел."""
        node = await self.get_node(doc_type, section_key)
        if not node or not node.parent_section_key:
            return None
        
        return await self.get_node(doc_type, node.parent_section_key)

    async def get_children(
        self, doc_type: DocumentType, section_key: str
    ) -> list[SectionTaxonomyNode]:
        """Получить список дочерних узлов."""
        stmt = select(SectionTaxonomyNode).where(
            SectionTaxonomyNode.doc_type == doc_type,
            SectionTaxonomyNode.parent_section_key == section_key,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_siblings(
        self, doc_type: DocumentType, section_key: str
    ) -> list[SectionTaxonomyNode]:
        """Получить список sibling узлов (узлы с тем же parent)."""
        node = await self.get_node(doc_type, section_key)
        if not node or not node.parent_section_key:
            return []
        
        # Получаем всех детей родителя, исключая сам узел
        stmt = select(SectionTaxonomyNode).where(
            SectionTaxonomyNode.doc_type == doc_type,
            SectionTaxonomyNode.parent_section_key == node.parent_section_key,
            SectionTaxonomyNode.section_key != section_key,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_related(
        self, doc_type: DocumentType, section_key: str
    ) -> list[SectionTaxonomyNode]:
        """
        Получить список связанных секций (related_sections).
        
        Возвращает узлы, которые связаны с данной секцией через related.
        """
        # Ищем связи где section_key является либо a, либо b
        stmt = select(SectionTaxonomyRelated).where(
            SectionTaxonomyRelated.doc_type == doc_type,
            or_(
                SectionTaxonomyRelated.a_section_key == section_key,
                SectionTaxonomyRelated.b_section_key == section_key,
            ),
        )
        result = await self.db.execute(stmt)
        related_links = list(result.scalars().all())
        
        # Собираем ключи связанных секций
        related_keys: set[str] = set()
        for link in related_links:
            if link.a_section_key == section_key:
                related_keys.add(link.b_section_key)
            else:
                related_keys.add(link.a_section_key)
        
        # Получаем узлы для связанных ключей
        if not related_keys:
            return []
        
        stmt = select(SectionTaxonomyNode).where(
            SectionTaxonomyNode.doc_type == doc_type,
            SectionTaxonomyNode.section_key.in_(related_keys),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_tree(
        self, doc_type: DocumentType
    ) -> dict[str, Any]:
        """
        Получить полное дерево taxonomy для doc_type.
        
        Возвращает структуру:
        {
            "nodes": [...],
            "aliases": [...],
            "related": [...]
        }
        """
        # Получаем все узлы
        stmt = select(SectionTaxonomyNode).where(
            SectionTaxonomyNode.doc_type == doc_type
        )
        result = await self.db.execute(stmt)
        nodes = list(result.scalars().all())
        
        # Получаем все алиасы
        stmt = select(SectionTaxonomyAlias).where(
            SectionTaxonomyAlias.doc_type == doc_type
        )
        result = await self.db.execute(stmt)
        aliases = list(result.scalars().all())
        
        # Получаем все связи
        stmt = select(SectionTaxonomyRelated).where(
            SectionTaxonomyRelated.doc_type == doc_type
        )
        result = await self.db.execute(stmt)
        related = list(result.scalars().all())
        
        return {
            "nodes": [
                {
                    "section_key": node.section_key,
                    "title_ru": node.title_ru,
                    "parent_section_key": node.parent_section_key,
                    "is_narrow": node.is_narrow,
                    "expected_content": node.expected_content,
                }
                for node in nodes
            ],
            "aliases": [
                {
                    "alias_key": alias.alias_key,
                    "canonical_key": alias.canonical_key,
                    "reason": alias.reason,
                }
                for alias in aliases
            ],
            "related": [
                {
                    "a_section_key": rel.a_section_key,
                    "b_section_key": rel.b_section_key,
                    "reason": rel.reason,
                }
                for rel in related
            ],
        }

    async def is_narrow(
        self, doc_type: DocumentType, section_key: str
    ) -> bool:
        """Проверить, является ли секция узкой (is_narrow=true)."""
        node = await self.get_node(doc_type, section_key)
        return node.is_narrow if node else False

