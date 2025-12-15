"""
Скрипт для заполнения БД начальными данными:
- workspace и user
- study
- template "CSR minimal"
- section_contracts (protocol.soa, protocol.endpoints, csr.methods.schedule)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.db.models.auth import User, Workspace
from app.db.models.studies import Study
from app.db.models.generation import Template
from app.db.models.sections import SectionContract
from app.db.enums import (
    DocumentType,
    StudyStatus,
    CitationPolicy,
    WorkspaceRole,
)


async def seed_db() -> None:
    """Заполнение БД начальными данными."""
    engine = create_async_engine(settings.async_database_url, echo=True)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session_factory() as session:
        # 1. Создаём workspace
        workspace = Workspace(name="Default Workspace")
        session.add(workspace)
        await session.flush()

        # 2. Создаём user
        user = User(
            email="admin@clinnexus.local",
            name="Admin User",
            is_active=True,
        )
        session.add(user)
        await session.flush()

        # 3. Создаём study
        study = Study(
            workspace_id=workspace.id,
            study_code="STUDY-001",
            title="Test Study",
            status=StudyStatus.ACTIVE,
        )
        session.add(study)
        await session.flush()

        # 4. Создаём template "CSR minimal"
        template = Template(
            workspace_id=workspace.id,
            doc_type=DocumentType.CSR,
            name="CSR minimal",
            template_body="# Methods\n\n## Study Design\n\n## Statistical Methods\n\n",
            version=1,
        )
        session.add(template)
        await session.flush()

        # 5. Создаём section_contracts
        contracts = [
            SectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                section_key="protocol.soa",
                title="Schedule of Activities",
                required_facts_json={
                    "visits": ["visit_id", "name", "day"],
                    "procedures": ["code", "name"],
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.soa"],
                },
                retrieval_recipe_json={
                    "method": "table_extraction",
                    "filters": {},
                },
                qc_ruleset_json={
                    "required_fields": ["visits", "procedures"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_CLAIM,
                version=1,
                is_active=True,
            ),
            SectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                section_key="protocol.endpoints",
                title="Endpoints",
                required_facts_json={
                    "primary_endpoint": {"type": "string"},
                    "secondary_endpoints": {"type": "array"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.endpoints"],
                },
                retrieval_recipe_json={
                    "method": "section_extraction",
                    "filters": {},
                },
                qc_ruleset_json={
                    "required_fields": ["primary_endpoint"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            SectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.CSR,
                section_key="csr.methods.schedule",
                title="Methods - Schedule",
                required_facts_json={
                    "schedule_description": {"type": "string"},
                    "visit_schedule": {"type": "array"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol", "csr"],
                    "section_keys": ["protocol.soa", "csr.methods.schedule"],
                },
                retrieval_recipe_json={
                    "method": "section_extraction",
                    "filters": {},
                },
                qc_ruleset_json={
                    "required_fields": ["schedule_description"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
        ]

        for contract in contracts:
            session.add(contract)

        await session.commit()

        print("✅ Seed завершён успешно!")
        print(f"   Workspace ID: {workspace.id}")
        print(f"   User ID: {user.id}")
        print(f"   Study ID: {study.id}")
        print(f"   Template ID: {template.id}")
        print(f"   Section Contracts: {len(contracts)}")


if __name__ == "__main__":
    asyncio.run(seed_db())
