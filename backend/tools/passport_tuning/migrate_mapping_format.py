"""
Скрипт для миграции cluster_to_section_key.json в новый формат с mapping_mode и notes.

Использование:
    python -m app.tools.passport_tuning.migrate_mapping_format

Или из корня backend:
    python tools/passport_tuning/migrate_mapping_format.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Добавляем корень backend в путь для импортов
backend_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_root))

from app.core.config import settings


def migrate_mapping_file(mapping_file: Path | None = None) -> None:
    """
    Мигрирует файл маппинга в новый формат.
    
    Добавляет дефолтные значения:
    - mapping_mode: "single" (если отсутствует)
    - notes: null (если отсутствует)
    
    Args:
        mapping_file: Путь к файлу маппинга. Если None, используется путь из настроек.
    """
    if mapping_file is None:
        mapping_path = Path(settings.passport_tuning_mapping_path)
        if not mapping_path.is_absolute():
            mapping_file = backend_root / mapping_path
        else:
            mapping_file = mapping_path
    
    if not mapping_file.exists():
        print(f"⚠️  Файл не найден: {mapping_file}")
        print("   Создайте файл или проверьте путь в настройках.")
        return
    
    print(f"📖 Чтение файла: {mapping_file}")
    
    try:
        with open(mapping_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга JSON: {e}")
        return
    except Exception as e:
        print(f"❌ Ошибка чтения файла: {e}")
        return
    
    # Подсчитываем изменения
    updated_count = 0
    total_count = len(data)
    
    # Добавляем дефолты для старых записей
    for cluster_id, entry in data.items():
        updated = False
        
        if "mapping_mode" not in entry:
            entry["mapping_mode"] = "single"
            updated = True
        
        if "notes" not in entry:
            entry["notes"] = None
            updated = True
        
        if updated:
            updated_count += 1
    
    if updated_count == 0:
        print("✅ Файл уже в новом формате, изменений не требуется.")
        return
    
    # Создаем резервную копию
    backup_file = mapping_file.with_suffix(".json.backup")
    print(f"💾 Создание резервной копии: {backup_file}")
    
    try:
        with open(backup_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка создания резервной копии: {e}")
        return
    
    # Сохраняем обновленный файл
    print(f"💾 Сохранение обновленного файла: {mapping_file}")
    
    try:
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Ошибка сохранения файла: {e}")
        print(f"   Восстановите из резервной копии: {backup_file}")
        return
    
    print(f"✅ Миграция завершена успешно!")
    print(f"   Обновлено записей: {updated_count} из {total_count}")
    print(f"   Резервная копия: {backup_file}")


if __name__ == "__main__":
    print("🔄 Миграция cluster_to_section_key.json в новый формат")
    print("=" * 60)
    migrate_mapping_file()
    print("=" * 60)

