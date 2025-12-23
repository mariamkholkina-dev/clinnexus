# Архив данных ClinNexus для переписывания алгоритма topic mapping

Дата создания: 2025-12-22

## Структура архива

### 01_schema_and_models/
**Схема БД и ORM-модели**

- `schema.sql` - актуальная схема базы данных (PostgreSQL)
- `models/` - папка с ORM-моделями SQLAlchemy:
  - `topics.py` - модели Topic, ClusterAssignment, TopicEvidence, HeadingCluster, TopicMappingRun
  - `anchors.py` - модели Anchor, Chunk
  - `sections.py` - модели SectionContract, SectionMap
  - `studies.py` - модели Document, DocumentVersion
  - `ingestion_runs.py` - модель IngestionRun
  - `facts.py` - модели Fact, FactEvidence
  - и другие модели

### 02_ingestion_pipeline/
**Код текущего ingestion pipeline (entrypoints)**

Главный оркестратор:
- `__init__.py` - IngestionService.ingest() - основной метод ингестии
- `job_runner.py` - run_ingestion_now() - точка входа для выполнения ингестии

Классификация зон:
- `source_zone_classifier.py` - SourceZoneClassifier с правилами из YAML

Извлечение фактов:
- `fact_extraction.py` - FactExtractionService.extract_and_upsert()
- `fact_extraction_rules.py` - реестр правил извлечения фактов (regex-паттерны)

Извлечение SoA:
- `soa_extraction.py` - SoAExtractionService.extract_soa()

Маппинг секций:
- `section_mapping.py` - SectionMappingService.map_sections()

Создание chunks:
- `chunking.py` - ChunkingService.rebuild_chunks_for_doc_version()

Модули ингестии:
- `docx_ingestor.py` - парсинг DOCX и создание anchors
- `heading_detector.py` - детекция заголовков
- `metrics_collector.py` - сбор метрик ингестии
- `metrics.py` - вычисление pipeline_config_hash, git_sha
- `quality_gate.py` - проверка качества ингестии

**Формирование ingestion_summary_json и needs_review:**
- `metrics_collector.py` - собирает метрики в `summary_json`
- `quality_gate.py` - формирует `quality_json` и определяет `needs_review`
- В `IngestionService.ingest()` (строки 524-531) формируется `ingestion_summary_json` и `needs_review`

### 03_topic_mapping/
**Код topic mapping (текущее состояние)**

Основной сервис:
- `topic_mapping.py` - TopicMappingService.map_topics_for_doc_version()
  - Формат cluster_assignments: см. модель ClusterAssignment в topics.py
  - Формат topic_evidence: см. модель TopicEvidence в topics.py
  - Как собираются anchor_ids/chunk_ids: см. topic_evidence_builder.py

Вспомогательные сервисы:
- `topic_repository.py` - репозиторий для работы с топиками
- `topic_evidence_builder.py` - построение topic_evidence из cluster_assignments
- `heading_clustering.py` - кластеризация заголовков (HeadingClusteringService)
- `cluster_headings.py` - offline-скрипт для кластеризации заголовков

**Формат cluster_assignments:**
- `doc_version_id` - UUID версии документа
- `cluster_id` - int, ID кластера заголовков
- `topic_key` - str, ключ топика
- `mapped_by` - str, режим маппинга ("auto" или "assist")
- `confidence` - float | None, уверенность маппинга
- `mapping_debug_json` - dict с top3 кандидатами и explanation

**Формат topic_evidence:**
- `doc_version_id` - UUID версии документа
- `topic_key` - str, ключ топика
- `source_zone` - str, зона источника
- `language` - DocumentLanguage enum
- `anchor_ids` - list[str], массив anchor_id
- `chunk_ids` - list[UUID], массив chunk_id
- `score` - float | None, оценка релевантности
- `evidence_json` - dict | None, дополнительные данные

### 04_configs_seeds/
**Конфиги/seed'ы, участвующие в pipeline_config_hash**

Правила зон:
- `source_zone_rules.yaml` - правила классификации source_zone по section_path и heading_text

