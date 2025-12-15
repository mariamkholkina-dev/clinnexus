from __future__ import annotations

"""
Пакет ORM-моделей ClinNexus.

Модели сгруппированы по доменам:
- auth: workspaces / users / memberships
- studies: studies / documents / document_versions
- anchors: anchors / chunks
- sections: section_contracts / section_maps
- facts: facts / fact_evidence
- generation: templates / model_configs / generation_runs / generated_sections
- conflicts: conflicts / conflict_items
- change: change_events / impact_items / tasks
- audit: audit_log
"""

from .auth import Membership, User, Workspace  # noqa: F401
from .audit import AuditLog  # noqa: F401
from .change import ChangeEvent, ImpactItem, Task  # noqa: F401
from .anchors import Anchor, Chunk  # noqa: F401
from .conflicts import Conflict, ConflictItem  # noqa: F401
from .facts import Fact, FactEvidence  # noqa: F401
from .generation import (  # noqa: F401
    GeneratedSection,
    GenerationRun,
    ModelConfig,
    Template,
)
from .sections import SectionContract, SectionMap  # noqa: F401
from .studies import Document, DocumentVersion, Study  # noqa: F401


