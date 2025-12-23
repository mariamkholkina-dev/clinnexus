"""Добавление полей метаданных в таблицу facts.

Добавляет:
- confidence: float (0..1) - уверенность в извлеченном факте
- extractor_version: int - версия экстрактора, использованного для извлечения
- meta_json: jsonb - дополнительные метаданные (parsed units, ranges, alternatives, etc.)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_add_fact_metadata_fields"
down_revision = "0010_add_study_core_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем поля в таблицу facts
    op.add_column("facts", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("facts", sa.Column("extractor_version", sa.Integer(), nullable=True))
    op.add_column("facts", sa.Column("meta_json", postgresql.JSONB(), nullable=True))
    
    # Устанавливаем дефолтные значения для существующих записей
    op.execute("UPDATE facts SET confidence = 1.0 WHERE confidence IS NULL")
    op.execute("UPDATE facts SET extractor_version = 1 WHERE extractor_version IS NULL")
    
    # Создаем индекс для быстрого поиска по confidence
    op.create_index("ix_facts_confidence", "facts", ["confidence"])


def downgrade() -> None:
    op.drop_index("ix_facts_confidence", table_name="facts")
    op.drop_column("facts", "meta_json")
    op.drop_column("facts", "extractor_version")
    op.drop_column("facts", "confidence")

