# Roadmap A — MVP “Картинг” (S0 → MVP)

Цель: как соло-разработчик быстро показать **ценность**:  
1) загрузка документов → **Anchors** (правильно)  
2) **Human-in-the-loop Section Mapping** для 5–10 ключевых секций  
3) извлечение **SoA таблицы** (табличные anchors `cell`)  
4) минимальный **Study KB v1** (несколько фактов)  
5) генерация **одной-двух секций** (one-shot structured JSON) + детерминированный QC  
6) базовые **conflicts/impact/tasks** на основе dependency graph

---

## S0. “Нулевая готовность”
**Outcome:** проект запускается локально, CI минимальный.

- Repo: `apps/web (Next.js+shadcn)`, `apps/api (FastAPI)`, `packages/shared`, `infra/sql`
- База: Postgres + pgvector, миграции работают, seed работает
- OpenAPI генерится и тестируется

**Acceptance**
- `GET /health` OK
- миграции накатываются с нуля
- seed contracts загружается

---

## S1. Anchors v1 (самое важное)
**Outcome:** любой загруженный документ → якоря атомарных элементов.

- Ingest pipeline:
  - upload → `uploaded`
  - parse → `processing`
  - anchors saved → `ready|needs_review|failed`
- Anchor ID: `{doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash(text_norm)}`  
  - `content_type`: `p|cell|fn|hdr|li|tbl`
- Хранить `text_raw`, `location_json`, `confidence`

**Acceptance**
- `GET /api/document-versions/{id}/anchors` возвращает anchors
- Для таблиц есть anchors `cell` (минимум N ячеек)
- Anchor IDs стабильны при повторной загрузке того же файла

---

## S2. Manual Section Mapping (Human-in-the-loop)
**Outcome:** пользователь может замаппить ключевые секции руками.

- UI:
  - список “канонических секций” (section_key) для doc_type
  - слева: документ (viewer) / список заголовков
  - справа: выбранный `section_key` + кнопки “Assign selected anchors”
- API:
  - list section_maps
  - override mapping (`mapped_by=user`, `status=overridden`)
- Минимальный автосаппорт:
  - candidates по ключевым словам из паспорта (optional)

**Acceptance**
- можно замаппить 5–10 секций протокола за 5–10 минут
- section_maps сохраняются и используются дальше

---

## S3. SoA extraction v1 (killer feature)
**Outcome:** система надежно находит **хотя бы одну** SoA таблицу и нормализует базовую структуру.

- Strategy:
  - если есть явный heading (“Schedule of Activities / Расписание процедур”) → искать таблицы рядом
  - выбирать “плотную” таблицу (много `cell`), распознавать header row/col
- Output (минимум):
  - матрица: процедура × визит
  - сохранение в `facts`/`artifacts` (на MVP достаточно JSON-артефакта)

**Acceptance**
- на тестовом протоколе SoA вытаскивается в JSON
- можно в UI показать таблицу из extracted JSON

---

## S4. Study KB v1 (только high-signal)
**Outcome:** извлекаем 10–20 фактов высокого ROI.

Примеры fact_key:
- `study.phase`
- `population.sample_size_total`
- `design.randomized`
- `design.blinding`
- `intervention.drug_name`
- `intervention.dose_regimen`
- `endpoints.primary`
- `endpoints.secondary`
- `soa.visits_count` (из SoA)
- `safety.ae_reporting_window`

Pipeline:
- rules-first (regex/табличные правила) + (опционально) LLM-JSON на слабых местах
- каждый факт хранит evidence anchors

**Acceptance**
- `GET /api/studies/{id}/facts` возвращает факты + evidence
- статус фактов: extracted/validated/needs_review/conflicting

---

## S5. Generation MVP: one-shot structured output + QC
**Outcome:** генерация 1–2 секций целевого документа (например CSR) из протокола/SAP/TFL.

- LLM output **одним вызовом**:
  - `final_text`
  - `artifacts.claims[]` (каждый с `anchor_ids`)
  - `artifacts.citations[]` (или citations внутри claims)
- QC (детерминированный):
  - anchors существуют
  - anchors принадлежат allowed_sources (dependency_sources)
  - числа (если есть) совпадают с Study KB (где применимо)
  - политика цитирования (per_claim)

**Acceptance**
- `POST /api/generate/section` даёт `qc_status=pass|fail|blocked`
- при fail/blocked приходит понятный `qc_report_json`

---

## S6. Conflicts / Impact / Tasks (минимально, но полезно)
**Outcome:** при загрузке новой версии документа → impact items + задачи.

- Dependency graph строится из `dependency_sources` в passport
- Diff на уровне:
  - anchors added/removed/changed
  - факты changed
  - section_maps coverage changed
- Создать:
  - `impact_items[]` с recommended_action
  - `tasks[]` (review_impact, regenerate_section, resolve_conflict)

**Acceptance**
- при новом `document_version` появляются impact items
- UI показывает список задач по study

---

## MVP Definition of Done
- 1 study, 1 protocol, 1 sap, (опц. tfl) загружаются
- anchors извлекаются (вкл. `cell` для таблиц)
- mapping 5–10 секций подтверждается вручную
- SoA извлекается и показывается
- 1–2 секции CSR генерируются с citations + QC
- impact/tasks создаются при обновлении протокола

---

## Что сознательно НЕ делать в MVP
- UI для редактирования паспортов (только seed в репо)
- двухпроходный claim-first (V2)
- тонкая настройка retrieval weights/quotas (кроме `cell` приоритета)
- полная автоматизация section mapping (V2)
