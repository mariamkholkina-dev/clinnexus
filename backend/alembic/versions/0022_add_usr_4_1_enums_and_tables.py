"""Обновление схемы данных для соответствия USR 4.1.

Добавляет:
- ENUM типы: fact_scope, audit_severity, audit_category, audit_status
- Поля в таблицу facts: scope, type_category
- Таблицу audit_issues для хранения аудиторских находок
- Таблицу terminology_dictionaries для словарей терминологии
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0022_usr_4_1_enums_and_tables"
down_revision = "0021_heading_block_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================================================
    # 1. Создание новых ENUM типов
    # ========================================================================
    
    # Создаём ENUM fact_scope
    fact_scope_enum = postgresql.ENUM(
        "global",
        "arm",
        "group",
        "visit",
        name="fact_scope",
        create_type=True,
    )
    fact_scope_enum.create(op.get_bind(), checkfirst=True)
    
    # Создаём ENUM audit_severity
    audit_severity_enum = postgresql.ENUM(
        "critical",
        "major",
        "minor",
        name="audit_severity",
        create_type=True,
    )
    audit_severity_enum.create(op.get_bind(), checkfirst=True)
    
    # Создаём ENUM audit_category
    audit_category_enum = postgresql.ENUM(
        "consistency",
        "grammar",
        "logic",
        "terminology",
        "compliance",
        name="audit_category",
        create_type=True,
    )
    audit_category_enum.create(op.get_bind(), checkfirst=True)
    
    # Создаём ENUM audit_status
    audit_status_enum = postgresql.ENUM(
        "open",
        "suppressed",
        "resolved",
        name="audit_status",
        create_type=True,
    )
    audit_status_enum.create(op.get_bind(), checkfirst=True)
    
    # ========================================================================
    # 2. Обновление таблицы facts: добавление полей scope и type_category
    # ========================================================================
    
    # Добавляем поле scope (с дефолтным значением 'global')
    op.add_column(
        "facts",
        sa.Column(
            "scope",
            postgresql.ENUM(
                "global",
                "arm",
                "group",
                "visit",
                name="fact_scope",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'global'::fact_scope"),
        ),
    )
    
    # Добавляем поле type_category
    op.add_column(
        "facts",
        sa.Column("type_category", sa.String(128), nullable=True),
    )
    
    # Создаём индекс для быстрого поиска по type_category
    op.create_index(
        "ix_facts_type_category",
        "facts",
        ["type_category"],
    )
    
    # Создаём индекс для быстрого поиска по scope
    op.create_index(
        "ix_facts_scope",
        "facts",
        ["scope"],
    )
    
    # ========================================================================
    # 3. Создание таблицы audit_issues
    # ========================================================================
    op.create_table(
        "audit_issues",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM(
                "critical",
                "major",
                "minor",
                name="audit_severity",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "category",
            postgresql.ENUM(
                "consistency",
                "grammar",
                "logic",
                "terminology",
                "compliance",
                name="audit_category",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location_anchors", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "open",
                "suppressed",
                "resolved",
                name="audit_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("suppression_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            onupdate=sa.func.now(),
        ),
    )
    
    # Индексы для таблицы audit_issues
    op.create_index(
        "ix_audit_issues_study_id",
        "audit_issues",
        ["study_id"],
    )
    op.create_index(
        "ix_audit_issues_doc_version_id",
        "audit_issues",
        ["doc_version_id"],
    )
    op.create_index(
        "ix_audit_issues_severity",
        "audit_issues",
        ["severity"],
    )
    op.create_index(
        "ix_audit_issues_category",
        "audit_issues",
        ["category"],
    )
    op.create_index(
        "ix_audit_issues_status",
        "audit_issues",
        ["status"],
    )
    
    # Составной индекс для фильтрации по study_id и status
    op.create_index(
        "ix_audit_issues_study_status",
        "audit_issues",
        ["study_id", "status"],
    )
    
    # ========================================================================
    # 4. Создание таблицы terminology_dictionaries
    # ========================================================================
    op.create_table(
        "terminology_dictionaries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("term_category", sa.String(128), nullable=False),
        sa.Column("preferred_term", sa.Text(), nullable=False),
        sa.Column("variations", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            onupdate=sa.func.now(),
        ),
    )
    
    # Индексы для таблицы terminology_dictionaries
    op.create_index(
        "ix_terminology_dictionaries_study_id",
        "terminology_dictionaries",
        ["study_id"],
    )
    op.create_index(
        "ix_terminology_dictionaries_term_category",
        "terminology_dictionaries",
        ["term_category"],
    )
    
    # Составной индекс для уникальности preferred_term в рамках study_id и term_category
    op.create_unique_constraint(
        "uq_terminology_study_category_term",
        "terminology_dictionaries",
        ["study_id", "term_category", "preferred_term"],
    )


def downgrade() -> None:
    # Удаляем таблицы
    op.drop_table("terminology_dictionaries")
    op.drop_table("audit_issues")
    
    # Удаляем индексы и поля из facts
    op.drop_index("ix_facts_scope", table_name="facts")
    op.drop_index("ix_facts_type_category", table_name="facts")
    op.drop_column("facts", "type_category")
    op.drop_column("facts", "scope")
    
    # Удаляем ENUM типы
    op.execute("DROP TYPE IF EXISTS audit_status")
    op.execute("DROP TYPE IF EXISTS audit_category")
    op.execute("DROP TYPE IF EXISTS audit_severity")
    op.execute("DROP TYPE IF EXISTS fact_scope")

