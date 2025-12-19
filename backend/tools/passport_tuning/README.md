# Утилиты для настройки паспортов

Набор оффлайн-утилит для работы с heading corpus и кластеризацией заголовков.

## Утилиты

1. **export_heading_corpus.py** - экспорт heading corpus из БД в JSONL
2. **cluster_headings.py** - кластеризация заголовков из JSONL корпуса
3. **split_clusters_by_language.py** - разделение clusters.json на файлы по языковому признаку (RU/EN)
4. **generate_contract_drafts.py** - генерация черновиков section_contract из кластеров
5. **Веб-интерфейс** (`/passport-tuning/cluster-mapping`) - ручной маппинг кластеров через веб-интерфейс (см. раздел "Создание mapping файла")

---

# Экспорт Heading Corpus

Оффлайн-утилита для экспорта heading corpus из базы данных PostgreSQL в формате JSONL.

## Описание

Утилита экспортирует записи заголовков (heading records) для всех `document_version` со статусом `ready`. Для каждого заголовка собирается информация о документе, самом заголовке и "окне" под заголовком (первые 50 anchors в той же секции).

## Требования

- Python 3.10+
- Настроенное подключение к базе данных PostgreSQL (через `.env` файл в `backend/`)
- Установленные зависимости проекта (`pip install -e .` в директории `backend/`)

## Использование

### Базовый запуск

```bash
cd backend
python -m tools.passport_tuning.export_heading_corpus --out output.jsonl
```

### С фильтрацией по workspace

```bash
python -m tools.passport_tuning.export_heading_corpus \
    --workspace-id "123e4567-e89b-12d3-a456-426614174000" \
    --out output.jsonl
```

### С фильтрацией по типу документа

```bash
python -m tools.passport_tuning.export_heading_corpus \
    --doc-type protocol \
    --out output.jsonl
```

### С ограничением количества документов

```bash
python -m tools.passport_tuning.export_heading_corpus \
    --limit-docs 10 \
    --out output.jsonl
```

### Комбинированные фильтры

```bash
python -m tools.passport_tuning.export_heading_corpus \
    --workspace-id "123e4567-e89b-12d3-a456-426614174000" \
    --doc-type protocol \
    --limit-docs 5 \
    --out output.jsonl
```

## Аргументы CLI

- `--workspace-id` (опционально): UUID workspace для фильтрации документов
- `--doc-type` (опционально): Тип документа для фильтрации. По умолчанию: `protocol`. Допустимые значения: `protocol`, `sap`, `tfl`, `csr`, `ib`, `icf`, `other`
- `--limit-docs` (опционально): Ограничение количества документов для обработки
- `--out` (обязательно): Путь к выходному JSONL файлу

## Формат вывода

Выходной файл содержит одну JSON-строку на каждый heading record. Каждая запись имеет следующую структуру:

```json
{
  "doc_version_id": "uuid",
  "document_id": "uuid",
  "doc_type": "protocol",
  "detected_language": "ru",
  "hdr_anchor_id": "uuid",
  "heading_text_raw": "Исходный текст заголовка",
  "heading_text_norm": "Нормализованный текст заголовка",
  "heading_level": 1,
  "para_index": 5,
  "section_path": "1.2.3",
  "window": {
    "content_type_counts": {
      "p": 10,
      "li": 5,
      "tbl": 2
    },
    "total_chars": 1500,
    "sample_text": "Первые 5 anchor.text_norm, обрезанные до 500 символов..."
  }
}
```

### Поля записи

- `doc_version_id`: UUID версии документа
- `document_id`: UUID документа
- `doc_type`: Тип документа (protocol, sap, tfl, и т.д.)
- `detected_language`: Определённый язык документа (ru, en, mixed, unknown)
- `hdr_anchor_id`: UUID anchor'а заголовка
- `heading_text_raw`: Исходный текст заголовка
- `heading_text_norm`: Нормализованный текст заголовка
- `heading_level`: Уровень заголовка (1-9) или `null`, если не удалось определить
- `para_index`: Индекс параграфа заголовка или `null`
- `section_path`: Путь секции (например, "1.2.3")
- `window`: Статистика "окна" под заголовком:
  - `content_type_counts`: Количество anchors по типам контента (p, li, tbl, cell, fn, hdr)
  - `total_chars`: Общее количество символов в окне
  - `sample_text`: Конкатенация первых 5 `anchor.text_norm`, обрезанная до 500 символов

