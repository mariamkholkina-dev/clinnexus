"""Добавление таблицы anchor_matches для выравнивания якорей между версиями документов.

Создаёт:
- anchor_matches: соответствия между якорями двух версий документа для diff/impact анализа
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009_add_anchor_matches"
down_revision = "0008_add_topics_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # Таблица anchor_matches
    # ========================================================================
    op.create_table(
        "anchor_matches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_anchor_id", sa.Text(), nullable=False),
        sa.Column("to_anchor_id", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("meta_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Уникальный индекс для (from_doc_version_id, to_doc_version_id, from_anchor_id)
    op.create_unique_constraint(
        "uq_anchor_matches_version_from_anchor",
        "anchor_matches",
        ["from_doc_version_id", "to_doc_version_id", "from_anchor_id"],
    )
    
    # Индексы для быстрого поиска
    op.create_index(
        "ix_anchor_matches_document_id",
        "anchor_matches",
        ["document_id"],
    )
    op.create_index(
        "ix_anchor_matches_versions",
        "anchor_matches",
        ["from_doc_version_id", "to_doc_version_id"],
    )
    op.create_index(
        "ix_anchor_matches_to_anchor",
        "anchor_matches",
        ["to_doc_version_id", "to_anchor_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_anchor_matches_to_anchor", table_name="anchor_matches")
    op.drop_index("ix_anchor_matches_versions", table_name="anchor_matches")
    op.drop_index("ix_anchor_matches_document_id", table_name="anchor_matches")
    op.drop_constraint(
        "uq_anchor_matches_version_from_anchor",
        "anchor_matches",
        type_="unique",
    )
    op.drop_table("anchor_matches")

