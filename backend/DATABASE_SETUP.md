# Настройка базы данных ClinNexus MVP

## Структура файлов

```
backend/
├── alembic/
│   ├── env.py                    # Конфигурация Alembic
│   └── versions/
│       ├── __init__.py
│       ├── 0001_initial_prod_skeleton.py  # Все таблицы + базовые индексы
│       └── 0002_enums_and_vector.py        # ENUM статусы + pgvector + индексы
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

```
{doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash(text_norm)}
```

Пример:
```
aa0e8400-e29b-41d4-a716-446655440005:3.2.1:p:1:hash123
```

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

## Требования

- PostgreSQL 18+
- pgvector extension
- Python 3.12+
- SQLAlchemy 2.0+
- Alembic