## Оптимизация

Утилита использует один оптимизированный SQL запрос с CTE (Common Table Expressions) для минимизации количества запросов к базе данных. Все данные собираются за один проход.

## Тестирование

Для запуска тестов (требуется настроенная БД):

```bash
cd backend
pytest tests/test_export_heading_corpus.py -v
```

Тесты можно пропустить, если переменная окружения `SKIP_DB_TESTS` установлена в `1`:

```bash
SKIP_DB_TESTS=1 pytest tests/test_export_heading_corpus.py -v
```

---

# Кластеризация заголовков

Оффлайн-утилита для кластеризации заголовков из JSONL корпуса с использованием гибридного алгоритма (TF-IDF + агломеративная кластеризация + опциональный merge по embeddings).

## Описание

Утилита читает JSONL корпус, созданный `export_heading_corpus.py`, и строит кластеры похожих заголовков. Использует гибридный алгоритм:

1. **TF-IDF векторизация** заголовков с униграммами и биграммами
2. **Агломеративная кластеризация** по cosine distance с threshold-based подходом
3. **Опциональный merge** по embeddings из БД (если доступны)

## Требования

- Python 3.10+
- Установленные зависимости: `numpy`, `scikit-learn` (добавлены в `pyproject.toml`)
- Опционально: настроенное подключение к БД для merge по embeddings

## Использование

### Базовый запуск

```bash
cd backend
python -m tools.passport_tuning.cluster_headings --in corpus.jsonl --out clusters.json
```

### С настройкой параметров

```bash
python -m tools.passport_tuning.cluster_headings \
    --in corpus.jsonl \
    --out clusters.json \
    --min-size 5 \
    --threshold 0.25
```

## Аргументы CLI

- `--in` (обязательно): Путь к входному JSONL файлу (corpus)
- `--out` (обязательно): Путь к выходному JSON файлу (clusters.json)
- `--min-size` (опционально): Минимальный размер кластера. По умолчанию: `3`
- `--threshold` (опционально): Порог distance для кластеризации. По умолчанию: `0.22`

## Формат вывода

Выходной файл `clusters.json` содержит массив кластеров:

```json
[
  {
    "cluster_id": 0,
    "top_titles_ru": [
      "Цели исследования",
      "Цели исследования",
      ...
    ],
    "top_titles_en": [
      "Study Objectives",
      "Study Objectives",
      ...
    ],
    "examples": [
      {
        "doc_version_id": "uuid",
        "section_path": "1.2.3",
        "heading_text_raw": "Цели исследования"
      },
      ...
    ],
    "stats": {
      "heading_level_histogram": {
        "1": 10,
        "2": 5
      },
      "content_type_distribution": {
        "p": 50,
        "li": 20,
        "tbl": 5
      },
      "avg_total_chars": 1250.5
    }
  },
  ...
]
```

### Поля кластера

- `cluster_id`: Уникальный идентификатор кластера
- `top_titles_ru`: Топ-20 заголовков на русском языке из кластера
- `top_titles_en`: Топ-20 заголовков на английском языке из кластера
- `examples`: До 10 примеров заголовков с `doc_version_id`, `section_path`, `heading_text_raw`
- `stats`: Статистика кластера:
  - `heading_level_histogram`: Распределение уровней заголовков (1-9)
  - `content_type_distribution`: Распределение типов контента в окнах под заголовками
  - `avg_total_chars`: Среднее количество символов в окнах

## Алгоритм

1. **Нормализация**: Заголовки нормализуются с помощью `normalize_title` и `normalize_text`
2. **TF-IDF**: Вычисляются TF-IDF векторы с параметрами:
   - `max_features=5000`
   - `min_df=2` (минимум 2 документа)
   - `max_df=0.95` (максимум 95% документов)
   - `ngram_range=(1, 2)` (униграммы и биграммы)
3. **Кластеризация**: Агломеративная кластеризация с:
   - `distance_threshold` из аргумента `--threshold`
   - `linkage='average'`
   - Фильтрация кластеров по `--min-size`
4. **Merge по embeddings** (опционально):
   - Загружаются embeddings из таблицы `chunks` для заголовков
   - Вычисляются средние embeddings для каждого кластера
   - Объединяются близкие кластеры (cosine distance < threshold * 0.7)

