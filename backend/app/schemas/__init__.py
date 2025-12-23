from __future__ import annotations

from app.schemas.anchors import AnchorOut, ChunkOut
from app.schemas.common import BaseResponse, ErrorResponse
from app.schemas.conflicts import ConflictOut
from app.schemas.documents import (
    DocumentCreate,
    DocumentOut,
    DocumentVersionCreate,
    DocumentVersionOut,
    UploadResult,
)
from app.schemas.facts import FactEvidenceOut, FactOut
from app.schemas.generation import (
    ArtifactsSchema,
    GenerateSectionRequest,
    GenerateSectionResult,
    QCErrorSchema,
    QCReportSchema,
)
from app.schemas.impact import ImpactItemOut
from app.schemas.sections import (
    SectionContractCreate,
    SectionContractOut,
    SectionMapOut,
    SectionMapOverrideRequest,
)
from app.schemas.studies import StudyCreate, StudyOut
from app.schemas.tasks import TaskOut
from app.schemas.topics import (
    ClusterAssignmentOut,
    HeadingClusterOut,
    TopicListItem,
    TopicMappingRunOut,
    TopicOut,
)

__all__ = [
    "BaseResponse",
    "ErrorResponse",
    "StudyCreate",
    "StudyOut",
    "DocumentCreate",
    "DocumentOut",
    "DocumentVersionCreate",
    "DocumentVersionOut",
    "UploadResult",
    "AnchorOut",
    "ChunkOut",
    "SectionContractCreate",
    "SectionContractOut",
    "SectionMapOut",
    "SectionMapOverrideRequest",
    "FactOut",
    "FactEvidenceOut",
    "GenerateSectionRequest",
    "GenerateSectionResult",
    "ArtifactsSchema",
    "QCReportSchema",
    "QCErrorSchema",
    "ConflictOut",
    "ImpactItemOut",
    "TaskOut",
    "TopicOut",
    "TopicListItem",
    "HeadingClusterOut",
    "TopicMappingRunOut",
    "ClusterAssignmentOut",
]

