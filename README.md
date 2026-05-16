# 🍏 App Store PPO Automation Control (PRO Mode)

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![GUI: PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://wiki.qt.io/Qt_for_Python)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**PPO Automation Control** — desktop-приложение для автоматизации работы с **Product Page Optimization (PPO)** и метаданными в **App Store Connect** через официальный API Apple.

Проект ориентирован на iOS-разработчиков и ASO-команды, которым нужно быстро и массово:
- создавать/обновлять PPO-варианты;
- загружать скриншоты в несколько локалей;
- управлять первичной загрузкой метаданных из GUI;
- выполнять AI-assisted генерацию текстов для ASO-полей.

---

## Что нового в текущей версии

Ниже — ключевые функциональные изменения, уже присутствующие в кодовой базе:

- Обновленный и масштабируемый GUI (адаптация размеров под экран, улучшенные размеры шрифтов, layout для 1080p–2K).  
- Расширенная вкладка **«Первичная загрузка»** с формами вместо обязательного ручного JSON.  
- Поддержка **Pull from Apple**: подтягивание существующей локализации и метаданных из App Store Connect в форму.  
- **Preview & Validate** перед отправкой: проверка лимитов полей и базовых ASO-ограничений.  
- Нормализация и валидация App Review контактов (включая форматирование телефона).  
- Санитизация URL-полей (`supportUrl`, `marketingUrl`, `privacyPolicyUrl`, `privacyChoicesUrl`) с автодобавлением `https://`.  
- Автоматическое ограничение длины для ключевых metadata-полей по лимитам Apple.  
- Улучшенная обработка категорий: конвертация iTunes genre ID в `appCategories` ID, где применимо.  
- Поддержка AI-генерации ASO-текстов через Gemini (с локальным хранением API key/модели в `.env`).

---

## Основные возможности

- **PPO automation**
  - Создание нового PPO-теста (до 3 вариантов).
  - Обновление существующего теста (варианты/локали выборочно).

- **Параллельная загрузка**
  - Многопоточная загрузка скриншотов (используется `concurrent.futures`).

- **Устойчивость к rate limit**
  - Логика повторов и backoff для сценариев API-ограничений.

- **GUI для metadata**
  - Управление description, keywords, subtitle, promotional text, release notes, app review notes, URL и category-полями.

- **Локальная конфигурация**
  - Работа с `.env` рядом со скриптом (`AuthKey`, `Issuer ID`, ключи интеграций и т.д.).

---

## Структура проекта

- `app_store_ppo_automation.py` — основное GUI-приложение и бизнес-логика API/валидаций.
- `requirements.txt` — зависимости Python.
- `Windows_build.bat` — сборка `.exe` через PyInstaller.
- `Mac_build.command` — сборка `.app` через PyInstaller.

---

## Установка и запуск

### 1) Клонирование

```bash
git clone <your-repo-url>
cd Apple-Automatic
```

### 2) (Рекомендуется) виртуальное окружение

```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows
```

### 3) Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4) Запуск

```bash
python app_store_ppo_automation.py
```

---

## Сборка standalone-приложения

Перед сборкой установите PyInstaller:

```bash
pip install pyinstaller
```

### Windows

```bat
Windows_build.bat
```

### macOS

```bash
chmod +x Mac_build.command
./Mac_build.command
```

После сборки артефакты появляются в папке `dist/`.

---

## Подготовка App Store Connect API ключа

1. Откройте **App Store Connect → Users and Access → Keys**.
2. Создайте ключ с правами **App Manager** или **Admin**.
3. Скачайте `.p8` файл.
4. Подготовьте:
   - `Issuer ID`
   - `Key ID`
   - `App ID` (числовой Apple ID приложения)
5. Заполните данные в GUI приложения.

---

## Важные замечания

- Приложение взаимодействует с реальным App Store Connect API; проверяйте данные перед массовыми апдейтами.
- Скриншоты отправляются как есть; очистку EXIF/метаданных нужно выполнять отдельно при необходимости.
- AI-сгенерированные тексты требуют ручной финальной проверки перед публикацией.

---

## Зависимости

- PySide6
- requests
- PyJWT
- python-dotenv
