from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "studies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_id", sa.Integer, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("storage_path", sa.String(length=512)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index(
        "ix_document_versions_doc_version",
        "document_versions",
        ["document_id", "version"],
        unique=True,
    )

    op.create_table(
        "anchors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("anchor_id", sa.String(length=512), nullable=False, unique=True),
        sa.Column(
            "document_version_id",
            sa.Integer,
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("section_path", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=8), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("location_json", postgresql.JSONB, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index(
        "ix_anchors_doc_version_section",
        "anchors",
        ["document_version_id", "section_path"],
        unique=False,
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Integer,
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(dim=1536), nullable=False),
        sa.Column("anchor_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_chunks_doc_version", "chunks", ["document_version_id"], unique=False)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_vector "
        "ON chunks USING ivfflat (embedding vector_cosine_ops);"
    )

    op.create_table(
        "study_facts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_id", sa.Integer, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value_json", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "fact_evidence",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fact_id", sa.Integer, sa.ForeignKey("study_facts.id"), nullable=False),
        sa.Column("anchor_id", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "section_contracts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("templates.id"), nullable=False),
        sa.Column("section_key", sa.String(length=255), nullable=False),
        sa.Column("schema_json", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_id", sa.Integer, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("section_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "generated_sections",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("generation_run_id", sa.Integer, sa.ForeignKey("generation_runs.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("artifacts_json", postgresql.JSONB, nullable=False),
        sa.Column("qc_report_json", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "conflicts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_id", sa.Integer, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "conflict_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conflict_id", sa.Integer, sa.ForeignKey("conflicts.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("left_anchor_id", sa.String(length=512)),
        sa.Column("right_anchor_id", sa.String(length=512)),
    )

    op.create_table(
        "change_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_id", sa.Integer, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "impact_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_id", sa.Integer, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("impact_item_id", sa.Integer, sa.ForeignKey("impact_items.id")),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id")),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("tasks")
    op.drop_table("impact_items")
    op.drop_table("change_events")
    op.drop_table("conflict_items")
    op.drop_table("conflicts")
    op.drop_table("generated_sections")
    op.drop_table("generation_runs")
    op.drop_table("section_contracts")
    op.drop_table("templates")
    op.drop_table("fact_evidence")
    op.drop_table("study_facts")
    op.drop_index("ix_chunks_doc_version", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_anchors_doc_version_section", table_name="anchors")
    op.drop_table("anchors")
    op.drop_index("ix_document_versions_doc_version", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("studies")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("workspaces")


