# Полная схема обработки документа в ClinNexus

## 1. Загрузка документа (Upload)

**Эндпоинт:** `POST /document-versions/{version_id}/upload`

**Что происходит:**
- Файл сохраняется в локальное хранилище (`backend/.data/uploads/{version_id}/`)
- Вычисляется SHA256 хеш файла
- Автоматически определяется язык документа (для DOCX)
- Обновляется `DocumentVersion`:
  - `source_file_uri` — путь к файлу
  - `source_sha256` — хеш файла
  - `ingestion_status` → `uploaded`
  - `document_language` — язык (ru/en/mixed/unknown)

**Хранение:**
- Таблица `document_versions` — метаданные версии
- Файл на диске

---

## 2. Ингестия документа (Ingestion Pipeline)

**Эндпоинт:** `POST /document-versions/{version_id}/ingest`

**Процесс координируется через `IngestionService.ingest()`:**

**Отслеживание процесса:**
- Создаётся запись `IngestionRun` в таблице `ingestion_runs` для отслеживания процесса ингестии (миграция 0013)
- Собираются метрики через `MetricsCollector` (количество anchors, chunks, фактов, качество маппинга и т.д.)
- Применяется `QualityGate` для оценки качества ингестии
- Результаты сохраняются в `ingestion_runs.summary_json`, `ingestion_runs.quality_json`, `ingestion_runs.warnings_json` и `ingestion_runs.errors_json`
- Связь с `document_versions` через `last_ingestion_run_id`

### Шаг 2.1: Очистка предыдущих данных
- Удаляются старые `chunks` для этой версии
- Удаляются старые `anchors` для этой версии
- Удаляются `facts` и `fact_evidence`, созданные из этой версии

### Шаг 2.2: Парсинг структуры документа (DocxIngestor)

**Классификация source_zone:**
- Для каждого anchor выполняется классификация через `SourceZoneClassifier`
- Входные данные: `section_path` (иерархия заголовков), `heading_text` (текст текущего заголовка), `language` (язык документа)
- Правила классификации загружаются из `backend/app/data/source_zone_rules.yaml`
- Поддерживаются паттерны для русского и английского языков
- Результат: `{zone, confidence, matched_rule_id}`

**Что извлекается:**
- Параграфы (`p`) — обычный текст
- Заголовки (`hdr`) — определяются через стили/outline/visual fallback
- Элементы списков (`li`) — через numbering properties
- Сноски (`fn`) — если доступны через python-docx
- Структура разделов (`section_path`) — иерархия заголовков

**Как создаются Anchors:**

Для каждого элемента создаётся `Anchor` с полями:
- `anchor_id` — формат зависит от типа:
  - Для paragraph-anchors (P/LI/HDR): `{doc_version_id}:{content_type}:{para_index}:{hash(text_norm)}`
  - Для footnotes (FN): `{doc_version_id}:fn:{fn_index}:{fn_para_index}:{hash(text_norm)}`
- `section_path` — путь по структуре документа (например, "3.2.1" или "__FRONTMATTER__"), не входит в anchor_id
- `content_type` — тип контента (hdr/p/li/fn/cell/tbl)
- `ordinal` — порядковый номер в секции (не входит в anchor_id)
- `text_raw` — исходный текст
- `text_norm` — нормализованный текст (whitespace collapsed)
- `text_hash` — SHA256 хеш нормализованного текста
- `location_json` — метаданные (para_index, fn_index, fn_para_index, style, section_path)
- `source_zone` — зона источника (ENUM: один из 12 канонических ключей + "unknown") для классификации контента
  - Канонические ключи: `overview`, `design`, `ip`, `statistics`, `safety`, `endpoints`, `population`, `procedures`, `data_management`, `ethics`, `admin`, `appendix`
  - Классификация выполняется через `SourceZoneClassifier` на основе `section_path` и `heading_text`
- `language` — язык контента (ru/en/mixed/unknown) для многоязычных документов

**Хранение:**
- Таблица `anchors` — все атомарные элементы документа

### Шаг 2.3: Извлечение Schedule of Activities (SoA)

**Сервис:** `SoAExtractionService.extract_soa()`

**Что извлекается:**
- Поиск таблицы SoA в документе
- Извлечение структуры:
  - `visits` — визиты исследования
  - `procedures` — процедуры
  - `matrix` — матрица визиты × процедуры
- Создание `cell` anchors для ячеек таблицы

