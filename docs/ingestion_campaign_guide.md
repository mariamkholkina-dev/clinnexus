# Руководство по запуску кампании ингестии протоколов

## Runbook TL;DR

**Быстрый чеклист перед запуском:**
1. ✅ БД мигрирована (`cd backend && alembic upgrade head`)
2. ✅ Конфиг `backend/app/data/source_zone_rules.yaml` существует
3. ✅ Документы загружены в БД (таблица `document_versions`)
4. ✅ Выбран dataset (workspace_id, doc_type=protocol, limit)
5. ✅ Запуск: `python -m app.scripts.run_ingestion_campaign --doc-type protocol --limit 5 --output ./campaign_results`
6. ✅ Проверка результатов: `campaign_summary.json` и SQL-запросы из раздела 9

---

## 1. Цель и когда запускать кампанию

### Зачем запускать кампанию на 200+ протоколов?

Кампания ингестии — это массовая обработка документов для:
- **Регрессионного тестирования**: проверка, что изменения в коде не ухудшили качество обработки
- **Оценки покрытия**: проверка, насколько хорошо система обрабатывает разнообразные протоколы
- **Тюнинга правил**: итеративное улучшение правил классификации (source_zone, SoA, факты, маппинг)
- **Загрузки новых документов**: скрипт поддерживает загрузку файлов напрямую через параметры `--upload-file` и `--upload-dir`

### Когда нужно перезапускать кампанию?

Запускайте кампанию после изменений в:
- **`backend/app/data/source_zone_rules.yaml`** — правила классификации заголовков по source_zone
- **Passports seed** (контракты секций) — изменения в `target_section_contracts` через API или seed-скрипт
- **Правила извлечения фактов** — изменения в `app/services/fact_extraction.py` или реестре правил
- **SoA extractor** — эвристики в `app/services/soa_extraction.py`
- **Маппинг секций** — логика в `app/services/section_mapping.py` или taxonomy
- **Параметры chunking** — настройки в `app/services/chunking.py`

---

## 2. Pre-flight checklist (перед запуском)

### 2.1 Миграции БД

Убедитесь, что БД находится на последней версии миграций:

```bash
cd backend
alembic upgrade head
```

Проверка текущей версии:
```bash
alembic current
```

Если есть проблемы с миграциями, см. раздел 10 (Troubleshooting).

### 2.2 Конфигурационные файлы

**Обязательные файлы:**
- `backend/app/data/source_zone_rules.yaml` — правила классификации source_zone

**Проверка наличия:**
```bash
ls -la backend/app/data/source_zone_rules.yaml
```

**Примечание:** Таблицы taxonomy удалены в миграции 0020. Структура документов определяется через templates и `target_section_contracts`.

### 2.3 Хранилище файлов и BYO keys

**Важно**: Кампания ингестии **НЕ использует LLM**, поэтому API ключи не требуются.

**Проверка путей к файлам:**
- Документы должны быть доступны по путям, указанным в `document_versions.source_file_uri`
- По умолчанию файлы хранятся в `backend/.data/uploads/{version_id}/{filename}`
- Убедитесь, что файлы существуют и доступны для чтения

**Проверка одного документа:**
```sql
SELECT id, source_file_uri 
FROM document_versions 
WHERE id = '<version_id>';
```

Затем проверьте, что файл существует:
```bash
# Если source_file_uri = 'file:///path/to/file.docx'
# или относительный путь
ls -la <путь_из_source_file_uri>
```

### 2.4 Embeddings (если используются)

Если в пайплайне используется векторизация chunks:
- Убедитесь, что расширение `pgvector` установлено в PostgreSQL
- Проверьте доступность модели для embeddings (если используется внешний сервис)

**Проверка pgvector:**
```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
```

**Примечание**: В текущей реализации кампания может работать без embeddings (chunks создаются, но векторизация может быть отложена). Кампания ингестии **НЕ использует LLM** для основной обработки, но может использовать LLM-assist для проблемных секций при маппинге (если настроены API ключи и `SECURE_MODE=true`).

### 2.5 Документы для обработки

Убедитесь, что в БД есть `document_versions` для обработки:

```sql
-- Подсчет протоколов, готовых к ингестии
SELECT COUNT(*) 
FROM document_versions dv
JOIN documents d ON dv.document_id = d.id
WHERE d.doc_type = 'protocol'
  AND dv.source_file_uri IS NOT NULL;
```

