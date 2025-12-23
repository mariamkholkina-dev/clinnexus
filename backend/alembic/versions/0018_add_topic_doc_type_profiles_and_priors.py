"""Добавление поддержки doc_type профилей и zone priors для topics.

Добавляет:
- В topics: applicable_to_json (JSONB) - список doc_type, к которым применим топик
- Таблицу topic_zone_priors для хранения приоритетов зон по doc_type для топиков
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0018_topic_doc_type_profiles"
down_revision = "0017_rename_section_to_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Добавляем applicable_to_json в таблицу topics
    # ========================================================================
    op.add_column(
        "topics",
        sa.Column(
            "applicable_to_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # Индекс для поиска топиков по doc_type
    op.create_index(
        "ix_topics_applicable_to_json",
        "topics",
        ["applicable_to_json"],
        postgresql_using="gin",
    )

    # ========================================================================
    # 2. Создаём таблицу topic_zone_priors
    # ========================================================================
    op.create_table(
        "topic_zone_priors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "topic_key",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("zone_key", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Уникальный constraint для (topic_key, doc_type, zone_key)
    op.create_unique_constraint(
        "uq_topic_zone_priors_topic_doc_zone",
        "topic_zone_priors",
        ["topic_key", "doc_type", "zone_key"],
    )

    # Индексы для быстрого поиска
    op.create_index(
        "ix_topic_zone_priors_topic_key",
        "topic_zone_priors",
        ["topic_key"],
    )
    op.create_index(
        "ix_topic_zone_priors_doc_type",
        "topic_zone_priors",
        ["doc_type"],
    )
    op.create_index(
        "ix_topic_zone_priors_topic_doc_type",
        "topic_zone_priors",
        ["topic_key", "doc_type"],
    )


def downgrade() -> None:
    # Удаляем таблицу topic_zone_priors
    op.drop_index("ix_topic_zone_priors_topic_doc_type", table_name="topic_zone_priors")
    op.drop_index("ix_topic_zone_priors_doc_type", table_name="topic_zone_priors")
    op.drop_index("ix_topic_zone_priors_topic_key", table_name="topic_zone_priors")
    op.drop_constraint(
        "uq_topic_zone_priors_topic_doc_zone",
        "topic_zone_priors",
        type_="unique",
    )
    op.drop_table("topic_zone_priors")

    # Удаляем поле applicable_to_json из topics
    op.drop_index("ix_topics_applicable_to_json", table_name="topics")
    op.drop_column("topics", "applicable_to_json")

