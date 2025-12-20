"""Добавление таблиц section taxonomy.

Создает три таблицы:
- section_taxonomy_nodes: иерархия секций (parent->child)
- section_taxonomy_aliases: алиасы секций (alias -> canonical)
- section_taxonomy_related: связанные секции (граф конфликтов)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_add_section_taxonomy"
down_revision = "0005_unique_fact_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Таблица узлов taxonomy (иерархия секций)
    op.create_table(
        "section_taxonomy_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("title_ru", sa.Text, nullable=False),
        sa.Column("parent_section_key", sa.Text, nullable=True),
        sa.Column("is_narrow", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("expected_content", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, onupdate=sa.func.now()),
    )
    
    # Уникальность section_key в рамках doc_type
    op.create_index(
        "uq_section_taxonomy_nodes_doc_type_section_key",
        "section_taxonomy_nodes",
        ["doc_type", "section_key"],
        unique=True,
    )
    
    # Индекс для быстрого поиска по parent
    op.create_index(
        "ix_section_taxonomy_nodes_parent",
        "section_taxonomy_nodes",
        ["doc_type", "parent_section_key"],
    )
    
    # Таблица алиасов
    op.create_table(
        "section_taxonomy_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("alias_key", sa.Text, nullable=False),
        sa.Column("canonical_key", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    
    # Уникальность alias_key в рамках doc_type
    op.create_index(
        "uq_section_taxonomy_aliases_doc_type_alias_key",
        "section_taxonomy_aliases",
        ["doc_type", "alias_key"],
        unique=True,
    )
    
    # Индекс для быстрого поиска canonical
    op.create_index(
        "ix_section_taxonomy_aliases_canonical",
        "section_taxonomy_aliases",
        ["doc_type", "canonical_key"],
    )
    
    # Таблица связанных секций (двунаправленный граф)
    op.create_table(
        "section_taxonomy_related",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("a_section_key", sa.Text, nullable=False),
        sa.Column("b_section_key", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    
    # Уникальность пары (a, b) в нормализованном порядке (лексикографически)
    # В приложении гарантируем, что a_section_key < b_section_key
    op.create_index(
        "uq_section_taxonomy_related_doc_type_ab",
        "section_taxonomy_related",
        ["doc_type", "a_section_key", "b_section_key"],
        unique=True,
    )
    
    # Индексы для быстрого поиска связанных секций
    op.create_index(
        "ix_section_taxonomy_related_a",
        "section_taxonomy_related",
        ["doc_type", "a_section_key"],
    )
    op.create_index(
        "ix_section_taxonomy_related_b",
        "section_taxonomy_related",
        ["doc_type", "b_section_key"],
    )


def downgrade() -> None:
    op.drop_table("section_taxonomy_related")
    op.drop_table("section_taxonomy_aliases")
    op.drop_table("section_taxonomy_nodes")