**Как загрузить документы:**
- Через API: `POST /api/v1/documents/{document_id}/versions` и `POST /api/v1/document-versions/{version_id}/upload`
- Через скрипт кампании: `--upload-file` или `--upload-dir` (см. раздел 4.1)
- Или через seed-скрипт (если есть тестовые данные)

---

## 3. Выбор dataset (200+ протоколов)

### 3.1 Идентификация протоколов в БД

Протоколы имеют `doc_type = 'protocol'` в таблице `documents`:

```sql
-- Список всех протоколов
SELECT d.id, d.name, d.workspace_id, COUNT(dv.id) as versions_count
FROM documents d
LEFT JOIN document_versions dv ON dv.document_id = d.id
WHERE d.doc_type = 'protocol'
GROUP BY d.id, d.name, d.workspace_id
ORDER BY d.name;
```

### 3.2 Выбор по workspace/study

Если нужно обработать только документы из конкретного workspace:

```sql
-- Протоколы в workspace
SELECT d.id, d.name
FROM documents d
WHERE d.doc_type = 'protocol'
  AND d.workspace_id = '<workspace_id>';
```

Используйте `--workspace-id` при запуске кампании (см. раздел 4).

### 3.3 Создание "золотого набора" (golden set)

**Зачем нужен golden set:**
- Быстрая проверка изменений (20-30 документов вместо 200+)
- Ручная валидация результатов
- Регрессионное тестирование

**Как создать golden set:**
1. Выберите 20-30 протоколов, которые покрывают разные случаи:
   - Разные языки (RU, EN, MIXED)
   - Разные структуры (с SoA и без)
   - Разные форматы (DOCX, PDF если поддерживается)
2. Сохраните список `version_id` в файл:

```sql
-- Экспорт списка version_id для golden set
SELECT dv.id
FROM document_versions dv
JOIN documents d ON dv.document_id = d.id
WHERE d.doc_type = 'protocol'
  AND d.workspace_id = '<workspace_id>'
LIMIT 30;
```

3. Используйте эти ID для ограничения выборки (можно модифицировать скрипт или использовать фильтр по `created_at`).

---

## 4. Запуск кампании

### 4.1 Тестовый запуск (один документ)

**Через API (для проверки одного документа):**
```bash
curl -X POST "http://localhost:8000/api/v1/document-versions/<version_id>/ingest"
```

**Через скрипт (dry-run для проверки выборки):**
```bash
cd backend
python -m app.scripts.run_ingestion_campaign \
  --doc-type protocol \
  --limit 1 \
  --dry-run
```

**Загрузка и обработка одного файла:**
```bash
cd backend
python -m app.scripts.run_ingestion_campaign \
  --workspace-id <workspace_uuid> \
  --doc-type protocol \
  --upload-file ./path/to/document.docx \
  --output ./campaign_results
```

**Загрузка и обработка директории с файлами:**
```bash
cd backend
python -m app.scripts.run_ingestion_campaign \
  --workspace-id <workspace_uuid> \
  --doc-type protocol \
  --upload-dir ./documents \
  --output ./campaign_results
```

### 4.2 Минимальный запуск (5 документов)

```bash
cd backend
python -m app.scripts.run_ingestion_campaign \
  --doc-type protocol \
  --limit 5 \
  --output ./campaign_results
```

**Параметры:**
- `--doc-type protocol` — обрабатывать только протоколы
- `--limit 5` — максимум 5 документов
- `--output ./campaign_results` — сохранить результаты в директорию

### 4.3 Полный запуск (200+ протоколов)

```bash
cd backend
python -m app.scripts.run_ingestion_campaign \
  --doc-type protocol \
  --output ./campaign_results_$(date +%Y%m%d_%H%M%S)
```

**Без `--limit`** обработаются все протоколы в БД.

**С фильтром по workspace:**
```bash
python -m app.scripts.run_ingestion_campaign \
  --workspace-id <workspace_uuid> \
  --doc-type protocol \
  --output ./campaign_results
```

**С фильтром по дате:**
```bash
python -m app.scripts.run_ingestion_campaign \
  --doc-type protocol \
  --since 2024-01-01 \
  --output ./campaign_results
```

### 4.4 Параметры скрипта

