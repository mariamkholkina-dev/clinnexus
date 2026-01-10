# Стандартизация 12 основных секций

## Обзор

В ClinNexus используется стандартизированный набор из **12 канонических ключей** для `source_zone` и `target_section`, соответствующих требованиям **Правил GCP ЕАЭС (Решение Совета ЕЭК от 03.11.2016 №79, Раздел 6)**.

1. `overview` — обзор, введение, обоснование, синопсис
2. `design` — дизайн исследования, методология, рандомизация, ослепление
3. `ip` — исследуемый препарат, дозировка, режим доз, IMP
4. `statistics` — статистика, план статистики, статистические методы, SAP
5. `safety` — безопасность, нежелательные явления, SAE, фармаконадзор
6. `endpoints` — конечные точки, эффективность, первичные/вторичные endpoints
7. `population` — популяция, критерии включения/исключения, отбор пациентов
8. `procedures` — процедуры, визиты, обследования, SoA (Schedule of Activities)
9. `data_management` — управление данными, EDC, eCRF, кодирование
10. `ethics` — этика, информированное согласие, EC, IRB, регуляторные вопросы
11. `admin` — администрирование, мониторинг, аудит, качество, SDV
12. `appendix` — приложения, annex

Дополнительно:
- `unknown` — неклассифицированный контент (используется только для `source_zone`, не для `target_section`)

**Реализация**: Канонические ключи определены в `SourceZone` enum (`app.db.enums.SourceZone`) и используются через константы в `app.core.section_standardization.CANONICAL_SECTION_KEYS`.

## Маппинг ЕАЭС (Решение № 79) → ClinNexus (12 Zones)

Структура разделов протокола клинического исследования согласно **Правилам GCP ЕАЭС (Решение Совета ЕЭК от 03.11.2016 №79, Раздел 6)** маппится на 12 канонических зон ClinNexus следующим образом:

| ЕАЭС (Решение № 79, Раздел 6) | ClinNexus Zone | Описание |
|------------------------------|----------------|----------|
| 6.1. Общая информация, Обоснование, Цели и задачи исследования | `overview` | Обзор, введение, обоснование, синопсис, общая информация |
| 6.2. Дизайн исследования, Методология, Рандомизация, Ослепление | `design` | Дизайн исследования, методология, рандомизация, ослепление |
| 6.3. Отбор субъектов исследования, Критерии включения/исключения | `population` | Популяция, критерии включения/исключения, отбор пациентов |
| 6.4. Лечение субъектов исследования, Исследуемый препарат (IMP) | `ip` | Исследуемый препарат, дозировка, режим доз, IMP |
| 6.5. Конечные точки, Критерии эффективности, Переменные эффективности | `endpoints` | Конечные точки, эффективность, первичные/вторичные endpoints |
| 6.6. Оценка эффективности, График визитов, Процедуры, Schedule of Activities (SoA) | `procedures` | Процедуры, визиты, обследования, SoA (Schedule of Activities) |
| 6.7. Оценка безопасности, Нежелательные явления, Фармаконадзор | `safety` | Безопасность, нежелательные явления, SAE, фармаконадзор |
| 6.8. Статистика, Статистические методы, Размер выборки, План статистики (SAP) | `statistics` | Статистика, план статистики, статистические методы, SAP |
| 6.9. Работа с данными, Управление данными, Сбор и хранение записей | `data_management` | Управление данными, EDC, eCRF, кодирование, хранение записей |
| 6.10. Этика, Этические аспекты, Информированное согласие, EC/IRB | `ethics` | Этика, информированное согласие, EC, IRB, регуляторные вопросы |
| 6.11. Администрирование, Контроль качества, Мониторинг, Аудит, Финансирование, Страхование | `admin` | Администрирование, мониторинг, аудит, качество, SDV |
| 6.12. Приложения, Annex | `appendix` | Приложения, annex, дополнительные материалы |

**Примечания**:
- Маппинг основан на стандартной структуре протокола КИ согласно Правилам GCP ЕАЭС
- Нумерация разделов (6.1-6.12) соответствует Разделу 6 Решения № 79
- Правила классификации `source_zone` для протоколов загружаются из `backend/app/data/source_zones/rules_protocol.yaml`
- Зоны валидируются через `ZoneSetRegistry` на соответствие разрешённым зонам для типа документа `protocol`

## Source Zone vs Target Section

### Source Zone
- **Где используется**: `anchors.source_zone`, `chunks.source_zone`
- **Тип**: ENUM `source_zone` (12 ключей + "unknown")
- **Назначение**: классификация контента по источнику в исходном документе
- **Определение**: автоматически через `SourceZoneClassifier` на основе `doc_type`, `section_path`, `heading_text` и `language`
- **Правила**: загружаются из `backend/app/data/source_zones/rules_{doc_type}.yaml` (зависит от типа документа)

