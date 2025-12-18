from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.db.enums import QCStatus
from app.main import create_app
from app.schemas.generation import (
    ArtifactsSchema,
    GenerateSectionResult,
    QCReportSchema,
)
from app.services.generation import GenerationService


def test_generate_section_byo_key_is_passed_to_service(monkeypatch) -> None:
    received: list[str | None] = []

    async def override_get_db():
        yield None

    async def fake_generate_section(self, req, *, byo_key: str | None = None):
        received.append(byo_key)
        return GenerateSectionResult(
            content_text="ok",
            artifacts_json=ArtifactsSchema(),
            qc_status=QCStatus.PASSED,
            qc_report_json=QCReportSchema(status=QCStatus.PASSED, errors=[]),
            generation_run_id=uuid4(),
        )

    monkeypatch.setattr(GenerationService, "generate_section", fake_generate_section)

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    payload = {
        "study_id": str(uuid4()),
        "target_doc_type": "protocol",
        "section_key": "protocol.soa",
        "template_id": str(uuid4()),
        "contract_id": str(uuid4()),
        "source_doc_version_ids": [str(uuid4())],
        "user_instruction": None,
    }

    # Без заголовка
    r1 = client.post("/api/generate/section", json=payload)
    assert r1.status_code == 200

    # С заголовком (обрезаем пробелы)
    r2 = client.post(
        "/api/generate/section",
        json=payload,
        headers={"X-LLM-API-Key": "  test-key  "},
    )
    assert r2.status_code == 200

    assert received == [None, "test-key"]


