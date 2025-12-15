## ClinNexus MVP Scope

### Входит в v1.0

- Базовые сущности и таблицы:
  - Workspace / User / Membership (RBAC-скелет).
  - Study, Document, DocumentVersion.
  - Anchor, Chunk (pgvector), Study KB Facts, fact_evidence.
  - Templates, SectionContracts.
  - GenerationRun, GeneratedSection.
  - Conflicts, conflict_items.
  - ChangeEvents, ImpactItems, Tasks.
  - AuditLog.
- Backend:
  - FastAPI-приложение со слоями `api/services/db/schemas/core`.
  - Эндпоинты-заглушки:
    - `POST /studies`
    - `POST /studies/{study_id}/documents`
    - `POST /document_versions/{version_id}/upload`
    - `POST /document_versions/{version_id}/ingest`
    - `GET /document_versions/{version_id}/anchors`
    - `GET /studies/{study_id}/facts`
    - `POST /generate/section`
    - `GET /conflicts?study_id=...`
    - `GET /impact?study_id=...`
  - Интерфейсы сервисов (ingestion, extraction, retrieval, generation, validation, diff, impact, conflict) с заглушками.
- DB / DevOps:
  - Alembic с первой миграцией и pgvector.
  - docker-compose: Postgres+pgvector, backend, frontend.
  - Makefile-команды: `dev`, `migrate`, `seed`.
- Frontend:
  - Next.js (App Router, TypeScript).
  - Страницы: `/studies`, `/studies/[id]`, `/documents/[versionId]`, `/kb/[studyId]`,
    `/copilot/[studyId]`, `/conflicts/[studyId]`, `/impact/[studyId]`.
- Документация:
  - ARCHITECTURE, SCHEMAS, MVP_SCOPE.

### Не входит в v1.0 (явные non-goals)

- Реальная аутентификация и управление сессиями (будет Supabase).
- OCR, парсинг PDF/DOCX, сложные пайплайны ingestion.
- Настоящие LLM-вызовы для генерации/экстракции/валидации.
- UI для управления шаблонами и контрактами секций.
- Полноценный RBAC с проверкой ролей на каждом эндпоинте.
- Продвинутая observability (tracing, метрики, дашборды).


