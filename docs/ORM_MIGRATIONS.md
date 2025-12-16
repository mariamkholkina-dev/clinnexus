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

## Миграции: два alembic-конфига

В репозитории есть **две** папки Alembic:

1) **`backend/alembic` + `backend/alembic.ini`** — основной сценарий для локального запуска из `backend/`.

2) **`db/alembic` + `db/alembic.ini`** — сценарий для запуска миграций в Docker (см. `Makefile`).

Обе схемы миграций используют одну и ту же `Base.metadata`, т.е. фактически описывают одну и ту же БД, но с разными путями запуска/URL хоста БД.

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
  - `alembic -c /app/db/alembic.ini upgrade head`

Это удобно, когда вы поднимаете всё через:

```bash
docker-compose up --build
```

И хотите применить миграции в окружении, где `DB_HOST=db` (как в `docker-compose.yml`).

## Где лежат миграции в backend

Основные миграции находятся в:

- `backend/alembic/versions/`

В `backend/DATABASE_SETUP.md` есть доп. контекст по структуре таблиц, enum и pgvector.

## Частые ошибки и диагностика

- **`alembic revision --autogenerate` создаёт “пустую” миграцию**:
  - Проверьте, что `env.py` импортирует модели (иначе metadata будет пустой).
  - Убедитесь, что вы запускаете Alembic из правильного места (`backend/` для `backend/alembic.ini`).
- **Разные URL для БД**:
  - Локально обычно `DB_HOST=localhost`
  - В Docker — `DB_HOST=db`
  - `backend/alembic/env.py` подставляет URL из `settings.sync_database_url` (т.е. из `.env`/env vars).


