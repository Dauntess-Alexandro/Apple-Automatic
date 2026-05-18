@echo off
cd /d "%~dp0"
echo Starting PPO Automation...
python app_store_ppo_automation.py
if errorlevel 1 (
    echo.
    echo App exited with an error. Check the messages above.
    pause
)
