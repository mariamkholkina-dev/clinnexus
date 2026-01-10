## STATUS REPORT

**Последнее обновление:** 2026-01-15 (обновлена информация об экспорте DOCX, audit issues и внутридокументных аудиторах)

### 1) Стек и запуск (команды)

- **Backend**: Python 3.12, FastAPI, Uvicorn, SQLAlchemy (async), Alembic, PostgreSQL + pgvector, python-docx, httpx  
  - Файлы: `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/core/config.py`
- **DB**: PostgreSQL (в docker-compose используется `pgvector/pgvector:0.8.1-pg18-trixie`)  
  - Файлы: `docker-compose.yml`, `backend/DATABASE_SETUP.md`
- **Frontend**: Next.js (app router), TypeScript  
  - Файлы: `frontend/package.json`, `frontend/app/*`

**Запуск dev через Docker:**
- `docker-compose up --build` (поднимает db/backend/frontend) — `Makefile`, `docker-compose.yml`
- Миграции: `docker-compose run --rm backend alembic -c /app/alembic.ini upgrade head` — `Makefile`
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
- `POST /api/document-versions/{version_id}/upload` — загрузка файла (валидирует расширение `.docx|.xlsx`, проверяет non-empty, пишет в storage, сохраняет sha256)  
- `POST /api/document-versions/{version_id}/ingest?force=...` — запуск ингестии (uploaded→processing→ready|needs_review|failed; guard rails)  
- `GET /api/document-versions/{version_id}` — получить версию  
- `GET /api/document-versions/{version_id}/anchors?section_path=...&content_type=...` — якоря  
- `GET /api/document-versions/{version_id}/soa` — извлечённый SoA из facts типа `soa`  
  - Файл: `backend/app/api/v1/documents.py`

**Section contracts / section maps (по коду):**
- `GET /api/section-contracts?doc_type=...&is_active=...` — список контрактов  
- `POST /api/section-contracts` — создание контракта (в MVP запрещено по умолчанию: `ENABLE_CONTRACT_EDITING=false`)  
- `GET /api/document-versions/{version_id}/section-maps` — список маппингов  
- `POST /api/document-versions/{version_id}/section-maps/{target_section}/override` — override пользователем (target_section вместо section_key)  
- `POST /api/document-versions/{version_id}/section-maps/rebuild?force=...` — пересборка system mappings  
- `POST /api/document-versions/{version_id}/section-maps/assist` — LLM-assisted mapping (gated secure_mode + keys)  
  - Файл: `backend/app/api/v1/sections.py`

**Passport tuning / Topics (по коду):**
- `GET /api/passport-tuning/clusters` — список кластеров заголовков (пагинация, поиск)  
- `GET /api/passport-tuning/mapping` — текущий маппинг cluster_id -> target_section  
- `POST /api/passport-tuning/mapping` — сохранение маппинга (с нормализацией через taxonomy)  
- `GET /api/passport-tuning/mapping/download` — скачать маппинг как JSON  
- `GET /api/passport-tuning/mapping/for_autotune` — маппинг для автотюнинга (исключает ambiguous/skip)  
- `GET /api/passport-tuning/sections?doc_type=...` — дерево taxonomy для doc_type  
- `POST /api/passport-tuning/cluster-to-topic-mapping` — загрузка маппинга cluster_id -> topic_key для doc_version  
  - Файл: `backend/app/api/v1/passport_tuning.py`

**Topics API (по коду):**
- `GET /api/topics?workspace_id=...&is_active=...` — список топиков (по умолчанию только активные)  
- `GET /api/topics/{topic_key}?workspace_id=...&include_profile=...` — детальная информация о топике  
- `GET /api/cluster-assignments?doc_version_id=...` — список привязок кластеров к топикам для версии документа  
- `GET /api/cluster-assignments/{cluster_id}?doc_version_id=...` — привязка кластера к топику по doc_version_id и cluster_id  
  - Файл: `backend/app/api/v1/topics.py`

**Generation / Conflicts / Impact / Tasks / Export (по коду):**
- `POST /api/generate/section` — генерация секции (MVP: детерминированный черновик + QC)  
  - Файл: `backend/app/api/v1/generation.py`
- `GET /api/conflicts?study_id=...` — детект конфликтов (сервисный слой)  
  - Файл: `backend/app/api/v1/conflicts.py`
- `GET /api/impact?study_id=...&change_event_id=...` — если есть `change_event_id`, считает impact; иначе TODO (возвращает `[]`)  
- `GET /api/tasks?study_id=...` — список tasks из БД  
  - Файл: `backend/app/api/v1/impact.py`
- `GET /api/document-versions/{version_id}/download` — скачивание собранного документа в формате DOCX (собирает все опубликованные секции в единый файл)  
  - Файл: `backend/app/api/v1/export.py`, `backend/app/services/export/docx_assembler.py`

### 3) Таблицы в БД и ключевые поля (особенно enums `document_type` и `citation_policy`)

**Основные ENUM (PostgreSQL native enum + Python Enum):**
- `document_type`: `protocol|sap|tfl|csr|ib|icf|other`  
  - Файлы: `backend/app/db/enums.py` (`DocumentType`), `backend/alembic/versions/0001_initial_prod_skeleton.py`
- `citation_policy`: `per_sentence|per_claim|none`  
  - Файлы: `backend/app/db/enums.py` (`CitationPolicy`), `backend/alembic/versions/0001_initial_prod_skeleton.py`, ORM: `backend/app/db/models/sections.py`
- `document_language`: `ru|en|mixed|unknown`  
  - Файлы: `backend/app/db/enums.py` (`DocumentLanguage`), `backend/alembic/versions/0004_add_document_language.py`, ORM: `backend/app/db/models/studies.py` (поле `document_versions.document_language`)
- `fact_scope`: `global|arm|group|visit`  
  - Файлы: `backend/app/db/enums.py` (`FactScope`), `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`, ORM: `backend/app/db/models/facts.py` (поле `facts.scope`)
- `audit_severity`: `critical|major|minor`  
  - Файлы: `backend/app/db/enums.py` (`AuditSeverity`), `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`, ORM: `backend/app/db/models/audit.py` (поле `audit_issues.severity`)
- `audit_category`: `consistency|grammar|logic|terminology|compliance`  
  - Файлы: `backend/app/db/enums.py` (`AuditCategory`), `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`, ORM: `backend/app/db/models/audit.py` (поле `audit_issues.category`)
- `audit_status`: `open|suppressed|resolved`  
  - Файлы: `backend/app/db/enums.py` (`AuditStatus`), `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`, ORM: `backend/app/db/models/audit.py` (поле `audit_issues.status`)

**Таблицы (по ORM; полный DDL — миграция 0001):**
- `workspaces(id, name, created_at)` — `backend/app/db/models/auth.py`
- `users(id, email, name, is_active, created_at)` — `backend/app/db/models/auth.py`
- `memberships(id, workspace_id, user_id, role, created_at)` + uq(workspace_id,user_id) — `backend/app/db/models/auth.py`
- `studies(id, workspace_id, study_code, title, status, created_at)` + uq(workspace_id,study_code) — `backend/app/db/models/studies.py`
- `documents(id, workspace_id, study_id, doc_type, title, lifecycle_status, created_at)` — `backend/app/db/models/studies.py`
- `document_versions(id, document_id, version_label, source_file_uri, source_sha256, effective_date, ingestion_status, ingestion_summary_json, document_language, last_ingestion_run_id, created_by, created_at)` — `backend/app/db/models/studies.py`
  - `last_ingestion_run_id` — ссылка на последний запуск ингестии (миграция 0013)
