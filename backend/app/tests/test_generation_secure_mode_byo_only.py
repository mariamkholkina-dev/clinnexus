from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.db.enums import DocumentType, QCStatus
from app.schemas.generation import GenerateSectionRequest, QCReportSchema
from app.services.generation import GenerationService, ValidationService
from app.services.lean_passport import LeanContextBuilder


class _FakeDB:
    def __init__(self, *, template, contract) -> None:
        self._template = template
        self._contract = contract
        self._added: list[object] = []

    async def get(self, model_cls, obj_id):  # noqa: ANN001
        # GenerationService запрашивает Template и SectionContract по id.
        name = getattr(model_cls, "__name__", "")
        if name == "Template":
            return self._template
        if name == "SectionContract":
            return self._contract
        return None

    def add(self, obj) -> None:  # noqa: ANN001
        self._added.append(obj)

    async def flush(self) -> None:
        # Эмулируем присвоение UUID при flush (как при INSERT).
        for obj in self._added:
            if getattr(obj, "id", None) is None:
                try:
                    setattr(obj, "id", uuid4())
                except Exception:  # noqa: BLE001
                    pass

    async def commit(self) -> None:
        return None

    async def refresh(self, obj) -> None:  # noqa: ANN001
        return None


@pytest.mark.asyncio
async def test_secure_mode_required_without_byo_key_returns_blocked(monkeypatch) -> None:
    template = SimpleNamespace(template_body="tmpl")
    contract = SimpleNamespace(
        section_key="protocol.soa",
        required_facts_json={},
        allowed_sources_json={},
        retrieval_recipe_json={"security": {"secure_mode_required": True}},
        qc_ruleset_json={},
    )
    db = _FakeDB(template=template, contract=contract)

    # Чтобы тест не ходил в БД за контекстом/валидацией (до них мы не дойдём в blocked ветке,
    # но пусть будет явно безопасно).
    async def fake_build_context(self, *, study_id, contract, source_doc_version_ids):  # noqa: ANN001
        return "", []

    async def fake_validate(self, **kwargs):  # noqa: ANN001
        return QCReportSchema(status=QCStatus.PASSED, errors=[])

    monkeypatch.setattr(LeanContextBuilder, "build_context", fake_build_context)
    monkeypatch.setattr(ValidationService, "validate", fake_validate)

    svc = GenerationService(db)  # type: ignore[arg-type]
    req = GenerateSectionRequest(
        study_id=uuid4(),
        target_doc_type=DocumentType.PROTOCOL,
        section_key="protocol.soa",
        template_id=uuid4(),
        contract_id=uuid4(),
        source_doc_version_ids=[uuid4()],
        user_instruction=None,
    )

    res = await svc.generate_section(req, byo_key=None)
    assert res.qc_status == QCStatus.BLOCKED
    assert res.qc_report_json.status == QCStatus.BLOCKED
    assert res.qc_report_json.errors
    assert res.qc_report_json.errors[0].type == "byo_key_required"
    assert "X-LLM-API-Key" in res.qc_report_json.errors[0].message


@pytest.mark.asyncio
async def test_secure_mode_required_with_byo_key_is_not_blocked(monkeypatch) -> None:
    template = SimpleNamespace(template_body="tmpl")
    contract = SimpleNamespace(
        section_key="protocol.soa",
        required_facts_json={},
        allowed_sources_json={},
        retrieval_recipe_json={"security": {"secure_mode_required": True}},
        qc_ruleset_json={},
    )
    db = _FakeDB(template=template, contract=contract)

    async def fake_build_context(self, *, study_id, contract, source_doc_version_ids):  # noqa: ANN001
        return "ctx", []

    async def fake_validate(self, **kwargs):  # noqa: ANN001
        return QCReportSchema(status=QCStatus.PASSED, errors=[])

    monkeypatch.setattr(LeanContextBuilder, "build_context", fake_build_context)
    monkeypatch.setattr(ValidationService, "validate", fake_validate)

    svc = GenerationService(db)  # type: ignore[arg-type]
    req = GenerateSectionRequest(
        study_id=uuid4(),
        target_doc_type=DocumentType.PROTOCOL,
        section_key="protocol.soa",
        template_id=uuid4(),
        contract_id=uuid4(),
        source_doc_version_ids=[uuid4()],
        user_instruction=None,
    )

    res = await svc.generate_section(req, byo_key="test-key")
    assert res.qc_status != QCStatus.BLOCKED


