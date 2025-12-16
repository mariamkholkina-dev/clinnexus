from __future__ import annotations

"""
Единый модуль Python Enum-ов для доменной модели.

Эти enum-ы используются как в ORM-моделях, так и в Alembic-миграциях.
"""

from enum import Enum


class WorkspaceRole(str, Enum):
    ADMIN = "admin"
    WRITER = "writer"
    CLINOPS = "clinops"
    QA = "qa"


class StudyStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DocumentType(str, Enum):
    PROTOCOL = "protocol"
    SAP = "sap"
    TFL = "tfl"
    CSR = "csr"
    IB = "ib"
    ICF = "icf"
    OTHER = "other"


class DocumentLifecycleStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class IngestionStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class DocumentLanguage(str, Enum):
    """Язык документа."""
    
    RU = "ru"
    EN = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class AnchorContentType(str, Enum):
    """Тип контента якоря.

    anchor_id НЕ зависит от структуры документа; section_path хранится отдельно
    и отражает только текущую структуру данного документа.
    """

    P = "p"
    CELL = "cell"
    FN = "fn"
    HDR = "hdr"
    LI = "li"
    TBL = "tbl"


class CitationPolicy(str, Enum):
    PER_SENTENCE = "per_sentence"
    PER_CLAIM = "per_claim"
    NONE = "none"


class SectionMapStatus(str, Enum):
    MAPPED = "mapped"
    NEEDS_REVIEW = "needs_review"
    OVERRIDDEN = "overridden"


class SectionMapMappedBy(str, Enum):
    SYSTEM = "system"
    USER = "user"


class FactStatus(str, Enum):
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    CONFLICTING = "conflicting"
    TBD = "tbd"
    NEEDS_REVIEW = "needs_review"


class EvidenceRole(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class ConflictSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConflictStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    ACCEPTED_RISK = "accepted_risk"
    SUPPRESSED = "suppressed"


class GenerationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class QCStatus(str, Enum):
    PASSED = "pass"
    FAILED = "fail"
    BLOCKED = "blocked"


class RecommendedAction(str, Enum):
    AUTO_PATCH = "auto_patch"
    REGENERATE_DRAFT = "regenerate_draft"
    MANUAL_REVIEW = "manual_review"


class ImpactStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class TaskType(str, Enum):
    REVIEW_EXTRACTION = "review_extraction"
    RESOLVE_CONFLICT = "resolve_conflict"
    REVIEW_IMPACT = "review_impact"
    REGENERATE_SECTION = "regenerate_section"


class TaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"



