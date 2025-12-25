# ORM и миграции (SQLAlchemy + Alembic)

Этот документ описывает, как в ClinNexus устроены:

- **ORM слой** (SQLAlchemy 2.0, async)
- **Миграции** (Alembic) — два сценария запуска: локально и через Docker

## ORM: где что лежит

- **База и URL**: `backend/app/core/config.py`
  - `settings.sync_database_url` — URL для синхронных операций (нужен Alembic’у).
  - `settings.async_database_url` — URL для async-движка приложения.
- **Declarative Base и конвенции имён**: `backend/app/db/base.py`
  - `Base` (SQLAlchemy 2.0 `DeclarativeBase`)
  - `metadata` с `naming_convention` (важно для предсказуемых имён constraint’ов в миграциях)
  - `UUIDMixin`, `TimestampMixin` — стандартные миксины моделей
- **Async engine / session factory**: `backend/app/db/session.py`
  - `engine = create_async_engine(settings.async_database_url, ...)`
  - `async_session_factory = async_sessionmaker(...)`
- **FastAPI dependency**: `backend/app/api/deps.py`
  - `get_db()` отдаёт `AsyncSession` для эндпойнтов
- **Модели**: `backend/app/db/models/`
  - Сгруппированы по доменам (auth/studies/anchors/sections/facts/…)
  - `backend/app/db/models/__init__.py` импортирует все модели в пакет

### Практический паттерн использования сессии

- В эндпойнтах обычно инжектится `db: AsyncSession = Depends(get_db)`
- В сервисах сессия передаётся явным параметром (или через конструктор), чтобы слой был тестируемым.

## Миграции: единый alembic-конфиг

В репозитории используется единый каталог Alembic:

**`backend/alembic` + `backend/alembic.ini`** — основной каталог для миграций, используется как локально, так и в Docker.

## Как Alembic “видит” модели (важно для autogenerate)

В `env.py` миграций сделано следующее:

- `target_metadata = Base.metadata`
- отдельным импортом подтягиваются все модели, чтобы они зарегистрировались в `Base.metadata`
  - пример: `from app.db import models  # noqa: F401`

Без этого Alembic при `--autogenerate` может “не увидеть” таблицы и сформировать неверный diff.

## Локальный workflow (без Docker)

### 1) Настроить `.env`

Файл ожидается в `backend/.env` (см. `backend/app/core/config.py`).
Минимальный набор:

- `DB_HOST=localhost`
- `DB_PORT=5432`
- `DB_NAME=clinnexus`
- `DB_USER=clinnexus`
- `DB_PASSWORD=clinnexus`

### 2) Применить миграции

Запуск из папки `backend/`:

```bash
cd backend
alembic upgrade head
```

### 3) Создать новую миграцию (autogenerate)

Запуск из `backend/`:

```bash
cd backend
alembic revision --autogenerate -m "short_message"
```

Рекомендации:

- Всегда проверяйте сгенерированный файл миграции руками.
- Если меняете типы/enum/индексы — особенно внимательно смотрите на `op.alter_column`, `op.execute`, `op.create_index`.

## Docker workflow (через `make`)

В `Makefile` есть цель:

- `make migrate` → вызывает Alembic внутри контейнера backend:
  - `alembic -c /app/alembic.ini upgrade head`

Это удобно, когда вы поднимаете всё через:

```bash
docker-compose up --build
```

И хотите применить миграции в окружении, где `DB_HOST=db` (как в `docker-compose.yml`).

## Где лежат миграции в backend

Основные миграции находятся в:

