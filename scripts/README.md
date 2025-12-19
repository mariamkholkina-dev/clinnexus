# Скрипты для развертывания ClinNexus

## setup-server.sh

Скрипт автоматической подготовки Ubuntu сервера для развертывания ClinNexus.

### Использование

```bash
# Скачайте скрипт на сервер
wget https://raw.githubusercontent.com/your-repo/clinnexus/main/scripts/setup-server.sh

# Или скопируйте через scp
scp scripts/setup-server.sh user@server:/tmp/

# Запустите с правами root
sudo bash setup-server.sh
```

### Что делает скрипт

1. Обновляет систему
2. Устанавливает Docker и Docker Compose
3. Настраивает firewall (ufw)
4. Создает необходимые директории
5. Настраивает системные лимиты

### Требования

- Ubuntu 20.04 LTS или новее
- Права root (sudo)
- Интернет соединение

### После выполнения скрипта

1. Скопируйте код приложения на сервер
2. Создайте файл `.env.prod` на основе `env.prod.example`
3. Запустите приложение: `docker compose -f docker-compose.prod.yml up -d`

Подробные инструкции см. в [DEPLOYMENT.md](../DEPLOYMENT.md).

