"""Тест миграции 0007: переименование section_key → target_section.

Проверяет:
1. Переименование полей section_key → target_section
2. Добавление view_key в section_contracts и generation_runs
3. Добавление source_zone и language в anchors и chunks
4. Создание индексов
5. Обратную совместимость через property
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.enums import (
    DocumentLanguage,
    DocumentType,
    GenerationStatus,
    IngestionStatus,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.db.models.anchors import Anchor, Chunk
from app.db.models.change import ImpactItem
from app.db.models.generation import GenerationRun
from app.db.models.sections import TargetSectionContract, TargetSectionMap


@pytest.fixture
def alembic_cfg(db_engine: AsyncEngine) -> Config:
    """Создает конфигурацию Alembic для тестов."""
    from app.core.config import settings
    
    cfg = Config()
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", settings.sync_database_url)
    return cfg


async def test_migration_0007_section_key_rename(
    db: AsyncSession, alembic_cfg: Config, db_engine: AsyncEngine
):
    """Тест миграции: переименование section_key → target_section и добавление новых полей."""
    
    # 1. Откатываемся до версии до миграции 0007 (если миграция уже применена)
    # Примечание: в тестовой БД миграции могут быть не применены, поэтому проверяем текущую версию
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    
    async with db_engine.begin() as conn:
        def run_downgrade(sync_conn):
            # Создаем синхронный контекст для миграций
            context = MigrationContext.configure(sync_conn)
            current_rev = context.get_current_revision()
            if current_rev:
                script = ScriptDirectory.from_config(alembic_cfg)
                # Откатываемся до 0006, если текущая версия >= 0007
                if current_rev == "0007_rename_section_key_to_target_section":
                    command.downgrade(alembic_cfg, "0006_add_section_taxonomy")
        
        await conn.run_sync(run_downgrade)
    
    # 2. Создаем тестовые данные со старыми именами полей (section_key)
    workspace_id = uuid.uuid4()
    study_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc_version_id = uuid.uuid4()
    template_id = uuid.uuid4()
    
    # Создаем workspace, study, document через SQL (так как модели могут быть обновлены)
    async with db.begin():
        # Создаем workspace
        await db.execute(
            text("""
                INSERT INTO workspaces (id, name, created_at)
                VALUES (:id, 'test_workspace', NOW())
            """),
            {"id": workspace_id}
        )
        
        # Создаем study
        await db.execute(
            text("""
                INSERT INTO studies (id, workspace_id, study_code, title, status, created_at)
                VALUES (:id, :workspace_id, 'TEST001', 'Test Study', 'active', NOW())
            """),
            {"id": study_id, "workspace_id": workspace_id}
        )
        
        # Создаем document
        await db.execute(
            text("""
                INSERT INTO documents (id, workspace_id, study_id, doc_type, title, lifecycle_status, created_at)
                VALUES (:id, :workspace_id, :study_id, 'protocol', 'Test Document', 'draft', NOW())
            """),
            {"id": doc_id, "workspace_id": workspace_id, "study_id": study_id}
        )
        
        # Создаем document_version
        await db.execute(
            text("""
                INSERT INTO document_versions (id, document_id, version_label, ingestion_status, document_language, created_at)
                VALUES (:id, :doc_id, 'v1', 'ready', 'unknown', NOW())
            """),
            {"id": doc_version_id, "doc_id": doc_id}
        )
        
        # Создаем template
        await db.execute(
            text("""
                INSERT INTO templates (id, workspace_id, doc_type, name, template_body, version, created_at)
                VALUES (:id, :workspace_id, 'protocol', 'Test Template', 'Template body', 1, NOW())
            """),
            {"id": template_id, "workspace_id": workspace_id}
        )
        
        # Создаем section_contract со старым именем section_key
        contract_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO target_section_contracts (
                    id, workspace_id, doc_type, target_section, title,
                    required_facts_json, allowed_sources_json, retrieval_recipe_json,
                    qc_ruleset_json, citation_policy, version, is_active, created_at
                )
                VALUES (
                    :id, :workspace_id, 'protocol', 'protocol.soa', 'Test Contract',
                    '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 'per_sentence', 1, true, NOW()
                )
            """),
            {"id": contract_id, "workspace_id": workspace_id}
        )
        
        # Создаем generation_run со старым именем section_key
        generation_run_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO generation_runs (
                    id, study_id, target_doc_type, section_key, template_id, contract_id,
                    input_snapshot_json, status, created_at
                )
                VALUES (
                    :id, :study_id, 'protocol', 'protocol.soa', :template_id, :contract_id,
                    '{}'::jsonb, 'queued', NOW()
                )
            """),
            {
                "id": generation_run_id,
                "study_id": study_id,
                "template_id": template_id,
                "contract_id": contract_id
            }
        )
        
        # Создаем section_map со старым именем section_key
        section_map_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO target_section_maps (
                    id, doc_version_id, target_section, anchor_ids, chunk_ids,
                    confidence, status, mapped_by, created_at
                )
                VALUES (
                    :id, :doc_version_id, 'protocol.soa', ARRAY[]::text[], ARRAY[]::uuid[],
                    0.9, 'mapped', 'system', NOW()
                )
            """),
            {"id": section_map_id, "doc_version_id": doc_version_id}
        )
        
        # Создаем impact_item со старым именем affected_section_key
        change_event_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO change_events (
                    id, study_id, source_document_id, from_version_id, to_version_id,
                    diff_summary_json, created_at
                )
                VALUES (
                    :id, :study_id, :doc_id, :doc_version_id, :doc_version_id,
                    '{}'::jsonb, NOW()
                )
            """),
            {
                "id": change_event_id,
                "study_id": study_id,
                "doc_id": doc_id,
                "doc_version_id": doc_version_id
            }
        )
        
        impact_item_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO impact_items (
                    id, change_event_id, affected_doc_type, affected_section_key,
                    reason_json, recommended_action, status, created_at
                )
                VALUES (
                    :id, :change_event_id, 'protocol', 'protocol.soa',
                    '{}'::jsonb, 'manual_review', 'pending', NOW()
                )
            """),
            {"id": impact_item_id, "change_event_id": change_event_id}
        )
        
        # Создаем anchor и chunk (без source_zone и language, они будут добавлены)
        anchor_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO anchors (
                    id, doc_version_id, anchor_id, section_path, content_type,
                    ordinal, text_raw, text_norm, text_hash, location_json, created_at
                )
                VALUES (
                    :id, :doc_version_id, 'test_anchor_1', '1.1', 'p',
                    1, 'Test text', 'test text', 'hash123', '{}'::jsonb, NOW()
                )
            """),
            {"id": anchor_id, "doc_version_id": doc_version_id}
        )
        
        chunk_id = uuid.uuid4()
        await db.execute(
            text("""
                INSERT INTO chunks (
                    id, doc_version_id, chunk_id, section_path, text,
                    anchor_ids, embedding, created_at
                )
                VALUES (
                    :id, :doc_version_id, 'test_chunk_1', '1.1', 'Test chunk text',
                    ARRAY[]::text[], ARRAY[0.0]::float[], NOW()
                )
            """),
            {"id": chunk_id, "doc_version_id": doc_version_id}
        )
        
        await db.commit()
    
    # 3. Применяем миграцию 0007
    async with db_engine.begin() as conn:
        def run_upgrade(sync_conn):
            command.upgrade(alembic_cfg, "0007_rename_section_key_to_target_section")
        
        await conn.run_sync(run_upgrade)
    
    # 4. Проверяем, что данные сохранились и доступны через новые имена полей
    async with db.begin():
        # Проверяем section_contracts
        result = await db.execute(
            text("SELECT target_section, view_key FROM target_section_contracts WHERE id = :id"),
            {"id": contract_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "protocol.soa"  # target_section
        assert row[1] is None  # view_key (NULL)
        
        # Проверяем generation_runs
        result = await db.execute(
            text("SELECT target_section, view_key FROM generation_runs WHERE id = :id"),
            {"id": generation_run_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "protocol.soa"  # target_section
        assert row[1] is None  # view_key (NULL)
        
        # Проверяем section_maps
        result = await db.execute(
            text("SELECT target_section FROM target_section_maps WHERE id = :id"),
            {"id": section_map_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "protocol.soa"  # target_section
        
        # Проверяем impact_items
        result = await db.execute(
            text("SELECT affected_target_section FROM impact_items WHERE id = :id"),
            {"id": impact_item_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "protocol.soa"  # affected_target_section
        
        # Проверяем anchors (source_zone и language должны быть добавлены с default)
        result = await db.execute(
            text("SELECT source_zone, language FROM anchors WHERE id = :id"),
            {"id": anchor_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "unknown"  # source_zone default
        assert row[1] == "unknown"  # language default
        
        # Проверяем chunks (source_zone и language должны быть добавлены с default)
        result = await db.execute(
            text("SELECT source_zone, language FROM chunks WHERE id = :id"),
            {"id": chunk_id}
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "unknown"  # source_zone default
        assert row[1] == "unknown"  # language default
        
        # Проверяем индексы
        result = await db.execute(
            text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'anchors' AND indexname LIKE '%source_zone%'
            """)
        )
        indexes = [row[0] for row in result.fetchall()]
        assert "ix_anchors_doc_version_source_zone" in indexes
        
        result = await db.execute(
            text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'chunks' AND indexname LIKE '%source_zone%'
            """)
        )
        indexes = [row[0] for row in result.fetchall()]
        assert "ix_chunks_doc_version_source_zone" in indexes
        
        await db.commit()
    
    # 5. Проверяем обратную совместимость через property в моделях
    async with db.begin():
        # Загружаем через ORM модели
        contract = await db.get(TargetSectionContract, contract_id)
        assert contract.target_section == "protocol.soa"
        # Проверяем обратную совместимость
        assert contract.section_key == "protocol.soa"  # через property
        
        generation_run = await db.get(GenerationRun, generation_run_id)
        assert generation_run.target_section == "protocol.soa"
        assert generation_run.section_key == "protocol.soa"  # через property
        
        section_map = await db.get(TargetSectionMap, section_map_id)
        assert section_map.target_section == "protocol.soa"
        assert section_map.section_key == "protocol.soa"  # через property
        
        impact_item = await db.get(ImpactItem, impact_item_id)
        assert impact_item.affected_target_section == "protocol.soa"
        assert impact_item.affected_section_key == "protocol.soa"  # через property
        
        anchor = await db.get(Anchor, anchor_id)
        assert anchor.source_zone == "unknown"
        assert anchor.language == DocumentLanguage.UNKNOWN
        
        chunk = await db.get(Chunk, chunk_id)
        assert chunk.source_zone == "unknown"
        assert chunk.language == DocumentLanguage.UNKNOWN
        
        await db.commit()

