# Архив файлов ClinNexus

Этот архив содержит файлы, реализующие ключевые компоненты системы по трём категориям:

## B) Код ingestion pipeline

**Путь:** `B_ingestion_pipeline/`

### Файлы:
- **services/ingestion/__init__.py** - IngestionService: оркестрация ингестии (upload → parse → anchors → chunks/embeddings → facts → SoA extraction)
- **services/ingestion/docx_ingestor.py** - DocxIngestor: парсинг DOCX, создание anchors, построение section_path, генерация anchor_id
- **services/ingestion/heading_detector.py** - HeadingDetector: детекция заголовков для построения section_path
- **services/text_normalization.py** - Нормализация текста (text_norm) для матчинга и хеширования
- **services/chunking.py** - ChunkingService: создание chunks с embeddings (feature hashing)
- **services/fact_extraction.py** - FactExtractionService: извлечение фактов (rules-first)
- **services/soa_extraction.py** - SoAExtractionService: извлечение Schedule of Activities из таблиц
- **api/documents.py** - API эндпоинты для upload и ingest

### Ключевые функции:
- `upload → parse → anchors → chunks/embeddings → facts → SoA extraction`
- Построение `section_path` на основе заголовков документа
- Нормализация текста (`text_norm`) для стабильного хеширования
- Генерация `anchor_id`: `{doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash}`

---

## C) Пасспорты и retrieval

**Путь:** `C_passports_retrieval/`

### Файлы:
- **db_models/sections.py** - Модели SectionContract и SectionMap (формат section_contracts)
- **services/retrieval.py** - RetrievalService: выбор anchors/chunks (pgvector, TODO: полная реализация)
- **services/generation.py** - GenerationService: генератор секций (prompt, structured output, citations)
- **api/sections.py** - API для работы с section_contracts и section_maps
- **schemas/sections.py** - Pydantic схемы для section_contracts
- **scripts/seed_section_contracts.py** - Сидер для загрузки паспортов из JSON
- **contracts_seed/*.json** - JSON файлы с паспортами секций (seed данные)

### Ключевые функции:
- Формат `section_contracts`: JSONB поля (required_facts_json, allowed_sources_json, retrieval_recipe_json, qc_ruleset_json)
- Retrieval service: выбор anchors/chunks по запросу (с фильтрами)
- Генератор секций: формирование prompt, structured output, citations на основе section_contracts

---

## D) Diff/impact/tasks

**Путь:** `D_diff_impact_tasks/`

### Файлы:
- **services/diff.py** - DiffService: сравнение DocumentVersion (TODO: полная реализация)
- **services/impact.py** - ImpactService: вычисление impact по topics (TODO: полная реализация)
- **db_models/change.py** - Модели ChangeEvent, ImpactItem, Task для diff/impact/tasks

### Ключевые функции:
- Сравнение DocumentVersion (по anchors/chunks/facts)
- Вычисление diff (добавленные/удалённые/изменённые секции)
- Построение задач/impact graph на основе изменений

---

## Примечания

- Файлы с пометкой "TODO" содержат заглушки для будущей реализации
- Все файлы используют async/await с SQLAlchemy async
- Модели БД используют pgvector для векторного поиска
- Section contracts хранятся в JSONB формате для гибкости

