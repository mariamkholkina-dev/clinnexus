"""Initial prod-ready schema for ClinNexus.

Создаёт все основные таблицы, enum-типы, индексы и расширение pgvector.
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0001_initial_prod_skeleton"
down_revision = None
branch_labels = None
depends_on = None


def _create_enums() -> None:
    # Multi-tenant / auth
    op.execute(
        "CREATE TYPE workspace_role AS ENUM ('admin','writer','clinops','qa')"
    )

    # Studies / documents
    op.execute("CREATE TYPE study_status AS ENUM ('active','archived')")
    op.execute(
        "CREATE TYPE document_type AS ENUM "
        "('protocol','sap','tfl','csr','ib','icf','other')"
    )
    op.execute(
        "CREATE TYPE document_lifecycle_status AS ENUM "
        "('draft','in_review','approved','superseded')"
    )
    op.execute(
        "CREATE TYPE ingestion_status AS ENUM "
        "('uploaded','processing','ready','needs_review','failed')"
    )

    # Anchors / chunks
    op.execute(
        "CREATE TYPE anchor_content_type AS ENUM "
        "('p','cell','fn','hdr','li','tbl')"
    )

    # Sections
    op.execute(
        "CREATE TYPE citation_policy AS ENUM "
        "('per_sentence','per_claim','none')"
    )
    op.execute(
        "CREATE TYPE section_map_status AS ENUM "
        "('mapped','needs_review','overridden')"
    )
    op.execute(
        "CREATE TYPE section_map_mapped_by AS ENUM "
        "('system','user')"
    )

    # Facts
    op.execute(
        "CREATE TYPE fact_status AS ENUM "
        "('extracted','validated','conflicting','tbd','needs_review')"
    )
    op.execute(
        "CREATE TYPE evidence_role AS ENUM "
        "('primary','supporting')"
    )

    # Conflicts
    op.execute(
        "CREATE TYPE conflict_severity AS ENUM "
        "('low','medium','high','critical')"
    )
    op.execute(
        "CREATE TYPE conflict_status AS ENUM "
        "('open','investigating','resolved','accepted_risk','suppressed')"
    )

    # Generation / QC
    op.execute(
        "CREATE TYPE generation_status AS ENUM "
        "('queued','running','blocked','completed','failed')"
    )
    op.execute(
        "CREATE TYPE qc_status AS ENUM "
        "('pass','fail','blocked')"
    )

    # Change / tasks
    op.execute(
        "CREATE TYPE recommended_action AS ENUM "
        "('auto_patch','regenerate_draft','manual_review')"
    )
    op.execute(
        "CREATE TYPE impact_status AS ENUM "
        "('pending','applied','rejected')"
    )
    op.execute(
        "CREATE TYPE task_type AS ENUM "
        "('review_extraction','resolve_conflict','review_impact','regenerate_section')"
    )
    op.execute(
        "CREATE TYPE task_status AS ENUM "
        "('open','in_progress','done','cancelled')"
    )


def upgrade() -> None:
    # Включаем расширение pgvector
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    _create_enums()

    # A) workspaces / users / memberships
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM(name="workspace_role", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_memberships_workspace_user",
        "memberships",
        ["workspace_id", "user_id"],
    )

    # B) studies / documents / document_versions
    op.create_table(
        "studies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("study_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="study_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_studies_workspace_code",
        "studies",
        ["workspace_id", "study_code"],
    )

    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "lifecycle_status",
            postgresql.ENUM(name="document_lifecycle_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "document_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(length=64), nullable=False),
        sa.Column("source_file_uri", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column(
            "ingestion_status",
            postgresql.ENUM(name="ingestion_status", create_type=False),
            nullable=False,
        ),
        sa.Column("ingestion_summary_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_document_versions_document_created_at",
        "document_versions",
        ["document_id", sa.text("created_at DESC")],
    )

    # C) anchors
    op.create_table(
        "anchors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("anchor_id", sa.String(length=512), nullable=False, unique=True),
        sa.Column("section_path", sa.Text(), nullable=False),
        sa.Column(
            "content_type",
            postgresql.ENUM(name="anchor_content_type", create_type=False),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text_raw", sa.Text(), nullable=False),
        sa.Column("text_norm", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("location_json", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("anchor_id", name="uq_anchors_anchor_id"),
    )
    op.create_index(
        "ix_anchors_doc_version_section_path",
        "anchors",
        ["doc_version_id", "section_path"],
    )
    op.create_index(
        "ix_anchors_doc_version_content_type",
        "anchors",
        ["doc_version_id", "content_type"],
    )

    # D) chunks (pgvector narrative index)
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_id", sa.String(length=512), nullable=False, unique=True),
        sa.Column("section_path", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("anchor_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("embedding", sa.dialects.postgresql.ARRAY(sa.Float()), nullable=False),  # Будет конвертировано в vector(1536) в миграции 0002
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_chunks_doc_version_section_path",
        "chunks",
        ["doc_version_id", "section_path"],
    )
    # Векторный индекс будет создан в миграции 0002 после конвертации в vector(1536)

    # E) semantic section passports + mapping
    op.create_table(
        "section_contracts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("required_facts_json", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_sources_json", postgresql.JSONB(), nullable=False),
        sa.Column("retrieval_recipe_json", postgresql.JSONB(), nullable=False),
        sa.Column("qc_ruleset_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "citation_policy",
            postgresql.ENUM(name="citation_policy", create_type=False),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_section_contracts_ws_doc_type_key_version",
        "section_contracts",
        ["workspace_id", "doc_type", "section_key", "version"],
    )

    op.create_table(
        "section_maps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column("anchor_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("chunk_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="section_map_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "mapped_by",
            postgresql.ENUM(name="section_map_mapped_by", create_type=False),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_section_maps_doc_version_section_key",
        "section_maps",
        ["doc_version_id", "section_key"],
    )

    # F) facts + fact_evidence
    op.create_table(
        "facts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_type", sa.Text(), nullable=False),
        sa.Column("fact_key", sa.Text(), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="fact_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_from_doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_facts_study_fact_type",
        "facts",
        ["study_id", "fact_type"],
    )
    op.create_unique_constraint(
        "uq_facts_study_type_key",
        "facts",
        ["study_id", "fact_type", "fact_key"],
    )

    op.create_table(
        "fact_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "fact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("anchor_id", sa.Text(), nullable=False),
        sa.Column(
            "evidence_role",
            postgresql.ENUM(name="evidence_role", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # G) templates + generation + QC gate
    op.create_table(
        "templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("template_body", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_templates_ws_doc_type_name_version",
        "templates",
        ["workspace_id", "doc_type", "name", "version"],
    )

    op.create_table(
        "model_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("params_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "generation_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_doc_type",
            sa.Enum(name="document_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("section_contracts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("input_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "model_config_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_configs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="generation_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "generated_sections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("artifacts_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "qc_status",
            postgresql.ENUM(name="qc_status", create_type=False),
            nullable=False,
        ),
        sa.Column("qc_report_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "published_to_document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # H) conflicts + workflow
    op.create_table(
        "conflicts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conflict_type", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            postgresql.ENUM(name="conflict_severity", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="conflict_status", create_type=False),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conflicts_study_status",
        "conflicts",
        ["study_id", "status"],
    )

    op.create_table(
        "conflict_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "conflict_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conflicts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("left_anchor_id", sa.Text(), nullable=True),
        sa.Column("right_anchor_id", sa.Text(), nullable=True),
        sa.Column(
            "left_fact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "right_fact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("facts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # I) change management: diff -> impact -> tasks
    op.create_table(
        "change_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("diff_summary_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "impact_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "change_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("change_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "affected_doc_type",
            sa.Enum(name="document_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("affected_section_key", sa.Text(), nullable=False),
        sa.Column("reason_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recommended_action",
            postgresql.ENUM(name="recommended_action", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="impact_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "type",
            postgresql.ENUM(name="task_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="task_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # J) audit log
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("before_json", postgresql.JSONB(), nullable=True),
        sa.Column("after_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_audit_log_workspace_created_at",
        "audit_log",
        ["workspace_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_log_entity",
        "audit_log",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    # Таблицы удаляем в обратном порядке зависимостей
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_workspace_created_at", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_table("tasks")
    op.drop_table("impact_items")
    op.drop_table("change_events")

    op.drop_table("conflict_items")
    op.drop_index("ix_conflicts_study_status", table_name="conflicts")
    op.drop_table("conflicts")

    op.drop_table("generated_sections")
    op.drop_table("generation_runs")
    op.drop_table("model_configs")
    op.drop_table("templates")

    op.drop_table("fact_evidence")
    op.drop_unique_constraint("uq_facts_study_type_key", "facts")
    op.drop_index("ix_facts_study_fact_type", table_name="facts")
    op.drop_table("facts")

    op.drop_unique_constraint(
        "uq_section_maps_doc_version_section_key",
        "section_maps",
    )
    op.drop_table("section_maps")
    op.drop_unique_constraint(
        "uq_section_contracts_ws_doc_type_key_version",
        "section_contracts",
    )
    op.drop_table("section_contracts")

    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_vector")
    op.drop_index(
        "ix_chunks_doc_version_section_path",
        table_name="chunks",
    )
    op.drop_table("chunks")

    op.drop_index(
        "ix_anchors_doc_version_content_type",
        table_name="anchors",
    )
    op.drop_index(
        "ix_anchors_doc_version_section_path",
        table_name="anchors",
    )
    op.drop_table("anchors")

    op.drop_index(
        "ix_document_versions_document_created_at",
        table_name="document_versions",
    )
    op.drop_table("document_versions")
    op.drop_table("documents")

    op.drop_unique_constraint("uq_studies_workspace_code", "studies")
    op.drop_table("studies")

    op.drop_unique_constraint("uq_memberships_workspace_user", "memberships")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("workspaces")

    # Enum-типы
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("DROP TYPE IF EXISTS task_type")
    op.execute("DROP TYPE IF EXISTS impact_status")
    op.execute("DROP TYPE IF EXISTS recommended_action")
    op.execute("DROP TYPE IF EXISTS qc_status")
    op.execute("DROP TYPE IF EXISTS generation_status")
    op.execute("DROP TYPE IF EXISTS conflict_status")
    op.execute("DROP TYPE IF EXISTS conflict_severity")
    op.execute("DROP TYPE IF EXISTS evidence_role")
    op.execute("DROP TYPE IF EXISTS fact_status")
    op.execute("DROP TYPE IF EXISTS section_map_mapped_by")
    op.execute("DROP TYPE IF EXISTS section_map_status")
    op.execute("DROP TYPE IF EXISTS citation_policy")
    op.execute("DROP TYPE IF EXISTS anchor_content_type")
    op.execute("DROP TYPE IF EXISTS ingestion_status")
    op.execute("DROP TYPE IF EXISTS document_lifecycle_status")
    op.execute("DROP TYPE IF EXISTS document_type")
    op.execute("DROP TYPE IF EXISTS study_status")
    op.execute("DROP TYPE IF EXISTS workspace_role")

    # Расширение vector можно оставить, так как оно может использоваться и в других схемах;
    # при необходимости можно раскомментировать:
    # op.execute("DROP EXTENSION IF EXISTS vector")