- `anchors(id, doc_version_id, anchor_id[unique], section_path, content_type, ordinal, text_raw, text_norm, text_hash, location_json, source_zone, language, confidence, created_at)` — `backend/app/db/models/anchors.py`
  - `source_zone`: ENUM с 12 каноническими ключами + "unknown" (миграция 0012)
  - `language`: enum (ru/en/mixed/unknown) (миграция 0007)
  - Индексы: `ix_anchors_doc_version_source_zone` (миграция 0012)
- `chunks(id, doc_version_id, chunk_id[unique], section_path, text, anchor_ids[], embedding vector(1536), metadata_json, source_zone, language, created_at)` — `backend/app/db/models/anchors.py`
  - `source_zone`: ENUM с 12 каноническими ключами + "unknown", наследуется от anchors (most_common) (миграция 0012)
  - `language`: enum (ru/en/mixed/unknown), наследуется от anchors (миграция 0007)
  - Индексы: `ix_chunks_doc_version_source_zone` (миграция 0012), pgvector: `backend/alembic/versions/0002_enums_and_vector.py`
- `target_section_contracts(id, workspace_id, doc_type, target_section, view_key, title, required_facts_json, allowed_sources_json, retrieval_recipe_json, qc_ruleset_json, citation_policy, version, is_active, created_at)` + uq(workspace_id,doc_type,target_section,version) — `backend/app/db/models/sections.py` (переименовано из `section_contracts` в миграции 0017)
  - `target_section`: один из 12 канонических ключей (валидация в моделях и схемах, миграция 0012)
  - `retrieval_recipe_json.prefer_source_zones`: приоритетные source_zone для retrieval (автоматически заполняются из правил)
  - `retrieval_recipe_json.fallback_source_zones`: резервные source_zone
- `target_section_maps(id, doc_version_id, target_section, anchor_ids[], chunk_ids[], confidence, status, mapped_by, notes, created_at)` — `backend/app/db/models/sections.py` (переименовано из `section_maps` в миграции 0017, section_key переименован в target_section в миграции 0007)
- `facts(id, study_id, fact_type, fact_key, value_json, unit, status, scope, type_category, created_from_doc_version_id, created_at, updated_at)` + uq(study_id,fact_type,fact_key) — `backend/app/db/models/facts.py`
  - `scope`: ENUM fact_scope (global/arm/group/visit) с дефолтным значением 'global' (миграция 0022)
  - `type_category`: String(128) для категоризации типа факта (миграция 0022)
  - Индексы: `ix_facts_scope`, `ix_facts_type_category` (миграция 0022)
- `fact_evidence(id, fact_id, anchor_id, evidence_role, created_at)` + uq(fact_id,anchor_id,evidence_role) — `backend/app/db/models/facts.py`, уникальный индекс: `backend/alembic/versions/0005_unique_fact_evidence.py`
- `templates(id, workspace_id, doc_type, name, template_body, version, created_at)` — `backend/app/db/models/generation.py`
- `model_configs(id, provider, model_name, prompt_version, params_json, created_at)` — `backend/app/db/models/generation.py`
- `generation_runs(id, study_id, target_doc_type, target_section, view_key, template_id, contract_id, input_snapshot_json, model_config_id, status, created_by, created_at)` — `backend/app/db/models/generation.py` (section_key переименован в target_section, добавлен view_key)
- `generated_target_sections(id, generation_run_id, content_text, artifacts_json, qc_status, qc_report_json, published_to_document_version_id, created_at)` — `backend/app/db/models/generation.py` (переименовано из `generated_sections` в миграции 0017)
- `conflicts(id, study_id, conflict_type, severity, status, title, description, owner_user_id, created_at, updated_at)` — `backend/app/db/models/conflicts.py`
- `conflict_items(id, conflict_id, left_anchor_id, right_anchor_id, left_fact_id, right_fact_id, evidence_json, created_at)` — `backend/app/db/models/conflicts.py`
- `change_events(id, study_id, source_document_id, from_version_id, to_version_id, diff_summary_json, created_at)` — `backend/app/db/models/change.py`
- `impact_items(id, change_event_id, affected_doc_type, affected_target_section, reason_json, recommended_action, status, created_at)` — `backend/app/db/models/change.py` (affected_section_key переименован в affected_target_section)
- `tasks(id, study_id, type, status, assigned_to, payload_json, created_at, updated_at)` — `backend/app/db/models/change.py`
- `audit_log(id, workspace_id, actor_user_id, action, entity_type, entity_id, before_json, after_json, created_at)` — `backend/app/db/models/audit.py`
- `anchor_matches(id, document_id, from_doc_version_id, to_doc_version_id, from_anchor_id, to_anchor_id, score, method, meta_json, created_at)` + uq(from_doc_version_id,to_doc_version_id,from_anchor_id) — `backend/app/db/models/anchor_matches.py`, миграция: `backend/alembic/versions/0009_add_anchor_matches.py`
- `topics(id, workspace_id, topic_key, title_ru, title_en, description, topic_profile_json, is_active, topic_embedding, applicable_to_json, created_at)` + uq(workspace_id,topic_key) — `backend/app/db/models/topics.py`, миграции: `backend/alembic/versions/0008_add_topics_tables.py`, `0014_extend_topics_production_quality.py`, `0018_add_topic_doc_type_profiles_and_priors.py`
  - `topic_profile_json` — профиль топика с aliases, keywords, source_zones, dissimilar_zones, embeddings (миграция 0014)
  - `is_active` — активность топика (миграция 0014)
  - `topic_embedding` — векторное представление топика VECTOR(1536) (миграция 0014)
  - `applicable_to_json` — список doc_type, к которым применим топик (миграция 0018)
- `cluster_assignments(id, doc_version_id, cluster_id, topic_key, mapped_by, confidence, notes, mapping_debug_json, created_at)` + uq(doc_version_id,cluster_id) — `backend/app/db/models/topics.py`, миграции: `backend/alembic/versions/0008_add_topics_tables.py`, `0015_add_mapping_debug_json_to_cluster_assignments.py`
  - `mapping_debug_json` — debug-информация о маппинге кластера на топик (миграция 0015)
