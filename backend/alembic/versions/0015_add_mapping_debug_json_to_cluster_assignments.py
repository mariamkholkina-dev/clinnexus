"""Добавление поля mapping_debug_json в cluster_assignments.

Добавляет JSONB поле для хранения debug-информации о маппинге кластера на топик.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0015_add_mapping_debug_json"
down_revision = "0014_topics_production"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем mapping_debug_json в cluster_assignments
    op.add_column(
        "cluster_assignments",
        sa.Column(
            "mapping_debug_json",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Удаляем mapping_debug_json из cluster_assignments
    op.drop_column("cluster_assignments", "mapping_debug_json")

