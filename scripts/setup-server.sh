#!/bin/bash
# Скрипт подготовки Ubuntu сервера для развертывания ClinNexus
# Использование: sudo bash setup-server.sh
#
# Этот скрипт устанавливает все необходимое для развертывания ClinNexus:
# - Docker и Docker Compose
# - Настраивает firewall
# - Создает необходимые директории
# - Настраивает системные параметры

set -e  # Остановка при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=========================================="
echo "Подготовка Ubuntu сервера для ClinNexus"
echo "==========================================${NC}"

# Проверка, что скрипт запущен от root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Ошибка: Скрипт должен быть запущен с правами root (sudo)${NC}"
    exit 1
fi

# Проверка версии Ubuntu
echo ""
echo "[0/9] Проверка версии Ubuntu..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "$ID" != "ubuntu" ]; then
        echo -e "${YELLOW}Предупреждение: Этот скрипт предназначен для Ubuntu. Продолжить? (y/n)${NC}"
        read -p "" -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
    echo "Обнаружена ОС: $PRETTY_NAME"
else
    echo -e "${YELLOW}Не удалось определить версию ОС. Продолжить? (y/n)${NC}"
    read -p "" -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Обновление системы
echo ""
echo -e "${GREEN}[1/9] Обновление системы...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

# Установка базовых пакетов
apt-get install -y -qq \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    software-properties-common \
    git \
    wget \
    unzip \
    ufw \
    jq

echo -e "${GREEN}✓ Система обновлена${NC}"

# Установка Docker
echo ""
echo -e "${GREEN}[2/9] Установка Docker...${NC}"
if ! command -v docker &> /dev/null; then
    echo "Установка Docker..."
    
    # Добавление официального GPG ключа Docker
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg

    # Добавление репозитория Docker
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Установка Docker Engine
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Запуск и автозапуск Docker
    systemctl start docker
    systemctl enable docker > /dev/null 2>&1

    echo -e "${GREEN}✓ Docker установлен успешно${NC}"
else
    echo -e "${YELLOW}Docker уже установлен${NC}"
fi

# Проверка версии Docker
DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | tr -d ',')
echo "  Версия Docker: $DOCKER_VERSION"

# Проверка Docker Compose
echo ""
echo -e "${GREEN}[3/9] Проверка Docker Compose...${NC}"
if docker compose version &> /dev/null; then
    COMPOSE_VERSION=$(docker compose version | cut -d' ' -f4)
    echo -e "${GREEN}✓ Docker Compose уже установлен (версия: $COMPOSE_VERSION)${NC}"
else
    echo "Установка Docker Compose standalone..."
    COMPOSE_LATEST=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | jq -r .tag_name)
    if [ -z "$COMPOSE_LATEST" ] || [ "$COMPOSE_LATEST" = "null" ]; then
        COMPOSE_LATEST="v2.24.0"  # Fallback версия
    fi
    curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_LATEST}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo -e "${GREEN}✓ Docker Compose установлен${NC}"
fi

# Настройка firewall (ufw)
echo ""
echo -e "${GREEN}[4/9] Настройка firewall...${NC}"

# Проверка, не заблокирован ли уже SSH
if ufw status | grep -q "Status: active"; then
    echo -e "${YELLOW}Firewall уже активен${NC}"
else
    # Разрешаем SSH (важно сделать первым!)
    ufw allow 22/tcp comment 'SSH' > /dev/null 2>&1 || true
    
    # Разрешаем HTTP и HTTPS
    ufw allow 80/tcp comment 'HTTP' > /dev/null 2>&1 || true
    ufw allow 443/tcp comment 'HTTPS' > /dev/null 2>&1 || true
    
    # Включаем firewall (интерактивно, чтобы не заблокировать текущую сессию)
    echo ""
    echo -e "${YELLOW}Внимание: Будет включен firewall (ufw).${NC}"
    echo -e "${YELLOW}Убедитесь, что SSH доступ настроен правильно!${NC}"
    read -p "Включить firewall? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ufw --force enable
        echo -e "${GREEN}✓ Firewall включен${NC}"
    else
        echo -e "${YELLOW}Firewall не включен. Включите вручную: sudo ufw enable${NC}"
    fi
fi

# Определение пользователя для приложения
echo ""
echo -e "${GREEN}[5/9] Настройка пользователя...${NC}"
if [ -n "$SUDO_USER" ]; then
    APP_USER=$SUDO_USER
else
    APP_USER=$(whoami)
fi

# Добавление пользователя в группу docker
if ! groups "$APP_USER" | grep -q docker; then
    usermod -aG docker "$APP_USER" 2>/dev/null || true
    echo -e "${GREEN}✓ Пользователь $APP_USER добавлен в группу docker${NC}"
    echo -e "${YELLOW}  ВАЖНО: Выйдите и войдите снова, чтобы изменения вступили в силу${NC}"