**Полный список параметров:**
- `--workspace-id <uuid>` — фильтр по workspace (обязателен при загрузке файлов, опционален при обработке существующих документов)
- `--doc-type <type>` — тип документа: `protocol`, `sap`, `any` (по умолчанию `protocol`)
- `--limit <n>` — максимальное количество документов (опционально)
- `--since <YYYY-MM-DD>` — фильтр по дате создания версии (опционально)
- `--dry-run` — режим проверки без реальной ингестии
- `--concurrency <n>` — количество параллельных задач (по умолчанию 1, **не рекомендуется менять** из-за рисков блокировок БД)
- `--output <path>` — директория для сохранения отчетов (опционально, если не указано — вывод в консоль)
- `--upload-file <path>` — путь к файлу для загрузки (DOCX, PDF, XLSX). При указании этого параметра файл будет загружен в БД и затем обработан
- `--upload-dir <path>` — путь к директории с файлами для загрузки (рекурсивный поиск). Поддерживаемые форматы: DOCX, PDF, XLSX
- `--study-code <code>` — код исследования для создаваемых исследований (опционально, по умолчанию генерируется автоматически)

### 4.5 Ожидаемое время выполнения

**Качественные оценки:**
- **Один документ**: 10-60 секунд (зависит от размера DOCX, количества таблиц, сложности структуры)
- **5 документов**: 1-5 минут
- **200+ документов**: 1-4 часа (зависит от размера документов и нагрузки на БД)

**Факторы, влияющие на время:**
- Размер DOCX файла (количество страниц, таблиц, заголовков)
- Количество anchors (больше anchors = больше времени на обработку)
- Наличие SoA таблиц (дополнительное время на извлечение)
- Нагрузка на БД (если БД используется другими процессами)
- Использование LLM-assist для проблемных секций (если настроено)
- Topic mapping (только для протоколов, добавляет время обработки)

---

## 5. Где хранятся результаты

### 5.1 База данных

#### Таблица `ingestion_runs`

Каждый запуск ингестии создает запись в `ingestion_runs`:

```sql
-- Последний запуск для каждого doc_version
SELECT 
    ir.id,
    ir.doc_version_id,
    ir.status,
    ir.started_at,
    ir.finished_at,
    ir.duration_ms,
    ir.pipeline_version,
    ir.pipeline_config_hash
FROM ingestion_runs ir
WHERE ir.doc_version_id = '<version_id>'
ORDER BY ir.started_at DESC
LIMIT 1;
```

**Ключевые поля:**
- `summary_json` — метрики ингестии (anchors, chunks, soa, facts, section_maps)
- `quality_json` — результаты QualityGate (флаги, scores, needs_review)
- `warnings_json` — список предупреждений
- `errors_json` — список ошибок
- `pipeline_config_hash` — хеш конфигураций (для сравнения кампаний)

#### Таблица `document_versions`

Зеркалирование результатов:
- `ingestion_summary_json` — копия `summary_json` из последнего `ingestion_run`
- `last_ingestion_run_id` — ссылка на последний успешный запуск

```sql
-- Просмотр summary для версии документа
SELECT 
    dv.id,
    dv.ingestion_summary_json->>'anchors' as anchors_summary,
    dv.ingestion_summary_json->>'soa' as soa_summary,
    dv.last_ingestion_run_id
FROM document_versions dv
WHERE dv.id = '<version_id>';
```

### 5.2 Файлы результатов кампании

Если указан `--output`, скрипт создает два файла:

**`campaign_summary.json`** — агрегированная статистика:
```json
{
  "campaign_started_at": "2024-01-15T10:00:00",
  "total_docs": 200,
  "ok": 195,
  "failed": 5,
  "needs_review": 45,
  "soa_found_rate": 0.92,
  "avg_unknown_rate": 0.08,
  "unknown_rate_above_10pct": 30,
  "unknown_rate_above_25pct": 5,
  "avg_mapping_coverage": 0.85,
  "mapping_coverage_below_75pct": 25,
  "top_warnings": {...},
  "top_errors": {...},
  "section_failures": {...}
}
```

**`campaign_details.csv`** — детали по каждому документу:
- `doc_version_id` — ID версии документа
- `status` — `ok` или `failed`
- `needs_review` — требуется ли ручная проверка
- `unknown_rate` — доля unknown source_zone
- `soa_found` — найден ли SoA
- `mapping_coverage` — покрытие маппинга секций
- `facts_total` — общее количество фактов
- `missing_required_count` — количество отсутствующих обязательных фактов
- `conflicting_count` — количество конфликтующих фактов
- `duration_ms` — время обработки в миллисекундах
- `error` — текст ошибки (если status = failed)

**Путь по умолчанию:**
- Если `--output` не указан, результаты выводятся в консоль
- Если указан относительный путь (например, `./campaign_results`), создается директория относительно текущей рабочей директории

