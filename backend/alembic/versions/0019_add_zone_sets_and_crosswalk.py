"""Добавление таблиц zone_sets и zone_crosswalk для кросс-документного связывания.

Создаёт:
- Таблицу zone_sets: doc_type -> список zone_key
- Таблицу zone_crosswalk: маппинг между зонами разных doc_types с весами
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0019_zone_sets_crosswalk"
down_revision = "0018_topic_doc_type_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Создаём таблицу zone_sets
    # ========================================================================
    op.create_table(
        "zone_sets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("zone_key", sa.Text(), nullable=False),
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

    # Уникальный constraint для (doc_type, zone_key)
    op.create_unique_constraint(
        "uq_zone_sets_doc_type_zone_key",
        "zone_sets",
        ["doc_type", "zone_key"],
    )

    # Индексы для быстрого поиска
    op.create_index(
        "ix_zone_sets_doc_type",
        "zone_sets",
        ["doc_type"],
    )
    op.create_index(
        "ix_zone_sets_doc_type_is_active",
        "zone_sets",
        ["doc_type", "is_active"],
    )

    # ========================================================================
    # 2. Создаём таблицу zone_crosswalk
    # ========================================================================
    op.create_table(
        "zone_crosswalk",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "from_doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("from_zone_key", sa.Text(), nullable=False),
        sa.Column(
            "to_doc_type",
            postgresql.ENUM(name="document_type", create_type=False),
            nullable=False,
        ),
        sa.Column("to_zone_key", sa.Text(), nullable=False),
        sa.Column(
            "weight",
            sa.Numeric(3, 2),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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

    # Уникальный constraint для (from_doc_type, from_zone_key, to_doc_type, to_zone_key)
    op.create_unique_constraint(
        "uq_zone_crosswalk_from_to",
        "zone_crosswalk",
        ["from_doc_type", "from_zone_key", "to_doc_type", "to_zone_key"],
    )

    # Индексы для быстрого поиска
    op.create_index(
        "ix_zone_crosswalk_from",
        "zone_crosswalk",
        ["from_doc_type", "from_zone_key"],
    )
    op.create_index(
        "ix_zone_crosswalk_to",
        "zone_crosswalk",
        ["to_doc_type", "to_zone_key"],
    )
    op.create_index(
        "ix_zone_crosswalk_from_to_type",
        "zone_crosswalk",
        ["from_doc_type", "from_zone_key", "to_doc_type"],
    )
    op.create_index(
        "ix_zone_crosswalk_is_active",
        "zone_crosswalk",
        ["is_active"],
    )


def downgrade() -> None:
    # Удаляем таблицу zone_crosswalk
    op.drop_index("ix_zone_crosswalk_is_active", table_name="zone_crosswalk")
    op.drop_index("ix_zone_crosswalk_from_to_type", table_name="zone_crosswalk")
    op.drop_index("ix_zone_crosswalk_to", table_name="zone_crosswalk")
    op.drop_index("ix_zone_crosswalk_from", table_name="zone_crosswalk")
    op.drop_constraint(
        "uq_zone_crosswalk_from_to",
        "zone_crosswalk",
        type_="unique",
    )
    op.drop_table("zone_crosswalk")

    # Удаляем таблицу zone_sets
    op.drop_index("ix_zone_sets_doc_type_is_active", table_name="zone_sets")
    op.drop_index("ix_zone_sets_doc_type", table_name="zone_sets")
    op.drop_constraint(
        "uq_zone_sets_doc_type_zone_key",
        "zone_sets",
        type_="unique",
    )
    op.drop_table("zone_sets")

