#!/bin/bash
# Скрипт для копирования статических файлов из frontend контейнера в volume
# Использование: ./scripts/copy-frontend-static.sh

set -e

FRONTEND_CONTAINER="clinnexus-frontend"
VOLUME_NAME="clinnexus_frontend_static"

echo "Копирование статических файлов из frontend контейнера..."

# Проверяем, что frontend контейнер запущен
if ! docker ps | grep -q "$FRONTEND_CONTAINER"; then
    echo "Ошибка: Frontend контейнер не запущен"
    exit 1
fi

# Создаем временный контейнер для копирования
TEMP_CONTAINER="temp-copy-static-$$"

# Создаем контейнер с volume
docker run --rm -d \
    --name "$TEMP_CONTAINER" \
    -v "$VOLUME_NAME:/target" \
    alpine:latest \
    sleep 3600

# Копируем файлы из frontend контейнера во временный контейнер
echo "Копирование файлов..."
docker cp "$FRONTEND_CONTAINER:/app/.next/static" "$TEMP_CONTAINER:/target/" || {
    echo "Ошибка при копировании файлов"
    docker rm -f "$TEMP_CONTAINER" 2>/dev/null || true
    exit 1
}

# Останавливаем временный контейнер
docker rm -f "$TEMP_CONTAINER" 2>/dev/null || true

echo "✓ Статические файлы скопированы в volume"

