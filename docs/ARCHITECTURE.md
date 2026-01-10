## Архитектура ClinNexus MVP (Соответствие USR 4.1)

### Ключевые сущности

- **Workspace / User / Membership**: многотенантность и RBAC. Роль: **Medical Writer**.
- **Study**: исследование. Хранит глобальные параметры (терапия, популяция, дизайн) как "Источник истины".
- **Document / DocumentVersion**:
  - Типы: `protocol`, `ib` (брошюра), `csr` (отчет), `icf` (согласие).
  - Приоритет: Протокол является мастер-документом (USR-1.6).
- **Anchor (Якорь)**:
  - Реализует USR-102 (Цифровой двойник).
  - Уникальный ID (`anchor_id`) для абзацев, ячеек таблиц, сносок.
  - `hash16`: обеспечивает стабильность ссылок при редактировании.
- **Fact Store (База фактов)**:
  - Реализует USR-201, USR-202.
  - Атрибуты: `scope` (Arm/Group), `data_type`, `criticality` (GxP), `transformability` (для ICF), `temporal` (визиты).
  - Таксономия: Административные, Дизайн, Популяция, Терапия, **БЭ-специфика** (Washout, Sampling points, Analytes), Безопасность.
- **AuditIssue (Результат аудита)**:
  - Новая сущность для USR-3xx и USR-4xx.
  - Поля: `severity` (Critical, Major, Minor), `category` (Consistency, Grammar, Logic, Terminology), `description`, `location_anchors` (список якорей), `status` (Open, Suppressed, Resolved), `suppression_reason`.
- **TerminologyDictionary (Терминологический словарь)**:
  - Для USR-302. Хранит утвержденные варианты написания для исследования (препарат, популяция).
- **ChangeLogItem (Элемент перечня изменений)**:
  - Для USR-602.
  - Поля: `section_name`, `old_value`, `new_value`, `justification`, `change_type`.

### Слои backend

- `app/api/v1`:
  - `audits.py` — запуск проверок (внутридокументных и кросс-документных), получение списка AuditIssues.
  - `export.py` — выгрузка документов (DOCX) и отчетов (Перечень изменений).
  - `dictionaries.py` — управление терминологией исследования.
  - *Существующие:* `studies.py`, `documents.py`, `generation.py`, `conflicts.py`, `impact.py`.

- `app/services`:
  - **Ingestion (USR-101, 102):**
    - `DocxIngestor`: расширенная поддержка структур GCP ЕАЭС для Протокола, БИ, CSR, ICF.
    - `NarrativeIndexer`: построение векторного индекса для "Layman Translation" (USR-501) и семантического поиска.
  
  - **Auditing (Новый модуль, USR-3xx, 4xx):**
    - `StyleAuditService`: проверка орфографии, грамматики, научного стиля (USR-301).
    - `TerminologyGuardService`: контроль единообразия терминов (USR-302).
    - `ProtocolLogicAuditor`:
      - `ConsistencyCheck`: Синопсис vs Теекст (USR-303).
      - `CalendarLogicCheck`: Visit Windows, Таблица vs Текст (USR-304).
      - `TraceabilityCheck`: Цель -> Точка -> Метод (USR-305).
    - `BioequivalenceAuditor`: специфика БЭ (Washout vs T1/2, плотность точек забора) (USR-306).
    - `CrossDocConsistencyService`: сверка Протокола с БИ, ICF, CSR (USR-401).

  - **Extraction:**
    - `FactExtractionService`: обновлен для извлечения параметров БЭ (USR-202).
  
  - **Generation (USR-5xx):**
    - `IcfGenerator`: использует `LaymanTransformer` (LLM-based упрощение терминов через Narrative Index) (USR-501).
    - `CsrGenerator`: автозаполнение разделов 1–10 данными Протокола (USR-502).
      - **TenseTransformer Logic**: Ключевой компонент для конвертации грамматического времени. Трансформирует нарратив из **будущего времени** (стиль Протокола: *"Визиты будут проводиться..."*) в **прошедшее время** (стиль Отчета: *"Визиты проводились..."*).
    - `DocxAssembler`: сборка финального DOCX с сохранением корпоративных стилей (USR-701).

  - **Change Management (USR-6xx):**
    - `SemanticDiffService`: сравнение версий текста и фактов (USR-601).
    - `ChangelogGenerator`: формирование таблицы "Было -> Стало -> Обоснование" (USR-602).

### Пайплайны

- **Ingestion & Validation (USR-101, 103)**
  1. Загрузка DOCX -> Парсинг структуры (Протокол/БИ/CSR/ICF).
  2. Создание "Цифрового двойника" (Anchors + Facts).
  3. **UI Validation**: Пользователь подтверждает корректность разбора зон и ключевых фактов перед аудитом.

- **Audit & Consistency Loop (USR-3xx, 4xx)**
  1. Пользователь запускает Аудит для версии документа.
  2. Параллельный запуск аудиторов:
     - `StyleAuditService` (Spellcheck/Style).
     - `LogicAuditor` (Internal Logic, BE-specifics).
     - `TerminologyGuard`.
  3. Если есть связанные документы -> запуск `CrossDocConsistencyService` (Protocol vs IB/ICF).
  4. Генерация записей `AuditIssue`.
  5. Пользователь в UI просматривает проблемы, исправляет текст или делает `Suppression` (подавление с обоснованием).

- **Generation Pipeline (USR-5xx)**
  1. **ICF**: Выбор разделов Протокола -> `LaymanTransformer` (упрощение) -> Генерация DOCX.
  2. **CSR (Clinical Study Report)**: 
     - Извлечение релевантных секций Протокола (Методология, Дизайн, Популяция).
     - **Tense Shift (Future -> Past)**: Применение `TenseTransformer` для изменения времени глаголов (planning -> reporting style).
     - Маппинг на структуру CSR (GCP ЕАЭС) -> Заполнение секций 1-10.
  3. **Export**: `DocxAssembler` применяет стили и собирает файл.

- **Change Management (USR-6xx)**
  1. Загрузка новой версии Протокола (V2).
  2. `AnchorAligner` выравнивает V1 и V2.
  3. `SemanticDiffService` детектирует изменения.
  4. `ChangelogGenerator` создает черновик "Перечня изменений".
  5. `ImpactService` помечает связанные разделы в ICF/CSR как требующие обновления (Outdated).

### Системные требования (USR-8)
- **Performance**: Оптимизация `IngestionService` и `AuditService` для обработки 300 стр. < 10 мин.
- **Audit Trail**: Все действия (загрузка, редактирование фактов, подавление ошибок, выгрузка) пишутся в `AuditLog`.
