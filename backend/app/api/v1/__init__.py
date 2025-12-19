from fastapi import APIRouter

from app.api.v1 import studies, documents, generation, conflicts, impact, sections, passport_tuning

router = APIRouter()

router.include_router(studies.router, tags=["studies"])
router.include_router(documents.router, tags=["documents"])
router.include_router(sections.router, tags=["sections"])
router.include_router(generation.router, tags=["generation"])
router.include_router(conflicts.router, tags=["conflicts"])
router.include_router(impact.router, tags=["impact"])
router.include_router(passport_tuning.router, prefix="/passport-tuning")


