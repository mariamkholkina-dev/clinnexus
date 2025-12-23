## Архитектура ClinNexus MVP

### Ключевые сущности

- **Workspace / User / Membership**: многотенантность и RBAC на уровне рабочего пространства.
- **Study**: исследование в рамках workspace.
- **Document / DocumentVersion**: документы и их версии, связанные со study.
- **Anchor**: минимальная адресуемая единица содержимого (параграф/ячейка/функция и т.д.).
  - Для paragraph-anchors (P/LI/HDR): `anchor_id = {doc_version_id}:{content_type}:{para_index}:{hash(text_norm)}`
  - Для footnotes (FN): `anchor_id = {doc_version_id}:fn:{fn_index}:{fn_para_index}:{hash(text_norm)}`
  - `location_json` описывает координаты в исходном документе (para_index, fn_index, fn_para_index, section_path и т.п.).
  - ВАЖНО: `section_path` и `ordinal` не входят в `anchor_id` для стабильности при переносах между разделами.
  - `source_zone` — зона источника (ENUM: один из 12 канонических ключей + "unknown") для классификации контента
    - Канонические ключи: `overview`, `design`, `ip`, `statistics`, `safety`, `endpoints`, `population`, `procedures`, `data_management`, `ethics`, `admin`, `appendix`
    - Классификация выполняется через `SourceZoneClassifier` на основе `section_path` и `heading_text`
  - `language` — язык контента (ru/en/mixed/unknown) для многоязычных документов.
- **Chunk**: векторное представление нескольких anchor-ов (pgvector) + список `anchor_ids`.
  - `source_zone` и `language` наследуются от anchors (most_common для source_zone) для фильтрации и поиска.
- **AnchorMatch**: соответствия между якорями разных версий документа для diff/impact анализа.
  - Используется для выравнивания якорей при сравнении версий (exact/fuzzy/embedding/hybrid методы).
- **Study KB Facts + fact_evidence**: факты исследования и их привязка к конкретным anchor-ам.
- **StudyCoreFacts**: структурированные основные факты исследования (study_title, phase, study_design_type, population_short, arms, primary_endpoints, sample_size, duration) с версионированием.
- **Templates + TargetSectionContracts**: шаблоны и контракты разделов протокола (JSON-схемы). Таблица `target_section_contracts` (переименована из `section_contracts` в миграции 0017).
  - `target_section` — целевая секция (один из 12 канонических ключей, валидация в моделях и схемах).
  - `view_key` — ключ представления для группировки секций в UI.
  - `retrieval_recipe_json.prefer_source_zones` — приоритетные source_zone для retrieval (автоматически заполняются из правил для каждой target_section).
  - `retrieval_recipe_json.fallback_source_zones` — резервные source_zone, если prefer пуст.
  - **ПРИМЕЧАНИЕ**: Структура документов определяется через templates и `target_section_contracts`. Таблицы taxonomy (`target_section_taxonomy_*`) удалены в миграции 0020.
- **Topics + HeadingClusters + ClusterAssignment + TopicEvidence**: семантические топики для группировки контента (расширено в миграциях 0014, 0018).
  - `topics` — топики с workspace_id, topic_key, title_ru/en, description, topic_profile_json, is_active, topic_embedding, applicable_to_json.
  - `heading_clusters` — кластеры заголовков с cluster_embedding (миграция 0014).
  - `cluster_assignments` — привязка кластеров к топикам для doc_version с mapping_debug_json (миграция 0015).
  - `topic_evidence` — агрегированные доказательства для топиков с anchor_ids, chunk_ids, source_zone, language.
  - `topic_mapping_runs` — отслеживание запусков маппинга топиков (миграция 0014).
  - `topic_zone_priors` — приоритеты зон по doc_type для топиков (миграция 0018).
- **ZoneSets + ZoneCrosswalk**: наборы зон и кросс-документный маппинг зон (миграция 0019).
- **IngestionRuns**: отслеживание запусков ингестии с метриками, качеством и предупреждениями (миграция 0013).
- **GenerationRun + GeneratedTargetSection**: процесс и результат генерации текста раздела, артефакты и QC. Таблица `generated_target_sections` (переименована из `generated_sections` в миграции 0017).
  - `target_section` — целевая секция (переименовано из `section_key` в миграции 0007).
  - `view_key` — ключ представления.
- **Conflicts + conflict_items**: обнаруженные противоречия между фактами/документами.
- **ChangeEvents + ImpactItems + Tasks**: изменения, их потенциальный импакт и задачи по внедрению.
  - `affected_target_section` — затронутая целевая секция (переименовано из `affected_section_key`).
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
  1. RetrievalService использует pgvector для поиска релевантных anchors/chunks с фильтрацией по `source_zone`.
  2. Приоритизация по `prefer_source_zones` из `section_contract.retrieval_recipe_json` (сначала prefer, затем fallback).
  3. GenerationService формирует `GenerationRun` и `GeneratedSection` (+Artifacts, QCReport).

- **Conflicts / Impact**
  1. DiffService/ValidationService генерируют ChangeEvents, Conflicts.
  2. ImpactService проецирует изменения на ImpactItems и Tasks.

- **Passport Tuning / Topics**
  1. API `/api/passport-tuning/*` для работы с кластерами заголовков и их маппингом на секции.
  2. TopicEvidenceBuilder строит агрегированные доказательства для топиков из cluster_assignments.
  3. Используется для настройки паспортов секций и семантической группировки контента.