## Тестирование

Для запуска тестов:

```bash
cd backend
pytest tests/test_cluster_headings.py -v
```

Тесты включают:
- Детерминированный тест на синтетическом корпусе
- Тесты отдельных функций (нормализация, TF-IDF, кластеризация, merge)
- Интеграционный тест полного пайплайна

---

# Генерация черновиков контрактов

Утилита для генерации черновиков `section_contract` из кластеров заголовков с автоматическим построением `retrieval_recipe_json` и `qc_ruleset_json`.

## Описание

Утилита читает `clusters.json` (результат кластеризации) и файл ручного соответствия `cluster_to_section_key.json`, затем генерирует `contracts_seed.json` с записями `section_contract` в формате, совместимом со схемой БД.

## Требования

- Python 3.10+
- Файл `clusters.json` (результат работы `cluster_headings.py`)
- Файл `cluster_to_section_key.json` с ручным соответствием кластеров и section_key

## Использование

### Базовый запуск

```bash
cd backend
python -m tools.passport_tuning.generate_contract_drafts \
    --clusters clusters.json \
    --mapping cluster_to_section_key.json \
    --out drafts/contracts_seed.json
```

### С указанием путей

```bash
python -m tools.passport_tuning.generate_contract_drafts \
    --clusters path/to/clusters.json \
    --mapping path/to/cluster_to_section_key.json \
    --out path/to/output.json
```

## Аргументы CLI

- `--clusters` (опционально): Путь к `clusters.json`. По умолчанию: `clusters.json`
- `--mapping` (опционально): Путь к `cluster_to_section_key.json`. По умолчанию: `cluster_to_section_key.json`
- `--out` (опционально): Путь к выходному файлу. По умолчанию: `drafts/contracts_seed.json`

## Формат входных данных

### clusters.json

Массив кластеров, созданный утилитой `cluster_headings.py`. Каждый кластер содержит:
- `cluster_id`: идентификатор кластера
- `top_titles_ru` / `top_titles_en`: топ заголовков по языкам
- `examples`: примеры заголовков
- `stats`: статистика (heading_level_histogram, content_type_distribution, avg_total_chars)

### cluster_to_section_key.json

**Важно**: Формат файла был обновлен. См. [MIGRATION_GUIDE.md](../../app/data/passport_tuning/MIGRATION_GUIDE.md) для деталей миграции.

Словарь соответствий кластеров и section_key (ключ - cluster_id как строка):

```json
{
  "0": {
    "doc_type": "protocol",
    "section_key": "protocol.references",
    "title_ru": "Список литературы",
    "mapping_mode": "single",
    "notes": null
  },
  "1": {
    "doc_type": "protocol",
    "section_key": "protocol.objectives",
    "title_ru": "Цели",
    "mapping_mode": "ambiguous",
    "notes": "Неоднозначное соответствие"
  }
}
```

**Поля:**
- `doc_type`: тип документа (protocol, csr, sap, и т.д.)
- `section_key`: семантический ключ секции (например, "protocol.references")
- `title_ru`: заголовок на русском языке (опционально)
- `mapping_mode`: режим маппинга - `"single"` (по умолчанию), `"ambiguous"`, `"skip"`, `"needs_split"` (обязательное)
- `notes`: комментарий или причина выбора режима (опционально, до 500 символов)

**Режимы маппинга:**
- `"single"` - однозначное соответствие (используется в автотюнинге)
- `"ambiguous"` - неоднозначное соответствие (исключается из автотюнинга)
- `"skip"` - кластер пропущен (исключается из автотюнинга, разрешены пустые `section_key` и `doc_type="other"`)
- `"needs_split"` - требуется разделение кластера (по умолчанию исключается из автотюнинга)

Шаблон файла: `cluster_to_section_key.json.template`

**Обратная совместимость**: Старые записи без `mapping_mode` автоматически получают `"single"`, без `notes` - `null`.

## Формат вывода

Выходной файл `contracts_seed.json` содержит массив записей `section_contract`:

