#!/bin/bash
# Скрипт для добавления текущего пользователя в группу docker
# Использование: sudo bash fix-docker-group.sh

set -e

# Проверка, что скрипт запущен от root
if [ "$EUID" -ne 0 ]; then 
    echo "Ошибка: Скрипт должен быть запущен с правами root (sudo)"
    exit 1
fi

# Определение текущего пользователя
if [ -n "$SUDO_USER" ]; then
    USER_TO_ADD=$SUDO_USER
else
    USER_TO_ADD=$(whoami)
fi

echo "Добавление пользователя $USER_TO_ADD в группу docker..."

# Добавление пользователя в группу docker
usermod -aG docker "$USER_TO_ADD"

echo "✓ Пользователь $USER_TO_ADD добавлен в группу docker"
echo ""
echo "ВАЖНО: Выполните одно из следующих действий:"
echo "  1. Выйдите и войдите снова (exit, затем новый вход)"
echo "  2. Или выполните: newgrp docker"
echo "  3. Или откройте новую SSH сессию"
echo ""
echo "Проверка: выполните 'groups' в новой сессии - должна быть группа 'docker'"