### Target Section
- **Где используется**: 
  - `target_section_contracts.target_section` (таблица переименована из `section_contracts` в миграции 0017)
  - `target_section_maps.target_section` (таблица переименована из `section_maps` в миграции 0017)
  - `generation_runs.target_section` (поле переименовано из `section_key` в миграции 0007)
- **Тип**: TEXT с валидацией на 12 канонических ключей (без "unknown")
- **Назначение**: целевая секция для генерации контента
- **Валидация**: 
  - В моделях: `TargetSectionContract.__init__()` вызывает `validate_target_section()` из `app.core.section_standardization`
  - В схемах: `SectionContractCreate` имеет валидатор `@field_validator("section_key")` (поле называется `section_key` в схеме, но маппится на `target_section` в модели)

## SourceZoneClassifier

Классификатор `SourceZoneClassifier` (`app.services.source_zone_classifier`) определяет `source_zone` для каждого anchor на основе:

- **Входные данные**:
  - `doc_type` — тип документа (определяет набор правил и разрешённые зоны)
  - `section_path` — иерархия заголовков (например, "H1/H2/H3") или список заголовков
  - `heading_text` — текст текущего заголовка (опционально)
  - `language` — язык документа ("ru" или "en", опционально)

- **Соответствие стандартам**:
  - Для `doc_type=protocol`: правила классификации соответствуют структуре разделов согласно **Правилам GCP ЕАЭС (Решение № 79, Раздел 6)**
  - Паттерны распознавания учитывают как русскоязычные, так и англоязычные варианты названий разделов

- **Выходные данные** (`SourceZoneResult`):
  - `zone` — один из 12 канонических ключей или "unknown" (нормализуется через `ZoneSetRegistry`)
  - `confidence` — уверенность классификации (0.0-1.0)
  - `matched_rule_id` — ID правила, которое сработало (для отладки)

- **Правила**: загружаются динамически из `backend/app/data/source_zones/rules_{doc_type}.yaml` для каждого типа документа
  - Правила кэшируются в памяти для каждого `doc_type` после первой загрузки
  - Regex-паттерны компилируются и кэшируются для ускорения работы
  - Поддерживаются паттерны для русского и английского языков
  - Для протоколов (`rules_protocol.yaml`): паттерны соответствуют разделам ЕАЭС (Решение № 79, Раздел 6)
  - Приоритет: более специфичные зоны проверяются первыми (например, `serious_adverse_events` перед `adverse_events`)
  - Confidence вычисляется на основе доли совпавших сегментов и силы совпадения (exact match = 1.0, strong partial = 0.7, weak partial = 0.4)
  - Зоны нормализуются через `ZoneSetRegistry` (`app.services.zone_set_registry`) для валидации разрешённых зон для данного `doc_type`
  - Используется singleton pattern через функцию `get_classifier()` для переиспользования экземпляра
  - Если `section_path` пустой или равен "ROOT", "__FRONTMATTER__", "FOOTNOTES", возвращается "unknown" с confidence 0.0

## Правила Prefer Source Zones

Каждая `target_section` имеет правила приоритизации для retrieval:

### Overview
- **Prefer**: `overview`, `design`
- **Fallback**: `endpoints`, `population`, `ip`

### Design
- **Prefer**: `design`
- **Fallback**: `overview`

### IP
- **Prefer**: `ip`
- **Fallback**: `overview`, `design`

### Endpoints
- **Prefer**: `endpoints`
- **Fallback**: `overview`, `procedures`

### Population
- **Prefer**: `population`
- **Fallback**: `design`

### Procedures
- **Prefer**: `procedures`
- **Fallback**: `design`

### Safety
- **Prefer**: `safety`
- **Fallback**: `procedures`

### Statistics
- **Prefer**: `statistics`
- **Fallback**: `design`

### Data Management
- **Prefer**: `data_management`
- **Fallback**: `admin`

### Ethics
- **Prefer**: `ethics`
- **Fallback**: `overview`, `admin`

### Admin
- **Prefer**: `admin`
- **Fallback**: `data_management`, `ethics`

### Appendix
- **Prefer**: `appendix`
- **Fallback**: `procedures`, `admin`


## Миграции данных

### Миграция 0007: Переименование section_key → target_section

