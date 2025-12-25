#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Автономный скрипт для векторизации топиков.

1. Подключается к БД, находит все записи в public.topics
2. Для каждого топика формирует текст для эмбеддинга:
   "Тема: {title_ru}. Описание: {description}. Синонимы: {aliases_ru}"
3. Использует OpenAI-compatible API или YandexGPT для получения векторов.
   Размерность зависит от модели:
   - OpenAI text-embedding-3-small: 1536 измерений
   - YandexGPT text-search-doc/latest: 256 измерений
4. Обновляет поле topic_embedding в базе
5. Добавляет лог: "Updated embedding for topic: {topic_key}"

Важно: При переходе с OpenAI на YandexGPT (или наоборот) необходимо пересоздать
все эмбеддинги топиков, так как размерности векторов различаются (1536 vs 256).
Без этого semantic similarity будет возвращать 0.0 из-за несовпадения размерностей.

Примеры использования:
  # Использование настроек из .env (YandexGPT)
  python scripts/vectorize_topics.py
  
  # Явное указание провайдера и модели
  python scripts/vectorize_topics.py --provider yandexgpt --model folder-id/yandexgpt/latest
  # Модель автоматически преобразуется в emb://folder-id/text-search-doc/latest
"""

import asyncio
import sys
from pathlib import Path

# Добавляем backend в путь для импортов
backend_dir = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import LLMProvider, settings
from app.core.logging import logger
from app.db.models.topics import Topic


def format_topic_text(topic: Topic) -> str:
    """
    Формирует строку для векторизации топика.
    
    Формат: "Тема: {title_ru}. Описание: {description}. Синонимы: {aliases_ru}"
    """
    parts = []
    
    if topic.title_ru:
        parts.append(f"Тема: {topic.title_ru}")
    
    if topic.description:
        parts.append(f"Описание: {topic.description}")
    
    # Извлекаем aliases_ru из topic_profile_json
    aliases_ru = []
    if topic.topic_profile_json:
        profile = topic.topic_profile_json
        # Проверяем базовый профиль
        if "aliases_ru" in profile:
            aliases = profile["aliases_ru"]
            if isinstance(aliases, list):
                aliases_ru.extend(aliases)
        # Проверяем profiles_by_doc_type (может быть несколько doc_type)
        if "profiles_by_doc_type" in profile:
            for doc_type_profile in profile["profiles_by_doc_type"].values():
                if "aliases_ru" in doc_type_profile:
                    doc_aliases = doc_type_profile["aliases_ru"]
                    if isinstance(doc_aliases, list):
                        aliases_ru.extend(doc_aliases)
    
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    unique_aliases = []
    for alias in aliases_ru:
        if alias and alias not in seen:
            seen.add(alias)
            unique_aliases.append(alias)
    
    if unique_aliases:
        aliases_str = ", ".join(unique_aliases)
        parts.append(f"Синонимы: {aliases_str}")
    
    if not parts:
        # Если нет данных, используем topic_key как fallback
        return f"Тема: {topic.topic_key}"
    
    return ". ".join(parts)


async def get_embedding(
    text: str,
    provider: LLMProvider,
    base_url: str,
    api_key: str,
    model: str = "text-embedding-3-small",
    timeout_sec: int = 30,
) -> list[float]:
    """
    Получает эмбеддинг текста через OpenAI-compatible API или YandexGPT.
    
    Args:
        text: Текст для векторизации
        provider: Провайдер (AZURE_OPENAI, OPENAI_COMPATIBLE, LOCAL, YANDEXGPT)
        base_url: Базовый URL API
        api_key: API ключ
        model: Модель для эмбеддингов (по умолчанию text-embedding-3-small)
        timeout_sec: Таймаут в секундах
    
    Returns:
        Список float значений эмбеддинга.
        Размерность зависит от модели:
        - OpenAI text-embedding-3-small: 1536 измерений
        - YandexGPT text-search-doc/latest: 256 измерений
    
    Raises:
        ValueError: При ошибках API
        httpx.HTTPError: При ошибках HTTP
    """
    # Нормализуем base_url
    base_url = base_url.rstrip("/")
    
    # Формируем URL и заголовки в зависимости от провайдера
    if provider == LLMProvider.AZURE_OPENAI:
        url = f"{base_url}/openai/deployments/{model}/embeddings"
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {"input": text}
    elif provider == LLMProvider.OPENAI_COMPATIBLE:
        if base_url.endswith("/v1"):
            base_url = base_url[: -len("/v1")]
        url = f"{base_url}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "input": text,
        }
    elif provider == LLMProvider.LOCAL:
        # Для локальных серверов пробуем OpenAI-compatible endpoint
        url = f"{base_url}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "input": text,
        }
    elif provider == LLMProvider.YANDEXGPT:
        # YandexGPT поддерживает OpenAI-совместимый API для эмбеддингов
        # https://yandex.cloud/ru/docs/ai-studio/concepts/openai-compatibility
        # Endpoint: https://llm.api.cloud.yandex.net/v1/embeddings
        if not base_url or base_url == "https://llm.api.cloud.yandex.net":
            url = "https://llm.api.cloud.yandex.net/v1/embeddings"
        else:
            url = f"{base_url.rstrip('/')}/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Для YandexGPT используем модель в формате emb://folder-id/text-search-doc/latest
        # Извлекаем folder-id из модели (например, gpt://folder-id/yandexgpt/latest или folder-id/yandexgpt/latest)
        if model.startswith("emb://"):
            # Уже в правильном формате
            model_uri = model
        elif model.startswith("gpt://"):
            # Формат gpt://folder-id/yandexgpt/latest - извлекаем folder-id
            folder_id = model.replace("gpt://", "").split("/")[0]
            model_uri = f"emb://{folder_id}/text-search-doc/latest"
        elif "/" in model:
            # Формат folder-id/yandexgpt/latest - извлекаем folder-id
            folder_id = model.split("/")[0]
            model_uri = f"emb://{folder_id}/text-search-doc/latest"
        else:
            # Предполагаем, что это folder-id
            model_uri = f"emb://{model}/text-search-doc/latest"
        payload = {
            "model": model_uri,
            "input": text,
        }
    else:
        raise ValueError(f"Неподдерживаемый провайдер: {provider}")
    
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # Извлекаем эмбеддинг из ответа
            if "data" in data and len(data["data"]) > 0:
                embedding = data["data"][0]["embedding"]
                if isinstance(embedding, list) and len(embedding) > 0:
                    return embedding
                else:
                    raise ValueError(f"Неожиданный формат эмбеддинга: {type(embedding)}")
            else:
                raise ValueError(f"Пустой ответ от API: {data}")
        except httpx.HTTPStatusError as e:
            error_text = e.response.text if e.response else "Нет ответа"
            raise ValueError(f"Ошибка HTTP {e.response.status_code}: {error_text}")
        except httpx.ReadTimeout:
            raise ValueError(f"Таймаут при получении эмбеддинга (>{timeout_sec} сек)")


async def vectorize_topics(
    db: AsyncSession,
    provider: LLMProvider | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = "text-embedding-3-small",
    batch_size: int = 10,
) -> dict[str, int]:
    """
    Векторизует все топики в базе данных.
    
    Args:
        db: Сессия базы данных
        provider: Провайдер LLM (если None, берётся из settings)
        base_url: Базовый URL API (если None, берётся из settings)
        api_key: API ключ (если None, берётся из settings)
        model: Модель для эмбеддингов
        batch_size: Размер батча для обработки
    
    Returns:
        Словарь со статистикой: {"processed": N, "updated": M, "errors": K}
    """
    # Используем настройки из config, если не указаны явно
    provider = provider or settings.llm_provider
    base_url = base_url or settings.llm_base_url
    api_key = api_key or settings.llm_api_key
    
    if not provider:
        raise ValueError("LLM provider не задан. Установите LLM_PROVIDER в .env")
    if not base_url:
        raise ValueError("LLM base_url не задан. Установите LLM_BASE_URL в .env")
    if not api_key:
        raise ValueError("LLM api_key не задан. Установите LLM_API_KEY в .env")
    
    # Загружаем все топики из public.topics
    stmt = select(Topic)
    result = await db.execute(stmt)
    topics = list(result.scalars().all())
    
    logger.info(f"Найдено {len(topics)} топиков в базе данных")
    
    if not topics:
        logger.info("Топики не найдены")
        return {"processed": 0, "updated": 0, "errors": 0}
    
    stats = {"processed": 0, "updated": 0, "errors": 0}
    
    # Обрабатываем топики батчами
    for i in range(0, len(topics), batch_size):
        batch = topics[i : i + batch_size]
        logger.info(f"Обработка батча {i // batch_size + 1} ({len(batch)} топиков)")
        
        for topic in batch:
            try:
                # Формируем текст для векторизации
                text = format_topic_text(topic)
                logger.debug(f"Генерация эмбеддинга для {topic.topic_key}: {text[:100]}...")
                
                # Получаем эмбеддинг
                embedding = await get_embedding(
                    text=text,
                    provider=provider,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )
                
                # Проверяем размерность в зависимости от провайдера
                expected_dim = 1536  # OpenAI по умолчанию
                if provider == LLMProvider.YANDEXGPT:
                    expected_dim = 256  # YandexGPT возвращает 256 измерений
                
                if len(embedding) != expected_dim:
                    logger.warning(
                        f"Неожиданная размерность эмбеддинга для {topic.topic_key}: "
                        f"{len(embedding)} (ожидается {expected_dim} для {provider.value})"
                    )
                else:
                    logger.debug(
                        f"Размерность эмбеддинга для {topic.topic_key}: {len(embedding)} "
                        f"(ожидается {expected_dim} для {provider.value})"
                    )
                
                # Обновляем топик
                topic.topic_embedding = embedding
                stats["updated"] += 1
                logger.info(f"Updated embedding for topic: {topic.topic_key}")
                
            except Exception as e:
                stats["errors"] += 1
                logger.error(f"✗ Ошибка при обработке {topic.topic_key}: {e}")
        
        stats["processed"] += len(batch)
        
        # Коммитим батч
        try:
            await db.commit()
            logger.info(f"Батч {i // batch_size + 1} сохранён в БД")
        except Exception as e:
            logger.error(f"Ошибка при сохранении батча {i // batch_size + 1}: {e}")
            await db.rollback()
            # Помечаем топики из батча как необработанные
            stats["updated"] -= len(batch)
            stats["errors"] += len(batch)
    
    return stats


async def main() -> None:
    """Главная функция скрипта."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Векторизация топиков",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Векторизация всех топиков
  python vectorize_topics.py
  
  # Использование конкретной модели
  python vectorize_topics.py --model text-embedding-3-large
  
  # Использование YandexGPT для эмбеддингов (256 измерений)
  python vectorize_topics.py --provider yandexgpt --model folder-id/yandexgpt/latest
  # Модель автоматически преобразуется в emb://folder-id/text-search-doc/latest
        """
    )
    
    parser.add_argument(
        "--model",
        default="text-embedding-3-small",
        help="Модель для эмбеддингов (по умолчанию: text-embedding-3-small)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Размер батча для обработки (по умолчанию: 10)"
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["azure_openai", "openai_compatible", "local", "yandexgpt"],
        help="Провайдер LLM (если не указан, берётся из .env)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        help="Базовый URL API (если не указан, берётся из .env)"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="API ключ (если не указан, берётся из .env)"
    )
    
    args = parser.parse_args()
    
    # Преобразуем provider в enum, если указан
    provider = None
    if args.provider:
        provider = LLMProvider(args.provider)
    
    # Подключаемся к БД
    engine = create_async_engine(settings.async_database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with session_factory() as db:
            try:
                stats = await vectorize_topics(
                    db=db,
                    provider=provider,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    model=args.model,
                    batch_size=args.batch_size,
                )
                
                logger.info("=" * 80)
                logger.info("ИТОГОВАЯ СТАТИСТИКА")
                logger.info("=" * 80)
                logger.info(f"Обработано топиков: {stats['processed']}")
                logger.info(f"Обновлено эмбеддингов: {stats['updated']}")
                logger.info(f"Ошибок: {stats['errors']}")
                
                if stats["errors"] > 0:
                    sys.exit(1)
                    
            except Exception as e:
                logger.error(f"Критическая ошибка: {e}")
                import traceback
                traceback.print_exc()
                sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    # Настройка event loop для Windows (psycopg требует SelectorEventLoop)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())

