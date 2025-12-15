from __future__ import annotations

import logging
import sys

from app.core.config import settings

"""
Настройка логгера для приложения.
"""

logger = logging.getLogger("clinnexus")
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
handler.setFormatter(formatter)

logger.addHandler(handler)