- `topic_evidence(id, doc_version_id, topic_key, source_zone, language, anchor_ids[], chunk_ids[], score, evidence_json, created_at)` + uq(doc_version_id,topic_key,source_zone,language) — `backend/app/db/models/topics.py`, миграция: `backend/alembic/versions/0008_add_topics_tables.py`
- `study_core_facts(id, study_id, doc_version_id, facts_json, facts_version, derived_from_doc_version_id, created_at)` — `backend/app/db/models/core_facts.py`, миграция: `backend/alembic/versions/0010_add_study_core_facts.py`
- `ingestion_runs(id, doc_version_id, status, started_at, finished_at, duration_ms, pipeline_version, pipeline_config_hash, summary_json, quality_json, warnings_json, errors_json)` — `backend/app/db/models/ingestion.py`, миграция: `backend/alembic/versions/0013_add_ingestion_runs.py`
- `heading_clusters(id, doc_version_id, cluster_id, language, top_titles_json, examples_json, stats_json, cluster_embedding, created_at)` + uq(doc_version_id,cluster_id,language) — `backend/app/db/models/topics.py`, миграция: `backend/alembic/versions/0014_extend_topics_production_quality.py`
- `topic_mapping_runs(id, doc_version_id, mode, params_json, metrics_json, pipeline_version, pipeline_config_hash, created_at)` — `backend/app/db/models/topics.py`, миграции: `backend/alembic/versions/0014_extend_topics_production_quality.py`, `0016_add_topic_indexes_and_constraints.py`
- `topic_zone_priors(id, topic_id, doc_type, zone_key, prior_weight, created_at)` + uq(topic_id,doc_type,zone_key) — `backend/app/db/models/topics.py`, миграция: `backend/alembic/versions/0018_add_topic_doc_type_profiles_and_priors.py`
- `zone_sets(id, doc_type, zone_key, is_active, created_at)` + uq(doc_type,zone_key) — `backend/app/db/models/zones.py`, миграция: `backend/alembic/versions/0019_add_zone_sets_and_crosswalk.py`
- `zone_crosswalk(id, from_doc_type, from_zone_key, to_doc_type, to_zone_key, weight, notes, is_active, created_at)` + uq(from_doc_type,from_zone_key,to_doc_type,to_zone_key) — `backend/app/db/models/zones.py`, миграция: `backend/alembic/versions/0019_add_zone_sets_and_crosswalk.py`
  - `weight` — вес маппинга (Numeric(3,2)) для ранжирования при cross-doc retrieval
  - `notes` — опциональные заметки о маппинге
  - `is_active` — флаг активности маппинга
- `heading_block_topic_assignments(id, doc_version_id, heading_block_id, topic_key, confidence, debug_json, created_at)` + uq(doc_version_id,heading_block_id) — `backend/app/db/models/topics.py`, миграция: `backend/alembic/versions/0021_add_heading_block_topic_assignments.py`
  - Прямой маппинг блоков заголовков на топики для doc_version. Блоки строятся динамически из anchors через `HeadingBlockBuilder`, `heading_block_id` — стабильный идентификатор блока.
- `audit_issues(id, study_id, doc_version_id, severity, category, description, location_anchors, status, suppression_reason, suggested_fix, created_at, updated_at)` — `backend/app/db/models/audit.py`, миграции: `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`, `0023_add_suggested_fix_to_audit_issues.py`
  - Таблица для хранения аудиторских находок (внутридокументных и кросс-документных)
  - `severity`: ENUM audit_severity (critical/major/minor)
  - `category`: ENUM audit_category (consistency/grammar/logic/terminology/compliance)
  - `status`: ENUM audit_status (open/suppressed/resolved)
  - `suggested_fix`: Text — предлагаемое исправление (миграция 0023)
  - Индексы: `ix_audit_issues_study_id`, `ix_audit_issues_doc_version_id`, `ix_audit_issues_severity`, `ix_audit_issues_category`, `ix_audit_issues_status`, `ix_audit_issues_study_status`
- `terminology_dictionaries(id, study_id, term_category, preferred_term, variations, created_at, updated_at)` + uq(study_id,term_category,preferred_term) — `backend/app/db/models/audit.py`, миграция: `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`
  - Таблица для хранения терминологических словарей исследования (USR-302, USR-307)
  - `variations`: JSONB — список вариантов написания термина
  - Индексы: `ix_terminology_dictionaries_study_id`, `ix_terminology_dictionaries_term_category`

### 4) Где реализован ingest → anchors и как формируется `anchor_id`

**Оркестрация ingest по HTTP:**
- `POST /api/document-versions/{version_id}/ingest` переводит статус, вызывает `run_ingestion_now(...)`, пишет `ingestion_summary_json` и финальный `ingestion_status`  
  - Файл: `backend/app/api/v1/documents.py`

**Пайплайн ingest (DOCX):**
- `IngestionService.ingest(doc_version_id)`:
  - удаляет старые `anchors` и `facts` от этой версии,
  - парсит DOCX через `DocxIngestor.ingest(...)` → создаёт `anchors` (bulk insert),
  - запускает `SoAExtractionService.extract_soa(...)` → создаёт cell anchors + факты `soa.*`,
  - запускает `SectionMappingService.map_sections(...)` → создаёт/обновляет `target_section_maps`,
  - собирает warnings/needs_review и summary для `ingestion_summary_json` (стабильная схема ключей + всегда заполняется даже при ошибках).  
  - Файл: `backend/app/services/ingestion/__init__.py`

**Формирование `anchor_id` (реальная реализация в DOCX ingestion):**
- Формат: `{doc_version_id}:{section_path}:{content_type}:{ordinal}:{sha256(text_norm)}`  
  - Файлы:
    - правило/описание: `backend/DATABASE_SETUP.md`, `docs/ARCHITECTURE.md`, `backend/app/db/models/anchors.py`
    - генерация: `backend/app/services/ingestion/docx_ingestor.py` (нормализация текста, `sha256`, сбор `section_path`, `ordinal`, конкатенация в строку)

### 5) Где реализованы `target_section_contracts` и seed, что сейчас падает и почему

**`target_section_contracts` (таблица + API, переименована из `section_contracts` в миграции 0017):**
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

**Section Taxonomy:**
- Таблицы taxonomy удалены (миграция 0020). Структура документов определяется через templates и target_section_contracts.

**SoA extraction (DOCX):**
- Реализован детектор таблицы SoA по скорингу (keywords + структура + маркеры X/✓ + штрафы), извлечение visits/procedures/matrix, создание cell anchors + запись в facts (`fact_type="soa"`, keys: `visits|procedures|matrix`) + evidence.  
  - Файлы: `backend/app/services/soa_extraction.py`, использование: `backend/app/services/ingestion/__init__.py`, чтение API: `backend/app/api/v1/documents.py`

### 9) Закрытие MVP шагов 1–6 (sweep)

- **Step 1/2 (модели/миграции/enums)**: Alembic `env.py` импортирует `app.db.models`, enum `anchor_content_type` включает `cell`, `IngestionStatus` соответствует state machine.
- **Step 3 (lifecycle + summary)**: `ingestion_summary_json` теперь всегда заполняется и имеет стабильную схему ключей `{anchors_created, soa_found, soa_facts_written, chunks_created, mapping_status, warnings, errors}` (доп. поля могут присутствовать).
- **Step 4 (DOCX anchors)**: anchors создаются для `hdr/p/li`, добавлена попытка извлечения `fn` (с graceful skip + warning при недоступности).
- **Step 5 (SoA + evidence)**: cell anchors включают `location_json.table_id/row_idx/col_idx/header_path`, расширены RU/EN ключевые слова детектора; тесты проверяют, что evidence ссылается на реальные `cell` anchors.
- **Step 5.5 (Rules-first fact extraction)**: реализовано извлечение фактов `protocol_version`, `amendment_date`, `planned_n_total` с поддержкой RU/EN паттернов; интегрировано в ingest пайплайн; evidence идемпотентно заменяется при повторных прогонах.
- **Миграция 0003 (nullable file fields)**: поля `source_file_uri` и `source_sha256` в `document_versions` теперь nullable (версия может быть создана до загрузки файла).
- **Миграция 0004 (document_language)**: добавлен enum `document_language` (`ru|en|mixed|unknown`) и поле в `document_versions`; автодетект языка при upload DOCX (если `document_language=UNKNOWN`).
- **Миграция 0005 (unique fact_evidence)**: добавлен уникальный индекс `(fact_id, anchor_id, evidence_role)` для предотвращения дубликатов evidence при повторных прогонах.
- **Миграция 0006 (section taxonomy)**: добавлены таблицы для иерархии секций (удалены в миграции 0020).
- **Миграция 0007 (rename section_key → target_section)**: переименование `section_key` в `target_section` во всех таблицах:
  - `target_section_contracts` (переименовано из `section_contracts`), `target_section_maps` (переименовано из `section_maps`), `generation_runs`, `impact_items`
  - Добавлено поле `view_key` в `target_section_contracts` и `generation_runs`
  - Добавлены поля `source_zone` (TEXT) и `language` в `anchors` и `chunks` с индексами (обновлено на ENUM в миграции 0012)
