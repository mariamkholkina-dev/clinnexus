"""Сделать поля source_file_uri и source_sha256 nullable в document_versions.

Поля source_file_uri и source_sha256 должны быть nullable, так как версия документа
может быть создана до загрузки файла. Файл загружается через отдельный эндпоинт /upload.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0003_nullable_file_fields"
down_revision = "0002_enums_and_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Изменяем source_file_uri на nullable
    op.alter_column(
        "document_versions",
        "source_file_uri",
        existing_type=sa.Text(),
        nullable=True,
    )
    
    # Изменяем source_sha256 на nullable
    op.alter_column(
        "document_versions",
        "source_sha256",
        existing_type=sa.String(length=64),
        nullable=True,
    )


def downgrade() -> None:
    # Возвращаем source_file_uri обратно на NOT NULL
    # ВНИМАНИЕ: Это может не сработать, если есть записи с NULL значениями
    op.alter_column(
        "document_versions",
        "source_file_uri",
        existing_type=sa.Text(),
        nullable=False,
    )
    
    # Возвращаем source_sha256 обратно на NOT NULL
    # ВНИМАНИЕ: Это может не сработать, если есть записи с NULL значениями
    op.alter_column(
        "document_versions",
        "source_sha256",
        existing_type=sa.String(length=64),
        nullable=False,
    )

