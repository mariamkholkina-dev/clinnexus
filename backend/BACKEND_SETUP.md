# Backend каркас ClinNexus

## Структура проекта

```
backend/app/
├── main.py                 # FastAPI app, CORS, health endpoint
├── core/
│   ├── config.py          # Pydantic settings, env
│   ├── logging.py         # Логгер
│   ├── errors.py          # Единый формат ошибок (code/message/details)
│   └── storage.py         # Локальное хранилище файлов
├── api/
│   ├── deps.py            # DB session dependency
│   └── v1/
│       ├── __init__.py    # Главный роутер
│       ├── studies.py     # Studies endpoints
│       ├── documents.py   # Documents/DocumentVersions endpoints
│       ├── sections.py    # Section contracts/maps endpoints
│       ├── generation.py # Generation endpoints
│       ├── conflicts.py  # Conflicts endpoints
│       └── impact.py      # Impact/Tasks endpoints
├── schemas/               # Pydantic DTO схемы
│   ├── common.py         # BaseResponse, ErrorResponse
│   ├── studies.py        # StudyCreate, StudyOut
│   ├── documents.py      # Document*, DocumentVersion*, UploadResult
│   ├── anchors.py        # AnchorOut, ChunkOut
│   ├── sections.py       # SectionContract*, SectionMap*
│   ├── facts.py          # FactOut, FactEvidenceOut
│   ├── generation.py     # GenerateSectionRequest/Result, QC schemas
│   ├── conflicts.py      # ConflictOut
│   ├── impact.py         # ImpactItemOut
│   └── tasks.py           # TaskOut
├── services/              # Service layer (интерфейсы + stub реализации)
│   ├── ingestion.py      # IngestionService
│   ├── soa_extraction.py # SoAExtractionService
│   ├── section_mapping.py # SectionMappingService
│   ├── section_mapping_assist.py # LLM-assisted mapping
│   ├── section_mapping_qc.py # QC gate для mapping
│   ├── fact_extraction.py # FactExtractionService
│   ├── retrieval.py      # RetrievalService
│   ├── generation.py     # GenerationService, ValidationService
│   ├── diff.py           # DiffService
│   ├── impact.py         # ImpactService
│   └── conflicts.py      # ConflictService
├── db/                    # DB слой (уже существующий)
│   ├── session.py        # Async session factory
│   ├── models/           # ORM модели
│   └── enums.py          # Enum'ы
└── scripts/
    └── seed.py           # Seed script (workspace, user, study, template, contracts)
```

## Запуск backend

### 1. Установка зависимостей

```bash
cd backend
pip install -e .
```

### 2. Настройка окружения

Создайте файл `.env` в `backend/` на основе `example.env`:

```env
APP_ENV=dev
DB_HOST=localhost
DB_PORT=5432
DB_NAME=clinnexus
DB_USER=clinnexus
DB_PASSWORD=clinnexus
```

### 3. Запуск миграций

```bash
cd backend
alembic upgrade head
```

### 4. Запуск seed скрипта

```bash
cd backend
python -m app.scripts.seed
```

### 5. Запуск сервера

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Или через Makefile (если настроен):

```bash
make backend-run
```

## Реализованные эндпойнты

### Health
- `GET /health` - Проверка работоспособности

### Studies
- `POST /api/v1/studies` - Создание исследования
- `GET /api/v1/studies/{study_id}` - Получение исследования
- `GET /api/v1/studies` - Список исследований (с фильтром workspace_id)
- `GET /api/v1/studies/{study_id}/facts` - Список фактов исследования

### Documents
- `POST /api/v1/studies/{study_id}/documents` - Создание документа
- `POST /api/v1/documents/{document_id}/versions` - Создание версии документа
- `POST /api/v1/document-versions/{version_id}/upload` - Загрузка файла
- `POST /api/v1/document-versions/{version_id}/ingest` - Запуск ингестии
- `GET /api/v1/document-versions/{version_id}` - Получение версии документа
- `GET /api/v1/document-versions/{version_id}/anchors` - Список якорей (с фильтрами section_path, content_type)

### Semantic Sections
- `GET /api/v1/section-contracts` - Список контрактов секций (с фильтрами doc_type, is_active)
- `POST /api/v1/section-contracts` - Создание контракта секции
- `GET /api/v1/document-versions/{version_id}/section-maps` - Список маппингов секций
- `POST /api/v1/document-versions/{version_id}/section-maps/{section_key}/override` - Переопределение маппинга

### Generation
- `POST /api/v1/generate/section` - Генерация секции документа

### Conflicts
- `GET /api/v1/conflicts?study_id={study_id}` - Список конфликтов

### Impact/Tasks
- `GET /api/v1/impact?study_id={study_id}` - Список элементов воздействия
- `GET /api/v1/tasks?study_id={study_id}` - Список задач

### Passport Tuning
- `GET /api/v1/passport-tuning/clusters` - Список кластеров заголовков (с пагинацией и поиском)
- `GET /api/v1/passport-tuning/mapping` - Получить текущий mapping (cluster_to_section_key.json)
- `POST /api/v1/passport-tuning/mapping` - Сохранить mapping (с валидацией и нормализацией через taxonomy)
- `GET /api/v1/passport-tuning/mapping/download` - Скачать mapping файл
- `GET /api/v1/passport-tuning/mapping/for_autotune` - Получить mapping для автотюнинга
- `GET /api/v1/passport-tuning/sections?doc_type=...` - Получить дерево taxonomy для doc_type

## Особенности реализации

### 1. Семантические секции
- `section_key` (например `protocol.soa`) - семантический идентификатор, не зависит от структуры документа
- `section_contract` хранит требования секции (facts, источники, qc, citation_policy)
- `section_map` привязывает `section_key` к `anchor_ids`/`chunk_ids` для конкретного `doc_version_id`
- Структура документов определяется через templates и target_section_contracts (таблицы taxonomy удалены в миграции 0020)
- **НЕ хранится** `section_path` внутри `section_contract`
- **НЕ генерируются** чанки "по section_key" вместо структуры документа

### 2. Service Layer
Все сервисы:
- Принимают `db: AsyncSession` через конструктор
- Возвращают структурированные результаты
- Содержат `TODO` вместо реальной LLM/OCR логики
- Логируют ключевые действия

### 3. Ingestion Pipeline
`POST /document-versions/{version_id}/ingest` выполняет:
1. `IngestionService.ingest()` - создание anchors/chunks
2. `SectionMappingService.map_sections()` - маппинг секций
3. `FactExtractionService.extract_and_upsert()` - извлечение фактов

Все шаги выполняются синхронно (для MVP).

### 4. Локальное хранилище
Файлы сохраняются в `backend/.data/uploads/{version_id}/{filename}`.

### 5. Audit Logging
Ключевые действия логируются в `audit_log` через helper метод в сервисах.

## Следующие шаги

1. Реализовать реальную логику ингестии (OCR/PDF парсинг)
2. Реализовать векторный поиск (pgvector)
3. Реализовать LLM интеграцию для генерации
4. Добавить background tasks для асинхронной обработки
5. Добавить аутентификацию и авторизацию

