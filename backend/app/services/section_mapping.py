from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.db.models.sections import SectionMap
from app.db.models.studies import DocumentVersion
from app.db.enums import SectionMapMappedBy, SectionMapStatus
from app.schemas.sections import SectionMapOut


class SectionMappingService:
    """Сервис для маппинга семантических секций на anchors/chunks документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def map_sections(
        self, doc_version_id: UUID, section_keys: list[str]
    ) -> list[SectionMapOut]:
        """
        Маппинг семантических секций на anchors/chunks документа.

        TODO: Реальная реализация должна:
        - Для каждого section_key найти соответствующие anchors/chunks
        - Использовать ML/NLP для определения соответствия
        - Создать или обновить SectionMap записи
        """
        logger.info(f"Маппинг секций для документа {doc_version_id}: {section_keys}")

        # Проверяем существование версии документа
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        results = []

        for section_key in section_keys:
            # Проверяем, существует ли уже маппинг
            stmt = select(SectionMap).where(
                SectionMap.doc_version_id == doc_version_id,
                SectionMap.section_key == section_key,
            )
            result = await self.db.execute(stmt)
            existing_map = result.scalar_one_or_none()

            if existing_map:
                results.append(SectionMapOut.model_validate(existing_map))
                continue

            # TODO: Реальная логика маппинга
            # Здесь должна быть логика:
            # 1. Найти anchors/chunks, которые соответствуют section_key
            # 2. Использовать ML/NLP для определения confidence
            # 3. Создать SectionMap

            # Заглушка: создаём маппинг
            new_map = SectionMap(
                doc_version_id=doc_version_id,
                section_key=section_key,
                anchor_ids=["anchor_1", "anchor_2"],  # TODO: реальные anchor_ids
                chunk_ids=None,  # TODO: реальные chunk_ids
                confidence=0.85,  # TODO: реальный confidence
                status=SectionMapStatus.MAPPED,
                mapped_by=SectionMapMappedBy.SYSTEM,
                notes=None,
            )

            self.db.add(new_map)
            await self.db.commit()
            await self.db.refresh(new_map)

            results.append(SectionMapOut.model_validate(new_map))

        logger.info(f"Маппинг завершён для {doc_version_id}: {len(results)} секций")
        return results