- **Миграция 0011 (fact metadata)**: добавлены поля метаданных в таблицу `facts`:
  - `confidence` (float) — уверенность в извлеченном факте
  - `extractor_version` (int) — версия экстрактора
  - `meta_json` (jsonb) — дополнительные метаданные
- **Миграция 0012 (стандартизация 12 основных секций)**: 
  - Создание ENUM `source_zone` с 12 каноническими ключами: `overview`, `design`, `ip`, `statistics`, `safety`, `endpoints`, `population`, `procedures`, `data_management`, `ethics`, `admin`, `appendix` + `unknown`
  - Обновление `anchors.source_zone` и `chunks.source_zone` на ENUM
  - Маппинг старых значений на канонические (например, `randomization` → `design`, `adverse_events` → `safety`)
  - Добавление индексов `(doc_version_id, source_zone)` для быстрого поиска
  - Валидация `target_section` на 12 канонических ключей в моделях и схемах
  - Правила `prefer_source_zones` для каждой `target_section` в `target_section_contracts.retrieval_recipe_json`
  - Обновление `SourceZoneClassifier` для работы с новыми зонами и поддержки `heading_text` и `language`
- **Миграция 0008 (topics tables)**: добавлены таблицы для работы с топиками:
  - `topics`: топики с workspace_id, topic_key, title_ru/en, description
  - `cluster_assignments`: привязка кластеров к топикам для doc_version
  - `topic_evidence`: агрегированные доказательства для топиков с anchor_ids, chunk_ids, source_zone, language
- **Миграция 0009 (anchor_matches)**: добавлена таблица `anchor_matches` для выравнивания якорей между версиями документов
- **Миграция 0010 (study_core_facts)**: добавлена таблица `study_core_facts` для структурированных основных фактов исследования
- **Миграция 0011 (fact metadata)**: добавлены поля метаданных в таблицу `facts`:
  - `confidence` (float) — уверенность в извлеченном факте
  - `extractor_version` (int) — версия экстрактора
  - `meta_json` (jsonb) — дополнительные метаданные
- **Миграция 0012 (стандартизация 12 основных секций)**: 
  - Создание ENUM `source_zone` с 12 каноническими ключами: `overview`, `design`, `ip`, `statistics`, `safety`, `endpoints`, `population`, `procedures`, `data_management`, `ethics`, `admin`, `appendix` + `unknown`
  - Обновление `anchors.source_zone` и `chunks.source_zone` на ENUM
  - Маппинг старых значений на канонические (например, `randomization` → `design`, `adverse_events` → `safety`)
  - Добавление индексов `(doc_version_id, source_zone)` для быстрого поиска
  - Валидация `target_section` на 12 канонических ключей в моделях и схемах
  - Правила `prefer_source_zones` для каждой `target_section` в `target_section_contracts.retrieval_recipe_json`
  - Обновление `SourceZoneClassifier` для работы с новыми зонами и поддержки `heading_text` и `language`
- **Миграция 0013 (ingestion_runs)**: добавлена таблица `ingestion_runs` для отслеживания запусков ингестии с метриками, качеством и предупреждениями; добавлено поле `last_ingestion_run_id` в `document_versions`
- **Миграция 0014 (topics production quality)**: расширение поддержки topics:
  - В `topics`: `topic_profile_json`, `is_active`, `topic_embedding` (VECTOR(1536))
  - Таблица `heading_clusters` для хранения кластеров заголовков
  - Таблица `topic_mapping_runs` для отслеживания запусков маппинга топиков
- **Миграция 0015 (mapping_debug_json)**: добавлено поле `mapping_debug_json` в `cluster_assignments` для хранения debug-информации о маппинге
- **Миграция 0016 (topic indexes)**: добавлены индексы и ограничения для topics и cluster_assignments, обновление `topic_mapping_runs` с `pipeline_version` и `pipeline_config_hash`
- **Миграция 0017 (rename section tables)**: переименование таблиц OUTPUT sections из `section_*` в `target_section_*`:
  - `section_contracts` → `target_section_contracts`
  - `section_maps` → `target_section_maps`
  - `generated_sections` → `generated_target_sections`
- **Миграция 0018 (topic doc_type profiles)**: добавлена поддержка doc_type профилей и zone priors:
  - В `topics`: `applicable_to_json` — список doc_type, к которым применим топик
  - Таблица `topic_zone_priors` для хранения приоритетов зон по doc_type для топиков
- **Миграция 0019 (zone sets and crosswalk)**: добавлены таблицы для кросс-документного связывания:
  - `zone_sets`: doc_type → список zone_key
  - `zone_crosswalk`: маппинг между зонами разных doc_types с весами
- **Миграция 0021 (heading_block_topic_assignments)**: добавлена таблица для прямого маппинга блоков заголовков на топики:
  - `heading_block_topic_assignments`: привязка `heading_block_id` к `topic_key` для doc_version
  - Блоки строятся динамически из anchors через `HeadingBlockBuilder`, `heading_block_id` — стабильный идентификатор блока
  - Используется `TopicMappingService` для создания маппингов и `TopicEvidenceBuilder` для построения доказательств
  - Уникальный индекс на `(doc_version_id, heading_block_id)` для предотвращения дубликатов
- **Миграция 0022 (USR-4.1 enums and tables)**: добавлены таблицы и ENUM типы для внутридокументного аудита:
  - Создание ENUM типов: `fact_scope` (global/arm/group/visit), `audit_severity` (critical/major/minor), `audit_category` (consistency/grammar/logic/terminology/compliance), `audit_status` (open/suppressed/resolved)
  - Обновление таблицы `facts`: добавлены поля `scope` (ENUM fact_scope с дефолтом 'global') и `type_category` (String(128)) с индексами
  - Создание таблицы `audit_issues` для хранения аудиторских находок (USR-401.1)
  - Создание таблицы `terminology_dictionaries` для хранения терминологических словарей исследования (USR-302, USR-307)
- **Миграция 0023 (suggested_fix to audit_issues)**: добавлено поле `suggested_fix` (Text, nullable) в таблицу `audit_issues` для хранения предлагаемых исправлений

**Facts (кроме SoA):**
- `FactExtractionService.extract_and_upsert(...)` реализован как **rules-first** извлечение:
  - Извлекает факты через regex-правила из `fact_extraction_rules.py` (поддержка RU/EN паттернов)
  - Поддерживает приоритеты правил, предпочтительные source_zones и топики
  - Создаёт факты со статусом `extracted`, `needs_review` или `validated` (после LLM-нормализации)
  - Поля `scope` (ENUM fact_scope: global/arm/group/visit) и `type_category` (String(128)) для категоризации фактов (миграция 0022)
  - Evidence ссылается только на реальные `anchor_id` (не фиктивные)
  - Идемпотентность: при повторном прогоне заменяет evidence для существующих фактов
  - Интегрирован в ingest пайплайн (вызывается после SoA extraction, перед section mapping)
  - **ValueNormalizer**: GxP-совместимый двойной контроль (Double Check) для сложных значений:
    - Автоматически определяет сложные значения (несколько чисел, длинные фразы, вложенные структуры)
    - Использует LLM для нормализации сложных значений (требует `secure_mode=true`)
    - Сравнивает результат LLM с regex-результатом
    - Если совпадают → статус `validated`, если нет → `conflicting`
  - Файлы: `backend/app/services/fact_extraction.py`, `backend/app/services/fact_extraction_rules.py`, `backend/app/services/value_normalizer.py`, `backend/app/services/ingestion/__init__.py`

