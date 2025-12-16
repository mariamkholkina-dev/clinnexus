### Section Passports (Lean MVP)

**Цель**: оставить `section_contracts` как DATA в БД (прод-каркас), но убрать конфигурационную перегрузку в MVP.

- **В MVP UI/API редактирование отключено**: `POST /api/section-contracts` возвращает **403** (см. `ENABLE_CONTRACT_EDITING=false`).
- Паспорта задаются файлами в репозитории: `contracts/seed/*.json`.
- Загрузка в БД выполняется сидером: `backend/app/scripts/seed_section_contracts.py`.

### Где живёт в БД

Таблица `section_contracts` содержит:
- `required_facts_json`
- `allowed_sources_json`
- `retrieval_recipe_json`
- `qc_ruleset_json`
- `citation_policy`
- `version`
- `is_active`

### MVP shape (Lean Passport)

#### 1) `required_facts_json`

Только список `facts[]`:

```json
{
  "facts": [
    {
      "fact_key": "study.design_type",
      "required": true,
      "min_status": "extracted",
      "expected_type": "string",
      "unit_allowed": ["days"],
      "aliases": ["design"],
      "family": "study"
    }
  ]
}
```

#### 2) `allowed_sources_json`

```json
{
  "dependency_sources": [
    {
      "doc_type": "protocol",
      "section_keys": ["protocol.soa"],
      "required": true,
      "role": "primary",
      "precedence": 0,
      "min_mapping_confidence": 0.6,
      "allowed_content_types": ["cell", "tbl", "p"]
    }
  ],
  "document_scope": {
    "same_study_only": true,
    "allow_superseded": false
  }
}
```

#### 3) `retrieval_recipe_json`

Все «тонкие» настройки захардкожены в коде. Оставляем только:

```json
{
  "language": { "mode": "auto" },
  "context_build": { "max_chars": 12000 },
  "prefer_content_types": ["cell"],
  "fallback_search": {
    "query_templates": {
      "ru": ["расписание процедур", "SoA"],
      "en": ["schedule of activities", "SoA"]
    }
  },
  "security": { "secure_mode_required": true }
}
```

#### 4) `qc_ruleset_json`

MVP QC phases: `input_qc`, `citation_qc`, опционально `numbers_match_facts`.

```json
{
  "phases": ["input_qc", "citation_qc", "numbers_match_facts"],
  "gate_policy": {
    "on_missing_required_fact": "blocked",
    "on_low_mapping_confidence": "blocked",
    "on_citation_missing": "fail"
  },
  "numbers_match_facts": true,
  "warnings": []
}
```

#### 5) `citation_policy`

**По умолчанию** используем `per_claim`.

### Seed / загрузка в БД

Запуск (из папки `backend/` или из корня — без разницы, путь к seed-dir по умолчанию вычисляется от репозитория):

```bash
python -m app.scripts.seed_section_contracts --workspace-id <UUID>
```

Опции:
- `--seed-dir`: альтернативный каталог с `*.json`.
- `--no-deactivate-others`: не деактивировать другие версии той же секции.


