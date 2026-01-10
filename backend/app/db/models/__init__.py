from __future__ import annotations

"""
Пакет ORM-моделей ClinNexus.

Модели сгруппированы по доменам:
- auth: workspaces / users / memberships
- studies: studies / documents / document_versions
- anchors: anchors / chunks
- sections: target_section_contracts / target_section_maps
- facts: facts / fact_evidence
- generation: templates / model_configs / generation_runs / generated_target_sections
- conflicts: conflicts / conflict_items
- change: change_events / impact_items / tasks
- audit: audit_log
- zones: zone_sets / zone_crosswalk
"""

from .auth import Membership, User, Workspace  # noqa: F401
from .audit import AuditIssue, AuditLog  # noqa: F401
from .change import ChangeEvent, ImpactItem, Task  # noqa: F401
from .dictionaries import TerminologyDictionary  # noqa: F401
from .anchors import Anchor, Chunk  # noqa: F401
from .anchor_matches import AnchorMatch  # noqa: F401
from .conflicts import Conflict, ConflictItem  # noqa: F401
from .core_facts import StudyCoreFacts  # noqa: F401
from .facts import Fact, FactEvidence  # noqa: F401
from .generation import (  # noqa: F401
    GeneratedTargetSection,
    GenerationRun,
    ModelConfig,
    Template,
)
from .ingestion_runs import IngestionRun  # noqa: F401
from .sections import TargetSectionContract, TargetSectionMap  # noqa: F401
from .studies import Document, DocumentVersion, Study  # noqa: F401
from .topics import (  # noqa: F401
    ClusterAssignment,
    HeadingCluster,
    Topic,
    TopicEvidence,
    TopicMappingRun,
)
from .zones import ZoneCrosswalk, ZoneSet  # noqa: F401


