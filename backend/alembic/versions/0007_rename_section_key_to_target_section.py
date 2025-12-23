"""Переименование section_key → target_section и добавление новых полей.

Переименовывает:
- section_contracts.section_key → target_section
- section_maps.section_key → target_section
- generation_runs.section_key → target_section
- impact_items.affected_section_key → affected_target_section
- section_taxonomy_nodes.section_key → target_section
- section_taxonomy_nodes.parent_section_key → parent_target_section
- section_taxonomy_related.a_section_key → a_target_section
- section_taxonomy_related.b_section_key → b_target_section

Добавляет:
- view_key (text NULL) в section_contracts и generation_runs
- source_zone (text NOT NULL DEFAULT 'unknown') в anchors и chunks
- language (document_language NOT NULL DEFAULT 'unknown') в anchors и chunks

Индексы:
- anchors (doc_version_id, source_zone)
- chunks (doc_version_id, source_zone)
- anchors (doc_version_id, language) (если необходимо)
- chunks (doc_version_id, language) (если необходимо)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006_add_section_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Переименование section_key → target_section
    # ========================================================================
    
    # section_contracts
    op.alter_column("section_contracts", "section_key", new_column_name="target_section")
    
    # Обновляем уникальный индекс для section_contracts
    op.drop_constraint(
        "uq_section_contracts_ws_doc_type_key_version",
        "section_contracts",
        type_="unique"
    )
    op.create_unique_constraint(
        "uq_section_contracts_ws_doc_type_key_version",
        "section_contracts",
        ["workspace_id", "doc_type", "target_section", "version"],
    )
    
    # section_maps
    op.alter_column("section_maps", "section_key", new_column_name="target_section")
    
    # Обновляем уникальный индекс для section_maps
    op.drop_constraint(
        "uq_section_maps_doc_version_section_key",
        "section_maps",
        type_="unique"
    )
    op.create_unique_constraint(
        "uq_section_maps_doc_version_section_key",
        "section_maps",
        ["doc_version_id", "target_section"],
    )
    
    # generation_runs
    op.alter_column("generation_runs", "section_key", new_column_name="target_section")
    
    # impact_items
    op.alter_column("impact_items", "affected_section_key", new_column_name="affected_target_section")
    
    # section_taxonomy_nodes
    op.alter_column("section_taxonomy_nodes", "section_key", new_column_name="target_section")
    op.alter_column("section_taxonomy_nodes", "parent_section_key", new_column_name="parent_target_section")
    
    # Обновляем уникальный индекс для section_taxonomy_nodes
    # В миграции 0006 это был индекс, а не constraint
    op.drop_index(
        "uq_section_taxonomy_nodes_doc_type_section_key",
        table_name="section_taxonomy_nodes"
    )
    op.create_index(
        "uq_section_taxonomy_nodes_doc_type_section_key",
        "section_taxonomy_nodes",
        ["doc_type", "target_section"],
        unique=True,
    )
    
    # Обновляем индекс для parent
    op.drop_index("ix_section_taxonomy_nodes_parent", table_name="section_taxonomy_nodes")
    op.create_index(
        "ix_section_taxonomy_nodes_parent",
        "section_taxonomy_nodes",
        ["doc_type", "parent_target_section"],
    )
    
    # section_taxonomy_related
    op.alter_column("section_taxonomy_related", "a_section_key", new_column_name="a_target_section")
    op.alter_column("section_taxonomy_related", "b_section_key", new_column_name="b_target_section")
    
    # Обновляем уникальный индекс для section_taxonomy_related
    # В миграции 0006 это был индекс, а не constraint
    op.drop_index(
        "uq_section_taxonomy_related_doc_type_ab",
        table_name="section_taxonomy_related"
    )
    op.create_index(
        "uq_section_taxonomy_related_doc_type_ab",
        "section_taxonomy_related",
        ["doc_type", "a_target_section", "b_target_section"],
        unique=True,
    )
    
    # Обновляем индексы для section_taxonomy_related
    op.drop_index("ix_section_taxonomy_related_a", table_name="section_taxonomy_related")
    op.drop_index("ix_section_taxonomy_related_b", table_name="section_taxonomy_related")
    op.create_index(
        "ix_section_taxonomy_related_a",
        "section_taxonomy_related",
        ["doc_type", "a_target_section"],
    )
    op.create_index(
        "ix_section_taxonomy_related_b",
        "section_taxonomy_related",
        ["doc_type", "b_target_section"],
    )
    
    # ========================================================================
    # 2. Добавление view_key в section_contracts и generation_runs
    # ========================================================================
    
    op.add_column(
        "section_contracts",
        sa.Column("view_key", sa.Text(), nullable=True),
    )
    
    op.add_column(
        "generation_runs",
        sa.Column("view_key", sa.Text(), nullable=True),
    )
    
    # ========================================================================
    # 3. Добавление source_zone в anchors и chunks
    # ========================================================================
    
    op.add_column(
        "anchors",
        sa.Column("source_zone", sa.Text(), nullable=False, server_default="unknown"),
    )
    
    op.add_column(
        "chunks",
        sa.Column("source_zone", sa.Text(), nullable=False, server_default="unknown"),
    )
    
    # ========================================================================
    # 4. Добавление language в anchors и chunks
    # ========================================================================
    
    op.add_column(
        "anchors",
        sa.Column(
            "language",
            postgresql.ENUM(name="document_language", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
    )
    
    op.add_column(
        "chunks",
        sa.Column(
            "language",
            postgresql.ENUM(name="document_language", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
    )
    
    # ========================================================================
    # 5. Добавление индексов
    # ========================================================================
    
    # Индексы для source_zone
    op.create_index(
        "ix_anchors_doc_version_source_zone",
        "anchors",
        ["doc_version_id", "source_zone"],
    )
    
    op.create_index(
        "ix_chunks_doc_version_source_zone",
        "chunks",
        ["doc_version_id", "source_zone"],
    )
    
    # Индексы для language (если необходимо)
    op.create_index(
        "ix_anchors_doc_version_language",
        "anchors",
        ["doc_version_id", "language"],
    )
    
    op.create_index(
        "ix_chunks_doc_version_language",
        "chunks",
        ["doc_version_id", "language"],
    )


def downgrade() -> None:
    # Удаляем индексы
    op.drop_index("ix_chunks_doc_version_language", table_name="chunks")
    op.drop_index("ix_anchors_doc_version_language", table_name="anchors")
    op.drop_index("ix_chunks_doc_version_source_zone", table_name="chunks")
    op.drop_index("ix_anchors_doc_version_source_zone", table_name="anchors")
    
    # Удаляем language из anchors и chunks
    op.drop_column("chunks", "language")
    op.drop_column("anchors", "language")
    
    # Удаляем source_zone из anchors и chunks
    op.drop_column("chunks", "source_zone")
    op.drop_column("anchors", "source_zone")
    
    # Удаляем view_key из generation_runs и section_contracts
    op.drop_column("generation_runs", "view_key")
    op.drop_column("section_contracts", "view_key")
    
    # Восстанавливаем индексы section_taxonomy_related
    op.drop_index("ix_section_taxonomy_related_b", table_name="section_taxonomy_related")
    op.drop_index("ix_section_taxonomy_related_a", table_name="section_taxonomy_related")
    op.drop_index(
        "uq_section_taxonomy_related_doc_type_ab",
        table_name="section_taxonomy_related"
    )
    op.create_index(
        "uq_section_taxonomy_related_doc_type_ab",
        "section_taxonomy_related",
        ["doc_type", "a_section_key", "b_section_key"],
        unique=True,
    )
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
    
    # Восстанавливаем индексы section_taxonomy_nodes
    op.drop_index("ix_section_taxonomy_nodes_parent", table_name="section_taxonomy_nodes")
    op.drop_index(
        "uq_section_taxonomy_nodes_doc_type_section_key",
        table_name="section_taxonomy_nodes"
    )
    op.create_index(
        "uq_section_taxonomy_nodes_doc_type_section_key",
        "section_taxonomy_nodes",
        ["doc_type", "section_key"],
        unique=True,
    )
    op.create_index(
        "ix_section_taxonomy_nodes_parent",
        "section_taxonomy_nodes",
        ["doc_type", "parent_section_key"],
    )
    
    # Переименовываем обратно
    op.alter_column("section_taxonomy_related", "b_target_section", new_column_name="b_section_key")
    op.alter_column("section_taxonomy_related", "a_target_section", new_column_name="a_section_key")
    op.alter_column("section_taxonomy_nodes", "parent_target_section", new_column_name="parent_section_key")
    op.alter_column("section_taxonomy_nodes", "target_section", new_column_name="section_key")
    op.alter_column("impact_items", "affected_target_section", new_column_name="affected_section_key")
    op.alter_column("generation_runs", "target_section", new_column_name="section_key")
    
    # Восстанавливаем индексы section_maps
    op.drop_constraint(
        "uq_section_maps_doc_version_section_key",
        "section_maps",
        type_="unique"
    )
    op.create_unique_constraint(
        "uq_section_maps_doc_version_section_key",
        "section_maps",
        ["doc_version_id", "section_key"],
    )
    op.alter_column("section_maps", "target_section", new_column_name="section_key")
    
    # Восстанавливаем индексы section_contracts
    op.drop_constraint(
        "uq_section_contracts_ws_doc_type_key_version",
        "section_contracts",
        type_="unique"
    )
    op.create_unique_constraint(
        "uq_section_contracts_ws_doc_type_key_version",
        "section_contracts",
        ["workspace_id", "doc_type", "section_key", "version"],
    )
    op.alter_column("section_contracts", "target_section", new_column_name="section_key")