**Generation:**
- `GenerationService.generate_section(...)` реализован как MVP-каркас:
  - создаёт `GenerationRun`,
  - строит контекст детерминированно из `target_section_maps` (через `LeanContextBuilder`),
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
- `RetrievalService.retrieve_with_zone_crosswalk(...)` реализован частично: использует `ZoneConfigService` для получения целевых зон через crosswalk, но векторный поиск не реализован (возвращает `[]`).
  - Файлы: `backend/app/services/retrieval.py`, `backend/app/services/zone_config.py`, индексы: `backend/alembic/versions/0002_enums_and_vector.py`

**Impact (change management):**
- `ImpactService.compute_impact(...)` **реализован**: вычисляет воздействие изменений документов на основе `anchor_matches`, находит затронутые `GeneratedTargetSection`, создаёт `ImpactItem` и системные задачи `Task` для пользователя.  
  - Использует `AnchorAligner` для определения измененных якорей (score < 0.95 считается изменением)  
  - Находит удаленные якоря (отсутствующие в `AnchorMatch`)  
  - Извлекает `anchor_id` из `artifacts_json` сгенерированных секций  
  - Создает `ImpactItem` для каждой затронутой секции с описанием изменений  
  - Автоматически создает `Task` типа `REVIEW_IMPACT` для пользователя  
  - Файлы: `backend/app/services/impact.py`, `backend/app/services/anchor_aligner.py`

### 7) Новые сервисы и компоненты

**Export / DocxAssembler:**
- Сервис для сборки документов из сгенерированных секций в формат DOCX
- Использует `python-docx` для создания DOCX файлов с сохранением структуры и форматирования
- Удаляет маркеры якорей из текста и формирует отображаемые имена секций
- Файлы: `backend/app/services/export/docx_assembler.py`, `backend/app/api/v1/export.py`
- Реализует: USR-309 (Выгрузка в DOCX)

**AuditService:**
- Сервис для оркестрации всех аудиторов (внутридокументных и кросс-документных)
- Внутридокументные аудиторы:
  - `ConsistencyAuditor`: проверка согласованности числовых значений (размер выборки, длительность исследования)
  - `AbbreviationAuditor`: проверка правильности использования аббревиатур
  - `VisitLogicAuditor`: проверка логики визитов и процедур
  - `PlaceholderAuditor`: обнаружение незаполненных плейсхолдеров
- Кросс-документные аудиторы:
  - `ProtocolIcfConsistencyAuditor`: проверка согласованности между Протоколом и ICF
  - `ProtocolCsrConsistencyAuditor`: проверка согласованности между Протоколом и CSR
- Сохраняет найденные проблемы в таблицу `audit_issues` с автоматическим логированием в `audit_log`
- Файлы: `backend/app/services/audit/service.py`, `backend/app/services/audit/intra/*`, `backend/app/services/audit/cross/*`
- Реализует: USR-401.1 (Поиск несоответствий внутри документа)

**TopicMappingService:**
- Сервис для маппинга блоков заголовков на топики для версии документа
- Использует `HeadingBlockBuilder` для построения блоков заголовков из anchors
- Создает `heading_block_topic_assignments` для прямого маппинга блоков на топики
- Файлы: `backend/app/services/topic_mapping.py`, `backend/app/services/heading_block_builder.py`

**TopicEvidenceBuilder:**
- Строит агрегированные доказательства для топиков из `heading_block_topic_assignments`
- Создает `topic_evidence` с anchor_ids, chunk_ids, source_zone, language
- Файлы: `backend/app/services/topic_evidence_builder.py`

**HeadingBlockBuilder:**
- Строит блоки заголовков динамически из anchors для doc_version
- Генерирует стабильный `heading_block_id` для каждого блока
- Используется для прямого маппинга блоков на топики (миграция 0021)
- Файлы: `backend/app/services/heading_block_builder.py`

**ValueNormalizer:**
- GxP-совместимый двойной контроль (Double Check) для извлеченных значений фактов
- Автоматически определяет сложные значения и использует LLM для нормализации
- Сравнивает результат LLM с regex-результатом для валидации
- Требует `secure_mode=true` и настроенный LLM клиент
- Файлы: `backend/app/services/value_normalizer.py`, `backend/app/services/llm_client.py`

**TopicRepository, HeadingClusterRepository и ClusterAssignmentRepository:**
- Репозитории для работы с топиками, кластерами заголовков и привязками кластеров
- Используются в Topics API для получения списков и детальной информации
- Файлы: `backend/app/services/topic_repository.py`

**ZoneSetRepository и ZoneCrosswalkRepository:**
- Репозитории для работы с наборами зон и кросс-документным маппингом зон
- Поддерживают массовые операции (bulk_upsert) для идемпотентной загрузки данных
- Файлы: `backend/app/services/zone_repository.py`

**ZoneConfigService:**
- Сервис для загрузки и работы с конфигурациями зон из YAML файлов
- Загружает zone_sets, zone_crosswalk и topic_zone_priors из `app/data/source_zones/`
- Валидирует зоны для doc_type, применяет приоритеты зон на основе topic_key
- Используется для cross-doc retrieval через `RetrievalService.retrieve_with_zone_crosswalk()`
- Файлы: `backend/app/services/zone_config.py`

### 8) TODO (ближайшие шаги)

1) **Настроить LLM assist и стабилизировать контрактные ожидания**:
   - **Текущий статус**: `SectionMappingAssistService` реализован, но требует настройки окружения. `enable_contract_editing=false` по умолчанию (MVP ограничение), контракты загружаются через сидер `seed_section_contracts.py`.
   - **Что нужно сделать**:
     - Настроить переменные окружения: `SECURE_MODE=true`, `LLM_PROVIDER` (openai/yandexgpt), `LLM_BASE_URL`, `LLM_API_KEY`.
     - Убедиться, что `TargetSectionContract.retrieval_recipe_json` v2 соответствует документному языку (RU/EN/MIXED) и использует правильные `prefer_source_zones`.
     - Для тестирования/разработки: рассмотреть возможность временного включения `ENABLE_CONTRACT_EDITING=true` или использования сидера перед запуском тестов.
   - **Файлы**: `backend/app/core/config.py` (enable_contract_editing, secure_mode, llm_*), `backend/app/services/section_mapping_assist.py` (проверка secure_mode и ключей), `backend/app/services/llm_client.py`, `backend/app/scripts/seed_section_contracts.py`, `contracts/seed/*.json`
