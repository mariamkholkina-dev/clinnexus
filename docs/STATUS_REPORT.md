## STATUS REPORT (на дату: 2025-12-17)

### 1) Стек и запуск (команды)

- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy (async), Alembic, PostgreSQL + pgvector, python-docx, httpx  
  - Файлы: `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/core/config.py`
- **DB**: PostgreSQL (в docker-compose используется `pgvector/pgvector:0.8.1-pg18-trixie`)  
  - Файлы: `docker-compose.yml`, `backend/DATABASE_SETUP.md`
- **Frontend**: Next.js (app router), TypeScript  
  - Файлы: `frontend/package.json`, `frontend/app/*`

**Запуск dev через Docker:**
- `docker-compose up --build` (поднимает db/backend/frontend) — `Makefile`, `docker-compose.yml`
- Миграции: `docker-compose run --rm backend alembic -c /app/db/alembic.ini upgrade head` — `Makefile`
- Seed: `docker-compose run --rm backend python -m app.scripts.seed` — `Makefile`, `backend/app/scripts/seed.py`

**Локальный запуск backend (без Docker):**
- `cd backend && pip install -e .`
- `cd backend && alembic upgrade head`
- `cd backend && python -m app.scripts.seed`
- `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`  
  - Файл: `backend/BACKEND_SETUP.md`

### 2) Эндпоинты реализованы (по OpenAPI + по коду)

