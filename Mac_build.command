#!/bin/bash

# Переходим в папку, откуда запущен скрипт
cd "$(dirname "$0")"

echo "=========================================="
echo "Начинаю сборку PPO Automation для Mac..."
echo "=========================================="

# --onedir вместо --onefile: на macOS .app с onefile часто не запускается
# (распаковка + проверка Gatekeeper при каждом старте).
pyinstaller --windowed --onedir --name "PPO_Automation" --clean -y app_store_ppo_automation.py

echo ""
echo "=========================================="
echo "Сборка завершена! Убираю мусор..."
echo "=========================================="

# Удаляем папку build и файл .spec
rm -rf build/
rm -f PPO_Automation.spec

# Убираем xattr/.DS_Store — иначе codesign падает и .app не открывается из Finder
find dist -name '.DS_Store' -delete 2>/dev/null || true
find dist -name '._*' -delete 2>/dev/null || true
xattr -cr dist/PPO_Automation.app dist/PPO_Automation 2>/dev/null || true

# com.apple.provenance часто ломает codesign — чистая копия без xattr
if [ -d dist/PPO_Automation.app ]; then
  rm -rf /tmp/PPO_Automation_clean.app
  ditto --norsrc --noextattr dist/PPO_Automation.app /tmp/PPO_Automation_clean.app
  rm -rf dist/PPO_Automation.app
  ditto /tmp/PPO_Automation_clean.app dist/PPO_Automation.app
  rm -rf /tmp/PPO_Automation_clean.app
fi
codesign --force --deep --sign - dist/PPO_Automation.app 2>/dev/null || true

echo ""
echo "ГОТОВО! Запускайте dist/PPO_Automation.app (или run_app.command)."
echo "Ярлык на рабочем столе: PPO_Automation.app / «PPO Automation.command»"