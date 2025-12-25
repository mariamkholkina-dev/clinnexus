"""Value Normalizer на базе LLM для двойной проверки извлеченных значений фактов.

Обеспечивает GxP-совместимый двойной контроль (Double Check):
1. Regex-правило находит потенциальное значение
2. Если формат значения сложный - передаем в LLM для нормализации
3. Сравниваем результат LLM с результатом Regex
4. Если совпадают - статус 'validated', если нет - 'conflicting'
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.db.enums import FactStatus
from app.services.fact_extraction_rules import ExtractedFactCandidate, parse_date_to_iso
from app.services.llm_client import LLMClient


class ValueNormalizationResult:
    """Результат нормализации значения через LLM."""

    def __init__(
        self,
        normalized_value: dict[str, Any] | None,
        status: FactStatus,
        llm_value: dict[str, Any] | None = None,
        match: bool = False,
        llm_confidence: float = 0.0,
    ) -> None:
        self.normalized_value = normalized_value
        self.status = status
        self.llm_value = llm_value
        self.match = match
        self.llm_confidence = llm_confidence


class ValueNormalizer:
    """Сервис для нормализации значений фактов через LLM (Double Check для GxP)."""

    def __init__(self) -> None:
        """Инициализация нормализатора."""
        self.llm_client: LLMClient | None = None
        if settings.secure_mode and settings.llm_provider:
            try:
                self.llm_client = LLMClient()
            except Exception as e:
                logger.warning(f"Не удалось инициализировать LLM клиент для Value Normalizer: {e}")

    def _is_complex_value(self, candidate: ExtractedFactCandidate, text_fragment: str) -> bool:
        """
        Определяет, является ли значение сложным и требует ли LLM-нормализации.
        
        Критерии сложности:
        - Содержит несколько чисел (например, "120 участников, включая 20 в контрольной группе")
        - Содержит описательные фразы с числами
        - Длина raw_value > 50 символов
        - Содержит запятые, союзы, сложные конструкции
        """
        raw_value = candidate.raw_value or ""
        
        # Если raw_value слишком длинный - вероятно сложный
        if len(raw_value) > 50:
            return True
        
        # Если содержит несколько чисел
        numbers = re.findall(r'\d+', raw_value)
        if len(numbers) >= 2:
            return True
        
        # Если содержит сложные конструкции (запятые, союзы, предлоги)
        complex_patterns = [
            r'\b(?:включая|including|среди|among|из них|of which)\b',
            r'\b(?:в том числе|including|plus|плюс)\b',
            r',\s*\d+',  # запятая перед числом
            r'\d+\s*,\s*\d+',  # числа через запятую
        ]
        for pattern in complex_patterns:
            if re.search(pattern, raw_value, re.IGNORECASE):
                return True
        
        # Если value_json содержит вложенные структуры или массивы
        value_json = candidate.value_json or {}
        if isinstance(value_json, dict):
            # Если есть несколько ключей или вложенные объекты
            if len(value_json) > 2:
                return True
            for v in value_json.values():
                if isinstance(v, (list, dict)) and len(v) > 1:
                    return True
            # Если это список соотношений (для randomization_ratio)
            if "value" in value_json and isinstance(value_json.get("value"), list):
                # Список соотношений - это сложное значение, требует LLM для выбора главного
                return True
        
        return False

    async def normalize_value(
        self,
        candidate: ExtractedFactCandidate,
        text_fragment: str,
    ) -> ValueNormalizationResult:
        """
        Нормализует значение факта через LLM и сравнивает с regex-результатом.
        
        Args:
            candidate: Кандидат факта, извлеченный через regex
            text_fragment: Фрагмент текста, из которого было извлечено значение
            
        Returns:
            ValueNormalizationResult с нормализованным значением и статусом
        """
        # Если LLM недоступен, возвращаем исходное значение без валидации
        if not self.llm_client:
            logger.debug(
                f"LLM недоступен для нормализации факта {candidate.fact_type}.{candidate.fact_key}, "
                f"возвращаем исходное значение"
            )
            return ValueNormalizationResult(
                normalized_value=candidate.value_json,
                status=FactStatus.EXTRACTED,
                match=False,
                llm_confidence=0.0,
            )

        # Проверяем, является ли значение сложным
        if not self._is_complex_value(candidate, text_fragment):
            logger.debug(
                f"Значение факта {candidate.fact_type}.{candidate.fact_key} не является сложным, "
                f"пропускаем LLM-нормализацию"
            )
            return ValueNormalizationResult(
                normalized_value=candidate.value_json,
                status=FactStatus.EXTRACTED,
                match=False,
                llm_confidence=0.0,
            )

        try:
            # Проверяем, что text_fragment не пустой
            if not text_fragment or not text_fragment.strip():
                logger.warning(
                    f"Пустой text_fragment для факта {candidate.fact_type}.{candidate.fact_key}, "
                    f"пропускаем LLM-нормализацию"
                )
                return ValueNormalizationResult(
                    normalized_value=candidate.value_json,
                    status=FactStatus.EXTRACTED,
                    match=False,
                    llm_confidence=0.0,
                )
            
            # Формируем промпт для LLM
            fact_key = f"{candidate.fact_type}.{candidate.fact_key}"
            
            # Специальная обработка для randomization_ratio с несколькими соотношениями
            value_json = candidate.value_json or {}
            is_ratio_list = (
                isinstance(value_json, dict) 
                and "value" in value_json 
                and isinstance(value_json.get("value"), list)
                and fact_key == "study.design.randomization_ratio"
            )
            
            if is_ratio_list:
                # Специальный промпт для выбора главного соотношения из списка
                system_prompt = (
                    "Ты - эксперт по извлечению структурированных данных из клинических протоколов. "
                    "В тексте найдено несколько соотношений рандомизации для разных когорт. "
                    "Твоя задача - выбрать ГЛАВНОЕ соотношение (обычно первое упоминание или наиболее часто упоминаемое). "
                    "Верни только JSON объект с полем 'value' (одно соотношение в формате 'X:Y'), "
                    "без списка. Не добавляй пояснений или комментариев."
                )
                user_prompt = (
                    f"В тексте найдены следующие соотношения рандомизации: {', '.join(value_json.get('value', []))}. "
                    f"Выбери главное соотношение (обычно первое или наиболее часто упоминаемое в тексте). "
                    f"Текст: {text_fragment[:500] if text_fragment else ''}\n\n"
                    f"Верни только JSON объект, например: {{\"value\": \"2:1\"}}"
                )
            else:
                system_prompt = (
                    "Ты - эксперт по извлечению структурированных данных из клинических протоколов. "
                    "Твоя задача - извлечь строгое значение для указанного поля в формате JSON. "
                    "Верни только JSON объект с полем 'value' и дополнительными полями при необходимости. "
                    "Не добавляй пояснений или комментариев."
                )
                user_prompt = (
                    f"Извлеки из текста строгое значение для поля '{fact_key}' в формате JSON. "
                    f"Текст: {text_fragment[:500] if text_fragment else ''}\n\n"
                    f"Верни только JSON объект, например: {{\"value\": ...}}"
                )
            
            # Вызываем LLM
            logger.info(
                f"Запрос LLM для нормализации значения факта {fact_key} "
                f"(raw_value={candidate.raw_value[:100] if candidate.raw_value else None}, "
                f"is_ratio_list={is_ratio_list})"
            )
            
            # Создаем упрощенный запрос
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Вызываем LLM напрямую через httpx (упрощенный вариант)
            request_id = str(uuid.uuid4())
            
            # Используем внутренний метод LLM клиента для вызова
            if self.llm_client.provider.value == "azure_openai":
                url = f"{self.llm_client.base_url}/openai/deployments/{self.llm_client.model}/chat/completions"
                headers = {
                    "api-key": self.llm_client.api_key,
                    "Content-Type": "application/json",
                }
            elif self.llm_client.provider.value == "openai_compatible":
                url = f"{self.llm_client.base_url}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.llm_client.api_key}",
                    "Content-Type": "application/json",
                }
            elif self.llm_client.provider.value == "yandexgpt":
                # YandexGPT использует OpenAI-совместимый endpoint
                # Согласно документации: https://yandex.cloud/ru/docs/ai-studio/concepts/openai-compatibility
                if not self.llm_client.base_url or self.llm_client.base_url == "https://llm.api.cloud.yandex.net":
                    url = "https://llm.api.cloud.yandex.net/v1/chat/completions"
                else:
                    # Если указан кастомный base_url, используем его с /v1/chat/completions
                    url = f"{self.llm_client.base_url.rstrip('/')}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.llm_client.api_key}",
                    "Content-Type": "application/json",
                }
            else:  # local
                url = f"{self.llm_client.base_url}/api/chat"
                headers = {"Content-Type": "application/json"}
            
            # Формируем payload в зависимости от провайдера
            if self.llm_client.provider.value == "yandexgpt":
                # YandexGPT использует OpenAI-совместимый формат
                # Формируем modelUri для YandexGPT
                # Пользователь может указать модель в формате:
                # - "folder-id/yandexgpt/latest" -> преобразуется в "gpt://folder-id/yandexgpt/latest"
                # - "gpt://folder-id/yandexgpt/latest" -> используется как есть
                if not self.llm_client.model:
                    logger.error(f"Модель не указана для YandexGPT, пропускаем нормализацию факта {fact_key}")
                    return ValueNormalizationResult(
                        normalized_value=candidate.value_json,
                        status=FactStatus.EXTRACTED,
                        match=False,
                        llm_confidence=0.0,
                    )
                
                if self.llm_client.model.startswith("gpt://"):
                    model_uri = self.llm_client.model
                else:
                    model_uri = f"gpt://{self.llm_client.model}"
                
                # OpenAI-совместимый формат запроса
                payload = {
                    "model": model_uri,  # Используем modelUri в поле model для OpenAI-совместимости
                    "messages": messages,  # Стандартный формат OpenAI (role + content)
                    "temperature": self.llm_client.temperature,
                    "max_tokens": 500,
                }
                
                # Логируем payload для отладки (без секретных данных)
                logger.debug(
                    f"YandexGPT payload для факта {fact_key}: "
                    f"model={model_uri}, messages_count={len(messages)}, "
                    f"temperature={payload['temperature']}"
                )
            elif self.llm_client.provider.value == "local":
                payload = {
                    "model": self.llm_client.model,
                    "messages": messages,
                    "options": {"temperature": 0.0},
                    "stream": False,
                }
            else:
                payload = {
                    "model": self.llm_client.model,
                    "messages": messages,
                    "temperature": 0.0,  # Детерминированность для GxP
                    "max_tokens": 500,
                }

            async with httpx.AsyncClient(timeout=self.llm_client.timeout_sec) as client:
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    response_data = response.json()
                except httpx.HTTPStatusError as e:
                    error_body = ""
                    try:
                        if e.response is not None:
                            error_body = e.response.text[:1000]
                    except Exception:
                        pass
                    logger.error(
                        f"Ошибка HTTP при запросе к LLM для факта {fact_key}: "
                        f"status={e.response.status_code if e.response else None}, "
                        f"url={url}, error_body={error_body[:500]}, "
                        f"payload_keys={list(payload.keys()) if payload else None}"
                    )
                    raise
            
            # Извлекаем content из ответа
            if self.llm_client.provider.value == "local" and "message" in response_data:
                content = response_data["message"].get("content", "")
            elif self.llm_client.provider.value == "yandexgpt":
                # YandexGPT OpenAI-совместимый API возвращает ответ в стандартном формате OpenAI
                content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if not content:
                logger.warning(f"Пустой ответ от LLM для факта {fact_key}")
                return ValueNormalizationResult(
                    normalized_value=candidate.value_json,
                    status=FactStatus.EXTRACTED,
                    match=False,
                    llm_confidence=0.0,
                )

            # Парсим JSON из ответа
            content_str = str(content).strip()
            
            # Убираем markdown code blocks если есть
            if content_str.startswith("```"):
                parts = content_str.split("```")
                if len(parts) >= 3:
                    inner = parts[1].strip()
                    if "\n" in inner:
                        first_line, rest = inner.split("\n", 1)
                        if first_line.strip().lower() == "json":
                            inner = rest.strip()
                    content_str = inner.strip()
            
            # Извлекаем JSON объект
            if "{" in content_str and "}" in content_str:
                l = content_str.find("{")
                r = content_str.rfind("}")
                if l != -1 and r != -1 and r > l:
                    content_str = content_str[l : r + 1]
            
            try:
                llm_value_json = json.loads(content_str)
            except json.JSONDecodeError as e:
                logger.error(
                    f"Ошибка парсинга JSON от LLM для факта {fact_key}: {e}. "
                    f"Content: {content_str[:200]}"
                )
                return ValueNormalizationResult(
                    normalized_value=candidate.value_json,
                    status=FactStatus.EXTRACTED,
                    match=False,
                    llm_confidence=0.0,
                )

            # Проверяем, является ли значение LLM пустым или невалидным
            def _is_llm_value_empty(llm_value: dict[str, Any] | None) -> bool:
                """Проверяет, является ли значение LLM пустым."""
                if llm_value is None:
                    return True
                # Проверяем, есть ли ключ "value" и он не пустой
                if "value" in llm_value:
                    val = llm_value["value"]
                    if val is None:
                        return True
                    if isinstance(val, str) and not val.strip():
                        return True
                    if isinstance(val, list) and len(val) == 0:
                        return True
                    if isinstance(val, dict) and len(val) == 0:
                        return True
                # Если структура не содержит "value", проверяем сам словарь
                if isinstance(llm_value, dict) and len(llm_value) == 0:
                    return True
                return False
            
            llm_is_empty = _is_llm_value_empty(llm_value_json)
            
            # Сравниваем результат LLM с regex-результатом
            regex_value = candidate.value_json
            
            # Специальная обработка для списков соотношений
            # Если regex извлек список, а LLM вернула одно соотношение, проверяем, содержится ли оно в списке
            if is_ratio_list and isinstance(regex_value, dict) and "value" in regex_value:
                regex_ratios = regex_value.get("value", [])
                if isinstance(regex_ratios, list) and len(regex_ratios) > 0:
                    llm_ratio = llm_value_json.get("value") if isinstance(llm_value_json, dict) else None
                    # Если LLM вернула одно соотношение, проверяем, есть ли оно в списке regex
                    if llm_ratio and isinstance(llm_ratio, str):
                        # Нормализуем формат соотношения для сравнения
                        llm_ratio_norm = llm_ratio.replace("/", ":").strip()
                        match = any(
                            ratio.replace("/", ":").strip() == llm_ratio_norm 
                            for ratio in regex_ratios
                        )
                    else:
                        match = False
                else:
                    match = self._compare_values(regex_value, llm_value_json)
            else:
                match = self._compare_values(regex_value, llm_value_json)
            
            # Устанавливаем confidence для LLM (0.85 для успешного извлечения)
            llm_confidence = 0.85 if llm_value_json and not llm_is_empty else 0.0
            
            # Определяем финальное значение для возврата
            final_value = regex_value
            original_regex_value = candidate.value_json  # Сохраняем оригинальное значение для логирования
            
            # Вспомогательная функция для нормализации значений в логах (для дат)
            def _format_value_for_log(value: dict[str, Any] | None) -> str:
                """Форматирует значение для логирования, нормализуя даты в ISO."""
                if value is None:
                    return "None"
                if isinstance(value, dict) and "value" in value:
                    val = value["value"]
                    if isinstance(val, str):
                        # Проверяем, является ли это датой
                        iso_date = parse_date_to_iso(val)
                        if iso_date is not None:
                            return f"{{'value': '{iso_date}'}}"  # Показываем нормализованную дату
                return str(value)
            
            if is_ratio_list and match and not llm_is_empty:
                # Если LLM выбрала главное соотношение из списка и оно совпадает - используем его
                final_value = llm_value_json
                logger.info(
                    f"LLM выбрала главное соотношение для факта {fact_key}: "
                    f"из списка {original_regex_value.get('value', []) if isinstance(original_regex_value, dict) else []} "
                    f"выбрано {llm_value_json.get('value')}"
                )
            
            if match:
                logger.info(
                    f"Значения совпадают для факта {fact_key}: "
                    f"Regex={_format_value_for_log(original_regex_value)}, "
                    f"LLM={_format_value_for_log(llm_value_json)}"
                )
                status = FactStatus.VALIDATED
            elif llm_is_empty:
                # Если LLM вернула пустое значение, а regex нашел значение - это не конфликт,
                # а просто LLM не смогла извлечь. Используем regex-результат со статусом EXTRACTED
                logger.info(
                    f"LLM вернула пустое значение для факта {fact_key}, "
                    f"используем regex-результат: {_format_value_for_log(regex_value)}"
                )
                status = FactStatus.EXTRACTED
            else:
                # Значения не совпадают и LLM вернула не пустое значение - это конфликт
                logger.warning(
                    f"Значения НЕ совпадают для факта {fact_key}: "
                    f"Regex={_format_value_for_log(regex_value)}, "
                    f"LLM={_format_value_for_log(llm_value_json)}"
                )
                status = FactStatus.CONFLICTING

            return ValueNormalizationResult(
                normalized_value=final_value,  # Используем выбранное значение (LLM для списков соотношений)
                status=status,
                llm_value=llm_value_json,
                match=match,
                llm_confidence=llm_confidence,
            )

        except Exception as e:
            logger.error(
                f"Ошибка при нормализации значения факта {candidate.fact_type}.{candidate.fact_key}: {e}",
                exc_info=True,
            )
            # В случае ошибки возвращаем исходное значение без валидации
            return ValueNormalizationResult(
                normalized_value=candidate.value_json,
                status=FactStatus.EXTRACTED,
                match=False,
                llm_confidence=0.0,
            )

    def _compare_values(
        self, regex_value: dict[str, Any] | None, llm_value: dict[str, Any] | None
    ) -> bool:
        """
        Сравнивает значения, извлеченные regex и LLM.
        
        Учитывает:
        - Числовые значения (с небольшой погрешностью)
        - Строковые значения (нормализация пробелов)
        - Вложенные структуры
        """
        if regex_value is None and llm_value is None:
            return True
        if regex_value is None or llm_value is None:
            return False

        # Если оба - простые значения
        if "value" in regex_value and "value" in llm_value:
            regex_val = regex_value["value"]
            llm_val = llm_value["value"]

            # Числовые значения
            if isinstance(regex_val, (int, float)) and isinstance(llm_val, (int, float)):
                # Допускаем небольшую погрешность для float
                if isinstance(regex_val, float) or isinstance(llm_val, float):
                    return abs(float(regex_val) - float(llm_val)) < 0.01
                return regex_val == llm_val

            # Строковые значения (нормализуем пробелы и даты)
            if isinstance(regex_val, str) and isinstance(llm_val, str):
                # Проверяем, являются ли значения датами
                regex_iso = parse_date_to_iso(regex_val)
                llm_iso = parse_date_to_iso(llm_val)
                
                # Если оба значения являются датами, сравниваем ISO-строки
                if regex_iso is not None and llm_iso is not None:
                    return regex_iso == llm_iso
                
                # Если только одно значение является датой, они не совпадают
                if regex_iso is not None or llm_iso is not None:
                    return False
                
                # Для обычных строк нормализуем пробелы
                regex_norm = re.sub(r"\s+", " ", regex_val.strip().lower())
                llm_norm = re.sub(r"\s+", " ", llm_val.strip().lower())
                return regex_norm == llm_norm

            # Словари (рекурсивное сравнение)
            if isinstance(regex_val, dict) and isinstance(llm_val, dict):
                return self._compare_dicts(regex_val, llm_val)

            # Списки (сравниваем как множества для порядка-независимости)
            if isinstance(regex_val, list) and isinstance(llm_val, list):
                if len(regex_val) != len(llm_val):
                    return False
                # Для простых списков сравниваем множества
                if all(isinstance(x, (str, int, float)) for x in regex_val) and all(
                    isinstance(x, (str, int, float)) for x in llm_val
                ):
                    return set(regex_val) == set(llm_val)
                # Для сложных списков сравниваем поэлементно
                return all(
                    self._compare_values(
                        {"value": rv} if not isinstance(rv, dict) else rv,
                        {"value": lv} if not isinstance(lv, dict) else lv,
                    )
                    for rv, lv in zip(regex_val, llm_val)
                )

        # Если структуры разные (нет ключа "value"), сравниваем как словари напрямую
        if isinstance(regex_value, dict) and isinstance(llm_value, dict):
            return self._compare_dicts(regex_value, llm_value, depth=0)
        
        # Для остальных случаев - прямое сравнение
        return regex_value == llm_value

    def _compare_dicts(self, d1: dict[str, Any], d2: dict[str, Any], depth: int = 0) -> bool:
        """
        Рекурсивно сравнивает два словаря.
        
        Args:
            d1: Первый словарь для сравнения
            d2: Второй словарь для сравнения
            depth: Текущая глубина рекурсии (для защиты от бесконечной рекурсии)
        """
        # Защита от слишком глубокой рекурсии
        MAX_DEPTH = 50
        if depth > MAX_DEPTH:
            logger.warning(f"Превышена максимальная глубина рекурсии ({MAX_DEPTH}) при сравнении словарей")
            return False
        
        if set(d1.keys()) != set(d2.keys()):
            return False
        
        for key in d1.keys():
            val1 = d1[key]
            val2 = d2[key]
            
            # Прямое сравнение простых типов
            if isinstance(val1, (str, int, float, bool, type(None))) and isinstance(
                val2, (str, int, float, bool, type(None))
            ):
                if val1 != val2:
                    return False
                continue
            
            # Рекурсивное сравнение словарей
            if isinstance(val1, dict) and isinstance(val2, dict):
                if not self._compare_dicts(val1, val2, depth + 1):
                    return False
                continue
            
            # Рекурсивное сравнение списков
            if isinstance(val1, list) and isinstance(val2, list):
                if len(val1) != len(val2):
                    return False
                for v1, v2 in zip(val1, val2):
                    if isinstance(v1, dict) and isinstance(v2, dict):
                        if not self._compare_dicts(v1, v2, depth + 1):
                            return False
                    elif v1 != v2:
                        return False
                continue
            
            # Для остальных случаев - прямое сравнение
            if val1 != val2:
                return False
        
        return True

