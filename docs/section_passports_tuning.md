Ниже — те же Cursor prompts, но **на русском**, и формулировками “сделай PR/задачу” так, чтобы Cursor хорошо их понял. Каждый промпт — отдельная задача.

---

## Промпт 1 — Экспорт корпуса заголовков/окон (из БД в JSONL)

**Цель:** выгрузить данные по 200 протоколам для оффлайн-тюнинга.

**Prompt для Cursor:**

> Реализуй оффлайн-утилиту `backend/tools/passport_tuning/export_heading_corpus.py`.
>
> Требования:
>
> * Подключение к Postgres через существующую конфигурацию приложения (используй `backend/app/core/config.py` или текущий db session/engine, как принято в проекте).
> * Аргументы CLI: `--workspace-id`, `--doc-type protocol`, `--limit-docs`, `--out <path>`.
> * Для каждого `document_version` со статусом `ready` экспортируй записи “heading records”:
>
>   * `doc_version_id`, `document_id`, `doc_type`, `detected_language`
>   * `hdr_anchor_id`, `heading_text_raw`, `heading_text_norm`, `heading_level` (если есть), `para_index`, `section_path`
>   * “окно” под заголовком: возьми первые N anchors (N=50) после heading внутри того же `section_path` и посчитай:
>
>     * количество по `content_type` (p/li/tbl/cell/fn/hdr)
>     * `total_chars`
>     * `sample_text`: конкат первых 5 anchor.text (обрезать до 500 символов)
> * Формат выхода: JSONL (одна строка = один heading record).
> * Оптимизация: минимизируй количество запросов (лучше SQL join/CTE, чем ORM-циклы).
> * Добавь `backend/tools/passport_tuning/README.md` с инструкцией запуска.
> * Никаких LLM/web вызовов.
>
> Добавь минимальный тест (можно условно пропускать без DB env), но сделай код тестопригодным (функции для вычислений отдельно от IO).

---

## Промпт 2 — Кластеризация заголовков в кандидаты секций (RU-first)

**Цель:** автоматически выделить “типы разделов” по заголовкам.

**Prompt:**

> Реализуй `backend/tools/passport_tuning/cluster_headings.py`, который читает corpus JSONL и строит `clusters.json`.
>
> Выход `clusters.json` — массив кластеров:
>
> * `cluster_id`
> * `top_titles_ru` (топ-20)
> * `top_titles_en` (топ-20)
> * `examples` (до 10): `{doc_version_id, section_path, heading_text_raw}`
> * `stats`: `heading_level_histogram`, `content_type_distribution`, `avg_total_chars`
>
> Алгоритм (гибрид):
>
> 1. нормализуй заголовки (reuse существующие normalize_title/normalize_text)
> 2. TF-IDF по заголовкам + агломеративная кластеризация по cosine distance (threshold-based)
> 3. опционально merge по embeddings, если они существуют в БД; если нет — корректно пропусти
>
> CLI: `--in corpus.jsonl --out clusters.json --min-size 3 --threshold 0.22`.
> Учти язык: раздели статистики RU/EN.
> Добавь небольшой детерминированный тест на синтетическом корпусе.

---

## Промпт 3 — Генератор черновиков паспортов из кластеров (signals-only)

**Цель:** получить черновики `target_section_contracts` (mapping.signals + базовые qc). (Таблица переименована из `section_contracts` в миграции 0017)

**Prompt:**

> Реализуй `backend/tools/passport_tuning/generate_contract_drafts.py`.
>
> Вход:
>
> * `clusters.json`
> * файл ручного соответствия `cluster_to_section_key.json` (cluster_id -> {doc_type, section_key, title_ru})
> этот файл cluster_to_section_key.json делается в 2 этапа:
> * `clusters.json` + список секций по типу документа из LLM -> дефолтная секция + 3 наиболее подходящих
> * обновленный `clusters.json` подсовыывется на страницу frontend\app\passport-tuning\cluster-mapping и там обрабатывается в ручном режиме 
>
> Выход:
>
> * `drafts/contracts_seed.json`: массив записей section_contract в формате, совместимом со схемой БД:
>
>   * `doc_type`, `section_key`, `title`
>   * `required_facts_json` (по умолчанию пусто)
>   * `allowed_sources_json` (по умолчанию для protocol: primary required=true; для csr — дефолты по аналогии)
>   * `retrieval_recipe_json`, где:
>
>     * `mapping.signals.lang.ru/en` с `must/should/not/regex` из заголовков кластера
>     * `heading_levels` из гистограммы, но НЕ слишком жёстко: max минимум 3, если есть evidence
>     * `context_build`: max_chars + prefer_content_types из distribution (если много li → prefer li; если много cell → prefer cell)
>     * `fallback_search.query_templates`: RU-first (title_ru + синонимы)
>   * `qc_ruleset_json`: дефолты, где:
>
>     * если доля li высокая → `prefer_list_items`
>     * если доля cell высокая → `require_cell_anchors`
>   * `citation_policy` = per_claim
>
> Обязательно:
>
> * санитайз regex (экранирование, compile-check)
> * строго валидный JSON (никаких лишних символов/суффиксов)
> * стабильная сортировка вывода (doc_type, section_key)
> * без LLM
>
> Добавь доку + шаблон cluster_to_section_key.json.

---

## Промпт 4 — Оценка качества маппинга на корпусе (coverage + evidence_health + stability)

**Цель:** автоматически мерить, насколько хорошо паспорта работают на 200 протоколах.

**Prompt:**