---

## 6. Оценка результатов (что считается "хорошим")

### 6.1 Ключевые метрики и пороги

#### Unknown source_zone rate

**Пороги:**
- **< 10%** — отлично
- **10-25%** — приемлемо, но стоит улучшить правила
- **> 25%** — требует внимания, нужно тюнить `source_zone_rules.yaml`

**Где смотреть:**
- `summary_json.anchors.unknown_rate`
- `campaign_summary.avg_unknown_rate`

#### SoA found rate

**Ожидания:**
- **> 90%** для протоколов — хорошо
- **< 90%** — нужно улучшить SoA extractor

**Дополнительные проверки SoA:**
- `matrix_density` (плотность матрицы) — должна быть > 2%
- `visits_count` — минимум 4 визита
- `procedures_count` — минимум 5 процедур

**Где смотреть:**
- `summary_json.soa.found`
- `summary_json.soa.matrix_density`
- `campaign_summary.soa_found_rate`

#### Mapping coverage rate

**Пороги:**
- **> 75%** — хорошо (9+ из 12 целевых секций)
- **50-75%** — приемлемо, но стоит улучшить
- **< 50%** — требует внимания

**Ожидаемые 12 секций для протокола:**
1. `protocol.synopsis` (overview)
2. `protocol.study_design` (design)
3. `protocol.ip` (ip)
4. `protocol.endpoints` (endpoints)
5. `protocol.population` (population)
6. `protocol.procedures` (procedures)
7. `protocol.soa` (procedures)
8. `protocol.statistics` (statistics)
9. `protocol.safety` (safety)
10. `protocol.data_management` (data_management)
11. `protocol.ethics` (ethics)
12. `protocol.admin` (admin)

**Где смотреть:**
- `summary_json.section_maps.coverage_rate`
- `campaign_summary.avg_mapping_coverage`

#### Facts missing/conflicting

**Обязательные факты для протокола:**
- `protocol_meta.protocol_version`
- `population.planned_n_total`

**Пороги:**
- **0 missing** — отлично
- **1 missing** — предупреждение
- **≥ 2 missing** — требует внимания

**Конфликты:**
- **0 conflicting** — отлично
- **≥ 1 conflicting** — требует ручной проверки

**Где смотреть:**
- `summary_json.facts.missing_required`
- `summary_json.facts.conflicting_count`

#### Parse sanity checks

**Проверки парсинга:**
- `anchors.total` — должно быть > 1000 для типичного протокола
- `anchors.by_content_type.hdr` — должно быть > 20 заголовков
- Если меньше — возможна проблема с парсингом DOCX

**Где смотреть:**
- `summary_json.anchors.total`
- `summary_json.anchors.by_content_type`

### 6.2 Интерпретация флагов

#### `needs_review = true`

**Что это значит:**
Документ требует ручной проверки из-за одного или нескольких проблем:
- Высокий unknown source_zone (> 25%)
- SoA не найден или подозрительный
- Низкое покрытие маппинга (< 75%)
- Отсутствуют обязательные факты (≥ 2)
- Есть конфликтующие факты
- Подозрительный парсинг (мало anchors/заголовков)

**Что делать:**
1. Проверить `quality_json.flags` для определения конкретной проблемы
2. Исправить проблему в коде/конфиге (см. раздел 7)
3. Перезапустить ингестию для этого документа

#### `ok` но есть warnings

**Что это значит:**
Ингестия прошла успешно, но есть предупреждения (например, повышенный unknown_rate 10-25%, отсутствует 1 обязательный факт).

**Что делать:**
- Если warnings не критичны — можно игнорировать
- Если warnings повторяются в многих документах — стоит улучшить правила (см. раздел 7)

---

## 7. Triage workflow (что исправлять на основе метрик)

### 7.1 Высокий unknown source_zone (> 25%)

**Симптомы:**
- `summary_json.anchors.unknown_rate > 0.25`
- `quality_json.flags.high_unknown_source_zone = true`
- В `campaign_summary` много документов с `unknown_rate_above_25pct`

**Вероятные причины:**
- В `source_zone_rules.yaml` нет правил для часто встречающихся заголовков
- Regex-паттерны слишком строгие
- Заголовки на другом языке (RU vs EN)

**Где исправить:**
- **Файл**: `backend/app/data/source_zone_rules.yaml`
- **Добавить правила** для неизвестных заголовков (см. `summary_json.anchors.top_unknown_headings`)

