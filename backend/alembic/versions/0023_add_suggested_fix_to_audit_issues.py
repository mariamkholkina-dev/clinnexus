"""Добавление поля suggested_fix в таблицу audit_issues.

Добавляет опциональное поле suggested_fix для хранения предлагаемых исправлений
к аудиторским находкам.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0023_add_suggested_fix_to_audit_issues"
down_revision = "0022_usr_4_1_enums_and_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем поле suggested_fix в таблицу audit_issues
    op.add_column("audit_issues", sa.Column("suggested_fix", sa.Text(), nullable=True))


def downgrade() -> None:
    # Удаляем поле suggested_fix
    op.drop_column("audit_issues", "suggested_fix")

