"""Добавление таблиц для работы с топиками.

Создаёт:
- topics: топики с workspace_id, topic_key (unique), title_ru, title_en, description
- cluster_assignments: привязка кластеров к топикам для doc_version
- topic_evidence: агрегированные доказательства для топиков с anchor_ids, chunk_ids
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_add_topics_tables"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Таблица topics
    # ========================================================================
    op.create_table(
        "topics",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic_key", sa.Text(), nullable=False),
        sa.Column("title_ru", sa.Text(), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Уникальный индекс для (workspace_id, topic_key)
    op.create_unique_constraint(
        "uq_topics_workspace_topic_key",
        "topics",
        ["workspace_id", "topic_key"],
    )
    
    # ========================================================================
    # 2. Таблица cluster_assignments
    # ========================================================================
    op.create_table(
        "cluster_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("topic_key", sa.Text(), nullable=False),
        sa.Column("mapped_by", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Уникальный индекс для (doc_version_id, cluster_id)
    op.create_unique_constraint(
        "uq_cluster_assignments_doc_version_cluster",
        "cluster_assignments",
        ["doc_version_id", "cluster_id"],
    )
    
    # Индекс для topic_key
    op.create_index(
        "ix_cluster_assignments_topic_key",
        "cluster_assignments",
        ["topic_key"],
    )
    
    # ========================================================================
    # 3. Таблица topic_evidence
    # ========================================================================
    op.create_table(
        "topic_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic_key", sa.Text(), nullable=False),
        sa.Column("source_zone", sa.Text(), nullable=False),
        sa.Column(
            "language",
            postgresql.ENUM(name="document_language", create_type=False),
            nullable=False,
        ),
        sa.Column("anchor_ids", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "chunk_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Уникальный индекс для (doc_version_id, topic_key, source_zone, language)
    op.create_unique_constraint(
        "uq_topic_evidence_doc_version_topic_source_lang",
        "topic_evidence",
        ["doc_version_id", "topic_key", "source_zone", "language"],
    )
    
    # Индекс для topic_key
    op.create_index(
        "ix_topic_evidence_topic_key",
        "topic_evidence",
        ["topic_key"],
    )


def downgrade() -> None:
    # Удаляем индексы и таблицы в обратном порядке
    op.drop_index("ix_topic_evidence_topic_key", table_name="topic_evidence")
    op.drop_constraint(
        "uq_topic_evidence_doc_version_topic_source_lang",
        "topic_evidence",
        type_="unique",
    )
    op.drop_table("topic_evidence")
    
    op.drop_index(
        "ix_cluster_assignments_topic_key",
        table_name="cluster_assignments",
    )
    op.drop_constraint(
        "uq_cluster_assignments_doc_version_cluster",
        "cluster_assignments",
        type_="unique",
    )
    op.drop_table("cluster_assignments")
    
    op.drop_constraint(
        "uq_topics_workspace_topic_key",
        "topics",
        type_="unique",
    )
    op.drop_table("topics")