**Как валидировать:**
1. Добавить правила в `source_zone_rules.yaml`
2. Запустить кампанию на golden set (20-30 документов)
3. Проверить, что `unknown_rate` снизился
4. Проверить, что новые правила не сломали существующие (регрессия)

**Пример добавления правила:**
```yaml
source_zones:
  - zone: "design"
    patterns:
      ru:
        - "(?i).*новый.*паттерн.*"  # Добавить новый паттерн
```

### 7.2 SoA missing/suspicious

**Симптомы:**
- `summary_json.soa.found = false` для протоколов
- `summary_json.soa.matrix_density < 0.02`
- `summary_json.soa.visits_count < 4` или `procedures_count < 5`
- `quality_json.flags.soa_missing = true` или `soa_suspicious = true`

**Вероятные причины:**
- SoA таблица имеет нестандартную структуру
- Эвристики в `SoAExtractionService` не распознают таблицу
- Таблица находится в неожиданном месте документа

**Где исправить:**
- **Файл**: `backend/app/services/soa_extraction.py`
- **Методы**: `_score_table_as_soa()`, `_extract_soa_from_table()`
- **Параметры**: пороги для confidence, минимальное количество визитов/процедур

**Как валидировать:**
1. Найти проблемный документ в БД
2. Посмотреть структуру таблицы (через debug-логи или экспорт anchors)
3. Добавить/улучшить эвристики в `soa_extraction.py`
4. Перезапустить ингестию для этого документа
5. Проверить, что SoA найден и метрики улучшились

**Добавление примеров:**
- Сохранить примеры проблемных таблиц в тестовые данные
- Добавить unit-тесты в `backend/tests/test_soa_extraction.py`

### 7.3 Низкое покрытие маппинга (< 75%)

**Симптомы:**
- `summary_json.section_maps.coverage_rate < 0.75`
- `quality_json.flags.low_mapping_coverage = true`
- В `campaign_summary` много документов с `mapping_coverage_below_75pct`

**Вероятные причины:**
- В `target_section_contracts` нет правил для часто встречающихся заголовков
- Правила prefer/fallback zones в `SectionMappingService` не работают
- Заголовки не распознаются из-за проблем с source_zone классификацией

**Где исправить:**
- **Section Contracts**: через API `/api/v1/section-contracts` или seed-скрипт `seed_section_contracts.py` (обновить `retrieval_recipe_json.signals`)
- **Mapping service**: `backend/app/services/section_mapping.py` (улучшить эвристики)
- **Passport tuning**: через API `/api/v1/passport-tuning/mapping` (ручной маппинг кластеров)
- **Таблица**: `target_section_contracts` (переименована из `section_contracts` в миграции 0017)

**Как валидировать:**
1. Проверить `summary_json.section_maps.per_target_section` для проблемных секций
2. Найти заголовки, которые не маппятся (через `anchors` с нужным `source_zone`)
3. Обновить правила в `target_section_contracts` или улучшить правила маппинга
4. Перезапустить ингестию на golden set
5. Проверить улучшение `coverage_rate`

### 7.4 Факты missing

**Симптомы:**
- `summary_json.facts.missing_required` содержит обязательные факты
- `quality_json.flags.facts_missing_required = true`

**Вероятные причины:**
- В `FactExtractionService` нет правил для извлечения этих фактов
- Regex-паттерны не соответствуют формату в документах
- Факты находятся в неожиданных местах (например, в таблицах, а не в тексте)

**Где исправить:**
- **Файл**: `backend/app/services/fact_extraction.py` — логика применения правил и реестр правил извлечения фактов
- **Добавить regex-паттерны** в функцию `get_extraction_rules()` или соответствующий метод в `fact_extraction.py`

**Как валидировать:**
1. Найти документ, где факт отсутствует
2. Найти в документе, где должен быть факт (через anchors/chunks)
3. Добавить правило извлечения в `fact_extraction.py`
4. Перезапустить ингестию для этого документа
5. Проверить, что факт извлечен

**Пример добавления правила:**
```python
# В backend/app/services/fact_extraction.py, в соответствующем методе
# Добавьте правило извлечения для нового паттерна
```

### 7.5 Много failed runs

**Симптомы:**
- В `campaign_summary` высокий `failed` count
- В `campaign_details.csv` много строк со `status = "failed"`
- В `ingestion_runs.errors_json` повторяющиеся ошибки

