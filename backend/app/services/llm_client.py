"""LLM клиент для section mapping assist."""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import LLMProvider, settings
from app.core.logging import logger


class LLMCandidate(BaseModel):
    """Кандидат заголовка от LLM."""

    heading_anchor_id: str
    confidence: float
    rationale: str


class LLMCandidatesResponse(BaseModel):
    """Ответ LLM с кандидатами."""

    candidates: dict[str, list[LLMCandidate]]


class LLMClient:
    """Клиент для вызова LLM API."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        """
        Инициализация LLM клиента.

        Args:
            provider: Провайдер LLM
            base_url: Базовый URL API
            api_key: API ключ
            model: Модель LLM
            temperature: Температура (0.0 для детерминированности)
            timeout_sec: Таймаут в секундах
        """
        self.provider = provider or settings.llm_provider
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.temperature = temperature if temperature is not None else settings.llm_temperature
        self.timeout_sec = timeout_sec or settings.llm_timeout_sec

        if not self.provider:
            raise ValueError("LLM provider не задан")
        if not self.base_url:
            raise ValueError("LLM base_url не задан")
        if not self.api_key:
            raise ValueError("LLM api_key не задан")

        # Нормализация base_url:
        # - для openai_compatible мы сами добавляем "/v1/..." в _call_openai_compatible,
        #   поэтому если пользователь уже указал base_url с "/v1", убираем его, чтобы
        #   не получить "/v1/v1/chat/completions".
        # - в целом избавляемся от завершающего "/".
        self.base_url = self.base_url.rstrip("/")
        if self.provider == LLMProvider.OPENAI_COMPATIBLE and self.base_url.endswith("/v1"):
            self.base_url = self.base_url[: -len("/v1")]

    async def generate_candidates(
        self,
        system_prompt: str,
        user_prompt: dict[str, Any],
        request_id: str | None = None,
    ) -> LLMCandidatesResponse:
        """
        Генерирует кандидатов заголовков через LLM.

        Args:
            system_prompt: Системный промпт
            user_prompt: Пользовательский промпт (JSON объект)
            request_id: ID запроса для логирования

        Returns:
            LLMCandidatesResponse с кандидатами

        Raises:
            ValueError: Если ответ невалиден
            httpx.HTTPError: При ошибках HTTP
        """
        request_id = request_id or str(uuid.uuid4())
        logger.info(
            f"[LLM] Запрос кандидатов (request_id={request_id}, provider={self.provider.value}, "
            f"model={self.model})"
        )

        # Формируем сообщения
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False, indent=2)},
        ]

        # Вызываем LLM в зависимости от провайдера
        if self.provider == LLMProvider.AZURE_OPENAI:
            response_data = await self._call_azure_openai(messages, request_id)
        elif self.provider == LLMProvider.OPENAI_COMPATIBLE:
            response_data = await self._call_openai_compatible(messages, request_id)
        elif self.provider == LLMProvider.LOCAL:
            response_data = await self._call_local(messages, request_id)
        else:
            raise ValueError(f"Неподдерживаемый провайдер: {self.provider}")

        # Парсим и валидируем ответ
        try:
            # Извлекаем content из ответа
            content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                raise ValueError("Пустой ответ от LLM")

            # Парсим JSON.
            # На практике LLM может оборачивать JSON в ```json ... ``` или добавлять текст.
            # Стараемся аккуратно извлечь JSON-объект.
            content_str = str(content).strip()
            if content_str.startswith("```"):
                # Убираем ограждения кода, если они есть
                # Примеры:
                # ```json\n{...}\n```
                # ```\n{...}\n```
                parts = content_str.split("```")
                # Берём первую "внутреннюю" часть
                if len(parts) >= 3:
                    inner = parts[1].strip()
                    # Если первая строка — "json", выкидываем её
                    if "\n" in inner:
                        first_line, rest = inner.split("\n", 1)
                        if first_line.strip().lower() == "json":
                            inner = rest.strip()
                    content_str = inner.strip()

            # Если остался текст вокруг JSON — пытаемся взять диапазон от первой "{" до последней "}"
            if "{" in content_str and "}" in content_str:
                l = content_str.find("{")
                r = content_str.rfind("}")
                if l != -1 and r != -1 and r > l:
                    content_str = content_str[l : r + 1]

            # Парсим JSON
            try:
                response_json = json.loads(content_str)
            except json.JSONDecodeError as e:
                preview = content_str[:800].replace("\n", "\\n")
                logger.error(
                    f"[LLM] Ошибка парсинга JSON (request_id={request_id}): {e}; "
                    f"content_preview[:800]={preview!r}"
                )
                raise ValueError(f"Невалидный JSON от LLM: {e}")

            # Валидируем через Pydantic
            validated = LLMCandidatesResponse.model_validate(response_json)
            logger.info(
                f"[LLM] Успешно получены кандидаты (request_id={request_id}, "
                f"sections={len(validated.candidates)})"
            )
            return validated

        except ValidationError as e:
            logger.error(f"[LLM] Ошибка валидации ответа (request_id={request_id}): {e}")
            raise ValueError(f"Ответ LLM не соответствует схеме: {e}")

    async def _call_azure_openai(
        self, messages: list[dict[str, str]], request_id: str
    ) -> dict[str, Any]:
        """Вызов Azure OpenAI API."""
        url = f"{self.base_url}/openai/deployments/{self.model}/chat/completions"
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": 2000,
        }

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    async def _call_openai_compatible(
        self, messages: list[dict[str, str]], request_id: str
    ) -> dict[str, Any]:
        """Вызов OpenAI-compatible API."""
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": 2000,
        }

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    async def _call_local(
        self, messages: list[dict[str, str]], request_id: str
    ) -> dict[str, Any]:
        """
        Вызов локального LLM API.

        Поддерживаем два популярных варианта:
        - Ollama (`POST /api/chat`) — формат отличается, нормализуем к OpenAI-like `choices`.
        - OpenAI-compatible локальные рантаймы (например, LM Studio, vLLM, text-generation-webui),
          которые часто экспонируют `POST /v1/chat/completions`.

        Важно: в dev окружениях нередко ставят LLM_PROVIDER=local, но BASE_URL указывает
        на OpenAI-compatible сервер. В таком случае делаем fallback автоматически.
        """
        ollama_url = f"{self.base_url}/api/chat"
        headers = {"Content-Type": "application/json"}
        ollama_payload = {
            "model": self.model,
            "messages": messages,
            "options": {"temperature": self.temperature},
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            try:
                response = await client.post(ollama_url, headers=headers, json=ollama_payload)
                response.raise_for_status()
                result = response.json()
                # Ollama возвращает ответ в другом формате
                if "message" in result:
                    return {
                        "choices": [
                            {
                                "message": {
                                    "content": result["message"].get("content", ""),
                                }
                            }
                        ]
                    }
                # Иногда локальные прокси могут вернуть уже OpenAI-like формат — просто отдаём как есть
                return result
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else None
                body_preview = ""
                try:
                    body_preview = (e.response.text or "")[:500] if e.response is not None else ""
                except Exception:
                    body_preview = ""

                # Если /api/chat не поддерживается (часто 404), пробуем OpenAI-compatible endpoint
                if status in (400, 404, 405):
                    logger.warning(
                        f"[LLM] LOCAL endpoint /api/chat недоступен (status={status}) — "
                        f"пробуем OpenAI-compatible /v1/chat/completions (request_id={request_id}). "
                        f"body[:500]={body_preview!r}"
                    )
                else:
                    logger.warning(
                        f"[LLM] Ошибка LOCAL /api/chat (status={status}) — "
                        f"пробуем fallback /v1/chat/completions (request_id={request_id}). "
                        f"body[:500]={body_preview!r}"
                    )

            # Fallback: OpenAI-compatible без жёсткой зависимости от auth.
            # Некоторые локальные рантаймы требуют Bearer (можно передать любой), некоторые игнорируют.
            oa_url = f"{self.base_url}/v1/chat/completions"
            oa_headers = {"Content-Type": "application/json"}
            if self.api_key:
                oa_headers["Authorization"] = f"Bearer {self.api_key}"
            oa_payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": 2000,
            }

            response2 = await client.post(oa_url, headers=oa_headers, json=oa_payload)
            response2.raise_for_status()
            return response2.json()

