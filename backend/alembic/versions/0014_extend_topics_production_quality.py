"""Расширение поддержки topics для production-качества.

Добавляет:
- В topics: topic_profile_json (JSONB), is_active (BOOLEAN), topic_embedding (VECTOR(1536) nullable)
- Таблицу heading_clusters для хранения кластеров заголовков
- Таблицу topic_mapping_runs для отслеживания запусков маппинга топиков
- Индексы для оптимизации запросов
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0014_topics_production"
down_revision = "0013_add_ingestion_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Расширение таблицы topics
    # ========================================================================
    
    # Добавляем topic_profile_json
    op.add_column(
        "topics",
        sa.Column(
            "topic_profile_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    
    # Добавляем is_active
    op.add_column(
        "topics",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    
    # Убеждаемся, что расширение vector доступно
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    
    # Добавляем topic_embedding как vector(1536) напрямую через SQL
    op.execute("ALTER TABLE topics ADD COLUMN topic_embedding vector(1536) NULL")
    
    # Создаём GIN индекс на topic_profile_json
    op.create_index(
        "ix_topics_topic_profile_json",
        "topics",
        ["topic_profile_json"],
        postgresql_using="gin",
    )
    
    # Индекс на is_active для фильтрации активных топиков
    op.create_index(
        "ix_topics_is_active",
        "topics",
        ["is_active"],
    )
    
    # ========================================================================
    # 2. Таблица heading_clusters
    # ========================================================================
    op.create_table(
        "heading_clusters",
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
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column(
            "language",
            postgresql.ENUM(name="document_language", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "top_titles_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "examples_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "stats_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # cluster_embedding будет добавлен отдельно через SQL
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Добавляем cluster_embedding как vector(1536) напрямую через SQL
    # (vector нельзя создать через sa.Column в Alembic, нужно через execute)
    op.execute("ALTER TABLE heading_clusters ADD COLUMN cluster_embedding vector(1536) NULL")
    
    # Уникальный индекс для (doc_version_id, cluster_id, language)
    op.create_unique_constraint(
        "uq_heading_clusters_doc_version_cluster_language",
        "heading_clusters",
        ["doc_version_id", "cluster_id", "language"],
    )
    
    # Индексы для быстрого поиска
    op.create_index(
        "ix_heading_clusters_doc_version_id",
        "heading_clusters",
        ["doc_version_id"],
    )
    op.create_index(
        "ix_heading_clusters_cluster_id",
        "heading_clusters",
        ["cluster_id"],
    )
    op.create_index(
        "ix_heading_clusters_doc_version_cluster",
        "heading_clusters",
        ["doc_version_id", "cluster_id"],
    )
    
    # ========================================================================
    # 3. Таблица topic_mapping_runs (опционально)
    # ========================================================================
    op.create_table(
        "topic_mapping_runs",
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
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column(
            "params_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    
    # Индекс на doc_version_id для быстрого поиска запусков по документу
    op.create_index(
        "ix_topic_mapping_runs_doc_version_id",
        "topic_mapping_runs",
        ["doc_version_id"],
    )
    
    # Индекс на created_at для сортировки
    op.create_index(
        "ix_topic_mapping_runs_created_at",
        "topic_mapping_runs",
        ["created_at"],
    )


def downgrade() -> None:
    # Удаляем таблицы и индексы в обратном порядке
    
    # 3. Удаляем topic_mapping_runs
    op.drop_index("ix_topic_mapping_runs_created_at", table_name="topic_mapping_runs")
    op.drop_index("ix_topic_mapping_runs_doc_version_id", table_name="topic_mapping_runs")
    op.drop_table("topic_mapping_runs")
    
    # 2. Удаляем heading_clusters
    op.drop_index("ix_heading_clusters_doc_version_cluster", table_name="heading_clusters")
    op.drop_index("ix_heading_clusters_cluster_id", table_name="heading_clusters")
    op.drop_index("ix_heading_clusters_doc_version_id", table_name="heading_clusters")
    op.drop_constraint(
        "uq_heading_clusters_doc_version_cluster_language",
        "heading_clusters",
        type_="unique",
    )
    op.drop_table("heading_clusters")
    
    # 1. Удаляем поля из topics
    op.drop_index("ix_topics_is_active", table_name="topics")
    op.drop_index("ix_topics_topic_profile_json", table_name="topics")
    op.drop_column("topics", "topic_embedding")
    op.drop_column("topics", "is_active")
    op.drop_column("topics", "topic_profile_json")