> Реализуй `backend/tools/passport_tuning/evaluate_mapping.py`.
>
> Вход:
>
> * `--workspace-id`
> * `--doc-type protocol`
> * `--contracts` (источник: DB или seed.json)
> * `--out report.json`
>
> Логика:
>
> * Для каждого section_contract и каждого подходящего `document_version` запусти существующий `SectionMappingService` (используй продовый код).
> * Собери метрики на уровне `section_key`:
>
>   * coverage: количество `mapped/needs_review/failed`
>   * confidence: avg + p50/p90
>   * evidence_health: avg anchors count, avg доля li, avg доля cell, avg доля tbl
> * Stability по версиям:
>
>   * для документов с несколькими версиями (один document_id): сравни соседние версии:
>
>     * Jaccard по множеству **hash(text_norm)** anchors (не anchor_id, чтобы не зависеть от doc_version_id)
>     * similarity по section_path (длина общего префикса / max_len)
>   * выведи avg и p10
> * “Подсказки по провалам”:
>
>   * для failed: топ-10 ближайших headings по keyword match (и embeddings, если есть)
> * Вывод: JSON + печать читаемой таблицы в stdout.
>
> Ограничения:
>
> * без LLM/web
> * старайся ускорить (батч чтение из БД, аккуратная параллельность)
> * флаг `--dry-run` (никаких записей в БД)
>
> Добавь unit tests на хелперы метрик.

---

## Промпт 5 — Очередь на ручную проверку (active learning queue)

**Цель:** выбрать “что смотреть руками”, а не всё подряд.

**Prompt:**

> Реализуй `backend/tools/passport_tuning/build_review_queue.py`.
>
> Вход:
>
> * `report.json` из evaluate_mapping
> * опционально список priority `section_keys` (soa, endpoints, safety...)
>
> Выход:
>
> * `review_queue.json`: список задач:
>
>   * `doc_version_id`, `section_key`, `reason` (low_confidence/low_evidence_health/unstable/missing)
>   * `suggested_candidates`: top-5 section_paths/headings (keyword + embeddings если доступны)
>
> Scoring:
>
> * missing и unstable важнее, чем просто low_confidence
> * приоритет бизнес-важных секций
> * лимит задач на один doc_version, чтобы не заспамить
>
> Без записи в БД, детерминированная сортировка.

---

## Промпт 6 — Обучение signals из overrides (авто-патчи паспортов)

**Цель:** превращать подтверждения/оверрайды пользователя в улучшение signals.

**Prompt:**

> Реализуй `backend/tools/passport_tuning/learn_from_overrides.py`.
>
> Вход:
>
> * workspace_id
> * вытащи target_section_maps со статусом `overridden` (и/или user_confirmed=true, если есть) (таблица переименована из `section_maps` в миграции 0017)
> * текущие contracts из DB
>
> Выход:
>
> * `contracts_patch.json` (или SQL patch), который добавляет/улучшает `retrieval_recipe_json.mapping.signals`:
>
>   * добавить “should” термины из подтверждённых заголовков (top n-grams)
>   * добавить “not” термины из частых конфузов (например, endpoints путается с objectives)
>   * обновить regex списки безопасными паттернами (compile-tested)
> * Никогда автоматически не удаляй существующие сигналы (только add). Удаление — только как “suggestions”.
>
> Обязательно:
>
> * language aware RU/EN (RU в приоритете)
> * фильтрация слишком общих слов (stopwords)
> * diff-вывод: old vs new по каждому изменённому контракту.

---

## Промпт 7 — Сделать маппинг менее хрупким (soft heading_levels + контекстные триггеры)

**Цель:** исправить кейсы типа endpoints/AE/title_page, когда секция есть, но не мэпится.

**Prompt:**

> Улучши `SectionMappingService`, чтобы маппинг был более устойчивым:
>
> * `heading_levels` из контракта трактуй как soft-preference, а не как жёсткий фильтр:
>
>   * если кандидатов в диапазоне нет, расширь поиск на более глубокие уровни, но понижай confidence.
> * Добавь контекстные триггеры (score boost), если в первых N anchors под заголовком встречаются паттерны:
>
>   * Endpoints RU: "первичн(ая|ый) конечн", "вторичн(ые|ая) конечн", "критери(и|й) эффективности", "параметр(ы)? эффективности"
>   * Safety/AE RU: "нежелательн(ые|ое) явлен", "серьезн(ое|ые) нежелательн", "СНЯ", "SAE", "отчетност"
> * Anti-confusion penalty:
>
>   * если заголовок/контекст похож на objectives/цели/задачи — штраф для endpoints
> * Добавь структурированный debug-лог (по section_key: top candidates, breakdown scoring).
>
> Добавь тесты на синтетике (список headings + anchors), без настоящего docx.
> Сохрани обратную совместимость с текущими contracts.

---

## Промпт 8 — “Один запуск” пайплайна тюнинга

**Цель:** одна команда прогоняет всё и складывает артефакты в папку.

**Prompt:**

> Реализуй `backend/tools/passport_tuning/run_pipeline.py`.
>
> Шаги:
>
> 1. export corpus
> 2. cluster headings
> 3. generate drafts (если есть cluster_to_section_key.json)
> 4. evaluate mapping
> 5. build review queue
>
> CLI:
>
> * опции skip step’ов
> * выбор contracts: DB или seed.json
> * выходная папка со timestamp
> * сохранить `run_meta.json` (снимок конфигурации запуска)
>
> Без LLM, без web.
> Добавь README как запускать end-to-end.

---

Если хочешь — я могу ещё написать короткий “мастер-промпт” для Cursor: **“сначала открой repo_structure.txt, найди SectionMappingService, покажи где лучше вставить evidence_health/stability метрики и какой формат логов сделать”**.
