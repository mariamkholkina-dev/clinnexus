#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Скрипт для генерации эмбеддингов мастер-топиков.

Загружает все топики из public.topics, у которых topic_embedding IS NULL,
формирует строку для векторизации и обновляет записи в БД.

Цель: поднять mapped_rate топиков с 7% до 80%.
"""

import asyncio
import selectors
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


async def get_embedding_from_openai(
    text: str,
    provider: LLMProvider,
    base_url: str,
    api_key: str,
    model: str = "text-embedding-3-small",
    timeout_sec: int = 30,
) -> list[float]:
    """
    Получает эмбеддинг текста через OpenAI API или совместимый сервис.
    
    Args:
        text: Текст для векторизации
        provider: Провайдер (AZURE_OPENAI, OPENAI_COMPATIBLE, LOCAL)
        base_url: Базовый URL API
        api_key: API ключ
        model: Модель для эмбеддингов (по умолчанию text-embedding-3-small)
        timeout_sec: Таймаут в секундах
    
    Returns:
        Список float значений эмбеддинга (1536 измерений для text-embedding-3-small)
    
    Raises:
        ValueError: При ошибках API
        httpx.HTTPError: При ошибках HTTP
    """
    # Нормализуем base_url
    base_url = base_url.rstrip("/")
    if provider == LLMProvider.OPENAI_COMPATIBLE and base_url.endswith("/v1"):
        base_url = base_url[: -len("/v1")]
    
    # Формируем URL и заголовки в зависимости от провайдера
    if provider == LLMProvider.AZURE_OPENAI:
        url = f"{base_url}/openai/deployments/{model}/embeddings"
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {"input": text}
    elif provider == LLMProvider.OPENAI_COMPATIBLE:
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


def format_topic_text(topic: Topic) -> str:
    """
    Формирует строку для векторизации топика.
    
    Формат: "Title: {title_ru}. Description: {description}. Aliases: {aliases_ru}"
    """
    parts = []
    
    if topic.title_ru:
        parts.append(f"Title: {topic.title_ru}")
    
    if topic.description:
        parts.append(f"Description: {topic.description}")
    
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
        parts.append(f"Aliases: {aliases_str}")
    
    if not parts:
        # Если нет данных, используем topic_key как fallback
        return f"Topic: {topic.topic_key}"
    
    return ". ".join(parts)


async def generate_embeddings_for_topics(
    db: AsyncSession,
    provider: LLMProvider | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = "text-embedding-3-small",
    dry_run: bool = False,
    batch_size: int = 10,
) -> dict[str, int]:
    """
    Генерирует эмбеддинги для всех топиков с NULL topic_embedding.
    
    Args:
        db: Сессия базы данных
        provider: Провайдер LLM (если None, берётся из settings)
        base_url: Базовый URL API (если None, берётся из settings)
        api_key: API ключ (если None, берётся из settings)
        model: Модель для эмбеддингов
        dry_run: Если True, только показывает, что будет сделано, без обновления БД
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
    
    # Загружаем топики с NULL topic_embedding
    stmt = select(Topic).where(Topic.topic_embedding.is_(None))
    result = await db.execute(stmt)
    topics = list(result.scalars().all())
    
    logger.info(f"Найдено {len(topics)} топиков без эмбеддингов")
    
    if not topics:
        logger.info("Все топики уже имеют эмбеддинги")
        return {"processed": 0, "updated": 0, "errors": 0}
    
    if dry_run:
        logger.info("=== DRY RUN MODE ===")
        logger.info("Будут обработаны следующие топики:")
        for topic in topics[:10]:  # Показываем первые 10
            text = format_topic_text(topic)
            logger.info(f"  - {topic.topic_key}: {text[:100]}...")
        if len(topics) > 10:
            logger.info(f"  ... и ещё {len(topics) - 10} топиков")
        return {"processed": len(topics), "updated": 0, "errors": 0}
    
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
                embedding = await get_embedding_from_openai(
                    text=text,
                    provider=provider,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )
                
                # Проверяем размерность (должно быть 1536 для text-embedding-3-small)
                if len(embedding) != 1536:
                    logger.warning(
                        f"Неожиданная размерность эмбеддинга для {topic.topic_key}: "
                        f"{len(embedding)} (ожидается 1536)"
                    )
                
                # Обновляем топик
                topic.topic_embedding = embedding
                stats["updated"] += 1
                logger.info(f"✓ Обновлён эмбеддинг для {topic.topic_key}")
                
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
        description="Генерация эмбеддингов для мастер-топиков",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Проверка (dry-run)
  python generate_topic_embeddings.py --dry-run
  
  # Генерация эмбеддингов
  python generate_topic_embeddings.py
  
  # Использование конкретной модели
  python generate_topic_embeddings.py --model text-embedding-3-large
        """
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать, что будет сделано, без обновления БД"
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
        choices=["azure_openai", "openai_compatible", "local"],
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
                stats = await generate_embeddings_for_topics(
                    db=db,
                    provider=provider,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    model=args.model,
                    dry_run=args.dry_run,
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

