## Архитектура ClinNexus MVP

### Ключевые сущности

- **Workspace / User / Membership**: многотенантность и RBAC на уровне рабочего пространства.
- **Study**: исследование в рамках workspace.
- **Document / DocumentVersion**: документы и их версии, связанные со study.
- **Anchor**: минимальная адресуемая единица содержимого (параграф/ячейка/функция и т.д.).
  - `anchor_id = {doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash(text_norm)}`
  - `location_json` описывает координаты в исходном документе (page/bbox, table_id/row/col и т.п.).
- **Chunk**: векторное представление нескольких anchor-ов (pgvector) + список `anchor_ids`.
- **Study KB Facts + fact_evidence**: факты исследования и их привязка к конкретным anchor-ам.
- **Templates + SectionContracts**: шаблоны и контракты разделов протокола (JSON-схемы).
- **GenerationRun + GeneratedSection**: процесс и результат генерации текста раздела, артефакты и QC.
- **Conflicts + conflict_items**: обнаруженные противоречия между фактами/документами.
- **ChangeEvents + ImpactItems + Tasks**: изменения, их потенциальный импакт и задачи по внедрению.
- **AuditLog**: append-only лог действий пользователей.

### Слои backend

- `app/api`: HTTP-контракты (FastAPI), только оркестрация и валидация.
- `app/services`: доменные сервисы (ingestion, extraction, retrieval, generation, validation, diff, impact, conflict).
- `app/db`: SQLAlchemy-модели и сессии.
- `app/schemas`: Pydantic-схемы для JSON-платформы (FactItem, SoAResult, Artifacts, QCReport и др.).
- `app/core`: настройки, ошибки, кросс-срезы.
- `app/worker`: заготовка воркера для фоновых задач (ingestion, extraction, generation).

### Пайплайны

- **Ingestion**
  1. `POST /document_versions/{version_id}/upload` — загрузка файла, сохранение в локальное хранилище.
  2. `POST /document_versions/{version_id}/ingest` — постановка задачи ingestion в воркер.
  3. Воркер извлекает текст/структуру, создаёт Anchors, Chunks, обновляет статус `DocumentVersion`.

- **SoA / Facts**
  1. Воркер строит SoAResult и записывает факты (Study KB Facts) + fact_evidence.
  2. `GET /studies/{study_id}/facts` читает агрегированное представление.

- **Retrieval / Generation**
  1. RetrievalService использует pgvector для поиска релевантных anchors/chunks.
  2. GenerationService формирует `GenerationRun` и `GeneratedSection` (+Artifacts, QCReport).

- **Conflicts / Impact**
  1. DiffService/ValidationService генерируют ChangeEvents, Conflicts.
  2. ImpactService проецирует изменения на ImpactItems и Tasks.