Миграция `0007_rename_section_key_to_target_section.py`:
- Переименовывает `section_key` → `target_section` в таблицах:
  - `section_contracts.section_key` → `target_section` (таблица переименована в `target_section_contracts` в миграции 0017)
  - `section_maps.section_key` → `target_section` (таблица переименована в `target_section_maps` в миграции 0017)
  - `generation_runs.section_key` → `target_section`
  - `impact_items.affected_section_key` → `affected_target_section`
  - `section_taxonomy_nodes.section_key` → `target_section` (таблица удалена в миграции 0020)
  - `section_taxonomy_nodes.parent_section_key` → `parent_target_section`
  - `section_taxonomy_related.a_section_key` → `a_target_section`
  - `section_taxonomy_related.b_section_key` → `b_target_section`
- Добавляет поле `view_key` (TEXT, nullable) в таблицы `section_contracts` и `generation_runs`
- Добавляет поля `source_zone` (TEXT, NOT NULL, DEFAULT 'unknown') и `language` (ENUM document_language, NOT NULL, DEFAULT 'unknown') в таблицы `anchors` и `chunks`
- Создаёт индексы:
  - `ix_anchors_doc_version_source_zone` на `(doc_version_id, source_zone)`
  - `ix_chunks_doc_version_source_zone` на `(doc_version_id, source_zone)`
  - `ix_anchors_doc_version_language` на `(doc_version_id, language)`
  - `ix_chunks_doc_version_language` на `(doc_version_id, language)`

### Миграция 0012: ENUM source_zone и стандартизация

Миграция `0012_add_source_zone_enum_and_standardize_sections.py`:

1. Создаёт PostgreSQL ENUM `source_zone` с 12 ключами + "unknown"
2. Маппит старые значения на канонические:
   - `randomization` → `design`
   - `adverse_events`, `serious_adverse_events` → `safety`
   - `statistical_methods` → `statistics`
   - `eligibility` → `population`
   - `ip_handling` → `ip`
   - `study_design` → `design`
   - `objectives` → `overview`
   - `study_population` → `population`
3. Обновляет тип полей `anchors.source_zone` и `chunks.source_zone` с TEXT на ENUM
4. Устанавливает значение по умолчанию `'unknown'::source_zone`
5. Создаёт индексы `ix_anchors_doc_version_source_zone` и `ix_chunks_doc_version_source_zone` (если их ещё нет)

### Миграция 0017: Переименование таблиц

Миграция `0017_rename_section_tables_to_target_section.py`:
- Переименовывает таблицы (в порядке зависимостей):
  - `section_taxonomy_aliases` → `target_section_taxonomy_aliases`
  - `section_taxonomy_related` → `target_section_taxonomy_related`
  - `section_taxonomy_nodes` → `target_section_taxonomy_nodes`
  - `section_maps` → `target_section_maps`
  - `generated_sections` → `generated_target_sections`
  - `section_contracts` → `target_section_contracts`
- PostgreSQL автоматически обновляет внешние ключи при переименовании таблиц
- Переименование выполняется только если таблица существует и целевая таблица ещё не существует

## Валидация

- **Модели**: 
  - `TargetSectionContract.__init__()` вызывает `validate_target_section()` из `app.core.section_standardization` при создании объекта
  - Валидация выполняется мягко (не блокирует создание для обратной совместимости со старыми данными)
- **Схемы**: 
  - `SectionContractCreate` имеет валидатор `@field_validator("section_key")` для проверки соответствия 12 каноническим ключам
  - Поле в схеме называется `section_key`, но маппится на `target_section` в модели
- **Обратная совместимость**: 
  - Модели `TargetSectionContract`, `TargetSectionMap`, `GenerationRun` и `ImpactItem` имеют property `section_key` / `affected_section_key` для обратной совместимости
  - Использование этих property выдает `DeprecationWarning` и перенаправляет на `target_section` / `affected_target_section`
- **Автозаполнение**: 
  - При создании `TargetSectionContract` через схему `SectionContractCreate`, метод `model_post_init()` (в схеме Pydantic) автоматически заполняет `prefer_source_zones` и `fallback_source_zones` в `retrieval_recipe_json` из правил `TARGET_SECTION_PREFER_SOURCE_ZONES`, если они не заданы явно
  - Автозаполнение происходит только если `retrieval_recipe_json.prefer_source_zones` пуст или None
  - Используется функция `get_prefer_source_zones()` из `app.core.section_standardization`
  - **Важно**: Автозаполнение происходит на уровне схемы Pydantic, а не в модели SQLAlchemy

## Утилиты

### Модуль `app/core/section_standardization.py`

Предоставляет константы и функции для работы с каноническими секциями:

