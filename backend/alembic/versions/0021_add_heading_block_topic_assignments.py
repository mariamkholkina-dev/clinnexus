"""Добавление таблицы heading_block_topic_assignments для прямого маппинга блоков на топики.

Создаёт:
- heading_block_topic_assignments: привязка heading_block_id к topic_key для doc_version
  (блоки строятся динамически из anchors, поэтому heading_block_id - это стабильный идентификатор)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0021_heading_block_assignments"
down_revision = "0020_drop_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # Таблица heading_block_topic_assignments
    # ========================================================================
    op.create_table(
        "heading_block_topic_assignments",
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
        sa.Column("heading_block_id", sa.Text(), nullable=False),
        sa.Column("topic_key", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("debug_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Уникальный индекс для (doc_version_id, heading_block_id)
    op.create_unique_constraint(
        "uq_heading_block_assignments_doc_version_block",
        "heading_block_topic_assignments",
        ["doc_version_id", "heading_block_id"],
    )
    
    # Индексы для быстрого поиска
    op.create_index(
        "ix_heading_block_assignments_doc_version_id",
        "heading_block_topic_assignments",
        ["doc_version_id"],
    )
    op.create_index(
        "ix_heading_block_assignments_topic_key",
        "heading_block_topic_assignments",
        ["topic_key"],
    )


def downgrade() -> None:
    # Удаляем индексы и таблицу
    op.drop_index(
        "ix_heading_block_assignments_topic_key",
        table_name="heading_block_topic_assignments",
    )
    op.drop_index(
        "ix_heading_block_assignments_doc_version_id",
        table_name="heading_block_topic_assignments",
    )
    op.drop_constraint(
        "uq_heading_block_assignments_doc_version_block",
        "heading_block_topic_assignments",
        type_="unique",
    )
    op.drop_table("heading_block_topic_assignments")