**Хранение:**
- Дополнительные `anchors` с `content_type=cell`
- Таблица `facts`:
  - `fact_type="soa"`, `fact_key="visits"` — список визитов
  - `fact_type="soa"`, `fact_key="procedures"` — список процедур
  - `fact_type="soa"`, `fact_key="matrix"` — матрица визиты × процедуры
  - `status` определяется на основе `confidence`: `extracted` (≥0.7) или `needs_review` (<0.7)
- Таблица `fact_evidence` — связь фактов с `anchor_id` ячеек (ограничение: первые 100 для matrix)

### Шаг 2.4: Создание Chunks (Narrative Index)

**Сервис:** `ChunkingService.rebuild_chunks_for_doc_version()`

**Что создаётся:**
- Группировка `anchors` по `section_path` (исключая cell anchors)
- Объединение текста нескольких anchors в один chunk (до ~450 токенов)
- Создание embedding через feature hashing (детерминированный, без внешних API)
- Определение `source_zone` для chunk: наиболее часто встречающаяся `source_zone` среди anchors в chunk

**Поля Chunk:**
- `id` — UUID (первичный ключ)
- `chunk_id` = `{doc_version_id}:{section_path}:{ordinal}:{hash16}` (строковый идентификатор)
- `section_path` — путь секции
- `text` — объединённый текст из anchors (исключая cell anchors)
- `anchor_ids[]` — массив строковых `anchor_id` anchors, входящих в chunk
- `embedding` — вектор 1536 размерности (feature hashing, детерминированный)
- `metadata_json` — метаданные (token_estimate, anchor_count)
- `source_zone` — зона источника (most_common среди anchors в chunk)
- `language` — язык контента (наследуется от anchors)

**Хранение:**
- Таблица `chunks` — векторный индекс для семантического поиска

### Шаг 2.4.1: Выравнивание якорей с предыдущей версией (Anchor Alignment)

**Сервис:** `AnchorAligner.align()`

**Что происходит:**
- Поиск предыдущей версии документа по `document_id` и `effective_date` (или `created_at`)
- Выравнивание якорей текущей версии с якорями предыдущей версии
- Создание `anchor_matches` — соответствия между якорями разных версий
- Использование методов: exact match, fuzzy match, embedding similarity, hybrid

**Хранение:**
- Таблица `anchor_matches`:
  - `prev_anchor_id` → `curr_anchor_id` (строковые идентификаторы)
  - `match_type` — exact/fuzzy/embedding/hybrid
  - `similarity_score` — оценка сходства (для fuzzy/embedding)
  - `change_type` — unchanged/changed/added/deleted

**Результат:**
- Метрики выравнивания: количество matched и changed anchors
- Сохранение в `summary_json.matched_anchors` и `summary_json.changed_anchors`

**Примечание:** Выполняется после создания chunks для обеспечения возможности сравнения версий документов и анализа изменений.

### Шаг 2.5: Извлечение фактов (Rules-first)

**Сервис:** `FactExtractionService.extract_and_upsert()`

**Что извлекается (через regex-правила):**
- `protocol_meta / protocol_version` — версия протокола
- `protocol_meta / amendment_date` — дата поправки
- `population / planned_n_total` — планируемое число участников
- Другие факты согласно реестру правил извлечения

**Процесс:**
1. Загрузка anchors (hdr/p/li/fn), сортировка
2. Поиск паттернов в тексте
3. Upsert в `facts` по `(study_id, fact_type, fact_key)`
4. Создание `fact_evidence` — связь факта с `anchor_id`
5. Определение `status` на основе качества извлечения (extracted/needs_review)

**Примечание:** Выполняется после создания chunks и извлечения SoA, чтобы все anchors были доступны.

**Хранение:**
- Таблица `facts`:
  - `fact_type` — категория (protocol_meta, population, soa)
  - `fact_key` — ключ факта
  - `value_json` — значение в JSON
  - `status` — extracted/validated/conflicting/tbd/needs_review
  - `created_from_doc_version_id` — источник
  - `confidence` — уверенность извлечения (опционально)
  - `extractor_version` — версия экстрактора (опционально)
- Таблица `fact_evidence`:
  - `fact_id` → `anchor_id` (строковый идентификатор)
  - `evidence_role` — primary/supporting

### Шаг 2.5.1: Проверка согласованности фактов (Fact Consistency Check)

**Сервис:** `FactConsistencyService.check_study_consistency()`

**Что проверяется:**
- Логические несоответствия между фактами исследования
- Противоречия в значениях фактов одного типа
- Обнаруженные конфликты сохраняются в таблице `conflicts`

**Результат:**
- Количество найденных конфликтов
- Если найдены конфликты, устанавливается флаг `needs_review`
- Предупреждения добавляются в `warnings_json`

