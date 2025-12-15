from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import SoAResult, FactItem


class IngestionService(Protocol):
    async def upload_document_version(
        self,
        db: AsyncSession,
        document_version_id: int,
        file_path: str,
    ) -> None:  # pragma: no cover - interface
        ...

    async def start_ingestion(self, db: AsyncSession, document_version_id: int) -> None:  # pragma: no cover - interface
        ...


class SoAExtractionService(Protocol):
    async def extract_soa(
        self,
        db: AsyncSession,
        document_version_id: int,
    ) -> SoAResult:  # pragma: no cover - interface
        ...


class FactExtractionService(Protocol):
    async def extract_facts(
        self,
        db: AsyncSession,
        study_id: int,
    ) -> list[FactItem]:  # pragma: no cover - interface
        ...


class DummyIngestionService:
    """Заглушка ingestion-сервиса. Реальная логика будет добавлена позже."""

    async def upload_document_version(
        self,
        db: AsyncSession,  # noqa: ARG002
        document_version_id: int,  # noqa: ARG002
        file_path: str,  # noqa: ARG002
    ) -> None:
        return None

    async def start_ingestion(
        self,
        db: AsyncSession,  # noqa: ARG002
        document_version_id: int,  # noqa: ARG002
    ) -> None:
        return None


class DummySoAExtractionService:
    async def extract_soa(
        self,
        db: AsyncSession,  # noqa: ARG002
        document_version_id: int,  # noqa: ARG002
    ) -> SoAResult:
        # TODO: заменить на реальную реализацию
        return SoAResult(visits=[], procedures=[], matrix=[])


class DummyFactExtractionService:
    async def extract_facts(
        self,
        db: AsyncSession,  # noqa: ARG002
        study_id: int,  # noqa: ARG002
    ) -> list[FactItem]:
        # TODO: заменить на реальную реализацию
        return []