**OpenAPI (автогенерация FastAPI):**
- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`
- Health: `/health`  
  - Файл: `backend/app/main.py`

**API роуты (prefix `/api`, версия v1 отсутствует как prefix в коде):**
- Роутер: `backend/app/api/v1/__init__.py` (подключается в `backend/app/main.py` как `app.include_router(..., prefix="/api")`)

**Studies / Documents / Versions / Anchors / SoA (по коду):**
- `POST /api/studies` — создать study  
- `GET /api/studies/{study_id}` — получить study  
- `GET /api/studies?workspace_id=...` — список studies  
- `GET /api/studies/{study_id}/documents` — список документов исследования  
- `GET /api/documents/{document_id}/versions` — список версий документа  
- `GET /api/studies/{study_id}/facts` — список фактов (facts + evidence)  
  - Файл: `backend/app/api/v1/studies.py`
- `POST /api/studies/{study_id}/documents` — создать документ  
- `POST /api/documents/{document_id}/versions` — создать версию документа  
- `POST /api/document-versions/{version_id}/upload` — загрузка файла (валидирует расширение `.pdf|.docx|.xlsx`, проверяет non-empty, пишет в storage, сохраняет sha256)  
- `POST /api/document-versions/{version_id}/ingest?force=...` — запуск ингестии (uploaded→processing→ready|needs_review|failed; guard rails)  
- `GET /api/document-versions/{version_id}` — получить версию  
- `GET /api/document-versions/{version_id}/anchors?section_path=...&content_type=...` — якоря  
- `GET /api/document-versions/{version_id}/soa` — извлечённый SoA из facts типа `soa`  
  - Файл: `backend/app/api/v1/documents.py`

**Section contracts / section maps (по коду):**
- `GET /api/section-contracts?doc_type=...&is_active=...` — список контрактов  
- `POST /api/section-contracts` — создание контракта (в MVP запрещено по умолчанию: `ENABLE_CONTRACT_EDITING=false`)  
- `GET /api/document-versions/{version_id}/section-maps` — список маппингов  
- `POST /api/document-versions/{version_id}/section-maps/{section_key}/override` — override пользователем  
- `POST /api/document-versions/{version_id}/section-maps/rebuild?force=...` — пересборка system mappings  
- `POST /api/document-versions/{version_id}/section-maps/assist` — LLM-assisted mapping (gated secure_mode + keys)  
  - Файл: `backend/app/api/v1/sections.py`

**Generation / Conflicts / Impact / Tasks (по коду):**
- `POST /api/generate/section` — генерация секции (MVP: детерминированный черновик + QC)  
  - Файл: `backend/app/api/v1/generation.py`
- `GET /api/conflicts?study_id=...` — детект конфликтов (сервисный слой)  
  - Файл: `backend/app/api/v1/conflicts.py`
- `GET /api/impact?study_id=...&change_event_id=...` — если есть `change_event_id`, считает impact; иначе TODO (возвращает `[]`)  
- `GET /api/tasks?study_id=...` — список tasks из БД  
  - Файл: `backend/app/api/v1/impact.py`

### 3) Таблицы в БД и ключевые поля (особенно enums `document_type` и `citation_policy`)

**Основные ENUM (PostgreSQL native enum + Python Enum):**
- `document_type`: `protocol|sap|tfl|csr|ib|icf|other`  
  - Файлы: `backend/app/db/enums.py` (`DocumentType`), `backend/alembic/versions/0001_initial_prod_skeleton.py`
- `citation_policy`: `per_sentence|per_claim|none`  
  - Файлы: `backend/app/db/enums.py` (`CitationPolicy`), `backend/alembic/versions/0001_initial_prod_skeleton.py`, ORM: `backend/app/db/models/sections.py`
- `document_language`: `ru|en|mixed|unknown`  
  - Файлы: `backend/app/db/enums.py` (`DocumentLanguage`), `backend/alembic/versions/0004_add_document_language.py`, ORM: `backend/app/db/models/studies.py` (поле `document_versions.document_language`)

**Таблицы (по ORM; полный DDL — миграция 0001):**
- `workspaces(id, name, created_at)` — `backend/app/db/models/auth.py`
- `users(id, email, name, is_active, created_at)` — `backend/app/db/models/auth.py`
- `memberships(id, workspace_id, user_id, role, created_at)` + uq(workspace_id,user_id) — `backend/app/db/models/auth.py`
- `studies(id, workspace_id, study_code, title, status, created_at)` + uq(workspace_id,study_code) — `backend/app/db/models/studies.py`
- `documents(id, workspace_id, study_id, doc_type, title, lifecycle_status, created_at)` — `backend/app/db/models/studies.py`
- `document_versions(id, document_id, version_label, source_file_uri, source_sha256, effective_date, ingestion_status, ingestion_summary_json, document_language, created_by, created_at)` — `backend/app/db/models/studies.py`
- `anchors(id, doc_version_id, anchor_id[unique], section_path, content_type, ordinal, text_raw, text_norm, text_hash, location_json, confidence, created_at)` — `backend/app/db/models/anchors.py`
- `chunks(id, doc_version_id, chunk_id[unique], section_path, text, anchor_ids[], embedding vector(1536), metadata_json, created_at)` — `backend/app/db/models/anchors.py`, индексы/pgvector: `backend/alembic/versions/0002_enums_and_vector.py`
- `section_contracts(id, workspace_id, doc_type, section_key, title, required_facts_json, allowed_sources_json, retrieval_recipe_json, qc_ruleset_json, citation_policy, version, is_active, created_at)` + uq(workspace_id,doc_type,section_key,version) — `backend/app/db/models/sections.py`
- `section_maps(id, doc_version_id, section_key, anchor_ids[], chunk_ids[], confidence, status, mapped_by, notes, created_at)` — `backend/app/db/models/sections.py`
- `facts(id, study_id, fact_type, fact_key, value_json, unit, status, created_from_doc_version_id, created_at, updated_at)` + uq(study_id,fact_type,fact_key) — `backend/app/db/models/facts.py`
- `fact_evidence(id, fact_id, anchor_id, evidence_role, created_at)` + uq(fact_id,anchor_id,evidence_role) — `backend/app/db/models/facts.py`, уникальный индекс: `backend/alembic/versions/0005_unique_fact_evidence.py`
- `templates(id, workspace_id, doc_type, name, template_body, version, created_at)` — `backend/app/db/models/generation.py`
- `model_configs(id, provider, model_name, prompt_version, params_json, created_at)` — `backend/app/db/models/generation.py`
- `generation_runs(id, study_id, target_doc_type, section_key, template_id, contract_id, input_snapshot_json, model_config_id, status, created_by, created_at)` — `backend/app/db/models/generation.py`
- `generated_sections(id, generation_run_id, content_text, artifacts_json, qc_status, qc_report_json, published_to_document_version_id, created_at)` — `backend/app/db/models/generation.py`
- `conflicts(id, study_id, conflict_type, severity, status, title, description, owner_user_id, created_at, updated_at)` — `backend/app/db/models/conflicts.py`
- `conflict_items(id, conflict_id, left_anchor_id, right_anchor_id, left_fact_id, right_fact_id, evidence_json, created_at)` — `backend/app/db/models/conflicts.py`
- `change_events(id, study_id, source_document_id, from_version_id, to_version_id, diff_summary_json, created_at)` — `backend/app/db/models/change.py`
- `impact_items(id, change_event_id, affected_doc_type, affected_section_key, reason_json, recommended_action, status, created_at)` — `backend/app/db/models/change.py`
- `tasks(id, study_id, type, status, assigned_to, payload_json, created_at, updated_at)` — `backend/app/db/models/change.py`
- `audit_log(id, workspace_id, actor_user_id, action, entity_type, entity_id, before_json, after_json, created_at)` — `backend/app/db/models/audit.py`

### 4) Где реализован ingest → anchors и как формируется `anchor_id`

**Оркестрация ingest по HTTP:**
- `POST /api/document-versions/{version_id}/ingest` переводит статус, вызывает `run_ingestion_now(...)`, пишет `ingestion_summary_json` и финальный `ingestion_status`  
  - Файл: `backend/app/api/v1/documents.py`

**Пайплайн ingest (DOCX):**
- `IngestionService.ingest(doc_version_id)`:
  - удаляет старые `anchors` и `facts` от этой версии,
  - парсит DOCX через `DocxIngestor.ingest(...)` → создаёт `anchors` (bulk insert),
  - запускает `SoAExtractionService.extract_soa(...)` → создаёт cell anchors + факты `soa.*`,
  - запускает `SectionMappingService.map_sections(...)` → создаёт/обновляет `section_maps`,
  - собирает warnings/needs_review и summary для `ingestion_summary_json` (стабильная схема ключей + всегда заполняется даже при ошибках).  
  - Файл: `backend/app/services/ingestion/__init__.py`

**Формирование `anchor_id` (реальная реализация в DOCX ingestion):**
- Формат: `{doc_version_id}:{section_path}:{content_type}:{ordinal}:{sha256(text_norm)}`  
  - Файлы:
    - правило/описание: `backend/DATABASE_SETUP.md`, `docs/ARCHITECTURE.md`, `backend/app/db/models/anchors.py`
    - генерация: `backend/app/services/ingestion/docx_ingestor.py` (нормализация текста, `sha256`, сбор `section_path`, `ordinal`, конкатенация в строку)

### 5) Где реализованы `section_contracts` и seed, что сейчас падает и почему

**`section_contracts` (таблица + API):**
- ORM: `backend/app/db/models/sections.py` (`SectionContract`)
- GET list: `backend/app/api/v1/sections.py` (`GET /api/section-contracts`)
- POST create: `backend/app/api/v1/sections.py` (`POST /api/section-contracts`)  
  - По умолчанию **запрещён** (403), если `settings.enable_contract_editing == False` — `backend/app/core/config.py`

**Seed контрактов:**
- “Хардкодный” seed примеров: `backend/app/scripts/seed.py` (создаёт workspace/user/study/template + набор `SectionContract`)
- Основной сидер из репозитория (contracts/seed/*.json): `backend/app/scripts/seed_section_contracts.py`
- Миграция старых контрактов в multilang v2: `backend/app/scripts/seed_contracts_multilang.py`
- Данные паспортов: `contracts/seed/*.json`

**Что сейчас падает и почему (по smoke-check Step 6.5):**
- Скрипт: `scripts/check_step6_5_llm_assist.py`
- Типовые причины фейла:
  - `ensure_section_contracts(...)` пытается создать отсутствующие контракты через `POST /api/section-contracts`, но API отдаёт **403**, потому что `enable_contract_editing=false` (MVP защита).  
    - Файлы: `backend/app/api/v1/sections.py`, `backend/app/core/config.py`, `scripts/check_step6_5_llm_assist.py`
  - `assist` ожидает `secure_mode=true` и `llm_used=true`, но по умолчанию `secure_mode=false` и/или не заданы `LLM_PROVIDER/LLM_BASE_URL/LLM_API_KEY` → API вернёт 403/400/502, а smoke-check упадёт на assert.  
    - Файлы: `backend/app/api/v1/sections.py`, `backend/app/services/section_mapping_assist.py`, `backend/app/core/config.py`, `scripts/check_step6_5_llm_assist.py`

### 6) Что сделано по section mapping / SoA extraction / facts / generation / QC

**Section mapping (deterministic):**
- Реализован автоподбор заголовков и захват “heading block” по `retrieval_recipe_json` (v1 и v2 с RU/EN), создание/обновление `section_maps`, статус/уверенность, уважение `overridden`.  
  - Файлы: `backend/app/services/section_mapping.py`, `backend/app/api/v1/sections.py`

**LLM-assisted section mapping + QC gate:**
- Реализован LLM вызов кандидатов заголовков + детерминированный QC gate (must/not keywords, regex, min block size, пересечения с другими маппингами, derived_confidence). Опционально применяет в БД (`apply=true`) и не трогает `overridden`.  
  - Gating: `SECURE_MODE=true` и наличие ключей.  
  - Файлы: `backend/app/services/section_mapping_assist.py`, `backend/app/services/section_mapping_qc.py`, `backend/app/services/llm_client.py`, `backend/app/api/v1/sections.py`, `backend/app/core/config.py`

**SoA extraction (DOCX):**
- Реализован детектор таблицы SoA по скорингу (keywords + структура + маркеры X/✓ + штрафы), извлечение visits/procedures/matrix, создание cell anchors + запись в facts (`fact_type="soa"`, keys: `visits|procedures|matrix`) + evidence.  
  - Файлы: `backend/app/services/soa_extraction.py`, использование: `backend/app/services/ingestion/__init__.py`, чтение API: `backend/app/api/v1/documents.py`

### 8) Закрытие MVP шагов 1–6 (sweep)

- **Step 1/2 (модели/миграции/enums)**: Alembic `env.py` импортирует `app.db.models`, enum `anchor_content_type` включает `cell`, `IngestionStatus` соответствует state machine.
- **Step 3 (lifecycle + summary)**: `ingestion_summary_json` теперь всегда заполняется и имеет стабильную схему ключей `{anchors_created, soa_found, soa_facts_written, chunks_created, mapping_status, warnings, errors}` (доп. поля могут присутствовать).
- **Step 4 (DOCX anchors)**: anchors создаются для `hdr/p/li`, добавлена попытка извлечения `fn` (с graceful skip + warning при недоступности).
- **Step 5 (SoA + evidence)**: cell anchors включают `location_json.table_id/row_idx/col_idx/header_path`, расширены RU/EN ключевые слова детектора; тесты проверяют, что evidence ссылается на реальные `cell` anchors.
- **Step 5.5 (Rules-first fact extraction)**: реализовано извлечение фактов `protocol_version`, `amendment_date`, `planned_n_total` с поддержкой RU/EN паттернов; интегрировано в ingest пайплайн; evidence идемпотентно заменяется при повторных прогонах.
- **Миграция 0004 (document_language)**: добавлен enum `document_language` (`ru|en|mixed|unknown`) и поле в `document_versions`; автодетект языка при upload DOCX (если `document_language=UNKNOWN`).
- **Миграция 0005 (unique fact_evidence)**: добавлен уникальный индекс `(fact_id, anchor_id, evidence_role)` для предотвращения дубликатов evidence при повторных прогонах.

**Facts (кроме SoA):**
- `FactExtractionService.extract_and_upsert(...)` реализован как **rules-first** извлечение (без LLM):
  - Извлекает 3 типа фактов: `protocol_meta/protocol_version`, `protocol_meta/amendment_date`, `population/planned_n_total`
  - Поддерживает RU/EN паттерны (regex-based)
  - Создаёт факты со статусом `extracted` или `needs_review` (если не найдено)
  - Evidence ссылается только на реальные `anchor_id` (не фиктивные)
  - Идемпотентность: при повторном прогоне заменяет evidence для существующих фактов
  - Интегрирован в ingest пайплайн (вызывается после SoA extraction, перед section mapping)
  - Файлы: `backend/app/services/fact_extraction.py`, `backend/app/services/ingestion/__init__.py` (строка 336-345)

**Generation:**
- `GenerationService.generate_section(...)` реализован как MVP-каркас:
  - создаёт `GenerationRun`,
  - строит контекст детерминированно из `section_maps` (через `LeanContextBuilder`),
  - формирует черновой `content_text` (без LLM),
  - формирует `artifacts` (claims + citations),
  - вызывает `ValidationService` (QC),
  - пишет `GeneratedSection`.  
  - Файлы: `backend/app/services/generation.py`, `backend/app/services/lean_passport.py`, `backend/app/api/v1/generation.py`

**QC (generation):**
- Реализованы базовые проверки:
  - `missing_anchor_ids` (все цитируемые anchor_id должны существовать),
  - `anchor_outside_allowed_sources` (цитации должны быть из разрешённых источников по паспорту),
  - `per_claim` policy (каждый claim должен иметь ≥1 anchor_id),
  - `secure_mode_required` → BLOCKED если secure_mode выключен.  
  - Файлы: `backend/app/services/generation.py`, `backend/app/services/lean_passport.py`

**Retrieval (pgvector):**
- `RetrievalService.retrieve(...)` сейчас **заглушка** (возвращает `[]`), несмотря на наличие `chunks.embedding vector(1536)` и индексов.  
  - Файлы: `backend/app/services/retrieval.py`, индексы: `backend/alembic/versions/0002_enums_and_vector.py`

**Impact (change management):**
- `ImpactService.compute_impact(...)` сейчас **заглушка** (возвращает `[]`), несмотря на наличие таблиц `change_events` и `impact_items`.  
  - Файлы: `backend/app/services/impact.py`

### 7) TODO (ближайшие 3 шага)

1) **Починить Step 6.5 smoke-check без ослабления MVP-ограничений**:
   - Вариант A: перед запуском smoke-check поднимать сидер `seed_section_contracts.py` (и убрать POST в smoke-check), либо добавить отдельный "test-only" эндпоинт/флаг для создания контрактов в dev.  
   - Файлы: `scripts/check_step6_5_llm_assist.py`, `backend/app/scripts/seed_section_contracts.py`, `backend/app/core/config.py`, `backend/app/api/v1/sections.py`
2) **Включить/настроить LLM assist и стабилизировать контрактные ожидания**:
   - Настроить `SECURE_MODE=true`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`; убедиться, что `SectionContract.retrieval_recipe_json` v2 соответствует документному языку (RU/EN/MIXED).  
   - Файлы: `backend/app/core/config.py`, `backend/app/services/section_mapping_assist.py`, `backend/app/services/llm_client.py`, `contracts/seed/*.json`
3) **Довести retrieval/impact до рабочего контура**:
   - Реализовать `RetrievalService` (embed query + pgvector search), затем переключить generation на retrieval вместо чистого `section_maps`/anchors.  
   - Реализовать `ImpactService.compute_impact(...)` для вычисления воздействия изменений документов на основе diff и dependency graph.  
   - Расширить `FactExtractionService` для извлечения дополнительных фактов по контрактам (сейчас только 3 базовых: protocol_version, amendment_date, planned_n_total).  
   - Файлы: `backend/app/services/retrieval.py`, `backend/app/services/impact.py`, `backend/app/services/generation.py`, `backend/app/services/fact_extraction.py`, `backend/app/services/lean_passport.py`