- `backend/alembic/versions/`
  - `0001_initial_prod_skeleton.py` - Все таблицы + базовые индексы
  - `0002_enums_and_vector.py` - ENUM статусы + pgvector + индексы
  - `0003_make_document_version_file_fields_nullable.py` - source_file_uri и source_sha256 nullable
  - `0004_add_document_language.py` - document_language enum и поле в document_versions
  - `0005_unique_fact_evidence.py` - Уникальный индекс на fact_evidence
  - `0006_add_section_taxonomy.py` - Таблицы section taxonomy (nodes, aliases, related)
  - `0007_rename_section_key_to_target_section.py` - Переименование section_key → target_section, добавление view_key, source_zone, language
  - `0008_add_topics_tables.py` - Таблицы topics, cluster_assignments, topic_evidence
  - `0009_add_anchor_matches.py` - Таблица anchor_matches для выравнивания якорей между версиями
  - `0010_add_study_core_facts.py` - Таблица study_core_facts для структурированных основных фактов исследования
  - `0011_add_fact_metadata_fields.py` - Добавление полей метаданных в таблицу facts (confidence, extractor_version, meta_json)
  - `0012_add_source_zone_enum_and_standardize_sections.py` - Стандартизация 12 основных секций:
    - Создание ENUM `source_zone` с 12 каноническими ключами + "unknown"
    - Обновление `anchors.source_zone` и `chunks.source_zone` на ENUM
    - Маппинг старых значений на канонические
    - Добавление индексов `(doc_version_id, source_zone)` для быстрого поиска
  - `0013_add_ingestion_runs.py` - Добавление таблицы `ingestion_runs` для отслеживания запусков ингестии:
    - Таблица с метриками, качеством и предупреждениями
    - Добавление `last_ingestion_run_id` в `document_versions`
    - Индексы для быстрого поиска
  - `0014_extend_topics_production_quality.py` - Расширение поддержки topics для production-качества:
    - В `topics`: `topic_profile_json` (JSONB), `is_active` (BOOLEAN), `topic_embedding` (VECTOR(1536))
    - Таблица `heading_clusters` для хранения кластеров заголовков
    - Таблица `topic_mapping_runs` для отслеживания запусков маппинга топиков
    - Индексы для оптимизации запросов
  - `0015_add_mapping_debug_json_to_cluster_assignments.py` - Добавление поля `mapping_debug_json` в `cluster_assignments` для хранения debug-информации о маппинге
  - `0016_add_topic_indexes_and_constraints.py` - Добавление недостающих индексов и ограничений:
    - Составной индекс на `(workspace_id, is_active)` в `topics`
    - Индекс на `doc_version_id` в `cluster_assignments`
    - Check constraint для `mapped_by` в `cluster_assignments`
    - Обновление `topic_mapping_runs` с `pipeline_version` и `pipeline_config_hash`
  - `0017_rename_section_tables_to_target_section.py` - Переименование таблиц OUTPUT sections:
    - `section_contracts` → `target_section_contracts`
    - `section_maps` → `target_section_maps`
    - `section_taxonomy_nodes` → `target_section_taxonomy_nodes`
    - `section_taxonomy_aliases` → `target_section_taxonomy_aliases`
    - `section_taxonomy_related` → `target_section_taxonomy_related`
    - `generated_sections` → `generated_target_sections`
  - `0018_add_topic_doc_type_profiles_and_priors.py` - Добавление поддержки doc_type профилей и zone priors:
    - В `topics`: `applicable_to_json` (JSONB) - список doc_type, к которым применим топик
    - Таблица `topic_zone_priors` для хранения приоритетов зон по doc_type для топиков
  - `0019_add_zone_sets_and_crosswalk.py` - Добавление таблиц для кросс-документного связывания:
    - Таблица `zone_sets`: doc_type → список zone_key
    - Таблица `zone_crosswalk`: маппинг между зонами разных doc_types с весами
  - `0020_drop_target_section_taxonomy_tables.py` - Удаление таблиц taxonomy:
    - Удаляет таблицы `target_section_taxonomy_nodes`, `target_section_taxonomy_aliases`, `target_section_taxonomy_related`
    - Также удаляет legacy таблицы `section_taxonomy_*` если они еще существуют
    - Структура документов теперь определяется через templates и `target_section_contracts`
  - `0021_add_heading_block_topic_assignments.py` - Добавление таблицы для прямого маппинга блоков заголовков на топики:
    - Таблица `heading_block_topic_assignments`: привязка `heading_block_id` к `topic_key` для doc_version
    - Блоки строятся динамически из anchors через `HeadingBlockBuilder`, `heading_block_id` — стабильный идентификатор блока
    - Уникальный индекс на `(doc_version_id, heading_block_id)`
    - Индексы для быстрого поиска по `doc_version_id` и `topic_key`
    - Используется `TopicMappingService` для создания маппингов и `TopicEvidenceBuilder` для построения доказательств

В `backend/DATABASE_SETUP.md` есть доп. контекст по структуре таблиц, enum и pgvector.

## Частые ошибки и диагностика

- **`alembic revision --autogenerate` создаёт “пустую” миграцию**:
  - Проверьте, что `env.py` импортирует модели (иначе metadata будет пустой).
  - Убедитесь, что вы запускаете Alembic из правильного места (`backend/` для `backend/alembic.ini`).
- **Разные URL для БД**:
  - Локально обычно `DB_HOST=localhost`
  - В Docker — `DB_HOST=db`
  - `backend/alembic/env.py` подставляет URL из `settings.sync_database_url` (т.е. из `.env`/env vars).


