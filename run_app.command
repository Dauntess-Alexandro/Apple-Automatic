#!/bin/bash
cd "$(dirname "$0")"

APP="dist/PPO_Automation.app"
BIN="dist/PPO_Automation/PPO_Automation"

if [ -d "$APP" ]; then
    # После пересборки open() поднимает старое окно — сначала завершаем процесс
    pkill -f "PPO_Automation.app/Contents/MacOS/PPO_Automation" 2>/dev/null || true
    sleep 0.5
    xattr -cr "$APP" 2>/dev/null || true
    codesign --force --deep --sign - "$APP" 2>/dev/null || true
    open "$APP"
elif [ -x "$BIN" ]; then
    exec "$BIN"
else
    echo "Сначала соберите приложение: ./Mac_build.command"
    read -r -p "Нажмите Enter..."
    exit 1
fi