2) **Довести retrieval до рабочего контура**:
   - **Текущий статус**: `RetrievalService` существует, но является заглушкой (метод `retrieve()` возвращает пустой список). Инфраструктура готова: chunks содержат embeddings (feature hashing), pgvector индексы созданы, но векторный поиск не реализован. Метод `retrieve_with_zone_crosswalk()` частично реализован: использует `ZoneConfigService` для получения целевых зон через crosswalk, но векторный поиск не выполняется.
   - **Что нужно сделать**:
     - Реализовать векторный поиск в `RetrievalService.retrieve()`: векторизация query через embedding API (использовать ту же модель, что для chunks), pgvector cosine distance search с фильтрами по study_id, doc_type, source_zone, language.
     - Завершить реализацию `RetrievalService.retrieve_with_zone_crosswalk()`: добавить векторный поиск с фильтрацией по целевым зонам из crosswalk и применением весов для ранжирования.
     - Переключить `GenerationService` с `LeanContextBuilder` (детерминированный подход) на `RetrievalService` для семантического поиска релевантных chunks.
     - Расширить `FactExtractionService` для извлечения дополнительных фактов по контрактам (сейчас базовые факты извлекаются через rules-first подход).
   - **Файлы**: `backend/app/services/retrieval.py` (заглушка), `backend/app/services/zone_config.py` (реализован), `backend/app/services/generation.py` (использует `LeanContextBuilder`), `backend/app/services/fact_extraction.py`, `backend/app/services/lean_passport.py`
3) **Реализовать выгрузку сгенерированных документов в Word (USR-309)**: ✅ **Реализовано**
   - **Текущий статус**: Реализован эндпоинт `GET /api/document-versions/{version_id}/download` и сервис `DocxAssembler`.
   - **Что было сделано**:
     - Добавлен эндпоинт для экспорта сгенерированного документа в формате DOCX
     - Реализован сервис `DocxAssembler` для сборки всех опубликованных секций в единый DOCX файл
     - Используется `python-docx` для создания DOCX файлов с сохранением структуры и форматирования
     - Удаление маркеров якорей из текста и формирование отображаемых имен секций
     - Audit logging при скачивании
   - **Файлы**: `backend/app/api/v1/export.py`, `backend/app/services/export/docx_assembler.py`

4) **Реализовать поиск несоответствий внутри одного документа (USR-401.1)**: ⚠️ **Частично реализовано**
   - **Текущий статус**: Реализован `AuditService` с внутридокументными и кросс-документными аудиторами. Создана таблица `audit_issues` для хранения находок. Однако API эндпоинты для работы с audit_issues отсутствуют. `ConflictService` всё ещё является заглушкой.
   - **Что было сделано**:
     - Реализован `AuditService` для оркестрации всех аудиторов
     - Внутридокументные аудиторы:
       - `ConsistencyAuditor`: проверка согласованности числовых значений (размер выборки, длительность исследования)
       - `AbbreviationAuditor`: проверка правильности использования аббревиатур
       - `VisitLogicAuditor`: проверка логики визитов и процедур
       - `PlaceholderAuditor`: обнаружение незаполненных плейсхолдеров
     - Кросс-документные аудиторы:
       - `ProtocolIcfConsistencyAuditor`: проверка согласованности между Протоколом и ICF
       - `ProtocolCsrConsistencyAuditor`: проверка согласованности между Протоколом и CSR
     - Создана таблица `audit_issues` для хранения находок (миграция 0022, 0023)
     - Найденные проблемы сохраняются в `audit_issues` с автоматическим логированием в `audit_log`
   - **Что нужно сделать**:
     - Добавить API эндпоинты для запуска аудита: `POST /api/document-versions/{version_id}/audit/intra`, `POST /api/document-versions/{primary_id}/audit/cross/{secondary_id}`
     - Добавить API эндпоинты для получения списка audit_issues: `GET /api/audit-issues?study_id=...&doc_version_id=...&status=...`
     - Добавить API эндпоинт для обновления статуса audit_issue: `PATCH /api/audit-issues/{issue_id}`
     - Интегрировать аудит в основной пайплайн обработки документов (автоматический запуск после ингестии)
     - Реализовать детекцию междокументных конфликтов в `ConflictService` (сравнение фактов из разных документов)
   - **Файлы**: `backend/app/services/audit/service.py` (реализован), `backend/app/services/audit/intra/*` (реализовано), `backend/app/services/audit/cross/*` (реализовано), `backend/app/services/conflicts.py` (заглушка), `backend/app/api/v1/conflicts.py`, `backend/app/db/models/conflicts.py`, `backend/app/db/models/audit.py`

### 9) Соответствие требованиям USR (Пользовательские требования)

**Ссылка на спецификацию:** `docs/usr.md` (версия 1.2, дата: 15.12.2025)

#### Модуль 1: Управление исследованиями и документами (USR-101 — USR-107)

- **USR-101** (Создание и управление исследованиями): ✅ **Реализовано**
  - API: `POST /api/studies`, `GET /api/studies/{study_id}`, `GET /api/studies?workspace_id=...`
  - Модель: `studies(id, workspace_id, study_code, title, status, created_at)` с уникальным индексом на `(workspace_id, study_code)`
  - Файлы: `backend/app/api/v1/studies.py`, `backend/app/db/models/studies.py`

- **USR-102** (Загрузка документов DOCX): ✅ **Реализовано**
  - API: `POST /api/document-versions/{version_id}/upload`
  - Валидация расширения `.docx`, проверка non-empty, сохранение в storage, вычисление SHA256
  - Файлы: `backend/app/api/v1/documents.py`

- **USR-103** (Версионирование документов): ✅ **Реализовано**
  - Автоматическое присвоение версий через `version_label`
  - Метаданные: `created_at`, `created_by`, `doc_type`, привязка к `study_id` через `document_id`
  - История версий: `GET /api/documents/{document_id}/versions`
  - Файлы: `backend/app/db/models/studies.py` (DocumentVersion), `backend/app/api/v1/documents.py`

- **USR-104** (Просмотр документов): ⚠️ **Частично реализовано**
  - Backend: API для получения anchors (`GET /api/document-versions/{version_id}/anchors`) и версий документов
  - Frontend: требуется реализация UI для просмотра документов с переходом по разделам и поиском
  - Файлы: `backend/app/api/v1/documents.py`, `frontend/app/documents/*`

- **USR-105** (Автоматическая обработка документов): ✅ **Реализовано**
  - Пайплайн ингестии создаёт anchors, chunks, facts (Study KB)
  - Статусы `ingestion_status`: `uploaded`, `processing`, `ready`, `needs_review`, `failed`
  - `ingestion_summary_json` содержит warnings и errors для элементов, требующих проверки
  - Файлы: `backend/app/services/ingestion/__init__.py`, `backend/app/api/v1/documents.py`

- **USR-106** (Статусы жизненного цикла): ✅ **Реализовано**
  - Поле `lifecycle_status` в таблице `documents` (enum: `draft`, `in_review`, `approved`, `superseded`)
  - История изменений через `audit_log` (требует проверки реализации)
  - Файлы: `backend/app/db/models/studies.py`, `backend/app/db/models/audit.py`

- **USR-107** (Критичность документа): ❌ **Не реализовано**
  - Требуется добавление поля для классификации критичности (например, "Source of Truth / Reference / Supporting")
  - TODO: Добавить поле в модель `Document` или `DocumentVersion`

#### Модуль 2: Центральная база знаний исследования (USR-201 — USR-205)

- **USR-201** (Просмотр фактов Study KB): ✅ **Реализовано**
  - API: `GET /api/studies/{study_id}/facts` с фильтрацией по типу факта, документу-источнику и версии
  - Файлы: `backend/app/api/v1/studies.py`

- **USR-202** (Детали фактов): ✅ **Реализовано**
  - Значение факта: `facts.value_json`
  - Статус валидации: `facts.status` (enum: `extracted`, `validated`, `conflicting`, `needs_review`)
  - Кликабельные ссылки на anchors через `fact_evidence.anchor_id`
  - Файлы: `backend/app/db/models/facts.py`, `backend/app/schemas/facts.py`

