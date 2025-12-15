from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.core.config import settings

"""
Локальное хранилище файлов для загруженных документов.
"""


def save_upload(file_content: bytes, version_id: str, filename: str) -> tuple[str, str]:
    """
    Сохраняет загруженный файл в локальное хранилище.

    Args:
        file_content: Содержимое файла
        version_id: UUID версии документа
        filename: Имя файла

    Returns:
        Кортеж (uri, sha256)
    """
    # Создаём директорию для версии
    version_dir = Path(settings.storage_base_path) / str(version_id)
    version_dir.mkdir(parents=True, exist_ok=True)

    # Сохраняем файл
    file_path = version_dir / filename
    file_path.write_bytes(file_content)

    # Вычисляем SHA256
    sha256 = hashlib.sha256(file_content).hexdigest()

    # Формируем URI (относительный путь от корня проекта)
    base_path = Path(settings.storage_base_path)
    if base_path.is_absolute():
        uri = str(file_path.relative_to(base_path.parent))
    else:
        uri = str(file_path)

    return uri, sha256

