#!/bin/bash

# Переходим в папку, откуда запущен скрипт
cd "$(dirname "$0")"

echo "=========================================="
echo "Начинаю сборку PPO Automation для Mac..."
echo "=========================================="

# Запуск PyInstaller (Обратите внимание на .icns вместо .ico)
pyinstaller --windowed --onefile --name "PPO_Automation" app_store_ppo_automation.py

echo ""
echo "=========================================="
echo "Сборка завершена! Убираю мусор..."
echo "=========================================="

# Удаляем папку build и файл .spec
rm -rf build/
rm -f PPO_Automation.spec

echo ""
echo "ГОТОВО! Ваша программа PPO_Automation.app ждет вас в папке 'dist'."