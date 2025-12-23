"""Добавление ENUM source_zone и стандартизация 12 основных секций.

Создаёт:
- ENUM source_zone с 12 каноническими ключами + unknown
- Обновляет anchors.source_zone и chunks.source_zone на ENUM
- Добавляет индексы для быстрого поиска по source_zone
- Обновляет существующие значения на канонические
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012_source_zone_enum"
down_revision = "0011_add_fact_metadata_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Создаём ENUM source_zone с 12 каноническими ключами + unknown
    source_zone_enum = postgresql.ENUM(
        "overview",
        "design",
        "ip",
        "statistics",
        "safety",
        "endpoints",
        "population",
        "procedures",
        "data_management",
        "ethics",
        "admin",
        "appendix",
        "unknown",
        name="source_zone",
        create_type=True,
    )
    source_zone_enum.create(op.get_bind(), checkfirst=True)
    
    # 2. Обновляем существующие значения на канонические (маппинг старых значений)
    # Маппинг старых зон на новые канонические
    mapping_updates = [
        ("randomization", "design"),
        ("adverse_events", "safety"),
        ("serious_adverse_events", "safety"),
        ("statistical_methods", "statistics"),
        ("eligibility", "population"),
        ("ip_handling", "ip"),
        ("study_design", "design"),
        ("objectives", "overview"),
        ("study_population", "population"),
    ]
    
    # Обновляем anchors
    for old_value, new_value in mapping_updates:
        op.execute(
            sa.text("UPDATE anchors SET source_zone = :new_value WHERE source_zone = :old_value").bindparams(
                old_value=old_value, new_value=new_value
            )
        )
    
    # Обновляем chunks
    for old_value, new_value in mapping_updates:
        op.execute(
            sa.text("UPDATE chunks SET source_zone = :new_value WHERE source_zone = :old_value").bindparams(
                old_value=old_value, new_value=new_value
            )
        )
    
    # Устанавливаем "unknown" для всех NULL или нераспознанных значений
    op.execute("UPDATE anchors SET source_zone = 'unknown' WHERE source_zone IS NULL OR source_zone NOT IN ('overview', 'design', 'ip', 'statistics', 'safety', 'endpoints', 'population', 'procedures', 'data_management', 'ethics', 'admin', 'appendix', 'unknown')")
    op.execute("UPDATE chunks SET source_zone = 'unknown' WHERE source_zone IS NULL OR source_zone NOT IN ('overview', 'design', 'ip', 'statistics', 'safety', 'endpoints', 'population', 'procedures', 'data_management', 'ethics', 'admin', 'appendix', 'unknown')")
    
    # 3. Удаляем значения по умолчанию перед изменением типа
    op.execute("ALTER TABLE anchors ALTER COLUMN source_zone DROP DEFAULT")
    op.execute("ALTER TABLE chunks ALTER COLUMN source_zone DROP DEFAULT")
    
    # 4. Изменяем тип поля source_zone в anchors с TEXT на ENUM
    op.execute("ALTER TABLE anchors ALTER COLUMN source_zone TYPE source_zone USING source_zone::source_zone")
    
    # 5. Изменяем тип поля source_zone в chunks с TEXT на ENUM
    op.execute("ALTER TABLE chunks ALTER COLUMN source_zone TYPE source_zone USING source_zone::source_zone")
    
    # 6. Устанавливаем значение по умолчанию для ENUM типа
    op.execute("ALTER TABLE anchors ALTER COLUMN source_zone SET DEFAULT 'unknown'::source_zone")
    op.execute("ALTER TABLE chunks ALTER COLUMN source_zone SET DEFAULT 'unknown'::source_zone")
    
    # 7. Добавляем индексы для быстрого поиска по source_zone
    # Индексы уже могут существовать из предыдущей миграции 0007, поэтому проверяем перед созданием
    conn = op.get_bind()
    
    # Проверяем и создаём индекс для anchors, если его ещё нет
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_anchors_doc_version_source_zone'"
        )
    ).fetchone()
    if not result:
        op.create_index(
            "ix_anchors_doc_version_source_zone",
            "anchors",
            ["doc_version_id", "source_zone"],
        )
    
    # Проверяем и создаём индекс для chunks, если его ещё нет
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_chunks_doc_version_source_zone'"
        )
    ).fetchone()
    if not result:
        op.create_index(
            "ix_chunks_doc_version_source_zone",
            "chunks",
            ["doc_version_id", "source_zone"],
        )


def downgrade() -> None:
    # Удаляем индексы
    op.drop_index("ix_chunks_doc_version_source_zone", table_name="chunks")
    op.drop_index("ix_anchors_doc_version_source_zone", table_name="anchors")
    
    # Удаляем значения по умолчанию перед изменением типа
    op.execute("ALTER TABLE chunks ALTER COLUMN source_zone DROP DEFAULT")
    op.execute("ALTER TABLE anchors ALTER COLUMN source_zone DROP DEFAULT")
    
    # Возвращаем тип обратно в TEXT
    op.execute("ALTER TABLE chunks ALTER COLUMN source_zone TYPE text USING source_zone::text")
    op.execute("ALTER TABLE anchors ALTER COLUMN source_zone TYPE text USING source_zone::text")
    
    # Восстанавливаем значение по умолчанию для TEXT типа
    op.execute("ALTER TABLE chunks ALTER COLUMN source_zone SET DEFAULT 'unknown'")
    op.execute("ALTER TABLE anchors ALTER COLUMN source_zone SET DEFAULT 'unknown'")
    
    # Удаляем ENUM
    op.execute("DROP TYPE IF EXISTS source_zone")