else
    echo -e "${GREEN}✓ Пользователь $APP_USER уже в группе docker${NC}"
fi

# Создание директорий для приложения
echo ""
echo -e "${GREEN}[6/9] Создание директорий...${NC}"
APP_DIR="/opt/clinnexus"
DATA_DIR="/var/lib/clinnexus"
BACKUP_DIR="/opt/clinnexus/backups"

mkdir -p "$APP_DIR"
mkdir -p "$DATA_DIR"/{db,uploads,logs}
mkdir -p "$BACKUP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$BACKUP_DIR" 2>/dev/null || true
chown -R "$APP_USER:$APP_USER" "$DATA_DIR" 2>/dev/null || true
chmod 755 "$APP_DIR"
chmod 755 "$DATA_DIR"
chmod 755 "$BACKUP_DIR"

echo -e "${GREEN}✓ Директории созданы:${NC}"
echo "  - $APP_DIR (код приложения)"
echo "  - $DATA_DIR (данные)"
echo "  - $BACKUP_DIR (бэкапы)"

# Настройка системных лимитов
echo ""
echo -e "${GREEN}[7/9] Настройка системных лимитов...${NC}"

# Проверка, не добавлены ли уже лимиты
if ! grep -q "# ClinNexus limits" /etc/security/limits.conf; then
    cat >> /etc/security/limits.conf << EOF

# ClinNexus limits
* soft nofile 65536
* hard nofile 65536
* soft nproc 32768
* hard nproc 32768
EOF
    echo -e "${GREEN}✓ Лимиты файловых дескрипторов настроены${NC}"
else
    echo -e "${YELLOW}Лимиты уже настроены${NC}"
fi

# Настройка sysctl для лучшей производительности
if ! grep -q "# ClinNexus optimizations" /etc/sysctl.conf; then
    cat >> /etc/sysctl.conf << EOF

# ClinNexus optimizations
vm.max_map_count=262144
fs.file-max=2097152
net.core.somaxconn=65535
EOF
    sysctl -p > /dev/null 2>&1
    echo -e "${GREEN}✓ Параметры ядра настроены${NC}"
else
    echo -e "${YELLOW}Параметры ядра уже настроены${NC}"
fi

# Установка Certbot для SSL (опционально)
echo ""
echo -e "${GREEN}[8/9] Установка Certbot для SSL...${NC}"
read -p "Установить Certbot для SSL сертификатов? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if ! command -v certbot &> /dev/null; then
        apt-get install -y -qq certbot python3-certbot-nginx
        echo -e "${GREEN}✓ Certbot установлен${NC}"
    else
        echo -e "${YELLOW}Certbot уже установлен${NC}"
    fi
else
    echo -e "${YELLOW}Certbot пропущен${NC}"
fi

# Установка дополнительных утилит (опционально)
echo ""
echo -e "${GREEN}[9/9] Установка дополнительных утилит...${NC}"
read -p "Установить дополнительные утилиты (htop, net-tools, vim, nano)? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    apt-get install -y -qq htop net-tools vim nano tree
    echo -e "${GREEN}✓ Дополнительные утилиты установлены${NC}"
else
    echo -e "${YELLOW}Дополнительные утилиты пропущены${NC}"
fi

# Финальная информация
echo ""
echo -e "${GREEN}=========================================="
echo "Подготовка сервера завершена!"
echo "==========================================${NC}"
echo ""
echo -e "${GREEN}Установлено:${NC}"
echo "  ✓ Docker $DOCKER_VERSION"
if [ -n "$COMPOSE_VERSION" ]; then
    echo "  ✓ Docker Compose $COMPOSE_VERSION"
else
    echo "  ✓ Docker Compose (standalone)"
fi
echo "  ✓ Firewall (ufw) настроен"
echo ""
echo -e "${GREEN}Следующие шаги:${NC}"
echo "  1. Выйдите и войдите снова (чтобы изменения группы docker вступили в силу)"
echo "  2. Скопируйте код приложения в $APP_DIR"
echo "  3. Создайте файл .env.prod на основе env.prod.example"
echo "  4. Запустите: cd $APP_DIR && docker compose -f docker-compose.prod.yml up -d"
echo ""
echo -e "${GREEN}Полезные команды:${NC}"
echo "  - Проверка статуса Docker: systemctl status docker"
echo "  - Просмотр логов: docker compose -f docker-compose.prod.yml logs -f"
echo "  - Остановка: docker compose -f docker-compose.prod.yml down"
echo "  - Статус контейнеров: docker compose -f docker-compose.prod.yml ps"
echo ""
echo -e "${YELLOW}Важно:${NC}"
echo "  - Убедитесь, что firewall настроен правильно"
echo "  - Настройте SSL сертификаты для HTTPS (Let's Encrypt)"
echo "  - Настройте автоматические бэкапы базы данных"
echo "  - Регулярно обновляйте систему: sudo apt update && sudo apt upgrade"
echo ""
