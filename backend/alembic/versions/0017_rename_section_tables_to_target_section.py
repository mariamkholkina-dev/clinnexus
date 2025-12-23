"""Переименование таблиц section_* → target_section_*.

Переименовывает таблицы OUTPUT sections из section_* в target_section_*:
- section_contracts          → target_section_contracts
- section_maps               → target_section_maps
- section_taxonomy_nodes     → target_section_taxonomy_nodes
- section_taxonomy_aliases   → target_section_taxonomy_aliases
- section_taxonomy_related   → target_section_taxonomy_related
- generated_sections         → generated_target_sections

Без создания compatibility views. После миграции только новые имена таблиц существуют.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0017_rename_section_to_target"
down_revision = "0016_topic_indexes_constraints"
branch_labels = None
depends_on = None


def table_exists(conn, name: str) -> bool:
    """Проверяет существование таблицы в схеме public."""
    result = conn.execute(
        sa.text("SELECT to_regclass(:q)"), {"q": f"public.{name}"}
    ).scalar()
    return result is not None


def upgrade() -> None:
    """Переименовывает таблицы section_* в target_section_*."""
    conn = op.get_bind()
    
    # Порядок переименования: сначала листовые таблицы без зависимостей от других section_* таблиц
    # PostgreSQL автоматически обновит внешние ключи при переименовании
    
    # 1. section_taxonomy_aliases → target_section_taxonomy_aliases
    if table_exists(conn, "section_taxonomy_aliases") and not table_exists(conn, "target_section_taxonomy_aliases"):
        op.rename_table("section_taxonomy_aliases", "target_section_taxonomy_aliases", schema="public")
    
    # 2. section_taxonomy_related → target_section_taxonomy_related
    if table_exists(conn, "section_taxonomy_related") and not table_exists(conn, "target_section_taxonomy_related"):
        op.rename_table("section_taxonomy_related", "target_section_taxonomy_related", schema="public")
    
    # 3. section_taxonomy_nodes → target_section_taxonomy_nodes
    if table_exists(conn, "section_taxonomy_nodes") and not table_exists(conn, "target_section_taxonomy_nodes"):
        op.rename_table("section_taxonomy_nodes", "target_section_taxonomy_nodes", schema="public")
    
    # 4. section_maps → target_section_maps
    if table_exists(conn, "section_maps") and not table_exists(conn, "target_section_maps"):
        op.rename_table("section_maps", "target_section_maps", schema="public")
    
    # 5. generated_sections → generated_target_sections
    if table_exists(conn, "generated_sections") and not table_exists(conn, "generated_target_sections"):
        op.rename_table("generated_sections", "generated_target_sections", schema="public")
    
    # 6. section_contracts → target_section_contracts
    # (на неё ссылается generation_runs через FK, но PostgreSQL автоматически обновит FK)
    if table_exists(conn, "section_contracts") and not table_exists(conn, "target_section_contracts"):
        op.rename_table("section_contracts", "target_section_contracts", schema="public")


def downgrade() -> None:
    """Возвращает переименованные таблицы обратно к исходным именам."""
    conn = op.get_bind()
    
    # Обратный порядок переименования
    
    # 6. target_section_contracts → section_contracts
    if table_exists(conn, "target_section_contracts") and not table_exists(conn, "section_contracts"):
        op.rename_table("target_section_contracts", "section_contracts", schema="public")
    
    # 5. generated_target_sections → generated_sections
    if table_exists(conn, "generated_target_sections") and not table_exists(conn, "generated_sections"):
        op.rename_table("generated_target_sections", "generated_sections", schema="public")
    
    # 4. target_section_maps → section_maps
    if table_exists(conn, "target_section_maps") and not table_exists(conn, "section_maps"):
        op.rename_table("target_section_maps", "section_maps", schema="public")
    
    # 3. target_section_taxonomy_nodes → section_taxonomy_nodes
    if table_exists(conn, "target_section_taxonomy_nodes") and not table_exists(conn, "section_taxonomy_nodes"):
        op.rename_table("target_section_taxonomy_nodes", "section_taxonomy_nodes", schema="public")
    
    # 2. target_section_taxonomy_related → section_taxonomy_related
    if table_exists(conn, "target_section_taxonomy_related") and not table_exists(conn, "section_taxonomy_related"):
        op.rename_table("target_section_taxonomy_related", "section_taxonomy_related", schema="public")
    
    # 1. target_section_taxonomy_aliases → section_taxonomy_aliases
    if table_exists(conn, "target_section_taxonomy_aliases") and not table_exists(conn, "section_taxonomy_aliases"):
        op.rename_table("target_section_taxonomy_aliases", "section_taxonomy_aliases", schema="public")

