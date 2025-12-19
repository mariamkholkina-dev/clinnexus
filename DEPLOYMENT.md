# Инструкция по развертыванию ClinNexus на сервере

Это руководство описывает процесс развертывания ClinNexus на Ubuntu сервере с использованием Docker и Docker Compose.

## Требования к серверу

- **ОС**: Ubuntu 20.04 LTS или новее (рекомендуется 22.04 LTS)
- **RAM**: минимум 4 GB (рекомендуется 8 GB)
- **CPU**: минимум 2 ядра (рекомендуется 4+)
- **Диск**: минимум 20 GB свободного места (рекомендуется 50+ GB для данных)
- **Сеть**: статический IP адрес или доменное имя

## Шаг 1: Подготовка сервера

### 1.1. Запуск скрипта подготовки

Скопируйте скрипт `scripts/setup-server.sh` на сервер и выполните:

```bash
sudo bash setup-server.sh
```

Скрипт автоматически установит:
- Docker и Docker Compose
- Настроит firewall (ufw)
- Создаст необходимые директории
- Настроит системные лимиты

### 1.2. Ручная установка (альтернатива)

Если предпочитаете установить вручную:

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Добавление пользователя в группу docker
sudo usermod -aG docker $USER
newgrp docker

# Проверка установки
docker --version
docker compose version
```

## Шаг 2: Клонирование репозитория

```bash
# Создание директории для приложения
sudo mkdir -p /opt/clinnexus
sudo chown $USER:$USER /opt/clinnexus

# Клонирование репозитория
cd /opt/clinnexus
git clone <your-repo-url> .

# Или копирование файлов через scp/sftp
```

## Шаг 3: Настройка переменных окружения

```bash
# Копирование примера конфигурации
cp env.prod.example .env.prod
# Или если файл называется .env.prod.example:
# cp .env.prod.example .env.prod

# Редактирование конфигурации
nano .env.prod
# или
vim .env.prod
```

**Важно**: Обязательно измените следующие параметры:

- `DB_PASSWORD` - надежный пароль для базы данных (минимум 16 символов)
- `NEXT_PUBLIC_API_BASE_URL` - URL вашего сервера (например, `http://your-domain.com/api`)
- `LLM_*` - настройки LLM, если планируете использовать

## Шаг 4: Сборка и запуск приложения

### 4.1. Сборка образов

```bash
# Сборка всех образов
docker compose -f docker-compose.prod.yml build

# Или через Makefile
make prod-build
```

### 4.2. Запуск миграций базы данных

```bash
# Запуск миграций
docker compose -f docker-compose.prod.yml run --rm backend \
    alembic -c /app/db/alembic.ini upgrade head
```

### 4.3. Запуск seed скриптов (опционально)

```bash
# Загрузка начальных данных
docker compose -f docker-compose.prod.yml run --rm backend \
    python -m app.scripts.seed
```

### 4.4. Запуск всех сервисов

```bash
# Запуск в фоновом режиме
docker compose -f docker-compose.prod.yml up -d

# Или через Makefile
make prod-up
```

### 4.5. Проверка статуса

```bash
# Просмотр статуса контейнеров
docker compose -f docker-compose.prod.yml ps

# Просмотр логов
docker compose -f docker-compose.prod.yml logs -f

# Или через Makefile
make prod-logs
```

## Шаг 5: Проверка работоспособности

### 5.1. Проверка health checks

```bash
# Проверка backend
curl http://localhost/health

# Проверка frontend
curl http://localhost

# Проверка API
curl http://localhost/api/health
```

### 5.2. Проверка через браузер

Откройте в браузере:
- Frontend: `http://your-server-ip` или `http://your-domain.com`
- API Docs: `http://your-server-ip/api/docs` или `http://your-domain.com/api/docs`

## Шаг 6: Настройка SSL (HTTPS)

### 6.1. Установка Certbot

```bash
sudo apt install certbot python3-certbot-nginx -y
```

### 6.2. Получение SSL сертификата

```bash
# Если используете nginx в Docker, нужно временно остановить контейнер nginx
docker compose -f docker-compose.prod.yml stop nginx

# Получение сертификата
sudo certbot certonly --standalone -d your-domain.com

# Запуск nginx обратно
docker compose -f docker-compose.prod.yml start nginx
```

### 6.3. Обновление nginx.conf

Раскомментируйте секцию HTTPS в `nginx/nginx.conf` и укажите правильные пути к сертификатам:

```nginx
ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
```

### 6.4. Монтирование сертификатов в Docker

Обновите `docker-compose.prod.yml`, раскомментировав volume для сертификатов:

```yaml
volumes:
  - /etc/letsencrypt:/etc/letsencrypt:ro
```

### 6.5. Перезапуск nginx

```bash
docker compose -f docker-compose.prod.yml restart nginx
```

### 6.6. Автоматическое обновление сертификатов

Добавьте в crontab:

```bash
sudo crontab -e
```

Добавьте строку:

```
0 3 * * * certbot renew --quiet && docker compose -f /opt/clinnexus/docker-compose.prod.yml restart nginx
```

## Шаг 7: Настройка автоматических бэкапов

### 7.1. Создание скрипта бэкапа

Создайте файл `/opt/clinnexus/scripts/backup-db.sh`:

```bash
#!/bin/bash
BACKUP_DIR="/opt/clinnexus/backups"
DATE=$(date +%Y%m%d_%H%M%S)
DB_NAME="clinnexus"
DB_USER="clinnexus"

docker compose -f /opt/clinnexus/docker-compose.prod.yml exec -T db \
    pg_dump -U $DB_USER $DB_NAME > "$BACKUP_DIR/backup_$DATE.sql"

# Удаление бэкапов старше 30 дней
find $BACKUP_DIR -name "backup_*.sql" -mtime +30 -delete
```

Сделайте скрипт исполняемым:

```bash
chmod +x /opt/clinnexus/scripts/backup-db.sh
```

### 7.2. Настройка cron для автоматических бэкапов

```bash
crontab -e
```

Добавьте строку для ежедневного бэкапа в 2:00:

```
0 2 * * * /opt/clinnexus/scripts/backup-db.sh
```

## Шаг 8: Мониторинг и логирование

### 8.1. Просмотр логов

```bash
# Все сервисы
docker compose -f docker-compose.prod.yml logs -f

# Конкретный сервис
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f nginx
```

### 8.2. Мониторинг ресурсов

```bash
# Использование ресурсов контейнерами
docker stats

# Использование диска
df -h
docker system df
```

## Обновление приложения

### Обновление кода

```bash
cd /opt/clinnexus

# Получение обновлений
git pull

# Пересборка образов
docker compose -f docker-compose.prod.yml build

# Остановка старых контейнеров
docker compose -f docker-compose.prod.yml down

# Запуск новых контейнеров
docker compose -f docker-compose.prod.yml up -d

# Запуск миграций (если есть новые)
docker compose -f docker-compose.prod.yml run --rm backend \
    alembic -c /app/db/alembic.ini upgrade head
```

## Остановка приложения

```bash
# Остановка всех сервисов
docker compose -f docker-compose.prod.yml down

# Или через Makefile
make prod-down

# Остановка с удалением volumes (ОСТОРОЖНО: удалит данные!)
# docker compose -f docker-compose.prod.yml down -v
```

## Устранение неполадок

### Проблема: Контейнеры не запускаются

```bash
# Проверка логов
docker compose -f docker-compose.prod.yml logs

# Проверка статуса
docker compose -f docker-compose.prod.yml ps

# Проверка конфигурации
docker compose -f docker-compose.prod.yml config
```

### Проблема: База данных не доступна

```bash
# Проверка подключения к БД
docker compose -f docker-compose.prod.yml exec db psql -U clinnexus -d clinnexus

# Проверка логов БД
docker compose -f docker-compose.prod.yml logs db
```

### Проблема: Backend не отвечает

```bash
# Проверка health check
curl http://localhost/health

# Проверка логов backend
docker compose -f docker-compose.prod.yml logs backend

# Проверка переменных окружения
docker compose -f docker-compose.prod.yml exec backend env | grep DB_
```

### Проблема: Frontend не загружается

```bash
# Проверка логов frontend
docker compose -f docker-compose.prod.yml logs frontend

# Проверка переменной NEXT_PUBLIC_API_BASE_URL
docker compose -f docker-compose.prod.yml exec frontend env | grep NEXT_PUBLIC
```

## Безопасность

### Рекомендации по безопасности

1. **Пароли**: Используйте надежные пароли для базы данных (минимум 16 символов)
2. **Firewall**: Убедитесь, что firewall настроен правильно
3. **SSL**: Всегда используйте HTTPS в production
4. **Обновления**: Регулярно обновляйте систему и Docker образы
5. **Бэкапы**: Настройте автоматические бэкапы базы данных
6. **Мониторинг**: Настройте мониторинг и алерты

### Ограничение доступа к БД

В production рекомендуется не открывать порт 5432 наружу. Убедитесь, что в `docker-compose.prod.yml` порт БД закомментирован:

```yaml
ports:
  # - "5432:5432"  # Закомментировано для безопасности
```

## Дополнительные ресурсы

- [Docker документация](https://docs.docker.com/)
- [Docker Compose документация](https://docs.docker.com/compose/)
- [Nginx документация](https://nginx.org/ru/docs/)
- [Let's Encrypt документация](https://letsencrypt.org/docs/)

## Поддержка

При возникновении проблем:
1. Проверьте логи: `docker compose -f docker-compose.prod.yml logs`
2. Проверьте статус контейнеров: `docker compose -f docker-compose.prod.yml ps`
3. Проверьте конфигурацию: `docker compose -f docker-compose.prod.yml config`

