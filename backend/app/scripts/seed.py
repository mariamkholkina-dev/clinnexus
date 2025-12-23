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
from app.db.models.sections import TargetSectionContract
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

        # 5. Создаём section_contracts для протокола (MVP набор)
        contracts = [
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.synopsis",
                title="Synopsis",
                required_facts_json={},
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.synopsis"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["synopsis", "summary"],
                        "should": ["overview", "brief"],
                        "not": ["table of contents", "contents"],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Synopsis|Summary)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": [],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.objectives",
                title="Objectives",
                required_facts_json={
                    "primary_objective": {"type": "string"},
                    "secondary_objectives": {"type": "array"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.objectives"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["objective", "objectives", "study objectives", "цели исследования"],
                        "should": ["endpoint", "rationale"],
                        "not": ["table of contents", "contents"],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Objectives|Study Objectives)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                        "prefer_nearby": ["synopsis", "study design"],
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["primary_objective"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.study_design",
                title="Study Design",
                required_facts_json={
                    "design_type": {"type": "string"},
                    "randomization": {"type": "string"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.study_design"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["study design", "design"],
                        "should": ["methodology", "methods"],
                        "not": [],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Study Design|Design)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["design_type"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.soa",
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
                    "version": 1,
                    "heading_match": {
                        "must": ["schedule", "activities", "soa"],
                        "should": ["visits", "procedures", "таблица"],
                        "not": [],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Schedule of Activities|SoA|Visits)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["visits", "procedures"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_CLAIM,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.eligibility.inclusion",
                title="Inclusion Criteria",
                required_facts_json={
                    "criteria": {"type": "array"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.eligibility.inclusion"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["inclusion", "inclusion criteria"],
                        "should": ["eligibility", "criteria"],
                        "not": ["exclusion"],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Inclusion Criteria|Inclusion)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["criteria"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.eligibility.exclusion",
                title="Exclusion Criteria",
                required_facts_json={
                    "criteria": {"type": "array"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.eligibility.exclusion"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["exclusion", "exclusion criteria"],
                        "should": ["eligibility", "criteria"],
                        "not": ["inclusion"],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Exclusion Criteria|Exclusion)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["criteria"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.treatments.dosing",
                title="Treatments and Dosing",
                required_facts_json={
                    "treatments": {"type": "array"},
                    "dosing": {"type": "string"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.treatments.dosing"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["treatment", "dosing", "dose"],
                        "should": ["drug", "medication", "therapy"],
                        "not": [],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Treatment|Dosing|Dose)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["treatments"],
                    "validation_rules": [],
                },
                citation_policy=CitationPolicy.PER_SENTENCE,
                version=1,
                is_active=True,
            ),
            TargetSectionContract(
                workspace_id=workspace.id,
                doc_type=DocumentType.PROTOCOL,
                target_section="protocol.safety.ae_reporting",
                title="Safety - Adverse Event Reporting",
                required_facts_json={
                    "ae_reporting": {"type": "string"},
                },
                allowed_sources_json={
                    "doc_types": ["protocol"],
                    "section_keys": ["protocol.safety.ae_reporting"],
                },
                retrieval_recipe_json={
                    "version": 1,
                    "heading_match": {
                        "must": ["adverse event", "ae reporting", "safety"],
                        "should": ["reporting", "events", "safety"],
                        "not": [],
                    },
                    "regex": {
                        "heading": ["^(\\d+\\.)?\\s*(Adverse Event|AE Reporting|Safety)\\b"],
                    },
                    "scope": {
                        "doc_zone": "protocol",
                    },
                    "capture": {
                        "strategy": "heading_block",
                        "max_depth": 3,
                        "stop_at_same_or_higher_level": True,
                    },
                },
                qc_ruleset_json={
                    "required_fields": ["ae_reporting"],
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