- **USR-203** (Несколько источников для факта): ✅ **Реализовано**
  - Таблица `fact_evidence` поддерживает несколько записей для одного `fact_id` с разными `anchor_id`
  - Уникальный индекс на `(fact_id, anchor_id, evidence_role)` предотвращает дубликаты
  - Файлы: `backend/app/db/models/facts.py`

- **USR-204** (Правила приоритета источников): ⚠️ **Частично реализовано**
  - Логика приоритетов может быть реализована в `FactExtractionService` и `FactConflictDetector`
  - Требуется проверка наличия конфигурируемых правил и их применения
  - Файлы: `backend/app/services/fact_extraction.py`, `backend/app/services/fact_conflict_detector.py`

- **USR-204.1** (Интерфейс управления правилами приоритетов): ❌ **Не реализовано**
  - TODO: Добавить API и UI для управления правилами приоритетов источников с версионированием и audit trail

- **USR-205** (Управление схемой данных Study KB): ⚠️ **Частично реализовано**
  - Правила извлечения фактов: `backend/app/services/fact_extraction_rules.py`
  - Требуется проверка наличия административного интерфейса для управления схемой и правилами валидации
  - Файлы: `backend/app/services/fact_extraction_rules.py`

#### Модуль 3: Интеллектуальное создание документов (USR-301 — USR-309)

- **USR-301** (Создание документов на основе шаблонов): ✅ **Реализовано**
  - Таблица `templates` с полями `workspace_id`, `doc_type`, `name`, `template_body`, `version`
  - Файлы: `backend/app/db/models/generation.py`

- **USR-301.1** (Управление шаблонами): ⚠️ **Частично реализовано**
  - Модель `Template` существует, требуется проверка наличия API для загрузки и управления шаблонами
  - Файлы: `backend/app/db/models/generation.py`

- **USR-301.2** (Настройка артефактов шаблонов): ✅ **Реализовано**
  - Контракты секций: `target_section_contracts` с версионированием
  - Правила QC Gate: `target_section_contracts.qc_ruleset_json`
  - Версионирование и audit trail через `target_section_contracts.version` и `audit_log`
  - Файлы: `backend/app/db/models/sections.py`, `backend/app/db/models/audit.py`

- **USR-302** (Автодополнение из Study KB): ❌ **Не реализовано**
  - TODO: Реализовать проактивные подсказки Co-pilot при написании текста в редакторе
  - Требуется интеграция с frontend и API для автодополнения

- **USR-303** (Генерация чернового варианта секции): ✅ **Реализовано**
  - API: `POST /api/generate/section`
  - Использует контракты секций и источники для генерации
  - Файлы: `backend/app/api/v1/generation.py`, `backend/app/services/generation.py`

- **USR-304** (Кликабельные ссылки на источники): ✅ **Реализовано**
  - `GeneratedTargetSection.artifacts_json` содержит `claims` с `anchor_ids` для прослеживаемости
  - Файлы: `backend/app/db/models/generation.py`, `backend/app/schemas/generation.py`

- **USR-305** (Блокировка публикации без QC): ✅ **Реализовано**
  - `ValidationService` проверяет контент перед публикацией
  - `GeneratedTargetSection.qc_status` может быть `blocked` при неудачной проверке
  - `qc_report_json` содержит причины блокировки
  - Файлы: `backend/app/services/generation.py` (ValidationService)

- **USR-306** (Выбор контракта секции): ✅ **Реализовано**
  - API: `GET /api/section-contracts?doc_type=...`
  - Контракты определяют: `allowed_sources_json`, `required_facts_json`, `citation_policy`, `qc_ruleset_json`
  - Файлы: `backend/app/api/v1/sections.py`, `backend/app/db/models/sections.py`

- **USR-307** (Глоссарий/терминологические правила): ⚠️ **Частично реализовано**
  - Создана таблица `terminology_dictionaries` для хранения терминологических словарей исследования (миграция 0022)
  - Поля: `study_id`, `term_category`, `preferred_term`, `variations` (JSONB), `created_at`, `updated_at`
  - Уникальный индекс на `(study_id, term_category, preferred_term)`
  - Правила могут быть включены в `target_section_contracts.qc_ruleset_json`
  - TODO: Добавить API эндпоинты для управления терминологическими словарями
  - TODO: Реализовать применение терминологических правил при генерации и проверках (например, в `TerminologyGuardService`)
  - Файлы: `backend/app/db/models/audit.py` (TerminologyDictionary), `backend/alembic/versions/0022_add_usr_4_1_enums_and_tables.py`

- **USR-308** (Список missing evidence / TBD): ⚠️ **Частично реализовано**
  - Логика может быть реализована в `GenerationService` или `ValidationService`
  - Требуется проверка наличия API для получения списка missing evidence
  - TODO: Добавить API endpoint для получения missing evidence по секции

- **USR-309** (Выгрузка в DOCX): ✅ **Реализовано**
  - API: `GET /api/document-versions/{version_id}/download` — скачивание собранного документа в формате DOCX
  - Использует `DocxAssembler` для сборки всех опубликованных секций в единый DOCX файл
  - Сохраняет структуру и форматирование, удаляет маркеры якорей из текста
  - Audit logging при скачивании
  - Файлы: `backend/app/api/v1/export.py`, `backend/app/services/export/docx_assembler.py`

#### Модуль 4: Контроль качества и несоответствий (USR-401 — USR-409)

- **USR-401** (Автоматическая сверка данных между документами): ⚠️ **Частично реализовано**
  - `ConflictService` существует, но является заглушкой (только возвращает существующие конфликты)
  - `FactConflictDetector` и `FactConsistencyService` могут использоваться для детекции, но не интегрированы в основной пайплайн
  - Файлы: `backend/app/services/conflicts.py`, `backend/app/services/fact_conflict_detector.py`

- **USR-401.1** (Поиск несоответствий внутри документа): ⚠️ **Частично реализовано**
  - Реализован `AuditService` с внутридокументными аудиторами:
    - `ConsistencyAuditor`: проверка согласованности числовых значений (размер выборки, длительность исследования)
    - `AbbreviationAuditor`: проверка правильности использования аббревиатур
    - `VisitLogicAuditor`: проверка логики визитов и процедур
    - `PlaceholderAuditor`: обнаружение незаполненных плейсхолдеров
  - Реализованы кросс-документные аудиторы:
    - `ProtocolIcfConsistencyAuditor`: проверка согласованности между Протоколом и ICF
    - `ProtocolCsrConsistencyAuditor`: проверка согласованности между Протоколом и CSR
  - Найденные проблемы сохраняются в таблицу `audit_issues` (не `conflicts`)
  - TODO: Добавить API эндпоинты для запуска аудита и получения списка audit_issues
  - TODO: Интегрировать аудит в основной пайплайн обработки документов
  - Файлы: `backend/app/services/audit/service.py`, `backend/app/services/audit/intra/*`, `backend/app/services/audit/cross/*`, `backend/app/db/models/audit.py`

- **USR-402** (Дашборд несоответствий): ⚠️ **Частично реализовано**
  - Backend API: `GET /api/conflicts?study_id=...`
  - Требуется реализация frontend дашборда с фильтрацией по типу конфликта (междокументные/внутридокументные)
  - Файлы: `backend/app/api/v1/conflicts.py`, `frontend/app/conflicts/*`

