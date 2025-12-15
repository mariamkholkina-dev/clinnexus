from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger


class SoAExtractionService:
    """Сервис для извлечения Schedule of Activities из документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def extract_soa(self, doc_version_id: UUID) -> dict[str, Any]:
        """
        Извлекает Schedule of Activities из версии документа.

        TODO: Реальная реализация должна:
        - Найти секцию с section_key = "protocol.soa"
        - Извлечь таблицу SoA из anchors/chunks
        - Распарсить visits и procedures
        - Построить матрицу visit x procedure
        """
        logger.info(f"Извлечение SoA из документа {doc_version_id}")

        # TODO: Реальная логика извлечения SoA
        # Здесь должна быть логика:
        # 1. Найти section_map для "protocol.soa"
        # 2. Получить anchors/chunks по anchor_ids/chunk_ids
        # 3. Распарсить таблицу SoA
        # 4. Вернуть структурированные данные

        # Заглушка
        result = {
            "visits": [
                {"visit_id": "V1", "name": "Screening", "day": -28},
                {"visit_id": "V2", "name": "Baseline", "day": 1},
            ],
            "procedures": [
                {"code": "P1", "name": "Physical Exam"},
                {"code": "P2", "name": "Lab Tests"},
            ],
            "matrix": [
                {"visit_id": "V1", "procedure_code": "P1", "value": "X", "anchor_ids": []},
                {"visit_id": "V1", "procedure_code": "P2", "value": "X", "anchor_ids": []},
                {"visit_id": "V2", "procedure_code": "P1", "value": "X", "anchor_ids": []},
                {"visit_id": "V2", "procedure_code": "P2", "value": "O", "anchor_ids": []},
            ],
        }

        logger.info(f"SoA извлечён для {doc_version_id}")
        return result

