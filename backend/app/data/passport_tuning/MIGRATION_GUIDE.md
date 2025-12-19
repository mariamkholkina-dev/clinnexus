# Руководство по миграции cluster_to_section_key.json

## Изменения формата

В маппинг кластеров добавлены два новых поля:

1. **`mapping_mode`** (обязательное, по умолчанию `"single"`):
   - `"single"` - однозначное соответствие (по умолчанию)
   - `"ambiguous"` - неоднозначное соответствие (исключается из автотюнинга)
   - `"skip"` - кластер пропущен (исключается из автотюнинга)
   - `"needs_split"` - требуется разделение кластера (по умолчанию исключается из автотюнинга)

2. **`notes`** (опциональное, строка до 500 символов):
   - Комментарий или причина выбора режима маппинга

## Автоматическая миграция

**Хорошая новость**: код поддерживает обратную совместимость!

- При чтении старых записей без `mapping_mode` автоматически устанавливается `"single"`
- При чтении старых записей без `notes` устанавливается `null`
- При следующем сохранении через API файл автоматически обновится до нового формата

## Формат записи

### Старый формат (поддерживается):
```json
{
  "0": {
    "doc_type": "protocol",
    "section_key": "protocol.references",
    "title_ru": "Ссылки и литература"
  }
}
```

### Новый формат:
```json
{
  "0": {
    "doc_type": "protocol",
    "section_key": "protocol.references",
    "title_ru": "Ссылки и литература",
    "mapping_mode": "single",
    "notes": null
  }
}
```

## Примеры для разных режимов

### Однозначное соответствие (single):
```json
{
  "1": {
    "doc_type": "protocol",
    "section_key": "protocol.endpoints",
    "title_ru": "Конечные точки",
    "mapping_mode": "single",
    "notes": null
  }
}
```

### Неоднозначное соответствие (ambiguous):
```json
{
  "2": {
    "doc_type": "protocol",
    "section_key": "protocol.objectives",
    "title_ru": "Цели",
    "mapping_mode": "ambiguous",
    "notes": "Неоднозначное соответствие, требуется уточнение"
  }
}
```

### Пропущенный кластер (skip):
```json
{
  "3": {
    "doc_type": "other",
    "section_key": "",
    "title_ru": null,
    "mapping_mode": "skip",
    "notes": "Кластер пропущен, не требует маппинга"
  }
}
```

### Требуется разделение (needs_split):
```json
{
  "4": {
    "doc_type": "protocol",
    "section_key": "protocol.methods",
    "title_ru": "Методы исследования",
    "mapping_mode": "needs_split",
    "notes": "Рекомендуется разделить кластер на несколько секций"
  }
}
```

## Ручная миграция (опционально)

Если вы хотите вручную обновить существующий файл:

1. Откройте `backend/app/data/passport_tuning/cluster_to_section_key.json`
2. Для каждой записи добавьте:
   - `"mapping_mode": "single"` (если не указан другой режим)
   - `"notes": null` (если комментарий не нужен)

Пример скрипта для автоматической миграции (Python):
```python
import json
from pathlib import Path

mapping_file = Path("backend/app/data/passport_tuning/cluster_to_section_key.json")

with open(mapping_file, "r", encoding="utf-8") as f:
    data = json.load(f)

# Добавляем дефолты для старых записей
for cluster_id, entry in data.items():
    if "mapping_mode" not in entry:
        entry["mapping_mode"] = "single"
    if "notes" not in entry:
        entry["notes"] = None

# Сохраняем обновленный файл
with open(mapping_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
```

## Валидация

- Для режима `"skip"`: разрешены пустые `section_key` и `doc_type="other"`
- Для остальных режимов: `section_key` обязателен
- `notes` опционален, но рекомендуется заполнять для `ambiguous`, `needs_split` и `skip`

## Использование в автотюнинге

Endpoint `/api/passport-tuning/mapping/for_autotune` возвращает:
- **included**: только кластеры с `mapping_mode="single"` (и опционально `"needs_split"` если `include_needs_split=true`)
- **excluded**: списки кластеров с `"ambiguous"`, `"skip"` и `"needs_split"`

Кластеры с `mapping_mode="ambiguous"` и `"skip"` всегда исключаются из автотюнинга.

