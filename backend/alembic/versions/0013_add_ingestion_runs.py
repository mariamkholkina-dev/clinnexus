"""Добавление таблицы ingestion_runs для отслеживания запусков ингестии.

Создаёт:
- Таблицу ingestion_runs с метриками и качеством
- Добавляет last_ingestion_run_id в document_versions
- Индексы для быстрого поиска
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013_add_ingestion_runs"
down_revision = "0012_source_zone_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Создаём таблицу ingestion_runs
    op.create_table(
        "ingestion_runs",
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
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            # CHECK constraint будет добавлен ниже
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "duration_ms",
            sa.Integer,
            nullable=True,
        ),
        sa.Column(
            "pipeline_version",
            sa.Text,
            nullable=False,
        ),
        sa.Column(
            "pipeline_config_hash",
            sa.Text,
            nullable=False,
        ),
        sa.Column(
            "summary_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "quality_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "warnings_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "errors_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    
    # 2. Добавляем CHECK constraint для status
    op.execute(
        "ALTER TABLE ingestion_runs ADD CONSTRAINT chk_ingestion_runs_status "
        "CHECK (status IN ('ok', 'failed', 'partial'))"
    )
    
    # 3. Создаём индексы
    op.create_index(
        "idx_ingestion_runs_doc_version_id",
        "ingestion_runs",
        ["doc_version_id"],
    )
    op.create_index(
        "idx_ingestion_runs_started_at",
        "ingestion_runs",
        ["started_at"],
    )
    
    # 4. Добавляем last_ingestion_run_id в document_versions
    op.add_column(
        "document_versions",
        sa.Column(
            "last_ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Удаляем last_ingestion_run_id из document_versions
    op.drop_column("document_versions", "last_ingestion_run_id")
    
    # Удаляем индексы
    op.drop_index("idx_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_index("idx_ingestion_runs_doc_version_id", table_name="ingestion_runs")
    
    # Удаляем CHECK constraint
    op.execute("ALTER TABLE ingestion_runs DROP CONSTRAINT chk_ingestion_runs_status")
    
    # Удаляем таблицу
    op.drop_table("ingestion_runs")