- **USR-403** (Side-by-side view конфликтов): ⚠️ **Частично реализовано**
  - Backend: `conflict_items` содержит `left_anchor_id` и `right_anchor_id` для сравнения
  - Требуется реализация frontend для отображения side-by-side view
  - Файлы: `backend/app/db/models/conflicts.py`, `frontend/app/conflicts/*`

- **USR-404** (Отчеты о несоответствиях в XLSX): ❌ **Не реализовано**
  - TODO: Добавить эндпоинт для генерации отчетов о конфликтах в формате XLSX
  - Файлы: `backend/app/api/v1/conflicts.py`

- **USR-405** (Управление жизненным циклом несоответствий): ✅ **Реализовано**
  - Модель `Conflict` содержит поля: `owner_user_id`, `status` (enum: `open`, `investigating`, `resolved`, `accepted_risk`, `suppressed`), `title`, `description`
  - Файлы: `backend/app/db/models/conflicts.py`

- **USR-406** (Объяснимый отчет QC Gate): ✅ **Реализовано**
  - `GeneratedTargetSection.qc_report_json` содержит тип ошибки, затронутый фрагмент, ссылки на evidence и рекомендуемое действие
  - Файлы: `backend/app/services/generation.py` (ValidationService), `backend/app/schemas/generation.py`

- **USR-407** (Traceability report): ⚠️ **Частично реализовано**
  - Данные для traceability доступны через связи: `GeneratedTargetSection` → `artifacts_json.claims` → `anchor_ids` → `fact_evidence` → `facts` → `document_versions`
  - Требуется реализация API endpoint для генерации traceability report
  - TODO: Добавить эндпоинт для формирования traceability report

- **USR-408** (Механизм исключений/подавления): ✅ **Реализовано**
  - Статус `suppressed` в enum `ConflictStatus` для подавления ложноположительных конфликтов
  - Обоснование через поле `description` в `Conflict`
  - Audit trail через `audit_log` (требует проверки записи при изменении статуса)
  - Файлы: `backend/app/db/models/conflicts.py`, `backend/app/db/enums.py`

- **USR-409** (Семантические проверки как кандидаты): ⚠️ **Частично реализовано**
  - `Conflict.severity` может использоваться для маркировки уровня уверенности
  - Требуется проверка наличия поля `confidence` или аналогичного для отображения уровня уверенности
  - TODO: Добавить поле `confidence` в модель `Conflict` для семантических проверок

#### Модуль 5: Управление изменениями (USR-501 — USR-506)

- **USR-501** (Автоматический анализ влияния): ✅ **Реализовано**
  - `ImpactService.compute_impact(...)` вычисляет воздействие изменений документов
  - Использует `AnchorAligner` и `AnchorMatch` для определения измененных якорей
  - Файлы: `backend/app/services/impact.py`, `backend/app/api/v1/impact.py`

- **USR-502** (Отчет о влиянии): ✅ **Реализовано**
  - API: `GET /api/impact?study_id=...&change_event_id=...`
  - `ImpactItem` содержит описание затронутых документов и секций с указанием причины
  - Файлы: `backend/app/api/v1/impact.py`, `backend/app/services/impact.py`

- **USR-503** (Варианты обновления затронутых секций): ⚠️ **Частично реализовано**
  - `ImpactItem.recommended_action` содержит рекомендуемое действие
  - Требуется проверка наличия автоматического патча или регенерации
  - TODO: Реализовать автоматический патч для данных и регенерацию для нарратива

- **USR-504** (Режим сравнения redline/diff): ⚠️ **Частично реализовано**
  - `DiffService` существует для сравнения версий документов
  - Требуется реализация frontend для отображения redline/diff view
  - Файлы: `backend/app/services/diff.py`, `frontend/app/impact/*`

- **USR-505** (Принятие/отклонение обновлений): ⚠️ **Частично реализовано**
  - `ImpactItem.status` может использоваться для отслеживания принятия/отклонения
  - Требуется проверка наличия API для принятия/отклонения с комментарием и записью в audit trail
  - TODO: Добавить API endpoint для принятия/отклонения обновлений с комментарием

- **USR-506** (История impact-отчетов): ✅ **Реализовано**
  - Таблица `change_events` хранит историю изменений документов
  - Таблица `impact_items` хранит результаты анализа влияния
  - Файлы: `backend/app/db/models/change.py`

#### Общие и нефункциональные требования (USR-601 — USR-606)

- **USR-601** (Интерфейс): ⚠️ **Частично реализовано**
  - Frontend на Next.js с базовыми экранами
  - Требуется проверка интуитивности и доступности ключевых действий
  - Файлы: `frontend/app/*`

- **USR-602** (Производительность): ⚠️ **Требует тестирования**
  - Целевое время отклика автодополнения: ≤ 2 секунд (не реализовано)
  - Целевое время обработки DOCX до 300 страниц: ≤ 10 минут (требует измерения)
  - Файлы: `backend/app/services/ingestion/__init__.py`

- **USR-603** (Безопасность / RBAC): ✅ **Реализовано**
  - Модели: `workspaces`, `users`, `memberships` с полем `role`
  - Требуется проверка реализации разграничения прав на уровне Study/документов/функций
  - Файлы: `backend/app/db/models/auth.py`

- **USR-604** (Аудит): ✅ **Реализовано**
  - Таблица `audit_log` для записи действий пользователей и системы
  - Поля: `actor_user_id`, `action`, `entity_type`, `entity_id`, `before_json`, `after_json`, `created_at`
  - Файлы: `backend/app/db/models/audit.py`, `backend/app/core/audit.py`

- **USR-605** (Надежность / Backup): ⚠️ **Требует настройки**
  - Резервное копирование и восстановление должны быть настроены на уровне инфраструктуры
  - Параметры RPO/RTO должны быть согласованы с заказчиком

- **USR-606** (Наблюдаемость): ✅ **Реализовано**
  - Логирование через `app.core.logging`
  - Health endpoint: `GET /health`
  - Файлы: `backend/app/core/logging.py`, `backend/app/main.py`

#### Требования соответствия (USR-701 — USR-705)

- **USR-701** (GxP соответствие): ✅ **Частично реализовано**
  - Управление доступом: модели `workspaces`, `users`, `memberships`
  - Audit trail: таблица `audit_log`
  - Управляемые статусы: `lifecycle_status`, `ingestion_status`, `conflict.status`, `impact_item.status`
  - Контролируемая конфигурация: версионирование `target_section_contracts`, `templates`
  - Электронная подпись (Part 11 e-sign) исключена из v1.x
  - Файлы: `backend/app/db/models/*`, `backend/app/core/audit.py`

- **USR-702** (Валидационный пакет): ⚠️ **Требует разработки**
  - TODO: Сформировать валидационный пакет документов или шаблоны для формирования пакета
  - Требуется набор тест-кейсов и трассируемость требований

- **USR-703** (Политики конфиденциальности / GDPR): ⚠️ **Требует настройки**
  - Разграничение доступа реализовано через RBAC
  - Требуется настройка политик хранения/удаления данных в соответствии с согласованными политиками

- **USR-704** (Governance / Контроль изменений конфигурации): ✅ **Реализовано**
  - Версионирование: `target_section_contracts.version`, `templates.version`
  - Audit trail для изменений конфигурации через `audit_log`
  - Файлы: `backend/app/db/models/sections.py`, `backend/app/db/models/generation.py`

- **USR-705** (Режим валидации / Validation Mode): ❌ **Не реализовано**
  - TODO: Реализовать режим валидации с предопределенными тест-кейсами ('golden test cases')
  - Автоматическая генерация отчета о выполнении теста с вердиктом Pass/Fail