**Примечание:** Выполняется после извлечения фактов для выявления логических несоответствий в данных исследования.

### Шаг 2.6: Маппинг секций (Section Mapping)

**Сервис:** `SectionMappingService.map_sections()`

**Что происходит:**
- Автоматическое сопоставление семантических секций (`target_section`) с `section_path` документа
- Поиск заголовков, соответствующих секциям из `target_section_contracts`
- Создание `target_section_maps` — связь `target_section` с `anchor_ids` и `chunk_ids`
- `target_section` должен быть одним из 12 канонических ключей (валидация в моделях и схемах)
- **ПРИМЕЧАНИЕ**: Таблицы taxonomy удалены в миграции 0020. Структура документов определяется через templates и `target_section_contracts`.

**Хранение:**
- Таблица `target_section_maps` (переименована из `section_maps` в миграции 0017):
  - `doc_version_id` + `target_section` (уникально, переименовано из `section_key` в миграции 0007)
  - `anchor_ids[]` — anchors секции
  - `chunk_ids[]` — chunks секции
  - `confidence` — уверенность маппинга
  - `status` — mapped/needs_review/overridden
  - `mapped_by` — system/user

### Шаг 2.6.1: LLM-assist для проблемных секций (опционально)

**Сервис:** `SectionMappingAssistService.assist()`

**Что происходит:**
- Автоматическая проверка проблемных секций (статус `needs_review` или 0 anchors)
- Использование LLM для поиска заголовков-кандидатов для проблемных секций
- Применение найденных кандидатов с валидацией через QC
- Выполняется только если включен `SECURE_MODE` и настроены LLM API ключи

**Результат:**
- Обновление `target_section_maps` с найденными кандидатами
- QC-отчёты для каждой обработанной секции
- Информация о использовании LLM сохраняется в `summary_json.llm_info`

**Примечание:** Выполняется автоматически после маппинга секций для улучшения качества маппинга проблемных секций. При ошибках процесс не прерывается, ошибки добавляются в warnings.

### Шаг 2.7: Topic Mapping (только для протоколов)

**Сервисы:** `HeadingBlockBuilder`, `TopicMappingService`, `TopicEvidenceBuilder`

**Что происходит:**

1. **Построение heading blocks:**
   - `HeadingBlockBuilder` строит блоки заголовков из anchors для doc_version
   - Блок = заголовок (HDR) + контент (P/LI) до следующего заголовка
   - Генерируется стабильный `heading_block_id` на основе `heading_anchor_id`
   - Определяется `source_zone` через `SourceZoneClassifier`

2. **Маппинг блоков на топики:**
   - Прямой маппинг блоков на топики через `TopicMappingService`
   - Оценка соответствия блока топику включает:
     - **Heading match** (0.4 веса): точное/нечёткое совпадение заголовков с aliases топика
     - **Keyword match** (0.3 веса): совпадение ключевых слов из `topic_profile_json`
     - **Embedding similarity** (0.3 веса): cosine similarity между блоками и топиками
     - **Source zone prior** (0.3 веса): буст/штраф на основе совпадения source_zone
     - **Cluster prior** (опционально): приоритет от кластеризации, если включена
     - **Neighbor bonus**: бонус за соседство с уже замаппленным блоком
   - Создание `heading_block_topic_assignments` — прямая привязка блоков к топикам

3. **Опциональная кластеризация (если включена):**
   - Группировка похожих заголовков по семантическому сходству
   - Использование embedding для сравнения заголовков
   - Создание кластеров с порогами: `threshold=0.22`, `min_size=3`, `embedding_threshold=0.15`
   - Кластеризация используется только как prior для маппинга блоков

4. **Построение topic_evidence:**
   - `TopicEvidenceBuilder` строит агрегированные доказательства из `heading_block_topic_assignments`
   - Агрегация по `(topic_key, source_zone, language)`
   - Сбор `anchor_ids[]` и `chunk_ids[]` для каждого топика

**Хранение:**
- Таблица `topics` — семантические топики (миграции 0008, 0014, 0018):
  - `workspace_id`, `topic_key`, `title_ru`, `title_en`, `description`
  - `topic_profile_json` — профиль топика с aliases, keywords, source_zones, dissimilar_zones, embeddings
  - `is_active` — активность топика
  - `topic_embedding` — векторное представление топика VECTOR(1536)
  - `applicable_to_json` — список doc_type, к которым применим топик
