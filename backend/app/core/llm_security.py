from __future__ import annotations

from fastapi import Header


def maybe_get_byo_key(
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-API-Key"),
) -> str | None:
    """
    Возвращает BYO API-ключ из заголовка `X-LLM-API-Key`.

    Важно:
    - ключ НЕ логируем и не сохраняем;
    - пробелы по краям обрезаем;
    - пустую строку считаем отсутствием ключа (None).
    """
    if not x_llm_api_key:
        return None
    key = x_llm_api_key.strip()
    return key or None


