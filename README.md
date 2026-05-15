# 🍏 App Store PPO Automation Control (PRO Mode)

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://wiki.qt.io/Qt_for_Python)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**PPO Automation Control** — это мощное десктопное приложение для iOS-разработчиков и ASO-специалистов. Оно полностью автоматизирует рутинный процесс создания и обновления A/B тестов (Product Page Optimization) в App Store Connect для 50 локалей.

Больше не нужно вручную прокликивать десятки языков и поштучно загружать сотни скриншотов. Скрипт делает это в несколько потоков через официальный App Store Connect API.

---

## ✨ Ключевые возможности

*   **⚡ Многопоточная загрузка:** Загрузка скриншотов происходит параллельно (до 5 потоков), что ускоряет процесс в 4-5 раз.
*   **🛡️ Защита от лимитов (Rate Limit Protection):** Умный алгоритм экспоненциальной задержки автоматически обходит блокировки Apple (ошибки 429 Too Many Requests).
*   **🎯 Два режима работы:**
    *   `Создать новый тест:` Автоматическое создание структуры PPO (до 3 вариантов) и заливка скриншотов на все поддерживаемые локали.
    *   `Обновить существующий:` Точечная замена скриншотов. Вы можете выбрать конкретный вариант (Variant A/B/C) и конкретные языки для обновления, не пересоздавая тест.
*   **🧠 Умный маппинг вариантов:** Скрипт автоматически распознает названия вариантов в Apple (понимает как `Variant A`, так и `Treatment A`).
*   **💅 Современный GUI:** Интерфейс в темной теме на базе PySide6 с авто-загрузкой иконок флагов для удобной навигации по локалям.
*   **💾 Автосохранение сессий:** Ваши API-ключи надежно сохраняются локально в файл `.env`.
*   **🧾 Первичная загрузка метаданных через GUI:** Отдельная вкладка с обычными полями интерфейса загружает description, keywords, whatsNew/release notes, App Review notes, subtitle, privacy policy URL и category/appInfo-поля. JSON остался только как дополнительный режим для сложных `custom_requests`.

---

## 🧾 Первичная загрузка метаданных через GUI

Во вкладке **«ПЕРВИЧНАЯ ЗАГРУЗКА»** больше не нужно писать весь JSON вручную. Основные данные вводятся прямо в интерфейсе:

*   **Локаль:** выбор языка вручную или кнопка **«🌐 Взять primary locale из Apple»**, которая сама подтянет основную локаль приложения из App Store Connect.
*   **Version metadata:** description, keywords, promotional text, support URL, marketing URL и What's New / release notes.
*   **App info:** name, subtitle, privacy policy URL, privacy choices URL, primary/secondary category через выпадающий список и подсказка age/kids age band по умолчанию `4+`.
*   **App Review notes:** контактные данные, demo account и notes для команды ревью Apple.
*   **AI генерация (Gemini):** можно вставить Gemini API key, developer name, свое ТЗ/brief и отдельный промт генерации. Доступны prompt profiles, отдельные кнопки для генерации description, keywords, subtitle, category, App Review notes, переписывания description и сокращения keywords, а также Fix with AI / quick fixes; app name не меняется AI.

Кнопка **«🧩 Заполнить пример»** заполняет эти поля тестовыми данными, чтобы было понятно, что куда вводить. Кнопка **«📄 Импорт JSON в поля»** оставлена для совместимости: можно загрузить старый JSON, и программа разложит его значения по GUI-полям. Кнопка **«🔍 Preview & Validate»** показывает payload перед отправкой и выполняет строгие ASO-проверки: лимиты, banned words, dash symbols, дубли keywords, generic keywords, слова из app name/subtitle и URL-подсказки. Кнопка **«⬇️ Pull from Apple»** подтягивает текущие метаданные выбранной локали из App Store Connect в форму.

Gemini API key и модель сохраняются локально в `.env` рядом с App Store Connect настройками (developer name в `.env` больше не записывается). При выборе файла `AuthKey_XXXX.p8` программа автоматически подставляет `KEY_ID` из имени файла; `Issuer ID` нужно ввести вручную, потому что его нет внутри `.p8`. App name вводится вручную и не перезаписывается AI. Перед отправкой в Apple обязательно проверьте AI-тексты и category ID вручную.

Блок **custom_requests** остался только как продвинутый дополнительный режим. Он нужен для специфичных сущностей App Store Connect, например app availability или возрастных деклараций, где payload зависит от конкретной анкеты Apple. Отдельная вкладка **«APP PRIVACY»** формирует privacy draft с дефолтами: Device ID для Analytics, not tracking, not linked; Crash Data для Analytics, tracking, not linked. Перед реальной отправкой privacy endpoint нужно проверить и заменить на точный App Store Connect endpoint.

---

## 🛠 Установка для разработчиков (Из исходников)

1. Склонируйте репозиторий:
   ```bash
   git clone [https://github.com/ВАШ_НИК/ppo-automation-control.git](https://github.com/ВАШ_НИК/ppo-automation-control.git)
   cd ppo-automation-control
   
   Установите зависимости. Для удобства создайте виртуальное окружение:

Bash
pip install -r requirements.txt

   *(Содержимое requirements.txt: `PySide6`, `requests`, `PyJWT`, `python-dotenv`)*

3. Запустите скрипт:
   ```bash
   python app_store_ppo_automation.py
   
📦 Сборка готовой программы (.exe / .app)
Вам не обязательно запускать проект через консоль. Вы можете скомпилировать его в готовое приложение в один клик с помощью утилиты PyInstaller.

Установите упаковщик:

Bash
pip install pyinstaller

2. **Для Windows:** Запустите файл `build.bat` двойным кликом.
3. **Для macOS:** Откройте терминал, выдайте права файлу (`chmod +x build.command`) и запустите его.

После сборки в папке `dist/` появится готовый исполняемый файл, который можно переносить куда угодно.

---

## 🔑 Подготовка App Store API Keys

Для работы программы ей необходим доступ к вашему аккаунту Apple Developer.
1. Зайдите в [App Store Connect](https://appstoreconnect.apple.com/) -> **Users and Access** -> **Keys**.
2. Создайте новый ключ с правами **App Manager** или **Admin**.
3. Скопируйте **Issuer ID** и **Key ID**.
4. Скачайте файл приватного ключа (`.p8`).
5. Введите эти данные и ваш **App ID** (Apple ID приложения, состоящий из цифр) в настройках программы.

---

## ⚠️ Важное предупреждение (Приватность и Метаданные)

Скрипт отправляет файлы скриншотов на серверы Apple **байт-в-байт в их исходном виде**. Программа **не очищает** EXIF-метаданные ваших картинок.
Если вы заботитесь о приватности (например, работаете через антидетект-браузеры), обязательно прогоняйте ваши папки со скриншотами через программы для очистки метаданных (ImageOptim, ExifPurge) *перед* загрузкой их через этот софт.

---
