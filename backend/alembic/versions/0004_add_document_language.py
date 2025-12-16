"""Добавить document_language enum и поле в document_versions.

Добавляет:
- enum document_language с значениями ('ru','en','mixed','unknown')
- поле document_language в таблицу document_versions (NOT NULL, default 'unknown')
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0004_add_document_language"
down_revision = "0003_nullable_file_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Создаём enum document_language (идемпотентно)
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_language') THEN
                CREATE TYPE document_language AS ENUM ('ru','en','mixed','unknown');
            END IF;
        END $$;
        """
    )

    # 2. Добавляем поле document_language в document_versions
    op.add_column(
        "document_versions",
        sa.Column(
            "document_language",
            postgresql.ENUM(name="document_language", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
    )


def downgrade() -> None:
    # Удаляем поле document_language
    op.drop_column("document_versions", "document_language")
    
    # Удаляем enum document_language
    # ВНИМАНИЕ: Это не сработает, если enum используется в других таблицах
    op.execute("DROP TYPE IF EXISTS document_language")