**Вероятные причины:**
- Ошибки парсинга DOCX (поврежденные файлы, нестандартный формат)
- Проблемы с памятью (очень большие документы)
- Таймауты (долгая обработка)
- Ошибки в коде (баги в обработке edge cases)

**Где исправить:**
- **Парсинг DOCX**: `backend/app/services/ingestion/docx_ingestor.py`
- **Обработка ошибок**: добавить try-catch и более детальные логи
- **Таймауты**: увеличить лимиты времени (если применимо)

**Как валидировать:**
1. Посмотреть `errors_json` в `ingestion_runs` для failed документов
2. Воспроизвести ошибку на одном документе (запустить ингестию с debug-логами)
3. Исправить проблему в коде
4. Перезапустить ингестию для проблемных документов
5. Проверить, что ошибки исправлены

**Debug-логи:**
```bash
# Установить уровень логирования
export LOG_LEVEL=DEBUG
python -m app.scripts.run_ingestion_campaign --limit 1 --doc-type protocol
```

---

## 8. Регрессионная дисциплина

### 8.1 Сравнение двух кампаний

**По pipeline_config_hash:**
Каждая кампания сохраняет `pipeline_config_hash` в `ingestion_runs`. Это позволяет сравнивать результаты до и после изменений конфигураций.

```sql
-- Сравнение метрик по config_hash
SELECT 
    ir.pipeline_config_hash,
    COUNT(*) as runs_count,
    AVG((ir.summary_json->>'anchors')::json->>'unknown_rate')::float as avg_unknown_rate,
    AVG((ir.summary_json->>'section_maps')::json->>'coverage_rate')::float as avg_coverage
FROM ingestion_runs ir
WHERE ir.status = 'ok'
GROUP BY ir.pipeline_config_hash
ORDER BY ir.pipeline_config_hash;
```

**Before/after delta:**
1. Запустить кампанию до изменений, сохранить `campaign_summary.json` как `baseline_summary.json`
2. Внести изменения в код/конфиг
3. Запустить кампанию после изменений, сохранить как `new_summary.json`
4. Сравнить ключевые метрики:
   - `avg_unknown_rate` — не должен увеличиться
   - `avg_mapping_coverage` — не должен уменьшиться
   - `soa_found_rate` — не должен уменьшиться
   - `failed` count — не должен увеличиться

**Скрипт для сравнения (пример):**
```python
import json

baseline = json.load(open("baseline_summary.json"))
new = json.load(open("new_summary.json"))

print(f"Unknown rate: {baseline['avg_unknown_rate']:.2%} -> {new['avg_unknown_rate']:.2%}")
print(f"Mapping coverage: {baseline['avg_mapping_coverage']:.2%} -> {new['avg_mapping_coverage']:.2%}")
print(f"SoA found rate: {baseline['soa_found_rate']:.2%} -> {new['soa_found_rate']:.2%}")
```

### 8.2 Предотвращение регрессий

**Golden set:**
- Поддерживайте фиксированный набор из 20-30 документов
- После каждого изменения запускайте кампанию на golden set
- Убедитесь, что метрики не ухудшились

**Unit-тесты:**
- Добавляйте тесты для новых правил в `backend/tests/`
- Например, `test_source_zone_classifier.py` для правил source_zone
- `test_soa_extraction.py` для SoA extractor

**Рекомендации по проверкам:**
- Автоматизировать проверку "no worse than baseline" в CI/CD (если есть)
- Или создать скрипт, который сравнивает результаты и выдает предупреждения

---

## 9. Quick SQL / queries cookbook

### 9.1 Документы с needs_review

```sql
-- Список документов, требующих проверки
SELECT 
    d.name,
    dv.version_label,
    ir.quality_json->>'needs_review' as needs_review,
    ir.quality_json->'flags' as flags,
    ir.started_at
FROM ingestion_runs ir
JOIN document_versions dv ON ir.doc_version_id = dv.id
JOIN documents d ON dv.document_id = d.id
WHERE ir.status = 'ok'
  AND (ir.quality_json->>'needs_review')::boolean = true
ORDER BY ir.started_at DESC;
```

### 9.2 Топ unknown заголовков

```sql
-- Топ-20 неизвестных заголовков (если хранятся в summary_json)
SELECT 
    jsonb_array_elements(ir.summary_json->'anchors'->'top_unknown_headings') as heading_info
FROM ingestion_runs ir
WHERE ir.status = 'ok'
  AND ir.summary_json->'anchors'->'top_unknown_headings' IS NOT NULL
ORDER BY (heading_info->>'count')::int DESC
LIMIT 20;
```

