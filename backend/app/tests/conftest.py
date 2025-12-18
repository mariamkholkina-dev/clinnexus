"""
Локальные фикстуры pytest для тестов в backend/app/tests.

Важно: conftest из backend/tests не является родительским для backend/app/tests,
поэтому дублируем минимальный набор (db_engine/db) здесь.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base


# Настройка event loop для Windows (psycopg требует SelectorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _create_enum_if_not_exists(conn, enum_name: str, enum_values: str) -> None:
    """Создает enum-тип, если он не существует."""
    result = conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)"),
        {"name": enum_name},
    ).scalar()
    if not result:
        conn.execute(text(f"CREATE TYPE {enum_name} AS ENUM {enum_values}"))


def _create_enums_sync(conn) -> None:
    """Создает все необходимые enum-типы и расширения в PostgreSQL."""
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    _create_enum_if_not_exists(conn, "workspace_role", "('admin','writer','clinops','qa')")
    _create_enum_if_not_exists(conn, "study_status", "('active','archived')")
    _create_enum_if_not_exists(
        conn, "document_type", "('protocol','sap','tfl','csr','ib','icf','other')"
    )
    _create_enum_if_not_exists(
        conn, "document_lifecycle_status", "('draft','in_review','approved','superseded')"
    )
    _create_enum_if_not_exists(conn, "ingestion_status", "('uploaded','processing','ready','needs_review','failed')")
    _create_enum_if_not_exists(conn, "document_language", "('ru','en','mixed','unknown')")

    _create_enum_if_not_exists(conn, "anchor_content_type", "('p','cell','fn','hdr','li','tbl')")

    _create_enum_if_not_exists(conn, "citation_policy", "('per_sentence','per_claim','none')")
    _create_enum_if_not_exists(conn, "section_map_status", "('mapped','needs_review','overridden')")
    _create_enum_if_not_exists(conn, "section_map_mapped_by", "('system','user')")

    _create_enum_if_not_exists(conn, "fact_status", "('extracted','validated','conflicting','tbd','needs_review')")
    _create_enum_if_not_exists(conn, "evidence_role", "('primary','supporting')")

    _create_enum_if_not_exists(conn, "conflict_severity", "('low','medium','high','critical')")
    _create_enum_if_not_exists(
        conn, "conflict_status", "('open','investigating','resolved','accepted_risk','suppressed')"
    )

    _create_enum_if_not_exists(conn, "generation_status", "('queued','running','blocked','completed','failed')")
    _create_enum_if_not_exists(conn, "qc_status", "('pass','fail','blocked')")

    _create_enum_if_not_exists(conn, "recommended_action", "('auto_patch','regenerate_draft','manual_review')")
    _create_enum_if_not_exists(conn, "impact_status", "('pending','applied','rejected')")
    _create_enum_if_not_exists(
        conn, "task_type", "('review_extraction','resolve_conflict','review_impact','regenerate_section')"
    )
    _create_enum_if_not_exists(conn, "task_status", "('open','in_progress','done','cancelled')")


@pytest.fixture(scope="function")
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Создает тестовый async engine для БД (PostgreSQL обязателен из-за JSONB/pgvector)."""
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url:
        db_host = os.getenv("TEST_DB_HOST", "localhost")
        db_port = os.getenv("TEST_DB_PORT", "5432")
        db_name = os.getenv("TEST_DB_NAME", "clinnexus_test")
        db_user = os.getenv("TEST_DB_USER", "clinnexus")
        db_password = os.getenv("TEST_DB_PASSWORD", "clinnexus")
        test_db_url = f"postgresql+psycopg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    engine = create_async_engine(test_db_url, echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_create_enums_sync)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="function")
async def db(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Фикстура async-сессии БД, откатывает транзакцию после теста."""
    async_session_factory = async_sessionmaker(
        db_engine, expire_on_commit=False, class_=AsyncSession
    )
    async with async_session_factory() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            if transaction.is_active:
                await transaction.rollback()
            await session.close()


