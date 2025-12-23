# MVP v1.0: ClinNexus — Evidence-first Copilot для клинических документов

## 0) Цель MVP (одной фразой)

Сделать систему, которая на одном исследовании умеет:

1. **генерировать 1–2 секции** на основе источников *с кликабельными доказательствами*
2. **находить ключевые несоответствия** между документами
3. **обновлять затронутые секции** при загрузке новой версии протокола
   …и всё это завязано на **Anchors + Study KB + QC Gate**.

---

# 1) MVP Scope (включая SoA)

## 1.1 Поддерживаемые документы (обязательные)

* **Protocol v1/v2** (DOCX как приоритет; PDF digital как вторично)
* **SAP** (PDF или DOCX — минимум для нескольких фактов; можно “light”)
* **TFL** (опционально в v1.0 — можно заменить на “табличные outputs” как PDF)

## 1.2 Обязательная сложная таблица в MVP

* **Schedule of Activities (SoA)** из протокола:

  * извлечение таблицы SoA в структурный формат
  * создание anchors уровня **ячейки** (`content_type=cell`) + связанной мета-информации (visit/procedure/window)
  * минимум: корректно распознавать “визиты × процедуры” и отмечать где стоит X / timepoint / window

> Это “killer feature” MVP: конкурентам часто не хватает таблиц — ты сразу выигрываешь.

## 1.3 Что исключаем из MVP (чтобы не утонуть)

* OCR для сканов (только пометка **Needs Review**, без обещаний качества)
* Полная автоматизация CSR (только 1–2 секции как demo)
* Семантический triage несоответствий (можно как “beta”, но не core)
* Интеграции EDC/CTMS, e-sign

---

# 2) Выбранный end-to-end сценарий MVP (сквозной)

**Scenario A: Protocol v1 → генерация секции + QC → Protocol v2 → impact + update**

1. Загружаешь Protocol v1 (DOCX)
2. Ingestion создаёт:

   * anchors (paragraph/cell/footnote)
   * chunks для Narrative Index
   * SoA table extraction (структурно)
3. Extractor наполняет Study KB v1 (факты + evidence anchors)
4. В редакторе генерируешь **секцию “Schedule/Study Design”**:

   * facts-injection из KB
   * narrative draft + citations
   * QC Gate (citations + numeric/term checks)
5. Загружаешь Protocol v2
6. Diff facts + SoA → Impact report: какие секции затронуты
7. Система предлагает авто-патч (факты) или регенерацию (нарратив) + redline

---

# 3) Study KB v1 (минимальный, но “сильный” набор фактов)

### 3.1 Facts v1 (15–30 элементов)

Обязательные группы:

**A) Версии и метаданные**

* `protocol_version`
* `amendment_id / amendment_date` (если есть)
* `study_phase` (если извлекается)
* `study_design_summary` (коротко; может быть TBD)

**B) Arms / Treatment**

* `treatment_regimen` (dose, route, frequency)
* `treatment_duration` (если явно)

**C) Populations / Definitions**

* `population_itt_definition`
* `population_pp_definition`
* `population_safety_definition`

**D) Endpoints**

* `endpoint_primary` (может быть список)
* `endpoint_secondary` (список)

**E) SoA / Visits (ключ MVP)**

* `visit_list` (V1…Vn + названия)
* `visit_windows` (window per visit, если есть)
* `procedure_list` (процедуры/оценки)
* `soa_matrix` (visit × procedure → mark/notes)
* `soa_notes` (легенда: “X”, “if applicable”, “±”, и т.п.)

**F) Sample size (если доступно)**

* `planned_n_total` (+ units)
* `planned_n_per_arm` (если есть)

### 3.2 Статусы фактов (сразу, prod-friendly)

`extracted | validated | conflicting | tbd | needs_review`

---

# 4) QC Gate v1 (минимум, который “держит качество”)

QC Gate должен быть **обязательным** для “publishable” вставки в документ.

**Проверки v1:**

1. **Citation required** (policy per sentence или per claim — задаётся контрактом секции)
2. **Evidence exists and renderable**: каждый `anchor_id` открывается в UI (paragraph/cell)
3. **Numeric parity**: если в claim есть число — оно совпадает с evidence (включая n/N, %)
4. **Allowed sources**: citations только из разрешённых doc_type/version (контракт секции)
5. **Terminology guard (минимум):** ключевые термины/названия endpoints должны совпадать с KB

