from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from app.core.config import settings

"""
Настройка логгера для приложения.
"""

logger = logging.getLogger("clinnexus")
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
# Важно: uvicorn может настраивать root logging через dictConfig.
# Чтобы наши debug-логи не пропадали, не полагаемся на propagation.
logger.propagate = False
logger.disabled = False

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)

# File handler: пишем в файл с timestamp в названии.
# Путь по умолчанию: backend/.data/logs/clinnexus_YYYYMMDD_HHMMSS.log
_logs_dir = Path(".data") / "logs"
_logs_dir.mkdir(parents=True, exist_ok=True)
_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file_path = _logs_dir / f"clinnexus_{_ts}.log"
file_handler = logging.FileHandler(_log_file_path, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(handler)
    logger.addHandler(file_handler)

# Приводим логи uvicorn/fastapi к одному уровню, чтобы DEBUG реально показывался в dev.
for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
    ext_logger = logging.getLogger(name)
    ext_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    ext_logger.disabled = False
    if not ext_logger.handlers:
        ext_logger.addHandler(handler)
        ext_logger.addHandler(file_handler)

# Также выравниваем root logger (на случай если uvicorn выставил его выше).
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Диагностическая строка при старте: показывает эффективные уровни.
logger.info(
    "Logging configured "
    f"(settings.log_level={settings.log_level!r}, "
    f"clinnexus_level={logging.getLevelName(logger.level)}, "
    f"root_level={logging.getLevelName(root_logger.level)}, "
    f"log_file={str(_log_file_path)})"
)