```json
[
  {
    "doc_type": "protocol",
    "section_key": "protocol.references",
    "title": "Список литературы",
    "required_facts_json": {
      "facts": []
    },
    "allowed_sources_json": {
      "dependency_sources": [
        {
          "doc_type": "protocol",
          "section_keys": [],
          "required": true,
          "role": "primary",
          "precedence": 0,
          "min_mapping_confidence": 0.0,
          "allowed_content_types": []
        }
      ],
      "document_scope": {
        "same_study_only": true,
        "allow_superseded": false
      }
    },
    "retrieval_recipe_json": {
      "version": 2,
      "language": {
        "mode": "auto"
      },
      "mapping": {
        "signals": {
          "lang": {
            "ru": {
              "must": ["список", "литературы"],
              "should": ["литература"],
              "not": [],
              "regex": []
            },
            "en": {
              "must": [],
              "should": [],
              "not": [],
              "regex": []
            }
          }
        },
        "min_heading_level": 1,
        "max_heading_level": 3
      },
      "context_build": {
        "max_chars": 5000,
        "prefer_content_types": ["li"]
      },
      "fallback_search": {
        "query_templates": {
          "ru": ["СПИСОК ЛИТЕРАТУРЫ", "Список литературы"]
        }
      },
      "security": {
        "secure_mode_required": true
      }
    },
    "qc_ruleset_json": {
      "phases": ["input_qc", "citation_qc"],
      "gate_policy": {
        "on_missing_required_fact": "blocked",
        "on_low_mapping_confidence": "blocked",
        "on_citation_missing": "fail"
      },
      "warnings": [
        {
          "type": "prefer_list_items",
          "message": "Секция содержит много элементов списка"
        }
      ],
      "numbers_match_facts": false
    },
    "citation_policy": "per_claim"
  }
]
```

## Алгоритм генерации

### 1. Mapping Signals

Извлекаются из заголовков кластера:
- **must**: первые 2-4 наиболее частых слова из топ-5 заголовков
- **should**: остальные слова из заголовков 6-15
- **not**: пусто по умолчанию
- **regex**: паттерны нумерации (например, "1.2.3") из заголовков

Все regex паттерны санитизируются и валидируются.

### 2. Heading Levels

Извлекаются из `heading_level_histogram`:
- `min_heading_level`: минимальный уровень из гистограммы
- `max_heading_level`: максимальный уровень (минимум 3, если есть evidence)

### 3. Context Build

- `max_chars`: вычисляется как `avg_total_chars * 1.5` (clamp 1000-10000)
- `prefer_content_types`: определяется на основе `content_type_distribution`:
  - Если много `li` (>= 10) → `["li"]`
  - Если много `cell` (>= 5) → `["cell"]`
  - Если много `p` (>= 10) → `["p"]`

### 4. Fallback Search

- `query_templates`: RU-first заголовки из кластера
- Формат: `{"ru": [...], "en": [...]}`

### 5. QC Ruleset

- `prefer_list_items`: если доля `li` >= 30%
- `require_cell_anchors`: если доля `cell` >= 20%
- `citation_policy`: всегда `per_claim`

### 6. Allowed Sources

- Для `protocol`: `primary` source с `required=true`
- Для `csr`: аналогичные дефолты
- Для других типов: пустые дефолты

## Валидация

- Все regex паттерны санитизируются и проверяются на валидность
- Выходной JSON строго валидируется
- Сортировка по `doc_type`, затем по `section_key` (стабильная)

## Примеры

### Создание mapping файла

#### Вариант 1: Веб-интерфейс (рекомендуется)

Для удобного ручного маппинга кластеров используйте веб-интерфейс:

1. **Запустите backend и frontend серверы:**
   ```bash
   # Backend (в директории backend/)
   uvicorn app.main:app --reload
   
   # Frontend (в директории frontend/)
   npm run dev
   ```

2. **Откройте веб-интерфейс:**
   - Перейдите по адресу: `http://localhost:3000/passport-tuning/cluster-mapping`
   - Или используйте URL вашего развернутого приложения

