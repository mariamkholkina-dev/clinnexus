"""Сервис для стилистической трансформации текста с помощью LLM.

USR-501, 502: Трансформация медицинских текстов для пациентов и отчетов.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.core.config import LLMProvider, settings
from app.core.logging import logger
from app.services.llm_client import LLMClient


class TextTransformer:
    """Сервис для трансформации текста с помощью LLM."""

    def __init__(self) -> None:
        """Инициализация трансформера."""
        self.llm_client: LLMClient | None = None
        # Инициализируем LLM клиент только если secure_mode=True
        if settings.secure_mode and settings.llm_provider:
            try:
                self.llm_client = LLMClient()
            except Exception as e:
                logger.warning(f"Не удалось инициализировать LLM клиент для TextTransformer: {e}")

    async def to_layman(self, text: str) -> str:
        """
        Переписывает медицинский текст простым языком для пациентов.

        Args:
            text: Медицинский текст для трансформации

        Returns:
            Текст, переписанный простым языком (или исходный текст при ошибке)
        """
        if not settings.secure_mode:
            logger.debug("SECURE_MODE=False, возвращаем mock-ответ для to_layman")
            return f"[Layman draft] {text[:100]}..."

        if not self.llm_client:
            logger.warning(
                "LLM клиент недоступен для to_layman, возвращаем исходный текст"
            )
            return text

        system_prompt = (
            "Ты - эксперт по адаптации медицинских текстов для пациентов. "
            "Переписывай медицинский текст простым языком, понятным пациентам (уровень 6-8 класса). "
            "Замени термины на простые слова (например, венепункция -> забор крови). "
            "Сохрани весь смысл и важные детали."
        )
        user_prompt = (
            f"Перепиши этот медицинский текст простым языком, понятным пациенту (уровень 6-8 класса). "
            f"Замени термины (венепункция -> забор крови). Сохрани смысл.\n\n"
            f"Текст:\n{text}"
        )

        return await self._call_llm(system_prompt, user_prompt, text)

    async def shift_tense_to_past(self, text: str) -> str:
        """
        Переписывает текст протокола из будущего/настоящего времени в прошедшее (стиль отчетности).

        Args:
            text: Текст протокола для трансформации

        Returns:
            Текст в прошедшем времени (или исходный текст при ошибке)
        """
        if not settings.secure_mode:
            logger.debug("SECURE_MODE=False, возвращаем mock-ответ для shift_tense_to_past")
            return f"[Past tense draft] {text[:100]}..."

        if not self.llm_client:
            logger.warning(
                "LLM клиент недоступен для shift_tense_to_past, возвращаем исходный текст"
            )
            return text

        system_prompt = (
            "You are an expert at rewriting clinical protocol text from future/present tense "
            "to past tense (reporting style). Keep all numbers, units, and data exactly consistent. "
            "Do not summarize or change the meaning."
        )
        user_prompt = (
            f"Rewrite the following clinical protocol text from future/present tense to past tense "
            f"(reporting style). Keep all numbers, units, and data exactly consistent. "
            f"Do not summarize.\n\n"
            f"Text:\n{text}"
        )

        return await self._call_llm(system_prompt, user_prompt, text)

    async def _call_llm(
        self, system_prompt: str, user_prompt: str, fallback_text: str
    ) -> str:
        """
        Вызывает LLM для трансформации текста.

        Args:
            system_prompt: Системный промпт
            user_prompt: Пользовательский промпт с текстом
            fallback_text: Исходный текст, который вернется при ошибке

        Returns:
            Трансформированный текст или исходный текст при ошибке
        """
        if not self.llm_client:
            return fallback_text

        request_id = str(uuid.uuid4())
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.info(
            f"[TextTransformer] Запрос к LLM (request_id={request_id}, "
            f"provider={self.llm_client.provider.value}, model={self.llm_client.model})"
        )

        try:
            # Определяем URL и заголовки в зависимости от провайдера
            if self.llm_client.provider == LLMProvider.AZURE_OPENAI:
                url = (
                    f"{self.llm_client.base_url}/openai/deployments/"
                    f"{self.llm_client.model}/chat/completions"
                )
                headers = {
                    "api-key": self.llm_client.api_key,
                    "Content-Type": "application/json",
                }
                payload = {
                    "messages": messages,
                    "temperature": self.llm_client.temperature,
                    "max_tokens": 4000,  # Больше токенов для текстовой трансформации
                }
            elif self.llm_client.provider == LLMProvider.OPENAI_COMPATIBLE:
                url = f"{self.llm_client.base_url}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.llm_client.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": self.llm_client.model,
                    "messages": messages,
                    "temperature": self.llm_client.temperature,
                    "max_tokens": 4000,
                }
            elif self.llm_client.provider == LLMProvider.YANDEXGPT:
                if (
                    not self.llm_client.base_url
                    or self.llm_client.base_url == "https://llm.api.cloud.yandex.net"
                ):
                    url = "https://llm.api.cloud.yandex.net/v1/chat/completions"
                else:
                    url = f"{self.llm_client.base_url.rstrip('/')}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.llm_client.api_key}",
                    "Content-Type": "application/json",
                }
                # Формируем modelUri для YandexGPT
                if self.llm_client.model.startswith("gpt://"):
                    model_uri = self.llm_client.model
                else:
                    model_uri = f"gpt://{self.llm_client.model}"
                payload = {
                    "model": model_uri,
                    "messages": messages,
                    "temperature": self.llm_client.temperature,
                    "max_tokens": 4000,
                }
            elif self.llm_client.provider == LLMProvider.LOCAL:
                # Пробуем сначала Ollama endpoint
                url = f"{self.llm_client.base_url}/api/chat"
                headers = {"Content-Type": "application/json"}
                payload = {
                    "model": self.llm_client.model,
                    "messages": messages,
                    "options": {"temperature": self.llm_client.temperature},
                    "stream": False,
                }
            else:
                logger.error(
                    f"Неподдерживаемый провайдер LLM: {self.llm_client.provider}"
                )
                return fallback_text

            # Выполняем запрос
            async with httpx.AsyncClient(timeout=self.llm_client.timeout_sec) as client:
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    response_data = response.json()
                except httpx.HTTPStatusError as e:
                    # Для LOCAL провайдера пробуем fallback на OpenAI-compatible endpoint
                    if (
                        self.llm_client.provider == LLMProvider.LOCAL
                        and e.response is not None
                        and e.response.status_code in (400, 404, 405)
                    ):
                        logger.warning(
                            f"[TextTransformer] LOCAL endpoint /api/chat недоступен, "
                            f"пробуем OpenAI-compatible endpoint (request_id={request_id})"
                        )
                        oa_url = f"{self.llm_client.base_url}/v1/chat/completions"
                        oa_headers = {"Content-Type": "application/json"}
                        if self.llm_client.api_key:
                            oa_headers["Authorization"] = (
                                f"Bearer {self.llm_client.api_key}"
                            )
                        oa_payload = {
                            "model": self.llm_client.model,
                            "messages": messages,
                            "temperature": self.llm_client.temperature,
                            "max_tokens": 4000,
                        }
                        response = await client.post(
                            oa_url, headers=oa_headers, json=oa_payload
                        )
                        response.raise_for_status()
                        response_data = response.json()
                    else:
                        error_body = ""
                        try:
                            if e.response is not None:
                                error_body = e.response.text[:500]
                        except Exception:
                            pass
                        logger.error(
                            f"[TextTransformer] Ошибка HTTP при запросе к LLM "
                            f"(request_id={request_id}, status={e.response.status_code if e.response else None}): "
                            f"{error_body}"
                        )
                        return fallback_text
                except httpx.ReadTimeout as e:
                    logger.error(
                        f"[TextTransformer] Таймаут при запросе к LLM "
                        f"(request_id={request_id}, timeout_sec={self.llm_client.timeout_sec})"
                    )
                    return fallback_text
                except Exception as e:
                    logger.error(
                        f"[TextTransformer] Неожиданная ошибка при запросе к LLM "
                        f"(request_id={request_id}): {e}",
                        exc_info=True,
                    )
                    return fallback_text

            # Извлекаем content из ответа
            if (
                self.llm_client.provider == LLMProvider.LOCAL
                and "message" in response_data
            ):
                content = response_data["message"].get("content", "")
            else:
                content = (
                    response_data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

            if not content:
                logger.warning(
                    f"[TextTransformer] Пустой ответ от LLM (request_id={request_id})"
                )
                return fallback_text

            # Очищаем возможные markdown блоки кода
            content = content.strip()
            if content.startswith("```"):
                parts = content.split("```")
                if len(parts) >= 3:
                    inner = parts[1].strip()
                    if "\n" in inner:
                        first_line, rest = inner.split("\n", 1)
                        if first_line.strip().lower() in ("text", "plain", "markdown"):
                            inner = rest.strip()
                    content = inner.strip()

            logger.info(
                f"[TextTransformer] Успешно получен трансформированный текст "
                f"(request_id={request_id}, length={len(content)})"
            )
            return content

        except Exception as e:
            logger.error(
                f"[TextTransformer] Ошибка при трансформации текста "
                f"(request_id={request_id}): {e}",
                exc_info=True,
            )
            return fallback_text

