# Стандартизация 12 основных секций

## Обзор

В ClinNexus используется стандартизированный набор из **12 канонических ключей** для `source_zone` и `target_section`:

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

## Source Zone vs Target Section

### Source Zone
- **Где используется**: `anchors.source_zone`, `chunks.source_zone`
- **Тип**: ENUM `source_zone` (12 ключей + "unknown")
- **Назначение**: классификация контента по источнику в исходном документе
- **Определение**: автоматически через `SourceZoneClassifier` на основе `section_path`, `heading_text` и `language`
- **Правила**: загружаются из `backend/app/data/source_zone_rules.yaml`

### Target Section
- **Где используется**: `target_section_contracts.target_section`, `target_section_maps.target_section`, `generation_runs.target_section` (таблицы `target_section_contracts` и `target_section_maps` переименованы из `section_contracts` и `section_maps` в миграции 0017)
- **Тип**: TEXT с валидацией на 12 канонических ключей (без "unknown")
- **Назначение**: целевая секция для генерации контента
- **Валидация**: проверка в моделях (`SectionContract`) и схемах (`SectionContractCreate`)

## SourceZoneClassifier

Классификатор `SourceZoneClassifier` определяет `source_zone` для каждого anchor на основе:

- **Входные данные**:
  - `section_path` — иерархия заголовков (например, "H1/H2/H3")
  - `heading_text` — текст текущего заголовка (опционально)
  - `language` — язык документа ("ru" или "en", опционально)

- **Выходные данные**:
  - `zone` — один из 12 канонических ключей или "unknown"
  - `confidence` — уверенность классификации (0.0-1.0)
  - `matched_rule_id` — ID правила, которое сработало (для отладки)

- **Правила**: загружаются из `backend/app/data/source_zone_rules.yaml`
  - Поддерживаются паттерны для русского и английского языков
  - Приоритет: более специфичные зоны проверяются первыми
  - Confidence вычисляется на основе доли совпавших сегментов и силы совпадения

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

## Использование в Retrieval

При поиске релевантных chunks для генерации секции:

1. Сначала ищутся chunks из `prefer_source_zones`
2. Если недостаточно контента, используются chunks из `fallback_source_zones`
3. Фильтрация по `source_zone` выполняется через индекс `(doc_version_id, source_zone)`

## Миграция данных

Миграция `0012_add_source_zone_enum_and_standardize_sections.py`:

1. Создаёт ENUM `source_zone` с 12 ключами + "unknown"
2. Маппит старые значения на канонические:
   - `randomization` → `design`
   - `adverse_events`, `serious_adverse_events` → `safety`
   - `statistical_methods` → `statistics`
   - `eligibility` → `population`
   - `ip_handling` → `ip`
   - `study_design` → `design`
   - `objectives` → `overview`
   - `study_population` → `population`
3. Обновляет тип полей `anchors.source_zone` и `chunks.source_zone` на ENUM
4. Добавляет индексы для быстрого поиска

## Валидация

- **Модели**: `SectionContract.__init__()` проверяет `target_section` при создании объекта (таблица `target_section_contracts`, переименована из `section_contracts` в миграции 0017)
- **Схемы**: `SectionContractCreate` имеет валидатор `@field_validator("section_key")`
- **Автозаполнение**: при создании `SectionContract` через схему, `prefer_source_zones` автоматически заполняются из правил, если не заданы явно

## Утилиты

Модуль `app/core/section_standardization.py` предоставляет:

- `CANONICAL_SECTION_KEYS` — список 12 канонических ключей
- `TARGET_SECTION_PREFER_SOURCE_ZONES` — правила prefer/fallback для каждой target_section
- `is_valid_target_section(value)` — проверка валидности target_section
- `is_valid_source_zone(value)` — проверка валидности source_zone
- `get_prefer_source_zones(target_section)` — получение правил для target_section
- `validate_target_section(value)` — валидация с выбрасыванием ValueError
- `validate_source_zone(value)` — валидация с выбрасыванием ValueError