- Таблица `heading_block_topic_assignments` (миграция 0021) — прямой маппинг блоков на топики:
  - `doc_version_id`, `heading_block_id`, `topic_key`, `confidence`
  - `debug_json` — debug-информация о маппинге (top3 кандидаты, сигналы)
  - Уникальный индекс на `(doc_version_id, heading_block_id)`
- Таблица `heading_clusters` (миграция 0014) — кластеры заголовков (опционально):
  - `doc_version_id`, `cluster_id`, `language`
  - `top_titles_json`, `examples_json`, `stats_json`
  - `cluster_embedding` — векторное представление кластера VECTOR(1536)
- Таблица `cluster_assignments` — привязка кластеров к топикам для doc_version (опционально):
  - `mapping_debug_json` — debug-информация о маппинге (миграция 0015)
- Таблица `topic_evidence` — агрегированные доказательства для топиков:
  - `anchor_ids[]`, `chunk_ids[]`, `source_zone`, `language`
  - `score` — максимальный confidence из assignments
  - `evidence_json` — метаданные (top_headings, block_ids, blocks_count)
- Таблица `topic_mapping_runs` (миграция 0014) — отслеживание запусков маппинга топиков
- Таблица `topic_zone_priors` (миграция 0018) — приоритеты зон по doc_type для топиков

**Примечание:** Topic mapping выполняется только для документов типа `protocol`. При ошибках процесс не прерывается, ошибки добавляются в warnings. Кластеризация опциональна и используется только как prior для маппинга блоков.

### Шаг 2.8: Quality Gate и финализация

**Сервис:** `QualityGate.evaluate()`

**Что проверяется:**
- Обязательные факты (required_facts) из `QualityGate.REQUIRED_FACTS`
- Метрики ингестии (количество anchors, chunks, фактов, качество маппинга)
- Наличие SoA для протоколов
- Качество извлечения фактов

**Результат:**
- `quality_json` — оценка качества ингестии
- `needs_review` — флаг необходимости ручной проверки
- `warnings` — предупреждения о потенциальных проблемах

**Хранение:**
- Таблица `ingestion_runs` (миграция 0013):
  - `quality_json` — результаты оценки качества
  - `summary_json` — сводные метрики ингестии
  - `warnings_json` — список предупреждений
  - `errors_json` — список ошибок
  - `pipeline_version` и `pipeline_config_hash` — версия и конфигурация пайплайна
  - `status` — статус запуска (ok/failed/partial)
  - `duration_ms` — длительность выполнения
- `DocumentVersion.ingestion_summary_json` — зеркалирование для обратной совместимости
- `DocumentVersion.last_ingestion_run_id` — ссылка на последний запуск ингестии

---

## 3. Использование извлечённых данных

### 3.1: Study Knowledge Base (Facts)

**Доступ:** `GET /studies/{study_id}/facts`

**Использование:**
- Централизованное хранилище фактов исследования
- Используется при генерации секций документов
- Каждый факт связан с доказательствами (`fact_evidence` → `anchors`)
- Отслеживание изменений через `created_from_doc_version_id`

### 3.2: Векторный поиск (Retrieval)

**Сервис:** `RetrievalService.retrieve()`

**Как работает:**
- Векторизация запроса (query embedding)
- Поиск похожих `chunks` через pgvector (cosine distance)
- Фильтрация по `study_id`, `doc_type`, `section_path`, `source_zone`
- Приоритизация по `prefer_source_zones` из `section_contract.retrieval_recipe_json`:
  - Сначала ищутся chunks из `prefer_source_zones`
  - Если недостаточно, используются `fallback_source_zones`
- Возврат топ-k релевантных chunks с `anchor_ids`

**Использование:**
- Генерация секций — поиск релевантного контекста
- Поиск информации по запросу пользователя

### 3.3: Генерация секций (Generation)

**Сервис:** `GenerationService.generate_section()`

**Процесс:**
1. Получение `template` и `section_contract`
2. Извлечение фактов из Study KB по `required_facts_json`
3. Поиск релевантных chunks через `RetrievalService`
4. Генерация текста через LLM с контекстом
5. Извлечение artifacts (claims, numbers, citations)
6. Валидация через QC Gate
7. Сохранение `generation_run` и `generated_section`

**Хранение:**
- Таблица `generation_runs` — процесс генерации
- Таблица `generated_target_sections` (переименовано из `generated_sections` в миграции 0017):
  - `content_text` — сгенерированный текст
  - `artifacts_json` — структурированные артефакты (claims, citations)
  - `qc_status` — результат валидации
  - `qc_report_json` — отчёт QC

### 3.4: Прослеживаемость (Traceability)

