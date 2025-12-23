# Настройка базы данных ClinNexus MVP

## Структура файлов

```
backend/
├── alembic/
│   ├── env.py                    # Конфигурация Alembic
│   └── versions/
│       ├── __init__.py
│       ├── 0001_initial_prod_skeleton.py  # Все таблицы + базовые индексы
│       ├── 0002_enums_and_vector.py        # ENUM статусы + pgvector + индексы
│       ├── 0003_make_document_version_file_fields_nullable.py  # source_file_uri и source_sha256 nullable
│       ├── 0004_add_document_language.py  # document_language enum и поле в document_versions
│       ├── 0005_unique_fact_evidence.py   # Уникальный индекс на fact_evidence
│       └── 0006_add_section_taxonomy.py   # Таблицы section taxonomy (nodes, aliases, related)
├── alembic.ini                   # Конфигурация Alembic
├── app/
│   └── db/
│       ├── base.py               # Базовый класс моделей
│       ├── session.py            # Async сессии SQLAlchemy
│       ├── enums.py              # Python Enum типы
│       └── models/               # ORM модели
│           ├── __init__.py
│           ├── auth.py           # workspaces, users, memberships
│           ├── studies.py         # studies, documents, document_versions
│           ├── anchors.py         # anchors, chunks
│           ├── sections.py       # section_contracts, section_maps
│           ├── taxonomy.py       # section_taxonomy_nodes, section_taxonomy_aliases, section_taxonomy_related
│           ├── facts.py          # facts, fact_evidence
│           ├── generation.py     # templates, model_configs, generation_runs, generated_sections
│           ├── conflicts.py      # conflicts, conflict_items
│           ├── change.py         # change_events, impact_items, tasks
│           └── audit.py           # audit_log
└── examples_data.sql             # Примеры данных для тестирования
```

## Команды для применения миграций

### 1. Применить все миграции

```bash
cd backend
alembic upgrade head
```

### 2. Проверка в psql

```bash
# Подключиться к БД
psql -h localhost -U clinnexus -d clinnexus

# Проверить расширения
\dx

# Проверить типы ENUM
\dT+

# Проверить индексы
\di

# Проверить таблицы
\dt
```

### 3. Откат миграций (если нужно)

```bash
# Откатить последнюю миграцию
alembic downgrade -1

# Откатить все миграции
alembic downgrade base
```

## Ключевые особенности архитектуры

### section_path vs section_key

**section_path** — путь по текущей структуре документа (из заголовков/стилей):
- Используется в `anchors` и `chunks` для навигации по документу
- Может меняться при обновлении структуры документа
- Пример: `"3.2.1"` означает "Раздел 3, подраздел 2, пункт 1"

**section_key** — семантический ключ секции (универсальный):
- Используется в `section_contracts` и `section_maps`
- Не зависит от структуры документа
- Пример: `"protocol.soa"` означает "Schedule of Activities" в протоколе

### Привязка семантических секций к документам

- `section_contracts` описывает требования к секции и **НЕ хранит** `section_path`
- `section_maps` привязывает `section_key` к конкретным `anchor_ids`/`chunk_ids` конкретной версии документа

### anchor_id формат

Для paragraph-anchors (P/LI/HDR):
```
{doc_version_id}:{content_type}:{para_index}:{hash(text_norm)}
```

Для footnotes (FN):
```
{doc_version_id}:fn:{fn_index}:{fn_para_index}:{hash(text_norm)}
```

Для cell-anchors (CELL):
```
{doc_version_id}:cell:{table_index}:{row_idx}:{col_idx}:{hash(text_norm)}
```

Примеры:
```
# Paragraph anchor
aa0e8400-e29b-41d4-a716-446655440005:p:42:hash123

# Footnote anchor
aa0e8400-e29b-41d4-a716-446655440005:fn:1:2:hash456
```

ВАЖНО: `section_path` и `ordinal` НЕ входят в `anchor_id` для стабильности при переносах между разделами.

## Примеры данных

См. файл `examples_data.sql` для примеров:
- `section_contract` для `protocol.soa`
- `section_map` для конкретного `doc_version_id`
- Рабочие примеры всех основных таблиц

## Технические детали

### PostgreSQL ENUM типы

Все статусы используют нативные PostgreSQL ENUM:
- `ingestion_status`: uploaded, processing, ready, needs_review, failed
- `fact_status`: extracted, validated, conflicting, tbd, needs_review
- `generation_status`: queued, running, blocked, completed, failed
- `qc_status`: pass, fail, blocked
- `conflict_status`: open, investigating, resolved, accepted_risk, suppressed
- `severity`: low, medium, high, critical
- `task_status`: open, in_progress, done, cancelled
- `task_type`: review_extraction, resolve_conflict, review_impact, regenerate_section
- `section_map_status`: mapped, needs_review, overridden
- `mapped_by`: system, user
- `citation_policy`: per_sentence, per_claim, none
- `document_language`: ru, en, mixed, unknown

### pgvector

- Расширение `vector` включено в миграции
- `chunks.embedding` имеет тип `vector(1536)`
- Индекс по embeddings:
  - Приоритет: HNSW с `vector_cosine_ops`
  - Fallback: IVFFLAT с `vector_cosine_ops`

### Индексы

**Базовые индексы (0001):**
- `anchors`: unique(anchor_id), index(doc_version_id, section_path), index(doc_version_id, content_type)
- `chunks`: unique(chunk_id), index(doc_version_id, section_path)
- `facts`: unique(study_id, fact_type, fact_key), index(study_id, fact_type)
- `audit_log`: index(workspace_id, created_at desc), index(entity_type, entity_id)

**Дополнительные индексы по статусам (0002):**
- `document_versions(ingestion_status)`
- `facts(status)`
- `generation_runs(status)`
- `generated_sections(qc_status)`
- `conflicts(status)`, `conflicts(severity)`
- `tasks(status)`, `tasks(type)`
- `section_maps(status)`

**Миграция 0003 (nullable file fields):**
- `document_versions.source_file_uri` и `document_versions.source_sha256` теперь nullable (версия может быть создана до загрузки файла)

**Миграция 0004 (document_language):**
- Добавлен enum `document_language` (ru, en, mixed, unknown)
- Добавлено поле `document_versions.document_language` (NOT NULL, default 'unknown')
- Автодетект языка при upload DOCX

**Миграция 0005 (unique fact_evidence):**
- Уникальный индекс `uq_fact_evidence_fact_anchor_role` на `(fact_id, anchor_id, evidence_role)` для предотвращения дубликатов

**Миграция 0006 (section taxonomy):**
- `section_taxonomy_nodes`: иерархия секций (doc_type, section_key, title_ru, parent_section_key, is_narrow, expected_content)
  - Уникальность: `(doc_type, section_key)`
  - Индекс: `(doc_type, parent_section_key)`
- `section_taxonomy_aliases`: алиасы секций (doc_type, alias_key, canonical_key, reason)
  - Уникальность: `(doc_type, alias_key)`
  - Индекс: `(doc_type, canonical_key)`
- `section_taxonomy_related`: связанные секции (doc_type, a_section_key, b_section_key, reason)
  - Уникальность: `(doc_type, a_section_key, b_section_key)` (лексикографически нормализовано)
  - Индексы: `(doc_type, a_section_key)`, `(doc_type, b_section_key)`

## Требования

- PostgreSQL 18+
- pgvector extension
- Python 3.12+
- SQLAlchemy 2.0+
- Alembic

