"""
Сидер Lean Section Passports (section_contracts) для MVP.

Требования:
- Паспорта как DATA в БД.
- В MVP запрещаем UI/API редактирование; загрузка только из репозитория.
- Читаем contracts/seed/*.json и upsert в section_contracts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.logging import logger
from app.db.models.sections import TargetSectionContract
from app.db.enums import CitationPolicy, DocumentType
from app.schemas.sections import (
    AllowedSourcesMVP,
    QCRulesetMVP,
    RequiredFactsMVP,
    RetrievalRecipeMVP,
)


def _load_seed_files(seed_dir: Path) -> list[dict[str, Any]]:
    files = sorted(seed_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"Не найдено seed-файлов в {seed_dir}")
    out: list[dict[str, Any]] = []
    for fp in files:
        data = json.loads(fp.read_text(encoding="utf-8"))
        data["_seed_file"] = fp.name
        out.append(data)
    return out


def _validate_contract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Минимальная валидация lean JSON; лишние поля игнорируются схемами.
    payload["required_facts_json"] = RequiredFactsMVP.model_validate(
        payload.get("required_facts_json") or {}
    ).model_dump()
    payload["allowed_sources_json"] = AllowedSourcesMVP.model_validate(
        payload.get("allowed_sources_json") or {}
    ).model_dump()
    payload["retrieval_recipe_json"] = RetrievalRecipeMVP.model_validate(
        payload.get("retrieval_recipe_json") or {}
    ).model_dump()
    payload["qc_ruleset_json"] = QCRulesetMVP.model_validate(
        payload.get("qc_ruleset_json") or {}
    ).model_dump()
    return payload


async def seed_contracts(*, workspace_id: UUID, seed_dir: Path, deactivate_others: bool) -> None:
    engine = create_async_engine(settings.async_database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    payloads = _load_seed_files(seed_dir)
    logger.info(f"Загрузка {len(payloads)} паспортов из {seed_dir}")

    async with session_factory() as session:
        for raw in payloads:
            seed_file = raw.pop("_seed_file", "unknown")
            doc_type = DocumentType(raw["doc_type"])
            section_key = raw["section_key"]
            version = int(raw.get("version", 2))

            payload = _validate_contract_payload(raw)

            # Upsert по уникальному ключу (workspace_id, doc_type, section_key, version)
            stmt = select(TargetSectionContract).where(
                TargetSectionContract.workspace_id == workspace_id,
                TargetSectionContract.doc_type == doc_type,
                TargetSectionContract.target_section == section_key,
                TargetSectionContract.version == version,
            )
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()

            if existing:
                existing.title = payload["title"]
                existing.required_facts_json = payload["required_facts_json"]
                existing.allowed_sources_json = payload["allowed_sources_json"]
                existing.retrieval_recipe_json = payload["retrieval_recipe_json"]
                existing.qc_ruleset_json = payload["qc_ruleset_json"]
                existing.citation_policy = CitationPolicy(payload.get("citation_policy", "per_claim"))
                existing.is_active = bool(payload.get("is_active", True))
                logger.info(f"UPDATED {doc_type.value}:{section_key} v{version} ({seed_file})")
            else:
                contract = TargetSectionContract(
                    workspace_id=workspace_id,
                    doc_type=doc_type,
                    target_section=section_key,
                    title=payload["title"],
                    required_facts_json=payload["required_facts_json"],
                    allowed_sources_json=payload["allowed_sources_json"],
                    retrieval_recipe_json=payload["retrieval_recipe_json"],
                    qc_ruleset_json=payload["qc_ruleset_json"],
                    citation_policy=CitationPolicy(payload.get("citation_policy", "per_claim")),
                    version=version,
                    is_active=bool(payload.get("is_active", True)),
                )
                session.add(contract)
                logger.info(f"INSERTED {doc_type.value}:{section_key} v{version} ({seed_file})")

            if deactivate_others:
                # Деактивируем другие версии той же секции
                stmt_others = select(TargetSectionContract).where(
                    TargetSectionContract.workspace_id == workspace_id,
                    TargetSectionContract.doc_type == doc_type,
                    TargetSectionContract.target_section == section_key,
                    TargetSectionContract.version != version,
                    TargetSectionContract.is_active == True,
                )
                res_others = await session.execute(stmt_others)
                for other in res_others.scalars().all():
                    other.is_active = False

        await session.commit()

    await engine.dispose()


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed Lean section_contracts passports into DB")
    ap.add_argument("--workspace-id", required=True, help="Workspace UUID")
    ap.add_argument(
        "--seed-dir",
        default=str(Path(__file__).resolve().parents[3] / "contracts" / "seed"),
        help="Путь к contracts/seed (по умолчанию: <repo>/contracts/seed)",
    )
    ap.add_argument(
        "--no-deactivate-others",
        action="store_true",
        help="Не деактивировать другие версии той же секции",
    )
    args = ap.parse_args()

    workspace_id = UUID(args.workspace_id)
    seed_dir = Path(args.seed_dir).resolve()
    deactivate_others = not args.no_deactivate_others

    import asyncio

    asyncio.run(
        seed_contracts(
            workspace_id=workspace_id,
            seed_dir=seed_dir,
            deactivate_others=deactivate_others,
        )
    )


if __name__ == "__main__":
    main()