**Цепочка прослеживаемости:**
```
Сгенерированный текст
  ↓ (через artifacts)
Facts из Study KB
  ↓ (через fact_evidence)
Anchors (конкретные места в документе)
  ↓ (через doc_version_id)
Исходный документ и версия
```

**Использование:**
- UI показывает цепочку доказательств
- Клик по утверждению → факт → anchor → исходный документ
- Сравнение версий документов через `change_events`

---

## 4. Структура данных в БД

**Основные таблицы:**

1. `documents` — документы исследования
2. `document_versions` — версии документов с метаданными (добавлено `last_ingestion_run_id` в миграции 0013)
3. `ingestion_runs` — записи о процессах ингестии (статус, метрики, качество, warnings, errors) (миграция 0013)
4. `anchors` — атомарные элементы документа (параграфы, заголовки, ячейки)
5. `chunks` — векторные представления для поиска
6. `anchor_matches` — соответствия между якорями разных версий документа
7. `facts` — факты исследования (Study KB)
8. `fact_evidence` — связь фактов с anchors
9. `conflicts` — обнаруженные противоречия между фактами/документами
10. `study_core_facts` — структурированные основные факты исследования с версионированием
11. `target_section_maps` — маппинг семантических секций на anchors/chunks (переименовано из `section_maps` в миграции 0017)
12. `target_section_contracts` — требования к секциям (target_section, view_key, retrieval_recipe, qc_ruleset) (переименовано из `section_contracts` в миграции 0017)
13. `topics` — семантические топики для группировки контента (расширено в миграциях 0014, 0018)
14. `heading_clusters` — кластеры заголовков (миграция 0014, опционально)
15. `cluster_assignments` — привязка кластеров к топикам для doc_version (расширено в миграции 0015, опционально)
16. `heading_block_topic_assignments` — прямой маппинг блоков заголовков на топики (миграция 0021)
17. `topic_evidence` — агрегированные доказательства для топиков
18. `topic_mapping_runs` — отслеживание запусков маппинга топиков (миграция 0014)
19. `topic_zone_priors` — приоритеты зон по doc_type для топиков (миграция 0018)
20. `zone_sets` — наборы зон по doc_type (миграция 0019)
21. `zone_crosswalk` — кросс-документный маппинг зон (миграция 0019)
22. `generation_runs` — процессы генерации (target_section, view_key)
23. `generated_target_sections` — результаты генерации (переименовано из `generated_sections` в миграции 0017)

**Связи:**
- `anchor_id` — глобальный строковый идентификатор якоря (не UUID)
- `chunk.anchor_ids[]` — массив строковых `anchor_id` в chunk
- `fact_evidence.anchor_id` — доказательство факта (строковый `anchor_id`)
- `target_section_map.anchor_ids[]` и `chunk_ids[]` — маппинг секции (chunk_ids — массив UUID chunk.id)
- `anchor_matches` — соответствия между якорями разных версий (для diff/impact анализа)
- `topic_evidence.anchor_ids[]` и `chunk_ids[]` — доказательства для топиков
- `heading_block_topic_assignments.heading_block_id` — стабильный идентификатор блока заголовка
- `cluster_assignments` — связь кластеров с топиками для doc_version (опционально)
- `ingestion_runs` — отслеживание процессов ингестии, связанных с `document_versions`

---

## 5. Особенности реализации

1. **Идемпотентность:** re-ingest удаляет старые данные и создаёт заново
2. **Детерминированность:** embeddings через feature hashing (без внешних API)
3. **Версионность:** каждая версия документа имеет свои anchors/chunks
4. **Прослеживаемость:** все факты связаны с конкретными местами в документах
5. **Гибридное извлечение:** rules-first для простых фактов, LLM для сложных (в будущем)
6. **Метрики и мониторинг:** каждый процесс ингестии отслеживается через `IngestionRun` с метриками и оценкой качества
7. **Quality Gate:** автоматическая проверка качества ингестии с флагом `needs_review` для проблемных случаев
8. **Topic Mapping:** автоматическая группировка контента по семантическим топикам через heading blocks (только для протоколов)
9. **Стабильность anchor_id:** `anchor_id` не зависит от `section_path` и `ordinal` для устойчивости при изменениях структуры документа
10. **Структура документов:** определяется через templates и target_section_contracts (таблицы taxonomy удалены в миграции 0020)
11. **Anchor Alignment:** автоматическое выравнивание якорей между версиями документа для анализа изменений
12. **Fact Consistency Check:** автоматическая проверка согласованности фактов исследования
13. **LLM-assist:** опциональное использование LLM для улучшения маппинга проблемных секций

