"""Уникальность fact_evidence по (fact_id, anchor_id, evidence_role).

Цель: предотвратить накопление дубликатов evidence при повторных прогонов ingest/recompute.
Добавляет уникальный индекс UNIQUE (fact_id, anchor_id, evidence_role) на fact_evidence.
Перед созданием индекса удаляет существующие дубликаты (если они есть).
"""

from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "0005_unique_fact_evidence"
down_revision = "0004_add_document_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Удаляем дубликаты (оставляем одну строку на ключ).
    # Используем ctid, чтобы детерминированно удалить "более ранние" записи.
    op.execute(
        """
        DELETE FROM fact_evidence a
        USING fact_evidence b
        WHERE a.fact_id = b.fact_id
          AND a.anchor_id = b.anchor_id
          AND a.evidence_role = b.evidence_role
          AND a.ctid < b.ctid;
        """
    )

    # 2) Добавляем уникальный индекс.
    op.create_index(
        "uq_fact_evidence_fact_anchor_role",
        "fact_evidence",
        ["fact_id", "anchor_id", "evidence_role"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_fact_evidence_fact_anchor_role", table_name="fact_evidence")


