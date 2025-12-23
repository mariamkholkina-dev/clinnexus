"""Добавление таблицы study_core_facts для хранения основных фактов исследования.

Создаёт:
- study_core_facts: структурированные основные факты исследования (study_title, phase, 
  study_design_type, population_short, arms, primary_endpoints, sample_size, duration)
  с версионированием и привязкой к doc_version_id
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0010_add_study_core_facts"
down_revision = "0009_add_anchor_matches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # Таблица study_core_facts
    # ========================================================================
    op.create_table(
        "study_core_facts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("facts_json", postgresql.JSONB(), nullable=False),
        sa.Column("facts_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "derived_from_doc_version_id",
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
    
    # Индекс для быстрого поиска по study_id и версии
    op.create_index(
        "ix_study_core_facts_study_version",
        "study_core_facts",
        ["study_id", "facts_version"],
    )
    
    # Индекс для поиска по doc_version_id
    op.create_index(
        "ix_study_core_facts_doc_version",
        "study_core_facts",
        ["doc_version_id"],
    )


def downgrade() -> None:
    # Удаляем индексы и таблицу
    op.drop_index("ix_study_core_facts_doc_version", table_name="study_core_facts")
    op.drop_index("ix_study_core_facts_study_version", table_name="study_core_facts")
    op.drop_table("study_core_facts")