- **Константы**:
  - `CANONICAL_SECTION_KEYS` — список 12 канонических ключей (без "unknown"), построен из значений `SourceZone` enum
  - `TARGET_SECTION_PREFER_SOURCE_ZONES` — словарь правил prefer/fallback для каждой target_section
    - Формат: `{target_section: {"prefer": [...], "fallback": [...]}}`
    - Использует значения из `SourceZone` enum для консистентности

- **Функции валидации**:
  - `is_valid_target_section(value: str) -> bool` — проверка валидности target_section (один из 12 ключей)
  - `is_valid_source_zone(value: str) -> bool` — проверка валидности source_zone (12 ключей + "unknown")
  - `validate_target_section(value: str) -> None` — валидация с выбрасыванием `ValueError` при невалидном значении
  - `validate_source_zone(value: str) -> None` — валидация с выбрасыванием `ValueError` при невалидном значении

- **Функции для retrieval**:
  - `get_prefer_source_zones(target_section: str) -> dict[str, list[str]]` — получение правил prefer/fallback для target_section
    - Возвращает `{"prefer": [...], "fallback": [...]}` или пустые списки, если секция не найдена

### Модуль `app/services/zone_set_registry.py`

`ZoneSetRegistry` управляет наборами разрешённых зон для каждого типа документа:

- Загружает конфигурацию из `backend/app/data/source_zones/zone_sets.yaml` при инициализации
- Используется singleton pattern через функцию `get_registry()` для переиспользования экземпляра
- `get_allowed_zones(doc_type: DocumentType) -> list[str]` — возвращает список разрешённых зон для типа документа (возвращает пустой список, если doc_type не найден в конфигурации)
- `validate_zone(doc_type: DocumentType, zone_key: str) -> bool` — проверяет, является ли зона разрешённой для данного типа документа
- `normalize_zone(doc_type: DocumentType, zone: str) -> str` — нормализует зону, возвращая "unknown" для неразрешённых зон
- Используется `SourceZoneClassifier` для валидации и нормализации классифицированных зон после применения правил

## Использование в Retrieval

При поиске релевантных chunks для генерации секции через `RetrievalService` (`app.services.retrieval`):

1. **Приоритетные зоны**: Сначала ищутся chunks из `prefer_source_zones` 
   - Берутся из `retrieval_recipe_json.prefer_source_zones` в `TargetSectionContract`
   - Или автоматически из `TARGET_SECTION_PREFER_SOURCE_ZONES` (если не заданы явно)
2. **Резервные зоны**: Если недостаточно контента, используются chunks из `fallback_source_zones`
   - Берутся из `retrieval_recipe_json.fallback_source_zones` в `TargetSectionContract`
   - Или автоматически из `TARGET_SECTION_PREFER_SOURCE_ZONES` (если не заданы явно)
3. **Фильтрация**: Фильтрация по `source_zone` выполняется через индекс `(doc_version_id, source_zone)` в таблицах `anchors` и `chunks`
   - Индексы: `ix_anchors_doc_version_source_zone` и `ix_chunks_doc_version_source_zone`

## Структура файлов конфигурации

Все файлы конфигурации находятся в директории `backend/app/data/source_zones/`:

- **Правила классификации**: `rules_{doc_type}.yaml` (например, `rules_protocol.yaml`, `rules_csr.yaml`)
  - YAML файлы с паттернами для каждого типа документа
  - Формат: словарь с ключом `source_zones`, содержащий список правил
  - Каждое правило содержит:
    - `zone` — имя зоны (может быть неканоническим, будет нормализовано через `ZoneSetRegistry`)
    - `patterns.ru` — список regex-паттернов для русского языка
    - `patterns.en` — список regex-паттернов для английского языка
  - Загружаются динамически `SourceZoneClassifier` при первом обращении к `doc_type`
  - **Для протоколов** (`rules_protocol.yaml`): правила соответствуют структуре разделов согласно **Правилам GCP ЕАЭС (Решение № 79, Раздел 6)**, см. раздел [Маппинг ЕАЭС (Решение № 79) → ClinNexus (12 Zones)](#маппинг-еаэс-решение--79--clinnexus-12-zones)
  
- **Наборы зон**: `zone_sets.yaml`
  - Определяет разрешённые зоны для каждого типа документа
  - Формат: словарь `{doc_type: [list of allowed zones]}`
  - Используется `ZoneSetRegistry` для валидации и нормализации зон
  - Если зона не найдена в списке разрешённых для `doc_type`, она нормализуется в "unknown"

- **Кросс-референсы**: `zone_crosswalk.yaml`
  - Маппинг между зонами разных типов документов
  - Используется для трансляции зон между различными типами документов

- **Приоритеты зон для топиков**: `topic_zone_priors.yaml`
  - Определяет приоритеты зон по типам документов для маппинга топиков

