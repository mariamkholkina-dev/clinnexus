from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.llm_security import maybe_get_byo_key
from app.schemas.generation import GenerateSectionRequest, GenerateSectionResult
from app.services.generation import GenerationService

router = APIRouter()


@router.post(
    "/generate/section",
    response_model=GenerateSectionResult,
    status_code=status.HTTP_200_OK,
)
async def generate_section(
    payload: GenerateSectionRequest,
    db: AsyncSession = Depends(get_db),
    byo_key: str | None = Depends(maybe_get_byo_key),
) -> GenerateSectionResult:
    """Генерация секции документа."""
    generation_service = GenerationService(db)
    result = await generation_service.generate_section(payload, byo_key=byo_key)
    return result



