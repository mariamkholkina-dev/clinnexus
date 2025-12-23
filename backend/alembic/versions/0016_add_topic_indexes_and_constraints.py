"""Добавление недостающих индексов и ограничений для topics и cluster_assignments.

Добавляет:
- Составной индекс на (workspace_id, is_active) в topics
- Индекс на doc_version_id в cluster_assignments
- Check constraint для mapped_by в cluster_assignments (auto/assist/manual/seed/import)
- Обновление topic_mapping_runs с pipeline_version и pipeline_config_hash
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0016_topic_indexes_constraints"
down_revision = "0015_add_mapping_debug_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Составной индекс на (workspace_id, is_active) в topics
    # ========================================================================
    op.create_index(
        "ix_topics_workspace_is_active",
        "topics",
        ["workspace_id", "is_active"],
    )
    
    # ========================================================================
    # 2. Индекс на doc_version_id в cluster_assignments
    # ========================================================================
    op.create_index(
        "ix_cluster_assignments_doc_version_id",
        "cluster_assignments",
        ["doc_version_id"],
    )
    
    # ========================================================================
    # 3. Check constraint для mapped_by в cluster_assignments
    # ========================================================================
    op.create_check_constraint(
        "ck_cluster_assignments_mapped_by",
        "cluster_assignments",
        sa.text("mapped_by IN ('auto', 'assist', 'manual', 'seed', 'import')"),
    )
    
    # ========================================================================
    # 4. Обновление topic_mapping_runs: добавление pipeline_version и pipeline_config_hash
    # ========================================================================
    # Добавляем поля как nullable сначала для существующих записей
    op.add_column(
        "topic_mapping_runs",
        sa.Column("pipeline_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "topic_mapping_runs",
        sa.Column("pipeline_config_hash", sa.Text(), nullable=True),
    )
    
    # Устанавливаем значения по умолчанию для существующих записей (если есть)
    op.execute(
        "UPDATE topic_mapping_runs SET pipeline_version = '' WHERE pipeline_version IS NULL"
    )
    op.execute(
        "UPDATE topic_mapping_runs SET pipeline_config_hash = '' WHERE pipeline_config_hash IS NULL"
    )
    
    # Теперь делаем поля NOT NULL согласно требованиям
    op.alter_column("topic_mapping_runs", "pipeline_version", nullable=False)
    op.alter_column("topic_mapping_runs", "pipeline_config_hash", nullable=False)


def downgrade() -> None:
    # Удаляем в обратном порядке
    
    # 4. Удаляем поля из topic_mapping_runs
    op.drop_column("topic_mapping_runs", "pipeline_config_hash")
    op.drop_column("topic_mapping_runs", "pipeline_version")
    
    # 3. Удаляем check constraint
    op.drop_constraint(
        "ck_cluster_assignments_mapped_by",
        "cluster_assignments",
        type_="check",
    )
    
    # 2. Удаляем индекс на doc_version_id
    op.drop_index("ix_cluster_assignments_doc_version_id", table_name="cluster_assignments")
    
    # 1. Удаляем составной индекс
    op.drop_index("ix_topics_workspace_is_active", table_name="topics")

