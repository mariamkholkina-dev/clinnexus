from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class ImpactService(Protocol):
    async def list_impact_items(
        self,
        db: AsyncSession,
        study_id: int,
    ) -> list[dict]:  # pragma: no cover - interface
        ...


class ConflictService(Protocol):
    async def list_conflicts(
        self,
        db: AsyncSession,
        study_id: int,
    ) -> list[dict]:  # pragma: no cover - interface
        ...


class DummyImpactService:
    async def list_impact_items(
        self,
        db: AsyncSession,  # noqa: ARG002
        study_id: int,  # noqa: ARG002
    ) -> list[dict]:
        return []


class DummyConflictService:
    async def list_conflicts(
        self,
        db: AsyncSession,  # noqa: ARG002
        study_id: int,  # noqa: ARG002
    ) -> list[dict]:
        return []