---

# 5) Несоответствия v1 (структурные правила, без “магии”)

MVP включает **structured conflict detection** по KB:

* разные значения `protocol_version` / `amendment_date` в документах
* mismatch определений POP (Protocol vs SAP, если извлекли)
* mismatch primary endpoint naming/definition
* mismatch planned N (Protocol vs SAP/TFL если есть)
* mismatch SoA: изменение окна визита/процедуры между v1 и v2

UI:

* dashboard конфликтов
* side-by-side evidence (paragraph vs paragraph, cell vs cell)

---

# 6) Change management v1 (diff → impact → patch/regenerate)

**Diff v1:**

* факт-дифф (старое→новое в KB)
* SoA-дифф (изменившиеся visits/procedures/cells)
* anchors mapping по similarity (внутренний)

**Impact v1:**

* по provenance: если секция ссылалась на факт/anchor, который изменился → секция “affected”

**Обновление v1:**

* facts-injection → auto patch
* narrative blocks → regenerate draft + redline
* пользователь принимает/отклоняет (audit)

---

# 7) Прод-готовый скелет (БД + статусы + сервисные интерфейсы)

Ниже — MVP использует **подмножество** прод-скелета, но таблицы и контракты уже “как в проде”.

## 7.1 Таблицы БД (MVP-минимум, но prod-friendly)

Обязательные:

**workspaces, users, memberships**
**studies**
**documents, document_versions**
**anchors** *(с content_type и location_json)*
**chunks** *(pgvector)*
**facts, fact_evidence**
**templates, target_section_contracts** (переименовано из `section_contracts` в миграции 0017)
**generation_runs, generated_sections**
**conflicts, conflict_items**
**change_events, impact_items, tasks**
**audit_log**
**model_configs**

## 7.2 Статусы (enum)

* ingestion: `uploaded|processing|ready|needs_review|failed`
* fact: `extracted|validated|conflicting|tbd|needs_review`
* generation: `queued|running|blocked|completed|failed`
* qc: `pass|fail|blocked`
* conflict: `open|investigating|resolved|accepted_risk|suppressed`
* task: `open|in_progress|done|cancelled`

## 7.3 “Замороженные” интерфейсы сервисов (чтобы не ломать ядро)

**IngestionService**

* `ingest(doc_version_id) -> IngestionResult`
  Создаёт anchors + chunks + SoA extraction summary.

**SoAExtractionService**

* `extract_soa(doc_version_id) -> SoAResult`
  Возвращает структурную SoA + anchor_ids клеток/заголовков.

**FactExtractionService**

* `extract(doc_version_id) -> FactExtractionResult (JSON schema)`
* `upsert_to_kb(study_id, result) -> changed_fact_ids`

**RetrievalService**

* `retrieve(query, filters, k) -> chunks`

**GenerationService**

* `generate_section(request) -> content + artifacts + input_snapshot`

**ValidationService (QC Gate)**

* `validate(content, artifacts, contract_id) -> QCReport`

**DiffService**

* `diff_versions(from_version_id, to_version_id) -> DiffResult`

**ImpactService**

* `compute_impact(change_event_id) -> ImpactItems` *(по provenance)*

**ConflictService**

* `detect_structured(study_id) -> conflict_ids`

---

# 8) UI MVP (минимум экранов, максимум “вау”)

1. **Study Dashboard**: документы, версии, статусы, задачи
2. **Document Viewer**: исходник + переход по anchors; SoA view (таблица с подсветкой cell anchors)
3. **Study KB Viewer**: факты + статус + evidence links
4. **Section Editor (Co-pilot)**: generate → QC report → publish; traceability click-through
5. **Conflicts Dashboard**: список + side-by-side evidence
6. **Impact & Updates**: список затронутых секций + redline + apply/reject

---

# 9) Как выглядит “Definition of Done” для MVP

MVP считается готовым, если на golden dataset (1 study, protocol v1/v2):

* SoA надёжно извлекается (клетки кликабельны, структура корректна)
* Study KB заполняется минимум по SoA/visits/procedures + версии + 2–3 ещё факта
* Генерация 1 секции проходит QC Gate (citations + numbers/terms)
* При загрузке v2 появляется impact report и предлагается обновление с redline
* Конфликты по KB отображаются и открываются side-by-side