3. **Работа с интерфейсом:**
   - **Левая панель**: Список кластеров с фильтрами и поиском
     - Фильтры: "Все", "Размечено" (✓), "Не размечено" (○), "Проблемные" (⚠)
     - Поиск по заголовкам (RU/EN)
     - Статусные бейджи: ✓ Размечено (зеленый), ! Неоднозначно (желтый), ⤴ Нужен сплит (фиолетовый), ⦸ Пропущен (серый)
   
   - **Центральная панель**: Детали выбранного кластера
     - Топ заголовки (RU/EN)
     - Примеры заголовков из документов
     - Статистика кластера
   
   - **Правая панель**: Настройка маппинга
     - **Тип документа** (`doc_type`): Выбор из списка (protocol, csr, sap, и т.д.)
     - **Ключ секции** (`section_key`): Автодополнение из списка стандартных ключей
     - **Заголовок RU** (`title_ru`): Опциональный заголовок на русском
     - **Режим маппинга** (`mapping_mode`): Обязательное поле
       - "Однозначно" (`single`) - используется в автотюнинге
       - "Неоднозначно" (`ambiguous`) - исключается из автотюнинга, подсвечивается желтым
       - "Нужен сплит" (`needs_split`) - требуется разделение кластера, подсвечивается фиолетовым
       - "Пропустить" (`skip`) - кластер пропущен, разрешены пустые поля
     - **Комментарий** (`notes`): Опциональный комментарий (до 500 символов)
     - **Рекомендации**: Автоматические предложения на основе заголовков кластера

4. **Сохранение:**
   - Кнопка "Сохранить" - сохраняет маппинг для текущего кластера
   - Кнопка "Сохранить и далее" - сохраняет и переходит к следующему неразмеченному кластеру
   - Кнопка "Очистить" - удаляет маппинг для текущего кластера
   - Кнопка "Скачать JSON" - скачивает полный файл `cluster_to_section_key.json`

5. **Особенности интерфейса:**
   - При выборе режима "Пропустить" (`skip`) поля `doc_type`, `section_key`, `title_ru` автоматически отключаются
   - Режим "Неоднозначно" (`ambiguous`) не блокирует сохранение, но подсвечивает интерфейс желтым цветом
   - Режим "Нужен сплит" (`needs_split`) подсвечивается фиолетовым цветом
   - Интерфейс автоматически загружает существующий маппинг при выборе кластера

6. **После завершения маппинга:**
   - Скачайте файл `cluster_to_section_key.json` через кнопку "Скачать JSON"
   - Сохраните файл в нужное место (например, `backend/app/data/passport_tuning/cluster_to_section_key.json`)
   - Используйте файл для генерации контрактов (см. следующий раздел)

#### Вариант 2: Ручное редактирование JSON

1. Скопируйте шаблон:
```bash
cp cluster_to_section_key.json.template cluster_to_section_key.json
```

2. Отредактируйте `cluster_to_section_key.json`, добавив соответствия для нужных кластеров

3. Запустите генерацию:
```bash
python -m tools.passport_tuning.generate_contract_drafts
```

### Полный пайплайн

```bash
# 1. Экспорт корпуса
python -m tools.passport_tuning.export_heading_corpus --out corpus.jsonl

# 2. Кластеризация
python -m tools.passport_tuning.cluster_headings --in corpus.jsonl --out clusters.json

# 3. Создание mapping (выберите один из вариантов):

# Вариант A: Веб-интерфейс (рекомендуется)
# - Запустите backend и frontend серверы
# - Откройте http://localhost:3000/passport-tuning/cluster-mapping
# - Выполните маппинг через веб-интерфейс
# - Скачайте cluster_to_section_key.json через кнопку "Скачать JSON"

# Вариант B: Ручное редактирование JSON
# - Скопируйте шаблон: cp cluster_to_section_key.json.template cluster_to_section_key.json
# - Отредактируйте cluster_to_section_key.json вручную

# 4. Генерация контрактов
python -m tools.passport_tuning.generate_contract_drafts \
    --clusters clusters.json \
    --mapping cluster_to_section_key.json \
    --out drafts/contracts_seed.json
```

### Разделение кластеров по языкам

Если нужно разделить `clusters.json` на файлы по языковому признаку:

```bash
# Разделение на clusters_ru.json и clusters_en.json
python -m tools.passport_tuning.split_clusters_by_language \
    --input clusters.json \
    --output-dir .

# Строгий режим (кластер попадает только в один файл)
python -m tools.passport_tuning.split_clusters_by_language \
    --input clusters.json \
    --strict
```

Результат:
- `clusters_ru.json` - кластеры с русскими заголовками (`top_titles_ru` не пустой)
- `clusters_en.json` - кластеры с английскими заголовками (`top_titles_en` не пустой)