---

## 6. Детали форматов данных

### Anchor ID формат

**ВАЖНО:** `section_path` и `ordinal` **НЕ входят** в `anchor_id` для стабильности при переносах между разделами. Они хранятся как отдельные поля для UI/структуры, но не участвуют в идентичности якоря.

**Формат для paragraph-anchors (P/LI/HDR):**
```
{doc_version_id}:{content_type}:{para_index}:{hash(text_norm)}
```

**Формат для footnotes (FN):**
```
{doc_version_id}:fn:{fn_index}:{fn_para_index}:{hash(text_norm)}
```

**Примеры:**
```
# Paragraph anchor
aa0e8400-e29b-41d4-a716-446655440005:p:42:a1b2c3d4e5f6...

# Footnote anchor
aa0e8400-e29b-41d4-a716-446655440005:fn:3:1:a1b2c3d4e5f6...
```

Где:
- `doc_version_id` — UUID версии документа
- `content_type` — тип контента (p/li/hdr)
- `para_index` — порядковый номер параграфа в документе (из location_json)
- `fn_index` — индекс сноски (только для FN)
- `fn_para_index` — порядковый номер параграфа внутри сноски (только для FN)
- `hash(text_norm)` — стабильный хеш нормализованного текста

### Chunk ID формат
```
{doc_version_id}:{section_path}:{ordinal}:{text_hash16}
```

### Section Path
- `ROOT` — корневой уровень
- `__FRONTMATTER__` — титульная страница (до первого реального заголовка)
- `H1/H2/H3` — иерархия заголовков (нормализованная)
- `FOOTNOTES` — секция сносок

### Content Types
- `hdr` — заголовок
- `p` — параграф
- `li` — элемент списка
- `fn` — сноска
- `cell` — ячейка таблицы
- `tbl` — таблица (для будущего использования)

### Source Zones (12 канонических ключей)
- `overview` — обзор, введение, обоснование, синопсис
- `design` — дизайн исследования, методология, рандомизация, ослепление
- `ip` — исследуемый препарат, дозировка, режим доз, IMP
- `statistics` — статистика, план статистики, статистические методы, SAP
- `safety` — безопасность, нежелательные явления, SAE, фармаконадзор
- `endpoints` — конечные точки, эффективность, первичные/вторичные endpoints
- `population` — популяция, критерии включения/исключения, отбор пациентов
- `procedures` — процедуры, визиты, обследования, SoA (Schedule of Activities)
- `data_management` — управление данными, EDC, eCRF, кодирование
- `ethics` — этика, информированное согласие, EC, IRB, регуляторные вопросы
- `admin` — администрирование, мониторинг, аудит, качество, SDV
- `appendix` — приложения, annex
- `unknown` — неклассифицированный контент (по умолчанию)

### Target Sections и Section Contracts

**`target_section`** (переименовано из `section_key` для ясности):
- Используется в `target_section_contracts`, `target_section_maps`, `generation_runs`
- Должен быть одним из 12 канонических ключей (без "unknown")
- Валидируется через `validate_target_section()` в моделях и схемах

**`view_key`** (новое поле):
- Ключ представления для группировки секций в UI
- Позволяет группировать несколько `target_section` под одним представлением

**Section Contracts (`target_section_contracts`, переименована из `section_contracts` в миграции 0017):**
- `target_section` — целевая секция (один из 12 канонических ключей)
- `view_key` — ключ представления для группировки
- `required_facts_json` — обязательные факты из Study KB
- `allowed_sources_json` — допустимые источники
- `retrieval_recipe_json` — правила retrieval:
  - `prefer_source_zones` — приоритетные source_zone для поиска
  - `fallback_source_zones` — резервные source_zone, если prefer пуст
- `qc_ruleset_json` — правила валидации качества
- `citation_policy` — политика цитирования (per_sentence/per_claim/none)

**Section Taxonomy:**
- Таблицы taxonomy (`target_section_taxonomy_nodes`, `target_section_taxonomy_aliases`, `target_section_taxonomy_related`) удалены в миграции 0020. Структура документов определяется через templates и `target_section_contracts`.

---

## 7. Классификация контента: source_zone и topic

### 7.1 Source Zone (зона источника)

**Что мэпится к source_zone:**
- `section_path` (иерархия заголовков документа, например "3.2.1" или "H1/H2/H3")
- `heading_text` (текст текущего заголовка)

