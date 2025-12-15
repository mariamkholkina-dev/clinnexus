from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import AnchorContentType


class AnchorOut(BaseModel):
    """Схема для вывода якоря."""

    id: UUID
    anchor_id: str
    section_path: str
    content_type: AnchorContentType
    ordinal: int
    text_raw: str
    location_json: dict[str, Any]
    confidence: float | None

    class Config:
        from_attributes = True


class ChunkOut(BaseModel):
    """Схема для вывода чанка."""

    id: UUID
    chunk_id: str
    section_path: str
    text: str
    anchor_ids: list[str]
    metadata: dict[str, Any] | None

    class Config:
        from_attributes = True