Seed contracts:
- `contracts_seed/` - JSON файлы с контрактами секций:
  - `protocol.study_design.v2.json`
  - `protocol.soa.v2.json`
  - `protocol.endpoints.v2.json`
  - `protocol.eligibility.v2.json`
  - `csr.synopsis.v2.json`
  - `csr.methods.study_design.v2.json`
  - `sap.analysis_sets.v2.json`

Taxonomy:
- `section_taxonomy_protocol.json` - таксономия секций для протоколов

**Примечание:** pipeline_config_hash вычисляется в `backend/app/services/ingestion/metrics.py` методом `hash_configs()` и включает:
- source_zone_rules.yaml
- section_taxonomy_protocol.json
- seed contracts из contracts/seed/

### 05_campaign_results/
**Результаты кампании и guide**

- `ingestion_campaign_guide.md` - руководство по запуску кампании ингестии
- `campaign_summary.json` - результаты последней кампании (2025-12-22)

### 06_data_dump/
**Минимальный дамп данных (без PHI)**

- `export_sample_data.sql` - SQL-скрипт для экспорта примеров данных:
  - 1-2 примера document_versions
  - 1-2 примера ingestion_runs (summary_json, quality_json)
  - Агрегаты по anchors/chunks для 1 документа (counts by content_type/source_zone/language)
  - Несколько строк cluster_assignments
  - Несколько строк topic_evidence
  - Примеры heading_clusters

**Использование:**
```bash
psql -d clinnexus_db -f export_sample_data.sql > sample_data_export.txt
```

## Ключевые моменты для переписывания алгоритма

### 1. Формат данных cluster_assignments
См. модель `ClusterAssignment` в `01_schema_and_models/models/topics.py`:
- Связь: `doc_version_id` + `cluster_id` → `topic_key`
- `mapping_debug_json` содержит top3 кандидатов с детальными explanation

### 2. Формат данных topic_evidence
См. модель `TopicEvidence` в `01_schema_and_models/models/topics.py`:
- Агрегирует `anchor_ids` и `chunk_ids` для топика
- Группируется по `source_zone` и `language`
- Собирается из cluster_assignments через `topic_evidence_builder.py`

### 3. Текущий алгоритм маппинга
См. `03_topic_mapping/topic_mapping.py`:
- `_score_cluster_against_topics()` - вычисление score для каждого топика
- Компоненты score:
  - `rule_score` (40%): alias_match + keyword_match
  - `embedding_score` (30%): cosine similarity embeddings
  - `source_zone_prior` (30%): приоритет по source_zone
- Финальный score: взвешенная сумма с бустом для alias_match > 0.7

### 4. Метрики качества
См. `MappingMetrics` в `topic_mapping.py`:
- `coverage` - % кластеров с confidence >= threshold
- `ambiguity` - % кластеров где top1-top2 < delta
- `fallback_rate` - % кластеров где сработал только keyword-match
- `conflict_rate` - % кластеров помеченных dissimilar-конфликтом

### 5. Pipeline config hash
См. `02_ingestion_pipeline/metrics.py`:
- `hash_configs()` - вычисляет хеш от конфигов для отслеживания версий
- Участвуют: source_zone_rules.yaml, section_taxonomy_protocol.json, seed contracts

## Зависимости между компонентами

1. **Ingestion pipeline** создает:
   - anchors (с source_zone через SourceZoneClassifier)
   - chunks (из anchors через ChunkingService)
   - heading_clusters (через HeadingClusteringService)

2. **Topic mapping** использует:
   - heading_clusters (для маппинга на топики)
   - anchors/chunks (для построения topic_evidence)

3. **Topic evidence** строится из:
   - cluster_assignments (связь кластеров с топиками)
   - anchors/chunks (привязка к конкретному контенту)

## Примечания

- Все модели используют UUID для первичных ключей
- source_zone классифицируется через SourceZoneClassifier на основе section_path и heading_text
- topic_evidence агрегируется по source_zone и language для фильтрации
- pipeline_config_hash используется для отслеживания версий конфигурации

