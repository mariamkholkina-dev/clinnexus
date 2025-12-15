"""Нормализация ENUM типов и добавление pgvector для embeddings.

Шаг 2.п.2:
- Проверка/создание extension vector (идемпотентно)
- Конвертация chunks.embedding из ARRAY в vector(1536)
- Создание правильного pgvector индекса для embeddings
- Добавление дополнительных индексов для статусов/поиска
- Убедиться, что все enum колонки используют правильные типы
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0002_enums_and_vector"
down_revision = "0001_initial_prod_skeleton"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Включаем расширение pgvector (идемпотентно)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Проверяем и конвертируем chunks.embedding из ARRAY в vector(1536)
    # Получаем информацию о текущем типе колонки
    connection = op.get_bind()
    result = connection.execute(
        sa.text("""
            SELECT udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' 
            AND table_name = 'chunks' 
            AND column_name = 'embedding'
        """)
    ).fetchone()
    
    if result:
        udt_name = result[0]
        
        # Если это ARRAY (_float8 или другой массив), конвертируем в vector
        if udt_name in ('_float8', '_float4', '_numeric'):
            # Удаляем старый индекс, если он существует
            op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_vector")
            
            # Конвертируем ARRAY в vector(1536)
            # pgvector поддерживает прямое приведение массива к vector
            # Если прямое приведение не работает, используем через текст
            try:
                op.execute("""
                    ALTER TABLE chunks 
                    ALTER COLUMN embedding TYPE vector(1536) 
                    USING embedding::vector(1536)
                """)
            except Exception:
                # Если прямое приведение не работает, используем через текст
                op.execute("""
                    ALTER TABLE chunks 
                    ALTER COLUMN embedding TYPE vector(1536) 
                    USING embedding::text::vector(1536)
                """)
        # Если уже vector - ничего не делаем, просто убедимся что индекс правильный
        elif udt_name == 'vector':
            # Удаляем старый индекс, если он существует (может быть неправильного типа)
            op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_vector")
        else:
            # Неизвестный тип - пропускаем
            pass

    # 3. Создаём правильный pgvector индекс для chunks.embedding
    # Используем HNSW для лучшей производительности (если доступно в pgvector)
    # Если HNSW недоступно, используем ivfflat
    # Используем vector_cosine_ops для косинусного расстояния (лучше для embeddings)
    try:
        # Пробуем создать HNSW индекс (требует pgvector >= 0.5.0)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw 
            ON chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)
    except Exception:
        # Если HNSW недоступно, используем ivfflat (работает в более старых версиях)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ivfflat 
            ON chunks USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)

    # 4. Примечание: Все ENUM типы уже созданы в миграции 0001 как нативные PostgreSQL ENUM.
    # Колонки используют postgresql.ENUM с create_type=False, что означает, что типы уже существуют.
    # Дополнительная конвертация не требуется.
    
    # 5. Добавляем дополнительные индексы для статусов/поиска
    
    # Индекс для document_versions.ingestion_status (для фильтрации по статусу)
    op.create_index(
        "ix_document_versions_ingestion_status",
        "document_versions",
        ["ingestion_status"],
        if_not_exists=True,
    )
    
    # Индекс для facts.status (для фильтрации по статусу фактов)
    op.create_index(
        "ix_facts_status",
        "facts",
        ["status"],
        if_not_exists=True,
    )
    
    # Индекс для generation_runs.status (для фильтрации по статусу генерации)
    op.create_index(
        "ix_generation_runs_status",
        "generation_runs",
        ["status"],
        if_not_exists=True,
    )
    
    # Индекс для generated_sections.qc_status (для фильтрации по QC статусу)
    op.create_index(
        "ix_generated_sections_qc_status",
        "generated_sections",
        ["qc_status"],
        if_not_exists=True,
    )
    
    # Индекс для conflicts.status (для фильтрации по статусу)
    op.create_index(
        "ix_conflicts_status",
        "conflicts",
        ["status"],
        if_not_exists=True,
    )
    
    # Индекс для conflicts.severity (для фильтрации по серьезности)
    op.create_index(
        "ix_conflicts_severity",
        "conflicts",
        ["severity"],
        if_not_exists=True,
    )
    
    # Индекс для tasks.status (для фильтрации по статусу задач)
    op.create_index(
        "ix_tasks_status",
        "tasks",
        ["status"],
        if_not_exists=True,
    )
    
    # Индекс для tasks.type (для фильтрации по типу задач)
    op.create_index(
        "ix_tasks_type",
        "tasks",
        ["type"],
        if_not_exists=True,
    )
    
    # Индекс для section_maps.status (для фильтрации по статусу маппинга)
    op.create_index(
        "ix_section_maps_status",
        "section_maps",
        ["status"],
        if_not_exists=True,
    )
    
    # Индекс для chunks по doc_version_id и section_path (для поиска по секциям)
    # Этот индекс уже может существовать, но добавим для уверенности
    op.create_index(
        "ix_chunks_doc_version_section_path",
        "chunks",
        ["doc_version_id", "section_path"],
        if_not_exists=True,
    )


def downgrade() -> None:
    # Удаляем дополнительные индексы
    op.drop_index("ix_chunks_doc_version_section_path", table_name="chunks", if_exists=True)
    op.drop_index("ix_section_maps_status", table_name="section_maps", if_exists=True)
    op.drop_index("ix_tasks_type", table_name="tasks", if_exists=True)
    op.drop_index("ix_tasks_status", table_name="tasks", if_exists=True)
    op.drop_index("ix_conflicts_severity", table_name="conflicts", if_exists=True)
    op.drop_index("ix_conflicts_status", table_name="conflicts", if_exists=True)
    op.drop_index("ix_generated_sections_qc_status", table_name="generated_sections", if_exists=True)
    op.drop_index("ix_generation_runs_status", table_name="generation_runs", if_exists=True)
    op.drop_index("ix_facts_status", table_name="facts", if_exists=True)
    op.drop_index("ix_document_versions_ingestion_status", table_name="document_versions", if_exists=True)
    
    # Удаляем pgvector индексы
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_ivfflat")
    
    # Конвертируем chunks.embedding обратно в ARRAY
    # Проверяем текущий тип
    connection = op.get_bind()
    result = connection.execute(
        sa.text("""
            SELECT udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = 'chunks' 
            AND column_name = 'embedding'
        """)
    ).fetchone()
    
    if result:
        udt_name = result[0]
        if udt_name == 'vector':
            # Конвертируем vector обратно в ARRAY
            # Восстанавливаем старый индекс для ARRAY (если нужен)
            op.execute("""
                ALTER TABLE chunks 
                ALTER COLUMN embedding TYPE double precision[] 
                USING embedding::text::double precision[]
            """)
    
    # Расширение vector не удаляем, так как оно может использоваться в других схемах