**Альтернатива (через таблицу anchors):**
```sql
-- Топ unknown заголовков из таблицы anchors
SELECT 
    a.section_path,
    COUNT(*) as count
FROM anchors a
JOIN document_versions dv ON a.doc_version_id = dv.id
JOIN documents d ON dv.document_id = d.id
WHERE a.source_zone = 'unknown'
  AND a.content_type = 'hdr'
  AND d.doc_type = 'protocol'
GROUP BY a.section_path
ORDER BY count DESC
LIMIT 20;
```

### 9.3 SoA found vs not found

```sql
-- Статистика по SoA
SELECT 
    CASE 
        WHEN (ir.summary_json->'soa'->>'found')::boolean THEN 'found'
        ELSE 'not_found'
    END as soa_status,
    COUNT(*) as count,
    AVG((ir.summary_json->'soa'->>'matrix_density')::float) as avg_density
FROM ingestion_runs ir
JOIN document_versions dv ON ir.doc_version_id = dv.id
JOIN documents d ON dv.document_id = d.id
WHERE ir.status = 'ok'
  AND d.doc_type = 'protocol'
GROUP BY soa_status;
```

### 9.4 Распределение покрытия маппинга

```sql
-- Распределение coverage_rate
SELECT 
    CASE 
        WHEN coverage < 0.5 THEN '< 50%'
        WHEN coverage < 0.75 THEN '50-75%'
        WHEN coverage < 0.9 THEN '75-90%'
        ELSE '>= 90%'
    END as coverage_bucket,
    COUNT(*) as count
FROM (
    SELECT 
        (ir.summary_json->'section_maps'->>'coverage_rate')::float as coverage
    FROM ingestion_runs ir
    JOIN document_versions dv ON ir.doc_version_id = dv.id
    JOIN documents d ON dv.document_id = d.id
    WHERE ir.status = 'ok'
      AND d.doc_type = 'protocol'
) subq
GROUP BY coverage_bucket
ORDER BY coverage_bucket;
```

### 9.5 Факты missing required

```sql
-- Документы с отсутствующими обязательными фактами
SELECT 
    d.name,
    dv.version_label,
    jsonb_array_elements_text(ir.summary_json->'facts'->'missing_required') as missing_fact,
    ir.started_at
FROM ingestion_runs ir
JOIN document_versions dv ON ir.doc_version_id = dv.id
JOIN documents d ON dv.document_id = d.id
WHERE ir.status = 'ok'
  AND jsonb_array_length(ir.summary_json->'facts'->'missing_required') > 0
ORDER BY ir.started_at DESC;
```

### 9.6 Последние 20 failed runs с ошибками

```sql
-- Последние failed runs
SELECT 
    d.name,
    dv.version_label,
    ir.errors_json,
    ir.started_at,
    ir.duration_ms
FROM ingestion_runs ir
JOIN document_versions dv ON ir.doc_version_id = dv.id
JOIN documents d ON dv.document_id = d.id
WHERE ir.status = 'failed'
ORDER BY ir.started_at DESC
LIMIT 20;
```

### 9.7 Сравнение метрик между кампаниями

```sql
-- Сравнение по pipeline_config_hash
SELECT 
    ir.pipeline_config_hash,
    COUNT(*) as runs,
    AVG((ir.summary_json->'anchors'->>'unknown_rate')::float) as avg_unknown_rate,
    AVG((ir.summary_json->'section_maps'->>'coverage_rate')::float) as avg_coverage,
    SUM(CASE WHEN (ir.quality_json->>'needs_review')::boolean THEN 1 ELSE 0 END) as needs_review_count
FROM ingestion_runs ir
WHERE ir.status = 'ok'
GROUP BY ir.pipeline_config_hash
ORDER BY MAX(ir.started_at) DESC;
```

---

## 10. Appendix: Troubleshooting

### 10.1 Проблемы с подключением к БД

**Ошибка**: `could not connect to server` или `password authentication failed`

**Решение:**
1. Проверьте переменные окружения в `.env`:
   ```bash
   cat backend/.env | grep DB_
   ```
2. Убедитесь, что PostgreSQL запущен:
   ```bash
   # Linux/Mac
   sudo systemctl status postgresql
   
   # Windows (через Services)
   # Или через Docker
   docker ps | grep postgres
   ```
3. Проверьте подключение вручную:
   ```bash
   psql -h localhost -U clinnexus -d clinnexus
   ```

### 10.2 Проблемы с миграциями

