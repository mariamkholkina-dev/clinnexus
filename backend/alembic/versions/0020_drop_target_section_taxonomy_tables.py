"""Удаление таблиц target_section_taxonomy_*.

Удаляет таблицы taxonomy, которые больше не используются:
- target_section_taxonomy_aliases
- target_section_taxonomy_related
- target_section_taxonomy_nodes

Также удаляет legacy таблицы section_taxonomy_* если они еще существуют.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0020_drop_taxonomy"
down_revision = "0019_zone_sets_crosswalk"
branch_labels = None
depends_on = None


def table_exists(conn, name: str) -> bool:
    """Проверяет существование таблицы в схеме public."""
    result = conn.execute(
        sa.text("SELECT to_regclass(:q)"), {"q": f"public.{name}"}
    ).scalar()
    return result is not None


def upgrade() -> None:
    """Удаляет таблицы taxonomy."""
    conn = op.get_bind()
    
    # Удаляем в правильном порядке (сначала зависимые таблицы)
    # 1. target_section_taxonomy_aliases (FK к nodes)
    if table_exists(conn, "target_section_taxonomy_aliases"):
        op.drop_table("target_section_taxonomy_aliases", schema="public")
    
    # 2. target_section_taxonomy_related (FK к nodes)
    if table_exists(conn, "target_section_taxonomy_related"):
        op.drop_table("target_section_taxonomy_related", schema="public")
    
    # 3. target_section_taxonomy_nodes
    if table_exists(conn, "target_section_taxonomy_nodes"):
        op.drop_table("target_section_taxonomy_nodes", schema="public")
    
    # Удаляем legacy таблицы section_taxonomy_* если они еще существуют
    if table_exists(conn, "section_taxonomy_aliases"):
        op.drop_table("section_taxonomy_aliases", schema="public")
    
    if table_exists(conn, "section_taxonomy_related"):
        op.drop_table("section_taxonomy_related", schema="public")
    
    if table_exists(conn, "section_taxonomy_nodes"):
        op.drop_table("section_taxonomy_nodes", schema="public")


def downgrade() -> None:
    """Восстанавливает таблицы taxonomy (минимальная реализация для совместимости)."""
    from sqlalchemy.dialects import postgresql
    
    # Восстанавливаем target_section_taxonomy_nodes
    op.create_table(
        "target_section_taxonomy_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("target_section", sa.Text, nullable=False),
        sa.Column("title_ru", sa.Text, nullable=False),
        sa.Column("parent_target_section", sa.Text, nullable=True),
        sa.Column("is_narrow", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("expected_content", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, onupdate=sa.func.now()),
    )
    
    op.create_index(
        "uq_section_taxonomy_nodes_doc_type_section_key",
        "target_section_taxonomy_nodes",
        ["doc_type", "target_section"],
        unique=True,
    )
    
    op.create_index(
        "ix_section_taxonomy_nodes_parent",
        "target_section_taxonomy_nodes",
        ["doc_type", "parent_target_section"],
    )
    
    # Восстанавливаем target_section_taxonomy_aliases
    op.create_table(
        "target_section_taxonomy_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("alias_key", sa.Text, nullable=False),
        sa.Column("canonical_key", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    
    op.create_index(
        "uq_section_taxonomy_aliases_doc_type_alias_key",
        "target_section_taxonomy_aliases",
        ["doc_type", "alias_key"],
        unique=True,
    )
    
    op.create_index(
        "ix_section_taxonomy_aliases_canonical",
        "target_section_taxonomy_aliases",
        ["doc_type", "canonical_key"],
    )
    
    # Восстанавливаем target_section_taxonomy_related
    op.create_table(
        "target_section_taxonomy_related",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("a_target_section", sa.Text, nullable=False),
        sa.Column("b_target_section", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    
    op.create_index(
        "uq_section_taxonomy_related_doc_type_ab",
        "target_section_taxonomy_related",
        ["doc_type", "a_target_section", "b_target_section"],
        unique=True,
    )
    
    op.create_index(
        "ix_section_taxonomy_related_a",
        "target_section_taxonomy_related",
        ["doc_type", "a_target_section"],
    )
    
    op.create_index(
        "ix_section_taxonomy_related_b",
        "target_section_taxonomy_related",
        ["doc_type", "b_target_section"],
    )