**Откуда берутся ключи source_zone:**
- 12 канонических ключей определены в enum `SourceZone` в `backend/app/db/enums.py`:
  - `overview`, `design`, `ip`, `statistics`, `safety`, `endpoints`, `population`, 
    `procedures`, `data_management`, `ethics`, `admin`, `appendix`
  - Дополнительно: `unknown` — для неклассифицированного контента
- Правила классификации хранятся в `backend/app/data/source_zone_rules.yaml`
- Каждое правило содержит regex-паттерны для русского и английского языков

**Как работает классификация:**
1. Для каждого anchor в процессе ингестии вызывается `SourceZoneClassifier.classify()`
2. Классификатор проверяет `section_path` и `heading_text` на соответствие паттернам из YAML
3. Результат: `{zone, confidence, matched_rule_id}`
4. Значение `source_zone` сохраняется в таблице `anchors`
5. Для chunks: `source_zone` определяется как наиболее часто встречающаяся зона среди anchors в chunk

**Где используется source_zone:**
- В таблице `anchors` — классификация каждого якоря документа
- В таблице `chunks` — фильтрация при векторном поиске
- В `target_section_contracts.retrieval_recipe_json` — указание приоритетных зон для поиска (`prefer_source_zones`, `fallback_source_zones`)
- В `topic_mapping` — как prior для маппинга кластеров на топики (source_zone_prior)

### 7.2 Topic (топик)

**Что мэпится к topic:**
- Блоки заголовков (`HeadingBlock`) мэпятся напрямую на топики (`Topic`) через `TopicMappingService`
- Блоки строятся динамически из anchors через `HeadingBlockBuilder`
- Кластеризация опциональна и используется только как prior для маппинга

**Откуда берутся ключи topic:**
- `topic_key` — произвольный строковый ключ (не enum), задаётся при создании топика
- Топики создаются вручную через API или сидером для workspace
- Хранятся в таблице `topics` с полями:
  - `topic_key` — уникальный ключ топика (например, "statistics_analysis", "safety_monitoring")
  - `title_ru`, `title_en` — название топика
  - `topic_profile_json` — профиль топика с aliases, keywords, source_zones, dissimilar_zones, embeddings

**Как работает маппинг на топики:**
1. **Построение heading blocks** (`HeadingBlockBuilder.build_blocks_for_doc_version()`):
   - Группировка anchors: заголовок (HDR) + контент (P/LI) до следующего заголовка
   - Генерация стабильного `heading_block_id` на основе `heading_anchor_id`
   - Определение `source_zone` через `SourceZoneClassifier`

2. **Опциональная кластеризация заголовков** (если включена):
   - Группировка похожих заголовков по embedding similarity
   - Пороги: `threshold=0.22`, `min_size=3`, `embedding_threshold=0.15`
   - Создание `HeadingCluster` с `cluster_id`, `top_titles_json`, `examples_json`, `cluster_embedding`
   - Используется только как prior для маппинга блоков

3. **Маппинг блоков на топики** (`TopicMappingService.map_topics_for_doc_version()`):
   - Для каждого блока вычисляется score против всех активных топиков workspace
   - Score включает:
     - **Heading match** (0.4 веса): точное/нечёткое совпадение заголовков с aliases топика
     - **Keyword match** (0.3 веса): совпадение ключевых слов из `topic_profile_json`
     - **Embedding similarity** (0.3 веса): cosine similarity между блоками и топиками
     - **Source zone prior** (0.3 веса): буст, если `source_zone` блока совпадает с `source_zones` топика, штраф для `dissimilar_zones`
     - **Cluster prior** (опционально): приоритет от кластеризации, если включена
     - **Neighbor bonus**: бонус за соседство с уже замаппленным блоком
   - Создаётся `HeadingBlockTopicAssignment` с `heading_block_id`, `topic_key`, `confidence`, `debug_json`

4. **Построение topic_evidence** (`TopicEvidenceBuilder.build_evidence_for_doc_version()`):
   - Агрегация `anchor_ids[]` и `chunk_ids[]` по `(topic_key, source_zone, language)`
   - Создание `TopicEvidence` с метаданными (top_headings, block_ids, blocks_count)

**Где используется topic:**
- В таблице `heading_block_topic_assignments` — прямая привязка блоков заголовков к топикам для конкретной версии документа
- В таблице `topic_evidence` — агрегированные доказательства для топиков (anchor_ids, chunk_ids)
- В таблице `cluster_assignments` — опциональная привязка кластеров к топикам (если кластеризация включена)
- Для группировки и навигации по контенту документа по семантическим темам

**Примечание:** Topic mapping выполняется только для документов типа `protocol` в рамках шага 2.7 ингестии. Кластеризация опциональна и используется только как prior для маппинга блоков.

