#!/bin/bash

set -e

# Переходим в папку, откуда запущен скрипт
cd "$(dirname "$0")"

require_closed_application() {
  if pgrep -x "PPO_Automation" >/dev/null; then
    echo "Закройте PPO_Automation перед пересборкой: обновление открытой программы повреждает загрузку её модулей."
    exit 1
  fi
}

require_closed_application

echo "=========================================="
echo "Начинаю сборку PPO Automation для Mac..."
echo "=========================================="

# --onedir вместо --onefile: на macOS .app с onefile часто не запускается
# (распаковка + проверка Gatekeeper при каждом старте).
if [ -x ".venv/bin/pyinstaller" ]; then
  PYINSTALLER=".venv/bin/pyinstaller"
else
  PYINSTALLER="pyinstaller"
fi
"$PYINSTALLER" --windowed --onedir --name "PPO_Automation" --clean -y --add-data "templates:templates" --add-data "docs/GITHUB_ACTIONS_IOS.md:docs" app_store_ppo_automation.py

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
echo "Устанавливаю программу и ярлык на рабочем столе..."
# Desktop/Documents may be synced by File Provider, which re-adds FinderInfo
# and invalidates codesign checks. Keep the signed bundle in ~/Applications.
APP_DIRECTORY="$HOME/Applications"
INSTALLED_APP="$APP_DIRECTORY/PPO_Automation.app"
DESKTOP_APP="$HOME/Desktop/PPO_Automation.app"
if [ -d dist/PPO_Automation.app ]; then
  mkdir -p "$APP_DIRECTORY"
  INSTALL_ROOT=$(mktemp -d "$APP_DIRECTORY/.PPO_Automation-install.XXXXXX")
  ditto --norsrc --noextattr dist/PPO_Automation.app "$INSTALL_ROOT/PPO_Automation.app"
  xattr -cr "$INSTALL_ROOT/PPO_Automation.app"
  codesign --force --deep --sign - "$INSTALL_ROOT/PPO_Automation.app"
  codesign --verify --deep --strict "$INSTALL_ROOT/PPO_Automation.app"
  require_closed_application
  if [ -e "$INSTALLED_APP" ] || [ -L "$INSTALLED_APP" ]; then
    mv "$INSTALLED_APP" "$INSTALL_ROOT/previous.app"
  fi
  if ! mv "$INSTALL_ROOT/PPO_Automation.app" "$INSTALLED_APP"; then
    if [ -e "$INSTALL_ROOT/previous.app" ] || [ -L "$INSTALL_ROOT/previous.app" ]; then
      mv "$INSTALL_ROOT/previous.app" "$INSTALLED_APP"
    fi
    exit 1
  fi
  if [ -e "$DESKTOP_APP" ] || [ -L "$DESKTOP_APP" ]; then
    mv "$DESKTOP_APP" "$INSTALL_ROOT/previous-desktop.app"
  fi
  if ! ln -s "$INSTALLED_APP" "$DESKTOP_APP"; then
    if [ -e "$INSTALL_ROOT/previous-desktop.app" ] || [ -L "$INSTALL_ROOT/previous-desktop.app" ]; then
      mv "$INSTALL_ROOT/previous-desktop.app" "$DESKTOP_APP"
    fi
    exit 1
  fi
  codesign --verify --deep --strict "$DESKTOP_APP"
  rm -rf "$INSTALL_ROOT"
  echo "Установлено: $INSTALLED_APP"
  echo "Ярлык: $DESKTOP_APP"
fi

echo ""
echo "ГОТОВО! Запускайте с рабочего стола: PPO_Automation.app"
echo "Или: $INSTALLED_APP"