**Ошибка**: `Target database is not up to date` или конфликты миграций

**Решение:**
1. Проверьте текущую версию:
   ```bash
   cd backend
   alembic current
   ```
2. Посмотрите историю миграций:
   ```bash
   alembic history
   ```
3. Если нужно откатиться:
   ```bash
   alembic downgrade -1  # Откатить на одну версию
   ```
4. Если миграции сломаны, может потребоваться ручное исправление (см. `backend/DATABASE_SETUP.md`)

### 10.3 Отсутствуют конфигурационные файлы

**Ошибка**: `FileNotFoundError: source_zone_rules.yaml` или подобное

**Решение:**
1. Проверьте наличие файлов:
   ```bash
   ls -la backend/app/data/source_zone_rules.yaml
   ```
2. Если файлы отсутствуют, восстановите из git:
   ```bash
   git checkout backend/app/data/source_zone_rules.yaml
   ```
3. Или создайте минимальные версии (см. примеры в репозитории)

### 10.4 Файлы документов не найдены

**Ошибка**: `FileNotFoundError: <путь_к_файлу>`

**Решение:**
1. Проверьте `source_file_uri` в БД:
   ```sql
   SELECT id, source_file_uri FROM document_versions WHERE id = '<version_id>';
   ```
2. Проверьте, что файл существует по указанному пути:
   ```bash
   ls -la <путь_из_source_file_uri>
   ```
3. Если файл перемещен, обновите `source_file_uri`:
   ```sql
   UPDATE document_versions 
   SET source_file_uri = 'file:///новый/путь/к/файлу.docx'
   WHERE id = '<version_id>';
   ```

### 10.5 Идемпотентность перезапуска

**Вопрос**: Можно ли безопасно перезапустить ингестию для документа?

**Ответ**: Да, ингестия идемпотентна:
- При запуске с `force=True` (по умолчанию в кампании) удаляются существующие anchors, chunks, facts для этого `doc_version_id`
- Затем создаются новые данные
- `ingestion_runs` сохраняет историю всех запусков

**Как перезапустить один документ:**
```bash
# Через API
curl -X POST "http://localhost:8000/api/v1/document-versions/<version_id>/ingest"
```

Или модифицировать скрипт кампании для обработки конкретного `version_id`.

### 10.6 Debug одного проблемного документа

**Включение debug-логов:**
```bash
export LOG_LEVEL=DEBUG
python -m app.scripts.run_ingestion_campaign --limit 1 --doc-type protocol
```

**Просмотр anchors/chunks для документа:**
```sql
-- Anchors для документа
SELECT 
    a.anchor_id,
    a.section_path,
    a.content_type,
    a.source_zone,
    LEFT(a.text_norm, 100) as text_preview
FROM anchors a
WHERE a.doc_version_id = '<version_id>'
ORDER BY a.ordinal
LIMIT 50;

-- Chunks для документа
SELECT 
    c.chunk_id,
    c.section_path,
    c.source_zone,
    c.token_estimate,
    array_length(c.anchor_ids, 1) as anchors_count
FROM chunks c
WHERE c.doc_version_id = '<version_id>'
ORDER BY c.ordinal
LIMIT 20;
```

**Экспорт summary_json для анализа:**
```sql
-- Полный summary_json
SELECT 
    ir.summary_json
FROM ingestion_runs ir
WHERE ir.doc_version_id = '<version_id>'
ORDER BY ir.started_at DESC
LIMIT 1;
```

Сохраните результат в файл и проанализируйте структуру метрик.

### 10.7 Проблемы с производительностью

**Медленная обработка:**
- Уменьшите `--concurrency` (по умолчанию 1, не рекомендуется увеличивать)
- Проверьте нагрузку на БД (другие процессы)
- Оптимизируйте запросы к БД (индексы на `doc_version_id`, `anchor_id` и т.д.)

**Нехватка памяти:**
- Обрабатывайте документы меньшими батчами (`--limit`)
- Увеличьте память для Python процесса (если возможно)

---

## Примечания

- **Текущие ограничения**: Скрипт обрабатывает документы последовательно (`concurrency=1`) для безопасности. Параллельная обработка может привести к блокировкам БД.
- **Отсутствующие функции**: Если в коде отсутствует какая-то функциональность, упомянутая в руководстве, это указано в соответствующих разделах. Предложения по реализации можно добавить в issues репозитория.

---

**Последнее обновление**: 2024-12-19  
**Версия скрипта**: `backend/app/scripts/run_ingestion_campaign.py`