### 7.3 Section Contracts (контракты секций)

**Что такое target_section_contracts:**
- Семантические паспорта секций, определяющие требования к структуре и содержанию секций документов
- Универсальные контракты, не привязанные к конкретной структуре документа
- Таблица `target_section_contracts` (переименована из `section_contracts` в миграции 0017)

**Откуда берутся target_section_contracts:**
- Загружаются через сидер из репозитория (`backend/app/scripts/seed_section_contracts.py`)
- Могут создаваться через API (`POST /api/section-contracts`), но в MVP редактирование отключено по умолчанию
- Хранятся в таблице `target_section_contracts` с версионированием (поле `version`)

**Структура target_section_contract:**
- `workspace_id` — рабочее пространство
- `doc_type` — тип документа (protocol, sap, csr, etc.)
- `target_section` — целевая секция (один из 12 канонических ключей, без "unknown")
- `view_key` — ключ представления для группировки секций в UI
- `title` — название секции
- `required_facts_json` — обязательные факты из Study KB, которые должны быть в секции
- `allowed_sources_json` — допустимые источники (doc_type, source_zones, content_types)
- `retrieval_recipe_json` — правила retrieval:
  - `prefer_source_zones` — приоритетные source_zone для поиска контента
  - `fallback_source_zones` — резервные source_zone
  - `signals` — сигналы для маппинга (must_keywords, should_keywords, not_keywords, regex_patterns)
- `qc_ruleset_json` — правила валидации качества (проверки на must_keywords, not_keywords, regex, block_size)
- `citation_policy` — политика цитирования (per_sentence/per_claim/none)
- `version` — версия контракта
- `is_active` — активность контракта

**Где и для какой цели используется target_section_contracts:**

1. **Section Mapping (маппинг секций)** — `SectionMappingService.map_sections()`:
   - Определяет, какие части документа соответствуют семантическим секциям
   - Использует `retrieval_recipe_json.signals` для поиска заголовков-кандидатов
   - Создаёт `SectionMap` — связь `target_section` с `anchor_ids[]` и `chunk_ids[]` для конкретной версии документа
   - Применяется QC через `section_mapping_qc` на основе `qc_ruleset_json`

2. **Generation (генерация секций)** — `GenerationService.generate_section()`:
   - Определяет требования к генерируемой секции
   - Использует `required_facts_json` для извлечения фактов из Study KB
   - Использует `allowed_sources_json` и `retrieval_recipe_json` для поиска релевантных chunks через `RetrievalService`
   - Применяет `citation_policy` для форматирования цитат
   - Валидирует результат через QC на основе `qc_ruleset_json`

3. **Retrieval (поиск контента)** — используется косвенно через `retrieval_recipe_json`:
   - Определяет приоритетные `source_zones` для поиска релевантного контента
   - Фильтрует chunks по допустимым источникам из `allowed_sources_json`

4. **QC (контроль качества)** — `SectionMappingQC.validate_mapping()`:
   - Проверяет соответствие найденных секций требованиям из `qc_ruleset_json`
   - Валидирует наличие обязательных ключевых слов (must_keywords)
   - Проверяет отсутствие запрещённых слов (not_keywords)
   - Применяет regex-паттерны для валидации структуры

**Связь с другими сущностями:**
- `TargetSectionMap` ссылается на `target_section` из `target_section_contracts`
- `GenerationRun` и `GeneratedTargetSection` ссылаются на `contract_id`
- Контракты версионируются: уникальный ключ `(workspace_id, doc_type, target_section, version)`

### 7.4 Section Taxonomy (удалено)

**ПРИМЕЧАНИЕ:** Таблицы taxonomy (`target_section_taxonomy_*`) удалены в миграции 0020. Структура документов определяется через templates и target_section_contracts.

---

## 8. Поток данных при генерации секции

```
1. Пользователь запрашивает генерацию секции
   ↓
2. GenerationService получает section_contract
   ↓
3. Извлечение required_facts из Study KB
   ↓
4. RetrievalService ищет релевантные chunks
   ↓
5. Формирование контекста для LLM:
   - Факты из KB
   - Релевантные chunks
   - Template
   ↓
6. Генерация текста через LLM
   ↓
7. Извлечение artifacts:
   - Verifiable claims
   - Numbers
   - Citations (anchor_ids)
   ↓
8. Валидация через QC Gate
   ↓
9. Сохранение в generated_sections
```

---

Эта схема обеспечивает структурированное хранение и использование данных документов с полной прослеживаемостью от исходного документа до сгенерированного текста.

