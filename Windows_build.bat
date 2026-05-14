@echo off
echo ==========================================
echo Начинаю сборку PPO Automation для Windows...
echo ==========================================

:: Запуск PyInstaller
pyinstaller --noconsole --onefile --name "PPO_Automation" app_store_ppo_automation.py

echo.
echo ==========================================
echo Сборка завершена! Убираю мусор...
echo ==========================================

:: Удаляем папку build
if exist build\ (
    rmdir /s /q build
)

:: Удаляем файл .spec
if exist PPO_Automation.spec (
    del /f /q PPO_Automation.spec
)

echo.
echo ГОТОВО! Ваш PPO_Automation.exe ждет вас в папке 'dist'.
pause