import os
import sys
import time
import tempfile
import base64
import io
import csv
import jwt
import requests
import threading
import concurrent.futures
import json
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, quote
from PIL import Image, ImageOps
from dotenv import load_dotenv
import yaml
from image_optimize import LocalImageOptimizer
from xcode_ipa_build import (
    build_and_optionally_upload,
    discover_scheme,
    find_xcode_project_dir_by_app_name,
    find_xcodeproj,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLineEdit, QPushButton, QTextEdit, QFileDialog, QLabel, QGroupBox,
    QProgressBar, QTabWidget, QCheckBox, QScrollArea, QComboBox, QToolButton,
    QFrame, QSplitter, QSizePolicy, QStackedWidget, QSpinBox,
    QDialog, QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView,
)
from PySide6.QtGui import QIcon, QFont, QGuiApplication, QColor, QPixmap, QImage, QDesktopServices
from PySide6.QtCore import QThread, Signal, Qt, QSize, QUrl, QTimer

def _resolve_config_dir():
    """Папка для .env: рядом со скриптом или с .app/.exe при сборке PyInstaller."""
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        exe_dir = os.path.dirname(exe)
        if sys.platform == "darwin" and os.path.basename(exe_dir) == "MacOS":
            return os.path.dirname(os.path.dirname(os.path.dirname(exe_dir)))
        if sys.platform == "darwin" and os.path.basename(exe_dir) == "PPO_Automation":
            return os.path.dirname(exe_dir)
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


SCRIPT_DIR = _resolve_config_dir()
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
FLAGS_CACHE_DIR = os.path.join(SCRIPT_DIR, ".flags_cache")
load_dotenv(ENV_PATH)

STOREPAL_BASE_URL = (os.getenv("STOREPAL_API_URL") or "https://storepal.app").rstrip("/")
STOREPAL_CREDENTIALS_PATH = os.path.join(os.path.expanduser("~"), ".storepal", "credentials.json")
STOREPAL_DASHBOARD_NEW_APP_URL = f"{STOREPAL_BASE_URL}/dashboard/apps/new"
STOREPAL_DOCS_CLI_URL = f"{STOREPAL_BASE_URL}/docs/cli"
FIGMA_API_BASE = "https://api.figma.com"
ACCOUNTS_ROSTER_PATH = os.path.join(SCRIPT_DIR, ".accounts_roster.json")
FORMSPREE_FORM_BASE = "https://formspree.io/f"
WEB3FORMS_SUBMIT_URL = "https://api.web3forms.com/submit"
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"
BREWPAGE_HTML_API = "https://brewpage.app/api/html"
ZERODEPLOY_DROP_URL = "https://api.zerodeploy.dev/drop"
LITTERBOX_UPLOAD_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
GITLAB_APPLE_CONNECT_PATH = ".metadata/apple-connect.yaml"
GITLAB_DEFAULT_REF = "main"
DEFAULT_XCODE_PROJECTS_ROOT = os.path.expanduser("~/Downloads/Projects")
DEFAULT_XCODE_PROJECT_PATH = ""  # подставляется по имени после «Загрузить App ID»

AITUNNEL_API_BASE = os.getenv("AITUNNEL_API_BASE", "https://api.aitunnel.ru/v1").rstrip("/")
AITUNNEL_MODELS_URL = "https://api.aitunnel.ru/public/aitunnel/models/chat"
AITUNNEL_IMAGE_MODELS_URL = "https://api.aitunnel.ru/public/aitunnel/models/images"
ZAI_API_BASE = os.getenv("ZAI_API_BASE", "https://api.z.ai/api/paas/v4").rstrip("/")
AI_PROVIDER_AITUNNEL = "aitunnel"
AI_PROVIDER_ZAI = "zai"
DEFAULT_AI_MODEL = os.getenv("AITUNNEL_MODEL") or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DEFAULT_AITUNNEL_IMAGE_MODEL = os.getenv("AITUNNEL_IMAGE_MODEL", "gemini-2.5-flash-image")
DEFAULT_ZAI_MODEL = os.getenv("ZAI_MODEL", "glm-5")
ICON_IMAGE_SIZE = "1024x1024"
ICON_IMAGE_PIXELS = 1024
ICONS_OUTPUT_DIR = os.path.expanduser("~/Downloads/Icons")
ZAI_MODEL_MAX_TOTAL_COST = float(os.getenv("ZAI_MODEL_MAX_TOTAL_COST", "6"))
# Цены Z.AI: USD за 1M токенов (input + output), docs.z.ai/guides/overview/pricing
ZAI_MODEL_CATALOG = [
    {"model_id": "glm-4.7-flash", "input": 0.0, "output": 0.0},
    {"model_id": "glm-4.5-flash", "input": 0.0, "output": 0.0},
    {"model_id": "glm-4-32b-0414-128k", "input": 0.1, "output": 0.1},
    {"model_id": "glm-4.7-flashx", "input": 0.07, "output": 0.4},
    {"model_id": "glm-4.5-air", "input": 0.2, "output": 1.1},
    {"model_id": "glm-4.7", "input": 0.6, "output": 2.2},
    {"model_id": "glm-4.6", "input": 0.6, "output": 2.2},
    {"model_id": "glm-4.5", "input": 0.6, "output": 2.2},
    {"model_id": "glm-5", "input": 1.0, "output": 3.2},
    {"model_id": "glm-5-turbo", "input": 1.2, "output": 4.0},
    {"model_id": "glm-4.5-airx", "input": 1.1, "output": 4.5},
    {"model_id": "glm-5.1", "input": 1.4, "output": 4.4},
    {"model_id": "glm-5.2", "input": 1.4, "output": 4.4},
    {"model_id": "glm-4.5-x", "input": 2.2, "output": 8.9},
]
ZAI_MODEL_PRIORITY = [
    "glm-4.7-flash",
    "glm-4.5-flash",
    "glm-4-32b-0414-128k",
    "glm-4.7-flashx",
    "glm-4.5-air",
    "glm-4.7",
    "glm-5",
    "glm-5.2",
]
DEFAULT_ZAI_MODELS = [
    "glm-4.7-flash",
    "glm-4.5-flash",
    "glm-4.5-air",
    "glm-4.7",
    "glm-5",
    "glm-5.2",
]
AITUNNEL_MODEL_MAX_TOTAL_COST = float(os.getenv("AITUNNEL_MODEL_MAX_TOTAL_COST", "1200"))
DEFAULT_AITUNNEL_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gpt-4o-mini",
    "deepseek-chat",
    "claude-haiku-4.5",
]
AITUNNEL_MODEL_PRIORITY = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gpt-4o-mini",
    "deepseek-chat",
    "deepseek-v3.2",
    "claude-haiku-4.5",
    "mistral-small-2603",
    "qwen3.5-flash-02-23",
]
DEFAULT_AITUNNEL_IMAGE_MODELS = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-lite-image",
    "gpt-image-1-mini",
    "flux.2-klein-4b",
    "seedream-4.5",
]
AITUNNEL_IMAGE_MODEL_PRIORITY = [
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-lite-image",
    "gpt-image-1-mini",
    "flux.2-klein-4b",
    "seedream-4.5",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "flux.2-pro",
    "gpt-image-1",
    "flux.2-flex",
    "flux.2-max",
    "gpt-image-2",
]


def _aitunnel_model_cost_score(info):
    return float(info.get("prompt_cost") or 0) + float(info.get("completion_cost") or 0)


def _format_aitunnel_model_label(model_id, info):
    provider = str(info.get("provider") or "").strip()
    cost = _aitunnel_model_cost_score(info)
    provider_part = f"{provider} · " if provider else ""
    return f"{model_id} ({provider_part}~{cost:.0f})"


def _aitunnel_image_model_cost_score(info):
    min_price = float(info.get("min_price_per_image") or 0)
    max_price = float(info.get("max_price_per_image") or min_price)
    return (min_price + max_price) / 2.0 if max_price else min_price


def _format_aitunnel_image_model_label(model_id, info):
    provider = str(info.get("provider") or "").strip()
    min_price = float(info.get("min_price_per_image") or 0)
    max_price = float(info.get("max_price_per_image") or min_price)
    provider_part = f"{provider} · " if provider else ""
    if min_price and max_price and abs(max_price - min_price) > 0.01:
        cost_part = f"{min_price:.1f}–{max_price:.1f}₽"
    elif min_price:
        cost_part = f"~{min_price:.1f}₽"
    else:
        cost_part = "price n/a"
    return f"{model_id} ({provider_part}{cost_part})"


def fetch_aitunnel_model_catalog(timeout=15, max_total_cost=None):
    max_total_cost = AITUNNEL_MODEL_MAX_TOTAL_COST if max_total_cost is None else max_total_cost
    try:
        response = requests.get(AITUNNEL_MODELS_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            rows = []
            for model_id, info in data.items():
                if not isinstance(info, dict):
                    continue
                cost = _aitunnel_model_cost_score(info)
                if cost <= max_total_cost:
                    rows.append((cost, model_id, info))
            priority = {model_id: index for index, model_id in enumerate(AITUNNEL_MODEL_PRIORITY)}
            rows.sort(key=lambda item: (priority.get(item[1], len(priority) + 1), item[0], item[1]))
            return [
                (model_id, _format_aitunnel_model_label(model_id, info), cost)
                for cost, model_id, info in rows
            ]
    except Exception:
        pass
    return [(model_id, model_id, 0.0) for model_id in DEFAULT_AITUNNEL_MODELS]


def fetch_aitunnel_chat_models(timeout=15, max_total_cost=None):
    return [model_id for model_id, _, _ in fetch_aitunnel_model_catalog(timeout, max_total_cost)]


def fetch_aitunnel_image_model_catalog(timeout=15):
    try:
        response = requests.get(AITUNNEL_IMAGE_MODELS_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            rows = []
            for model_id, info in data.items():
                if not isinstance(info, dict):
                    continue
                if info.get("supports_generation") is False:
                    continue
                cost = _aitunnel_image_model_cost_score(info)
                rows.append((cost, model_id, info))
            priority = {model_id: index for index, model_id in enumerate(AITUNNEL_IMAGE_MODEL_PRIORITY)}
            rows.sort(key=lambda item: (priority.get(item[1], len(priority) + 1), item[0], item[1]))
            return [
                (model_id, _format_aitunnel_image_model_label(model_id, info), cost)
                for cost, model_id, info in rows
            ]
    except Exception:
        pass
    return [(model_id, model_id, 0.0) for model_id in DEFAULT_AITUNNEL_IMAGE_MODELS]


def _zai_model_cost_score(info):
    return float(info.get("input") or 0) + float(info.get("output") or 0)


def _format_zai_model_label(model_id, info):
    cost = _zai_model_cost_score(info)
    if cost <= 0:
        return f"{model_id} (free)"
    return f"{model_id} (~${cost:.2f}/1M)"


def fetch_zai_model_catalog(max_total_cost=None):
    max_total_cost = ZAI_MODEL_MAX_TOTAL_COST if max_total_cost is None else max_total_cost
    rows = []
    for info in ZAI_MODEL_CATALOG:
        model_id = info.get("model_id")
        if not model_id:
            continue
        cost = _zai_model_cost_score(info)
        if cost <= max_total_cost:
            rows.append((cost, model_id, info))
    priority = {model_id: index for index, model_id in enumerate(ZAI_MODEL_PRIORITY)}
    rows.sort(key=lambda item: (item[0], priority.get(item[1], len(priority) + 1), item[1]))
    return [
        (model_id, _format_zai_model_label(model_id, info), cost)
        for cost, model_id, info in rows
    ]


def fetch_zai_chat_models(max_total_cost=None):
    return [model_id for model_id, _, _ in fetch_zai_model_catalog(max_total_cost)]


def parse_openai_compatible_error(response, provider_name="AI"):
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    error = payload.get("error") or {}
    message = error.get("message") if isinstance(error, dict) else str(error)
    if not message:
        choices = payload.get("choices") or []
        if choices:
            choice_error = choices[0].get("error") or {}
            message = choice_error.get("message", "")
    message = message or response.text[:500]
    return message.splitlines()[0] if message else f"{provider_name}: HTTP {response.status_code}"


def parse_aitunnel_error(response):
    return parse_openai_compatible_error(response, "AITUNNEL")


def parse_zai_error(response):
    return parse_openai_compatible_error(response, "Z.AI")


def _extract_chat_completion_text(data, provider_name="AI"):
    choices = data.get("choices") or []
    if not choices:
        raise Exception(f"{provider_name} вернул пустой список choices.")
    choice = choices[0]
    choice_error = choice.get("error") or {}
    if choice_error:
        raise Exception(choice_error.get("message", str(choice_error)))
    message = choice.get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        text = (message.get("reasoning_content") or "").strip()
    if not text:
        finish_reason = choice.get("finish_reason", "UNKNOWN")
        raise Exception(f"{provider_name} вернул пустой ответ (finish_reason={finish_reason}).")
    return text


def aitunnel_chat_completion(api_key, model, messages, temperature=0.7, json_mode=False, timeout=90, max_retries=4):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = None
    for attempt in range(max_retries):
        response = requests.post(
            f"{AITUNNEL_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code not in (429, 500, 502, 503, 504):
            if response.status_code >= 400:
                raise Exception(parse_aitunnel_error(response))
            break
        if attempt == max_retries - 1:
            raise Exception(parse_aitunnel_error(response))
        time.sleep(2 ** attempt)

    return _extract_chat_completion_text(response.json(), "AITUNNEL")


def _ensure_icon_png_bytes(image_bytes, size=ICON_IMAGE_PIXELS):
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    if image.size != (size, size):
        image = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _icon_filename_from_app_name(app_name):
    name = str(app_name or "").strip()
    if not name:
        return "AppIcon.png"
    # Keep spaces and letters; strip path-unsafe characters only.
    safe = re.sub(r'[\\/:*?"<>|]+', "", name)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    safe = safe[:80] if safe else "AppIcon"
    return f"{safe}.png"


def _unique_icon_output_path(directory, filename):
    os.makedirs(directory, exist_ok=True)
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    index = 2
    while True:
        candidate = os.path.join(directory, f"{base} ({index}){ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def aitunnel_image_generation(api_key, model, prompt, size=ICON_IMAGE_SIZE, output_format="png", timeout=180, max_retries=3):
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "output_format": output_format,
    }
    response = None
    for attempt in range(max_retries):
        response = requests.post(
            f"{AITUNNEL_API_BASE}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code not in (429, 500, 502, 503, 504):
            if response.status_code >= 400:
                raise Exception(parse_aitunnel_error(response))
            break
        if attempt == max_retries - 1:
            raise Exception(parse_aitunnel_error(response))
        time.sleep(2 ** attempt)

    data = response.json()
    items = data.get("data") or []
    if not items:
        raise Exception("AITUNNEL вернул пустой список изображений.")
    b64_payload = items[0].get("b64_json")
    if not b64_payload:
        raise Exception("AITUNNEL не вернул b64_json для изображения.")
    raw_bytes = base64.b64decode(b64_payload)
    png_bytes = _ensure_icon_png_bytes(raw_bytes, ICON_IMAGE_PIXELS)
    usage = data.get("usage") or {}
    return {
        "png_bytes": png_bytes,
        "cost_rub": usage.get("cost_rub"),
        "balance": usage.get("balance"),
        "model": data.get("model") or model,
        "media_type": items[0].get("media_type") or "image/png",
    }


def zai_chat_completion(api_key, model, messages, temperature=0.7, json_mode=False, timeout=90, max_retries=4):
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = None
    for attempt in range(max_retries):
        response = requests.post(
            f"{ZAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept-Language": "en-US,en",
            },
            json=payload,
            timeout=timeout,
        )
        if response.status_code not in (429, 500, 502, 503, 504):
            if response.status_code >= 400:
                raise Exception(parse_zai_error(response))
            break
        if attempt == max_retries - 1:
            raise Exception(parse_zai_error(response))
        time.sleep(2 ** attempt)

    return _extract_chat_completion_text(response.json(), "Z.AI")


def ai_chat_completion(provider, api_key, model, messages, temperature=0.7, json_mode=False, timeout=90, max_retries=4):
    if provider == AI_PROVIDER_ZAI:
        return zai_chat_completion(
            api_key, model, messages,
            temperature=temperature,
            json_mode=json_mode,
            timeout=timeout,
            max_retries=max_retries,
        )
    return aitunnel_chat_completion(
        api_key, model, messages,
        temperature=temperature,
        json_mode=json_mode,
        timeout=timeout,
        max_retries=max_retries,
    )

# UI scale tuned for 1080p–2K displays
UI_FONT_BASE_PT = 14
UI_FONT_TITLE_PT = 20
UI_FONT_SECTION_PT = 12
UI_LOCALE_GRID_COLUMNS = 5
UI_FORM_MAX_WIDTH = 980
UI_LOG_MIN_HEIGHT = 320
UI_LOCALE_COLUMN_MIN_WIDTH = 170
UI_FIELD_MIN_HEIGHT = 34 if sys.platform == "darwin" else 30
UI_API_SCROLL_MAX_RATIO = 0.45


def platform_ui_font(size_pt=UI_FONT_BASE_PT):
    if sys.platform == "darwin":
        font = QFont()
        font.setPointSize(size_pt)
        return font
    return QFont("Segoe UI", size_pt)


def make_section_label(text):
    label = QLabel(text)
    label.setProperty("role", "section")
    label.setWordWrap(True)
    return label


def _prepare_api_field(widget):
    widget.setMinimumHeight(UI_FIELD_MIN_HEIGHT)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return widget


def _add_labeled_fields_row(layout, fields, trailing_widget=None, trailing_stretch=0):
    row = QHBoxLayout()
    row.setSpacing(12)
    for label_text, widget in fields:
        col = QVBoxLayout()
        col.setSpacing(5)
        col.addWidget(make_section_label(label_text))
        col.addWidget(_prepare_api_field(widget))
        row.addLayout(col, 1)
    if trailing_widget is not None:
        btn_col = QVBoxLayout()
        btn_col.setSpacing(5)
        btn_col.addStretch(1)
        btn_col.addWidget(trailing_widget)
        row.addLayout(btn_col, trailing_stretch)
    layout.addLayout(row)


def default_window_size():
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1560, 980
    avail = screen.availableGeometry()
    width = min(max(int(avail.width() * 0.92), 1180), 2100)
    height = min(max(int(avail.height() * 0.90), 780), 1320)
    return width, height


def make_page_header(title, subtitle=None):
    container = QWidget()
    container.setObjectName("page_header")
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(4)
    title_label = QLabel(title)
    title_label.setProperty("role", "title")
    layout.addWidget(title_label)
    if subtitle:
        hint = QLabel(subtitle)
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
    return container

def _read_env_file():
    values = {}
    if not os.path.isfile(ENV_PATH):
        return values
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def resolve_storepal_token():
    env_file = _read_env_file()
    token = (
        env_file.get("STOREPAL_TOKEN", "").strip()
        or os.getenv("STOREPAL_TOKEN", "").strip()
    )
    if token:
        return token
    try:
        with open(STOREPAL_CREDENTIALS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("token") or "").strip()
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return ""


def resolve_storepal_base_url():
    env_file = _read_env_file()
    base = (
        env_file.get("STOREPAL_API_URL", "").strip()
        or os.getenv("STOREPAL_API_URL", "").strip()
        or STOREPAL_BASE_URL
    )
    try:
        with open(STOREPAL_CREDENTIALS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        file_base = (data.get("baseUrl") or "").strip()
        if file_base:
            base = file_base
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        pass
    return base.rstrip("/")


def resolve_gitlab_base_url():
    env_file = _read_env_file()
    base = (
        env_file.get("GITLAB_URL", "").strip()
        or os.getenv("GITLAB_URL", "").strip()
        or "https://gitlab.com"
    )
    return base.rstrip("/")


def resolve_gitlab_token():
    env_file = _read_env_file()
    return (
        env_file.get("GITLAB_TOKEN", "").strip()
        or os.getenv("GITLAB_TOKEN", "").strip()
    )


def resolve_xcode_projects_root():
    env_file = _read_env_file()
    return (
        env_file.get("XCODE_PROJECTS_ROOT", "").strip()
        or os.getenv("XCODE_PROJECTS_ROOT", "").strip()
        or DEFAULT_XCODE_PROJECTS_ROOT
    )


def resolve_xcode_project_path():
    env_file = _read_env_file()
    return (
        env_file.get("XCODE_PROJECT_PATH", "").strip()
        or os.getenv("XCODE_PROJECT_PATH", "").strip()
        or DEFAULT_XCODE_PROJECT_PATH
    )


def resolve_xcode_scheme():
    env_file = _read_env_file()
    return (
        env_file.get("XCODE_SCHEME", "").strip()
        or os.getenv("XCODE_SCHEME", "").strip()
    )


def resolve_xcode_team_id():
    env_file = _read_env_file()
    return (
        env_file.get("XCODE_TEAM_ID", "").strip()
        or os.getenv("XCODE_TEAM_ID", "").strip()
        or os.getenv("DEVELOPMENT_TEAM", "").strip()
    )


def normalize_app_name_for_gitlab(app_name):
    """River Aspect → RiverAspect (для матча path/name репо)."""
    return re.sub(r"[^a-zA-Z0-9]+", "", (app_name or "").strip())


def extract_apple_connect_brief(yaml_text):
    """Достаёт description.value из .metadata/apple-connect.yaml (в т.ч. вложенный)."""
    raw = (yaml_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    def _collect(node, found):
        if isinstance(node, dict):
            desc = node.get("description")
            if isinstance(desc, dict):
                value = desc.get("value")
                if value is not None:
                    text = str(value).strip()
                    if text:
                        found.append(text)
            elif isinstance(desc, str) and desc.strip():
                found.append(desc.strip())
            for child in node.values():
                _collect(child, found)
        elif isinstance(node, list):
            for item in node:
                _collect(item, found)

    try:
        data = yaml.safe_load(raw)
    except Exception:
        data = None
    found = []
    if data is not None:
        _collect(data, found)
    if found:
        # Берём самый длинный non-empty description.value
        return max(found, key=len)

    # Fallback regex: description → value: | / > block (с любым отступом)
    match = re.search(
        r"(?ms)^[ \t]*description:\s*\n"
        r"(?:^[ \t]+(?!value:).+\n)*?"
        r"^[ \t]+value:\s*[|>][+-]?\s*\n"
        r"((?:^[ \t]+.+\n?)*)",
        raw,
    )
    if match:
        block = match.group(1)
        lines = []
        for line in block.splitlines():
            # Останавливаемся, если встретили ключ на уровне value (2 spaces typically)
            if re.match(r"^[ \t]{0,3}[a-zA-Z_][a-zA-Z0-9_]*:\s*", line) and not line.startswith("    "):
                break
            if line.startswith("    "):
                lines.append(line[4:])
            elif line.startswith("\t"):
                lines.append(line.lstrip("\t"))
            else:
                lines.append(line.strip())
        text = "\n".join(lines).strip()
        if text:
            return text

    # Inline value: "..." or value: text
    match = re.search(
        r"(?ms)^[ \t]*description:\s*\n"
        r"(?:^[ \t]+(?!value:).+\n)*?"
        r"^[ \t]+value:\s*[\"']?(.*?)[\"']?\s*$",
        raw,
    )
    if match:
        text = (match.group(1) or "").strip().strip("\"'")
        if text and text not in ("|", ">"):
            return text
    return ""


def gitlab_search_projects(search, token=None, base_url=None, per_page=50):
    token = (token or resolve_gitlab_token()).strip()
    base = (base_url or resolve_gitlab_base_url()).rstrip("/")
    if not token:
        raise RuntimeError("Нет GITLAB_TOKEN.")
    if not search:
        raise RuntimeError("Пустой поисковый запрос GitLab.")
    response = requests.get(
        f"{base}/api/v4/projects",
        headers={"PRIVATE-TOKEN": token, "Accept": "application/json"},
        params={
            "search": search,
            "membership": "true",
            "per_page": per_page,
            "order_by": "last_activity_at",
            "sort": "desc",
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"GitLab search failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    return data if isinstance(data, list) else []


def gitlab_pick_best_project(projects, app_name):
    compact = normalize_app_name_for_gitlab(app_name).lower()
    if not projects:
        return None
    if not compact:
        return projects[0]

    best = None
    best_score = -1
    for project in projects:
        name = project.get("name") or ""
        path = project.get("path") or ""
        full = project.get("path_with_namespace") or ""
        c_name = normalize_app_name_for_gitlab(name).lower()
        c_path = normalize_app_name_for_gitlab(path).lower()
        score = 0
        if c_name == compact or c_path == compact:
            score += 100
        if c_name.startswith(compact) or c_path.startswith(compact):
            score += 70
        if compact in c_name or compact in c_path:
            score += 40
        if compact in normalize_app_name_for_gitlab(full).lower():
            score += 20
        if score > best_score:
            best_score = score
            best = project
    return best if best_score > 0 else projects[0]


def gitlab_fetch_file_raw(
    project_id,
    file_path,
    token=None,
    base_url=None,
    ref=GITLAB_DEFAULT_REF,
    try_fallbacks=True,
):
    token = (token or resolve_gitlab_token()).strip()
    base = (base_url or resolve_gitlab_base_url()).rstrip("/")
    if not token:
        raise RuntimeError("Нет GITLAB_TOKEN.")
    encoded = quote(file_path, safe="")
    branches = []
    for branch in ((ref,) if not try_fallbacks else (ref, "main", "master", "HEAD")):
        if branch and branch not in branches:
            branches.append(branch)
    last_error = None
    for branch in branches:
        response = requests.get(
            f"{base}/api/v4/projects/{project_id}/repository/files/{encoded}/raw",
            headers={"PRIVATE-TOKEN": token},
            params={"ref": branch},
            timeout=45,
        )
        if response.status_code == 200:
            return response.text, branch
        last_error = f"{response.status_code}: {response.text[:200]}"
        if response.status_code == 404:
            continue
        raise RuntimeError(f"GitLab file fetch failed ({last_error})")
    raise RuntimeError(f"Файл {file_path} не найден в проекте {project_id}. {last_error or ''}")


def fetch_gitlab_app_brief(app_name, token=None, base_url=None):
    """Ищет репо по имени приложения и достаёт description.value из apple-connect.yaml."""
    app_name = (app_name or "").strip()
    if not app_name:
        raise RuntimeError("Нет названия приложения для поиска в GitLab.")
    token = (token or resolve_gitlab_token()).strip()
    base = (base_url or resolve_gitlab_base_url()).rstrip("/")
    compact = normalize_app_name_for_gitlab(app_name)
    queries = []
    for q in (compact, app_name, app_name.replace(" ", "")):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    projects = []
    seen_ids = set()
    for query in queries:
        for item in gitlab_search_projects(query, token=token, base_url=base):
            pid = item.get("id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            projects.append(item)
        if projects:
            break
    if not projects:
        raise RuntimeError(f"GitLab: проект для «{app_name}» не найден (искал: {', '.join(queries)}).")

    project = gitlab_pick_best_project(projects, app_name)
    project_id = project.get("id")
    project_path = project.get("path_with_namespace") or project.get("path") or str(project_id)
    default_branch = (project.get("default_branch") or "").strip() or GITLAB_DEFAULT_REF

    refs = []
    for ref in (default_branch, "main", "master", "HEAD"):
        if ref and ref not in refs:
            refs.append(ref)

    last_yaml = ""
    last_ref = default_branch
    for ref in refs:
        try:
            yaml_text, used_ref = gitlab_fetch_file_raw(
                project_id,
                GITLAB_APPLE_CONNECT_PATH,
                token=token,
                base_url=base,
                ref=ref,
                try_fallbacks=False,
            )
        except Exception:
            continue
        last_yaml = yaml_text or ""
        last_ref = used_ref
        if "git-lfs.github.com" in last_yaml or last_yaml.startswith("version https://git-lfs"):
            raise RuntimeError(
                f"GitLab: {project_path}/{GITLAB_APPLE_CONNECT_PATH} — это Git LFS pointer, нужен обычный YAML."
            )
        brief = extract_apple_connect_brief(yaml_text)
        if brief:
            return {
                "brief": brief,
                "project_id": project_id,
                "project_path": project_path,
                "app_name": app_name,
                "ref": used_ref,
            }

    snippet = ""
    if last_yaml:
        idx = last_yaml.lower().find("description:")
        chunk = last_yaml[idx:idx + 240] if idx >= 0 else last_yaml[:240]
        snippet = " Фрагмент файла: " + re.sub(r"\s+", " ", chunk).strip()
    raise RuntimeError(
        f"GitLab: в {project_path}/{GITLAB_APPLE_CONNECT_PATH} "
        f"(ref={last_ref}) не найден непустой description.value.{snippet}"
    )


def save_storepal_credentials(token, base_url=None):
    base_url = (base_url or resolve_storepal_base_url()).rstrip("/")
    cred_dir = os.path.dirname(STOREPAL_CREDENTIALS_PATH)
    os.makedirs(cred_dir, exist_ok=True)
    payload = {"token": token, "email": "", "baseUrl": base_url}
    with open(STOREPAL_CREDENTIALS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    try:
        os.chmod(STOREPAL_CREDENTIALS_PATH, 0o600)
    except OSError:
        pass


def storepal_slugify(value):
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:48] or "my-app"


def build_storepal_privacy_markdown(
    app_name,
    contact_email,
    developer_name="",
    collects_data=False,
    uses_analytics=False,
    uses_ads=False,
    uses_purchases=False,
    uses_crash=False,
):
    app_name = (app_name or "the App").strip() or "the App"
    contact_email = (contact_email or "support@example.com").strip()
    developer_name = (developer_name or app_name).strip() or app_name
    today = time.strftime("%Y-%m-%d")

    data_lines = []
    if collects_data:
        data_lines.append("- We may collect information you provide directly (for example account or contact details).")
    if uses_analytics:
        data_lines.append("- We may collect usage and analytics data to understand how the App is used.")
    if uses_crash:
        data_lines.append("- We may collect diagnostics and crash logs to improve stability.")
    if uses_ads:
        data_lines.append("- Advertising partners may collect device and interaction data to show ads.")
    if uses_purchases:
        data_lines.append("- Purchase and subscription status may be processed by Apple / payment providers.")
    if not data_lines:
        data_lines.append("- The App does not collect personal data beyond what Apple or the device provides by default.")

    third_parties = []
    if uses_analytics:
        third_parties.append("analytics providers")
    if uses_crash:
        third_parties.append("crash reporting providers")
    if uses_ads:
        third_parties.append("advertising networks")
    if uses_purchases:
        third_parties.append("payment / subscription processors")
    third_party_text = (
        f"We may share limited data with {', '.join(third_parties)} solely to operate the App."
        if third_parties
        else "We do not sell personal information. Limited processing may occur through Apple platform services."
    )

    return f"""# Privacy Policy for {app_name}

**Effective date:** {today}

This Privacy Policy describes how {developer_name} ("we", "us") handles information in connection with the mobile application **{app_name}** (the "App").

## Contact

If you have questions about this policy or your data, contact us at: **{contact_email}**

You can also use the App support page linked from the App Store listing.

## Information We Collect

{chr(10).join(data_lines)}

## How We Use Information

We use information to:
- provide and maintain the App;
- respond to support requests;
- improve reliability and user experience;
- comply with legal obligations.

## Sharing

{third_party_text}

## Data Retention

We retain information only as long as needed for the purposes above, unless a longer period is required by law.

## Your Choices

Depending on your region, you may have rights to access, correct, delete, or restrict processing of your personal data. Contact us at {contact_email} to make a request.

On iOS you can also review App Privacy disclosures on the App Store product page and manage permissions in system Settings.

## Children

The App is not directed to children under 13 (or the equivalent minimum age in your jurisdiction). We do not knowingly collect personal information from children.

## Changes

We may update this Privacy Policy from time to time. The effective date above will be revised when changes are published.

## App Store

This policy is intended to satisfy the publicly accessible Privacy Policy URL requirement for App Store Connect.
"""


class StorePalApiError(Exception):
    def __init__(self, message, code="", status=0):
        super().__init__(message)
        self.code = code
        self.status = status


def storepal_api_request(method, path, token=None, payload=None, params=None, timeout=45):
    token = (token or resolve_storepal_token()).strip()
    if not token:
        raise StorePalApiError(
            "Нет StorePal token. Войдите через кнопку Login или вставьте STOREPAL_TOKEN.",
            code="NOT_LOGGED_IN",
            status=401,
        )
    base = resolve_storepal_base_url()
    url = f"{base}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    response = requests.request(
        method.upper(),
        url,
        headers=headers,
        json=payload,
        params=params,
        timeout=timeout,
    )
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code >= 400:
        err = (data or {}).get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            message = err.get("message") or f"StorePal API error ({response.status_code})"
            code = err.get("code") or "API_ERROR"
        else:
            message = f"StorePal API error ({response.status_code})"
            code = "API_ERROR"
        raise StorePalApiError(message, code=code, status=response.status_code)
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


def storepal_list_apps(token=None):
    data = storepal_api_request("GET", "/api/v1/apps", token=token) or {}
    return data.get("apps") or []


def storepal_get_app(slug, token=None):
    return storepal_api_request("GET", "/api/v1/app", token=token, params={"slug": slug}) or {}


def storepal_create_app(name, slug, description="", token=None):
    payload = {
        "name": name,
        "slug": slug,
    }
    if description:
        payload["description"] = description
    return storepal_api_request("POST", "/api/v1/apps", token=token, payload=payload) or {}


def storepal_set_privacy(slug, content, token=None):
    return storepal_api_request(
        "PUT",
        "/api/v1/privacy",
        token=token,
        params={"slug": slug},
        payload={"content": content},
    ) or {}


def storepal_extract_urls(app_payload):
    if not isinstance(app_payload, dict):
        return {}
    urls = app_payload.get("urls")
    if isinstance(urls, dict) and urls:
        return urls
    slug = (app_payload.get("slug") or "").strip()
    if not slug:
        return {}
    base = resolve_storepal_base_url()
    return {
        "privacy": f"{base}/{slug}/privacy",
        "support": f"{base}/{slug}/support",
        "terms": f"{base}/{slug}/terms",
        "faq": f"{base}/{slug}/faq",
        "releases": f"{base}/{slug}/releases",
    }


def resolve_formspree_form_id():
    env_file = _read_env_file()
    raw = (
        env_file.get("FORMSPREE_FORM_ID", "").strip()
        or os.getenv("FORMSPREE_FORM_ID", "").strip()
    )
    if not raw:
        return ""
    # Accept full endpoint URL or bare hashid.
    match = re.search(r"formspree\.io/f/([A-Za-z0-9]+)", raw)
    if match:
        return match.group(1)
    return raw.strip("/").split("/")[-1]


def resolve_formspree_api_key():
    env_file = _read_env_file()
    return (
        env_file.get("FORMSPREE_API_KEY", "").strip()
        or os.getenv("FORMSPREE_API_KEY", "").strip()
    )


def resolve_web3forms_access_key():
    env_file = _read_env_file()
    return (
        env_file.get("WEB3FORMS_ACCESS_KEY", "").strip()
        or os.getenv("WEB3FORMS_ACCESS_KEY", "").strip()
    )


def _normalize_app_id_digits(value):
    return re.sub(r"\D+", "", str(value or ""))


def _normalize_app_name_key(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _split_person_name(full_name):
    parts = [p for p in re.split(r"\s+", str(full_name or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _looks_like_email(value):
    text = str(value or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text))


def _looks_like_app_id(value):
    digits = _normalize_app_id_digits(value)
    return digits.isdigit() and 8 <= len(digits) <= 12


def _looks_like_phone(value):
    text = str(value or "").strip()
    digits = _normalize_app_id_digits(text)
    # В вашей таблице телефоны часто без +, просто цифры
    return bool(digits) and digits.isdigit() and 8 <= len(digits) <= 15


def _looks_like_telegram(value):
    return str(value or "").strip().startswith("@")


def _roster_phone_with_plus(value):
    """Гарантирует + в начале телефона (Apple Review format)."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = normalize_review_phone(raw)
    if normalized:
        return normalized
    digits = _normalize_app_id_digits(raw)
    if not digits:
        return raw
    return f"+{digits}" if not raw.startswith("+") else raw


def _parse_roster_table_text(text):
    """Парсит CSV/TSV/таблицу из Google Sheets (вставка). Возвращает list[dict]."""
    raw = (text or "").strip()
    if not raw:
        return []
    # Normalize Google Sheets clipboard quirks
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    sample = raw[:4096]
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    reader = csv.reader(io.StringIO(raw), dialect)
    rows = [list(cell.strip() for cell in row) for row in reader if any(str(c).strip() for c in row)]
    if not rows:
        return []

    header = [c.lower() for c in rows[0]]
    header_map = {}
    aliases = {
        "app_id": ("app id", "app_id", "apple id", "apple_id", "appid"),
        "app_name": ("app", "app name", "app_name", "name app", "project", "прила", "приложение", "название"),
        "person_name": ("person", "full name", "fullname", "contact name", "имя", "fio", "разраб", "developer"),
        "email": (
            "email", "e-mail", "mail", "review email", "почта",
            "логин", "login", "username", "user name", "apple login",
        ),
        "phone": ("phone", "tel", "telephone", "телефон"),
        "country": ("country", "страна"),
        "telegram": ("telegram", "tg", "handle"),
    }
    for idx, cell in enumerate(header):
        for key, names in aliases.items():
            if cell in names or any(name in cell for name in names):
                header_map.setdefault(key, idx)

    has_header = bool(header_map) and (
        "email" in header_map or "app_id" in header_map or "app_name" in header_map
    )
    data_rows = rows[1:] if has_header else rows
    accounts = []
    for row in data_rows:
        cells = list(row) + [""] * 8
        record = {
            "app_id": "",
            "app_name": "",
            "person_name": "",
            "email": "",
            "phone": "",
            "country": "",
            "telegram": "",
        }
        if has_header:
            for key, idx in header_map.items():
                if idx < len(row):
                    record[key] = str(row[idx]).strip()
        else:
            # Без App ID: app | empty | @tg | phone | person | country | email | ...
            for cell in row:
                value = str(cell or "").strip()
                if not value:
                    continue
                if _looks_like_email(value) and not record["email"]:
                    record["email"] = value
                elif _looks_like_telegram(value) and not record["telegram"]:
                    record["telegram"] = value
                elif _looks_like_phone(value) and not record["phone"]:
                    record["phone"] = value
                elif value.lower() in {
                    "сша", "usa", "uk", "великобритания", "канада", "canada",
                    "нидерланды", "испания", "germany", "германия", "франция",
                } and not record["country"]:
                    record["country"] = value
                elif not record["app_name"] and not _looks_like_email(value):
                    record["app_name"] = value
                elif not record["person_name"] and " " in value and not _looks_like_email(value):
                    record["person_name"] = value
            if record["app_name"] and not record["person_name"]:
                for cell in row:
                    value = str(cell or "").strip()
                    if (
                        value
                        and value != record["app_name"]
                        and " " in value
                        and not _looks_like_email(value)
                        and not _looks_like_telegram(value)
                        and not _looks_like_phone(value)
                    ):
                        record["person_name"] = value
                        break

        if record.get("phone"):
            record["phone"] = _roster_phone_with_plus(record["phone"])
        app_id = _normalize_app_id_digits(record.get("app_id"))
        if app_id:
            record["app_id"] = app_id
        if not record.get("email") and not record.get("app_name"):
            continue
        accounts.append(record)
    return accounts


def load_accounts_roster():
    try:
        with open(ACCOUNTS_ROSTER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            return data["accounts"]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return []


def save_accounts_roster(accounts):
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "accounts": list(accounts or []),
    }
    with open(ACCOUNTS_ROSTER_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return ACCOUNTS_ROSTER_PATH


def find_account_by_app_id(app_id, accounts=None):
    needle = _normalize_app_id_digits(app_id)
    if not needle:
        return None
    for item in accounts if accounts is not None else load_accounts_roster():
        if _normalize_app_id_digits(item.get("app_id")) == needle:
            return item
    return None


def find_account_by_app_name(app_name, accounts=None):
    needle = _normalize_app_name_key(app_name)
    if not needle:
        return None
    items = accounts if accounts is not None else load_accounts_roster()
    for item in items:
        if _normalize_app_name_key(item.get("app_name")) == needle:
            return item
    for item in items:
        key = _normalize_app_name_key(item.get("app_name"))
        if key and (key in needle or needle in key):
            return item
    return None


def find_account_for_app(app_id="", app_name="", accounts=None):
    """Сначала по названию приложения, потом по App ID (если есть)."""
    items = accounts if accounts is not None else load_accounts_roster()
    by_name = find_account_by_app_name(app_name, items)
    if by_name:
        return by_name
    return find_account_by_app_id(app_id, items)


def formspree_fetch_submissions(form_id, api_key, limit=50, offset=0):
    """Тянет submissions из Formspree Forms API (Professional/Business)."""
    form_id = normalize_formspree_form_id(form_id)
    api_key = (api_key or "").strip()
    if not form_id:
        raise ValueError("Пустой Formspree form id.")
    if not api_key:
        raise ValueError(
            "Нет FORMSPREE_API_KEY. В Formspree: форма → Settings → HTTP API "
            "(нужен Professional/Business)."
        )
    url = f"https://formspree.io/api/0/forms/{form_id}/submissions"
    response = requests.get(
        url,
        params={"limit": int(limit), "offset": int(offset), "order": "desc"},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        timeout=45,
    )
    if response.status_code == 401:
        # fallback basic auth as in Formspree docs: curl -u :API_KEY
        response = requests.get(
            url,
            params={"limit": int(limit), "offset": int(offset), "order": "desc"},
            auth=("", api_key),
            headers={"Accept": "application/json"},
            timeout=45,
        )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = ""
        if isinstance(data, dict):
            message = data.get("error") or data.get("message") or ""
        raise RuntimeError(
            message
            or f"Formspree submissions API {response.status_code}: {response.text[:240]}"
        )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("submissions") or data.get("data") or []
    return []


def create_local_privacy_hosted_url(
    app_name,
    contact_email="",
    developer_name="",
    collects_data=False,
    uses_analytics=False,
    uses_crash=False,
    uses_ads=False,
    uses_purchases=False,
    brewpage_ns=None,
    theme="default",
    host_backend="brewpage",
):
    """Локальный Privacy (markdown→HTML) + публичный URL. Без Pro/API-ключа."""
    markdown = build_storepal_privacy_markdown(
        app_name=app_name,
        contact_email=contact_email,
        developer_name=developer_name,
        collects_data=collects_data,
        uses_analytics=uses_analytics,
        uses_ads=uses_ads,
        uses_purchases=uses_purchases,
        uses_crash=uses_crash,
    )
    title = f"Privacy Policy — {(app_name or 'App').strip() or 'App'}"
    html = markdown_to_simple_html(title, markdown, theme=theme)
    slug = storepal_slugify(app_name)
    return host_public_html(
        html,
        filename=f"{slug}-privacy.html",
        brewpage_ns=brewpage_ns,
        host_backend=host_backend,
    )


def markdown_to_simple_html(title, markdown_text, theme="default"):
    """Минимальный markdown→HTML для публичной privacy/support страницы."""
    if theme == "alt":
        style = (
            "body{font-family:Georgia,'Times New Roman',serif;"
            "max-width:680px;margin:40px auto;padding:0 20px;line-height:1.65;color:#0f172a;background:#fafafa}"
            "h1{font-size:1.75rem;font-weight:700}h2{font-size:1.15rem;margin-top:1.7em;color:#134e4a}"
            "a{color:#0f766e}ul{padding-left:1.25em}"
        )
    else:
        style = (
            "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;"
            "max-width:720px;margin:32px auto;padding:0 16px;line-height:1.55;color:#111}"
            "h1{font-size:1.6rem}h2{font-size:1.2rem;margin-top:1.6em}"
            "a{color:#2563eb}ul{padding-left:1.2em}"
        )
    lines = (markdown_text or "").splitlines()
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head>',
        '<meta charset="utf-8"/>',
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>',
        f"<title>{_html_escape(title)}</title>",
        "<style>",
        style,
        "</style></head><body>",
    ]
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h1>{_html_escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h2>{_html_escape(line[3:].strip())}</h2>")
        elif line.startswith("- "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_html_escape(line[2:].strip())}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{_inline_md(_html_escape(line))}</p>")
    if in_list:
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _html_escape(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _inline_md(escaped_text):
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped_text)


def normalize_formspree_form_id(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    match = re.search(r"formspree\.io/f/([A-Za-z0-9]+)", raw)
    if match:
        return match.group(1)
    return raw.strip("/").split("/")[-1]


def build_formspree_support_html(app_name, contact_email, form_id, privacy_url=""):
    app_name = (app_name or "App").strip() or "App"
    contact_email = (contact_email or "").strip()
    form_id = normalize_formspree_form_id(form_id)
    if not form_id:
        raise ValueError("Пустой Formspree form id.")
    endpoint = f"{FORMSPREE_FORM_BASE}/{form_id}"
    privacy_link = (
        f'<p><a href="{_html_escape(privacy_url)}">Privacy Policy</a></p>'
        if privacy_url else ""
    )
    email_line = (
        f"<p>Email: <a href=\"mailto:{_html_escape(contact_email)}\">{_html_escape(contact_email)}</a></p>"
        if contact_email else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Support — {_html_escape(app_name)}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#111;line-height:1.5}}
h1{{font-size:1.6rem;margin-bottom:8px}}
label{{display:block;margin:12px 0 4px;font-weight:600}}
input,textarea{{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d1d5db;border-radius:8px;font:inherit}}
button{{margin-top:16px;padding:12px 18px;border:0;border-radius:8px;background:#2563eb;color:#fff;font-weight:600;cursor:pointer}}
.hint{{color:#6b7280;font-size:.95rem}}
a{{color:#2563eb}}
</style>
</head>
<body>
<h1>{_html_escape(app_name)} Support</h1>
<p class="hint">Need help? Send a message below. We usually reply within 1–2 business days.</p>
{email_line}
<form action="{_html_escape(endpoint)}" method="POST">
  <input type="hidden" name="_subject" value="{_html_escape(app_name)} support request"/>
  <label for="email">Your email</label>
  <input id="email" type="email" name="email" required placeholder="you@example.com"/>
  <label for="message">Message</label>
  <textarea id="message" name="message" rows="6" required placeholder="Describe your issue..."></textarea>
  <button type="submit">Send feedback</button>
</form>
{privacy_link}
</body>
</html>
"""


def normalize_web3forms_access_key(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    # Accept pasted UUID-looking keys; strip accidental URL wrappers.
    match = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        raw,
    )
    if match:
        return match.group(1)
    return raw


def build_web3forms_support_html(app_name, contact_email, access_key, privacy_url=""):
    """Support page с формой Web3Forms (другой endpoint/стиль, чем Formspree)."""
    app_name = (app_name or "App").strip() or "App"
    contact_email = (contact_email or "").strip()
    access_key = normalize_web3forms_access_key(access_key)
    if not access_key:
        raise ValueError("Пустой Web3Forms access key.")
    privacy_link = (
        f'<p class="footer"><a href="{_html_escape(privacy_url)}">Privacy Policy</a></p>'
        if privacy_url else ""
    )
    email_line = (
        f'<p class="meta">Contact: <a href="mailto:{_html_escape(contact_email)}">{_html_escape(contact_email)}</a></p>'
        if contact_email else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Help — {_html_escape(app_name)}</title>
<style>
body{{font-family:Georgia,'Times New Roman',serif;max-width:600px;margin:48px auto;padding:0 20px;color:#0f172a;line-height:1.6;background:#f8fafc}}
h1{{font-size:1.7rem;margin-bottom:6px;font-weight:700}}
.meta,.hint{{color:#475569;font-size:.95rem}}
label{{display:block;margin:14px 0 6px;font-weight:600;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:.9rem}}
input,textarea{{width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid #cbd5e1;border-radius:6px;font:inherit;background:#fff}}
button{{margin-top:18px;padding:12px 20px;border:0;border-radius:6px;background:#0f766e;color:#fff;font-weight:700;cursor:pointer;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}}
.footer{{margin-top:28px}}
a{{color:#0f766e}}
</style>
</head>
<body>
<h1>Help Center</h1>
<p class="hint">{_html_escape(app_name)} — send us a short message and we will get back to you.</p>
{email_line}
<form action="{_html_escape(WEB3FORMS_SUBMIT_URL)}" method="POST">
  <input type="hidden" name="access_key" value="{_html_escape(access_key)}"/>
  <input type="hidden" name="subject" value="{_html_escape(app_name)} support request"/>
  <input type="hidden" name="from_name" value="{_html_escape(app_name)} Support"/>
  <input type="checkbox" name="botcheck" style="display:none" tabindex="-1" autocomplete="off"/>
  <label for="name">Name</label>
  <input id="name" type="text" name="name" required placeholder="Your name"/>
  <label for="email">Email</label>
  <input id="email" type="email" name="email" required placeholder="you@example.com"/>
  <label for="message">How can we help?</label>
  <textarea id="message" name="message" rows="6" required placeholder="Describe the issue..."></textarea>
  <button type="submit">Send message</button>
</form>
{privacy_link}
</body>
</html>
"""


def _normalize_brewpage_ns(value=None):
    """BrewPage namespace: public / custom / random (чтобы URL не были однотипными)."""
    raw = (value or "").strip().lower()
    if not raw or raw == "public":
        return "public"
    if raw in ("random", "auto", "unique"):
        return "p" + secrets.token_hex(4)
    cleaned = re.sub(r"[^a-z0-9-]", "", raw)
    if len(cleaned) < 3:
        return "p" + secrets.token_hex(4)
    return cleaned[:32]


def host_html_via_brewpage(html_content, filename="page.html", ttl_days=30, brewpage_ns=None):
    """Публикует HTML на brewpage.app — отдаёт как text/html (в отличие от catbox)."""
    ttl_days = max(1, min(int(ttl_days or 30), 30))
    ns = _normalize_brewpage_ns(brewpage_ns)
    response = requests.post(
        f"{BREWPAGE_HTML_API}?ns={ns}&ttl={ttl_days}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Pages/1.0",
        },
        json={
            "content": html_content or "",
            "filename": filename or "page.html",
            "showTopBar": False,
        },
        timeout=60,
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = (
            (data.get("message") if isinstance(data, dict) else None)
            or (data.get("error") if isinstance(data, dict) else None)
            or response.text[:240]
            or f"HTTP {response.status_code}"
        )
        raise RuntimeError(f"BrewPage upload failed: {message}")
    url = ""
    if isinstance(data, dict):
        url = (data.get("link") or data.get("url") or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"BrewPage: ответ без URL: {data}")
    return url


def host_html_via_zerodeploy(html_content, filename="index.html"):
    """Публикует HTML на ZeroDeploy Drop → уникальный *.zerodeploy.app (text/html). ~72ч.

    Всегда кладём как index.html, иначе корень сайта отдаёт 404
    (файл доступен только по /имя.html).
    """
    response = requests.post(
        ZERODEPLOY_DROP_URL,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "Pages/1.0",
            "X-Filename": "index.html",
        },
        data=(html_content or "").encode("utf-8"),
        timeout=60,
    )
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = (
            (data.get("message") if isinstance(data, dict) else None)
            or response.text[:240]
            or f"HTTP {response.status_code}"
        )
        raise RuntimeError(f"ZeroDeploy upload failed: {message}")
    url = ""
    if isinstance(data, dict):
        url = (
            ((data.get("data") or {}).get("url") if isinstance(data.get("data"), dict) else None)
            or data.get("url")
            or ""
        )
        url = (url or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"ZeroDeploy: ответ без URL: {data}")
    return url.rstrip("/") + "/"


def host_html_via_litterbox(html_content, filename="page.html", time="72h"):
    """Временный HTML на litterbox (catbox sibling). Обычно text/html, до 72ч."""
    response = requests.post(
        LITTERBOX_UPLOAD_URL,
        data={"reqtype": "fileupload", "time": time or "72h"},
        files={"fileToUpload": (filename, (html_content or "").encode("utf-8"), "text/html; charset=utf-8")},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Litterbox upload failed ({response.status_code}): {response.text[:200]}")
    url = (response.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Litterbox вернул неожиданный ответ: {url[:200]}")
    return url


def host_html_via_catbox(html_content, filename="page.html"):
    """Заливает HTML на catbox.moe и возвращает публичный URL (fallback)."""
    response = requests.post(
        CATBOX_UPLOAD_URL,
        data={"reqtype": "fileupload"},
        files={"fileToUpload": (filename, (html_content or "").encode("utf-8"), "text/html; charset=utf-8")},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Catbox upload failed ({response.status_code}): {response.text[:200]}")
    url = (response.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"Catbox вернул неожиданный ответ: {url[:200]}")
    return url


def host_public_html(html_content, filename="page.html", brewpage_ns=None, host_backend="brewpage"):
    """Хостит HTML как нормальную веб-страницу (~30 дней на BrewPage).

    brewpage — Formspree: BrewPage → catbox.
    alt / web3forms — тот же долгий BrewPage, но random ns (другие URL), → catbox.
    """
    errors = []
    backend = (host_backend or "brewpage").strip().lower()
    ns = brewpage_ns
    if backend in ("alt", "zerodeploy", "web3forms"):
        ns = brewpage_ns or "random"

    try:
        return host_html_via_brewpage(
            html_content,
            filename=filename,
            ttl_days=30,
            brewpage_ns=ns,
        )
    except Exception as e:
        errors.append(f"BrewPage: {e}")
    try:
        return host_html_via_catbox(html_content, filename=filename)
    except Exception as e:
        errors.append(f"Catbox: {e}")
    raise RuntimeError("Не удалось залить HTML. " + " | ".join(errors))


def check_public_url_alive(url, timeout=12):
    """Проверяет, что публичный URL отвечает (не 404/таймаут). Возвращает (ok, reason)."""
    raw = (url or "").strip()
    if not raw:
        return False, "пусто"
    if not raw.startswith(("http://", "https://")):
        return False, "не http(s)"
    headers = {"User-Agent": "PPO-Automation/1.0"}
    try:
        response = requests.head(raw, timeout=timeout, allow_redirects=True, headers=headers)
        if response.status_code in (403, 405, 501):
            response = requests.get(
                raw,
                timeout=timeout,
                allow_redirects=True,
                headers=headers,
                stream=True,
            )
            try:
                next(response.iter_content(512), b"")
            finally:
                response.close()
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}"
        return True, f"HTTP {response.status_code}"
    except requests.Timeout:
        return False, "таймаут"
    except requests.RequestException as exc:
        return False, str(exc)[:120]


def resolve_ai_provider():
    env_file = _read_env_file()
    provider = (
        env_file.get("AI_PROVIDER", "").strip().lower()
        or os.getenv("AI_PROVIDER", "").strip().lower()
    )
    if provider in (AI_PROVIDER_ZAI, "z.ai", "glm"):
        return AI_PROVIDER_ZAI
    return AI_PROVIDER_AITUNNEL


def resolve_aitunnel_api_key():
    env_file = _read_env_file()
    return (
        env_file.get("AITUNNEL_API_KEY", "").strip()
        or os.getenv("AITUNNEL_API_KEY", "").strip()
        or env_file.get("GEMINI_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )


def resolve_zai_api_key():
    env_file = _read_env_file()
    return (
        env_file.get("ZAI_API_KEY", "").strip()
        or os.getenv("ZAI_API_KEY", "").strip()
    )


def resolve_ai_api_key(provider=None):
    provider = provider or resolve_ai_provider()
    if provider == AI_PROVIDER_ZAI:
        return resolve_zai_api_key()
    return resolve_aitunnel_api_key()


def resolve_ai_model(provider=None):
    provider = provider or resolve_ai_provider()
    env_file = _read_env_file()
    if provider == AI_PROVIDER_ZAI:
        return (
            env_file.get("ZAI_MODEL", "").strip()
            or os.getenv("ZAI_MODEL", "").strip()
            or DEFAULT_ZAI_MODEL
        )
    return (
        env_file.get("AITUNNEL_MODEL", "").strip()
        or os.getenv("AITUNNEL_MODEL", "").strip()
        or env_file.get("GEMINI_MODEL", "").strip()
        or os.getenv("GEMINI_MODEL", "").strip()
        or DEFAULT_AI_MODEL
    )


def resolve_aitunnel_image_model():
    env_file = _read_env_file()
    return (
        env_file.get("AITUNNEL_IMAGE_MODEL", "").strip()
        or os.getenv("AITUNNEL_IMAGE_MODEL", "").strip()
        or DEFAULT_AITUNNEL_IMAGE_MODEL
    )


def ai_provider_label(provider):
    return "Z.AI (GLM)" if provider == AI_PROVIDER_ZAI else "AITUNNEL"


REQUIRED_APP_REVIEW_FIELDS = ("contactEmail", "contactPhone")

# Лимиты Apple (символы)
METADATA_FIELD_LIMITS = {
    "description": 4000,
    "keywords": 100,
    "promotionalText": 170,
    "subtitle": 30,
    "name": 30,
    "notes": 4000,
}


KEYWORD_PART_SPLIT_RE = re.compile(r"[,،;؛\n]+")
KEYWORD_INLINE_WORD_SPLIT_RE = re.compile(r"[\s\u00a0\u200b\u200c\u200d\ufeff]+")


def _split_keyword_parts(text):
    """Keywords: латиница/кириллица через запятую; арабский часто через пробел или ،"""
    text = str(text or "").strip()
    if not text:
        return []
    if KEYWORD_PART_SPLIT_RE.search(text):
        return [part.strip() for part in KEYWORD_PART_SPLIT_RE.split(text) if part.strip()]
    return [word for word in KEYWORD_INLINE_WORD_SPLIT_RE.split(text) if word]


def _join_keyword_parts(parts):
    return ",".join(parts)


def truncate_keywords_to_limit(text, limit=100):
    """Убирает целые keywords с конца (запятая/пробел), не режет посередине слова."""
    text = str(text or "").strip()
    if not text or len(text) <= limit:
        return text, False
    parts = _split_keyword_parts(text)
    if not parts:
        return text, False

    original = text
    while len(parts) > 1 and len(_join_keyword_parts(parts)) > limit:
        parts.pop()

    result = _join_keyword_parts(parts)
    if len(result) <= limit:
        return result, result != original

    while parts and len(_join_keyword_parts(parts)) > limit:
        last = parts[-1]
        subwords = [word for word in KEYWORD_INLINE_WORD_SPLIT_RE.split(last) if word]
        if len(subwords) > 1:
            subwords.pop()
            parts[-1] = " ".join(subwords)
        else:
            parts.pop()

    result = _join_keyword_parts(parts)
    return result, result != original


def truncate_text_by_words(text, limit):
    """Убирает целые слова с конца (пробелы), не режет посередине слова."""
    text = str(text or "").strip()
    if not text or len(text) <= limit:
        return text, False
    words = text.split()
    if not words:
        return text, False
    while len(words) > 1:
        candidate = " ".join(words)
        if len(candidate) <= limit:
            return candidate, candidate != text
        words.pop()
    return words[0] if len(words[0]) <= limit else words[0], True


def truncate_metadata_field(field_key, text, limits_map=None):
    limits_map = limits_map or METADATA_FIELD_LIMITS
    limit = limits_map.get(field_key)
    if not limit or not isinstance(text, str):
        return text, False
    text = text.strip()
    if len(text) <= limit:
        return text, False
    if field_key == "keywords":
        return truncate_keywords_to_limit(text, limit)
    return truncate_text_by_words(text, limit)

# Поля version localization, которые Apple может отклонить по статусу версии
VERSION_LOCALIZATION_STATE_OPTIONAL = ("whatsNew", "promotionalText")
APP_STORE_VERSION_STATE_OPTIONAL = ("copyright",)
APP_INFO_LOCALIZATION_UNKNOWN = ("privacyPolicyText",)  # tvOS, не iOS

AGE_RATING_DECLARATION_FREQUENCY_FIELDS = {
    "alcoholTobaccoOrDrugUseOrReferences",
    "contests",
    "gamblingSimulated",
    "gunsOrOtherWeapons",
    "medicalOrTreatmentInformation",
    "profanityOrCrudeHumor",
    "sexualContentGraphicAndNudity",
    "sexualContentOrNudity",
    "horrorOrFearThemes",
    "matureOrSuggestiveThemes",
    "violenceCartoonOrFantasy",
    "violenceRealisticProlongedGraphicOrSadistic",
    "violenceRealistic",
}
AGE_RATING_DECLARATION_BOOLEAN_FIELDS = {
    "advertising",
    "gambling",
    "healthOrWellnessTopics",
    "lootBox",
    "messagingAndChat",
    "parentalControls",
    "ageAssurance",
    "unrestrictedWebAccess",
    "userGeneratedContent",
}
AGE_RATING_DECLARATION_NULLABLE_FIELDS = {"kidsAgeBand", "developerAgeRatingInfoUrl"}
AGE_RATING_DECLARATION_ALLOWED_FIELDS = (
    AGE_RATING_DECLARATION_FREQUENCY_FIELDS
    | AGE_RATING_DECLARATION_BOOLEAN_FIELDS
    | AGE_RATING_DECLARATION_NULLABLE_FIELDS
)


def default_age_rating_declaration():
    """Default ASC questionnaire answers: all capabilities No and all content None."""
    attrs = {field: "NONE" for field in AGE_RATING_DECLARATION_FREQUENCY_FIELDS}
    attrs.update({field: False for field in AGE_RATING_DECLARATION_BOOLEAN_FIELDS})
    attrs["kidsAgeBand"] = None
    return attrs


DEFAULT_CONTENT_RIGHTS_DECLARATION = "DOES_NOT_USE_THIRD_PARTY_CONTENT"


def normalize_review_phone(phone):
    """Формат Apple: +<код страны> <номер>, например +1 555 010 0611."""
    raw = str(phone or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.lstrip().startswith("+"):
        digits = digits  # already includes country if user typed +44...
    elif len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        pass
    else:
        digits = digits
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 {digits[1:4]} {digits[4:7]} {digits[7:11]}"
    if len(digits) >= 8:
        cc = digits[0] if len(digits) <= 11 else digits[:2]
        rest = digits[len(cc):]
        chunks = [rest[i:i + 3] for i in range(0, len(rest), 3)]
        return f"+{cc} " + " ".join(chunks).strip()
    return f"+{digits}"


def prepare_app_review_detail_attributes(source, existing=None):
    """Собирает полный набор полей для App Review (PATCH требует email и phone)."""
    existing = existing or {}
    allowed = {
        "contactFirstName", "contactLastName", "contactPhone", "contactEmail",
        "notes"
    }
    merged = {}
    for field in allowed:
        if field in source and source[field] not in (None, ""):
            merged[field] = source[field]
        elif field in existing and existing[field] not in (None, ""):
            merged[field] = existing[field]
    if merged.get("contactPhone"):
        merged["contactPhone"] = normalize_review_phone(merged["contactPhone"])
    missing = [field for field in REQUIRED_APP_REVIEW_FIELDS if not merged.get(field)]
    return merged, missing


def sanitize_metadata_urls(attributes):
    result = dict(attributes)
    for key in ("supportUrl", "marketingUrl", "privacyPolicyUrl", "privacyChoicesUrl"):
        value = result.get(key)
        if not value:
            continue
        url = str(value).strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        result[key] = url
    return result


def apply_metadata_field_limits(attributes, limits_map, logger=None):
    result = {}
    for key, value in attributes.items():
        if value in (None, ""):
            continue
        if key in limits_map and isinstance(value, str) and len(value) > limits_map[key]:
            limit = limits_map[key]
            trimmed, _changed = truncate_metadata_field(key, value, limits_map)
            if logger:
                logger(
                    f"⚠️ {key}: сокращено {len(value)} → {len(trimmed)} символов "
                    f"(лимит {limit}, целые слова с конца)"
                )
            result[key] = trimmed
        else:
            result[key] = value
    return result


def sanitize_version_localization_attributes(attributes, logger=None):
    attrs = sanitize_metadata_urls(attributes)
    if attrs.get("keywords") and isinstance(attrs["keywords"], str):
        attrs["keywords"] = attrs["keywords"].lower().replace(" ", "")
    return apply_metadata_field_limits(attrs, METADATA_FIELD_LIMITS, logger)


def sanitize_app_info_localization_attributes(attributes, logger=None):
    attrs = sanitize_metadata_urls(attributes)
    return apply_metadata_field_limits(attrs, METADATA_FIELD_LIMITS, logger)


def resolve_app_category_id(category_id):
    """Конвертирует iTunes genre ID (6016), API id (ENTERTAINMENT) или имя в appCategories id."""
    if not category_id:
        return ""
    raw = str(category_id).strip()
    if not raw:
        return ""

    gui_id = normalize_gui_category_id(raw)
    if gui_id and gui_id in ITUNES_GENRE_TO_APP_CATEGORY:
        return ITUNES_GENRE_TO_APP_CATEGORY[gui_id]

    # Уже API id, в т.ч. subcategory вроде GAMES_ACTION.
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw):
        return raw
    upper = raw.upper().replace("-", "_").replace(" ", "_")
    upper = re.sub(r"_+", "_", upper).strip("_")
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", upper):
        return upper
    return ""


def normalize_gui_category_id(category_value):
    """Нормализует AI/ручной ввод категории в iTunes genre ID для GUI (напр. 6013)."""
    if category_value is None:
        return ""
    raw = str(category_value).strip()
    if not raw:
        return ""

    if raw in ITUNES_GENRE_TO_APP_CATEGORY:
        return raw

    api_to_itunes = {api: itunes for itunes, api in ITUNES_GENRE_TO_APP_CATEGORY.items()}
    upper = raw.upper().replace("-", "_").replace(" ", "_")
    upper = re.sub(r"_+", "_", upper).strip("_")
    if upper in api_to_itunes:
        return api_to_itunes[upper]

    name_key = _category_name_key(raw)
    name_map = _app_category_name_to_itunes()
    if name_key in name_map:
        return name_map[name_key]

    # "Health & Fitness (6013)" / "... primary 6013 ..."
    match = re.search(r"\b(60\d{2})\b", raw)
    if match and match.group(1) in ITUNES_GENRE_TO_APP_CATEGORY:
        return match.group(1)

    for api_id, itunes_id in api_to_itunes.items():
        if re.search(rf"\b{re.escape(api_id)}\b", raw.upper()):
            return itunes_id

    # Имя внутри rationale: сначала длинные фразы, без коротких ложных совпадений.
    for key, itunes_id in sorted(name_map.items(), key=lambda item: len(item[0]), reverse=True):
        if len(key) < 4:
            continue
        if re.search(rf"(^|[^a-z0-9]){re.escape(key)}([^a-z0-9]|$)", name_key):
            return itunes_id
    return ""


def _category_name_key(value):
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"^app\s+", "", text.strip())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _app_category_name_to_itunes():
    mapping = {}
    for itunes_id, display_name in APP_CATEGORY_OPTIONS:
        if not itunes_id:
            continue
        mapping[_category_name_key(display_name)] = itunes_id
    # Частые варианты от моделей.
    aliases = {
        "photo and video": "6008",
        "photos and video": "6008",
        "photo video": "6008",
        "social networking": "6005",
        "social network": "6005",
        "health and fitness": "6013",
        "health fitness": "6013",
        "food and drink": "6023",
        "food drink": "6023",
        "graphics and design": "6027",
        "graphic and design": "6027",
        "developer tools": "6026",
        "dev tools": "6026",
        "games": "6014",
        "game": "6014",
    }
    mapping.update(aliases)
    return mapping


def extract_category_ids_from_rationale(category_name):
    """Достаёт primary/secondary из текста categoryName от AI."""
    text = str(category_name or "").strip()
    if not text:
        return "", ""
    primary = ""
    secondary = ""
    primary_match = re.search(
        r"primary(?:\s+category)?\s*[:\-]\s*([^\n;.|]+)",
        text,
        flags=re.IGNORECASE,
    )
    secondary_match = re.search(
        r"secondary(?:\s+category)?\s*[:\-]\s*([^\n;.|]+)",
        text,
        flags=re.IGNORECASE,
    )
    if primary_match:
        primary = normalize_gui_category_id(primary_match.group(1))
    if secondary_match:
        secondary = normalize_gui_category_id(secondary_match.group(1))
    if not primary:
        primary = normalize_gui_category_id(text)
    return primary, secondary


# Словарь: Код -> (Код_Флага, Понятное название)
DEFAULT_GEMINI_PROMPT = """Language (mandatory):
Always write ALL generated metadata in English, regardless of the app's primary locale, source language, or selected UI locale. Do not output Czech, Russian, or any other language unless the user explicitly asks for a different language.

Description:
Role:
Act as a Senior Copywriter at a top-tier creative agency. Your goal is to write App Store copy that feels human, evocative, and entirely devoid of AI-speak or marketing clichés.

The Mission:
Based on the provided brief, craft an original App Store Description (max 1500 chars) and Promotional Text (max 100 chars).

Style & Execution:
Avoid these words: revolutionary, seamless, unlock, empower, unleash, ultimate, simple, solution, discover, master, stay organized.
Use staccato and flow. Alternate punchy short sentences with longer descriptive ones.
Use sensory details and specific real-world scenarios when relevant.
No bullet points in the description. No dashes. English only.

Keywords:
Act as a Senior ASO Specialist. Build a zero-waste keyword string under 100 characters including commas.
Exclude words from the App Name and Subtitle. Avoid: best, top, easy, fast, free, app, simple.
Use lowercase English words separated only by commas. No dashes.

Category:
Recommend 1 Primary category and 1 Secondary category.
For primaryCategory/secondaryCategory return ONLY an iTunes genre ID (e.g. 6013) or API enum (e.g. HEALTH_AND_FITNESS).
Never return display names like "Health & Fitness" or "Games" in those ID fields.
Put the human-readable rationale only in categoryName.
Base decisions only on features explicitly stated in the brief. Flag category risks honestly. No dashes.

App Review notes:
Create a brief courteous cover note in English for Apple App Review. 3 to 5 sentences.
Mention the developer name if provided. Main points to cover if true in the brief: no account registration/login, no paid content/subscriptions/IAP, no user-generated content, no ads/tracking, no camera/location/contacts/photos/microphone, no external services required for core functionality, all levels/content are bundled in the app.
Keep it professional, calm, human, and respectful. Do not make it sound like a standard template.
"""

PROMPT_PROFILES_FILE = os.path.join(SCRIPT_DIR, ".prompt_profiles.json")

DEFAULT_PROMPT_PROFILES = {
    "Human premium ASO": DEFAULT_GEMINI_PROMPT,
    "Strict keywords only": """Act as a Senior ASO specialist. Generate only keywords under 100 characters including commas. Use lowercase, no spaces after commas, no duplicates, no dashes, no generic filler words, and exclude words from the app name and subtitle. Return JSON only.""",
    "Review notes clean": """Create concise App Review notes in English. Professional, calm, human. Mention only facts explicitly provided in the brief. Avoid privacy/legal overexplaining and avoid template language. Return JSON only.""",
    "Category safety": """Recommend primary and secondary App Store categories based only on explicitly stated features. Be conservative and moderation-safe. primaryCategory/secondaryCategory must be iTunes IDs (6013) or API enums (HEALTH_AND_FITNESS), never display names. Explain risks only in categoryName. Return JSON only.""",
}

TRANSLATION_FIELDS = [
    ("description", "Description", 4000, "version"),
    ("keywords", "Keywords", 100, "version"),
    ("promotionalText", "Promotional Text", 170, "version"),
    ("whatsNew", "What's New", 4000, "version"),
    ("subtitle", "Subtitle", 30, "app_info"),
]

TRANSLATION_MAX_WORKERS = int(os.getenv("TRANSLATION_MAX_WORKERS", "20"))
LOCALIZATION_UPLOAD_MAX_WORKERS = int(os.getenv("LOCALIZATION_UPLOAD_MAX_WORKERS", "3"))
TRANSLATION_REQUEST_INTERVAL_SECONDS = float(os.getenv("TRANSLATION_REQUEST_INTERVAL_SECONDS", "1"))
TRANSLATION_FALLBACK_MODEL = os.getenv("TRANSLATION_FALLBACK_MODEL", "gemini-2.5-flash-lite")
TRANSLATION_ERROR_RETRIES = int(os.getenv("TRANSLATION_ERROR_RETRIES", "2"))
TRANSLATION_RETRY_PAUSE_SECONDS = float(os.getenv("TRANSLATION_RETRY_PAUSE_SECONDS", "2"))
SCREENSHOT_UPLOAD_MAX_WORKERS = int(os.getenv("SCREENSHOT_UPLOAD_MAX_WORKERS", "4"))
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 20
DESKTOP_DIR = os.path.realpath(os.path.expanduser("~/Desktop"))
DOWNLOADS_DIR = os.path.realpath(os.path.expanduser("~/Downloads"))
_SCREENSHOT_SOURCE_DIRS = (DOWNLOADS_DIR, DESKTOP_DIR)
_SCREENSHOT_NAME_RE = re.compile(
    r"(simulator\s*screenshot|screen\s*shot|screenshot|снимок\s*экрана)",
    re.IGNORECASE,
)


def _is_under_dir(path, root_dir):
    try:
        real = os.path.realpath(path)
        root = os.path.realpath(root_dir)
    except OSError:
        return False
    return real == root or real.startswith(root + os.sep)


def _is_under_screenshot_source(path):
    return any(_is_under_dir(path, root) for root in _SCREENSHOT_SOURCE_DIRS)


def _is_under_desktop(path):
    return _is_under_dir(path, DESKTOP_DIR)


def _looks_like_screenshot_filename(name):
    return bool(name) and bool(_SCREENSHOT_NAME_RE.search(os.path.basename(name)))


def _delete_desktop_screenshot_files(paths, logger=None):
    """Удаляет скрины из Downloads/Desktop после успешного upload."""
    log = logger or (lambda _msg: None)
    deleted = []
    skipped = []
    for path in paths or []:
        if not path:
            continue
        if not _is_under_screenshot_source(path):
            skipped.append(path)
            continue
        if not _is_screenshot_image_file(path):
            skipped.append(path)
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(path)
                where = "Загрузки" if _is_under_dir(path, DOWNLOADS_DIR) else "Desktop"
                log(f"🗑 Удалён скрин из {where}: {os.path.basename(path)}")
        except OSError as exc:
            log(f"⚠️ Не удалось удалить {path}: {exc}")
    return deleted, skipped


TRANSLATION_PROFILES = {
    "ASO natural": (
        "Translate as a senior App Store localization editor. Preserve the original meaning, features, "
        "constraints, and factual claims exactly. Adapt wording naturally for the target locale so it reads "
        "like native App Store copy, not a literal machine translation. Keep a polished ASO tone, but do not "
        "invent new features, benefits, pricing, privacy claims, subscriptions, tracking, login, ads, or medical/"
        "financial claims unless they are explicitly present in the source. For description and promotional text, "
        "keep the copy fluent and human. For subtitle, keep it concise and within the Apple limit. For keywords, "
        "keep comma-separated keyword intent and localize terms users would actually search for."
    ),
    "Strict literal": (
        "Translate as close to the source as possible while making the target language grammatically correct. "
        "Preserve sentence order, structure, tone, and terminology. Do not rewrite creatively, do not optimize, "
        "do not add marketing embellishments, and do not remove factual details unless required by grammar. "
        "Use this profile when legal/product accuracy matters more than ASO style."
    ),
    "Keywords optimized": (
        "Optimize specifically for App Store keyword localization. Keep the result lowercase where the language "
        "allows it, comma-separated, with no spaces after commas unless the locale normally requires them. Stay "
        "under Apple's keyword limit. Remove duplicates, generic filler words, weak words like best/top/free/easy/"
        "simple/app, and words already contained in the app name or subtitle if obvious from the source. Localize "
        "search intent, not just words: use natural local market terms, common synonyms, and high-intent phrases. "
        "Return only the final keyword string for keyword fields. For non-keyword fields, still translate naturally "
        "but keep the language concise and ASO-aware."
    ),
    "Short premium style": (
        "Translate and compress into a premium, concise App Store style. Prefer short clean sentences, polished "
        "phrasing, and confident wording. Preserve all important meaning but remove fluff, repetition, and weak "
        "generic marketing language. This is best for subtitle, promotional text, and short release notes. Do not "
        "sound exaggerated, salesy, or AI-generated. Never invent claims not present in the source."
    ),
    "No banned words": (
        "Translate naturally while actively avoiding banned or overused marketing clichés such as revolutionary, "
        "seamless, unlock, empower, unleash, ultimate, simple, solution, discover, master, stay organized, and "
        "similar hype words in the target language. Keep the wording compliant, factual, calm, and App Store-safe. "
        "Do not add unsupported privacy, tracking, login, ads, subscription, medical, or financial claims. If the "
        "source contains a risky phrase, translate the meaning using safer wording instead of copying the risk."
    ),
}

TRANSLATION_STATUS_COLORS = {
    "error": QColor("#7F1D1D"),
    "warn": QColor("#713F12"),
    "info": QColor("#1E3A8A"),
    "ok": QColor("#14532D"),
}

APP_SCREENSHOT_MAX_PER_SET = 10
IPHONE_67_SCREENSHOT_SIZE = (1290, 2796)
IPHONE_65_SCREENSHOT_SIZE = (1242, 2688)  # портрет; альбом = (2688, 1242)
IPHONE_65_SCREENSHOT_SIZE_1284 = (1284, 2778)  # 6.5" портрет (iPhone 12/13/14 Pro)
APP_IPHONE_65_DISPLAY_TYPE = "APP_IPHONE_65"
APP_IPHONE_67_DISPLAY_TYPE = "APP_IPHONE_67"
SCREENSHOT_JPEG_QUALITY = 92
SCREENSHOT_IMAGE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif",
)
SCREENSHOT_JPEG_EXTENSIONS = (".jpg", ".jpeg")
SCREENSHOT_FILE_DIALOG_FILTER = (
    "Изображения (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff *.heic *.heif);;"
    "Все файлы (*.*)"
)
VERSION_URL_ATTRIBUTE_KEYS = ("supportUrl", "marketingUrl")
API_REQUEST_MAX_RETRIES = 6
SCREENSHOT_FILE_UPLOAD_RETRIES = 4


def _clamp_workers(value, default=1):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(CONCURRENCY_MIN, min(CONCURRENCY_MAX, number))


def resolve_translation_max_workers():
    env_file = _read_env_file()
    return _clamp_workers(
        env_file.get("TRANSLATION_MAX_WORKERS") or os.getenv("TRANSLATION_MAX_WORKERS") or TRANSLATION_MAX_WORKERS,
        TRANSLATION_MAX_WORKERS,
    )


def resolve_localization_upload_max_workers():
    env_file = _read_env_file()
    return _clamp_workers(
        env_file.get("LOCALIZATION_UPLOAD_MAX_WORKERS")
        or os.getenv("LOCALIZATION_UPLOAD_MAX_WORKERS")
        or LOCALIZATION_UPLOAD_MAX_WORKERS,
        LOCALIZATION_UPLOAD_MAX_WORKERS,
    )


def resolve_screenshot_upload_max_workers():
    env_file = _read_env_file()
    return _clamp_workers(
        env_file.get("SCREENSHOT_UPLOAD_MAX_WORKERS")
        or os.getenv("SCREENSHOT_UPLOAD_MAX_WORKERS")
        or SCREENSHOT_UPLOAD_MAX_WORKERS,
        SCREENSHOT_UPLOAD_MAX_WORKERS,
    )


APP_CATEGORY_OPTIONS = [
    ("", "Не выбрано"),
    ("6000", "Business"),
    ("6001", "Weather"),
    ("6002", "Utilities"),
    ("6003", "Travel"),
    ("6004", "Sports"),
    ("6005", "Social Networking"),
    ("6006", "Reference"),
    ("6007", "Productivity"),
    ("6008", "Photo & Video"),
    ("6009", "News"),
    ("6010", "Navigation"),
    ("6011", "Music"),
    ("6012", "Lifestyle"),
    ("6013", "Health & Fitness"),
    ("6014", "Games"),
    ("6015", "Finance"),
    ("6016", "Entertainment"),
    ("6017", "Education"),
    ("6018", "Books"),
    ("6020", "Medical"),
    ("6023", "Food & Drink"),
    ("6024", "Shopping"),
    ("6025", "Stickers"),
    ("6026", "Developer Tools"),
    ("6027", "Graphics & Design"),
]

JIRA_METADATA_FIELDS = {
    "privacy_policy_url": "customfield_10952",
    "support_url": "customfield_11335",
    "app_store_link": "customfield_10422",
    "app_id": "customfield_10949",
    "category": "customfield_12223",
    "age": "customfield_12425",
    "subtitle_white": "customfield_12218",
    "keywords_white": "customfield_12288",
    "subtitle_live": "customfield_12249",
    "keywords_live": "customfield_12250",
    "description_white": "customfield_12247",
    "design_metadata": "customfield_12220",
    "codemagic_account": "customfield_12221",
}

JIRA_DESIGN_METADATA_VALUE = "Local optimize"
JIRA_CODEMAGIC_ACCOUNT_VALUE = "alexandr7"

JIRA_CATEGORY_VALUES = {
    "6000": "App Business",
    "6001": "App Weather",
    "6002": "App Utilities",
    "6003": "App Travel",
    "6004": "App Sports",
    "6005": "App Social Networking",
    "6006": "App Reference",
    "6007": "App Productivity",
    "6008": "App Photo & Video",
    "6009": "App News",
    "6010": "App Navigation",
    "6011": "App Music",
    "6012": "App Lifestyle",
    "6013": "App Health & Fitness",
    "6014": "App Games",
    "6015": "App Finance",
    "6016": "App Entertainment",
    "6017": "App Education",
    "6018": "App Books",
    "6020": "App Medical",
    "6023": "App Food & Drink",
    "6024": "App Shopping",
    "6026": "App Developer Tools",
    "6027": "App Graphics & Design",
    "BUSINESS": "App Business",
    "WEATHER": "App Weather",
    "UTILITIES": "App Utilities",
    "TRAVEL": "App Travel",
    "SPORTS": "App Sports",
    "SOCIAL_NETWORKING": "App Social Networking",
    "REFERENCE": "App Reference",
    "PRODUCTIVITY": "App Productivity",
    "PHOTO_AND_VIDEO": "App Photo & Video",
    "NEWS": "App News",
    "NAVIGATION": "App Navigation",
    "MUSIC": "App Music",
    "LIFESTYLE": "App Lifestyle",
    "HEALTH_AND_FITNESS": "App Health & Fitness",
    "GAMES": "App Games",
    "FINANCE": "App Finance",
    "ENTERTAINMENT": "App Entertainment",
    "EDUCATION": "App Education",
    "BOOKS": "App Books",
    "MEDICAL": "App Medical",
    "FOOD_AND_DRINK": "App Food & Drink",
    "SHOPPING": "App Shopping",
    "DEVELOPER_TOOLS": "App Developer Tools",
    "GRAPHICS_AND_DESIGN": "App Graphics & Design",
}

JIRA_AGE_VALUES = {
    "": "+4",
    "FIVE_AND_UNDER": "+4",
    "SIX_TO_EIGHT": "+9",
    "NINE_TO_ELEVEN": "+13",
    "+4": "+4",
    "+9": "+9",
    "+13": "+13",
    "+16": "+16",
    "+18": "+18",
}

# iTunes genre ID (GUI) → App Store Connect API appCategories id
ITUNES_GENRE_TO_APP_CATEGORY = {
    "6000": "BUSINESS",
    "6001": "WEATHER",
    "6002": "UTILITIES",
    "6003": "TRAVEL",
    "6004": "SPORTS",
    "6005": "SOCIAL_NETWORKING",
    "6006": "REFERENCE",
    "6007": "PRODUCTIVITY",
    "6008": "PHOTO_AND_VIDEO",
    "6009": "NEWS",
    "6010": "NAVIGATION",
    "6011": "MUSIC",
    "6012": "LIFESTYLE",
    "6013": "HEALTH_AND_FITNESS",
    "6014": "GAMES",
    "6015": "FINANCE",
    "6016": "ENTERTAINMENT",
    "6017": "EDUCATION",
    "6018": "BOOKS",
    "6020": "MEDICAL",
    "6023": "FOOD_AND_DRINK",
    "6024": "SHOPPING",
    "6025": "STICKERS",
    "6026": "DEVELOPER_TOOLS",
    "6027": "GRAPHICS_AND_DESIGN",
}

APP_CATEGORY_RELATIONSHIP_FIELDS = (
    "primaryCategory", "primarySubcategoryOne", "primarySubcategoryTwo",
    "secondaryCategory", "secondarySubcategoryOne", "secondarySubcategoryTwo",
)

BANNED_ASO_WORDS = [
    "revolutionary", "seamless", "unlock", "empower", "unleash",
    "ultimate", "simple", "solution", "discover", "master", "stay organized"
]

LOCALE_MAP = {
    "ar-SA": ("sa", "Arabic"),
    "bn-BD": ("bd", "Bengali"),
    "ca":    ("ad", "Catalan"), 
    "zh-Hans": ("cn", "Chinese (Simplified)"),
    "zh-Hant": ("tw", "Chinese (Traditional)"),
    "hr":    ("hr", "Croatian"),
    "cs":    ("cz", "Czech"),
    "da":    ("dk", "Danish"),
    "nl-NL": ("nl", "Dutch"),
    "en-AU": ("au", "English (Australia)"),
    "en-CA": ("ca", "English (Canada)"),
    "en-GB": ("gb", "English (U.K.)"),
    "en-US": ("us", "English (U.S.)"),
    "fi":    ("fi", "Finnish"),
    "fr-CA": ("ca", "French (Canada)"),
    "fr-FR": ("fr", "French (France)"),
    "de-DE": ("de", "German"),
    "el":    ("gr", "Greek"),
    "gu-IN": ("in", "Gujarati"),
    "he":    ("il", "Hebrew"),
    "hi":    ("in", "Hindi"),
    "hu":    ("hu", "Hungarian"),
    "id":    ("id", "Indonesian"),
    "it":    ("it", "Italian"),
    "ja":    ("jp", "Japanese"),
    "kn-IN": ("in", "Kannada"),
    "ko":    ("kr", "Korean"),
    "ms":    ("my", "Malay"),
    "ml-IN": ("in", "Malayalam"),
    "mr-IN": ("in", "Marathi"),
    "no":    ("no", "Norwegian"),
    "or-IN": ("in", "Odia"),
    "pl":    ("pl", "Polish"),
    "pt-BR": ("br", "Portuguese (Brazil)"),
    "pt-PT": ("pt", "Portuguese (Portugal)"),
    "pa-IN": ("in", "Punjabi"),
    "ro":    ("ro", "Romanian"),
    "ru":    ("ru", "Russian"),
    "sk":    ("sk", "Slovak"),
    "sl-SI": ("si", "Slovenian"),
    "es-MX": ("mx", "Spanish (Mexico)"),
    "es-ES": ("es", "Spanish (Spain)"),
    "sv":    ("se", "Swedish"),
    "ta-IN": ("in", "Tamil"),
    "te-IN": ("in", "Telugu"),
    "th":    ("th", "Thai"),
    "tr":    ("tr", "Turkish"),
    "uk":    ("ua", "Ukrainian"),
    "ur-PK": ("pk", "Urdu"),
    "vi":    ("vn", "Vietnamese")
}

def ensure_flags_downloaded():
    os.makedirs(FLAGS_CACHE_DIR, exist_ok=True)
    missing_flags = []
    
    for code, (cc, name) in LOCALE_MAP.items():
        if not os.path.exists(os.path.join(FLAGS_CACHE_DIR, f"{cc}.png")):
            missing_flags.append(cc)
            
    if missing_flags:
        print("Скачивание иконок флагов для интерфейса (один раз)...")
        unique_ccs = list(set(missing_flags))
        
        def fetch(cc):
            try:
                r = requests.get(f"https://flagcdn.com/w20/{cc}.png", timeout=5)
                if r.status_code == 200:
                    with open(os.path.join(FLAGS_CACHE_DIR, f"{cc}.png"), "wb") as f:
                        f.write(r.content)
            except: pass
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(fetch, unique_ccs)

def _is_screenshot_image_file(path):
    return bool(path) and os.path.splitext(path)[1].lower() in SCREENSHOT_IMAGE_EXTENSIONS

def resolve_figma_token():
    env_file = _read_env_file()
    return (
        env_file.get("FIGMA_TOKEN", "").strip()
        or os.getenv("FIGMA_TOKEN", "").strip()
        or env_file.get("FIGMA_ACCESS_TOKEN", "").strip()
        or os.getenv("FIGMA_ACCESS_TOKEN", "").strip()
    )


def parse_figma_url(url):
    """Достаёт file_key и optional node_id из ссылки Figma."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Пустая ссылка Figma.")
    parsed = urlparse(raw)
    path_parts = [p for p in parsed.path.split("/") if p]
    file_key = ""
    for marker in ("design", "file", "proto", "board", "deck"):
        if marker in path_parts:
            idx = path_parts.index(marker)
            if idx + 1 < len(path_parts):
                file_key = path_parts[idx + 1]
                break
    if not file_key and path_parts:
        # fallback: /FILE_KEY/...
        file_key = path_parts[0]
    if not file_key or len(file_key) < 6:
        raise ValueError("Не удалось найти file key в ссылке Figma.")

    query = parse_qs(parsed.query)
    node_raw = (query.get("node-id") or query.get("node_id") or [""])[0].strip()
    node_id = node_raw.replace("-", ":") if node_raw else ""
    return {"file_key": file_key, "node_id": node_id, "url": raw}


def figma_api_get(path, token, params=None, timeout=60):
    token = (token or "").strip()
    if not token:
        raise ValueError("Нет FIGMA_TOKEN. Укажите Personal Access Token на вкладке «Настройки API».")
    headers = {
        "X-Figma-Token": token,
        "Accept": "application/json",
    }
    response = requests.get(
        f"{FIGMA_API_BASE}{path}",
        headers=headers,
        params=params or {},
        timeout=timeout,
    )
    if response.status_code == 403:
        raise RuntimeError("Figma API 403: проверьте токен и доступ к файлу.")
    if response.status_code == 404:
        raise RuntimeError("Figma API 404: файл или node не найден.")
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:300]
        raise RuntimeError(f"Figma API {response.status_code}: {detail}")
    return response.json()


def _figma_node_size(node):
    box = node.get("absoluteBoundingBox") or {}
    return float(box.get("width") or 0), float(box.get("height") or 0)


def _figma_node_sort_key(node):
    box = node.get("absoluteBoundingBox") or {}
    return (
        float(box.get("y") or 0),
        float(box.get("x") or 0),
        (node.get("name") or ""),
    )


def _figma_find_page(document, preferred_node_id=""):
    pages = [
        child for child in (document.get("children") or [])
        if child.get("type") == "CANVAS"
    ]
    if not pages:
        raise RuntimeError("В файле Figma нет страниц (CANVAS).")
    if preferred_node_id:
        # Prefer exact page match.
        for page in pages:
            if page.get("id") == preferred_node_id:
                return page
        # Walk into pages to find which page owns the node.
        for page in pages:
            stack = list(page.get("children") or [])
            while stack:
                node = stack.pop()
                if node.get("id") == preferred_node_id:
                    return page
                stack.extend(node.get("children") or [])
    return pages[0]


FIGMA_EXPORTABLE_TYPES = ("FRAME", "COMPONENT", "INSTANCE", "RECTANGLE", "GROUP", "SECTION")


def collect_figma_page_export_nodes(page_node, min_side=200):
    """Все подходящие топ-левел ноды на странице (фреймы/картинки скринов)."""
    nodes = []
    for child in page_node.get("children") or []:
        if child.get("type") not in FIGMA_EXPORTABLE_TYPES:
            continue
        width, height = _figma_node_size(child)
        if max(width, height) < min_side:
            continue
        nodes.append(child)
    nodes.sort(key=_figma_node_sort_key)
    return nodes


def _figma_safe_filename(name, index, node_id):
    base = re.sub(r"[^\w\-]+", "_", (name or "frame").strip(), flags=re.UNICODE)
    base = re.sub(r"_+", "_", base).strip("_") or "frame"
    base = base[:80]
    nid = (node_id or "").replace(":", "-")
    return f"{index:02d}_{base}_{nid}.png"


def download_figma_page_screenshots(figma_url, token, dest_dir=None, logger=None, scale=1):
    """
    Скачивает все фреймы/скрин-ноды со страницы Figma в dest_dir.
    Возвращает отсортированный список путей к PNG.
    """
    log = logger or (lambda _msg: None)
    parsed = parse_figma_url(figma_url)
    file_key = parsed["file_key"]
    node_id = parsed["node_id"]
    log(f"Figma: file={file_key}" + (f", node={node_id}" if node_id else ""))

    # depth=2 достаточно для топ-левел детей страницы
    file_data = figma_api_get(
        f"/v1/files/{file_key}",
        token,
        params={"depth": 2},
        timeout=90,
    )
    document = file_data.get("document") or {}
    page = _figma_find_page(document, node_id)
    page_name = page.get("name") or "Page"
    export_nodes = collect_figma_page_export_nodes(page)
    if not export_nodes:
        raise RuntimeError(
            f"На странице «{page_name}» не найдено фреймов/картинок для экспорта."
        )
    log(f"Figma: страница «{page_name}» — нод к экспорту: {len(export_nodes)}")

    node_ids = [n["id"] for n in export_nodes]
    images_map = {}
    chunk_size = 10
    for start in range(0, len(node_ids), chunk_size):
        chunk = node_ids[start:start + chunk_size]
        log(f"Figma: запрос export URLs {start + 1}–{start + len(chunk)} / {len(node_ids)}...")
        payload = figma_api_get(
            f"/v1/images/{file_key}",
            token,
            params={
                "ids": ",".join(chunk),
                "format": "png",
                "scale": scale,
                "use_absolute_bounds": "true",
            },
            timeout=120,
        )
        if payload.get("err"):
            raise RuntimeError(f"Figma images error: {payload.get('err')}")
        chunk_images = payload.get("images") or {}
        images_map.update(chunk_images)

    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="figma_screens_")
    os.makedirs(dest_dir, exist_ok=True)

    saved = []
    for index, node in enumerate(export_nodes, start=1):
        nid = node["id"]
        image_url = images_map.get(nid)
        if not image_url:
            log(f"Figma: ⚠️ нет URL для «{node.get('name')}» ({nid}) — пропуск")
            continue
        filename = _figma_safe_filename(node.get("name"), index, nid)
        out_path = os.path.join(dest_dir, filename)
        log(f"Figma: скачиваю {index}/{len(export_nodes)} — {filename}")
        response = requests.get(image_url, timeout=120)
        response.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(response.content)
        saved.append(out_path)

    if not saved:
        raise RuntimeError("Figma: не удалось скачать ни одного изображения.")
    log(f"Figma: готово, файлов: {len(saved)} → {dest_dir}")
    return saved


def _list_screenshot_files_in_folder(folder_path):
    try:
        names = os.listdir(folder_path)
    except OSError:
        return []
    return sorted(
        name for name in names
        if _is_screenshot_image_file(os.path.join(folder_path, name))
    )


def _list_downloads_screenshot_paths(limit=None):
    """Скрины из ~/Downloads (Simulator Screenshot и т.п.), новые сверху."""
    folder = DOWNLOADS_DIR
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    paths = []
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if not _is_screenshot_image_file(path):
            continue
        if not _looks_like_screenshot_filename(name):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        paths.append((mtime, path))
    paths.sort(key=lambda item: (-item[0], item[1].lower()))
    result = [path for _, path in paths]
    if limit is not None:
        return result[: max(0, int(limit))]
    return result


def _list_desktop_screenshot_paths(limit=None):
    """Картинки со скриншотами на Desktop, новые сверху."""
    folder = DESKTOP_DIR
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    paths = []
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if not _is_screenshot_image_file(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        paths.append((mtime, path))
    paths.sort(key=lambda item: (-item[0], item[1].lower()))
    result = [path for _, path in paths]
    if limit is not None:
        return result[: max(0, int(limit))]
    return result


def _screenshot_upload_basename(original_path, preserve_extension=False):
    base = os.path.splitext(os.path.basename(original_path))[0]
    if preserve_extension:
        ext = os.path.splitext(original_path)[1].lower() or ".png"
        return f"{base}{ext}"
    return f"{base}.jpg"

class ScreenshotConverter:
    """Конвертация скринов в JPG перед ресайзом и локальной оптимизацией."""

    def __init__(self, logger_callback=None):
        self.logger = logger_callback or (lambda _msg: None)
        self._cache = {}
        self._temp_files = []
        self._lock = threading.Lock()

    def process(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext in SCREENSHOT_JPEG_EXTENSIONS:
            return file_path

        with self._lock:
            if file_path in self._cache:
                return self._cache[file_path]

        original_size = os.path.getsize(file_path)
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".jpg", prefix="screen_jpg_")
            os.close(fd)
            with Image.open(file_path) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(temp_path, "JPEG", quality=SCREENSHOT_JPEG_QUALITY, optimize=True)

            new_size = os.path.getsize(temp_path)
            with self._lock:
                if file_path in self._cache:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    return self._cache[file_path]
                self._temp_files.append(temp_path)
                self._cache[file_path] = temp_path
            self.logger(
                f"JPG: {os.path.basename(file_path)} "
                f"({original_size // 1024} KB → {new_size // 1024} KB)"
            )
            return temp_path
        except Exception as exc:
            self.logger(
                f"⚠️ Конвертация в JPG ({os.path.basename(file_path)}): {exc}. Используем оригинал."
            )
            with self._lock:
                self._cache[file_path] = file_path
            return file_path

    def cleanup(self):
        with self._lock:
            temp_paths = list(self._temp_files)
            self._temp_files.clear()
            self._cache.clear()
        for temp_path in temp_paths:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

class ScreenshotResizer:
    """Центр-кроп и ресайз скринов до заданного размера (JPG или PNG)."""

    def __init__(
        self,
        enabled=False,
        logger_callback=None,
        target_size=None,
        output_format="JPEG",
    ):
        self.enabled = bool(enabled)
        self.logger = logger_callback or (lambda _msg: None)
        self.target_size = target_size or IPHONE_67_SCREENSHOT_SIZE
        self.output_format = (output_format or "JPEG").upper()
        if self.output_format not in ("JPEG", "PNG"):
            self.output_format = "JPEG"
        self._cache = {}
        self._temp_files = []
        self._lock = threading.Lock()

    def process(self, file_path):
        if not self.enabled:
            return file_path
        with self._lock:
            if file_path in self._cache:
                return self._cache[file_path]

        target_w, target_h = self.target_size
        target_ratio = target_w / target_h
        original_size = os.path.getsize(file_path)
        suffix = ".png" if self.output_format == "PNG" else ".jpg"
        prefix = f"screen_{target_w}x{target_h}_"
        try:
            fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
            os.close(fd)
            with Image.open(file_path) as img:
                img = ImageOps.exif_transpose(img)
                if self.output_format == "PNG":
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                width, height = img.size
                src_ratio = width / height
                if src_ratio > target_ratio:
                    crop_w = int(height * target_ratio)
                    left = (width - crop_w) // 2
                    img = img.crop((left, 0, left + crop_w, height))
                elif src_ratio < target_ratio:
                    crop_h = int(width / target_ratio)
                    top = (height - crop_h) // 2
                    img = img.crop((0, top, width, top + crop_h))
                if img.size != (target_w, target_h):
                    img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                if self.output_format == "PNG":
                    img.save(temp_path, "PNG", optimize=True)
                else:
                    img.save(temp_path, "JPEG", quality=SCREENSHOT_JPEG_QUALITY, optimize=True)

            new_size = os.path.getsize(temp_path)
            with self._lock:
                if file_path in self._cache:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                    return self._cache[file_path]
                self._temp_files.append(temp_path)
                self._cache[file_path] = temp_path
            fmt_label = "PNG" if self.output_format == "PNG" else "JPG"
            self.logger(
                f"Ресайз: {os.path.basename(file_path)} → {target_w}×{target_h} {fmt_label} "
                f"({original_size // 1024} KB → {new_size // 1024} KB)"
            )
            return temp_path
        except Exception as exc:
            self.logger(f"⚠️ Ресайз ({os.path.basename(file_path)}): {exc}. Используем оригинал.")
            with self._lock:
                self._cache[file_path] = file_path
            return file_path

    def cleanup(self):
        with self._lock:
            temp_paths = list(self._temp_files)
            self._temp_files.clear()
            self._cache.clear()
        for temp_path in temp_paths:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

def _is_transient_network_error(exc):
    """SSL обрывы, reset соединения и таймауты — повторяем запрос."""
    if isinstance(
        exc,
        (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    cause = exc
    while cause is not None:
        if isinstance(cause, ConnectionResetError):
            return True
        if isinstance(cause, OSError) and getattr(cause, "winerror", None) == 10054:
            return True
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "unexpected_eof",
            "connection aborted",
            "connection reset",
            "remotedisconnected",
            "broken pipe",
        )
    )


def _is_retriable_screenshot_upload_error(exc):
    """Сетевой сбой / исчерпаны внутренние retry _request — можно повторить upload целиком."""
    return (
        isinstance(exc, requests.exceptions.RequestException)
        and _is_transient_network_error(exc)
    ) or (
        isinstance(exc, Exception) and str(exc).startswith("API Request failed:")
    )


class ASCClient:
    def __init__(self, issuer_id, key_id, private_key_path, app_id, logger_callback):
        self.issuer_id = issuer_id
        self.key_id = key_id
        self.private_key_path = private_key_path
        self.app_id = app_id
        self.logger = logger_callback
        self.base_url = "https://api.appstoreconnect.apple.com/v1"
        self._load_private_key()

    def _load_private_key(self):
        try:
            with open(self.private_key_path, "r", encoding="utf-8") as f:
                self.private_key = f.read()
        except Exception as e:
            self.logger(f"Ошибка загрузки ключа (.p8): {str(e)}")
            self.private_key = None

    def _generate_token(self):
        if not self.private_key:
            raise Exception("Приватный ключ не загружен. Проверьте путь к файлу .p8")
            
        headers = {"kid": self.key_id, "typ": "JWT"}
        payload = {
            "iss": self.issuer_id,
            "exp": int(time.time()) + 1200,
            "aud": "appstoreconnect-v1"
        }
        return jwt.encode(payload, self.private_key, algorithm="ES256", headers=headers)

    # Умная функция запроса с защитой от лимитов Apple и повтором при обрыве сети
    def _request(self, method, endpoint, payload=None, max_retries=API_REQUEST_MAX_RETRIES):
        url = f"{self.base_url}/{endpoint}"
        
        for attempt in range(max_retries):
            token = self._generate_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, timeout=120)
                elif method == "POST":
                    response = requests.post(url, headers=headers, json=payload, timeout=120)
                elif method == "PATCH":
                    response = requests.patch(url, headers=headers, json=payload, timeout=120)
                elif method == "DELETE":
                    response = requests.delete(url, headers=headers, timeout=120)
                    response.raise_for_status()
                    return {} 
                else:
                    return None
                    
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                retryable = False
                if e.response is not None and e.response.status_code in [429, 502, 503]:
                    retryable = True
                elif _is_transient_network_error(e):
                    retryable = True

                if retryable and attempt < max_retries - 1:
                    sleep_time = min(2 ** attempt, 10)
                    if _is_transient_network_error(e):
                        self.logger(
                            f"⚠️ Сетевая ошибка ({endpoint.split('/')[0]}), "
                            f"повтор {attempt + 2}/{max_retries} через {sleep_time} с..."
                        )
                    time.sleep(sleep_time)
                    continue
                        
                err_msg = str(e)
                if e.response is not None:
                    err_msg += f" | {e.response.text}"
                self.logger(f"HTTP Error: {err_msg}")
                raise Exception(f"API Request failed: {endpoint.split('/')[0]}")

    def get_latest_app_store_version(self):
        self.logger("Получение актуальной версии приложения...")
        endpoint = f"apps/{self.app_id}/appStoreVersions?filter[appStoreState]=PREPARE_FOR_SUBMISSION,READY_FOR_SALE"
        data = self._request("GET", endpoint)
        if not data.get("data"):
            raise Exception("Не найдена подходящая версия приложения для работы.")
        return data["data"][0]["id"]

    def get_latest_app_store_version_info(self):
        version_id = self.get_latest_app_store_version()
        attrs = self.get_app_store_version_attributes(version_id)
        return {
            "id": version_id,
            "version_string": attrs.get("versionString", ""),
            "state": attrs.get("appStoreState", ""),
            "platform": attrs.get("platform", ""),
            "app_name": self.get_app_name(),
        }

    def get_app_name(self):
        data = self._request("GET", f"apps/{self.app_id}?fields[apps]=name")
        if not data:
            return ""
        return data.get("data", {}).get("attributes", {}).get("name", "") or ""

    def list_apps(self):
        self.logger("Получение списка приложений App Store Connect...")
        endpoint = "apps?limit=200&fields[apps]=name,bundleId,sku,primaryLocale"
        data = self._request("GET", endpoint)
        apps = []
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            apps.append({
                "id": item.get("id", ""),
                "name": attrs.get("name", ""),
                "bundle_id": attrs.get("bundleId", ""),
                "sku": attrs.get("sku", ""),
                "primary_locale": attrs.get("primaryLocale", ""),
            })
        return apps

    def get_app_store_version_attributes(self, version_id):
        data = self._request("GET", f"appStoreVersions/{version_id}")
        return data.get("data", {}).get("attributes", {}) if data else {}

    def get_version_locales(self, version_id):
        endpoint = f"appStoreVersions/{version_id}/appStoreVersionLocalizations"
        data = self._request("GET", endpoint)
        locales = []
        for loc in data.get("data", []):
            locales.append(loc["attributes"]["locale"])
        return locales

    def get_version_localization_records(self, version_id):
        endpoint = f"appStoreVersions/{version_id}/appStoreVersionLocalizations?limit=200"
        data = self._request("GET", endpoint)
        return data.get("data", []) if data else []

    def get_version_localization_by_locale(self, version_id, locale):
        records = self.get_version_localization_records(version_id)
        for item in records:
            attrs = item.get("attributes", {})
            if attrs.get("locale") == locale:
                return {"id": item.get("id", ""), "attributes": attrs}
        return None

    def create_version_localization(self, version_id, locale, attributes):
        payload = {
            "data": {
                "type": "appStoreVersionLocalizations",
                "attributes": {"locale": locale, **attributes},
                "relationships": {
                    "appStoreVersion": {
                        "data": {"type": "appStoreVersions", "id": version_id}
                    }
                }
            }
        }
        return self._request("POST", "appStoreVersionLocalizations", payload)["data"]["id"]

    def _patch_resource_attributes(self, resource_type, resource_id, attributes, optional_fields=()):
        """PATCH с отбрасыванием полей, которые Apple не принимает в текущем статусе."""
        endpoint = f"{resource_type}/{resource_id}"
        current = dict(attributes)
        stripped = []
        while current:
            payload = {
                "data": {
                    "type": resource_type,
                    "id": resource_id,
                    "attributes": current,
                }
            }
            try:
                self._request("PATCH", endpoint, payload)
                return "partial" if stripped else "ok"
            except Exception as exc:
                err_text = str(exc).lower()
                removed = None
                match = re.search(r"attribute\s+'([\w]+)'", err_text)
                if match and match.group(1) in current:
                    removed = match.group(1)
                if not removed:
                    for field in optional_fields:
                        if field not in current:
                            continue
                        field_token = field.lower().replace("_", "")
                        if field_token in err_text.replace("_", ""):
                            removed = field
                            break
                if removed:
                    current.pop(removed)
                    stripped.append(removed)
                    self.logger(f"⚠️ {removed} пропущен — недоступен для текущего статуса в Apple.")
                    continue
                raise
        return "skipped"

    def update_version_localization(self, localization_id, attributes):
        return self._patch_resource_attributes(
            "appStoreVersionLocalizations",
            localization_id,
            attributes,
            VERSION_LOCALIZATION_STATE_OPTIONAL,
        )

    def update_app_store_version(self, version_id, attributes):
        return self._patch_resource_attributes(
            "appStoreVersions",
            version_id,
            attributes,
            APP_STORE_VERSION_STATE_OPTIONAL,
        )

    def get_app_primary_locale(self):
        data = self._request("GET", f"apps/{self.app_id}?fields[apps]=primaryLocale")
        primary_locale = data.get("data", {}).get("attributes", {}).get("primaryLocale")
        if not primary_locale:
            raise Exception("Не удалось получить primary locale приложения.")
        return primary_locale

    def get_app_info(self):
        data = self._request("GET", f"apps/{self.app_id}/appInfos?limit=1")
        if not data.get("data"):
            raise Exception("Не найден appInfo для приложения.")
        return data["data"][0]["id"]

    def get_app_info_localization_records(self, app_info_id):
        endpoint = f"appInfos/{app_info_id}/appInfoLocalizations?limit=200"
        data = self._request("GET", endpoint)
        return data.get("data", []) if data else []

    def get_app_info_localization_by_locale(self, app_info_id, locale):
        records = self.get_app_info_localization_records(app_info_id)
        for item in records:
            attrs = item.get("attributes", {})
            if attrs.get("locale") == locale:
                return {"id": item.get("id", ""), "attributes": attrs}
        return None

    def create_app_info_localization(self, app_info_id, locale, attributes):
        payload = {
            "data": {
                "type": "appInfoLocalizations",
                "attributes": {"locale": locale, **attributes},
                "relationships": {
                    "appInfo": {
                        "data": {"type": "appInfos", "id": app_info_id}
                    }
                }
            }
        }
        return self._request("POST", "appInfoLocalizations", payload)["data"]["id"]

    def update_app_info_localization(self, localization_id, attributes):
        return self._patch_resource_attributes(
            "appInfoLocalizations",
            localization_id,
            attributes,
            APP_INFO_LOCALIZATION_UNKNOWN,
        )

    def resolve_version_url_defaults(self, version_id, overrides=None):
        """Support/Marketing URL: GUI → primary locale → любая локаль с заполненными URL."""
        overrides = overrides or {}
        picked = {}
        for key in VERSION_URL_ATTRIBUTE_KEYS:
            val = str(overrides.get(key, "") or "").strip()
            if val:
                picked[key] = val
        if len(picked) == len(VERSION_URL_ATTRIBUTE_KEYS):
            return sanitize_metadata_urls(picked)

        records = self.get_version_localization_records(version_id)
        primary_locale = None
        try:
            primary_locale = self.get_app_primary_locale()
        except Exception:
            pass

        ordered = []
        if primary_locale:
            ordered.extend(
                r for r in records if r.get("attributes", {}).get("locale") == primary_locale
            )
        ordered.extend(
            r for r in records if not primary_locale or r.get("attributes", {}).get("locale") != primary_locale
        )
        for record in ordered:
            attrs = record.get("attributes", {}) or {}
            for key in VERSION_URL_ATTRIBUTE_KEYS:
                if key in picked:
                    continue
                val = str(attrs.get(key, "") or "").strip()
                if val:
                    picked[key] = val
        return sanitize_metadata_urls(picked)

    def ensure_version_localization(self, version_id, locale, url_defaults=None):
        defaults = self.resolve_version_url_defaults(version_id, url_defaults)
        existing = self.get_version_localization_by_locale(version_id, locale)
        if existing:
            loc_id = existing["id"]
            attrs = existing.get("attributes", {}) or {}
            patch = {}
            for key in VERSION_URL_ATTRIBUTE_KEYS:
                if defaults.get(key) and not str(attrs.get(key, "") or "").strip():
                    patch[key] = defaults[key]
            if patch:
                patch = sanitize_version_localization_attributes(patch, self.logger)
                if patch:
                    self.update_version_localization(loc_id, patch)
                    self.logger(
                        f"[{locale}] Подтянуты URL: {', '.join(patch.keys())}."
                    )
            return loc_id, False

        create_attrs = sanitize_version_localization_attributes(defaults, self.logger) if defaults else {}
        created_id = self.create_version_localization(version_id, locale, create_attrs)
        if create_attrs:
            self.logger(f"[{locale}] Локаль создана с support/marketing URL.")
        return created_id, True

    def ensure_app_info_localization(self, app_info_id, locale):
        existing = self.get_app_info_localization_by_locale(app_info_id, locale)
        if existing:
            return existing["id"], False
        created_id = self.create_app_info_localization(app_info_id, locale, {})
        return created_id, True

    def get_translation_source_payload(self, source_locale):
        version_id = self.get_latest_app_store_version()
        app_info_id = self.get_app_info()
        version_item = self.get_version_localization_by_locale(version_id, source_locale)
        app_info_item = self.get_app_info_localization_by_locale(app_info_id, source_locale)

        source = {"locale": source_locale}
        if version_item:
            attrs = version_item.get("attributes", {})
            source.update({
                "description": attrs.get("description", ""),
                "keywords": attrs.get("keywords", ""),
                "promotionalText": attrs.get("promotionalText", ""),
                "whatsNew": attrs.get("whatsNew", ""),
            })
        if app_info_item:
            attrs = app_info_item.get("attributes", {})
            source.update({
                "subtitle": attrs.get("subtitle", ""),
            })
        return {
            "version_id": version_id,
            "app_info_id": app_info_id,
            "source": source,
        }

    def update_app_info(self, app_info_id, attributes=None, relationships=None):
        payload = {
            "data": {
                "type": "appInfos",
                "id": app_info_id
            }
        }
        if attributes:
            payload["data"]["attributes"] = attributes
        if relationships:
            payload["data"]["relationships"] = relationships
        self._request("PATCH", f"appInfos/{app_info_id}", payload)

    def update_app(self, attributes):
        payload = {
            "data": {
                "type": "apps",
                "id": self.app_id,
                "attributes": attributes
            }
        }
        self._request("PATCH", f"apps/{self.app_id}", payload)

    def get_age_rating_declaration(self, app_info_id):
        data = self._request("GET", f"appInfos/{app_info_id}/ageRatingDeclaration")
        return data.get("data") if data else None

    def update_age_rating_declaration(self, declaration_id, attributes):
        payload = {
            "data": {
                "type": "ageRatingDeclarations",
                "id": declaration_id,
                "attributes": attributes
            }
        }
        self._request("PATCH", f"ageRatingDeclarations/{declaration_id}", payload)

    def get_app_review_detail(self, version_id):
        data = self._request("GET", f"appStoreVersions/{version_id}/appStoreReviewDetail")
        return data.get("data") if data else None

    def create_app_review_detail(self, version_id, attributes):
        payload = {
            "data": {
                "type": "appStoreReviewDetails",
                "attributes": attributes,
                "relationships": {
                    "appStoreVersion": {
                        "data": {"type": "appStoreVersions", "id": version_id}
                    }
                }
            }
        }
        return self._request("POST", "appStoreReviewDetails", payload)["data"]["id"]

    def update_app_review_detail(self, review_detail_id, attributes):
        payload = {
            "data": {
                "type": "appStoreReviewDetails",
                "id": review_detail_id,
                "attributes": attributes
            }
        }
        self._request("PATCH", f"appStoreReviewDetails/{review_detail_id}", payload)

    def run_custom_request(self, method, endpoint, payload=None):
        return self._request(method.upper(), endpoint.lstrip("/"), payload)

    def get_all_territory_ids(self):
        endpoint = "territories?limit=200"
        data = self._request("GET", endpoint)
        territory_ids = [item.get("id") for item in data.get("data", []) if item.get("id")]
        if not territory_ids:
            raise Exception("Не удалось получить список территорий из App Store Connect.")
        return territory_ids

    def set_app_available_territories(self, territory_ids):
        payload = {
            "data": [{"type": "territories", "id": territory_id} for territory_id in territory_ids]
        }
        self._request("PATCH", f"apps/{self.app_id}/relationships/availableTerritories", payload)

    def get_app_data_usages(self):
        endpoint = f"apps/{self.app_id}/dataUsages?include=category,purpose,dataProtection&limit=200"
        data = self._request("GET", endpoint)
        return data.get("data", []) if data else []

    def delete_app_data_usage(self, usage_id):
        self._request("DELETE", f"appDataUsages/{usage_id}")

    def create_app_data_usage(self, category_id=None, purpose_id=None, protection_id=None):
        relationships = {
            "app": {"data": {"type": "apps", "id": self.app_id}}
        }
        if category_id:
            relationships["category"] = {"data": {"type": "appDataUsageCategories", "id": category_id}}
        if protection_id:
            relationships["dataProtection"] = {"data": {"type": "appDataUsageDataProtections", "id": protection_id}}
        if purpose_id:
            relationships["purpose"] = {"data": {"type": "appDataUsagePurposes", "id": purpose_id}}
        payload = {"data": {"type": "appDataUsages", "relationships": relationships}}
        return self._request("POST", "appDataUsages", payload)

    def get_data_usage_publish_state(self):
        data = self._request("GET", f"apps/{self.app_id}/dataUsagePublishState")
        return data.get("data") if data else None

    def publish_data_usages(self):
        state = self.get_data_usage_publish_state()
        if not state:
            raise Exception("Не найден dataUsagePublishState для приложения.")
        if state.get("attributes", {}).get("published"):
            return False
        state_id = state["id"]
        payload = {
            "data": {
                "type": "appDataUsagesPublishState",
                "id": state_id,
                "attributes": {"published": True}
            }
        }
        self._request("PATCH", f"appDataUsagesPublishState/{state_id}", payload)
        return True

    def sync_app_privacy_details(self, usages_config, publish=True):
        existing = self.get_app_data_usages()
        for item in existing:
            self.delete_app_data_usage(item["id"])
        self.logger(f"Удалено старых записей App Privacy: {len(existing)}")

        created = 0
        for usage_config in usages_config:
            category = usage_config.get("category")
            purposes = usage_config.get("purposes") or []
            data_protections = usage_config.get("data_protections") or []
            if not data_protections:
                continue
            if not purposes:
                purposes = [None]
            for purpose in purposes:
                for protection in data_protections:
                    label = f"{category or 'N/A'} / {purpose or 'N/A'} / {protection}"
                    self.logger(f"Создаю App Privacy: {label}")
                    self.create_app_data_usage(category, purpose, protection)
                    created += 1

        if publish:
            if self.publish_data_usages():
                self.logger("App Privacy опубликована в App Store Connect.")
            else:
                self.logger("App Privacy уже была опубликована.")
        return created

    def get_experiments(self, version_id):
        endpoint = f"appStoreVersions/{version_id}/appStoreVersionExperiments"
        res = self._request("GET", endpoint)
        return res.get("data", []) if res else []

    def get_treatments(self, experiment_id):
        endpoint = f"appStoreVersionExperiments/{experiment_id}/appStoreVersionExperimentTreatments"
        res = self._request("GET", endpoint)
        return res.get("data", []) if res else []

    def get_localizations(self, treatment_id):
        endpoint = f"appStoreVersionExperimentTreatments/{treatment_id}/appStoreVersionExperimentTreatmentLocalizations"
        res = self._request("GET", endpoint)
        return res.get("data", []) if res else []

    def create_ppo_experiment(self, version_id, name, traffic_proportion):
        payload = {
            "data": {
                "type": "appStoreVersionExperiments",
                "attributes": {
                    "name": name,
                    "trafficProportion": int(traffic_proportion)
                },
                "relationships": {
                    "appStoreVersion": {
                        "data": {"type": "appStoreVersions", "id": version_id}
                    }
                }
            }
        }
        res = self._request("POST", "appStoreVersionExperiments", payload)
        return res["data"]["id"]

    def update_ppo_experiment(self, experiment_id, traffic_proportion):
        payload = {
            "data": {
                "type": "appStoreVersionExperiments",
                "id": experiment_id,
                "attributes": {
                    "trafficProportion": int(traffic_proportion)
                }
            }
        }
        self._request("PATCH", f"appStoreVersionExperiments/{experiment_id}", payload)

    def create_treatment(self, experiment_id, name):
        payload = {
            "data": {
                "type": "appStoreVersionExperimentTreatments",
                "attributes": {"name": name},
                "relationships": {
                    "appStoreVersionExperiment": {
                        "data": {"type": "appStoreVersionExperiments", "id": experiment_id}
                    }
                }
            }
        }
        res = self._request("POST", "appStoreVersionExperimentTreatments", payload)
        return res["data"]["id"]

    def create_treatment_localization(self, treatment_id, locale):
        payload = {
            "data": {
                "type": "appStoreVersionExperimentTreatmentLocalizations",
                "attributes": {"locale": locale},
                "relationships": {
                    "appStoreVersionExperimentTreatment": {
                        "data": {"type": "appStoreVersionExperimentTreatments", "id": treatment_id}
                    }
                }
            }
        }
        res = self._request("POST", "appStoreVersionExperimentTreatmentLocalizations", payload)
        return res["data"]["id"]

    def get_screenshot_sets(self, localization_id):
        endpoint = f"appStoreVersionExperimentTreatmentLocalizations/{localization_id}/appScreenshotSets"
        res = self._request("GET", endpoint)
        return res.get("data", []) if res else []

    def get_screenshots(self, set_id):
        endpoint = f"appScreenshotSets/{set_id}/appScreenshots"
        res = self._request("GET", endpoint)
        return res.get("data", []) if res else []

    def delete_screenshot(self, screenshot_id):
        endpoint = f"appScreenshots/{screenshot_id}"
        self._request("DELETE", endpoint)

    def clear_screenshot_set(self, set_id):
        screenshots = self.get_screenshots(set_id)
        for item in screenshots:
            self.delete_screenshot(item["id"])
        return len(screenshots)

    def create_screenshot_set(self, localization_id, display_type="APP_IPHONE_67"):
        payload = {
            "data": {
                "type": "appScreenshotSets",
                "attributes": {"screenshotDisplayType": display_type},
                "relationships": {
                    "appStoreVersionExperimentTreatmentLocalization": {
                        "data": {"type": "appStoreVersionExperimentTreatmentLocalizations", "id": localization_id}
                    }
                }
            }
        }
        res = self._request("POST", "appScreenshotSets", payload)
        return res["data"]["id"]

    def upload_screenshot(self, set_id, file_path, file_name):
        file_size = os.path.getsize(file_path)
        payload = {
            "data": {
                "type": "appScreenshots",
                "attributes": {
                    "fileName": file_name,
                    "fileSize": file_size
                },
                "relationships": {
                    "appScreenshotSet": {
                        "data": {"type": "appScreenshotSets", "id": set_id}
                    }
                }
            }
        }
        
        res = self._request("POST", "appScreenshots", payload)
        screenshot_id = res["data"]["id"]
        upload_operations = res["data"]["attributes"]["uploadOperations"]
        
        # Загрузка бинарных данных на AWS сервера Apple
        with open(file_path, "rb") as f:
            for operation in upload_operations:
                headers = {h["name"]: h["value"] for h in operation["requestHeaders"]}
                offset = operation["offset"]
                length = operation["length"]
                f.seek(offset)
                chunk = f.read(length)
                
                for attempt in range(SCREENSHOT_FILE_UPLOAD_RETRIES):
                    try:
                        req = requests.put(
                            operation["url"], headers=headers, data=chunk, timeout=180
                        )
                        req.raise_for_status()
                        break
                    except requests.exceptions.RequestException as exc:
                        if attempt >= SCREENSHOT_FILE_UPLOAD_RETRIES - 1:
                            raise
                        if _is_transient_network_error(exc) or (
                            exc.response is not None
                            and exc.response.status_code in [429, 502, 503]
                        ):
                            time.sleep(min(2 ** attempt, 8))
                            continue
                        raise

        commit_payload = {
            "data": {
                "type": "appScreenshots",
                "id": screenshot_id,
                "attributes": {"uploaded": True}
            }
        }
        self._request("PATCH", f"appScreenshots/{screenshot_id}", commit_payload)

class MetadataWorker(QThread):
    log_msg = Signal(str)
    progress_update = Signal(int, str)
    finished = Signal()
    finished_ok = Signal(bool)

    VERSION_LOCALIZATION_FIELDS = {
        "description", "keywords", "marketingUrl", "promotionalText",
        "supportUrl", "whatsNew"
    }
    APP_INFO_LOCALIZATION_FIELDS = {
        "name", "subtitle", "privacyPolicyUrl", "privacyChoicesUrl",
        # privacyPolicyText — только tvOS, для iOS не отправляем
    }
    APP_STORE_VERSION_FIELDS = {"copyright"}
    APP_REVIEW_DETAIL_FIELDS = {
        "contactFirstName", "contactLastName", "contactPhone", "contactEmail",
        "notes"
    }

    def __init__(self, api_creds, metadata_config):
        super().__init__()
        self.api_creds = api_creds
        self.metadata_config = metadata_config
        self.success = False

    def _clean_version_attributes(self, source):
        normalized = dict(source)
        for alias in ("releaseNotes", "whats_new"):
            if alias in normalized and "whatsNew" not in normalized:
                normalized["whatsNew"] = normalized[alias]
        return self._clean_attributes(normalized, self.VERSION_LOCALIZATION_FIELDS)

    def _clean_attributes(self, source, allowed_fields):
        normalized = dict(source)
        if "kidsAgeBand" in normalized:
            raw_age = str(normalized.get("kidsAgeBand", "")).strip()
            allowed_age_bands = {"", "FIVE_AND_UNDER", "SIX_TO_EIGHT", "NINE_TO_ELEVEN"}
            if raw_age and raw_age not in allowed_age_bands:
                self.log_msg.emit(
                    f"⚠️ Некорректный kidsAgeBand '{raw_age}'. Допустимо: FIVE_AND_UNDER / SIX_TO_EIGHT / NINE_TO_ELEVEN. Поле будет пропущено."
                )
                normalized.pop("kidsAgeBand", None)
        return {key: value for key, value in normalized.items() if key in allowed_fields and value not in (None, "")}

    def _clean_age_rating_declaration(self, source):
        attrs = {}
        for key, value in dict(source or {}).items():
            if key not in AGE_RATING_DECLARATION_ALLOWED_FIELDS:
                self.log_msg.emit(f"⚠️ Age Ratings: неподдерживаемое поле '{key}' пропущено.")
                continue
            if key in AGE_RATING_DECLARATION_FREQUENCY_FIELDS:
                normalized = str(value or "").strip().upper()
                if normalized not in {"NONE", "INFREQUENT", "FREQUENT"}:
                    self.log_msg.emit(
                        f"⚠️ Age Ratings: '{key}'='{value}' неверно. Используйте NONE / INFREQUENT / FREQUENT."
                    )
                    continue
                attrs[key] = normalized
            elif key in AGE_RATING_DECLARATION_BOOLEAN_FIELDS:
                if isinstance(value, bool):
                    attrs[key] = value
                elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                    attrs[key] = value.strip().lower() == "true"
                else:
                    self.log_msg.emit(f"⚠️ Age Ratings: '{key}' должен быть true/false, поле пропущено.")
            elif key in AGE_RATING_DECLARATION_NULLABLE_FIELDS:
                attrs[key] = value if value not in ("", "null") else None
        return attrs

    def run(self):
        try:
            self.progress_update.emit(0, "Подготовка метаданных...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )

            tasks_done = 0
            total_tasks = (
                len(self.metadata_config.get("version_localizations", [])) +
                len(self.metadata_config.get("app_info_localizations", [])) +
                (1 if self.metadata_config.get("app_info") else 0) +
                (1 if self.metadata_config.get("app_store_version") else 0) +
                (1 if self.metadata_config.get("app_review_detail") else 0) +
                (1 if self.metadata_config.get("availability_mode") else 0) +
                (1 if "collects_data" in self.metadata_config else 0) +
                (1 if self.metadata_config.get("age_rating_declaration") else 0) +
                (1 if self.metadata_config.get("content_rights_declaration") else 0) +
                len(self.metadata_config.get("custom_requests", []))
            )
            if total_tasks == 0:
                raise Exception("В JSON нет задач для загрузки метаданных.")

            def tick(message):
                nonlocal tasks_done
                tasks_done += 1
                percent = int((tasks_done / total_tasks) * 100)
                self.progress_update.emit(percent, message)

            version_id = None
            app_info_id = None
            version_localizations = self.metadata_config.get("version_localizations", [])
            include_whats_new = bool(self.metadata_config.get("include_whats_new", False))
            if version_localizations:
                version_id = client.get_latest_app_store_version()
                version_attrs = client.get_app_store_version_attributes(version_id)
                version_state = version_attrs.get("appStoreState", "")
                if version_state == "READY_FOR_SALE" and include_whats_new:
                    self.log_msg.emit(
                        "⚠️ What's New не отправляется: версия уже в продаже. "
                        "Создайте новую версию (1.0.1) для release notes."
                    )
                    include_whats_new = False
                existing_records = client.get_version_localization_records(version_id)
                existing_by_locale = {item["attributes"]["locale"]: item["id"] for item in existing_records}

                for item in version_localizations:
                    locale = item.get("locale")
                    if not locale:
                        raise Exception("В version_localizations найдена запись без locale.")
                    attributes = sanitize_version_localization_attributes(
                        self._clean_version_attributes(item),
                        self.log_msg.emit,
                    )
                    if not include_whats_new and attributes.pop("whatsNew", None):
                        self.log_msg.emit(
                            f"[{locale}] What's New не отправлен "
                            "(первая версия / галочка «Отправлять What's New» выключена)."
                        )
                    if not attributes:
                        self.log_msg.emit(f"[{locale}] Нет version-полей для обновления, пропуск.")
                        tick(f"Пропуск {locale}")
                        continue

                    if locale in existing_by_locale:
                        result = client.update_version_localization(existing_by_locale[locale], attributes)
                        if result == "skipped":
                            self.log_msg.emit(
                                f"⚠️ [{locale}] whatsNew недоступен в текущем статусе версии — "
                                "заполните description/keywords или создайте новую версию."
                            )
                        elif result == "partial":
                            self.log_msg.emit(
                                f"✅ [{locale}] Обновлены поля version metadata "
                                f"(часть полей пропущена — статус версии в Apple)."
                            )
                        else:
                            self.log_msg.emit(f"✅ [{locale}] Обновлены description/keywords/URLs.")
                    else:
                        client.create_version_localization(
                            version_id, locale,
                            sanitize_version_localization_attributes(attributes, self.log_msg.emit),
                        )
                        self.log_msg.emit(f"✅ [{locale}] Создана version-локализация.")
                    tick(f"Готово {locale}")

            app_info_localizations = self.metadata_config.get("app_info_localizations", [])
            app_info_payload = self.metadata_config.get("app_info")
            if app_info_localizations or app_info_payload:
                app_info_id = app_info_id or client.get_app_info()

                if app_info_payload:
                    relationships = {}
                    for rel_name in APP_CATEGORY_RELATIONSHIP_FIELDS:
                        category_id = app_info_payload.get(rel_name)
                        if not category_id:
                            continue
                        api_category_id = resolve_app_category_id(category_id)
                        if not api_category_id:
                            self.log_msg.emit(
                                f"⚠️ {rel_name}: неизвестный ID категории '{category_id}'. Поле пропущено."
                            )
                            continue
                        relationships[rel_name] = {
                            "data": {"type": "appCategories", "id": api_category_id}
                        }
                    attributes = {
                        k: v for k, v in app_info_payload.items()
                        if k not in APP_CATEGORY_RELATIONSHIP_FIELDS and v not in (None, "")
                    }
                    if attributes or relationships:
                        try:
                            client.update_app_info(app_info_id, attributes or None, relationships or None)
                            self.log_msg.emit("✅ Обновлены category/kidsAgeBand и другие appInfo-поля.")
                        except Exception as exc:
                            self.log_msg.emit(
                                f"⚠️ app_info (категории/kidsAgeBand) не обновлены: {exc}. "
                                "Остальные метаданные продолжают загружаться."
                            )
                    else:
                        self.log_msg.emit("⚠️ app_info: нет валидных полей для обновления.")
                    tick("Готово appInfo")

                if app_info_localizations:
                    existing_records = client.get_app_info_localization_records(app_info_id)
                    existing_by_locale = {item["attributes"]["locale"]: item["id"] for item in existing_records}

                    for item in app_info_localizations:
                        locale = item.get("locale")
                        if not locale:
                            raise Exception("В app_info_localizations найдена запись без locale.")
                        attributes = sanitize_app_info_localization_attributes(
                            self._clean_attributes(item, self.APP_INFO_LOCALIZATION_FIELDS),
                            self.log_msg.emit,
                        )
                        if not attributes:
                            self.log_msg.emit(f"[{locale}] Нет app info-полей для обновления, пропуск.")
                            tick(f"Пропуск {locale}")
                            continue

                        if locale in existing_by_locale:
                            result = client.update_app_info_localization(
                                existing_by_locale[locale], attributes
                            )
                            if result == "partial":
                                self.log_msg.emit(
                                    f"✅ [{locale}] Обновлены app info поля (часть пропущена Apple)."
                                )
                            else:
                                self.log_msg.emit(f"✅ [{locale}] Обновлены subtitle/privacy policy/name.")
                        else:
                            client.create_app_info_localization(app_info_id, locale, attributes)
                            self.log_msg.emit(f"✅ [{locale}] Создана app info-локализация.")
                        tick(f"Готово {locale}")

            age_rating_declaration = self.metadata_config.get("age_rating_declaration")
            if age_rating_declaration:
                app_info_id = app_info_id or client.get_app_info()
                declaration = client.get_age_rating_declaration(app_info_id)
                if not declaration:
                    raise Exception("Не найден Age Rating Declaration для приложения.")
                attributes = self._clean_age_rating_declaration(age_rating_declaration)
                if attributes:
                    client.update_age_rating_declaration(declaration["id"], attributes)
                    self.log_msg.emit("✅ Age Ratings обновлены: Step 1 = NO, content fields = NONE.")
                else:
                    self.log_msg.emit("⚠️ Age Ratings: нет валидных полей для обновления.")
                tick("Готово Age Ratings")

            content_rights_declaration = self.metadata_config.get("content_rights_declaration")
            if content_rights_declaration:
                client.update_app({
                    "contentRightsDeclaration": content_rights_declaration
                })
                self.log_msg.emit("✅ Content Rights обновлены: third-party content = NO.")
                tick("Готово Content Rights")

            app_store_version = self.metadata_config.get("app_store_version")
            if app_store_version:
                version_id = version_id or client.get_latest_app_store_version()
                version_attributes = self._clean_attributes(
                    app_store_version, self.APP_STORE_VERSION_FIELDS
                )
                if version_attributes:
                    result = client.update_app_store_version(version_id, version_attributes)
                    if result == "partial":
                        self.log_msg.emit(
                            "✅ Copyright: частично (поле недоступно в текущем статусе — проверьте в Connect)."
                        )
                    elif result == "skipped":
                        self.log_msg.emit("⚠️ Copyright не обновлён — недоступен для этой версии.")
                    else:
                        self.log_msg.emit("✅ Обновлён copyright на уровне версии приложения.")
                else:
                    self.log_msg.emit("⚠️ app_store_version: нет поддерживаемых полей, пропуск.")
                tick("Готово appStoreVersion")

            app_review_detail = self.metadata_config.get("app_review_detail")
            if app_review_detail:
                version_id = locals().get("version_id") or client.get_latest_app_store_version()
                existing_detail = client.get_app_review_detail(version_id)
                existing_attrs = existing_detail.get("attributes", {}) if existing_detail else {}
                source_attrs = self._clean_attributes(app_review_detail, self.APP_REVIEW_DETAIL_FIELDS)
                attributes, missing = prepare_app_review_detail_attributes(source_attrs, existing_attrs)
                if not attributes:
                    self.log_msg.emit("В app_review_detail нет поддерживаемых полей, пропуск.")
                    tick("Пропуск review detail")
                elif missing:
                    raise Exception(
                        "App Review: обязательны contactEmail и contactPhone "
                        f"(формат телефона: +1 555 010 0611). Не заполнено: {', '.join(missing)}"
                    )
                else:
                    if existing_detail:
                        client.update_app_review_detail(existing_detail["id"], attributes)
                        self.log_msg.emit("✅ Обновлены App Review notes/contact.")
                    else:
                        client.create_app_review_detail(version_id, attributes)
                        self.log_msg.emit("✅ Созданы App Review notes/contact.")
                    tick("Готово App Review")

            availability_mode = self.metadata_config.get("availability_mode")
            if availability_mode:
                self.log_msg.emit(
                    "⚠️ Availability пропущен: App Store Connect больше не принимает "
                    "обновление стран через этот API endpoint."
                )
                tick("Готово availability")

            if "collects_data" in self.metadata_config:
                self.log_msg.emit(
                    "⚠️ App Privacy пропущена: endpoint dataUsages больше не доступен "
                    "через текущий App Store Connect API."
                )
                tick("Готово app privacy")

            for request_config in self.metadata_config.get("custom_requests", []):
                method = request_config.get("method", "PATCH")
                endpoint = request_config.get("endpoint")
                payload = request_config.get("payload")
                if not endpoint:
                    raise Exception("В custom_requests найдена запись без endpoint.")
                client.run_custom_request(method, endpoint, payload)
                self.log_msg.emit(f"✅ Custom request выполнен: {method.upper()} {endpoint}")
                tick("Готово custom request")

            self.progress_update.emit(100, "Метаданные загружены")
            self.log_msg.emit("=== ПЕРВИЧНАЯ ЗАГРУЗКА МЕТАДАННЫХ ЗАВЕРШЕНА! ===")
            self.success = True
        except Exception as e:
            self.progress_update.emit(0, "Ошибка метаданных")
            self.log_msg.emit(f"КРИТИЧЕСКАЯ ОШИБКА МЕТАДАННЫХ: {str(e)}")
        finally:
            self.finished_ok.emit(self.success)
            self.finished.emit()


class FetchCurrentMetadataWorker(QThread):
    log_msg = Signal(str)
    metadata_fetched = Signal(dict)
    finished = Signal()

    def __init__(self, api_creds, locale):
        super().__init__()
        self.api_creds = api_creds
        self.locale = locale

    def run(self):
        try:
            self.log_msg.emit(f"Загрузка текущих метаданных Apple для {self.locale}...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            version_id = client.get_latest_app_store_version()
            version_records = client.get_version_localization_records(version_id)
            version_item = next((item["attributes"] for item in version_records if item["attributes"].get("locale") == self.locale), None)

            app_info_id = client.get_app_info()
            app_info_records = client.get_app_info_localization_records(app_info_id)
            app_info_item = next((item["attributes"] for item in app_info_records if item["attributes"].get("locale") == self.locale), None)

            review_detail = client.get_app_review_detail(version_id)
            metadata_config = {}
            if version_item:
                metadata_config["version_localizations"] = [version_item]
            if app_info_item:
                metadata_config["app_info_localizations"] = [app_info_item]
            if review_detail:
                metadata_config["app_review_detail"] = review_detail.get("attributes", {})

            self.metadata_fetched.emit(metadata_config)
            self.log_msg.emit(f"✅ Текущие метаданные для {self.locale} загружены в форму.")
        except Exception as e:
            self.log_msg.emit(f"Ошибка Pull from Apple: {str(e)}")
        finally:
            self.finished.emit()


class FetchMetadataSourceWorker(QThread):
    log_msg = Signal(str)
    source_fetched = Signal(dict)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            self.log_msg.emit("Pull from Apple: определяю primary locale и latest version...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            primary_locale = client.get_app_primary_locale()
            version_info = client.get_latest_app_store_version_info()
            version_id = version_info.get("id")
            version_records = client.get_version_localization_records(version_id)
            version_item = next(
                (item["attributes"] for item in version_records if item["attributes"].get("locale") == primary_locale),
                None
            )

            app_info_id = client.get_app_info()
            app_info_records = client.get_app_info_localization_records(app_info_id)
            app_info_item = next(
                (item["attributes"] for item in app_info_records if item["attributes"].get("locale") == primary_locale),
                None
            )

            review_detail = client.get_app_review_detail(version_id)
            metadata_config = {
                "source_locale": primary_locale,
                "version_info": version_info,
            }
            if version_item:
                metadata_config["version_localizations"] = [version_item]
            if app_info_item:
                metadata_config["app_info_localizations"] = [app_info_item]
            if review_detail:
                metadata_config["app_review_detail"] = review_detail.get("attributes", {})
            self.source_fetched.emit(metadata_config)
            self.log_msg.emit(
                f"✅ Pull from Apple готов: {primary_locale}, "
                f"{version_info.get('version_string') or '—'} {version_info.get('state') or ''}"
            )
        except Exception as e:
            self.log_msg.emit(f"Ошибка Pull from Apple: {str(e)}")
        finally:
            self.finished.emit()


class FetchPrimaryLocaleWorker(QThread):
    log_msg = Signal(str)
    primary_locale_fetched = Signal(str)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            self.log_msg.emit("Получение primary locale из App Store Connect...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            primary_locale = client.get_app_primary_locale()
            self.primary_locale_fetched.emit(primary_locale)
            self.log_msg.emit(f"✅ Primary locale приложения: {primary_locale}")
        except Exception as e:
            self.log_msg.emit(f"Ошибка загрузки primary locale: {str(e)}")
        finally:
            self.finished.emit()


class FetchAppVersionWorker(QThread):
    log_msg = Signal(str)
    version_fetched = Signal(dict)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            self.log_msg.emit("Получение версии приложения из App Store Connect...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            version_info = client.get_latest_app_store_version_info()
            self.version_fetched.emit(version_info)
            version = version_info.get("version_string") or "без versionString"
            state = version_info.get("state") or "unknown state"
            self.log_msg.emit(f"✅ Версия приложения: {version} ({state})")
        except Exception as e:
            self.log_msg.emit(f"Ошибка загрузки версии приложения: {str(e)}")
        finally:
            self.finished.emit()


class FetchAppsWorker(QThread):
    log_msg = Signal(str)
    apps_fetched = Signal(list)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                "",
                self.log_msg.emit
            )
            apps = client.list_apps()
            self.apps_fetched.emit(apps)
            self.log_msg.emit(f"✅ Приложений найдено: {len(apps)}")
        except Exception as e:
            self.log_msg.emit(f"Ошибка загрузки списка приложений: {str(e)}")
        finally:
            self.finished.emit()


class FetchTranslationSourceWorker(QThread):
    log_msg = Signal(str)
    source_fetched = Signal(dict)
    finished = Signal()

    def __init__(self, api_creds, source_locale):
        super().__init__()
        self.api_creds = api_creds
        self.source_locale = source_locale

    def run(self):
        try:
            self.log_msg.emit(f"Загрузка source metadata для {self.source_locale}...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            payload = client.get_translation_source_payload(self.source_locale)
            self.source_fetched.emit(payload)
            non_empty = sum(1 for field_key, _, _, _ in TRANSLATION_FIELDS if payload["source"].get(field_key))
            self.log_msg.emit(f"✅ Source metadata загружен. Непустых полей: {non_empty}")
        except Exception as e:
            self.log_msg.emit(f"Ошибка source pull: {str(e)}")
        finally:
            self.finished.emit()


class FetchTranslationAutoSourceWorker(QThread):
    log_msg = Signal(str)
    source_fetched = Signal(dict)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            self.log_msg.emit("Определяю primary locale и загружаю source metadata...")
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            primary_locale = client.get_app_primary_locale()
            payload = client.get_translation_source_payload(primary_locale)
            payload["primary_locale"] = primary_locale
            self.source_fetched.emit(payload)
            self.log_msg.emit(f"✅ Auto source готов для locale: {primary_locale}")
        except Exception as e:
            self.log_msg.emit(f"Ошибка auto source: {str(e)}")
        finally:
            self.finished.emit()


class GeminiLocalizationTranslationWorker(QThread):
    log_msg = Signal(str)
    progress_update = Signal(int, str)
    translations_ready = Signal(list)
    finished = Signal()

    def __init__(self, api_key, model, source_locale, target_locales, fields, profile_name, source_payload, ai_provider=AI_PROVIDER_AITUNNEL, max_workers=None):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.ai_provider = ai_provider
        self.source_locale = source_locale
        self.target_locales = target_locales
        self.fields = fields
        self.profile_name = profile_name
        self.source_payload = source_payload
        self.max_workers = _clamp_workers(max_workers, TRANSLATION_MAX_WORKERS)
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0

    def _wait_for_translation_slot(self):
        if TRANSLATION_REQUEST_INTERVAL_SECONDS <= 0:
            return
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_for = TRANSLATION_REQUEST_INTERVAL_SECONDS - elapsed
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _parse_ai_error(self, response):
        return parse_aitunnel_error(response)

    def _translate_field_with_model(self, model, target_locale, field_name, source_text):
        prompt_rule = TRANSLATION_PROFILES.get(self.profile_name, TRANSLATION_PROFILES["ASO natural"])
        prompt = f"""
Translate App Store metadata field.
Source locale: {self.source_locale}
Target locale: {target_locale}
Field: {field_name}
Profile: {self.profile_name}
Rule: {prompt_rule}

Return only translated text. No JSON. No markdown.
Preserve commas for keywords where relevant.

Source text:
{source_text}
""".strip()

        for attempt in range(4):
            self._wait_for_translation_slot()
            try:
                return ai_chat_completion(
                    self.ai_provider,
                    self.api_key,
                    model,
                    [{"role": "user", "content": prompt}],
                    temperature=0.2,
                    timeout=90,
                    max_retries=1,
                )
            except Exception as exc:
                if attempt == 3:
                    raise
                if "429" in str(exc).lower() or "quota" in str(exc).lower():
                    time.sleep(2 ** attempt)
                    continue
                raise

    def _translate_field(self, target_locale, field_name, source_text):
        try:
            return self._translate_field_with_model(self.model, target_locale, field_name, source_text)
        except Exception as exc:
            if (
                TRANSLATION_FALLBACK_MODEL
                and TRANSLATION_FALLBACK_MODEL != self.model
                and "quota exceeded" in str(exc).lower()
            ):
                self.log_msg.emit(
                    f"⚠️ {self.model} исчерпал quota, пробую fallback {TRANSLATION_FALLBACK_MODEL}: "
                    f"{target_locale} · {field_name}"
                )
                return self._translate_field_with_model(
                    TRANSLATION_FALLBACK_MODEL,
                    target_locale,
                    field_name,
                    source_text,
                )
            raise

    def _translate_task(self, locale, field_name, source_text):
        if not source_text:
            return {
                "locale": locale,
                "field": field_name,
                "source": "",
                "translation": "",
                "status": "warn",
                "warning": "Пустой source для поля",
            }
        try:
            translated = self._translate_field(locale, field_name, source_text)
            return {
                "locale": locale,
                "field": field_name,
                "source": source_text,
                "translation": translated,
                "status": "ok" if translated else "warn",
                "warning": "" if translated else "Пустой перевод",
            }
        except Exception as field_exc:
            return {
                "locale": locale,
                "field": field_name,
                "source": source_text,
                "translation": "",
                "status": "error",
                "warning": f"Ошибка AI: {field_exc}",
            }

    def _run_task_batch(self, tasks, label_prefix="Перевод"):
        rows = []
        total = max(1, len(tasks))
        done = 0
        max_workers = min(self.max_workers, total)
        self.log_msg.emit(f"{label_prefix}: {total} задач, потоков: {max_workers}")
        self.progress_update.emit(0, f"{label_prefix}: 0/{total}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(self._translate_task, locale, field_name, source_text): (locale, field_name)
                for locale, field_name, source_text in tasks
            }
            for future in concurrent.futures.as_completed(future_to_task):
                locale, field_name = future_to_task[future]
                rows.append(future.result())
                done += 1
                self.log_msg.emit(f"{label_prefix} {done}/{total}: {locale} · {field_name}")
                percent = int(done * 100 / total)
                self.progress_update.emit(percent, f"{label_prefix}: {done}/{total}")
        return rows

    def _merge_retry_rows(self, rows, retry_rows):
        by_key = {(r.get("locale"), r.get("field")): idx for idx, r in enumerate(rows)}
        for row in retry_rows:
            key = (row.get("locale"), row.get("field"))
            idx = by_key.get(key)
            if idx is None:
                rows.append(row)
            else:
                rows[idx] = row
        return rows

    def run(self):
        rows = []
        try:
            tasks = []
            for locale in self.target_locales:
                for field_name in self.fields:
                    source_text = str(self.source_payload.get(field_name, "") or "").strip()
                    tasks.append((locale, field_name, source_text))

            rows = self._run_task_batch(tasks, label_prefix="Перевод")

            retries = max(0, TRANSLATION_ERROR_RETRIES)
            for attempt in range(1, retries + 1):
                failed_rows = [r for r in rows if r.get("status") == "error"]
                if not failed_rows:
                    break
                failed_locales = sorted({
                    str(r.get("locale") or "")
                    for r in failed_rows
                    if r.get("locale")
                })
                self.log_msg.emit(
                    f"↻ Повтор перевода #{attempt}/{retries}: "
                    f"локали {', '.join(failed_locales) or '—'} "
                    f"({len(failed_rows)} полей с ошибкой)"
                )
                if TRANSLATION_RETRY_PAUSE_SECONDS > 0:
                    time.sleep(TRANSLATION_RETRY_PAUSE_SECONDS)
                retry_tasks = [
                    (
                        r.get("locale"),
                        r.get("field"),
                        str(r.get("source") or self.source_payload.get(r.get("field"), "") or "").strip(),
                    )
                    for r in failed_rows
                    if r.get("locale") and r.get("field")
                ]
                if not retry_tasks:
                    break
                retry_rows = self._run_task_batch(
                    retry_tasks,
                    label_prefix=f"Повтор #{attempt}",
                )
                rows = self._merge_retry_rows(rows, retry_rows)
                failed_keys = {(r.get("locale"), r.get("field")) for r in failed_rows}
                still_failed_keys = {
                    (r.get("locale"), r.get("field"))
                    for r in rows
                    if r.get("status") == "error" and (r.get("locale"), r.get("field")) in failed_keys
                }
                recovered = len(failed_keys) - len(still_failed_keys)
                still_failed = len(still_failed_keys)
                if recovered > 0:
                    self.log_msg.emit(
                        f"✅ После повтора #{attempt}: восстановлено полей {recovered}, "
                        f"ошибок осталось {still_failed}"
                    )
                else:
                    self.log_msg.emit(
                        f"⚠️ Повтор #{attempt}: ошибок всё ещё {still_failed}"
                    )

            final_errors = sum(1 for r in rows if r.get("status") == "error")
            self.translations_ready.emit(rows)
            self.progress_update.emit(100, "Перевод завершен")
            if final_errors:
                self.log_msg.emit(
                    f"✅ Перевод завершен с ошибками: {final_errors} полей так и не перевелись."
                )
            else:
                self.log_msg.emit("✅ Перевод локализаций завершен.")
        except Exception as e:
            self.log_msg.emit(f"Ошибка перевода локализаций: {str(e)}")
        finally:
            self.finished.emit()


class LocalizationUploadWorker(QThread):
    log_msg = Signal(str)
    progress_update = Signal(int, str)
    upload_finished = Signal(dict)
    finished = Signal()

    def __init__(self, api_creds, rows, version_url_defaults=None, max_workers=None):
        super().__init__()
        self.api_creds = api_creds
        self.rows = rows
        self.version_url_defaults = version_url_defaults or {}
        self.max_workers = _clamp_workers(max_workers, LOCALIZATION_UPLOAD_MAX_WORKERS)

    def run(self):
        summary = {"locales": 0, "fields": 0, "errors": 0}
        summary_lock = threading.Lock()
        try:
            client = ASCClient(
                self.api_creds["issuer"],
                self.api_creds["key_id"],
                self.api_creds["p8_path"],
                self.api_creds["app_id"],
                self.log_msg.emit
            )
            version_id = client.get_latest_app_store_version()
            app_info_id = client.get_app_info()
            url_defaults = client.resolve_version_url_defaults(
                version_id, self.version_url_defaults
            )
            if url_defaults:
                self.log_msg.emit(
                    "URL для новых локалей: "
                    + ", ".join(f"{k}={v}" for k, v in url_defaults.items())
                )
            grouped = {}
            for row in self.rows:
                if not str(row.get("translation", "") or "").strip():
                    continue
                if row.get("status") == "error":
                    continue
                locale = row.get("locale", "")
                if not locale:
                    continue
                grouped.setdefault(locale, {"version": {}, "app_info": {}})
                field = row.get("field")
                value = row.get("translation", "")
                field_meta = next((f for f in TRANSLATION_FIELDS if f[0] == field), None)
                if not field_meta:
                    continue
                target_key = "version" if field_meta[3] == "version" else "app_info"
                grouped[locale][target_key][field] = value

            locale_items = list(grouped.items())
            total_locales = max(1, len(locale_items))
            done_locales = 0
            done_lock = threading.Lock()
            max_workers = min(self.max_workers, max(1, len(locale_items)))
            self.log_msg.emit(
                f"Параллельный upload переводов: {len(locale_items)} локалей, потоков: {max_workers}"
            )

            def upload_locale(locale, payloads):
                local_client = ASCClient(
                    self.api_creds["issuer"],
                    self.api_creds["key_id"],
                    self.api_creds["p8_path"],
                    self.api_creds["app_id"],
                    self.log_msg.emit,
                )
                fields_uploaded = 0
                if payloads["version"]:
                    loc_id, created = local_client.ensure_version_localization(
                        version_id, locale, url_defaults
                    )
                    attrs = sanitize_version_localization_attributes(payloads["version"], self.log_msg.emit)
                    if attrs:
                        local_client.update_version_localization(loc_id, attrs)
                        self.log_msg.emit(
                            f"✅ [{locale}] version localization {'создана' if created else 'обновлена'}."
                        )
                        fields_uploaded += len(attrs)
                if payloads["app_info"]:
                    loc_id, created = local_client.ensure_app_info_localization(app_info_id, locale)
                    attrs = sanitize_app_info_localization_attributes(payloads["app_info"], self.log_msg.emit)
                    if attrs:
                        local_client.update_app_info_localization(loc_id, attrs)
                        self.log_msg.emit(
                            f"✅ [{locale}] app info localization {'создана' if created else 'обновлена'}."
                        )
                        fields_uploaded += len(attrs)
                return fields_uploaded

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(upload_locale, locale, payloads): locale
                    for locale, payloads in locale_items
                }
                for future in concurrent.futures.as_completed(future_map):
                    locale = future_map[future]
                    try:
                        fields_uploaded = future.result()
                        with summary_lock:
                            summary["locales"] += 1
                            summary["fields"] += fields_uploaded
                    except Exception as locale_exc:
                        with summary_lock:
                            summary["errors"] += 1
                        self.log_msg.emit(f"❌ [{locale}] Ошибка upload: {locale_exc}")
                    with done_lock:
                        done_locales += 1
                        current = done_locales
                    percent = int((current / total_locales) * 100)
                    self.progress_update.emit(percent, f"Локализации: {current}/{total_locales}")
            self.upload_finished.emit(summary)
        except Exception as e:
            summary["errors"] += 1
            self.log_msg.emit(f"Критическая ошибка upload локализаций: {e}")
            self.upload_finished.emit(summary)
        finally:
            self.finished.emit()


class StorePalLoginWorker(QThread):
    log_msg = Signal(str)
    login_success = Signal(str)
    login_failed = Signal(str)
    finished = Signal()

    def __init__(self, base_url=None, timeout_sec=300):
        super().__init__()
        self.base_url = (base_url or resolve_storepal_base_url()).rstrip("/")
        self.timeout_sec = timeout_sec

    def run(self):
        server = None
        try:
            state = secrets.token_hex(16)
            result = {"token": None, "state": None, "error": None}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urlparse(self.path)
                    if parsed.path != "/callback":
                        self.send_response(404)
                        self.end_headers()
                        return
                    params = parse_qs(parsed.query)
                    result["token"] = (params.get("token") or [None])[0]
                    result["state"] = (params.get("state") or [None])[0]
                    body = (
                        b"<!DOCTYPE html><html><body style='font-family:system-ui;text-align:center;"
                        b"padding:48px;color:#374151'><h2>Authorization successful</h2>"
                        b"<p>You can close this tab and return to PPO Automation.</p></body></html>"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    return

            server = HTTPServer(("127.0.0.1", 0), Handler)
            port = server.server_address[1]
            authorize_url = f"{self.base_url}/cli/authorize?port={port}&state={state}"
            self.log_msg.emit("StorePal: открываю браузер для авторизации...")
            self.log_msg.emit(f"StorePal login URL: {authorize_url}")
            try:
                webbrowser.open(authorize_url)
            except Exception:
                pass

            server.timeout = 1.0
            deadline = time.time() + self.timeout_sec
            while time.time() < deadline and not result["token"]:
                server.handle_request()
                if self.isInterruptionRequested():
                    raise RuntimeError("Авторизация StorePal отменена.")

            if not result["token"]:
                raise RuntimeError("Таймаут авторизации StorePal (5 минут). Попробуйте снова.")
            if result["state"] != state:
                raise RuntimeError("State mismatch при StorePal login.")

            token = result["token"].strip()
            save_storepal_credentials(token, self.base_url)
            # validate
            apps = storepal_list_apps(token=token)
            self.log_msg.emit(f"StorePal: вход выполнен, apps: {len(apps)}")
            self.login_success.emit(token)
        except Exception as e:
            self.login_failed.emit(str(e))
        finally:
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    pass
            self.finished.emit()


class StorePalPagesWorker(QThread):
    log_msg = Signal(str)
    pages_ready = Signal(dict)
    pages_failed = Signal(str)
    finished = Signal()

    def __init__(self, options):
        super().__init__()
        self.options = options or {}

    def run(self):
        try:
            token = (self.options.get("token") or resolve_storepal_token()).strip()
            if not token:
                raise StorePalApiError("Нет StorePal token.", code="NOT_LOGGED_IN", status=401)

            mode = self.options.get("mode") or "create"
            slug = storepal_slugify(self.options.get("slug") or self.options.get("app_name") or "my-app")
            app_name = (self.options.get("app_name") or slug).strip()
            description = (self.options.get("description") or "").strip()
            privacy_md = self.options.get("privacy_markdown") or ""
            update_privacy = bool(self.options.get("update_privacy", True))

            if mode == "create":
                self.log_msg.emit(f"StorePal: создаю app /{slug}...")
                try:
                    created = storepal_create_app(app_name, slug, description=description, token=token)
                    self.log_msg.emit("StorePal: app создан.")
                    if isinstance(created, dict) and created.get("slug"):
                        slug = created["slug"]
                except StorePalApiError as e:
                    # App may already exist — continue with update path.
                    if e.status in (409, 422) or "already" in str(e).lower() or "exists" in str(e).lower():
                        self.log_msg.emit(f"StorePal: app уже есть ({e}). Обновляю страницы...")
                    else:
                        # Some plans require dashboard create; surface clear guidance.
                        raise StorePalApiError(
                            f"{e}. Если создание через API недоступно — создайте app на "
                            f"{STOREPAL_DASHBOARD_NEW_APP_URL}, затем выберите Existing.",
                            code=e.code,
                            status=e.status,
                        )

            if update_privacy and privacy_md.strip():
                self.log_msg.emit("StorePal: загружаю Privacy Policy...")
                storepal_set_privacy(slug, privacy_md, token=token)

            self.log_msg.emit(f"StorePal: получаю URLs для /{slug}...")
            app_payload = storepal_get_app(slug, token=token)
            urls = storepal_extract_urls(app_payload)
            if not urls.get("privacy") or not urls.get("support"):
                raise StorePalApiError("StorePal не вернул privacy/support URL.")

            result = {
                "slug": slug,
                "urls": urls,
                "app": app_payload,
            }
            self.pages_ready.emit(result)
        except Exception as e:
            detail = str(e)
            if isinstance(e, StorePalApiError):
                extras = []
                if e.status:
                    extras.append(str(e.status))
                if e.code:
                    extras.append(str(e.code))
                if extras:
                    detail = f"{detail} [{', '.join(extras)}]"
            self.pages_failed.emit(detail)
        finally:
            self.finished.emit()


class LocalPrivacyFormspreeWorker(QThread):
    log_msg = Signal(str)
    pages_ready = Signal(dict)
    pages_failed = Signal(str)
    finished = Signal()

    def __init__(self, options):
        super().__init__()
        self.options = options or {}

    def run(self):
        try:
            app_name = (self.options.get("app_name") or "App").strip()
            email = (self.options.get("email") or "").strip()
            developer = (self.options.get("developer") or "").strip()
            form_id = normalize_formspree_form_id(self.options.get("formspree_form_id") or "")
            if not form_id:
                raise RuntimeError("Нет FORMSPREE_FORM_ID (hashid формы, напр. xpqwyzab).")

            self.log_msg.emit("Privacy: генерирую локально и заливаю на хостинг...")
            privacy_url = create_local_privacy_hosted_url(
                app_name=app_name,
                contact_email=email,
                developer_name=developer,
                collects_data=bool(self.options.get("collects_data")),
                uses_analytics=bool(self.options.get("uses_analytics")),
                uses_crash=bool(self.options.get("uses_crash")),
                uses_ads=bool(self.options.get("uses_ads")),
                uses_purchases=bool(self.options.get("uses_purchases")),
            )
            self.log_msg.emit(f"Privacy URL: {privacy_url}")

            self.log_msg.emit("Formspree: собираю Support HTML с формой...")
            support_html = build_formspree_support_html(
                app_name=app_name,
                contact_email=email,
                form_id=form_id,
                privacy_url=privacy_url,
            )
            slug = storepal_slugify(app_name)
            self.log_msg.emit("Хостинг Support page...")
            support_url = host_public_html(support_html, filename=f"{slug}-support.html")
            self.log_msg.emit(f"Support URL: {support_url}")

            self.pages_ready.emit({
                "privacy": privacy_url,
                "support": support_url,
                "formspree": f"{FORMSPREE_FORM_BASE}/{form_id}",
            })
        except Exception as e:
            self.pages_failed.emit(str(e))
        finally:
            self.finished.emit()


class LocalPrivacyWeb3FormsWorker(QThread):
    log_msg = Signal(str)
    pages_ready = Signal(dict)
    pages_failed = Signal(str)
    finished = Signal()

    def __init__(self, options):
        super().__init__()
        self.options = options or {}

    def run(self):
        try:
            app_name = (self.options.get("app_name") or "App").strip()
            email = (self.options.get("email") or "").strip()
            developer = (self.options.get("developer") or "").strip()
            access_key = normalize_web3forms_access_key(self.options.get("web3forms_access_key") or "")
            if not access_key:
                raise RuntimeError("Нет WEB3FORMS_ACCESS_KEY (ключ с https://web3forms.com).")

            self.log_msg.emit("Privacy: локальный HTML (alt) → BrewPage (~30 дней)...")
            privacy_url = create_local_privacy_hosted_url(
                app_name=app_name,
                contact_email=email,
                developer_name=developer,
                collects_data=bool(self.options.get("collects_data")),
                uses_analytics=bool(self.options.get("uses_analytics")),
                uses_crash=bool(self.options.get("uses_crash")),
                uses_ads=bool(self.options.get("uses_ads")),
                uses_purchases=bool(self.options.get("uses_purchases")),
                theme="alt",
                host_backend="alt",
            )
            self.log_msg.emit(f"Privacy URL: {privacy_url}")

            self.log_msg.emit("Web3Forms: собираю Support HTML...")
            support_html = build_web3forms_support_html(
                app_name=app_name,
                contact_email=email,
                access_key=access_key,
                privacy_url=privacy_url,
            )
            slug = storepal_slugify(app_name)
            self.log_msg.emit("Хостинг Support page (BrewPage ~30 дней)...")
            support_url = host_public_html(
                support_html,
                filename=f"{slug}-help.html",
                host_backend="alt",
            )
            self.log_msg.emit(f"Support URL: {support_url}")

            self.pages_ready.emit({
                "privacy": privacy_url,
                "support": support_url,
                "web3forms": WEB3FORMS_SUBMIT_URL,
            })
        except Exception as e:
            self.pages_failed.emit(str(e))
        finally:
            self.finished.emit()


class FormspreeInboxWorker(QThread):
    log_msg = Signal(str)
    submissions_ready = Signal(list)
    submissions_failed = Signal(str)
    finished = Signal()

    def __init__(self, form_id, api_key, limit=50):
        super().__init__()
        self.form_id = form_id
        self.api_key = api_key
        self.limit = limit

    def run(self):
        try:
            self.log_msg.emit("Formspree Inbox: загружаю submissions...")
            items = formspree_fetch_submissions(
                self.form_id,
                self.api_key,
                limit=self.limit,
            )
            self.log_msg.emit(f"Formspree Inbox: получено {len(items)} сообщ.")
            self.submissions_ready.emit(list(items or []))
        except Exception as e:
            self.submissions_failed.emit(str(e))
        finally:
            self.finished.emit()


class GitLabBriefWorker(QThread):
    log_msg = Signal(str)
    brief_ready = Signal(dict)
    brief_failed = Signal(str)
    finished = Signal()

    def __init__(self, app_name, token="", base_url=""):
        super().__init__()
        self.app_name = app_name
        self.token = token
        self.base_url = base_url

    def run(self):
        try:
            self.log_msg.emit(f"GitLab: ищу ТЗ для «{self.app_name}»...")
            result = fetch_gitlab_app_brief(
                self.app_name,
                token=self.token,
                base_url=self.base_url,
            )
            self.log_msg.emit(
                f"GitLab: ТЗ из {result.get('project_path')}/{GITLAB_APPLE_CONNECT_PATH} "
                f"({len(result.get('brief') or '')} симв.)"
            )
            self.brief_ready.emit(result)
        except Exception as e:
            self.brief_failed.emit(str(e))
        finally:
            self.finished.emit()


class XcodeIpaWorker(QThread):
    """Локальный аналог Codemagic: archive → IPA → (опционально) upload."""
    log_msg = Signal(str)
    build_ok = Signal(dict)
    build_failed = Signal(str)
    finished = Signal()

    def __init__(self, options):
        super().__init__()
        self.options = options or {}

    def run(self):
        try:
            opts = self.options
            asc_client = None
            if opts.get("bump_build") or opts.get("need_asc"):
                asc_client = ASCClient(
                    opts["issuer_id"],
                    opts["key_id"],
                    opts["private_key_path"],
                    opts["app_id"],
                    lambda msg: self.log_msg.emit(msg),
                )
            result = build_and_optionally_upload(
                project_dir=opts.get("project_dir", ""),
                scheme=opts.get("scheme", ""),
                team_id=opts.get("team_id", ""),
                apple_id=opts.get("app_id", ""),
                issuer_id=opts.get("issuer_id", ""),
                key_id=opts.get("key_id", ""),
                private_key_path=opts.get("private_key_path", ""),
                bump_build=bool(opts.get("bump_build")),
                upload=bool(opts.get("upload")),
                scan_leaks=bool(opts.get("scan_leaks")),
                run_bulder=bool(opts.get("run_bulder")),
                asc_client=asc_client,
                log=lambda msg: self.log_msg.emit(msg),
            )
            self.build_ok.emit(result)
        except Exception as e:
            self.build_failed.emit(str(e))
        finally:
            self.finished.emit()


class GeminiMetadataWorker(QThread):
    log_msg = Signal(str)
    metadata_generated = Signal(dict)
    finished = Signal()

    def __init__(self, api_key, model, locale, app_context, user_prompt, developer_name, generation_mode="all", current_value="", ai_provider=AI_PROVIDER_AITUNNEL):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.ai_provider = ai_provider
        self.locale = locale
        self.app_context = app_context
        self.user_prompt = user_prompt
        self.developer_name = developer_name
        self.generation_mode = generation_mode
        self.current_value = current_value

    def _extract_json(self, text):
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end >= start:
            cleaned = cleaned[start:end + 1]
        return json.loads(cleaned)

    def run(self):
        try:
            self.log_msg.emit("AI: генерирую ASO-метаданные...")
            single_field_hints = {
                "description": 'Return JSON with ONLY non-empty key "description". Other keys = "".',
                "keywords": 'Return JSON with ONLY non-empty key "keywords". Other keys = "".',
                "subtitle": 'Return JSON with ONLY non-empty key "subtitle". Other keys = "".',
                "promotional_text": 'Return JSON with ONLY non-empty key "promotionalText". Other keys = "".',
                "whats_new": 'Return JSON with ONLY non-empty key "whatsNew". Other keys = "".',
                "review_notes": 'Return JSON with ONLY non-empty key "reviewNotes". Other keys = "".',
                "category": 'Return JSON with primaryCategory, secondaryCategory, categoryName. Other text keys = "".',
            }
            field_hint = single_field_hints.get(
                self.generation_mode,
                "Fill all relevant keys for a complete metadata draft."
            )
            prompt = f"""
Generate App Store metadata.

Output language: English only. Always write every generated text field in English, regardless of the app's primary locale, brief language, or target locale below.
Target locale in App Store Connect (reference only, do NOT change output language): {self.locale}.
Generation mode: {self.generation_mode}.
Field focus: {field_hint}
Current value to rewrite or shorten if relevant:
{self.current_value or "Not provided"}
Return only valid JSON without markdown.
Do not generate or change the app name. The app name is entered manually by the user.
Use this exact JSON schema:
{{
  "subtitle": "max 30 chars",
  "description": "max 1500 chars unless the user prompt says otherwise",
  "keywords": "comma-separated keywords, max 100 chars total",
  "promotionalText": "max 100 chars if the user prompt asks for that, otherwise max 170 chars",
  "whatsNew": "short release notes if relevant, otherwise empty string",
  "primaryCategory": "REQUIRED format: iTunes genre ID like 6013 OR API enum like HEALTH_AND_FITNESS. Never use display names such as Health & Fitness or Games",
  "secondaryCategory": "REQUIRED format: iTunes genre ID like 6012 OR API enum like LIFESTYLE. Never use display names. Empty string if none",
  "categoryName": "human-readable primary and secondary category suggestion with short rationale",
  "reviewNotes": "brief courteous App Review note if requested, otherwise empty string"
}}
Allowed category IDs: 6000 Business, 6001 Weather, 6002 Utilities, 6003 Travel, 6004 Sports, 6005 Social Networking, 6006 Reference, 6007 Productivity, 6008 Photo & Video, 6009 News, 6010 Navigation, 6011 Music, 6012 Lifestyle, 6013 Health & Fitness, 6014 Games, 6015 Finance, 6016 Entertainment, 6017 Education, 6018 Books, 6020 Medical, 6023 Food & Drink, 6024 Shopping, 6025 Stickers, 6026 Developer Tools, 6027 Graphics & Design.

User prompt / creative direction:
{self.user_prompt}

Developer name to mention if relevant:
{self.developer_name or "Not provided"}

App brief / technical task:
{self.app_context}

Hard safety rules:
- Base claims only on the app brief.
- All user-facing copy must be in English unless the user prompt explicitly requests another language.
- Avoid unsupported privacy, medical, financial, login, subscription, tracking, or ads claims unless explicitly stated in the brief.
- Never use em dashes or en dashes if the user prompt forbids dashes.
- Keep keywords lowercase and comma-separated.
"""
            text = ai_chat_completion(
                self.ai_provider,
                self.api_key,
                self.model,
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                json_mode=True,
                timeout=90,
            )
            if not text:
                raise Exception("AI вернул пустой ответ.")
            generated = self._extract_json(text)
            self.metadata_generated.emit(generated)
            self.log_msg.emit(f"✅ AI сгенерировал метаданные ({self.model}). Проверьте поля перед загрузкой в Apple.")
        except Exception as e:
            error_text = str(e)
            self.log_msg.emit(f"Ошибка AI: {error_text}")
        finally:
            self.finished.emit()


class IconGenerationWorker(QThread):
    log_msg = Signal(str)
    icon_generated = Signal(bytes, dict)
    finished = Signal()

    def __init__(self, api_key, model, prompt):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.prompt = prompt

    def run(self):
        try:
            self.log_msg.emit(f"AITUNNEL: генерирую иконку {ICON_IMAGE_SIZE} ({self.model})...")
            result = aitunnel_image_generation(
                self.api_key,
                self.model,
                self.prompt,
                size=ICON_IMAGE_SIZE,
                output_format="png",
            )
            cost = result.get("cost_rub")
            if cost is not None:
                self.log_msg.emit(f"Стоимость генерации: {cost} ₽")
            self.icon_generated.emit(result["png_bytes"], {
                "model": result.get("model") or self.model,
                "cost_rub": cost,
                "balance": result.get("balance"),
            })
            self.log_msg.emit(f"✅ Иконка готова: {ICON_IMAGE_PIXELS}×{ICON_IMAGE_PIXELS} PNG")
        except Exception as e:
            self.log_msg.emit(f"Ошибка генерации иконки: {e}")
        finally:
            self.finished.emit()


class JiraWorker(QThread):
    log_msg = Signal(str)
    finished = Signal()
    success = Signal(str)
    error = Signal(str)

    def __init__(self, base_url, email, api_token, issue_key, metadata):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token
        self.issue_key = issue_key.strip().upper()
        self.metadata = metadata

    def _select_value(self, value):
        return {"value": value} if value else None

    def _adf_doc(self, value):
        content = []
        for line in str(value).splitlines():
            paragraph = {"type": "paragraph"}
            if line:
                paragraph["content"] = [{"type": "text", "text": line}]
            content.append(paragraph)
        if not content:
            content.append({"type": "paragraph"})
        return {"type": "doc", "version": 1, "content": content}

    def _build_fields(self):
        meta = self.metadata
        fields = {}
        text_fields = {
            JIRA_METADATA_FIELDS["privacy_policy_url"]: meta.get("privacy_policy_url", ""),
            JIRA_METADATA_FIELDS["support_url"]: meta.get("support_url", ""),
            JIRA_METADATA_FIELDS["app_store_link"]: meta.get("app_store_link", ""),
            JIRA_METADATA_FIELDS["app_id"]: meta.get("app_id", ""),
            JIRA_METADATA_FIELDS["subtitle_white"]: meta.get("subtitle", ""),
            JIRA_METADATA_FIELDS["keywords_white"]: meta.get("keywords", ""),
            JIRA_METADATA_FIELDS["subtitle_live"]: meta.get("subtitle", ""),
            JIRA_METADATA_FIELDS["keywords_live"]: meta.get("keywords", ""),
        }
        for field_id, value in text_fields.items():
            if value:
                fields[field_id] = str(value)

        description = meta.get("description", "")
        if description:
            fields[JIRA_METADATA_FIELDS["description_white"]] = self._adf_doc(description)

        category_value = meta.get("jira_category", "")
        if category_value:
            fields[JIRA_METADATA_FIELDS["category"]] = self._select_value(category_value)

        age_value = meta.get("jira_age", "")
        if age_value:
            fields[JIRA_METADATA_FIELDS["age"]] = self._select_value(age_value)

        fields[JIRA_METADATA_FIELDS["design_metadata"]] = self._select_value(JIRA_DESIGN_METADATA_VALUE)
        fields[JIRA_METADATA_FIELDS["codemagic_account"]] = JIRA_CODEMAGIC_ACCOUNT_VALUE

        return fields

    def run(self):
        import base64
        try:
            auth = base64.b64encode(f"{self.email}:{self.api_token}".encode()).decode()
            headers = {
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            fields = self._build_fields()
            if not fields:
                raise Exception("Нет заполненных метаданных для отправки в Jira.")

            payload = {"fields": fields}
            self.log_msg.emit(f"Обновляю Jira карточку {self.issue_key}...")
            resp = requests.put(
                f"{self.base_url}/rest/api/3/issue/{self.issue_key}",
                headers=headers,
                json=payload,
                timeout=30
            )
            if not resp.ok:
                raise Exception(f"Jira returned {resp.status_code}: {resp.text}")

            issue_url = f"{self.base_url}/browse/{self.issue_key}"
            self.log_msg.emit(f"Карточка обновлена: {self.issue_key}")
            self.success.emit(issue_url)

        except Exception as e:
            err = str(e)
            self.log_msg.emit(f"Ошибка Jira: {err}")
            self.error.emit(err)
        finally:
            self.finished.emit()


class FetchExperimentsWorker(QThread):
    log_msg = Signal(str)
    experiments_fetched = Signal(list)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            self.log_msg.emit("Подключение к App Store Connect для поиска тестов...")
            client = ASCClient(
                self.api_creds["issuer"], 
                self.api_creds["key_id"], 
                self.api_creds["p8_path"], 
                self.api_creds["app_id"], 
                self.log_msg.emit
            )
            version_id = client.get_latest_app_store_version()
            experiments = client.get_experiments(version_id)
            exp_names = [e["attributes"]["name"] for e in experiments]
            
            self.experiments_fetched.emit(exp_names)
            self.log_msg.emit(f"Успешно загружено тестов: {len(exp_names)}")
        except Exception as e:
            self.log_msg.emit(f"Ошибка загрузки тестов: {str(e)}")
        finally:
            self.finished.emit()

class AutomationWorker(QThread):
    log_msg = Signal(str)
    progress_update = Signal(int, str)
    finished = Signal()

    def __init__(
        self,
        mode,
        api_creds,
        test_name,
        traffic,
        target_variants,
        target_locales,
        variants_paths,
        optimize_images=True,
        max_workers=None,
    ):
        super().__init__()
        self.mode = mode 
        self.api_creds = api_creds
        self.test_name = test_name
        self.traffic = traffic
        self.target_variants = target_variants
        self.target_locales = target_locales 
        self.variants_paths = variants_paths
        self.optimize_images = bool(optimize_images)
        self.max_workers = _clamp_workers(max_workers, SCREENSHOT_UPLOAD_MAX_WORKERS)

    def run(self):
        try:
            self.progress_update.emit(0, "Оценка задач...")
            client = ASCClient(
                self.api_creds["issuer"], 
                self.api_creds["key_id"], 
                self.api_creds["p8_path"], 
                self.api_creds["app_id"], 
                self.log_msg.emit
            )
            
            version_id = client.get_latest_app_store_version()
            
            active_variants = []
            for v_name, f_path in self.variants_paths.items():
                if f_path:
                    files = _list_screenshot_files_in_folder(f_path)
                    active_variants.append((v_name, f_path, files))

            if not active_variants:
                raise Exception("Не выбрана ни одна папка со скриншотами для загрузки.")

            upload_tasks = [] # Единый список всех задач для многопоточности

            # === ПОДГОТОВКА СТРУКТУРЫ ТЕСТА ===
            if self.mode == "create":
                app_locales = client.get_version_locales(version_id)
                num_locales = len(app_locales)
                self.log_msg.emit(f"Найдено локалей в приложении: {num_locales}")
                self.log_msg.emit(f"Создание теста '{self.test_name}'...")
                
                experiment_id = client.create_ppo_experiment(version_id, self.test_name, self.traffic)
                
                self.log_msg.emit("ШАГ 1: Создание структуры теста...")
                for variant_name in self.variants_paths.keys():
                    treatment_id = client.create_treatment(experiment_id, variant_name)
                    
                    for locale in app_locales:
                        loc_id = client.create_treatment_localization(treatment_id, locale)
                        
                        # Собираем задачу в список для дальнейшей многопоточной загрузки
                        for active_v_name, f_path, files in active_variants:
                            if active_v_name == variant_name:
                                upload_tasks.append({
                                    "variant": variant_name, 
                                    "locale": locale, 
                                    "loc_id": loc_id,
                                    "folder": f_path, 
                                    "files": files
                                })
                        
                client.update_ppo_experiment(experiment_id, self.traffic)
                self.log_msg.emit("✅ Структура готова! Переход к загрузке.")

            elif self.mode == "update":
                self.log_msg.emit(f"Поиск теста '{self.test_name}'...")
                experiments = client.get_experiments(version_id)
                exp = next((e for e in experiments if e["attributes"]["name"] == self.test_name), None)
                if not exp:
                    raise Exception(f"Тест '{self.test_name}' не найден.")
                
                exp_id = exp["id"]
                treatments = client.get_treatments(exp_id)
                treatment_map = {t["attributes"]["name"]: t["id"] for t in treatments}

                for v_name, f_path, files in active_variants:
                    if v_name not in self.target_variants:
                        continue 
                        
                    actual_treatment_name = None
                    if v_name in treatment_map:
                        actual_treatment_name = v_name
                    else:
                        alt_name = v_name.replace("Variant", "Treatment")
                        if alt_name in treatment_map:
                            actual_treatment_name = alt_name

                    if not actual_treatment_name:
                        self.log_msg.emit(f"Внимание: Вариант '{v_name}' не найден в Apple. Пропуск.")
                        continue
                        
                    t_id = treatment_map[actual_treatment_name]
                    locs = client.get_localizations(t_id)
                    
                    for loc in locs:
                        locale_code = loc["attributes"]["locale"]
                        if locale_code not in self.target_locales:
                            continue 
                            
                        upload_tasks.append({
                            "variant": actual_treatment_name, 
                            "locale": locale_code, 
                            "loc_id": loc["id"],
                            "folder": f_path, 
                            "files": files
                        })

                if not upload_tasks:
                    raise Exception("Не найдено локалей или вариантов для обновления. Проверьте галочки.")

            # === МНОГОПОТОЧНАЯ ЗАГРУЗКА КАРТИНОК ===
            if not upload_tasks:
                self.log_msg.emit("Задач на загрузку картинок нет.")
                self.finished.emit()
                return

            converter = ScreenshotConverter(self.log_msg.emit)
            optimizer = LocalImageOptimizer(self.optimize_images, self.log_msg.emit)
            seen_paths = set()
            prep_paths = []
            for task in upload_tasks:
                for filename in task["files"]:
                    fp = os.path.normpath(os.path.join(task["folder"], filename))
                    if fp not in seen_paths:
                        seen_paths.add(fp)
                        prep_paths.append(fp)

            self.log_msg.emit("Конвертация скринов в JPG...")
            converted_map = {}
            for i, fp in enumerate(prep_paths, start=1):
                self.log_msg.emit(f"JPG: подготовка {i}/{len(prep_paths)} — {os.path.basename(fp)}")
                converted_map[fp] = converter.process(fp)

            if optimizer.enabled:
                self.log_msg.emit("Локальная оптимизация скринов перед загрузкой (как на вкладке «Загрузка скринов»)...")
                for i, fp in enumerate(converted_map.values(), start=1):
                    self.log_msg.emit(f"Оптимизация: подготовка {i}/{len(prep_paths)} — {os.path.basename(fp)}")
                    optimizer.compress(fp)

            total_tasks = sum([1 + len(task["files"]) for task in upload_tasks])
            completed_tasks = 0
            start_time = time.time()
            progress_lock = threading.Lock() # Блокировка для безопасного обновления прогресса

            def tick():
                nonlocal completed_tasks
                with progress_lock:
                    completed_tasks += 1
                    percent = int((completed_tasks / total_tasks) * 100)
                    elapsed = time.time() - start_time
                    avg = elapsed / completed_tasks
                    rem = avg * (total_tasks - completed_tasks)
                    m, s = divmod(int(rem), 60)
                    self.progress_update.emit(percent, f"Осталось: ~{m:02d}:{s:02d}")

            # Рабочая функция для одного потока
            def upload_worker(task):
                friendly_name = LOCALE_MAP.get(task['locale'], ("", task['locale']))[1]
                self.log_msg.emit(f"[{task['variant']} | {friendly_name}] Запуск обработки...")
                
                def prepare_screenshot_set():
                    sets = client.get_screenshot_sets(task['loc_id'])
                    target_set_id = None
                    for s in sets:
                        if s["attributes"]["screenshotDisplayType"] == "APP_IPHONE_67":
                            target_set_id = s["id"]
                        for old_sc in client.get_screenshots(s["id"]):
                            client.delete_screenshot(old_sc["id"])
                    if not target_set_id:
                        target_set_id = client.create_screenshot_set(task['loc_id'], "APP_IPHONE_67")
                    return target_set_id

                for attempt in range(SCREENSHOT_FILE_UPLOAD_RETRIES):
                    try:
                        target_set_id = prepare_screenshot_set()
                        break
                    except Exception as exc:
                        if not _is_retriable_screenshot_upload_error(exc) or attempt >= SCREENSHOT_FILE_UPLOAD_RETRIES - 1:
                            raise
                        wait = min(2 ** attempt, 8)
                        self.log_msg.emit(
                            f"[{task['variant']} | {friendly_name}] "
                            f"⚠️ Ошибка подготовки набора скринов, "
                            f"повтор {attempt + 2}/{SCREENSHOT_FILE_UPLOAD_RETRIES} через {wait} с..."
                        )
                        time.sleep(wait)
                tick()

                for index, filename in enumerate(task['files'], start=1):
                    filepath = os.path.normpath(os.path.join(task['folder'], filename))
                    jpg_path = converted_map.get(filepath) or converter.process(filepath)
                    new_filename = f"{index}.jpg"
                    upload_path = optimizer.compress(jpg_path) if optimizer.enabled else jpg_path
                    self.log_msg.emit(
                        f"[{task['variant']} | {friendly_name}] Upload {index}/{len(task['files'])}: {new_filename}"
                    )
                    for attempt in range(SCREENSHOT_FILE_UPLOAD_RETRIES):
                        try:
                            client.upload_screenshot(target_set_id, upload_path, new_filename)
                            break
                        except Exception as exc:
                            if (
                                not _is_retriable_screenshot_upload_error(exc)
                                or attempt >= SCREENSHOT_FILE_UPLOAD_RETRIES - 1
                            ):
                                raise
                            wait = min(2 ** attempt, 8)
                            self.log_msg.emit(
                                f"[{task['variant']} | {friendly_name}] "
                                f"⚠️ Ошибка загрузки {new_filename}, "
                                f"повтор {attempt + 2}/{SCREENSHOT_FILE_UPLOAD_RETRIES} через {wait} с..."
                            )
                            time.sleep(wait)
                    tick()

            max_workers = min(self.max_workers, max(1, len(upload_tasks)))
            self.log_msg.emit(
                f"Начинаем многопоточную загрузку ({len(upload_tasks)} задач), потоков: {max_workers}."
            )
            
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(upload_worker, t) for t in upload_tasks]
                    
                    # Ожидаем завершения всех потоков и отлавливаем ошибки
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            raise Exception(f"Сбой в одном из потоков: {str(e)}")
            finally:
                converter.cleanup()
                optimizer.cleanup()

            self.progress_update.emit(100, "Завершено!")
            self.log_msg.emit("=== ПРОЦЕСС УСПЕШНО ЗАВЕРШЕН! ===")
            
        except Exception as e:
            self.progress_update.emit(0, "Ошибка выполнения")
            self.log_msg.emit(f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
        finally:
            self.finished.emit()

class RefreshLocalesWorker(QThread):
    log_msg = Signal(str)
    locales_fetched = Signal(list)
    finished = Signal()

    def __init__(self, api_creds):
        super().__init__()
        self.api_creds = api_creds

    def run(self):
        try:
            client = ASCClient(
                self.api_creds["issuer"], self.api_creds["key_id"],
                self.api_creds["p8_path"], self.api_creds["app_id"], self.log_msg.emit
            )
            version_id = client.get_latest_app_store_version()
            records = client.get_version_localization_records(version_id)
            locales = [item.get("attributes", {}).get("locale") for item in records if item.get("attributes", {}).get("locale")]
            self.locales_fetched.emit(locales)
            self.log_msg.emit(f"✅ Активных локалей найдено: {len(locales)}")
        except Exception as e:
            self.log_msg.emit(f"Ошибка обновления локалей: {str(e)}")
        finally:
            self.finished.emit()

class FigmaImportWorker(QThread):
    log_msg = Signal(str)
    import_finished = Signal(list)
    import_failed = Signal(str)
    finished = Signal()

    def __init__(self, figma_url, token):
        super().__init__()
        self.figma_url = figma_url
        self.token = token

    def run(self):
        try:
            paths = download_figma_page_screenshots(
                self.figma_url,
                self.token,
                logger=self.log_msg.emit,
                scale=1,
            )
            self.import_finished.emit(paths)
        except Exception as e:
            self.import_failed.emit(str(e))
        finally:
            self.finished.emit()


class ScreenshotUploadWorker(QThread):
    log_msg = Signal(str)
    progress_update = Signal(int, str)
    upload_finished = Signal(bool)
    finished = Signal()

    def __init__(
        self,
        api_creds,
        target_locales,
        jpeg_files,
        optimize_images=True,
        version_url_defaults=None,
        resize_to_iphone_67=False,
        iphone_65_direct=False,
        iphone_65_crop_png=False,
        max_workers=None,
    ):
        super().__init__()
        self.api_creds = api_creds
        self.target_locales = target_locales
        self.jpeg_files = jpeg_files
        self.optimize_images = bool(optimize_images)
        self.version_url_defaults = version_url_defaults or {}
        self.resize_to_iphone_67 = resize_to_iphone_67
        self.iphone_65_direct = iphone_65_direct
        self.iphone_65_crop_png = iphone_65_crop_png
        self.max_workers = _clamp_workers(max_workers, SCREENSHOT_UPLOAD_MAX_WORKERS)

    def run(self):
        optimizer = None
        resizer = None
        converter = None
        use_65 = self.iphone_65_direct or self.iphone_65_crop_png
        display_type = APP_IPHONE_65_DISPLAY_TYPE if use_65 else APP_IPHONE_67_DISPLAY_TYPE
        preserve_extension = use_65
        try:
            if self.iphone_65_direct:
                self.log_msg.emit(
                    f"Режим 6.5\" ({IPHONE_65_SCREENSHOT_SIZE[0]}×{IPHONE_65_SCREENSHOT_SIZE[1]}): "
                    f"без JPG и кропа → {'оптимизация → ' if self.optimize_images else ''}upload..."
                )
                source_files = list(self.jpeg_files)
            elif self.iphone_65_crop_png:
                tw, th = IPHONE_65_SCREENSHOT_SIZE_1284
                self.log_msg.emit(
                    f"Режим 6.5\" ({tw}×{th}): кроп/ресайз → PNG"
                    f"{' → оптимизация' if self.optimize_images else ''}..."
                )
                resizer = ScreenshotResizer(
                    enabled=True,
                    logger_callback=self.log_msg.emit,
                    target_size=IPHONE_65_SCREENSHOT_SIZE_1284,
                    output_format="PNG",
                )
                source_files = [resizer.process(path) for path in self.jpeg_files]
            else:
                converter = ScreenshotConverter(self.log_msg.emit)
                self.log_msg.emit("Конвертация скринов в JPG...")
                jpg_files = [converter.process(path) for path in self.jpeg_files]

                resizer = ScreenshotResizer(
                    enabled=self.resize_to_iphone_67,
                    logger_callback=self.log_msg.emit,
                    target_size=IPHONE_67_SCREENSHOT_SIZE,
                    output_format="JPEG",
                )
                source_files = jpg_files
                if resizer.enabled:
                    suffix = " перед оптимизацией" if self.optimize_images else ""
                    self.log_msg.emit(f"Ресайз скринов до 1290×2796 JPG{suffix}...")
                    source_files = [resizer.process(path) for path in jpg_files]

            optimizer = LocalImageOptimizer(self.optimize_images, self.log_msg.emit)
            if optimizer.enabled:
                self.log_msg.emit("Локальная оптимизация скринов перед загрузкой...")
                prepared_files = [optimizer.compress(path) for path in source_files]
            else:
                prepared_files = source_files

            client = ASCClient(
                self.api_creds["issuer"], self.api_creds["key_id"],
                self.api_creds["p8_path"], self.api_creds["app_id"], self.log_msg.emit
            )
            version_id = client.get_latest_app_store_version()
            url_defaults = client.resolve_version_url_defaults(
                version_id, self.version_url_defaults
            )
            if url_defaults:
                self.log_msg.emit(
                    "URL для новых локалей (скрины): "
                    + ", ".join(f"{k}={v}" for k, v in url_defaults.items())
                )
            records = client.get_version_localization_records(version_id)
            loc_to_id = {item["attributes"]["locale"]: item["id"] for item in records if item.get("attributes", {}).get("locale")}
            total_steps = max(1, len(self.target_locales) * len(self.jpeg_files))
            done = 0
            progress_lock = threading.Lock()
            locales_lock = threading.Lock()

            def tick():
                nonlocal done
                with progress_lock:
                    done += 1
                    percent = int((done / total_steps) * 100)
                    self.progress_update.emit(percent, f"Загрузка: {percent}%")

            def upload_locale(locale):
                local_client = ASCClient(
                    self.api_creds["issuer"], self.api_creds["key_id"],
                    self.api_creds["p8_path"], self.api_creds["app_id"], self.log_msg.emit
                )
                with locales_lock:
                    self.log_msg.emit(f"[{locale}] Проверка version-локали и URL...")
                    localization_id, _created = local_client.ensure_version_localization(
                        version_id, locale, url_defaults
                    )
                    loc_to_id[locale] = localization_id
                    time.sleep(0.5)
                self.log_msg.emit(f"[{locale}] Подготовка screenshot set {display_type}...")
                sets = local_client._request("GET", f"appStoreVersionLocalizations/{localization_id}/appScreenshotSets").get("data", [])
                target_set = next((s for s in sets if s.get("attributes", {}).get("screenshotDisplayType") == display_type), None)
                if target_set is None:
                    payload = {
                        "data": {
                            "type": "appScreenshotSets",
                            "attributes": {"screenshotDisplayType": display_type},
                            "relationships": {
                                "appStoreVersionLocalization": {
                                    "data": {"type": "appStoreVersionLocalizations", "id": localization_id}
                                }
                            }
                        }
                    }
                    target_set_id = local_client._request("POST", "appScreenshotSets", payload)["data"]["id"]
                else:
                    target_set_id = target_set["id"]

                removed = local_client.clear_screenshot_set(target_set_id)
                if removed:
                    self.log_msg.emit(f"[{locale}] Удалено старых скринов: {removed}")

                files_to_upload = prepared_files[:APP_SCREENSHOT_MAX_PER_SET]
                if len(prepared_files) > APP_SCREENSHOT_MAX_PER_SET:
                    self.log_msg.emit(
                        f"[{locale}] ⚠️ Apple допускает максимум {APP_SCREENSHOT_MAX_PER_SET} скринов. "
                        f"Будут загружены первые {APP_SCREENSHOT_MAX_PER_SET} из {len(prepared_files)}."
                    )

                for idx, file_path in enumerate(files_to_upload, start=1):
                    if self.iphone_65_crop_png:
                        base = os.path.splitext(os.path.basename(self.jpeg_files[idx - 1]))[0]
                        file_name = f"{idx}_{base}.png"
                    else:
                        file_name = f"{idx}_{_screenshot_upload_basename(self.jpeg_files[idx - 1], preserve_extension=preserve_extension)}"
                    self.log_msg.emit(f"[{locale}] Upload {idx}/{len(files_to_upload)}: {file_name}")
                    for attempt in range(SCREENSHOT_FILE_UPLOAD_RETRIES):
                        try:
                            local_client.upload_screenshot(target_set_id, file_path, file_name)
                            break
                        except Exception as exc:
                            transient = _is_retriable_screenshot_upload_error(exc)
                            if not transient or attempt >= SCREENSHOT_FILE_UPLOAD_RETRIES - 1:
                                raise
                            wait = min(2 ** attempt, 8)
                            self.log_msg.emit(
                                f"[{locale}] ⚠️ Ошибка загрузки {file_name}, "
                                f"повтор {attempt + 2}/{SCREENSHOT_FILE_UPLOAD_RETRIES} через {wait} с..."
                            )
                            time.sleep(wait)
                    tick()
                    time.sleep(1)

            max_workers = min(self.max_workers, max(1, len(self.target_locales)))
            self.log_msg.emit(f"Запускаем параллельную загрузку скринов: потоков {max_workers}.")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(upload_locale, locale) for locale in self.target_locales]
                for future in concurrent.futures.as_completed(futures):
                    future.result()

            self.log_msg.emit("✅ Upload скриншотов завершен.")
            self.upload_finished.emit(True)
        except Exception as e:
            self.log_msg.emit(f"Ошибка Upload: {str(e)}")
            self.upload_finished.emit(False)
        finally:
            if converter:
                converter.cleanup()
            if resizer:
                resizer.cleanup()
            if optimizer:
                optimizer.cleanup()
            self.finished.emit()


class CollapsibleApiPanel(QGroupBox):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 14)
        root_layout.setSpacing(10)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "hint")
        self.summary_label.setWordWrap(True)
        root_layout.addWidget(self.summary_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            max_h = int(screen.availableGeometry().height() * UI_API_SCROLL_MAX_RATIO)
            self.scroll_area.setMaximumHeight(max(320, max_h))

        self.body_widget = QWidget()
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(2, 2, 6, 2)
        self.body_layout.setSpacing(12)
        self.scroll_area.setWidget(self.body_widget)
        root_layout.addWidget(self.scroll_area)
        self.toggled.connect(self._on_toggled)
        self._on_toggled(True)

    def _on_toggled(self, expanded):
        self.scroll_area.setVisible(expanded)

    def set_summary(self, text, status="neutral"):
        self.summary_label.setText(text)
        self.summary_label.setProperty("status", status)
        self.summary_label.style().unpolish(self.summary_label)
        self.summary_label.style().polish(self.summary_label)


class VariantFolderCard(QFrame):
    select_requested = Signal(str)

    def __init__(self, variant_name, parent=None):
        super().__init__(parent)
        self.variant_name = variant_name
        self.setObjectName("variant_card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel(variant_name)
        title.setProperty("role", "section")
        layout.addWidget(title)

        self.path_label = QLabel("Папка не выбрана")
        self.path_label.setProperty("role", "hint")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.select_btn = QPushButton("Выбрать папку")
        self.select_btn.setObjectName("utility_btn")
        self.select_btn.clicked.connect(lambda: self.select_requested.emit(self.variant_name))
        layout.addWidget(self.select_btn)

    def set_path(self, folder_path):
        if folder_path:
            label = os.path.basename(folder_path)
            try:
                image_count = len(_list_screenshot_files_in_folder(folder_path))
                if image_count:
                    label = f"{label} · {image_count} скринов"
            except OSError:
                pass
            self.path_label.setText(label)
            self.path_label.setToolTip(folder_path)
            self.setProperty("has_path", "true")
        else:
            self.path_label.setText("Папка не выбрана")
            self.path_label.setToolTip("")
            self.setProperty("has_path", "false")
        self.style().unpolish(self)
        self.style().polish(self)


class LocalePickerWidget(QGroupBox):
    def __init__(
        self,
        locales_map,
        title="Локали",
        columns=UI_LOCALE_GRID_COLUMNS,
        parent=None,
        collapsible=False,
        start_collapsed=False,
    ):
        super().__init__(title, parent)
        self._locales_map = locales_map
        self._columns = max(2, columns)
        self._checkboxes = {}
        self._all_codes = []
        self._filtered_codes = []
        self._base_title = title
        self._collapsible = bool(collapsible)
        self._expanded_height = None
        self._build_ui()
        self._populate_locales()
        if self._collapsible:
            self.setCheckable(True)
            self.setChecked(not start_collapsed)
            self.toggled.connect(self._on_collapse_toggled)
            if start_collapsed:
                self._apply_collapsed(True)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._body_widgets = []

        controls_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск локали: код или название")
        self.search_input.textChanged.connect(self._apply_filter)
        controls_row.addWidget(self.search_input, stretch=1)

        self.btn_select_all = QPushButton("Выбрать все")
        self.btn_select_all.setObjectName("utility_btn")
        self.btn_select_all.clicked.connect(lambda: self._set_filtered_checked(True))
        controls_row.addWidget(self.btn_select_all)

        self.btn_clear_all = QPushButton("Снять все")
        self.btn_clear_all.setObjectName("utility_btn")
        self.btn_clear_all.clicked.connect(lambda: self._set_filtered_checked(False))
        controls_row.addWidget(self.btn_clear_all)
        layout.addLayout(controls_row)
        self._body_widgets.extend([self.search_input, self.btn_select_all, self.btn_clear_all])

        self.selection_label = QLabel()
        self.selection_label.setProperty("role", "hint")
        layout.addWidget(self.selection_label)
        self._body_widgets.append(self.selection_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumHeight(260)
        self.scroll_widget = QWidget()
        self.scroll_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.grid = QGridLayout(self.scroll_widget)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(6)
        self.grid.setAlignment(Qt.AlignTop)
        for column in range(self._columns):
            self.grid.setColumnMinimumWidth(column, UI_LOCALE_COLUMN_MIN_WIDTH)
            self.grid.setColumnStretch(column, 1)
        self.scroll_area.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll_area)
        self._body_widgets.append(self.scroll_area)

    def _on_collapse_toggled(self, expanded):
        self._apply_collapsed(not expanded)

    def _apply_collapsed(self, collapsed):
        for widget in self._body_widgets:
            widget.setVisible(not collapsed)
        if collapsed:
            self.setMaximumHeight(52)
            self.setMinimumHeight(0)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            if self._expanded_height is not None:
                self.setMaximumHeight(self._expanded_height)
                self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            else:
                self.setMaximumHeight(16777215)
                self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._update_selection_count()

    def _populate_locales(self):
        sorted_locales = sorted(self._locales_map.items(), key=lambda item: item[1][1])
        self._all_codes = [code for code, _ in sorted_locales]
        for code, (country_code, locale_name) in sorted_locales:
            cb = QCheckBox(f" {locale_name} ({code})")
            cb.setProperty("locale_code", code)
            cb.setMinimumWidth(UI_LOCALE_COLUMN_MIN_WIDTH)
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            icon_path = os.path.join(FLAGS_CACHE_DIR, f"{country_code}.png")
            if os.path.exists(icon_path):
                cb.setIcon(QIcon(icon_path))
                cb.setIconSize(QSize(20, 15))
            cb.stateChanged.connect(self._update_selection_count)
            self._checkboxes[code] = cb
        self._filtered_codes = list(self._all_codes)
        self._rebuild_grid()
        self._update_selection_count()

    def _rebuild_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        row, col = 0, 0
        for code in self._filtered_codes:
            cb = self._checkboxes[code]
            self.grid.addWidget(cb, row, col)
            col += 1
            if col >= self._columns:
                col = 0
                row += 1

    def _update_selection_count(self, *_args):
        selected = len(self.get_selected_locales())
        total = len(self._all_codes)
        visible = len(self._filtered_codes)
        if visible < total:
            self.selection_label.setText(
                f"Выбрано: {selected} из {total} · показано {visible} по фильтру"
            )
        else:
            self.selection_label.setText(f"Выбрано: {selected} из {total}")
        if self._collapsible:
            self.setTitle(f"{self._base_title} · выбрано {selected}")

    def _apply_filter(self):
        query = self.search_input.text().strip().lower()
        if not query:
            self._filtered_codes = list(self._all_codes)
        else:
            filtered = []
            for code in self._all_codes:
                _, name = self._locales_map[code]
                haystack = f"{code} {name}".lower()
                if query in haystack:
                    filtered.append(code)
            self._filtered_codes = filtered
        self._rebuild_grid()

    def _set_filtered_checked(self, state):
        for code in self._filtered_codes:
            self._checkboxes[code].setChecked(state)
        self._update_selection_count()

    def set_all_checked(self, state):
        for cb in self._checkboxes.values():
            cb.setChecked(state)
        self._update_selection_count()

    def get_selected_locales(self):
        return [code for code in self._all_codes if self._checkboxes[code].isChecked()]

    def set_selected_locales(self, locale_codes):
        selected = set(locale_codes)
        for code, cb in self._checkboxes.items():
            cb.setChecked(code in selected)
        self._update_selection_count()

    def set_available_locales(self, locale_codes):
        self.set_selected_locales(locale_codes)

    def set_compact_height(self, height=380):
        self._expanded_height = height
        if not self._collapsible or self.isChecked():
            self.setMaximumHeight(height)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setMaximumHeight(52)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

class ScreenshotDropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("drop_zone")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(4)
        title = QLabel("Перетащите скриншоты сюда (PNG, JPG, WEBP и др.)")
        title.setProperty("role", "section")
        hint = QLabel("Любой формат будет автоматически конвертирован в JPG перед обработкой.")
        hint.setProperty("role", "hint")
        layout.addWidget(title)
        layout.addWidget(hint)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            valid = any(
                _is_screenshot_image_file(url.toLocalFile())
                for url in event.mimeData().urls()
            )
            if valid:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        files = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if _is_screenshot_image_file(url.toLocalFile())
        ]
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            event.ignore()


class SidebarTabWidget(QWidget):
    """Виджет-навигация с вертикальным sidebar слева (как в мокапе).
    Эмулирует API QTabWidget: addTab(), currentIndex(), currentChanged, tabBar()."""

    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tab_labels = []
        self._tab_icons = []
        self._buttons = []
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Sidebar (вертикальная навигация) ---
        self._sidebar = QWidget()
        self._sidebar.setObjectName("nav_sidebar")
        self._sidebar.setFixedWidth(210)
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 16)
        sidebar_layout.setSpacing(4)

        # Логотип/заголовок вверху sidebar
        logo = QLabel("🍏  PPO Automation")
        logo.setObjectName("nav_logo")
        sidebar_layout.addWidget(logo)
        sidebar_layout.addSpacing(14)

        self._nav_container = QVBoxLayout()
        self._nav_container.setSpacing(2)
        sidebar_layout.addLayout(self._nav_container)
        sidebar_layout.addStretch()

        main_layout.addWidget(self._sidebar)

        # --- Stacked content ---
        # content_column позволяет разместить topbar НАД stack
        self._content_column = QVBoxLayout()
        self._content_column.setContentsMargins(0, 0, 0, 0)
        self._content_column.setSpacing(0)
        self._stack = QStackedWidget()
        self._content_column.addWidget(self._stack)
        main_layout.addLayout(self._content_column, stretch=1)

    def addTab(self, widget, label, icon=None):
        index = len(self._tab_labels)
        self._tab_labels.append(label)
        self._tab_icons.append(icon)
        self._stack.addWidget(widget)

        btn = QPushButton(f"  {icon or '●'}   {label}")
        btn.setObjectName("nav_item")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda checked, idx=index: self.setCurrentIndex(idx))
        self._buttons.append(btn)
        self._nav_container.addWidget(btn)

        if index == 0:
            self.setCurrentIndex(0)
        return index

    def insert_topbar(self, widget):
        """Размещает виджет-полосу над областью контента (stack), справа от sidebar."""
        self._content_column.insertWidget(0, widget)

    def setCurrentIndex(self, index):
        if index < 0 or index >= len(self._buttons):
            return
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == index)
        self.currentChanged.emit(index)

    def currentIndex(self):
        return self._stack.currentIndex()

    def widget(self, index):
        """Совместимость с QTabWidget.widget(index)."""
        return self._stack.widget(index)

    def count(self):
        return self._stack.count()

    def currentWidget(self):
        return self._stack.currentWidget()

    def tabBar(self):
        """Заглушка для совместимости с _apply_display_polish."""
        class _StubBar:
            def setExpanding(self, *a, **k): pass
            def setUsesScrollButtons(self, *a, **k): pass
            def setDocumentMode(self, *a, **k): pass
        return _StubBar()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        ensure_flags_downloaded()
        
        self.setWindowTitle("PPO Automation")
        self._initial_width, self._initial_height = default_window_size()
        self.resize(self._initial_width, self._initial_height)
        self._did_initial_maximize = False
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.setMinimumSize(min(1180, avail.width() - 24), min(780, avail.height() - 48))
        else:
            self.setMinimumSize(1180, 780)
        self.variants_paths = {"Variant A": "", "Variant B": "", "Variant C": ""}
        self._summary_app_name = ""
        self._summary_app_version = ""
        self.app_version_fetcher = None
        self.metadata_fetcher = None
        self.apps_fetcher = None
        self._pending_chain_screenshots_upload = False
        self._app_id_change_timer = QTimer(self)
        self._app_id_change_timer.setSingleShot(True)
        self._app_id_change_timer.setInterval(700)
        self._app_id_change_timer.timeout.connect(self._on_app_id_changed_debounced)
        self._setup_ui()
        self._apply_styles()
        self._apply_display_polish()

    def showEvent(self, event):
        super().showEvent(event)
        if self._did_initial_maximize:
            return
        self._did_initial_maximize = True
        # На macOS maximize надёжнее после первого show.
        QTimer.singleShot(0, self._maximize_to_screen)

    def _maximize_to_screen(self):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        self.showMaximized()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        sorted_locales = sorted(LOCALE_MAP.items(), key=lambda item: item[1][1])

        self.tab_api = QWidget()
        api_outer = QVBoxLayout(self.tab_api)
        api_outer.setContentsMargins(0, 0, 0, 0)
        api_outer.setSpacing(0)
        api_scroll = QScrollArea()
        api_scroll.setWidgetResizable(True)
        api_scroll.setFrameShape(QFrame.NoFrame)
        api_inner = QWidget()
        api_layout = QVBoxLayout(api_inner)
        api_layout.setSpacing(12)
        api_layout.setContentsMargins(4, 4, 12, 12)
        api_layout.addWidget(make_page_header(
            "Настройки API",
            "API credentials, ключи AI (AITUNNEL или Z.AI) и Jira сохраняются в .env автоматически."
        ))
        api_scroll.setWidget(api_inner)
        api_outer.addWidget(api_scroll)

        api_caption = QLabel("API credentials сохраняются в .env автоматически.")
        api_caption.setProperty("role", "hint")
        api_caption.setWordWrap(True)
        api_layout.addWidget(api_caption)

        self.issuer_input = QLineEdit(os.getenv("ISSUER_ID", ""))
        self.issuer_input.setPlaceholderText("Issuer ID из App Store Connect")
        self.key_input = QLineEdit(os.getenv("KEY_ID", ""))
        self.key_input.setPlaceholderText("Авто из AuthKey_*.p8")
        self.app_input = QLineEdit(os.getenv("APP_ID", ""))
        self.app_input.setPlaceholderText("APP_ID (Apple ID)")
        self.btn_fetch_app_id = QPushButton("Загрузить App ID")
        self.btn_fetch_app_id.setObjectName("utility_btn")
        self.btn_fetch_app_id.setMinimumWidth(140)
        self.btn_fetch_app_id.clicked.connect(self._fetch_app_ids)

        self.p8_path_input = QLineEdit(os.getenv("PRIVATE_KEY_PATH", ""))
        self.p8_path_input.setPlaceholderText("Путь к файлу AuthKey_...p8")
        self.p8_path_input.editingFinished.connect(self._autofill_key_id_from_p8_path)
        self.btn_select_p8 = QPushButton("Выбрать .p8")
        self.btn_select_p8.setObjectName("utility_btn")
        self.btn_select_p8.setMinimumWidth(140)
        self.btn_select_p8.clicked.connect(self._select_p8_file)

        _add_labeled_fields_row(
            api_layout,
            [("Issuer ID", self.issuer_input), ("Key ID", self.key_input)],
        )
        _add_labeled_fields_row(
            api_layout,
            [("App ID", self.app_input)],
            trailing_widget=self.btn_fetch_app_id,
        )
        _add_labeled_fields_row(
            api_layout,
            [("Private key (.p8)", self.p8_path_input)],
            trailing_widget=self.btn_select_p8,
        )

        api_separator = QFrame()
        api_separator.setFrameShape(QFrame.HLine)
        api_separator.setStyleSheet("QFrame { color: #2a2a45; margin: 4px 0; }")
        api_layout.addWidget(api_separator)

        api_layout.addWidget(make_section_label("Figma (импорт скриншотов)"))
        figma_hint = QLabel(
            "Personal Access Token: Figma → Settings → Security → Personal access tokens. "
            "Ссылку на файл вставляйте на вкладке «Скриншоты» (у каждой прилы своя)."
        )
        figma_hint.setProperty("role", "hint")
        figma_hint.setWordWrap(True)
        api_layout.addWidget(figma_hint)
        self.figma_token_input = _prepare_api_field(QLineEdit(resolve_figma_token()))
        self.figma_token_input.setPlaceholderText("figd_... (Figma Personal Access Token)")
        self.figma_token_input.setEchoMode(QLineEdit.Password)
        self.figma_token_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.figma_token_input)

        storepal_sep = QFrame()
        storepal_sep.setFrameShape(QFrame.HLine)
        storepal_sep.setStyleSheet("QFrame { color: #2a2a45; margin: 4px 0; }")
        api_layout.addWidget(storepal_sep)
        api_layout.addWidget(make_section_label("StorePal (основной: Privacy + Support + inbox у них)"))
        storepal_hint = QLabel(
            "Основной вариант. Бесплатно до 3 apps. Token из .env (STOREPAL_TOKEN) или ~/.storepal/credentials.json. "
            "Feedback inbox смотрите в кабинете StorePal."
        )
        storepal_hint.setProperty("role", "hint")
        storepal_hint.setWordWrap(True)
        api_layout.addWidget(storepal_hint)
        self.storepal_token_input = _prepare_api_field(QLineEdit(resolve_storepal_token()))
        self.storepal_token_input.setPlaceholderText("sp_user_... (StorePal token)")
        self.storepal_token_input.setEchoMode(QLineEdit.Password)
        self.storepal_token_input.editingFinished.connect(self._on_storepal_token_edited)
        api_layout.addWidget(self.storepal_token_input)
        storepal_btns = QHBoxLayout()
        self.btn_storepal_login = QPushButton("🔑 StorePal Login")
        self.btn_storepal_login.setObjectName("utility_btn")
        self.btn_storepal_login.clicked.connect(self._start_storepal_login)
        self.btn_storepal_import = QPushButton("Import ~/.storepal")
        self.btn_storepal_import.setObjectName("utility_btn")
        self.btn_storepal_import.clicked.connect(self._import_storepal_credentials)
        self.btn_storepal_docs = QPushButton("Docs")
        self.btn_storepal_docs.setObjectName("utility_btn")
        self.btn_storepal_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(STOREPAL_DOCS_CLI_URL))
        )
        storepal_btns.addWidget(self.btn_storepal_login)
        storepal_btns.addWidget(self.btn_storepal_import)
        storepal_btns.addWidget(self.btn_storepal_docs)
        storepal_btns.addStretch(1)
        api_layout.addLayout(storepal_btns)

        alt_sep = QFrame()
        alt_sep.setFrameShape(QFrame.HLine)
        alt_sep.setStyleSheet("QFrame { color: #2a2a45; margin: 4px 0; }")
        api_layout.addWidget(alt_sep)
        api_layout.addWidget(make_section_label("Резерв Privacy/Support (без StorePal)"))
        alt_hint = QLabel(
            "1) Formspree + BrewPage (~30 дней). "
            "2) Web3Forms + BrewPage (~30 дней, другой вид страниц / random URL). "
            "Для постоянного URL лучше StorePal. "
            "Inbox только у Formspree (API key, на free может не быть)."
        )
        alt_hint.setProperty("role", "hint")
        alt_hint.setWordWrap(True)
        api_layout.addWidget(alt_hint)
        self.formspree_form_id_input = _prepare_api_field(QLineEdit(resolve_formspree_form_id()))
        self.formspree_form_id_input.setPlaceholderText("Formspree form id / https://formspree.io/f/xxxx")
        self.formspree_form_id_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.formspree_form_id_input)
        self.formspree_api_key_input = _prepare_api_field(QLineEdit(resolve_formspree_api_key()))
        self.formspree_api_key_input.setPlaceholderText("Formspree API key (для Inbox в PPO)")
        self.formspree_api_key_input.setEchoMode(QLineEdit.Password)
        self.formspree_api_key_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.formspree_api_key_input)
        self.web3forms_key_input = _prepare_api_field(QLineEdit(resolve_web3forms_access_key()))
        self.web3forms_key_input.setPlaceholderText("Web3Forms access key (https://web3forms.com)")
        self.web3forms_key_input.setEchoMode(QLineEdit.Password)
        self.web3forms_key_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.web3forms_key_input)

        ai_label = make_section_label("AI провайдер (перевод и генерация метаданных)")
        api_layout.addWidget(ai_label)

        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.setMinimumHeight(UI_FIELD_MIN_HEIGHT)
        self.ai_provider_combo.addItem("AITUNNEL", AI_PROVIDER_AITUNNEL)
        self.ai_provider_combo.addItem("Z.AI (GLM подписка)", AI_PROVIDER_ZAI)
        provider_idx = self.ai_provider_combo.findData(resolve_ai_provider())
        if provider_idx >= 0:
            self.ai_provider_combo.setCurrentIndex(provider_idx)
        self.ai_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        self.ai_provider_combo.currentIndexChanged.connect(self._save_env_to_file)
        provider_col = QVBoxLayout()
        provider_col.setSpacing(5)
        provider_col.addWidget(make_section_label("Провайдер"))
        provider_col.addWidget(self.ai_provider_combo)
        api_layout.addLayout(provider_col)

        self.aitunnel_key_input = _prepare_api_field(QLineEdit(resolve_aitunnel_api_key()))
        self.aitunnel_key_input.setPlaceholderText("AITUNNEL API key")
        self.aitunnel_key_input.setEchoMode(QLineEdit.Password)
        self.aitunnel_key_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(make_section_label("AITUNNEL API key (чат + генерация картинок)"))
        api_layout.addWidget(self.aitunnel_key_input)

        aitunnel_model_row = QHBoxLayout()
        aitunnel_model_row.setSpacing(12)
        self.aitunnel_model_combo = QComboBox()
        self.aitunnel_model_combo.setMinimumHeight(UI_FIELD_MIN_HEIGHT)
        self.aitunnel_model_combo.currentIndexChanged.connect(self._save_env_to_file)
        self.btn_refresh_aitunnel_models = QPushButton("↻ Модели")
        self.btn_refresh_aitunnel_models.setObjectName("utility_btn")
        self.btn_refresh_aitunnel_models.setToolTip("Обновить список дешёвых и средних моделей AITUNNEL")
        self.btn_refresh_aitunnel_models.clicked.connect(self._refresh_aitunnel_models)
        model_col = QVBoxLayout()
        model_col.setSpacing(5)
        model_col.addWidget(make_section_label("Модель AITUNNEL (дешёвые и средние)"))
        model_col.addWidget(self.aitunnel_model_combo)
        aitunnel_model_row.addLayout(model_col, 1)
        aitunnel_model_row.addWidget(self.btn_refresh_aitunnel_models, 0, Qt.AlignBottom)

        self.aitunnel_settings_widget = QWidget()
        aitunnel_settings_layout = QVBoxLayout(self.aitunnel_settings_widget)
        aitunnel_settings_layout.setContentsMargins(0, 0, 0, 0)
        aitunnel_settings_layout.setSpacing(8)
        aitunnel_settings_layout.addLayout(aitunnel_model_row)
        api_layout.addWidget(self.aitunnel_settings_widget)

        aitunnel_image_model_row = QHBoxLayout()
        aitunnel_image_model_row.setSpacing(12)
        self.aitunnel_image_model_combo = QComboBox()
        self.aitunnel_image_model_combo.setMinimumHeight(UI_FIELD_MIN_HEIGHT)
        self.aitunnel_image_model_combo.currentIndexChanged.connect(self._save_env_to_file)
        self.btn_refresh_aitunnel_image_models = QPushButton("↻ Картинки")
        self.btn_refresh_aitunnel_image_models.setObjectName("utility_btn")
        self.btn_refresh_aitunnel_image_models.setToolTip("Обновить список моделей генерации изображений AITUNNEL")
        self.btn_refresh_aitunnel_image_models.clicked.connect(self._refresh_aitunnel_image_models)
        image_model_col = QVBoxLayout()
        image_model_col.setSpacing(5)
        image_model_col.addWidget(make_section_label("Модель AITUNNEL для генерации картинок / иконок"))
        image_model_col.addWidget(self.aitunnel_image_model_combo)
        aitunnel_image_model_row.addLayout(image_model_col, 1)
        aitunnel_image_model_row.addWidget(self.btn_refresh_aitunnel_image_models, 0, Qt.AlignBottom)
        self.aitunnel_image_settings_widget = QWidget()
        aitunnel_image_settings_layout = QVBoxLayout(self.aitunnel_image_settings_widget)
        aitunnel_image_settings_layout.setContentsMargins(0, 0, 0, 0)
        aitunnel_image_settings_layout.setSpacing(8)
        image_hint = QLabel("Иконки всегда генерируются через AITUNNEL, независимо от провайдера чата.")
        image_hint.setProperty("role", "hint")
        image_hint.setWordWrap(True)
        aitunnel_image_settings_layout.addWidget(image_hint)
        aitunnel_image_settings_layout.addLayout(aitunnel_image_model_row)
        api_layout.addWidget(self.aitunnel_image_settings_widget)

        self.zai_key_input = _prepare_api_field(QLineEdit(resolve_zai_api_key()))
        self.zai_key_input.setPlaceholderText("Z.AI API key (подписка z.ai)")
        self.zai_key_input.setEchoMode(QLineEdit.Password)
        self.zai_key_input.editingFinished.connect(self._save_env_to_file)

        self.zai_model_combo = QComboBox()
        self.zai_model_combo.setMinimumHeight(UI_FIELD_MIN_HEIGHT)
        self.zai_model_combo.currentIndexChanged.connect(self._save_env_to_file)
        self.btn_refresh_zai_models = QPushButton("↻ Модели")
        self.btn_refresh_zai_models.setObjectName("utility_btn")
        self.btn_refresh_zai_models.setToolTip("Обновить список GLM моделей Z.AI от дешёвых к дорогим")
        self.btn_refresh_zai_models.clicked.connect(self._refresh_zai_models)
        zai_model_row = QHBoxLayout()
        zai_model_row.setSpacing(12)
        zai_model_col = QVBoxLayout()
        zai_model_col.setSpacing(5)
        zai_model_col.addWidget(make_section_label("Модель Z.AI GLM (дешёвые → дорогие)"))
        zai_model_col.addWidget(self.zai_model_combo)
        zai_model_row.addLayout(zai_model_col, 1)
        zai_model_row.addWidget(self.btn_refresh_zai_models, 0, Qt.AlignBottom)

        self.zai_settings_widget = QWidget()
        zai_settings_layout = QVBoxLayout(self.zai_settings_widget)
        zai_settings_layout.setContentsMargins(0, 0, 0, 0)
        zai_settings_layout.setSpacing(8)
        zai_settings_layout.addWidget(make_section_label("Z.AI API key"))
        zai_settings_layout.addWidget(self.zai_key_input)
        zai_settings_layout.addLayout(zai_model_row)
        api_layout.addWidget(self.zai_settings_widget)

        self._populate_aitunnel_models(select_model=resolve_ai_model(AI_PROVIDER_AITUNNEL))
        self._populate_aitunnel_image_models(select_model=resolve_aitunnel_image_model())
        self._populate_zai_models(select_model=resolve_ai_model(AI_PROVIDER_ZAI))
        self._sync_ai_provider_ui()

        concurrency_separator = QFrame()
        concurrency_separator.setFrameShape(QFrame.HLine)
        concurrency_separator.setStyleSheet("QFrame { color: #2a2a45; margin: 4px 0; }")
        api_layout.addWidget(concurrency_separator)
        api_layout.addWidget(make_section_label("Параллельные потоки"))
        concurrency_hint = QLabel(
            "Сколько задач запускать одновременно: перевод AI, upload переводов в App Store, "
            "загрузка скриншотов / PPO."
        )
        concurrency_hint.setProperty("role", "hint")
        concurrency_hint.setWordWrap(True)
        api_layout.addWidget(concurrency_hint)

        def _make_workers_spin(value):
            spin = QSpinBox()
            spin.setRange(CONCURRENCY_MIN, CONCURRENCY_MAX)
            spin.setValue(_clamp_workers(value))
            spin.setMinimumHeight(UI_FIELD_MIN_HEIGHT)
            spin.setMinimumWidth(90)
            spin.valueChanged.connect(self._save_env_to_file)
            return spin

        self.translation_workers_spin = _make_workers_spin(resolve_translation_max_workers())
        self.localization_upload_workers_spin = _make_workers_spin(resolve_localization_upload_max_workers())
        self.screenshot_upload_workers_spin = _make_workers_spin(resolve_screenshot_upload_max_workers())
        concurrency_row = QHBoxLayout()
        concurrency_row.setSpacing(12)
        for label_text, spin in (
            ("Перевод AI", self.translation_workers_spin),
            ("Upload переводов", self.localization_upload_workers_spin),
            ("Скрины / PPO", self.screenshot_upload_workers_spin),
        ):
            col = QVBoxLayout()
            col.setSpacing(5)
            col.addWidget(make_section_label(label_text))
            col.addWidget(spin)
            concurrency_row.addLayout(col, 1)
        api_layout.addLayout(concurrency_row)

        jira_separator = QFrame()
        jira_separator.setFrameShape(QFrame.HLine)
        jira_separator.setStyleSheet("QFrame { color: #2a2a45; margin: 4px 0; }")
        api_layout.addWidget(jira_separator)

        api_layout.addWidget(make_section_label("GitLab (авто ТЗ / brief)"))
        gitlab_hint = QLabel(
            "По Name приложения ищет репо и берёт description.value из "
            f"{GITLAB_APPLE_CONNECT_PATH}. Token: read_api / read_repository."
        )
        gitlab_hint.setProperty("role", "hint")
        gitlab_hint.setWordWrap(True)
        api_layout.addWidget(gitlab_hint)
        self.gitlab_url_input = _prepare_api_field(QLineEdit(resolve_gitlab_base_url()))
        self.gitlab_url_input.setPlaceholderText("https://gitlab.com или ваш self-hosted URL")
        self.gitlab_url_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.gitlab_url_input)
        self.gitlab_token_input = _prepare_api_field(QLineEdit(resolve_gitlab_token()))
        self.gitlab_token_input.setPlaceholderText("GitLab Personal Access Token")
        self.gitlab_token_input.setEchoMode(QLineEdit.Password)
        self.gitlab_token_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.gitlab_token_input)

        jira_label = make_section_label("Jira Cloud (экспорт метаданных)")
        api_layout.addWidget(jira_label)
        self.jira_base_url_input = _prepare_api_field(QLineEdit(os.getenv("JIRA_BASE_URL", "")))
        self.jira_base_url_input.setPlaceholderText("https://your-company.atlassian.net")
        self.jira_base_url_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.jira_base_url_input)
        self.jira_email_input = _prepare_api_field(QLineEdit(os.getenv("JIRA_EMAIL", "")))
        self.jira_email_input.setPlaceholderText("your@email.com")
        self.jira_email_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.jira_email_input)
        self.jira_api_token_input = _prepare_api_field(QLineEdit(os.getenv("JIRA_API_TOKEN", "")))
        self.jira_api_token_input.setPlaceholderText("Jira API Token (Account → Security → API tokens)")
        self.jira_api_token_input.setEchoMode(QLineEdit.Password)
        self.jira_api_token_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.jira_api_token_input)
        jira_project_row = QHBoxLayout()
        self.jira_project_key_input = _prepare_api_field(QLineEdit(os.getenv("JIRA_PROJECT_KEY", "")))
        self.jira_project_key_input.setPlaceholderText("APP")
        self.jira_project_key_input.setMaximumWidth(120)
        self.jira_project_key_input.editingFinished.connect(self._save_env_to_file)
        self.jira_issue_type_input = _prepare_api_field(QLineEdit(os.getenv("JIRA_ISSUE_TYPE", "Story")))
        self.jira_issue_type_input.setPlaceholderText("Story")
        self.jira_issue_type_input.setMaximumWidth(100)
        self.jira_issue_type_input.editingFinished.connect(self._save_env_to_file)
        jira_project_row.addWidget(QLabel("Project:"))
        jira_project_row.addWidget(self.jira_project_key_input)
        jira_project_row.addWidget(QLabel("Type:"))
        jira_project_row.addWidget(self.jira_issue_type_input)
        jira_project_row.addStretch()
        api_layout.addLayout(jira_project_row)
        api_layout.addStretch(1)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.tabs = SidebarTabWidget()
        left_layout.addWidget(self.tabs)

        # --- Topbar: полоса сводки между sidebar и контентом ---
        self.topbar = QFrame()
        self.topbar.setObjectName("topbar")
        topbar_layout = QHBoxLayout(self.topbar)
        topbar_layout.setContentsMargins(18, 10, 18, 10)
        topbar_layout.setSpacing(10)
        self.topbar_badge = QLabel("⚪")
        self.topbar_badge.setObjectName("topbar_badge")
        self.topbar_summary_label = QLabel("")
        self.topbar_summary_label.setObjectName("topbar_summary")
        self.topbar_summary_label.setProperty("role", "section")
        topbar_layout.addWidget(self.topbar_badge)
        topbar_layout.addWidget(self.topbar_summary_label, stretch=1)
        self.tabs.insert_topbar(self.topbar)

        self.main_splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_panel.setObjectName("execution_panel")
        right_panel.setMinimumWidth(380)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)
        right_layout.addWidget(make_page_header(
            "Статус выполнения",
            "Прогресс задач и журнал операций в реальном времени."
        ))

        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.time_label = QLabel("Осталось: --:--")
        self.time_label.setFixedWidth(150)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.time_label)
        right_layout.addLayout(progress_layout)

        self.log_area = QTextEdit()
        self.log_area.setObjectName("log_area")
        self.log_area.setReadOnly(True)
        self.log_area.setMinimumHeight(UI_LOG_MIN_HEIGHT)
        right_layout.addWidget(self.log_area, stretch=1)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setStretchFactor(0, 7)
        self.main_splitter.setStretchFactor(1, 3)
        left_width = int(self._initial_width * 0.68)
        right_width = max(380, self._initial_width - left_width)
        self.main_splitter.setSizes([left_width, right_width])
        root_layout.addWidget(self.main_splitter, stretch=1)

        self.tab_create = QWidget()
        create_outer = QVBoxLayout(self.tab_create)
        create_outer.setContentsMargins(0, 0, 0, 0)
        create_scroll = QScrollArea()
        create_scroll.setWidgetResizable(True)
        create_scroll.setFrameShape(QFrame.NoFrame)
        create_inner = QWidget()
        create_layout = QVBoxLayout(create_inner)
        create_layout.setSpacing(12)
        create_layout.setContentsMargins(4, 4, 12, 12)
        create_scroll.setWidget(create_inner)
        create_outer.addWidget(create_scroll)

        create_layout.addWidget(make_page_header(
            "Новый PPO-тест",
            "Создайте эксперимент в App Store Connect и подготовьте папки скриншотов для вариантов."
        ))
        self.create_name_input = QLineEdit()
        self.create_name_input.setPlaceholderText("Имя нового теста (например: New Icon Test)")
        self.create_traffic_input = QLineEdit("75")
        self.create_traffic_input.setPlaceholderText("Трафик (например: 75)")
        self.create_traffic_input.setMaximumWidth(140)
        create_fields_row = QHBoxLayout()
        name_col = QVBoxLayout()
        name_col.addWidget(QLabel("Имя теста"))
        name_col.addWidget(self.create_name_input)
        traffic_col = QVBoxLayout()
        traffic_col.addWidget(QLabel("Процент трафика"))
        traffic_col.addWidget(self.create_traffic_input)
        create_fields_row.addLayout(name_col, stretch=3)
        create_fields_row.addLayout(traffic_col, stretch=1)
        create_layout.addLayout(create_fields_row)
        self.create_variants_group = self._build_variant_cards_group("Скриншоты вариантов для нового теста")
        create_layout.addWidget(self.create_variants_group)
        create_layout.addStretch()
        self.tabs.addTab(self.tab_create, "Новый тест", "✚")

        self.tab_update = QWidget()
        update_outer = QVBoxLayout(self.tab_update)
        update_outer.setContentsMargins(0, 0, 0, 0)
        update_scroll = QScrollArea()
        update_scroll.setWidgetResizable(True)
        update_scroll.setFrameShape(QFrame.NoFrame)
        update_inner = QWidget()
        update_layout = QVBoxLayout(update_inner)
        update_layout.setSpacing(12)
        update_layout.setContentsMargins(4, 4, 12, 12)
        update_scroll.setWidget(update_inner)
        update_outer.addWidget(update_scroll)
        
        update_layout.addWidget(make_page_header(
            "Обновление PPO-теста",
            "Выберите существующий тест, варианты, папки и локали для точечного обновления."
        ))
        update_layout.addWidget(QLabel("1. Выберите существующий тест из App Store Connect:"))
        
        test_select_layout = QHBoxLayout()
        self.update_name_combo = QComboBox()
        self.btn_fetch_tests = QPushButton("🔄 Загрузить список")
        self.btn_fetch_tests.setObjectName("utility_btn")
        self.btn_fetch_tests.clicked.connect(self._fetch_tests)
        
        test_select_layout.addWidget(self.update_name_combo, stretch=1)
        test_select_layout.addWidget(self.btn_fetch_tests)
        update_layout.addLayout(test_select_layout)

        update_layout.addWidget(QLabel("2. Какие варианты обновить?"))
        var_layout = QHBoxLayout()
        self.chk_var_a = QCheckBox("Variant A (или Treatment A)")
        self.chk_var_b = QCheckBox("Variant B (или Treatment B)")
        self.chk_var_c = QCheckBox("Variant C (или Treatment C)")
        self.chk_var_a.setChecked(True)
        self.chk_var_b.setChecked(True)
        self.chk_var_c.setChecked(True)
        
        self.chk_var_a.setProperty("variant_id", "Variant A")
        self.chk_var_b.setProperty("variant_id", "Variant B")
        self.chk_var_c.setProperty("variant_id", "Variant C")
        
        var_layout.addWidget(self.chk_var_a)
        var_layout.addWidget(self.chk_var_b)
        var_layout.addWidget(self.chk_var_c)
        update_layout.addLayout(var_layout)
        self.update_variants_group = self._build_variant_cards_group("Папки скриншотов для update-режима")
        update_layout.addWidget(self.update_variants_group)

        update_layout.addWidget(QLabel("3. Выберите локали для обновления:"))
        self.update_locale_picker = LocalePickerWidget(LOCALE_MAP, "Локали обновления")
        update_layout.addWidget(self.update_locale_picker)
        
        self.tabs.addTab(self.tab_update, "Обновить тест", "↻")

        self.tab_metadata = QWidget()
        metadata_layout = QVBoxLayout(self.tab_metadata)
        metadata_layout.setContentsMargins(4, 4, 4, 4)
        metadata_layout.addWidget(make_page_header(
            "Метаданные приложения",
            "Разделы на вкладках — меньше скролла, фокус на одной задаче. "
            "JSON для custom_requests — вкладка «Дополнительно»."
        ))

        self.btn_load_metadata_json = QPushButton("📄 Импорт JSON в поля")
        self.btn_load_metadata_json.setObjectName("utility_btn")
        self.btn_load_metadata_json.clicked.connect(self._load_metadata_json)
        self.btn_load_metadata_json.setVisible(False)
        self.btn_insert_metadata_example = QPushButton("🧩 Заполнить пример")
        self.btn_insert_metadata_example.setObjectName("utility_btn")
        self.btn_insert_metadata_example.clicked.connect(self._insert_metadata_example)
        self.btn_insert_metadata_example.setVisible(False)
        self.btn_preview_metadata = QPushButton("🔍 Preview & Validate")
        self.btn_preview_metadata.setObjectName("utility_btn")
        self.btn_preview_metadata.clicked.connect(self._preview_metadata)
        self.btn_preview_metadata.setVisible(False)

        metadata_toolbar = QFrame()
        metadata_toolbar.setObjectName("metadata_toolbar")
        tb_outer = QVBoxLayout(metadata_toolbar)
        tb_outer.setContentsMargins(10, 10, 10, 10)
        tb_outer.setSpacing(0)

        tb_row1 = QFrame()
        tb_row1.setObjectName("metadata_toolbar_row1")
        tb_row1_layout = QHBoxLayout(tb_row1)
        tb_row1_layout.setContentsMargins(0, 0, 0, 0)
        tb_row1_layout.setSpacing(10)

        locale_toolbar_col = QVBoxLayout()
        locale_toolbar_col.setSpacing(4)
        locale_toolbar_label = QLabel("Локаль")
        locale_toolbar_label.setProperty("role", "label")
        self.meta_locale_combo = QComboBox()
        self.meta_locale_combo.setMinimumWidth(180)
        self.meta_locale_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for code, (_, name) in sorted_locales:
            self.meta_locale_combo.addItem(f"{name} ({code})", code)
        default_locale_index = self.meta_locale_combo.findData("en-US")
        if default_locale_index >= 0:
            self.meta_locale_combo.setCurrentIndex(default_locale_index)
        locale_toolbar_col.addWidget(locale_toolbar_label)
        locale_toolbar_col.addWidget(self.meta_locale_combo)
        tb_row1_layout.addLayout(locale_toolbar_col, stretch=2)

        version_toolbar_col = QVBoxLayout()
        version_toolbar_col.setSpacing(4)
        version_toolbar_label = QLabel("Версия")
        version_toolbar_label.setProperty("role", "label")
        self.meta_app_version_input = QLineEdit("")
        self.meta_app_version_input.setPlaceholderText("из ASC")
        self.meta_app_version_input.setReadOnly(True)
        self.meta_app_version_input.setMaximumWidth(100)
        version_toolbar_col.addWidget(version_toolbar_label)
        version_toolbar_col.addWidget(self.meta_app_version_input)
        tb_row1_layout.addLayout(version_toolbar_col)

        tb_row1_layout.addStretch(1)

        self.btn_pull_metadata = QPushButton("⬇️ Pull")
        self.btn_pull_metadata.setObjectName("utility_btn")
        self.btn_pull_metadata.setMinimumHeight(34)
        self.btn_pull_metadata.clicked.connect(self._pull_metadata_from_apple)
        tb_row1_layout.addWidget(self.btn_pull_metadata, alignment=Qt.AlignBottom)

        tb_row2 = QFrame()
        tb_row2.setObjectName("metadata_toolbar_row2")
        tb_row2_layout = QHBoxLayout(tb_row2)
        tb_row2_layout.setContentsMargins(0, 8, 0, 0)
        tb_row2_layout.setSpacing(10)

        self.meta_source_locale_label = QLabel("Локаль: —")
        self.meta_source_locale_label.setProperty("role", "hint")
        self.meta_source_version_label = QLabel("Версия: —")
        self.meta_source_version_label.setProperty("role", "hint")
        tb_row2_layout.addWidget(self.meta_source_locale_label)
        tb_row2_layout.addWidget(self.meta_source_version_label)
        tb_row2_layout.addStretch(1)

        jira_toolbar_col = QVBoxLayout()
        jira_toolbar_col.setSpacing(4)
        jira_toolbar_label = QLabel("Jira")
        jira_toolbar_label.setProperty("role", "label")
        self.jira_issue_key_input = QLineEdit(os.getenv("JIRA_ISSUE_KEY", ""))
        self.jira_issue_key_input.setPlaceholderText("T7-1506")
        self.jira_issue_key_input.setMaximumWidth(100)
        self.jira_issue_key_input.editingFinished.connect(self._save_env_to_file)
        jira_toolbar_col.addWidget(jira_toolbar_label)
        jira_toolbar_col.addWidget(self.jira_issue_key_input)
        tb_row2_layout.addLayout(jira_toolbar_col)

        self.btn_create_jira = QPushButton("Обновить Jira")
        self.btn_create_jira.setObjectName("start_btn")
        self.btn_create_jira.setMinimumHeight(34)
        self.btn_create_jira.setToolTip("Записывает метаданные в существующую Jira карточку")
        self.btn_create_jira.clicked.connect(self._update_jira_issue)
        tb_row2_layout.addWidget(self.btn_create_jira, alignment=Qt.AlignBottom)

        tb_outer.addWidget(tb_row1)
        tb_outer.addWidget(tb_row2)
        metadata_layout.addWidget(metadata_toolbar)

        self.metadata_inner_tabs = QTabWidget()
        self.metadata_inner_tabs.setObjectName("metadata_inner_tabs")
        self.metadata_inner_tabs.setDocumentMode(True)

        def _metadata_tab_page(form_widget):
            scroll = QScrollArea()
            scroll.setObjectName("metadata_tab_scroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(form_widget)
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(scroll)
            return page

        content_widget = QWidget()
        content_widget.setObjectName("metadata_flat_panel")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(8, 10, 12, 12)

        content_layout.addWidget(make_section_label("Version metadata"))

        self.meta_description_input = QTextEdit()
        self.meta_description_input.setFixedHeight(132)
        self.meta_description_input.setPlaceholderText("Description / описание приложения")
        self.meta_keywords_input = QLineEdit()
        self.meta_keywords_input.setPlaceholderText("keyword1,keyword2,keyword3")
        self.meta_promotional_text_input = QTextEdit()
        self.meta_promotional_text_input.setFixedHeight(96)
        self.meta_promotional_text_input.setPlaceholderText("Promotional text")
        self.meta_whats_new_input = QTextEdit()
        self.meta_whats_new_input.setFixedHeight(104)
        self.meta_whats_new_input.setPlaceholderText(
            "Только для обновлений (1.0.1+). Первая версия — оставьте пустым."
        )
        self.meta_include_whats_new_checkbox = QCheckBox("Отправлять What's New в Apple")
        self.meta_include_whats_new_checkbox.setChecked(False)
        self.meta_include_whats_new_checkbox.setToolTip(
            "Для первого релиза (1.0.0) в App Store Connect нет What's New — не включайте."
        )
        self.meta_support_url_input = QLineEdit()
        self.meta_support_url_input.setPlaceholderText("https://example.com/support")
        self.meta_marketing_url_input = QLineEdit()
        self.meta_marketing_url_input.setPlaceholderText("https://example.com")
        self._syncing_marketing_privacy_urls = False
        self.meta_marketing_url_input.textChanged.connect(
            lambda _=None: self._sync_marketing_privacy_urls("marketing")
        )
        if not hasattr(self, "ai_field_buttons"):
            self.ai_field_buttons = []
        self._add_metadata_stacked_field(content_layout, "Description", self.meta_description_input, "description")
        self._add_metadata_pair_row(
            content_layout,
            "Keywords", self.meta_keywords_input,
            "Promotional text", self.meta_promotional_text_input,
            left_ai="keywords", right_ai="promotional_text",
        )
        self._add_metadata_stacked_field(content_layout, "What's New", self.meta_whats_new_input, "whats_new")
        content_layout.addWidget(self.meta_include_whats_new_checkbox)
        self._add_metadata_pair_row(
            content_layout,
            "Support URL", self.meta_support_url_input,
            "Marketing URL", self.meta_marketing_url_input,
        )
        content_layout.addStretch(1)
        self.metadata_inner_tabs.addTab(_metadata_tab_page(content_widget), "Контент")

        app_info_widget = QWidget()
        app_info_widget.setObjectName("metadata_flat_panel")
        app_info_layout = QVBoxLayout(app_info_widget)
        app_info_layout.setSpacing(10)
        app_info_layout.setContentsMargins(8, 10, 12, 12)
        app_info_layout.addWidget(make_section_label("App info"))
        self.meta_name_input = QLineEdit()
        self.meta_name_input.setPlaceholderText("App name")
        self.meta_name_input.editingFinished.connect(
            lambda: self._apply_account_roster_for_current_app(silent=True)
        )
        self.meta_subtitle_input = QLineEdit()
        self.meta_subtitle_input.setPlaceholderText("Subtitle")
        self.meta_copyright_input = QLineEdit()
        self.meta_copyright_input.setPlaceholderText("© Your Developer Name")
        self.meta_privacy_policy_url_input = QLineEdit()
        self.meta_privacy_policy_url_input.setPlaceholderText("https://example.com/privacy")
        self.meta_privacy_policy_url_input.textChanged.connect(
            lambda _=None: self._sync_marketing_privacy_urls("privacy")
        )
        self.meta_privacy_choices_url_input = QLineEdit()
        self.meta_privacy_choices_url_input.setPlaceholderText("https://example.com/privacy-choices")
        self.meta_primary_category_input = QComboBox()
        self.meta_secondary_category_input = QComboBox()
        for category_id, category_name in APP_CATEGORY_OPTIONS:
            label = category_name if not category_id else f"{category_name} ({category_id})"
            self.meta_primary_category_input.addItem(label, category_id)
            self.meta_secondary_category_input.addItem(label, category_id)
        self.meta_kids_age_band_input = QLineEdit("FIVE_AND_UNDER")
        self.meta_kids_age_band_input.setPlaceholderText("По умолчанию 4+ / без ограничений")
        self.meta_availability_mode_input = QComboBox()
        self.meta_availability_mode_input.addItem("Все страны (Apple default)", "ALL")
        self.meta_availability_mode_input.addItem("Страны на выбор", "SELECTED")
        self.meta_availability_mode_input.currentIndexChanged.connect(self._on_availability_mode_changed)
        self.meta_collects_data_checkbox = QCheckBox("Собираем данные (App Privacy)")
        self.meta_collects_data_checkbox.setChecked(False)
        self.meta_selected_territories = []
        self.meta_select_territories_btn = QPushButton("Выбрать страны…")
        self.meta_select_territories_btn.clicked.connect(self._open_territories_selector)
        self.meta_select_territories_btn.setVisible(False)
        self._add_metadata_pair_row(
            app_info_layout,
            "Name", self.meta_name_input,
            "Subtitle", self.meta_subtitle_input,
            right_ai="subtitle",
        )
        self._add_metadata_stacked_field(app_info_layout, "Copyright", self.meta_copyright_input)
        self._add_metadata_pair_row(
            app_info_layout,
            "Privacy Policy URL", self.meta_privacy_policy_url_input,
            "Privacy Choices URL", self.meta_privacy_choices_url_input,
        )
        self._add_metadata_pair_row(
            app_info_layout,
            "Primary Category", self.meta_primary_category_input,
            "Secondary Category", self.meta_secondary_category_input,
            left_ai="category", right_ai="category",
        )
        self._add_metadata_pair_row(
            app_info_layout,
            "Age rating default", self.meta_kids_age_band_input,
            "App availability", self.meta_availability_mode_input,
        )
        app_info_layout.addWidget(self.meta_select_territories_btn)
        app_info_layout.addWidget(self.meta_collects_data_checkbox)
        app_info_layout.addStretch(1)
        self.metadata_inner_tabs.addTab(_metadata_tab_page(app_info_widget), "App Info")

        review_widget = QWidget()
        review_widget.setObjectName("metadata_flat_panel")
        review_layout = QVBoxLayout(review_widget)
        review_layout.setSpacing(10)
        review_layout.setContentsMargins(8, 10, 12, 12)
        review_layout.addWidget(make_section_label("App Review notes"))
        self.meta_review_first_name_input = QLineEdit()
        self.meta_review_last_name_input = QLineEdit()
        self.meta_review_phone_input = QLineEdit()
        self.meta_review_phone_input.setPlaceholderText("+1 555 010 0611")
        self.meta_review_email_input = QLineEdit()
        self.meta_review_notes_input = QTextEdit()
        self.meta_review_notes_input.setFixedHeight(112)
        self._add_metadata_pair_row(
            review_layout,
            "Contact first name", self.meta_review_first_name_input,
            "Contact last name", self.meta_review_last_name_input,
        )
        self._add_metadata_pair_row(
            review_layout,
            "Contact phone", self.meta_review_phone_input,
            "Contact email", self.meta_review_email_input,
        )
        self._add_metadata_stacked_field(review_layout, "Notes", self.meta_review_notes_input, "review_notes")
        review_layout.addStretch(1)
        self.metadata_inner_tabs.addTab(_metadata_tab_page(review_widget), "Review")

        ai_widget = QWidget()
        ai_widget.setObjectName("metadata_flat_panel")
        ai_tab_layout = QVBoxLayout(ai_widget)
        ai_tab_layout.setSpacing(10)
        ai_tab_layout.setContentsMargins(8, 10, 12, 12)
        ai_tab_layout.addWidget(make_section_label("AI генерация"))
        ai_settings_hint = QLabel("Провайдер, API key и модель — на вкладке «Настройки API» (AITUNNEL или Z.AI GLM).")
        ai_settings_hint.setProperty("role", "hint")
        ai_tab_layout.addWidget(ai_settings_hint)
        self.ai_developer_name_input = QLineEdit("")
        self.ai_developer_name_input.setPlaceholderText("Developer name для App Review notes")
        self.ai_developer_name_input.textChanged.connect(self._apply_developer_name_to_review_fields)
        self.ai_app_context_input = QTextEdit()
        self.ai_app_context_input.setFixedHeight(132)
        self.ai_app_context_input.setPlaceholderText("Вставьте ТЗ: смысл приложения, функционал, аудитория, ограничения, что точно есть/нет...")
        self.ai_prompt_profile_combo = QComboBox()
        self.ai_prompt_profiles = self._load_prompt_profiles()
        self.ai_prompt_profile_combo.addItems(self.ai_prompt_profiles.keys())
        self.ai_prompt_input = QTextEdit()
        self.ai_prompt_input.setFixedHeight(220)
        self.ai_prompt_input.setPlaceholderText("Вставьте свой промт для AI: стиль description, правила keywords, category, notes...")
        self.ai_prompt_input.setPlainText(self.ai_prompt_profiles.get("Human premium ASO", DEFAULT_GEMINI_PROMPT))
        self._add_metadata_stacked_field(ai_tab_layout, "Developer name", self.ai_developer_name_input)
        self._add_metadata_stacked_field(ai_tab_layout, "ТЗ / brief приложения", self.ai_app_context_input)
        gitlab_brief_row = QHBoxLayout()
        self.btn_fetch_gitlab_brief = QPushButton("📥 ТЗ из GitLab")
        self.btn_fetch_gitlab_brief.setObjectName("utility_btn")
        self.btn_fetch_gitlab_brief.setToolTip(
            f"Найти репо по Name и подставить description.value из {GITLAB_APPLE_CONNECT_PATH}"
        )
        self.btn_fetch_gitlab_brief.clicked.connect(
            lambda: self._fetch_gitlab_brief(force=True, only_if_empty=False)
        )
        self.gitlab_brief_status = QLabel("")
        self.gitlab_brief_status.setProperty("role", "hint")
        self.gitlab_brief_status.setWordWrap(True)
        gitlab_brief_row.addWidget(self.btn_fetch_gitlab_brief)
        gitlab_brief_row.addWidget(self.gitlab_brief_status, stretch=1)
        ai_tab_layout.addLayout(gitlab_brief_row)
        storepal_ai_row = QHBoxLayout()
        self.btn_privacy_support_oneshot = QPushButton("🚀 Privacy + Support → Apple")
        self.btn_privacy_support_oneshot.setObjectName("utility_btn")
        self.btn_privacy_support_oneshot.setToolTip(
            "Сначала StorePal (долгие URL). Если лимит/план — случайно Formspree или Web3Forms "
            "50/50 на BrewPage (~30 дней) → URL в App Info → Apple."
        )
        self.btn_privacy_support_oneshot.clicked.connect(self._run_privacy_support_oneshot)
        self.btn_storepal_generate_pages = QPushButton("StorePal: Privacy + Support (основной)")
        self.btn_storepal_generate_pages.setObjectName("utility_btn")
        self.btn_storepal_generate_pages.setToolTip(
            "Основной вариант: Privacy + Support через StorePal API"
        )
        self.btn_storepal_generate_pages.clicked.connect(self._open_storepal_pages_dialog)
        self.btn_alt_privacy_support = QPushButton("Local Privacy + Formspree (резерв)")
        self.btn_alt_privacy_support.setObjectName("utility_btn")
        self.btn_alt_privacy_support.setToolTip(
            "Резерв 1: Privacy/Support HTML на BrewPage + форма Formspree"
        )
        self.btn_alt_privacy_support.clicked.connect(self._open_local_privacy_formspree_dialog)
        self.btn_alt2_privacy_support = QPushButton("Local Privacy + Web3Forms (резерв 2)")
        self.btn_alt2_privacy_support.setObjectName("utility_btn")
        self.btn_alt2_privacy_support.setToolTip(
            "Резерв 2: Web3Forms + BrewPage (~30 дней), другой вид страниц"
        )
        self.btn_alt2_privacy_support.clicked.connect(self._open_local_privacy_web3forms_dialog)
        storepal_ai_row.addWidget(self.btn_privacy_support_oneshot)
        storepal_ai_row.addWidget(self.btn_storepal_generate_pages)
        storepal_ai_row.addWidget(self.btn_alt_privacy_support)
        storepal_ai_row.addWidget(self.btn_alt2_privacy_support)
        storepal_ai_row.addStretch(1)
        ai_tab_layout.addLayout(storepal_ai_row)
        self._add_metadata_stacked_field(ai_tab_layout, "Prompt profile", self.ai_prompt_profile_combo)
        self._add_metadata_stacked_field(ai_tab_layout, "Промт генерации", self.ai_prompt_input)
        profile_buttons_layout = QHBoxLayout()
        self.btn_apply_prompt_profile = QPushButton("📌 Применить профиль")
        self.btn_apply_prompt_profile.setObjectName("utility_btn")
        self.btn_apply_prompt_profile.clicked.connect(self._apply_prompt_profile)
        self.btn_save_prompt_profile = QPushButton("💾 Сохранить профиль")
        self.btn_save_prompt_profile.setObjectName("utility_btn")
        self.btn_save_prompt_profile.clicked.connect(self._save_current_prompt_profile)
        self.btn_reset_ai_prompt = QPushButton("🧩 Вставить стандартный ASO промт")
        self.btn_reset_ai_prompt.setObjectName("utility_btn")
        self.btn_reset_ai_prompt.clicked.connect(lambda: self.ai_prompt_input.setPlainText(DEFAULT_GEMINI_PROMPT))
        profile_buttons_layout.addWidget(self.btn_apply_prompt_profile)
        profile_buttons_layout.addWidget(self.btn_save_prompt_profile)
        profile_buttons_layout.addWidget(self.btn_reset_ai_prompt)
        ai_tab_layout.addLayout(profile_buttons_layout)

        ai_buttons_grid = QGridLayout()
        ai_actions = [
            ("✨ Сгенерировать все", "all"),
        ]
        self.ai_generation_buttons = []
        for index, (label, mode) in enumerate(ai_actions):
            button = QPushButton(label)
            if mode == "all":
                button.setObjectName("main_generate_btn")
                button.setMinimumHeight(50)
            button.clicked.connect(lambda _, m=mode: self._generate_ai_metadata(m))
            self.ai_generation_buttons.append(button)
            ai_buttons_grid.addWidget(button, index, 0)
        ai_buttons_grid.setColumnStretch(0, 1)
        ai_tab_layout.addLayout(ai_buttons_grid)

        ai_tab_layout.addWidget(make_section_label("Fix with AI / quick fixes"))
        fix_grid = QGridLayout()
        fix_actions = [
            ("🧼 Fix banned words", "fix_banned_words"),
            ("➖ Remove dashes", "remove_dashes"),
            ("📏 Shorten to limits", "shorten_to_limits"),
            ("🔡 Normalize keywords", "normalize_keywords"),
        ]
        self.ai_fix_buttons = []
        for index, (label, mode) in enumerate(fix_actions):
            button = QPushButton(label)
            button.clicked.connect(lambda _, m=mode: self._run_fix_action(m))
            self.ai_fix_buttons.append(button)
            fix_grid.addWidget(button, index // 2, index % 2)
        fix_grid.setColumnStretch(0, 1)
        fix_grid.setColumnStretch(1, 1)
        fix_host = QWidget()
        fix_host.setLayout(fix_grid)
        ai_tab_layout.addWidget(fix_host)
        ai_tab_layout.addStretch(1)
        self.metadata_inner_tabs.addTab(_metadata_tab_page(ai_widget), "AI генерация")

        extra_widget = QWidget()
        extra_widget.setObjectName("metadata_flat_panel")
        extra_layout = QVBoxLayout(extra_widget)
        extra_layout.setSpacing(10)
        extra_layout.setContentsMargins(8, 10, 12, 12)
        extra_layout.addWidget(make_section_label("Дополнительно"))
        custom_hint = QLabel("Для app privacy / availability можно вставить массив custom_requests в JSON-формате:")
        custom_hint.setProperty("role", "hint")
        custom_hint.setWordWrap(True)
        extra_layout.addWidget(custom_hint)
        self.metadata_text = QTextEdit()
        self.metadata_text.setFixedHeight(112)
        self.metadata_text.setPlaceholderText('[{"method":"PATCH","endpoint":"...","payload":{...}}]')
        extra_layout.addWidget(self.metadata_text)

        self.chain_screenshots_checkbox = QCheckBox(
            "После метаданных: оптимизация скринов → загрузка (файлы и локали — вкладка «ЗАГРУЗКА СКРИНОВ»)"
        )
        self.chain_screenshots_checkbox.setChecked(False)
        extra_layout.addWidget(self.chain_screenshots_checkbox)
        extra_layout.addStretch(1)
        self.metadata_inner_tabs.addTab(_metadata_tab_page(extra_widget), "Дополнительно")

        metadata_layout.addWidget(self.metadata_inner_tabs, stretch=1)
        self.tabs.addTab(self.tab_metadata, "Метаданные", "📝")

        self.tab_translation = QWidget()
        translation_outer = QVBoxLayout(self.tab_translation)
        translation_outer.setContentsMargins(0, 0, 0, 0)
        translation_scroll = QScrollArea()
        translation_scroll.setWidgetResizable(True)
        translation_scroll.setFrameShape(QFrame.NoFrame)
        translation_inner = QWidget()
        translation_layout = QVBoxLayout(translation_inner)
        translation_layout.setSpacing(12)
        translation_layout.setContentsMargins(4, 4, 12, 12)
        translation_scroll.setWidget(translation_inner)
        translation_outer.addWidget(translation_scroll)

        translation_layout.addWidget(make_page_header(
            "Перевод локализаций",
            "Pull source из Apple, перевод через AITUNNEL или Z.AI GLM, preview/edit и batch upload в App Store Connect."
        ))

        source_group = QGroupBox("Source locale")
        source_form = QFormLayout(source_group)
        self.translation_source_locale_combo = QComboBox()
        for code, (_, name) in sorted_locales:
            self.translation_source_locale_combo.addItem(f"{name} ({code})", code)
        source_default_idx = self.translation_source_locale_combo.findData("en-US")
        if source_default_idx >= 0:
            self.translation_source_locale_combo.setCurrentIndex(source_default_idx)
        source_controls_row = QHBoxLayout()
        self.btn_translation_auto_source = QPushButton("Auto source from Apple")
        self.btn_translation_auto_source.setObjectName("utility_btn")
        self.btn_translation_auto_source.clicked.connect(self._fetch_translation_source_auto)
        source_controls_row.addWidget(self.translation_source_locale_combo, stretch=1)
        source_controls_row.addWidget(self.btn_translation_auto_source)
        source_form.addRow("Исходная локаль:", source_controls_row)
        self.translation_source_info = QLabel("Source metadata еще не загружен.")
        self.translation_source_info.setProperty("role", "hint")
        source_form.addRow("Source status:", self.translation_source_info)
        translation_layout.addWidget(source_group)

        self.translation_target_picker = LocalePickerWidget(LOCALE_MAP, "Целевые локали")
        translation_layout.addWidget(self.translation_target_picker)
        self.btn_translation_refresh_locales = QPushButton("🔄 Взять активные локали из Apple")
        self.btn_translation_refresh_locales.setObjectName("utility_btn")
        self.btn_translation_refresh_locales.setToolTip(
            "Подставит в целевые локали те, что уже есть у версии в App Store Connect"
        )
        self.btn_translation_refresh_locales.clicked.connect(self._refresh_translation_locales)
        translation_layout.addWidget(self.btn_translation_refresh_locales)

        fields_group = QGroupBox("Поля для перевода")
        fields_layout = QHBoxLayout(fields_group)
        self.translation_field_checks = {}
        for field_key, field_label, _limit, _target in TRANSLATION_FIELDS:
            cb = QCheckBox(field_label)
            cb.setChecked(field_key in {"description", "keywords", "promotionalText", "subtitle"})
            self.translation_field_checks[field_key] = cb
            fields_layout.addWidget(cb)
        translation_layout.addWidget(fields_group)

        profile_group = QGroupBox("AI Translation Profile")
        profile_layout = QFormLayout(profile_group)
        self.translation_profile_combo = QComboBox()
        self.translation_profile_combo.addItems(list(TRANSLATION_PROFILES.keys()))
        profile_layout.addRow("Профиль:", self.translation_profile_combo)
        translation_layout.addWidget(profile_group)

        self.translation_auto_upload_checkbox = QCheckBox(
            "После перевода сразу загрузить в App Store"
        )
        self.translation_auto_upload_checkbox.setChecked(True)
        self.translation_auto_upload_checkbox.setToolTip(
            "Если включено, после AI-перевода сразу уйдут uploadable строки в App Store Connect"
        )
        translation_layout.addWidget(self.translation_auto_upload_checkbox)
        self.translation_chain_screenshots_checkbox = QCheckBox(
            "После загрузки переводов → Скриншоты (Загрузки → Apple)"
        )
        self.translation_chain_screenshots_checkbox.setChecked(False)
        self.translation_chain_screenshots_checkbox.setToolTip(
            "После успешного upload переводов: вкладка Скриншоты → активные локали → "
            "скрины из Загрузки → upload в App Store. "
            "Сжатие/EXIF — по галочке на вкладке Скриншоты."
        )
        translation_layout.addWidget(self.translation_chain_screenshots_checkbox)

        translation_actions = QHBoxLayout()
        self.btn_translation_translate = QPushButton("Перевести")
        self.btn_translation_translate.setObjectName("start_btn")
        self.btn_translation_translate.clicked.connect(self._translate_localizations)
        self.btn_translation_validate = QPushButton("Validate")
        self.btn_translation_validate.setObjectName("utility_btn")
        self.btn_translation_validate.clicked.connect(self._validate_translation_table)
        self.btn_translation_upload = QPushButton("Загрузить переводы в Apple")
        self.btn_translation_upload.setObjectName("utility_btn")
        self.btn_translation_upload.clicked.connect(self._upload_translation_rows)
        translation_actions.addWidget(self.btn_translation_translate)
        translation_actions.addWidget(self.btn_translation_validate)
        translation_actions.addWidget(self.btn_translation_upload)
        translation_layout.addLayout(translation_actions)

        self.translation_table = QTableWidget(0, 6)
        self.translation_table.setHorizontalHeaderLabels([
            "Locale", "Field", "Source", "Translation", "Status", "Warning"
        ])
        self.translation_table.horizontalHeader().setStretchLastSection(True)
        self.translation_table.setWordWrap(True)
        self.translation_table.itemChanged.connect(self._on_translation_item_changed)
        translation_layout.addWidget(self.translation_table, stretch=1)
        self.translation_table_busy = False
        self.translation_source_payload = {}
        self.translation_version_id = ""
        self.translation_app_info_id = ""

        self.tabs.addTab(self.tab_translation, "Перевод локализации", "🌐")

        self.tab_screens_upload = QWidget()
        screens_outer = QVBoxLayout(self.tab_screens_upload)
        screens_outer.setContentsMargins(0, 0, 0, 0)
        screens_scroll = QScrollArea()
        screens_scroll.setWidgetResizable(True)
        screens_scroll.setFrameShape(QFrame.NoFrame)
        screens_inner = QWidget()
        screens_upload_layout = QVBoxLayout(screens_inner)
        screens_upload_layout.setSpacing(12)
        screens_upload_layout.setContentsMargins(4, 4, 12, 12)
        screens_scroll.setWidget(screens_inner)
        screens_outer.addWidget(screens_scroll)
        screens_upload_layout.addWidget(make_page_header(
            "Загрузка скриншотов",
            "Источник: скрины из Загрузки («Взять из Загрузки»), drag-drop/папка, "
            "или Figma REST. Потом: ресайз → сжатие/EXIF → App Store. "
            "После успешной загрузки файлы из Загрузки/Desktop удаляются автоматически."
        ))
        self.btn_refresh_locales = QPushButton("🔄 Взять активные локали из Apple")
        self.btn_refresh_locales.setObjectName("utility_btn")
        self.btn_refresh_locales.clicked.connect(self._refresh_screenshot_locales)
        screens_upload_layout.addWidget(self.btn_refresh_locales)
        self.upload_locale_picker = LocalePickerWidget(
            LOCALE_MAP,
            "Локали для скриншотов",
            columns=6,
            collapsible=True,
            start_collapsed=True,
        )
        self.upload_locale_picker.set_compact_height(445)
        screens_upload_layout.addWidget(self.upload_locale_picker)

        figma_group = QGroupBox("Figma")
        figma_layout = QVBoxLayout(figma_group)
        figma_layout.setSpacing(8)
        figma_url_hint = QLabel(
            "Для view-only: Export/сохрани скрины в Загрузки → «Взять из Загрузки». "
            "REST (нужен токен, есть лимиты): ссылка на файл/страницу — скачаются фреймы страницы."
        )
        figma_url_hint.setProperty("role", "hint")
        figma_url_hint.setWordWrap(True)
        figma_layout.addWidget(figma_url_hint)
        self.figma_url_input = QLineEdit()
        self.figma_url_input.setPlaceholderText(
            "https://www.figma.com/design/XXXX/Name?node-id=1-2"
        )
        figma_layout.addWidget(self.figma_url_input)
        figma_btns = QHBoxLayout()
        self.btn_figma_import = QPushButton("⬇️ Скачать из Figma")
        self.btn_figma_import.setObjectName("utility_btn")
        self.btn_figma_import.setToolTip("Импорт фреймов страницы в список файлов ниже (REST API)")
        self.btn_figma_import.clicked.connect(lambda: self._start_figma_import(upload_after=False))
        self.btn_figma_import_upload = QPushButton("Figma → Apple")
        self.btn_figma_import_upload.setObjectName("utility_btn")
        self.btn_figma_import_upload.setToolTip(
            "Скачать все фреймы со страницы (REST), затем ресайз/оптимизация/upload по текущим галочкам и локалям"
        )
        self.btn_figma_import_upload.clicked.connect(lambda: self._start_figma_import(upload_after=True))
        figma_btns.addWidget(self.btn_figma_import)
        figma_btns.addWidget(self.btn_figma_import_upload)
        figma_btns.addStretch(1)
        figma_layout.addLayout(figma_btns)
        screens_upload_layout.addWidget(figma_group)

        upload_files_group = QGroupBox("Файлы и запуск")
        upload_files_layout = QVBoxLayout(upload_files_group)
        upload_files_layout.setSpacing(10)
        self.upload_summary_label = QLabel("Локалей: 0 · Файлов: 0 · Всего upload задач: 0")
        self.upload_summary_label.setProperty("role", "section")
        upload_files_layout.addWidget(self.upload_summary_label)
        self.optimize_images_checkbox = QCheckBox("Сжимать PNG/JPG и убрать EXIF перед загрузкой")
        self.optimize_images_checkbox.setChecked(True)
        self.optimize_images_checkbox.setToolTip(
            "Локальная оптимизация через Pillow (без внешних API). "
            "Если выключено — файлы загружаются как есть."
        )
        upload_files_layout.addWidget(self.optimize_images_checkbox)
        self.resize_screenshots_checkbox = QCheckBox(
            "Резать входящие скрины до 1290×2796 и сохранять в JPG перед оптимизацией"
        )
        self.resize_screenshots_checkbox.toggled.connect(self._on_resize_screenshots_toggled)
        upload_files_layout.addWidget(self.resize_screenshots_checkbox)
        self.iphone_65_direct_checkbox = QCheckBox(
            "2688×1242 (6.5\") — без кропа и конвертации в JPG, сразу оптимизация → APP_IPHONE_65"
        )
        self.iphone_65_direct_checkbox.toggled.connect(self._on_iphone_65_direct_toggled)
        upload_files_layout.addWidget(self.iphone_65_direct_checkbox)
        self.iphone_65_crop_png_checkbox = QCheckBox(
            "1284×2778 (6.5\") — кроп/ресайз → PNG → оптимизация → APP_IPHONE_65"
        )
        self.iphone_65_crop_png_checkbox.toggled.connect(self._on_iphone_65_crop_png_toggled)
        upload_files_layout.addWidget(self.iphone_65_crop_png_checkbox)
        self._set_screenshot_mode_checkboxes("65_crop_png")
        self.screenshot_drop_zone = ScreenshotDropZone()
        self.screenshot_drop_zone.files_dropped.connect(self._set_upload_jpeg_files)
        upload_files_layout.addWidget(self.screenshot_drop_zone)
        files_layout = QHBoxLayout()
        self.btn_pick_desktop_screens = QPushButton("⬇ Взять из Загрузки")
        self.btn_pick_desktop_screens.setObjectName("utility_btn")
        self.btn_pick_desktop_screens.setToolTip(
            "Подхватить Simulator Screenshot / Screenshot из ~/Downloads (новые сверху). "
            "После успешного upload файлы из Загрузки удаляются."
        )
        self.btn_pick_desktop_screens.clicked.connect(self._pick_downloads_screenshots)
        self.btn_select_folder_screens = QPushButton("📂 Взять из папки")
        self.btn_select_folder_screens.setObjectName("utility_btn")
        self.btn_select_folder_screens.setToolTip("Выбрать папку со всеми скринами")
        self.btn_select_folder_screens.clicked.connect(self._select_screenshot_folder)
        self.btn_select_jpegs = QPushButton("📎 Выбрать файлы")
        self.btn_select_jpegs.setObjectName("utility_btn")
        self.btn_select_jpegs.clicked.connect(self._select_jpeg_files)
        self.lbl_selected_jpegs = QLabel("Файлы не выбраны")
        self.lbl_selected_jpegs.setProperty("role", "hint")
        files_layout.addWidget(self.btn_pick_desktop_screens)
        files_layout.addWidget(self.btn_select_folder_screens)
        files_layout.addWidget(self.btn_select_jpegs)
        files_layout.addWidget(self.lbl_selected_jpegs, stretch=1)
        upload_files_layout.addLayout(files_layout)
        self.upload_files_preview = QListWidget()
        self.upload_files_preview.setMaximumHeight(120)
        upload_files_layout.addWidget(self.upload_files_preview)
        self.upload_files_warning = QLabel("")
        self.upload_files_warning.setProperty("role", "hint")
        upload_files_layout.addWidget(self.upload_files_warning)
        self.upload_jpeg_files = []
        for checkbox in self.upload_locale_picker._checkboxes.values():
            checkbox.stateChanged.connect(self._update_upload_summary)
        self._update_upload_summary()
        self._update_upload_files_warning()
        self.btn_execute_upload = QPushButton("⬆️ ЗАГРУЗИТЬ СКРИНШОТЫ В APPLE")
        self.btn_execute_upload.setObjectName("upload_cta_btn")
        self.btn_execute_upload.setMinimumHeight(64)
        self.btn_execute_upload.clicked.connect(self._start_screenshot_upload)
        upload_files_layout.addWidget(self.btn_execute_upload)
        screens_upload_layout.addWidget(upload_files_group)
        screens_upload_layout.addStretch(1)
        self.tabs.addTab(self.tab_screens_upload, "Скриншоты", "📸")

        self.tab_icon = QWidget()
        icon_outer = QVBoxLayout(self.tab_icon)
        icon_outer.setContentsMargins(0, 0, 0, 0)
        icon_scroll = QScrollArea()
        icon_scroll.setWidgetResizable(True)
        icon_scroll.setFrameShape(QFrame.NoFrame)
        icon_inner = QWidget()
        icon_layout = QVBoxLayout(icon_inner)
        icon_layout.setSpacing(12)
        icon_layout.setContentsMargins(4, 4, 12, 12)
        icon_scroll.setWidget(icon_inner)
        icon_outer.addWidget(icon_scroll)
        icon_layout.addWidget(make_page_header(
            "Генерация иконки",
            f"PNG {ICON_IMAGE_PIXELS}×{ICON_IMAGE_PIXELS} через AITUNNEL. "
            f"Автосохранение в {ICONS_OUTPUT_DIR} с именем приложения."
        ))
        icon_hint = QLabel(
            "Модель картинок выбирается отдельно на вкладке «Настройки API» "
            "(AITUNNEL → Модель для генерации картинок). "
            f"Файл: «Название приложения.png» → {ICONS_OUTPUT_DIR}"
        )
        icon_hint.setProperty("role", "hint")
        icon_hint.setWordWrap(True)
        icon_layout.addWidget(icon_hint)

        self.icon_prompt_source_label = QLabel("")
        self.icon_prompt_source_label.setProperty("role", "section")
        self.icon_prompt_source_label.setWordWrap(True)
        icon_layout.addWidget(self.icon_prompt_source_label)

        self.icon_use_description_checkbox = QCheckBox("Добавить Description приложения к промту")
        self.icon_use_description_checkbox.setChecked(True)
        icon_layout.addWidget(self.icon_use_description_checkbox)

        self.icon_extra_prompt_input = QTextEdit()
        self.icon_extra_prompt_input.setFixedHeight(96)
        self.icon_extra_prompt_input.setPlaceholderText(
            "Доп. указания для иконки (стиль, цвета, символика). Необязательно."
        )
        icon_layout.addWidget(make_section_label("Доп. указания для иконки"))
        icon_layout.addWidget(self.icon_extra_prompt_input)

        icon_buttons = QHBoxLayout()
        self.btn_generate_icon = QPushButton("✨ Сгенерировать иконку")
        self.btn_generate_icon.setObjectName("main_generate_btn")
        self.btn_generate_icon.setMinimumHeight(50)
        self.btn_generate_icon.clicked.connect(self._generate_app_icon)
        self.btn_save_icon = QPushButton("💾 Сохранить ещё раз")
        self.btn_save_icon.setObjectName("utility_btn")
        self.btn_save_icon.setEnabled(False)
        self.btn_save_icon.setToolTip(f"Повторно сохранить в {ICONS_OUTPUT_DIR}")
        self.btn_save_icon.clicked.connect(self._save_generated_icon)
        self.btn_open_icons_folder = QPushButton("📂 Открыть Icons")
        self.btn_open_icons_folder.setObjectName("utility_btn")
        self.btn_open_icons_folder.clicked.connect(self._open_icons_folder)
        icon_buttons.addWidget(self.btn_generate_icon, 2)
        icon_buttons.addWidget(self.btn_save_icon, 1)
        icon_buttons.addWidget(self.btn_open_icons_folder, 1)
        icon_layout.addLayout(icon_buttons)

        self.icon_preview_label = QLabel("Превью появится здесь")
        self.icon_preview_label.setAlignment(Qt.AlignCenter)
        self.icon_preview_label.setMinimumHeight(340)
        self.icon_preview_label.setStyleSheet(
            "QLabel { background: #161625; border: 1px dashed #3a3a55; border-radius: 12px; color: #9a9ab0; }"
        )
        icon_layout.addWidget(self.icon_preview_label)
        self.icon_status_label = QLabel(f"Формат результата: PNG {ICON_IMAGE_PIXELS}×{ICON_IMAGE_PIXELS}")
        self.icon_status_label.setProperty("role", "hint")
        icon_layout.addWidget(self.icon_status_label)
        self._generated_icon_png = None
        icon_layout.addStretch(1)
        self.tabs.addTab(self.tab_icon, "Генерация иконки", "🖼")

        self.tab_feedback = QWidget()
        feedback_outer = QVBoxLayout(self.tab_feedback)
        feedback_outer.setContentsMargins(0, 0, 0, 0)
        feedback_scroll = QScrollArea()
        feedback_scroll.setWidgetResizable(True)
        feedback_scroll.setFrameShape(QFrame.NoFrame)
        feedback_inner = QWidget()
        feedback_layout = QVBoxLayout(feedback_inner)
        feedback_layout.setSpacing(12)
        feedback_layout.setContentsMargins(4, 4, 12, 12)
        feedback_scroll.setWidget(feedback_inner)
        feedback_outer.addWidget(feedback_scroll)
        feedback_layout.addWidget(make_page_header(
            "Feedback Inbox (Formspree)",
            "Резервный канал. Заявки с support-формы тянутся через Formspree API. "
            "Основной inbox — в кабинете StorePal. Нужен FORMSPREE_API_KEY (Pro/Business)."
        ))
        feedback_toolbar = QHBoxLayout()
        self.feedback_form_id_input = QLineEdit(resolve_formspree_form_id())
        self.feedback_form_id_input.setPlaceholderText("Formspree form id")
        self.btn_refresh_feedback = QPushButton("🔄 Обновить inbox")
        self.btn_refresh_feedback.setObjectName("utility_btn")
        self.btn_refresh_feedback.clicked.connect(self._refresh_formspree_inbox)
        self.btn_open_formspree_dashboard = QPushButton("Открыть Formspree")
        self.btn_open_formspree_dashboard.setObjectName("utility_btn")
        self.btn_open_formspree_dashboard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://formspree.io/forms"))
        )
        feedback_toolbar.addWidget(self.feedback_form_id_input, stretch=1)
        feedback_toolbar.addWidget(self.btn_refresh_feedback)
        feedback_toolbar.addWidget(self.btn_open_formspree_dashboard)
        feedback_layout.addLayout(feedback_toolbar)
        self.feedback_status_label = QLabel("Нажмите «Обновить inbox», чтобы загрузить submissions.")
        self.feedback_status_label.setProperty("role", "hint")
        self.feedback_status_label.setWordWrap(True)
        feedback_layout.addWidget(self.feedback_status_label)
        self.feedback_table = QTableWidget(0, 3)
        self.feedback_table.setHorizontalHeaderLabels(["Date", "Email", "Message"])
        self.feedback_table.horizontalHeader().setStretchLastSection(True)
        self.feedback_table.setWordWrap(True)
        self.feedback_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.feedback_table.setEditTriggers(QTableWidget.NoEditTriggers)
        feedback_layout.addWidget(self.feedback_table, stretch=1)
        self.tabs.addTab(self.tab_feedback, "Feedback", "💬")

        self.tab_build = QWidget()
        build_outer = QVBoxLayout(self.tab_build)
        build_outer.setContentsMargins(0, 0, 0, 0)
        build_outer.setSpacing(0)
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setFrameShape(QFrame.NoFrame)
        build_inner = QWidget()
        build_layout = QVBoxLayout(build_inner)
        build_layout.setSpacing(12)
        build_layout.setContentsMargins(4, 4, 12, 12)
        build_scroll.setWidget(build_inner)
        build_outer.addWidget(build_scroll)
        build_layout.addWidget(make_page_header(
            "Билд IPA (вместо Codemagic)",
            "На этом Mac: Pushwoosh-скрипт → +1 build → Archive → IPA → заливка в App Store Connect. "
            "После «Загрузить App ID» папка проекта подставляется автоматически по имени из "
            "корня Projects (как GitLab)."
        ))
        if sys.platform != "darwin":
            mac_only = QLabel("⚠ Сборка IPA работает только на macOS с установленным Xcode.")
            mac_only.setProperty("role", "hint")
            mac_only.setWordWrap(True)
            build_layout.addWidget(mac_only)

        self.xcode_projects_root_input = QLineEdit(resolve_xcode_projects_root())
        self.xcode_projects_root_input.setPlaceholderText("Корень папок проектов, напр. ~/Downloads/Projects")
        self.xcode_projects_root_input.editingFinished.connect(self._save_env_to_file)
        self.btn_pick_xcode_projects_root = QPushButton("Корень…")
        self.btn_pick_xcode_projects_root.setObjectName("utility_btn")
        self.btn_pick_xcode_projects_root.setMinimumWidth(100)
        self.btn_pick_xcode_projects_root.clicked.connect(self._pick_xcode_projects_root)
        _add_labeled_fields_row(
            build_layout,
            [("Корень Projects", self.xcode_projects_root_input)],
            trailing_widget=self.btn_pick_xcode_projects_root,
        )

        self.xcode_project_input = QLineEdit(resolve_xcode_project_path())
        self.xcode_project_input.setPlaceholderText("Папка Xcode-проекта (где .xcodeproj) — авто после App ID")
        self.xcode_project_input.editingFinished.connect(self._save_env_to_file)
        self.btn_pick_xcode_project = QPushButton("Выбрать папку")
        self.btn_pick_xcode_project.setObjectName("utility_btn")
        self.btn_pick_xcode_project.setMinimumWidth(140)
        self.btn_pick_xcode_project.clicked.connect(self._pick_xcode_project_dir)
        _add_labeled_fields_row(
            build_layout,
            [("Xcode проект", self.xcode_project_input)],
            trailing_widget=self.btn_pick_xcode_project,
        )

        self.xcode_scheme_input = QLineEdit(resolve_xcode_scheme())
        self.xcode_scheme_input.setPlaceholderText("Scheme (пусто = авто)")
        self.xcode_scheme_input.editingFinished.connect(self._save_env_to_file)
        self.xcode_team_input = QLineEdit(resolve_xcode_team_id())
        self.xcode_team_input.setPlaceholderText("Team ID (пусто = из Bulder-скрипта / Xcode)")
        self.xcode_team_input.editingFinished.connect(self._save_env_to_file)
        _add_labeled_fields_row(
            build_layout,
            [("Scheme", self.xcode_scheme_input), ("Team ID", self.xcode_team_input)],
        )

        self.xcode_run_bulder_checkbox = QCheckBox(
            "Запустить Scripts/bulder_pushwoosh_build_config.py (как в Codemagic)"
        )
        self.xcode_run_bulder_checkbox.setChecked(True)
        self.xcode_bump_build_checkbox = QCheckBox("+1 к build number из App Store Connect (agvtool)")
        self.xcode_bump_build_checkbox.setChecked(True)
        self.xcode_scan_leaks_checkbox = QCheckBox(
            "Проверить утечки перед сборкой (IP, /Users/…, MAC) — только лог, не удаляет"
        )
        self.xcode_scan_leaks_checkbox.setChecked(True)
        build_layout.addWidget(self.xcode_run_bulder_checkbox)
        build_layout.addWidget(self.xcode_bump_build_checkbox)
        build_layout.addWidget(self.xcode_scan_leaks_checkbox)

        build_btns = QHBoxLayout()
        self.btn_xcode_detect = QPushButton("Определить scheme")
        self.btn_xcode_detect.setObjectName("utility_btn")
        self.btn_xcode_detect.clicked.connect(self._detect_xcode_scheme)
        self.btn_xcode_build_only = QPushButton("Собрать IPA")
        self.btn_xcode_build_only.setObjectName("utility_btn")
        self.btn_xcode_build_only.setMinimumHeight(44)
        self.btn_xcode_build_only.clicked.connect(lambda: self._start_xcode_ipa_build(upload=False))
        self.btn_xcode_build_upload = QPushButton("🚀 Собрать и залить в App Store")
        self.btn_xcode_build_upload.setObjectName("main_generate_btn")
        self.btn_xcode_build_upload.setMinimumHeight(50)
        self.btn_xcode_build_upload.clicked.connect(lambda: self._start_xcode_ipa_build(upload=True))
        build_btns.addWidget(self.btn_xcode_detect)
        build_btns.addWidget(self.btn_xcode_build_only, 1)
        build_btns.addWidget(self.btn_xcode_build_upload, 2)
        build_layout.addLayout(build_btns)

        self.xcode_build_status = QLabel(
            "IPA сохранится в <проект>/build/ppo_ipa/. Подпись — automatic (нужен вход в Xcode → Accounts)."
        )
        self.xcode_build_status.setProperty("role", "hint")
        self.xcode_build_status.setWordWrap(True)
        build_layout.addWidget(self.xcode_build_status)
        build_layout.addStretch(1)
        self.tabs.addTab(self.tab_build, "Билд IPA", "📦")

        self.tab_accounts = QWidget()
        accounts_layout = QVBoxLayout(self.tab_accounts)
        accounts_layout.setContentsMargins(4, 4, 4, 4)
        accounts_layout.setSpacing(12)
        accounts_layout.addWidget(make_page_header(
            "База аккаунтов",
            "Импорт CSV/TSV из Google Sheets. По Name приложения подставляет "
            "Review имя / телефон(+) / email (колонка «Логин»)."
        ))
        accounts_toolbar = QHBoxLayout()
        self.btn_import_accounts_roster = QPushButton("📥 Импорт аккаунтов")
        self.btn_import_accounts_roster.setObjectName("utility_btn")
        self.btn_import_accounts_roster.setToolTip(
            "CSV/TSV из Google Sheets: название приложения, имя, телефон, email/логин."
        )
        self.btn_import_accounts_roster.clicked.connect(self._import_accounts_roster_file)
        self.btn_paste_accounts_roster = QPushButton("📋 Вставить таблицу")
        self.btn_paste_accounts_roster.setObjectName("utility_btn")
        self.btn_paste_accounts_roster.setToolTip("Вставь строки из Google Sheets (Cmd+C → сюда)")
        self.btn_paste_accounts_roster.clicked.connect(self._paste_accounts_roster_from_clipboard)
        self.btn_refresh_accounts_roster = QPushButton("🔄 Обновить")
        self.btn_refresh_accounts_roster.setObjectName("utility_btn")
        self.btn_refresh_accounts_roster.clicked.connect(self._refresh_accounts_roster_table)
        self.accounts_roster_search = QLineEdit()
        self.accounts_roster_search.setPlaceholderText("Поиск по приложению, имени, email, телефону…")
        self.accounts_roster_search.textChanged.connect(self._filter_accounts_roster_table)
        accounts_toolbar.addWidget(self.btn_import_accounts_roster)
        accounts_toolbar.addWidget(self.btn_paste_accounts_roster)
        accounts_toolbar.addWidget(self.btn_refresh_accounts_roster)
        accounts_toolbar.addWidget(self.accounts_roster_search, stretch=1)
        accounts_layout.addLayout(accounts_toolbar)
        self.accounts_roster_status = QLabel("")
        self.accounts_roster_status.setProperty("role", "hint")
        self.accounts_roster_status.setWordWrap(True)
        accounts_layout.addWidget(self.accounts_roster_status)
        self.accounts_roster_table = QTableWidget(0, 6)
        self.accounts_roster_table.setHorizontalHeaderLabels([
            "Приложение", "Имя", "Телефон", "Email", "Страна", "Telegram",
        ])
        self.accounts_roster_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.accounts_roster_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.accounts_roster_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.accounts_roster_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.accounts_roster_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.accounts_roster_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.accounts_roster_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.accounts_roster_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.accounts_roster_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.accounts_roster_table.setAlternatingRowColors(True)
        self.accounts_roster_table.setSortingEnabled(True)
        self.accounts_roster_table.verticalHeader().setVisible(False)
        accounts_layout.addWidget(self.accounts_roster_table, stretch=1)
        self.tabs.addTab(self.tab_accounts, "Аккаунты", "👥")

        self._api_tab_index = self.tabs.addTab(self.tab_api, "Настройки API", "⚙")

        self.start_btn = QPushButton("🚀 ЗАПУСТИТЬ ПРОЦЕСС")
        self.start_btn.setObjectName("start_btn") 
        self.start_btn.setMinimumHeight(52)
        self.start_btn.clicked.connect(self._start_process)
        root_layout.addWidget(self.start_btn)

        for widget in [
            self.issuer_input, self.key_input, self.app_input, self.p8_path_input,
            self.ai_provider_combo,
            self.aitunnel_key_input, self.aitunnel_model_combo, self.aitunnel_image_model_combo,
            self.zai_key_input, self.zai_model_combo, self.btn_refresh_zai_models,
        ]:
            if hasattr(widget, "textChanged"):
                widget.textChanged.connect(self._update_api_summary)
            elif hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(self._update_api_summary)
        self.app_input.textChanged.connect(self._on_app_id_changed)
        self._update_api_summary()
        self._update_icon_prompt_source_label()
        if self._has_complete_api_credentials():
            if self.app_input.text().strip():
                self._fetch_app_version(silent=True)
                self._pull_metadata_from_apple()
                self._prefetch_active_locales(silent=True)
                self._apply_account_roster_for_current_app(silent=True)
        self._refresh_accounts_roster_status()
        self._refresh_variant_cards()
        self.tabs.currentChanged.connect(self._on_tab_change)
        # Старт всегда с «Настройки API»
        self.tabs.setCurrentIndex(self._api_tab_index)
        self._on_tab_change(self.tabs.currentIndex())

    def _build_variant_cards_group(self, title):
        group = QGroupBox(title)
        layout = QGridLayout(group)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(10)
        if not hasattr(self, "variant_cards"):
            self.variant_cards = {}
        for column, variant_name in enumerate(("Variant A", "Variant B", "Variant C")):
            card = VariantFolderCard(variant_name)
            card.select_requested.connect(self._select_folder)
            layout.addWidget(card, 0, column)
            self.variant_cards.setdefault(variant_name, []).append(card)
        return group

    def _refresh_variant_cards(self):
        for variant_name, cards in getattr(self, "variant_cards", {}).items():
            current_path = self.variants_paths.get(variant_name, "")
            for card in cards:
                card.set_path(current_path)

    def _has_complete_api_credentials(self):
        return all([
            self.issuer_input.text().strip(),
            self.key_input.text().strip(),
            self.app_input.text().strip(),
            self.p8_path_input.text().strip(),
        ])

    def _api_summary_display_name(self):
        if self._summary_app_name:
            return self._summary_app_name.strip()
        if hasattr(self, "meta_name_input"):
            return self._line_text(self.meta_name_input)
        return ""

    def _api_summary_display_version(self):
        if self._summary_app_version:
            return self._summary_app_version.strip()
        if hasattr(self, "meta_app_version_input"):
            text = self.meta_app_version_input.text().strip()
            if text and text != "—":
                return text.split(" · ", 1)[0].strip()
        return ""

    def _format_api_summary_details(self, app_id):
        parts = []
        app_name = self._api_summary_display_name()
        app_version = self._api_summary_display_version()
        if app_name:
            parts.append(app_name)
        if app_version:
            parts.append(f"v{app_version}" if not app_version.startswith("v") else app_version)
        parts.append(f"App ID: {app_id}")
        return " | ".join(parts)

    def _on_app_id_changed(self):
        self._summary_app_name = ""
        self._summary_app_version = ""
        self._app_id_change_timer.start()

    def _on_app_id_changed_debounced(self):
        if self._has_complete_api_credentials() and self.app_input.text().strip():
            self._fetch_app_version(silent=True)
            self._prefetch_active_locales(silent=True)
        self._apply_account_roster_for_current_app(silent=True)
        self._refresh_accounts_roster_status()

    def _set_topbar_summary(self, text, status="neutral"):
        self.topbar_summary_label.setText(text)
        self.topbar_summary_label.setProperty("status", status)
        self.topbar_summary_label.style().unpolish(self.topbar_summary_label)
        self.topbar_summary_label.style().polish(self.topbar_summary_label)
        badge_text = {"ok": "🟢", "warn": "🟡", "neutral": "⚪"}.get(status, "⚪")
        self.topbar_badge.setText(badge_text)

    def _update_api_summary(self):
        missing = []
        fields = [
            ("Issuer", self.issuer_input.text().strip()),
            ("Key ID", self.key_input.text().strip()),
            ("App ID", self.app_input.text().strip()),
            ("P8 path", self.p8_path_input.text().strip()),
        ]
        for field_name, value in fields:
            if not value:
                missing.append(field_name)
        app_id = self.app_input.text().strip() or "—"
        details = self._format_api_summary_details(app_id)
        if missing:
            summary = f"Не заполнено: {', '.join(missing)} | {details}"
            self._set_topbar_summary(summary, status="warn")
        else:
            summary = f"Готово к запуску | {details}"
            self._set_topbar_summary(summary, status="ok")

    def _extract_key_id_from_p8_path(self):
        filename = os.path.basename(self.p8_path_input.text().strip())
        if not filename:
            return ""
        match = re.match(r"^AuthKey_([A-Za-z0-9]+)\.p8$", filename, re.IGNORECASE)
        return match.group(1) if match else ""

    def _apply_display_polish(self):
        app = QApplication.instance()
        if app is not None:
            app.setFont(platform_ui_font(UI_FONT_BASE_PT))
        mono_font = QFont("Menlo", 12) if sys.platform == "darwin" else QFont("Cascadia Mono", 12)
        self.log_area.setFont(mono_font)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        if sys.platform == "darwin":
            self.tabs.tabBar().setDocumentMode(True)

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0f;
                color: #e8e8f0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                font-size: 14px;
            }
            QLabel { color: #e8e8f0; font-weight: 500; background: transparent; }
            QLabel[role="title"] { font-size: 20px; color: #ffffff; font-weight: 700; letter-spacing: 0.2px; }
            QLabel[role="section"] { font-size: 12px; color: #a855f7; font-weight: 700; letter-spacing: 0.4px; }
            QLabel[role="hint"] { color: #9ca3af; font-weight: 400; font-size: 13px; }
            QLabel[role="label"] { color: #c4b5fd; font-weight: 600; font-size: 13px; }
            QLabel[status="ok"] { color: #4ADE80; font-weight: 600; }
            QLabel[status="warn"] { color: #FBBF24; font-weight: 600; }

            QWidget#page_header {
                background: transparent;
                border: none;
            }

            QWidget#execution_panel {
                background-color: #14142a;
                border: 1px solid #2a2a45;
                border-top: 3px solid #a855f7;
                border-radius: 10px;
                padding: 8px;
            }

            QGroupBox {
                border: 1px solid #2a2a45;
                border-radius: 10px;
                margin-top: 12px;
                padding-top: 20px;
                background-color: #14142a;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #c4b5fd;
                font-weight: 700;
            }
            QGroupBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #2a2a45;
                border-radius: 4px;
                background: #0d0d18;
            }
            QGroupBox::indicator:hover {
                border: 1px solid #a855f7;
            }
            QGroupBox::indicator:checked {
                background: #a855f7;
                border: 1px solid #c084fc;
            }

            QPushButton {
                background-color: #1a1a2e;
                border: 1px solid #2a2a45;
                border-radius: 8px;
                padding: 9px 12px;
                color: #f0f0ff;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #222238; border-color: #6366f1; }
            QPushButton:disabled { background-color: #12121e; border-color: #1e1e35; color: #6b7280; }

            QPushButton#start_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a855f7, stop:1 #6366f1);
                color: #ffffff;
                font-size: 15px;
                border: none;
                border-radius: 10px;
                font-weight: 700;
                padding: 12px 16px;
            }
            QPushButton#start_btn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #c084fc, stop:1 #818cf8); }
            QPushButton#start_btn:disabled { background: #1e1e35; color: #6b7280; }

            QPushButton#upload_cta_btn {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a855f7, stop:1 #6366f1);
                color: #ffffff;
                font-size: 16px;
                border: none;
                border-radius: 12px;
                font-weight: 800;
                padding: 14px 18px;
            }
            QPushButton#upload_cta_btn:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #c084fc, stop:1 #818cf8); }
            QPushButton#upload_cta_btn:disabled { background: #1e1e35; color: #6b7280; }

            QPushButton#utility_btn {
                padding: 5px 10px;
                font-size: 12px;
                border-radius: 6px;
                color: #c4b5fd;
                font-weight: 500;
                background-color: #1a1a2e;
            }
            QPushButton#utility_btn:hover { background-color: #222238; }
            QPushButton#main_generate_btn {
                background-color: #a855f7;
                color: #ffffff;
                font-size: 15px;
                border: none;
                font-weight: 700;
            }
            QPushButton#main_generate_btn:hover { background-color: #c084fc; }
            QPushButton#main_generate_btn:disabled { background-color: #1e1e35; color: #6b7280; }

            QLineEdit, QTextEdit, QComboBox {
                background-color: #0d0d18;
                border: 1px solid #2a2a45;
                border-radius: 8px;
                padding: 7px 10px;
                color: #f0f0ff;
                min-height: 30px;
                selection-background-color: #a855f7;
                selection-color: #ffffff;
            }
            QTextEdit { padding: 8px 10px; }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #a855f7; background-color: #11111f; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #14142a; color: #f0f0ff; selection-background-color: #3a2a55; border-radius: 5px; }

            QTabWidget::pane { border: 1px solid #2a2a45; border-radius: 10px; background-color: #0d0d18; margin-top: -1px; top: -1px; }
            QTabBar::tab {
                background: #14142a; border: 1px solid #2a2a45;
                border-top-left-radius: 8px; border-top-right-radius: 8px;
                padding: 8px 18px; margin-right: 4px; color: #9ca3af;
                min-width: 96px;
                border-top: 2px solid transparent;
            }
            QTabBar::tab:selected { background: #1e1e35; border-color: #6366f1; border-top: 2px solid #a855f7; color: #ffffff; font-weight: 700; }
            QTabBar::tab:hover:!selected { background: #1a1a2e; color: #c4b5fd; }

            QFrame#metadata_toolbar {
                background-color: #14142a;
                border: 1px solid #2a2a45;
                border-radius: 10px;
            }
            QFrame#metadata_toolbar_row2 {
                border-top: 1px solid #2a2a45;
            }
            QTabWidget#metadata_inner_tabs::pane {
                border: none;
                border-top: 1px solid #2a2a45;
                background-color: transparent;
                top: 0;
            }
            QTabWidget#metadata_inner_tabs QTabBar::tab {
                min-width: 72px;
                padding: 8px 14px;
            }
            QScrollArea#metadata_tab_scroll {
                border: none;
                background: transparent;
            }
            QWidget#metadata_flat_panel {
                background: transparent;
            }

            QProgressBar { border: 1px solid #2a2a45; border-radius: 8px; background-color: #0d0d18; text-align: center; color: #f0f0ff; font-weight: 700; height: 30px; }
            QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:0.5 #a855f7, stop:1 #ec4899); border-radius: 6px; }

            QCheckBox { color: #e2e8f0; spacing: 9px; padding: 4px 2px; background: transparent; }
            QCheckBox:hover { background: transparent; }
            QGroupBox QCheckBox { min-height: 22px; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #2a2a45; border-radius: 5px; background: #0d0d18; }
            QCheckBox::indicator:hover { border: 1px solid #a855f7; }
            QCheckBox::indicator:checked { background: #a855f7; border: 1px solid #c084fc; }

            QScrollArea { border: 1px solid #2a2a45; border-radius: 10px; background-color: #0d0d18; }
            QGroupBox QScrollArea { border: none; background: transparent; }
            QTableWidget {
                background-color: #0d0d18;
                border: 1px solid #2a2a45;
                border-radius: 8px;
                gridline-color: #2a2a45;
            }
            QHeaderView::section {
                background-color: #1e1e35;
                color: #c4b5fd;
                border: 1px solid #2a2a45;
                padding: 6px;
                font-weight: 600;
            }
            QTextEdit#log_area {
                background-color: #06060d;
                border: 1px solid #1e1e35;
                border-radius: 10px;
                color: #c4b5fd;
                padding: 10px;
            }
            QScrollBar:vertical { border: none; background: #0a0a0f; width: 12px; margin: 0px; }
            QScrollBar::handle:vertical { background: #2a2a45; min-height: 20px; border-radius: 6px; }
            QScrollBar::handle:vertical:hover { background: #3a3a55; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

            QFrame#variant_card {
                border: 1px solid #2a2a45;
                border-radius: 10px;
                background-color: #14142a;
            }
            QFrame#variant_card[has_path="true"] {
                border-color: #6366f1;
                background-color: #1e1e35;
            }

            QFrame#drop_zone {
                border: 2px dashed #6366f1;
                border-radius: 12px;
                background-color: #0d0d18;
                min-height: 80px;
            }
            QFrame#drop_zone:hover {
                background-color: #1e1e35;
                border-color: #a855f7;
            }

            QSplitter::handle {
                background-color: #1e1e35;
                width: 8px;
                border-radius: 4px;
                margin: 4px 2px;
            }
            QSplitter::handle:hover { background-color: #6366f1; }

            /* ===== Topbar сводки ===== */
            QFrame#topbar {
                background-color: #14142a;
                border-bottom: 1px solid #2a2a45;
            }
            QLabel#topbar_summary {
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#topbar_badge {
                font-size: 16px;
                background: transparent;
            }

            /* ===== Sidebar навигация ===== */
            QWidget#nav_sidebar {
                background-color: #0d0d18;
                border-right: 1px solid #2a2a45;
            }
            QLabel#nav_logo {
                color: #ffffff;
                font-size: 16px;
                font-weight: 800;
                padding: 4px 6px;
            }
            QPushButton#nav_item {
                text-align: left;
                background-color: transparent;
                border: none;
                border-left: 3px solid transparent;
                border-radius: 8px;
                padding: 11px 14px;
                color: #9ca3af;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton#nav_item:hover {
                background-color: rgba(168, 85, 247, 0.10);
                color: #c4b5fd;
            }
            QPushButton#nav_item:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(168,85,247,0.22), stop:1 rgba(168,85,247,0.02));
                border-left: 3px solid #a855f7;
                color: #ffffff;
                font-weight: 700;
            }
        """)

    def _load_prompt_profiles(self):
        profiles = dict(DEFAULT_PROMPT_PROFILES)
        try:
            if os.path.exists(PROMPT_PROFILES_FILE):
                with open(PROMPT_PROFILES_FILE, "r", encoding="utf-8") as f:
                    custom_profiles = json.load(f)
                if isinstance(custom_profiles, dict):
                    profiles.update({str(k): str(v) for k, v in custom_profiles.items()})
        except Exception as e:
            self._log(f"Ошибка загрузки prompt profiles: {e}")
        return profiles

    def _apply_prompt_profile(self):
        profile_name = self.ai_prompt_profile_combo.currentText()
        prompt = self.ai_prompt_profiles.get(profile_name)
        if prompt:
            self.ai_prompt_input.setPlainText(prompt)
            self._log(f"Prompt profile применен: {profile_name}")

    def _save_current_prompt_profile(self):
        profile_name = self.ai_prompt_profile_combo.currentText().strip() or "Custom"
        prompt = self._text_edit_text(self.ai_prompt_input)
        if not prompt:
            self._log("Ошибка: пустой промт нельзя сохранить как профиль.")
            return
        self.ai_prompt_profiles[profile_name] = prompt
        try:
            with open(PROMPT_PROFILES_FILE, "w", encoding="utf-8") as f:
                json.dump(self.ai_prompt_profiles, f, ensure_ascii=False, indent=2)
            if self.ai_prompt_profile_combo.findText(profile_name) < 0:
                self.ai_prompt_profile_combo.addItem(profile_name)
            self._log(f"Prompt profile сохранен: {profile_name}")
        except Exception as e:
            self._log(f"Ошибка сохранения prompt profile: {e}")

    def _line_text(self, widget):
        if isinstance(widget, QComboBox):
            value = widget.currentData()
            return str(value).strip() if value is not None else widget.currentText().strip()
        return widget.text().strip()

    def _add_metadata_stacked_field(self, layout, label, widget, ai_mode=None):
        block = QWidget()
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(4)
        field_label = QLabel(label.rstrip(":"))
        field_label.setProperty("role", "label")
        block_layout.addWidget(field_label)
        if ai_mode:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(widget, stretch=1)
            ai_button = QToolButton()
            ai_button.setText("✨")
            ai_button.setToolTip(f"Сгенерировать только это поле ({label.rstrip(':')})")
            ai_button.setFixedSize(34, 34)
            ai_button.clicked.connect(lambda _, mode=ai_mode: self._generate_ai_metadata(mode))
            if not hasattr(self, "ai_field_buttons"):
                self.ai_field_buttons = []
            self.ai_field_buttons.append(ai_button)
            row_layout.addWidget(ai_button)
            block_layout.addWidget(row_widget)
        else:
            block_layout.addWidget(widget)
        layout.addWidget(block)

    def _add_metadata_pair_row(
        self, layout, left_label, left_widget, right_label, right_widget,
        left_ai=None, right_ai=None,
    ):
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        def _column(lbl, w, ai_mode):
            col = QWidget()
            col_layout = QVBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(4)
            field_label = QLabel(lbl.rstrip(":"))
            field_label.setProperty("role", "label")
            col_layout.addWidget(field_label)
            if ai_mode:
                inner = QWidget()
                inner_layout = QHBoxLayout(inner)
                inner_layout.setContentsMargins(0, 0, 0, 0)
                inner_layout.setSpacing(6)
                inner_layout.addWidget(w, stretch=1)
                ai_button = QToolButton()
                ai_button.setText("✨")
                ai_button.setToolTip(f"Сгенерировать только это поле ({lbl.rstrip(':')})")
                ai_button.setFixedSize(34, 34)
                ai_button.clicked.connect(lambda _, mode=ai_mode: self._generate_ai_metadata(mode))
                if not hasattr(self, "ai_field_buttons"):
                    self.ai_field_buttons = []
                self.ai_field_buttons.append(ai_button)
                inner_layout.addWidget(ai_button)
                col_layout.addWidget(inner)
            else:
                col_layout.addWidget(w)
            return col

        row.addWidget(_column(left_label, left_widget, left_ai), stretch=1)
        row.addWidget(_column(right_label, right_widget, right_ai), stretch=1)
        layout.addWidget(row_widget)

    def _add_metadata_field_row(self, form_layout, label, widget, ai_mode):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(widget, stretch=1)
        ai_button = QToolButton()
        ai_button.setText("✨")
        ai_button.setToolTip(f"Сгенерировать только это поле ({label.strip(':')})")
        ai_button.setFixedSize(34, 34)
        ai_button.clicked.connect(lambda _, mode=ai_mode: self._generate_ai_metadata(mode))
        if not hasattr(self, "ai_field_buttons"):
            self.ai_field_buttons = []
        self.ai_field_buttons.append(ai_button)
        row_layout.addWidget(ai_button)
        form_layout.addRow(label, row_widget)

    def _apply_developer_name_to_review_fields(self):
        developer_name = self._line_text(self.ai_developer_name_input)
        if not developer_name:
            return
        normalized_name = " ".join(developer_name.split())
        name_parts = normalized_name.split(" ")
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        self.meta_review_first_name_input.setText(first_name)
        self.meta_review_last_name_input.setText(last_name)
        if not self._line_text(self.meta_copyright_input):
            self.meta_copyright_input.setText(f"© {normalized_name}")

    def _text_edit_text(self, widget):
        return widget.toPlainText().strip()

    def _set_combo_data(self, combo, value):
        index = combo.findData(value)
        if index < 0 and value:
            combo.addItem(str(value), str(value))
            index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_category_combo(self, combo, value, fallback_text=""):
        """Ставит категорию в combo, понимая имена/enum/ID от AI."""
        gui_id = normalize_gui_category_id(value)
        if not gui_id and fallback_text:
            gui_id = normalize_gui_category_id(fallback_text)
        if not gui_id:
            return False
        index = combo.findData(gui_id)
        if index < 0:
            self._log(f"⚠️ Категория '{value}' распознана как {gui_id}, но нет в списке GUI.")
            return False
        combo.setCurrentIndex(index)
        return True

    def _set_metadata_locale(self, locale):
        index = self.meta_locale_combo.findData(locale)
        if index < 0:
            self.meta_locale_combo.addItem(locale, locale)
            index = self.meta_locale_combo.findData(locale)
        self.meta_locale_combo.setCurrentIndex(index)

    def _metadata_api_creds(self):
        return {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "app_id": self.app_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip()
        }

    def _version_url_defaults_from_gui(self):
        return {
            "supportUrl": self._line_text(self.meta_support_url_input),
            "marketingUrl": self._line_text(self.meta_marketing_url_input),
        }

    def _worker_is_running(self, attr_name):
        worker = getattr(self, attr_name, None)
        return worker is not None and worker.isRunning()

    def _track_worker(self, attr_name, worker):
        worker.setParent(self)
        setattr(self, attr_name, worker)

        def _cleanup():
            current = getattr(self, attr_name, None)
            if current is worker:
                setattr(self, attr_name, None)
            worker.deleteLater()

        worker.finished.connect(_cleanup)

    def _wait_for_workers(self, attr_names, timeout_ms=5000):
        for attr_name in attr_names:
            worker = getattr(self, attr_name, None)
            if worker is not None and worker.isRunning():
                worker.wait(timeout_ms)

    def closeEvent(self, event):
        self._wait_for_workers((
            "app_version_fetcher",
            "metadata_fetcher",
            "apps_fetcher",
            "worker",
            "jira_worker",
            "gemini_worker",
            "screenshot_upload_worker",
            "locale_refresh_worker",
            "locale_prefetch_worker",
            "translation_upload_worker",
            "translation_worker",
            "figma_import_worker",
            "privacy_pages_worker",
            "privacy_oneshot_worker",
            "gitlab_brief_worker",
        ))
        super().closeEvent(event)

    def _pull_metadata_from_apple(self):
        self._save_env_to_file()
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все настройки API для Pull from Apple.")
            return
        if self._worker_is_running("metadata_fetcher"):
            return

        self.btn_pull_metadata.setEnabled(False)
        self.meta_source_locale_label.setText("Локаль: загрузка...")
        self.meta_source_version_label.setText("Версия: загрузка...")
        metadata_fetcher = FetchMetadataSourceWorker(api_creds)
        metadata_fetcher.log_msg.connect(self._log)
        metadata_fetcher.source_fetched.connect(self._apply_metadata_source_payload)
        metadata_fetcher.finished.connect(lambda: self.btn_pull_metadata.setEnabled(True))
        self._track_worker("metadata_fetcher", metadata_fetcher)
        metadata_fetcher.start()

    def _apply_metadata_source_payload(self, metadata_config):
        locale = metadata_config.get("source_locale")
        version_info = metadata_config.get("version_info", {})
        if locale:
            self._set_metadata_locale(locale)
            locale_name = LOCALE_MAP.get(locale, ("", locale))[1]
            self.meta_source_locale_label.setText(f"Локаль: {locale_name} ({locale})")
        version_text = version_info.get("version_string") or "—"
        state = version_info.get("state") or "unknown"
        self.meta_source_version_label.setText(f"Версия: {version_text} · {state}")
        self._apply_pulled_metadata(metadata_config)

    def _apply_pulled_metadata(self, metadata_config):
        if not metadata_config:
            self._log("В Apple не найдены метаданные для выбранной локали.")
            return
        self._apply_metadata_config_to_gui(metadata_config)
        # Таблица аккаунтов — источник Review email/имени для Privacy oneshot.
        self._apply_account_roster_for_current_app(silent=True)
        self._refresh_accounts_roster_status()
        app_name = self._line_text(self.meta_name_input) if hasattr(self, "meta_name_input") else ""
        if app_name:
            self._summary_app_name = app_name
            self._fetch_gitlab_brief(app_name=app_name, force=False, only_if_empty=True)

    def _set_translation_buttons_enabled(self, enabled):
        for btn in [
            getattr(self, "btn_translation_auto_source", None),
            getattr(self, "btn_translation_refresh_locales", None),
            getattr(self, "btn_translation_translate", None),
            getattr(self, "btn_translation_validate", None),
            getattr(self, "btn_translation_upload", None),
        ]:
            if btn is not None:
                btn.setEnabled(enabled)

    def _fetch_translation_source_auto(self):
        self._save_env_to_file()
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните Issuer ID, Key ID, APP_ID и .p8 для auto source.")
            return
        self.btn_translation_auto_source.setEnabled(False)
        self.translation_source_info.setText("Определяю primary locale и загружаю source metadata...")
        self.translation_auto_source_fetcher = FetchTranslationAutoSourceWorker(api_creds)
        self.translation_auto_source_fetcher.log_msg.connect(self._log)
        self.translation_auto_source_fetcher.source_fetched.connect(self._apply_translation_auto_source_payload)
        self.translation_auto_source_fetcher.finished.connect(
            lambda: self.btn_translation_auto_source.setEnabled(True)
        )
        self.translation_auto_source_fetcher.start()

    def _apply_translation_primary_locale(self, locale):
        idx = self.translation_source_locale_combo.findData(locale)
        if idx < 0:
            self.translation_source_locale_combo.addItem(locale, locale)
            idx = self.translation_source_locale_combo.findData(locale)
        if idx >= 0:
            self.translation_source_locale_combo.setCurrentIndex(idx)
        self._log(f"Primary locale для перевода выбрана: {locale}")

    def _apply_translation_auto_source_payload(self, payload):
        locale = payload.get("primary_locale") or payload.get("source", {}).get("locale")
        if locale:
            self._apply_translation_primary_locale(locale)
        self._apply_translation_source_payload(payload)

    def _apply_translation_source_payload(self, payload):
        self.translation_source_payload = payload.get("source", {})
        self.translation_version_id = payload.get("version_id", "")
        self.translation_app_info_id = payload.get("app_info_id", "")
        non_empty = sum(
            1 for field_key, _, _, _ in TRANSLATION_FIELDS
            if self.translation_source_payload.get(field_key)
        )
        self.translation_source_info.setText(
            f"Source locale: {self.translation_source_payload.get('locale', '—')} | "
            f"непустых полей: {non_empty}/{len(TRANSLATION_FIELDS)}"
        )

    def _selected_translation_fields(self):
        return [
            field_key for field_key, checkbox in self.translation_field_checks.items()
            if checkbox.isChecked()
        ]

    def _field_label(self, field_key):
        for key, label, _limit, _target in TRANSLATION_FIELDS:
            if key == field_key:
                return label
        return field_key

    def _field_limit(self, field_key):
        for key, _label, limit, _target in TRANSLATION_FIELDS:
            if key == field_key:
                return limit
        return None

    def _translate_localizations(self):
        self._save_env_to_file()
        if not self.translation_source_payload:
            self._log("Сначала нажмите Pull source from Apple.")
            return
        target_locales = self.translation_target_picker.get_selected_locales()
        if not target_locales:
            self._log("Ошибка: выберите хотя бы одну целевую локаль для перевода.")
            return
        fields = self._selected_translation_fields()
        if not fields:
            self._log("Ошибка: выберите хотя бы одно поле для перевода.")
            return
        api_key, model, provider = self._selected_ai_credentials()
        if not api_key:
            provider_label = ai_provider_label(provider)
            self._log(f"Ошибка: укажите API key для {provider_label} на вкладке «Настройки API».")
            return
        if not model:
            provider_label = ai_provider_label(provider)
            self._log(f"Ошибка: выберите модель для {provider_label} на вкладке «Настройки API».")
            return

        source_locale = self.translation_source_locale_combo.currentData() or "en-US"
        profile_name = self.translation_profile_combo.currentText() or "ASO natural"
        self._set_translation_buttons_enabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("AI перевод: 0%")
        self.translation_worker = GeminiLocalizationTranslationWorker(
            api_key=api_key,
            model=model,
            source_locale=source_locale,
            target_locales=target_locales,
            fields=fields,
            profile_name=profile_name,
            source_payload=self.translation_source_payload,
            ai_provider=provider,
            max_workers=self._selected_translation_workers(),
        )
        self.translation_worker.log_msg.connect(self._log)
        self.translation_worker.progress_update.connect(self._update_progress)
        self.translation_worker.translations_ready.connect(self._on_translations_ready)
        self.translation_worker.finished.connect(self._on_translation_worker_finished)
        self.translation_worker.start()

    def _on_translations_ready(self, rows):
        self._populate_translation_table(rows)
        self._pending_auto_upload_translations = bool(
            getattr(self, "translation_auto_upload_checkbox", None)
            and self.translation_auto_upload_checkbox.isChecked()
        )

    def _on_translation_worker_finished(self):
        if getattr(self, "_pending_auto_upload_translations", False):
            self._pending_auto_upload_translations = False
            self._log("Автозагрузка: отправляю переводы в App Store Connect...")
            self._upload_translation_rows()
            return
        self._set_translation_buttons_enabled(True)

    def _refresh_translation_locales(self):
        self._save_env_to_file()
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните Issuer ID, Key ID, APP_ID и .p8 для обновления локалей.")
            return
        if hasattr(self, "translation_locale_refresh_worker") and self.translation_locale_refresh_worker is not None:
            if self.translation_locale_refresh_worker.isRunning():
                return
        self.btn_translation_refresh_locales.setEnabled(False)
        self.translation_locale_refresh_worker = RefreshLocalesWorker(api_creds)
        self.translation_locale_refresh_worker.log_msg.connect(self._log)
        self.translation_locale_refresh_worker.locales_fetched.connect(self._apply_active_translation_locales)
        self.translation_locale_refresh_worker.finished.connect(
            lambda: self.btn_translation_refresh_locales.setEnabled(True)
        )
        self.translation_locale_refresh_worker.start()

    def _apply_active_translation_locales(self, active_locales):
        source_locale = ""
        if hasattr(self, "translation_source_locale_combo"):
            source_locale = self.translation_source_locale_combo.currentData() or ""
        locales = [code for code in (active_locales or []) if code and code != source_locale]
        self.translation_target_picker.set_available_locales(locales)
        skipped = ""
        if source_locale and source_locale in (active_locales or []):
            skipped = f" (source {source_locale} исключён из целевых)"
        self._log(f"✅ Целевые локали перевода: {len(locales)}{skipped}")

    def _populate_translation_table(self, rows):
        self.translation_table_busy = True
        self.translation_table.setRowCount(0)
        for row_data in rows:
            row_idx = self.translation_table.rowCount()
            self.translation_table.insertRow(row_idx)
            values = [
                row_data.get("locale", ""),
                self._field_label(row_data.get("field", "")),
                row_data.get("source", ""),
                row_data.get("translation", ""),
                row_data.get("status", ""),
                row_data.get("warning", ""),
            ]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col_idx != 3:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col_idx == 1:
                    item.setData(Qt.UserRole, row_data.get("field", ""))
                self.translation_table.setItem(row_idx, col_idx, item)
        self.translation_table_busy = False
        self._validate_translation_table(preserve_existing_errors=True)

    def _translation_row_uploadable(self, row):
        translation = str(row.get("translation", "") or "").strip()
        if not translation:
            return False
        return row.get("status") in ("ok", "info")

    def _status_for_row(self, field_key, translation_text):
        text = str(translation_text or "").strip()
        if not text:
            return "warn", "Пустой перевод"
        limit = self._field_limit(field_key)
        if limit and len(text) > limit:
            return "error", f"Превышен лимит {len(text)}/{limit}"
        if field_key == "keywords":
            if KEYWORD_INLINE_WORD_SPLIT_RE.search(text) and not KEYWORD_PART_SPLIT_RE.search(text):
                return "info", "Keywords: лучше comma-separated (без пробелов)"
            items = [v.strip().lower() for v in _split_keyword_parts(text)]
            duplicates = sorted({v for v in items if items.count(v) > 1})
            if duplicates:
                return "info", f"Дубли keywords: {', '.join(duplicates)}"
            generic = {"best", "top", "easy", "fast", "free", "app", "simple"}
            generic_hits = sorted(generic.intersection(items))
            if generic_hits:
                return "info", f"Generic keywords: {', '.join(generic_hits)}"
        lowered = text.lower()
        banned_hits = [word for word in BANNED_ASO_WORDS if word in lowered]
        if banned_hits:
            return "info", f"ASO warning: banned words ({', '.join(banned_hits[:3])})"
        return "ok", "Готово"

    def _apply_row_status_style(self, row_idx, status, warning):
        color = TRANSLATION_STATUS_COLORS.get(status, QColor("#1E293B"))
        status_text = {
            "ok": "готово",
            "warn": "пусто",
            "error": "ошибка",
            "info": "рекомендация",
        }.get(status, status)
        status_item = self.translation_table.item(row_idx, 4)
        warning_item = self.translation_table.item(row_idx, 5)
        if status_item is not None:
            status_item.setText(status_text)
        if warning_item is not None:
            warning_item.setText(warning)
        for col_idx in range(self.translation_table.columnCount()):
            item = self.translation_table.item(row_idx, col_idx)
            if item is not None:
                item.setBackground(color)

    def _validate_translation_table(self, preserve_existing_errors=False):
        self.translation_table_busy = True
        trimmed_rows = 0
        for row_idx in range(self.translation_table.rowCount()):
            field_item = self.translation_table.item(row_idx, 1)
            translation_item = self.translation_table.item(row_idx, 3)
            status_item = self.translation_table.item(row_idx, 4)
            warning_item = self.translation_table.item(row_idx, 5)
            if field_item is None or translation_item is None:
                continue
            warning_text = warning_item.text().strip() if warning_item is not None else ""
            is_limit_error = warning_text.startswith("Превышен лимит")
            if (
                preserve_existing_errors
                and status_item is not None
                and status_item.text().strip().lower() in {"error", "ошибка"}
                and warning_text
                and not is_limit_error
            ):
                self._apply_row_status_style(row_idx, "error", warning_text)
                continue
            field_key = field_item.data(Qt.UserRole) or field_item.text().strip()
            original = translation_item.text()
            trimmed, changed = truncate_metadata_field(field_key, original)
            if changed:
                translation_item.setText(trimmed)
                trimmed_rows += 1
            status, warning = self._status_for_row(field_key, translation_item.text())
            if changed:
                limit = self._field_limit(field_key) or len(trimmed)
                warning = (
                    f"Авто-сокращено {len(original)}→{len(trimmed)} "
                    f"(лимит {limit}, целые слова с конца). {warning}"
                )
                if status == "error":
                    status = "info"
            self._apply_row_status_style(row_idx, status, warning)
        self.translation_table_busy = False
        if trimmed_rows:
            self._log(
                f"Validate: авто-сокращено {trimmed_rows} строк "
                f"(убраны целые keywords/слова с конца, без обрезки посередине)."
            )
        self._log("Validate завершен для таблицы переводов.")

    def _on_translation_item_changed(self, item):
        if self.translation_table_busy or item.column() != 3:
            return
        row_idx = item.row()
        field_item = self.translation_table.item(row_idx, 1)
        if field_item is None:
            return
        field_key = field_item.data(Qt.UserRole) or field_item.text().strip()
        status, warning = self._status_for_row(field_key, item.text())
        self._apply_row_status_style(row_idx, status, warning)

    def _collect_translation_rows(self):
        rows = []
        for row_idx in range(self.translation_table.rowCount()):
            locale_item = self.translation_table.item(row_idx, 0)
            field_item = self.translation_table.item(row_idx, 1)
            source_item = self.translation_table.item(row_idx, 2)
            translation_item = self.translation_table.item(row_idx, 3)
            status_item = self.translation_table.item(row_idx, 4)
            warning_item = self.translation_table.item(row_idx, 5)
            if not all([locale_item, field_item, source_item, translation_item, status_item, warning_item]):
                continue
            rows.append({
                "locale": locale_item.text().strip(),
                "field": field_item.data(Qt.UserRole) or field_item.text().strip(),
                "source": source_item.text(),
                "translation": translation_item.text(),
                "status": {
                    "готово": "ok",
                    "пусто": "warn",
                    "ошибка": "error",
                    "рекомендация": "info",
                }.get(status_item.text().strip(), "warn"),
                "warning": warning_item.text().strip(),
            })
        return rows

    def _upload_translation_rows(self):
        rows = self._collect_translation_rows()
        if not rows:
            self._log("Нет данных для загрузки. Сначала выполните перевод.")
            self._set_translation_buttons_enabled(True)
            return
        upload_rows = [row for row in rows if self._translation_row_uploadable(row)]
        skipped_errors = sum(1 for row in rows if row.get("status") == "error")
        skipped_empty = sum(1 for row in rows if row.get("status") == "warn")
        if not upload_rows:
            self._log(
                "Нет строк для загрузки: нужен непустой перевод и статус без ошибки "
                "(ошибка лимита / AI)."
            )
            self._set_translation_buttons_enabled(True)
            return
        info_rows = sum(1 for row in upload_rows if row.get("status") == "info")
        if info_rows:
            self._log(f"ℹ️ {info_rows} строк с ASO-рекомендациями тоже будут загружены.")
        if skipped_errors:
            self._log(f"⚠️ Пропущено строк с ошибкой: {skipped_errors}.")
        if skipped_empty:
            self._log(f"⚠️ Пропущено пустых строк: {skipped_empty}.")
        self._set_translation_buttons_enabled(False)
        self.translation_upload_worker = LocalizationUploadWorker(
            self._metadata_api_creds(),
            upload_rows,
            version_url_defaults=self._version_url_defaults_from_gui(),
            max_workers=self._selected_localization_upload_workers(),
        )
        self.translation_upload_worker.log_msg.connect(self._log)
        self.translation_upload_worker.progress_update.connect(self._update_progress)
        self.translation_upload_worker.upload_finished.connect(self._on_translation_upload_finished)
        self.translation_upload_worker.finished.connect(lambda: self._set_translation_buttons_enabled(True))
        self.translation_upload_worker.start()

    def _on_translation_upload_finished(self, summary):
        self._log(
            "Upload переводов завершен: "
            f"локалей={summary.get('locales', 0)}, "
            f"полей={summary.get('fields', 0)}, "
            f"ошибок={summary.get('errors', 0)}"
        )
        chain_on = bool(
            getattr(self, "translation_chain_screenshots_checkbox", None)
            and self.translation_chain_screenshots_checkbox.isChecked()
        )
        if not chain_on:
            return
        remaining_errors = self._count_translation_error_rows()
        if remaining_errors > 0:
            self._log(
                f"Цепочка скринов пропущена: после повторов осталось ошибок перевода: {remaining_errors}."
            )
            return
        if int(summary.get("errors") or 0) > 0:
            self._log("Цепочка скринов пропущена: были ошибки upload переводов.")
            return
        if int(summary.get("locales") or 0) <= 0:
            self._log("Цепочка скринов пропущена: перевод не загрузился.")
            return
        self._start_translation_to_screenshots_chain()

    def _count_translation_error_rows(self):
        try:
            rows = self._collect_translation_rows()
        except Exception:
            return 0
        return sum(1 for row in rows if row.get("status") == "error")

    def _screens_tab_index(self):
        tabs = getattr(self, "tabs", None)
        target = getattr(self, "tab_screens_upload", None)
        if tabs is None or target is None:
            return -1
        for index in range(len(getattr(tabs, "_tab_labels", []) or [])):
            if tabs.widget(index) is target:
                return index
        # Fallback for plain QTabWidget-like API
        try:
            count = tabs._stack.count() if hasattr(tabs, "_stack") else 0
        except Exception:
            count = 0
        for index in range(count):
            if tabs.widget(index) is target:
                return index
        return 4

    def _start_translation_to_screenshots_chain(self):
        self._log(
            "Цепочка: перевод → Скриншоты "
            "(локали → Загрузки → upload"
            + (
                ", с очисткой EXIF/сжатием"
                if self._optimize_images_before_upload()
                else ""
            )
            + ")..."
        )
        self._pending_chain_screenshots_upload = True
        index = self._screens_tab_index()
        if index < 0:
            self._pending_chain_screenshots_upload = False
            self._log("Цепочка: вкладка Скриншоты не найдена.")
            return
        if self.tabs.currentIndex() == index:
            self._refresh_screenshot_locales()
        else:
            self.tabs.setCurrentIndex(index)

    def _finish_translation_screenshots_chain(self):
        paths = _list_downloads_screenshot_paths()
        if not paths:
            self._log("Цепочка: в Загрузках нет скринов — upload пропущен.")
            return
        self._set_upload_jpeg_files(paths)
        self._log(f"Цепочка: взято {len(paths)} скрин(ов) из Загрузки.")
        if not self._validate_screenshot_upload_prereqs():
            self._log("Цепочка: upload скринов не запущен (проверьте локали/файлы).")
            return
        if self._optimize_images_before_upload():
            self._log("Цепочка: оптимизация/EXIF по галочке → upload в App Store...")
        else:
            self._log("Цепочка: upload скринов в App Store (без оптимизации)...")
        self._start_screenshot_upload()

    def _fetch_app_version(self, silent=False):
        self._save_env_to_file()
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            if not silent:
                self._log("Ошибка: Заполните Issuer ID, Key ID, APP_ID и .p8 для загрузки версии.")
            return

        if self._worker_is_running("app_version_fetcher"):
            return

        if hasattr(self, "btn_fetch_app_version"):
            self.btn_fetch_app_version.setEnabled(False)
        app_version_fetcher = FetchAppVersionWorker(api_creds)
        app_version_fetcher.log_msg.connect(self._log)
        app_version_fetcher.version_fetched.connect(self._apply_app_version)
        app_version_fetcher.finished.connect(
            lambda: self.btn_fetch_app_version.setEnabled(True) if hasattr(self, "btn_fetch_app_version") else None
        )
        self._track_worker("app_version_fetcher", app_version_fetcher)
        app_version_fetcher.start()

    def _apply_app_version(self, version_info):
        version_string = version_info.get("version_string") or "—"
        state = version_info.get("state") or "unknown"
        version_id = version_info.get("id") or ""
        app_name = version_info.get("app_name", "").strip()
        if app_name:
            self._summary_app_name = app_name
        if version_string and version_string != "—":
            self._summary_app_version = version_string
        label = f"{version_string} · {state}"
        if version_id:
            label += f" · {version_id}"
        self.meta_app_version_input.setText(label)
        self._update_api_summary()

    def _app_lookup_creds(self):
        self._autofill_key_id_from_p8_path()
        return {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip(),
        }

    def _fetch_app_ids(self):
        creds = self._app_lookup_creds()
        missing = [name for name, value in creds.items() if not value]
        if missing:
            self._log(
                "Ошибка: для загрузки App ID нужны Issuer ID, Key ID и путь к .p8. "
                f"Не заполнено: {', '.join(missing)}"
            )
            return

        if self._worker_is_running("apps_fetcher"):
            return

        self.btn_fetch_app_id.setEnabled(False)
        self._log("Запрашиваю список приложений App Store Connect...")
        apps_fetcher = FetchAppsWorker(creds)
        apps_fetcher.log_msg.connect(self._log)
        apps_fetcher.apps_fetched.connect(self._apply_fetched_apps)
        apps_fetcher.finished.connect(lambda: self.btn_fetch_app_id.setEnabled(True))
        self._track_worker("apps_fetcher", apps_fetcher)
        apps_fetcher.start()

    def _apply_fetched_apps(self, apps):
        if not apps:
            self._log("В App Store Connect не найдено доступных приложений для этого ключа.")
            return
        if len(apps) == 1:
            self._set_selected_app(apps[0])
            return
        selected_app = self._select_app_dialog(apps)
        if selected_app:
            self._set_selected_app(selected_app)

    def _select_app_dialog(self, apps):
        dialog = QDialog(self)
        dialog.setWindowTitle("Выберите приложение")
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Выберите приложение, для которого нужно подставить APP_ID.")
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        search_input = QLineEdit()
        search_input.setPlaceholderText("Поиск по названию, bundle id, sku или app id")
        layout.addWidget(search_input)

        list_widget = QListWidget(dialog)
        layout.addWidget(list_widget, stretch=1)

        def app_label(app):
            name = app.get("name") or "Без названия"
            bundle_id = app.get("bundle_id") or "no bundle"
            sku = app.get("sku") or "no sku"
            app_id = app.get("id") or "no id"
            locale = app.get("primary_locale") or "no locale"
            return f"{name} | {bundle_id} | SKU: {sku} | APP_ID: {app_id} | {locale}"

        def populate(query=""):
            list_widget.clear()
            normalized = query.strip().lower()
            for app in apps:
                label = app_label(app)
                if normalized and normalized not in label.lower():
                    continue
                item = QListWidgetItem(label, list_widget)
                item.setData(Qt.UserRole, app)

        populate()
        search_input.textChanged.connect(populate)

        buttons_row = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_ok = QPushButton("Выбрать")
        btn_ok.setObjectName("start_btn")
        buttons_row.addStretch(1)
        buttons_row.addWidget(btn_cancel)
        buttons_row.addWidget(btn_ok)
        layout.addLayout(buttons_row)

        btn_cancel.clicked.connect(dialog.reject)
        btn_ok.clicked.connect(dialog.accept)
        list_widget.itemDoubleClicked.connect(lambda *_args: dialog.accept())

        if list_widget.count() > 0:
            list_widget.setCurrentRow(0)
        if dialog.exec() != QDialog.Accepted:
            return None
        current_item = list_widget.currentItem()
        if current_item is None:
            QMessageBox.warning(self, "App ID", "Выберите приложение из списка.")
            return None
        return current_item.data(Qt.UserRole)

    def _set_selected_app(self, app):
        app_id = app.get("id", "").strip()
        if not app_id:
            self._log("Ошибка: выбранное приложение не содержит APP_ID.")
            return
        self.app_input.blockSignals(True)
        self.app_input.setText(app_id)
        self.app_input.blockSignals(False)
        name = app.get("name") or "приложение"
        bundle_id = app.get("bundle_id") or "bundle id не указан"
        self._summary_app_name = name if name != "приложение" else ""
        self._summary_app_version = ""
        self._save_env_to_file()
        self._update_api_summary()
        self._log(f"APP_ID выбран: {app_id} ({name}, {bundle_id})")
        self._fetch_app_version(silent=True)
        self._pull_metadata_from_apple()
        self._prefetch_active_locales(silent=True)
        self._apply_account_roster_for_current_app(silent=False)
        self._refresh_accounts_roster_status()
        self._fetch_gitlab_brief(app_name=self._summary_app_name or name, force=True, only_if_empty=False)
        self._auto_fill_xcode_project_for_app(
            app_name=self._summary_app_name or name,
            bundle_id=bundle_id if bundle_id != "bundle id не указан" else "",
        )

    def _load_metadata_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите JSON с метаданными", "", "JSON Files (*.json);;All Files (*)")
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                metadata_config = json.load(f)
            self._apply_metadata_config_to_gui(metadata_config)
            self._log(f"JSON импортирован в GUI-поля: {file_path}")
        except json.JSONDecodeError as e:
            self._log(f"Ошибка JSON: строка {e.lineno}, колонка {e.colno}: {e.msg}")
        except Exception as e:
            self._log(f"Ошибка чтения JSON: {e}")

    def _apply_metadata_config_to_gui(self, metadata_config):
        version_items = metadata_config.get("version_localizations", [])
        app_info_items = metadata_config.get("app_info_localizations", [])
        version_item = version_items[0] if version_items else {}
        app_info_item = app_info_items[0] if app_info_items else {}
        app_info = metadata_config.get("app_info", {})
        review = metadata_config.get("app_review_detail", {})

        locale = version_item.get("locale") or app_info_item.get("locale")
        if locale:
            self._set_combo_data(self.meta_locale_combo, locale)

        self.meta_description_input.setPlainText(version_item.get("description", ""))
        self.meta_keywords_input.setText(version_item.get("keywords", ""))
        self.meta_promotional_text_input.setPlainText(version_item.get("promotionalText", ""))
        self.meta_whats_new_input.setPlainText(
            version_item.get("whatsNew") or version_item.get("notes") or version_item.get("releaseNotes", "")
        )
        self.meta_support_url_input.setText(version_item.get("supportUrl", ""))
        self.meta_marketing_url_input.setText(version_item.get("marketingUrl", ""))

        self.meta_name_input.setText(app_info_item.get("name", ""))
        self.meta_subtitle_input.setText(app_info_item.get("subtitle", ""))
        app_store_version = metadata_config.get("app_store_version", {})
        self.meta_copyright_input.setText(
            app_store_version.get("copyright") or app_info_item.get("copyright", "")
        )
        self.meta_privacy_policy_url_input.setText(app_info_item.get("privacyPolicyUrl", ""))
        self.meta_privacy_choices_url_input.setText(app_info_item.get("privacyChoicesUrl", ""))
        self._set_category_combo(self.meta_primary_category_input, app_info.get("primaryCategory", ""))
        self._set_category_combo(self.meta_secondary_category_input, app_info.get("secondaryCategory", ""))
        self.meta_kids_age_band_input.setText(app_info.get("kidsAgeBand", ""))
        self._set_combo_data(
            self.meta_availability_mode_input,
            metadata_config.get("availability_mode", "ALL")
        )
        self.meta_selected_territories = metadata_config.get("availability_territories", [])
        self.meta_collects_data_checkbox.setChecked(bool(metadata_config.get("collects_data", False)))
        if self.meta_selected_territories:
            self.meta_select_territories_btn.setText(f"Выбрано стран: {len(self.meta_selected_territories)}")
        else:
            self.meta_select_territories_btn.setText("Выбрать страны…")
        self._on_availability_mode_changed()

        self.meta_review_first_name_input.setText(review.get("contactFirstName", ""))
        self.meta_review_last_name_input.setText(review.get("contactLastName", ""))
        self.meta_review_phone_input.setText(review.get("contactPhone", ""))
        self.meta_review_email_input.setText(review.get("contactEmail", ""))
        self.meta_review_notes_input.setPlainText(review.get("notes", ""))

        custom_requests = metadata_config.get("custom_requests", [])
        self.metadata_text.setPlainText(json.dumps(custom_requests, indent=2, ensure_ascii=False) if custom_requests else "")

    def _insert_metadata_example(self):
        example = {
            "version_localizations": [
                {
                    "locale": "en-US",
                    "description": "Full App Store description",
                    "keywords": "keyword1,keyword2,keyword3",
                    "promotionalText": "Short promo text",
                    "supportUrl": "https://example.com/support",
                    "marketingUrl": "https://example.com",
                }
            ],
            "app_info_localizations": [
                {
                    "locale": "en-US",
                    "name": "App Name",
                    "subtitle": "Short subtitle",
                    "privacyPolicyUrl": "https://example.com/privacy",
                    "privacyChoicesUrl": "https://example.com/privacy-choices"
                }
            ],
            "app_info": {
                "primaryCategory": "6016",
                "secondaryCategory": "",
                "kidsAgeBand": "FIVE_AND_UNDER"
            },
            "age_rating_declaration": default_age_rating_declaration(),
            "content_rights_declaration": DEFAULT_CONTENT_RIGHTS_DECLARATION,
            "availability_mode": "ALL",
            "availability_territories": [],
            "collects_data": False,
            "app_store_version": {
                "copyright": "© Your Developer Name"
            },
            "app_review_detail": {
                "contactFirstName": "John",
                "contactLastName": "Appleseed",
                "contactPhone": "+1 555 0100",
                "contactEmail": "review@example.com",
                "notes": "Notes for App Review team"
            },
            "custom_requests": []
        }
        self._apply_metadata_config_to_gui(example)
        self._log("Пример метаданных заполнен в GUI-полях.")

    def _build_metadata_config_from_gui(self):
        locale = self.meta_locale_combo.currentData()
        include_whats_new = self.meta_include_whats_new_checkbox.isChecked()
        version_item = {
            "locale": locale,
            "description": self._text_edit_text(self.meta_description_input),
            "keywords": self._line_text(self.meta_keywords_input),
            "promotionalText": self._text_edit_text(self.meta_promotional_text_input),
            "supportUrl": self._line_text(self.meta_support_url_input),
            "marketingUrl": self._line_text(self.meta_marketing_url_input),
        }
        if include_whats_new:
            whats_new_text = self._text_edit_text(self.meta_whats_new_input)
            if whats_new_text:
                version_item["whatsNew"] = whats_new_text
        version_item = {k: v for k, v in version_item.items() if k == "locale" or v}

        app_info_item = {
            "locale": locale,
            "name": self._line_text(self.meta_name_input),
            "subtitle": self._line_text(self.meta_subtitle_input),
            "privacyPolicyUrl": self._line_text(self.meta_privacy_policy_url_input),
            "privacyChoicesUrl": self._line_text(self.meta_privacy_choices_url_input)
        }
        app_info_item = {k: v for k, v in app_info_item.items() if k == "locale" or v}

        app_store_version = {}
        copyright_text = self._line_text(self.meta_copyright_input)
        if copyright_text:
            app_store_version["copyright"] = copyright_text

        app_info = {
            "primaryCategory": self._line_text(self.meta_primary_category_input),
            "secondaryCategory": self._line_text(self.meta_secondary_category_input),
            "kidsAgeBand": self._line_text(self.meta_kids_age_band_input)
        }
        # AI/ручной orphan-пункт мог оставить имя вместо ID — нормализуем перед upload.
        for field in ("primaryCategory", "secondaryCategory"):
            raw = app_info.get(field, "")
            gui_id = normalize_gui_category_id(raw)
            if raw and gui_id:
                app_info[field] = gui_id
            elif raw and not gui_id:
                self._log(f"⚠️ {field} '{raw}' не распознан. Поле будет пропущено.")
                app_info[field] = ""
        valid_category_ids = {cat_id for cat_id, _ in APP_CATEGORY_OPTIONS if cat_id}
        valid_category_ids.update(ITUNES_GENRE_TO_APP_CATEGORY.values())
        for field in ("primaryCategory", "secondaryCategory"):
            cat_id = app_info.get(field)
            if cat_id and cat_id not in valid_category_ids and not resolve_app_category_id(cat_id):
                self._log(f"⚠️ {field} '{cat_id}' не найден в списке App Store категорий. Поле будет пропущено.")
                app_info[field] = ""
        primary = resolve_app_category_id(app_info.get("primaryCategory", ""))
        secondary = resolve_app_category_id(app_info.get("secondaryCategory", ""))
        if primary and secondary and primary == secondary:
            self._log("⚠️ Primary и Secondary category совпадают. Secondary будет пропущена.")
            app_info["secondaryCategory"] = ""
        app_info = {k: v for k, v in app_info.items() if v}

        app_review_detail = {
            "contactFirstName": self._line_text(self.meta_review_first_name_input),
            "contactLastName": self._line_text(self.meta_review_last_name_input),
            "contactPhone": normalize_review_phone(self._line_text(self.meta_review_phone_input)),
            "contactEmail": self._line_text(self.meta_review_email_input),
            "notes": self._text_edit_text(self.meta_review_notes_input)
        }
        app_review_detail = {k: v for k, v in app_review_detail.items() if v not in (None, "")}

        custom_requests_text = self._text_edit_text(self.metadata_text)
        custom_requests = []
        if custom_requests_text:
            custom_requests = json.loads(custom_requests_text)
            if isinstance(custom_requests, dict):
                custom_requests = [custom_requests]
            if not isinstance(custom_requests, list):
                raise ValueError("custom_requests должен быть JSON-массивом или одним JSON-объектом.")

        metadata_config = {}
        if len(version_item) > 1:
            metadata_config["version_localizations"] = [version_item]
        if include_whats_new:
            metadata_config["include_whats_new"] = True
        if len(app_info_item) > 1:
            metadata_config["app_info_localizations"] = [app_info_item]
        if app_info:
            metadata_config["app_info"] = app_info
        metadata_config["age_rating_declaration"] = default_age_rating_declaration()
        metadata_config["content_rights_declaration"] = DEFAULT_CONTENT_RIGHTS_DECLARATION
        if app_store_version:
            metadata_config["app_store_version"] = app_store_version
        if app_review_detail:
            metadata_config["app_review_detail"] = app_review_detail
        if custom_requests:
            metadata_config["custom_requests"] = custom_requests
        return metadata_config

    def _collect_metadata_for_jira(self):
        app_id = self.app_input.text().strip()
        category_id = self._line_text(self.meta_primary_category_input)
        age_raw = self._line_text(self.meta_kids_age_band_input)
        return {
            "app_name": self.meta_name_input.text().strip() or "—",
            "locale": self.meta_locale_combo.currentData() or "en-US",
            "description": self._text_edit_text(self.meta_description_input),
            "subtitle": self._line_text(self.meta_subtitle_input),
            "keywords": self._line_text(self.meta_keywords_input),
            "support_url": self._line_text(self.meta_support_url_input),
            "privacy_policy_url": self._line_text(self.meta_privacy_policy_url_input),
            "app_id": app_id,
            "app_store_link": f"https://apps.apple.com/app/id{app_id}" if app_id else "",
            "primary_category": category_id,
            "jira_category": self._jira_category_value(category_id, self.meta_primary_category_input.currentText()),
            "jira_age": self._jira_age_value(age_raw),
        }

    def _jira_category_value(self, category_id, category_label):
        raw = str(category_id or "").strip()
        if raw in JIRA_CATEGORY_VALUES:
            return JIRA_CATEGORY_VALUES[raw]

        resolved = resolve_app_category_id(raw)
        if resolved in JIRA_CATEGORY_VALUES:
            return JIRA_CATEGORY_VALUES[resolved]

        label = str(category_label or "").split("(", 1)[0].strip()
        if not label or label == "Не выбрано":
            return ""
        if label.startswith(("App ", "Game ")):
            return label
        return f"App {label}"

    def _jira_age_value(self, age_raw):
        raw = str(age_raw or "").strip().upper()
        return JIRA_AGE_VALUES.get(raw, raw if raw in {"+4", "+9", "+13", "+16", "+18"} else "")

    def _update_jira_issue(self):
        base_url = self.jira_base_url_input.text().strip()
        email = self.jira_email_input.text().strip()
        api_token = self.jira_api_token_input.text().strip()
        issue_key = self.jira_issue_key_input.text().strip()

        if not all([base_url, email, api_token, issue_key]):
            self._log("⚠️ Заполните Jira: Base URL, Email, API Token и Issue в настройках API")
            return

        metadata = self._collect_metadata_for_jira()
        self.btn_create_jira.setEnabled(False)
        self._log(f"Обновляю Jira карточку {issue_key}...")
        self.jira_worker = JiraWorker(base_url, email, api_token, issue_key, metadata)
        self.jira_worker.log_msg.connect(self._log)
        self.jira_worker.success.connect(self._on_jira_success)
        self.jira_worker.error.connect(lambda _: self.btn_create_jira.setEnabled(True))
        self.jira_worker.finished.connect(lambda: self.btn_create_jira.setEnabled(True))
        self.jira_worker.start()

    def _on_jira_success(self, issue_url):
        self._log(f"✅ Jira карточка обновлена: {issue_url}")
        QDesktopServices.openUrl(QUrl(issue_url))

    def _check_length(self, label, text, limit, results):
        length = len(text)
        if not text:
            results.append(f"⚠️ {label}: пусто")
        elif length <= limit:
            results.append(f"✅ {label}: {length}/{limit}")
        else:
            results.append(f"❌ {label}: {length}/{limit}")

    def _preview_metadata(self):
        try:
            metadata_config = self._build_metadata_config_from_gui()
        except json.JSONDecodeError as e:
            self._log(f"Ошибка JSON в custom_requests: строка {e.lineno}, колонка {e.colno}: {e.msg}")
            return
        except ValueError as e:
            self._log(f"Ошибка custom_requests: {e}")
            return

        self._log("=== PREVIEW & VALIDATE МЕТАДАННЫХ ===")
        if not metadata_config:
            self._log("❌ Нет данных для отправки. Заполните хотя бы одно поле.")
            return

        description = self._text_edit_text(self.meta_description_input)
        keywords = self._line_text(self.meta_keywords_input)
        subtitle = self._line_text(self.meta_subtitle_input)
        promo = self._text_edit_text(self.meta_promotional_text_input)
        notes = self._text_edit_text(self.meta_review_notes_input)
        primary_category = self._line_text(self.meta_primary_category_input)

        results = []
        self._check_length("Subtitle", subtitle, 30, results)
        self._check_length("Keywords", keywords, 100, results)
        self._check_length("Promotional Text", promo, 170, results)
        self._check_length("Description", description, 4000, results)
        if primary_category:
            results.append(f"✅ Primary Category ID: {primary_category}")
        else:
            results.append("⚠️ Primary Category не выбрана")
        if notes:
            results.append(f"✅ App Review notes: {len(notes)} chars")

        combined_raw = "\n".join([description, keywords, subtitle, promo])
        combined_text = combined_raw.lower()
        for word in BANNED_ASO_WORDS:
            if word in combined_text:
                results.append(f"⚠️ Найдено banned word: {word}")
        if any(dash in combined_raw for dash in ["—", "–", "-"]):
            results.append("⚠️ Найдены dash символы, хотя ASO-промт просит No dashes")
        if re.search(r"(^|\n)\s*[-•*]\s+", description):
            results.append("⚠️ Description похож на bullet list, а промт просит narrative без bullet points")
        if "download now" in combined_text:
            results.append("⚠️ Найдено клише Download now")
        if keywords:
            if keywords != keywords.lower():
                results.append("⚠️ Keywords лучше держать lowercase")
            if " " in keywords:
                results.append("⚠️ Keywords содержат пробелы — для ASO чаще нужен плотный comma-separated формат")
            keyword_items = _split_keyword_parts(keywords)
            duplicates = sorted({item for item in keyword_items if keyword_items.count(item) > 1})
            if duplicates:
                results.append(f"⚠️ Keywords имеют дубли: {', '.join(duplicates)}")
            generic_words = {"best", "top", "easy", "fast", "free", "app", "simple"}
            generic_hits = sorted(generic_words.intersection(keyword_items))
            if generic_hits:
                results.append(f"⚠️ Keywords содержат generic fillers: {', '.join(generic_hits)}")
            app_name_words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", self._line_text(self.meta_name_input)) if len(w) > 2}
            subtitle_words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", subtitle) if len(w) > 2}
            excluded_hits = sorted((app_name_words | subtitle_words).intersection(keyword_items))
            if excluded_hits:
                results.append(f"⚠️ Keywords повторяют слова из app name/subtitle: {', '.join(excluded_hits)}")
        for url_label, url_value in [
            ("Support URL", self._line_text(self.meta_support_url_input)),
            ("Marketing URL", self._line_text(self.meta_marketing_url_input)),
            ("Privacy Policy URL", self._line_text(self.meta_privacy_policy_url_input)),
        ]:
            if url_value and not url_value.startswith(("http://", "https://")):
                results.append(f"⚠️ {url_label} лучше начинать с https:// (будет добавлено автоматически)")

        app_name = self._line_text(self.meta_name_input)
        if app_name:
            self._check_length("App name", app_name, 30, results)
        copyright_text = self._line_text(self.meta_copyright_input)
        if copyright_text:
            results.append(f"✅ Copyright: {len(copyright_text)} chars (уровень версии, не локаль)")
        phone = self._line_text(self.meta_review_phone_input)
        if phone:
            normalized_phone = normalize_review_phone(phone)
            if normalized_phone != phone.strip():
                results.append(f"⚠️ Телефон будет нормализован: {normalized_phone}")
            else:
                results.append(f"✅ Contact phone: {normalized_phone}")
        email = self._line_text(self.meta_review_email_input)
        if email and "@" not in email:
            results.append("⚠️ Contact email выглядит некорректно")
        elif email:
            results.append(f"✅ Contact email: {email}")
        kids_band = self._line_text(self.meta_kids_age_band_input)
        if kids_band and kids_band not in {"", "FIVE_AND_UNDER", "SIX_TO_EIGHT", "NINE_TO_ELEVEN"}:
            results.append(
                f"⚠️ kidsAgeBand '{kids_band}' неверный — используйте FIVE_AND_UNDER / SIX_TO_EIGHT / NINE_TO_ELEVEN"
            )
        if metadata_config.get("age_rating_declaration"):
            results.append("✅ Age Ratings будут отправлены: Step 1 = NO, остальные content-поля = NONE")
        if metadata_config.get("content_rights_declaration") == DEFAULT_CONTENT_RIGHTS_DECLARATION:
            results.append("✅ Content Rights будет отправлен: third-party content = NO")
        if not self.meta_include_whats_new_checkbox.isChecked():
            results.append("ℹ️ What's New не будет отправлен (первая версия / галочка выключена)")

        for line in results:
            self._log(line)
        self._log("Payload preview:")
        self._log(json.dumps(metadata_config, ensure_ascii=False, indent=2))

    def _on_availability_mode_changed(self):
        is_selected_mode = (self.meta_availability_mode_input.currentData() == "SELECTED")
        self.meta_select_territories_btn.setVisible(is_selected_mode)

    def _fetch_territories_for_selector(self):
        issuer = self.issuer_input.text().strip()
        key_id = self.key_input.text().strip()
        p8_path = self.p8_path_input.text().strip()
        app_id = self.app_input.text().strip()
        if not all([issuer, key_id, p8_path, app_id]):
            raise Exception("Для выбора стран заполните Issuer ID, Key ID, App ID и путь к .p8.")
        client = ASCClient(issuer, key_id, p8_path, app_id, self._log)
        return client.get_all_territory_ids()

    def _open_territories_selector(self):
        try:
            territory_ids = self._fetch_territories_for_selector()
        except Exception as e:
            QMessageBox.warning(self, "Availability", f"Не удалось загрузить страны: {e}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор стран доступности")
        dialog.resize(420, 560)
        layout = QVBoxLayout(dialog)
        hint = QLabel("По умолчанию включены все страны. Снимайте галочки с ненужных.")
        layout.addWidget(hint)

        list_widget = QListWidget(dialog)
        selected_set = set(self.meta_selected_territories or territory_ids)
        for territory_id in territory_ids:
            item = QListWidgetItem(territory_id, list_widget)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if territory_id in selected_set else Qt.Unchecked)
        layout.addWidget(list_widget)

        buttons_row = QHBoxLayout()
        btn_all = QPushButton("Включить все")
        btn_none = QPushButton("Снять все")
        btn_ok = QPushButton("OK")
        btn_cancel = QPushButton("Отмена")
        buttons_row.addWidget(btn_all)
        buttons_row.addWidget(btn_none)
        buttons_row.addStretch(1)
        buttons_row.addWidget(btn_cancel)
        buttons_row.addWidget(btn_ok)
        layout.addLayout(buttons_row)

        def set_all(state):
            for i in range(list_widget.count()):
                list_widget.item(i).setCheckState(Qt.Checked if state else Qt.Unchecked)

        btn_all.clicked.connect(lambda: set_all(True))
        btn_none.clicked.connect(lambda: set_all(False))
        btn_ok.clicked.connect(dialog.accept)
        btn_cancel.clicked.connect(dialog.reject)

        if dialog.exec() == QDialog.Accepted:
            selected = []
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item.checkState() == Qt.Checked:
                    selected.append(item.text())
            if not selected:
                QMessageBox.warning(self, "Availability", "Нужно выбрать хотя бы одну страну.")
                return
            self.meta_selected_territories = selected
            self.meta_select_territories_btn.setText(f"Выбрано стран: {len(selected)}")

    def _sync_marketing_privacy_urls(self, source):
        """Дублирует Marketing URL <-> Privacy Policy URL, чтобы заполнять одно поле вместо двух."""
        if getattr(self, "_syncing_marketing_privacy_urls", False):
            return
        marketing_widget = getattr(self, "meta_marketing_url_input", None)
        privacy_widget = getattr(self, "meta_privacy_policy_url_input", None)
        if marketing_widget is None or privacy_widget is None:
            return

        marketing = marketing_widget.text().strip()
        privacy = privacy_widget.text().strip()
        self._syncing_marketing_privacy_urls = True
        try:
            if source == "marketing" and marketing and marketing != privacy:
                privacy_widget.setText(marketing)
            elif source == "privacy" and privacy and privacy != marketing:
                marketing_widget.setText(privacy)
        finally:
            self._syncing_marketing_privacy_urls = False

    def _on_storepal_token_edited(self):
        token = self.storepal_token_input.text().strip()
        if token:
            try:
                save_storepal_credentials(token)
            except Exception as e:
                self._log(f"Не удалось сохранить ~/.storepal/credentials.json: {e}")
        self._save_env_to_file()

    def _import_storepal_credentials(self):
        token = ""
        try:
            with open(STOREPAL_CREDENTIALS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            token = (data.get("token") or "").strip()
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            token = ""
        if not token:
            QMessageBox.information(
                self,
                "StorePal",
                f"Файл {STOREPAL_CREDENTIALS_PATH} не найден.\n"
                "Нажмите StorePal Login или выполните: npx storepal auth login",
            )
            return
        self.storepal_token_input.setText(token)
        self._save_env_to_file()
        self._log("StorePal token импортирован из ~/.storepal/credentials.json")
        QMessageBox.information(self, "StorePal", "Token импортирован.")

    def _start_storepal_login(self):
        if hasattr(self, "storepal_login_worker") and self.storepal_login_worker is not None and self.storepal_login_worker.isRunning():
            QMessageBox.information(self, "StorePal", "Авторизация уже запущена. Завершите её в браузере.")
            return
        self.btn_storepal_login.setEnabled(False)
        self._log("StorePal: запуск browser login...")
        self.storepal_login_worker = StorePalLoginWorker()
        self.storepal_login_worker.log_msg.connect(self._log)
        self.storepal_login_worker.login_success.connect(self._on_storepal_login_success)
        self.storepal_login_worker.login_failed.connect(self._on_storepal_login_failed)
        self.storepal_login_worker.finished.connect(lambda: self.btn_storepal_login.setEnabled(True))
        self.storepal_login_worker.start()

    def _on_storepal_login_success(self, token):
        self.storepal_token_input.setText(token)
        self._save_env_to_file()
        QMessageBox.information(self, "StorePal", "Вход выполнен. Token сохранён.")

    def _on_storepal_login_failed(self, message):
        self._log(f"StorePal login error: {message}")
        QMessageBox.warning(self, "StorePal Login", message)

    def _gitlab_credentials(self):
        base = ""
        token = ""
        if hasattr(self, "gitlab_url_input"):
            base = self.gitlab_url_input.text().strip()
        if hasattr(self, "gitlab_token_input"):
            token = self.gitlab_token_input.text().strip()
        return (base or resolve_gitlab_base_url()).rstrip("/"), token or resolve_gitlab_token()

    def _current_app_name_for_gitlab(self, app_name=None):
        name = (app_name or "").strip()
        if not name and hasattr(self, "meta_name_input"):
            name = self._line_text(self.meta_name_input)
        if not name:
            name = (getattr(self, "_summary_app_name", "") or "").strip()
        return name

    def _fetch_gitlab_brief(self, app_name=None, force=False, only_if_empty=False, on_done=None):
        """Тянет ТЗ из GitLab. on_done(success: bool) вызывается по завершении."""
        if only_if_empty and hasattr(self, "ai_app_context_input"):
            if self._text_edit_text(self.ai_app_context_input).strip():
                if on_done:
                    on_done(True)
                return False

        name = self._current_app_name_for_gitlab(app_name)
        base, token = self._gitlab_credentials()
        if not token:
            msg = "GitLab token не задан (Настройки API)."
            if hasattr(self, "gitlab_brief_status"):
                self.gitlab_brief_status.setText(msg)
            if not force:
                self._log(f"GitLab brief skip: {msg}")
            else:
                self._log(f"⚠️ {msg}")
                QMessageBox.warning(self, "GitLab", msg)
            if on_done:
                on_done(False)
            return False
        if not name:
            msg = "Нет Name приложения для поиска репо в GitLab."
            if hasattr(self, "gitlab_brief_status"):
                self.gitlab_brief_status.setText(msg)
            if force:
                QMessageBox.warning(self, "GitLab", msg)
            if on_done:
                on_done(False)
            return False

        if self._worker_is_running("gitlab_brief_worker"):
            if on_done:
                # предыдущий запрос ещё идёт — не блокируем цепочку вечно
                self._log("GitLab: предыдущий запрос ТЗ ещё выполняется...")
            return False

        self._gitlab_brief_on_done = on_done
        if hasattr(self, "btn_fetch_gitlab_brief"):
            self.btn_fetch_gitlab_brief.setEnabled(False)
        if hasattr(self, "gitlab_brief_status"):
            self.gitlab_brief_status.setText(f"Ищу ТЗ для «{name}»...")
        worker = GitLabBriefWorker(name, token=token, base_url=base)
        worker.log_msg.connect(self._log)
        worker.brief_ready.connect(self._on_gitlab_brief_ready)
        worker.brief_failed.connect(self._on_gitlab_brief_failed)
        worker.finished.connect(
            lambda: self.btn_fetch_gitlab_brief.setEnabled(True)
            if hasattr(self, "btn_fetch_gitlab_brief") else None
        )
        self._track_worker("gitlab_brief_worker", worker)
        worker.start()
        return True

    def _ensure_gitlab_brief_then(self, callback, only_if_empty=True):
        """Если ТЗ пустое — подтянуть из GitLab, затем callback()."""
        def _done(ok):
            if callback:
                callback()

        if only_if_empty and hasattr(self, "ai_app_context_input"):
            if self._text_edit_text(self.ai_app_context_input).strip():
                _done(True)
                return
        base, token = self._gitlab_credentials()
        if not token or not self._current_app_name_for_gitlab():
            _done(False)
            return
        started = self._fetch_gitlab_brief(
            force=False,
            only_if_empty=only_if_empty,
            on_done=_done,
        )
        if not started and only_if_empty and self._text_edit_text(self.ai_app_context_input).strip():
            _done(True)
        elif not started:
            # не удалось стартовать worker — всё равно идём дальше (AI сам ругнётся если пусто)
            _done(False)

    def _on_gitlab_brief_ready(self, result):
        result = result or {}
        brief = (result.get("brief") or "").strip()
        project_path = result.get("project_path") or ""
        if brief and hasattr(self, "ai_app_context_input"):
            self.ai_app_context_input.setPlainText(brief)
        if hasattr(self, "gitlab_brief_status"):
            self.gitlab_brief_status.setText(
                f"✓ {project_path} · {len(brief)} симв."
            )
        on_done = getattr(self, "_gitlab_brief_on_done", None)
        self._gitlab_brief_on_done = None
        if on_done:
            on_done(True)

    def _on_gitlab_brief_failed(self, message):
        if hasattr(self, "gitlab_brief_status"):
            self.gitlab_brief_status.setText(f"⚠️ {message}")
        self._log(f"GitLab brief: {message}")
        on_done = getattr(self, "_gitlab_brief_on_done", None)
        self._gitlab_brief_on_done = None
        if on_done:
            on_done(False)

    def _refresh_accounts_roster_status(self):
        if not hasattr(self, "accounts_roster_status"):
            return
        accounts = load_accounts_roster()
        app_name = self._line_text(self.meta_name_input) if hasattr(self, "meta_name_input") else ""
        app_id = self.app_input.text().strip() if hasattr(self, "app_input") else ""
        match = find_account_for_app(app_id=app_id, app_name=app_name, accounts=accounts)
        if not accounts:
            self.accounts_roster_status.setText(
                "База аккаунтов пуста — импортируй CSV или вставь таблицу."
            )
        elif match:
            self.accounts_roster_status.setText(
                f"Аккаунтов: {len(accounts)} · для текущего «{match.get('app_name') or app_name}»: "
                f"{match.get('person_name') or '—'} / {match.get('phone') or '—'} / {match.get('email') or '—'}"
            )
        else:
            self.accounts_roster_status.setText(
                f"Аккаунтов: {len(accounts)} · для «{app_name or 'без Name'}» совпадений нет."
            )
        if hasattr(self, "accounts_roster_table"):
            self._refresh_accounts_roster_table()

    def _refresh_accounts_roster_table(self):
        if not hasattr(self, "accounts_roster_table"):
            return
        accounts = load_accounts_roster()
        query = ""
        if hasattr(self, "accounts_roster_search"):
            query = self.accounts_roster_search.text().strip().lower()

        rows = []
        for item in accounts:
            app_name = str(item.get("app_name") or "").strip()
            person = str(item.get("person_name") or "").strip()
            phone = str(item.get("phone") or "").strip()
            email = str(item.get("email") or "").strip()
            country = str(item.get("country") or "").strip()
            telegram = str(item.get("telegram") or "").strip()
            hay = " ".join([app_name, person, phone, email, country, telegram]).lower()
            if query and query not in hay:
                continue
            rows.append((app_name, person, phone, email, country, telegram))

        rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))
        table = self.accounts_roster_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for row_idx, values in enumerate(rows):
            for col_idx, value in enumerate(values):
                table.setItem(row_idx, col_idx, QTableWidgetItem(value))
        table.setSortingEnabled(True)
        if hasattr(self, "accounts_roster_status") and accounts:
            # не затирать match-статус полностью — только если нет текущего match-текста про «найдено»
            pass

    def _filter_accounts_roster_table(self, _text=None):
        self._refresh_accounts_roster_table()

    def _import_accounts_roster_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Импорт аккаунтов (CSV/TSV)",
            os.path.expanduser("~/Downloads"),
            "Tables (*.csv *.tsv *.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError as exc:
            QMessageBox.warning(self, "Импорт аккаунтов", f"Не удалось прочитать файл:\n{exc}")
            return
        self._ingest_accounts_roster_text(text, source=os.path.basename(path))

    def _paste_accounts_roster_from_clipboard(self):
        clip = QGuiApplication.clipboard()
        text = clip.text() if clip is not None else ""
        if not str(text or "").strip():
            QMessageBox.information(
                self,
                "Импорт аккаунтов",
                "Буфер пуст. В Google Sheets выдели строки → Cmd+C → снова нажми «Вставить таблицу».",
            )
            return
        self._ingest_accounts_roster_text(text, source="clipboard")

    def _ingest_accounts_roster_text(self, text, source="import"):
        parsed = _parse_roster_table_text(text)
        if not parsed:
            QMessageBox.warning(
                self,
                "Импорт аккаунтов",
                "Не удалось распознать таблицу.\n"
                "Нужны колонки: название приложения | имя | телефон | email/логин "
                "(как в твоей Google Sheets; колонка «Логин» = email).",
            )
            return
        existing = {}
        for item in load_accounts_roster():
            key = _normalize_app_name_key(item.get("app_name"))
            if not key:
                key = _normalize_app_id_digits(item.get("app_id")) or f"email:{item.get('email')}"
            existing[key] = item
        added = 0
        updated = 0
        for item in parsed:
            name_key = _normalize_app_name_key(item.get("app_name"))
            app_id = _normalize_app_id_digits(item.get("app_id"))
            key = name_key or app_id or (f"email:{item.get('email')}" if item.get("email") else "")
            if not key:
                continue
            if item.get("phone"):
                item["phone"] = _roster_phone_with_plus(item["phone"])
            if key in existing:
                merged = dict(existing[key])
                for field, value in item.items():
                    if str(value or "").strip():
                        merged[field] = value
                existing[key] = merged
                updated += 1
            else:
                existing[key] = item
                added += 1
        accounts = list(existing.values())
        path = save_accounts_roster(accounts)
        self._log(
            f"Аккаунты ({source}): +{added} новых, ~{updated} обновлено, всего {len(accounts)} → {path}"
        )
        self._apply_account_roster_for_current_app(silent=False)
        self._refresh_accounts_roster_status()
        QMessageBox.information(
            self,
            "Импорт аккаунтов",
            f"Импортировано: +{added}, обновлено: {updated}.\n"
            f"Всего в базе: {len(accounts)}.\n\n"
            "Дальше PPO сам ищет строку по названию приложения (Name) "
            "и подставляет имя / телефон(+) / email.",
        )

    def _apply_account_roster_for_current_app(self, silent=False):
        app_id = self.app_input.text().strip() if hasattr(self, "app_input") else ""
        app_name = self._line_text(self.meta_name_input) if hasattr(self, "meta_name_input") else ""
        # Если Name ещё пустой — попробуем summary/ASC name
        if not app_name:
            app_name = getattr(self, "_summary_app_name", "") or ""
        account = find_account_for_app(app_id=app_id, app_name=app_name)
        if not account:
            if not silent:
                self._log(f"Аккаунты: для «{app_name or app_id or '—'}» записи нет.")
            return False
        email = str(account.get("email") or "").strip()
        person = str(account.get("person_name") or "").strip()
        phone = _roster_phone_with_plus(account.get("phone") or "")
        roster_app_name = str(account.get("app_name") or "").strip()
        first_name, last_name = _split_person_name(person)
        changed = []
        if email and hasattr(self, "meta_review_email_input"):
            self.meta_review_email_input.setText(email)
            changed.append("email")
        if first_name and hasattr(self, "meta_review_first_name_input"):
            self.meta_review_first_name_input.setText(first_name)
            changed.append("first")
        if last_name and hasattr(self, "meta_review_last_name_input"):
            self.meta_review_last_name_input.setText(last_name)
            changed.append("last")
        if phone and hasattr(self, "meta_review_phone_input"):
            self.meta_review_phone_input.setText(phone)
            changed.append("phone")
        if roster_app_name and hasattr(self, "meta_name_input") and not self._line_text(self.meta_name_input):
            self.meta_name_input.setText(roster_app_name)
            changed.append("app_name")
        if hasattr(self, "ai_developer_name_input") and person:
            self.ai_developer_name_input.setText(person)
            changed.append("developer")
        if changed and not silent:
            self._log(
                f"Аккаунты: по названию «{roster_app_name or app_name}» → "
                f"{person or '—'} / {phone or '—'} / {email or '—'}"
            )
        self._refresh_accounts_roster_status()
        return True

    def _privacy_oneshot_context(self):
        self._apply_account_roster_for_current_app(silent=True)
        app_name = self._line_text(self.meta_name_input)
        email = self._line_text(self.meta_review_email_input)
        developer = (
            self._line_text(self.ai_developer_name_input)
            or self._line_text(self.meta_copyright_input).lstrip("© ").strip()
            or app_name
        )
        collects = bool(
            getattr(self, "meta_collects_data_checkbox", None)
            and self.meta_collects_data_checkbox.isChecked()
        )
        return {
            "app_name": app_name,
            "email": email,
            "developer": developer,
            "collects_data": collects,
            "uses_analytics": False,
            "uses_crash": False,
            "uses_ads": False,
            "uses_purchases": False,
            "description": self._text_edit_text(self.meta_description_input)[:280],
        }

    def _run_privacy_support_oneshot(self, chain_full_metadata=False):
        ctx = self._privacy_oneshot_context()
        if not ctx["app_name"] or not ctx["email"]:
            QMessageBox.warning(
                self,
                "Privacy + Support",
                "Заполните в Метаданных: Name и Review email — они нужны для страниц.\n"
                "В таблице аккаунтов email берётся из колонки «Логин» — переимпортируй CSV.",
            )
            return False
        if not self._has_complete_api_credentials():
            QMessageBox.warning(
                self,
                "Privacy + Support",
                "Заполните API credentials (Issuer / Key / App ID / .p8), чтобы сохранить URL в Apple.",
            )
            return False

        self._privacy_oneshot_ctx = ctx
        self._privacy_oneshot_backend = None
        self._privacy_oneshot_chain_full_metadata = bool(chain_full_metadata)

        token = ""
        if hasattr(self, "storepal_token_input"):
            token = self.storepal_token_input.text().strip()
        token = token or resolve_storepal_token()
        if token:
            self._start_privacy_oneshot_storepal(ctx, token)
            return True

        started = self._start_privacy_oneshot_local_random(ctx)
        if not started:
            self._privacy_oneshot_chain_full_metadata = False
        return started

    def _privacy_oneshot_local_credentials(self):
        form_id = ""
        if hasattr(self, "formspree_form_id_input"):
            form_id = self.formspree_form_id_input.text().strip()
        form_id = normalize_formspree_form_id(form_id or resolve_formspree_form_id())

        access_key = ""
        if hasattr(self, "web3forms_key_input"):
            access_key = self.web3forms_key_input.text().strip()
        access_key = normalize_web3forms_access_key(access_key or resolve_web3forms_access_key())
        return form_id, access_key

    def _start_privacy_oneshot_local_random(self, ctx):
        """Formspree / Web3Forms 50/50 (оба BrewPage ~30 дней). Returns True if started."""
        form_id, access_key = self._privacy_oneshot_local_credentials()
        prefer_formspree = secrets.randbelow(2) == 0
        if prefer_formspree:
            if form_id:
                self._start_privacy_oneshot_formspree(ctx, form_id)
                return True
            if access_key:
                self._start_privacy_oneshot_web3forms(ctx, access_key)
                return True
        else:
            if access_key:
                self._start_privacy_oneshot_web3forms(ctx, access_key)
                return True
            if form_id:
                self._start_privacy_oneshot_formspree(ctx, form_id)
                return True

        QMessageBox.warning(
            self,
            "Privacy + Support",
            "Нужен StorePal token или Formspree form id / Web3Forms key "
            "на вкладке «Настройки API».\n"
            "Порядок: StorePal → при лимитах Formspree/Web3Forms 50/50 (~30 дней).",
        )
        return False

    @staticmethod
    def _storepal_error_looks_like_limit(message):
        text = (message or "").lower()
        needles = (
            "limit",
            "quota",
            "plan",
            "upgrade",
            "maximum",
            "too many",
            "exceeded",
            "subscription",
            "billing",
            "payment",
            "402",
            "429",
            "403",
            "forbidden",
            "not allowed",
            "free tier",
            "free plan",
            "создайте app на",
            "через api недоступно",
            "api недоступн",
        )
        return any(n in text for n in needles)

    def _set_privacy_support_urls(self, privacy_url, support_url):
        if privacy_url and hasattr(self, "meta_privacy_policy_url_input"):
            self.meta_privacy_policy_url_input.setText(privacy_url)
        if support_url and hasattr(self, "meta_support_url_input"):
            self.meta_support_url_input.setText(support_url)
        if privacy_url and hasattr(self, "meta_marketing_url_input"):
            if not self._line_text(self.meta_marketing_url_input):
                self.meta_marketing_url_input.setText(privacy_url)

    def _check_privacy_support_urls_report(self):
        """Проверяет Privacy/Support URL. Возвращает (alive: bool, details: list[str])."""
        checks = [
            ("Privacy", self._line_text(self.meta_privacy_policy_url_input) if hasattr(self, "meta_privacy_policy_url_input") else ""),
            ("Support", self._line_text(self.meta_support_url_input) if hasattr(self, "meta_support_url_input") else ""),
        ]
        details = []
        alive = True
        for label, url in checks:
            ok, reason = check_public_url_alive(url)
            short = (url[:64] + "…") if len(url) > 64 else (url or "—")
            details.append(f"{label}: {short} → {reason}")
            if not ok:
                alive = False
        return alive, details

    def _ensure_privacy_support_urls_before_upload(self):
        """
        Если URL мёртвые/пустые — пересоздаёт Privacy+Support и потом продолжит upload.
        True = можно грузить сейчас; False = ждём async repair или стоп.
        """
        alive, details = self._check_privacy_support_urls_report()
        for line in details:
            self._log(f"URL check: {line}")
        if alive:
            self._privacy_url_repair_attempted = False
            return True

        if getattr(self, "_privacy_url_repair_attempted", False):
            self._privacy_oneshot_repair_then_upload = False
            self._privacy_url_repair_attempted = False
            self._log("⚠️ Privacy/Support URL всё ещё недоступны после пересоздания — загрузка отменена.")
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            if hasattr(self, "btn_privacy_support_oneshot"):
                self.btn_privacy_support_oneshot.setEnabled(True)
            QMessageBox.warning(
                self,
                "Privacy / Support URL",
                "Ссылки Privacy/Support недоступны (404/таймаут), даже после пересоздания.\n"
                "Проверьте StorePal / Formspree / Web3Forms и хостинг.",
            )
            return False

        self._privacy_url_repair_attempted = True
        self._privacy_oneshot_repair_then_upload = True
        self._log("⚠️ Privacy/Support URL мертвы или пусты → пересоздаю страницы, затем загрузка...")
        if not self._run_privacy_support_oneshot(chain_full_metadata=False):
            self._privacy_oneshot_repair_then_upload = False
            self._privacy_url_repair_attempted = False
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            return False
        return False

    def _upload_full_metadata_to_apple(self, skip_url_check=False):
        """Полная загрузка метаданных (включая Privacy/Support URL) в App Store Connect."""
        if not skip_url_check:
            if not self._ensure_privacy_support_urls_before_upload():
                return

        try:
            metadata_config = self._build_metadata_config_from_gui()
        except (json.JSONDecodeError, ValueError) as e:
            self._log(f"Ошибка сборки метаданных после Privacy/Support: {e}")
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            QMessageBox.warning(self, "Метаданные", str(e))
            return
        if not metadata_config:
            self._log("Ошибка: после Privacy/Support нечего грузить в метаданные.")
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            return

        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Нет API credentials для сохранения метаданных в Apple.")
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            return

        if hasattr(self, "start_btn"):
            self.start_btn.setEnabled(False)
        if hasattr(self, "btn_privacy_support_oneshot"):
            self.btn_privacy_support_oneshot.setEnabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Метаданные: 0%")
        self._privacy_url_repair_attempted = False
        self._log("URL живые → загружаю все метаданные в App Store Connect...")
        worker = MetadataWorker(api_creds, metadata_config)
        worker.log_msg.connect(self._log)
        worker.progress_update.connect(self._update_progress)
        worker.finished_ok.connect(self._on_metadata_upload_finished)
        worker.finished.connect(
            lambda: (
                self.start_btn.setEnabled(True) if hasattr(self, "start_btn") else None,
                self.btn_privacy_support_oneshot.setEnabled(True)
                if hasattr(self, "btn_privacy_support_oneshot") else None,
            )
        )
        self._track_worker("privacy_oneshot_worker", worker)
        worker.start()

    def _upload_privacy_support_urls_to_apple(self):
        locale = self.meta_locale_combo.currentData() if hasattr(self, "meta_locale_combo") else None
        if not locale:
            self._log("Privacy oneshot: нет locale в GUI — URL в Apple не отправлены.")
            return
        privacy = self._line_text(self.meta_privacy_policy_url_input)
        support = self._line_text(self.meta_support_url_input)
        marketing = self._line_text(self.meta_marketing_url_input)
        if not privacy and not support:
            self._log("Privacy oneshot: пустые URL — upload пропущен.")
            return

        metadata_config = {}
        version_item = {"locale": locale}
        if support:
            version_item["supportUrl"] = support
        if marketing:
            version_item["marketingUrl"] = marketing
        if len(version_item) > 1:
            metadata_config["version_localizations"] = [version_item]
        app_info_item = {"locale": locale}
        if privacy:
            app_info_item["privacyPolicyUrl"] = privacy
        choices = self._line_text(self.meta_privacy_choices_url_input)
        if choices:
            app_info_item["privacyChoicesUrl"] = choices
        if len(app_info_item) > 1:
            metadata_config["app_info_localizations"] = [app_info_item]

        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Privacy oneshot: нет API credentials для сохранения в Apple.")
            return
        if hasattr(self, "btn_privacy_support_oneshot"):
            self.btn_privacy_support_oneshot.setEnabled(False)
        self._log("Privacy oneshot: сохраняю Privacy/Support URL в App Store Connect...")
        worker = MetadataWorker(api_creds, metadata_config)
        worker.log_msg.connect(self._log)
        worker.progress_update.connect(self._update_progress)
        worker.finished_ok.connect(self._on_privacy_oneshot_upload_finished)
        worker.finished.connect(
            lambda: self.btn_privacy_support_oneshot.setEnabled(True)
            if hasattr(self, "btn_privacy_support_oneshot") else None
        )
        self._track_worker("privacy_oneshot_worker", worker)
        worker.start()

    def _on_privacy_oneshot_upload_finished(self, success):
        if success:
            self._log("✅ Privacy + Support URL сохранены в Apple.")
            QMessageBox.information(
                self,
                "Privacy + Support",
                "Страницы созданы, URL вписаны в App Info и сохранены в App Store Connect.",
            )
        else:
            self._log("⚠️ Privacy oneshot: URL в GUI есть, но сохранение в Apple не удалось.")
            QMessageBox.warning(
                self,
                "Privacy + Support",
                "Страницы созданы и URL подставлены в GUI, но загрузка в Apple не удалась. "
                "Проверьте лог и нажмите «Загрузить метаданные».",
            )

    def _start_privacy_oneshot_storepal(self, ctx, token):
        privacy_md = build_storepal_privacy_markdown(
            app_name=ctx["app_name"],
            contact_email=ctx["email"],
            developer_name=ctx["developer"],
            collects_data=ctx["collects_data"],
            uses_analytics=ctx["uses_analytics"],
            uses_ads=ctx["uses_ads"],
            uses_purchases=ctx["uses_purchases"],
            uses_crash=ctx["uses_crash"],
        )
        options = {
            "token": token,
            "mode": "create",
            "app_name": ctx["app_name"],
            "slug": storepal_slugify(ctx["app_name"]),
            "description": ctx["description"],
            "privacy_markdown": privacy_md,
            "update_privacy": True,
        }
        self._privacy_oneshot_backend = "storepal"
        if hasattr(self, "btn_privacy_support_oneshot"):
            self.btn_privacy_support_oneshot.setEnabled(False)
        self._log(f"Privacy oneshot (StorePal): создаю страницы для /{options['slug']}...")
        worker = StorePalPagesWorker(options)
        worker.log_msg.connect(self._log)
        worker.pages_ready.connect(self._on_privacy_oneshot_pages_ready)
        worker.pages_failed.connect(self._on_privacy_oneshot_pages_failed)
        self._track_worker("privacy_pages_worker", worker)
        worker.start()

    def _start_privacy_oneshot_formspree(self, ctx, form_id):
        options = {
            "formspree_form_id": form_id,
            "app_name": ctx["app_name"],
            "email": ctx["email"],
            "developer": ctx["developer"],
            "collects_data": ctx["collects_data"],
            "uses_analytics": ctx["uses_analytics"],
            "uses_crash": ctx["uses_crash"],
            "uses_ads": ctx["uses_ads"],
            "uses_purchases": ctx["uses_purchases"],
        }
        self._privacy_oneshot_backend = "formspree"
        if hasattr(self, "btn_privacy_support_oneshot"):
            self.btn_privacy_support_oneshot.setEnabled(False)
        self._log("Privacy oneshot (Formspree): создаю Privacy/Support...")
        worker = LocalPrivacyFormspreeWorker(options)
        worker.log_msg.connect(self._log)
        worker.pages_ready.connect(self._on_privacy_oneshot_pages_ready)
        worker.pages_failed.connect(self._on_privacy_oneshot_pages_failed)
        self._track_worker("privacy_pages_worker", worker)
        worker.start()

    def _start_privacy_oneshot_web3forms(self, ctx, access_key):
        options = {
            "web3forms_access_key": access_key,
            "app_name": ctx["app_name"],
            "email": ctx["email"],
            "developer": ctx["developer"],
            "collects_data": ctx["collects_data"],
            "uses_analytics": ctx["uses_analytics"],
            "uses_crash": ctx["uses_crash"],
            "uses_ads": ctx["uses_ads"],
            "uses_purchases": ctx["uses_purchases"],
        }
        self._privacy_oneshot_backend = "web3forms"
        if hasattr(self, "btn_privacy_support_oneshot"):
            self.btn_privacy_support_oneshot.setEnabled(False)
        self._log("Privacy oneshot (Web3Forms): создаю Privacy/Support...")
        worker = LocalPrivacyWeb3FormsWorker(options)
        worker.log_msg.connect(self._log)
        worker.pages_ready.connect(self._on_privacy_oneshot_pages_ready)
        worker.pages_failed.connect(self._on_privacy_oneshot_pages_failed)
        self._track_worker("privacy_pages_worker", worker)
        worker.start()

    def _on_privacy_oneshot_pages_ready(self, result):
        result = result or {}
        urls = result.get("urls") or {}
        privacy_url = urls.get("privacy") or result.get("privacy") or ""
        support_url = urls.get("support") or result.get("support") or ""
        self._set_privacy_support_urls(privacy_url, support_url)
        self._log(f"Privacy oneshot privacy: {privacy_url}")
        self._log(f"Privacy oneshot support: {support_url}")
        repair_upload = bool(getattr(self, "_privacy_oneshot_repair_then_upload", False))
        self._privacy_oneshot_repair_then_upload = False
        chain_full = bool(getattr(self, "_privacy_oneshot_chain_full_metadata", False))
        self._privacy_oneshot_chain_full_metadata = False
        if repair_upload:
            self._log("Privacy/Support пересозданы → повторная загрузка метаданных в Apple...")
            self._upload_full_metadata_to_apple(skip_url_check=False)
            return
        if chain_full:
            self._log("Privacy/Support готовы → проверяю ТЗ (GitLab) → генерация AI...")
            self._ensure_gitlab_brief_then(
                lambda: self._continue_chain_after_brief(),
                only_if_empty=True,
            )
        else:
            self._upload_privacy_support_urls_to_apple()

    def _continue_chain_after_brief(self):
        if not self._generate_ai_metadata("all", chain_upload=True):
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            if hasattr(self, "btn_privacy_support_oneshot"):
                self.btn_privacy_support_oneshot.setEnabled(True)

    def _on_privacy_oneshot_pages_failed(self, message):
        backend = getattr(self, "_privacy_oneshot_backend", None)
        ctx = getattr(self, "_privacy_oneshot_ctx", None) or {}
        if backend == "storepal" and self._storepal_error_looks_like_limit(message):
            form_id, access_key = self._privacy_oneshot_local_credentials()
            if form_id or access_key:
                self._log(
                    f"⚠️ StorePal лимит/план ({message}). "
                    "Fallback → Formspree/Web3Forms 50/50..."
                )
                if self._start_privacy_oneshot_local_random(ctx):
                    return

        repair_upload = bool(getattr(self, "_privacy_oneshot_repair_then_upload", False))
        self._privacy_oneshot_repair_then_upload = False
        self._privacy_url_repair_attempted = False
        chain_full = bool(getattr(self, "_privacy_oneshot_chain_full_metadata", False))
        self._privacy_oneshot_chain_full_metadata = False
        if hasattr(self, "btn_privacy_support_oneshot"):
            self.btn_privacy_support_oneshot.setEnabled(True)
        if (chain_full or repair_upload) and hasattr(self, "start_btn"):
            self.start_btn.setEnabled(True)
        self._log(f"Privacy oneshot error: {message}")
        QMessageBox.warning(self, "Privacy + Support", message)

    def _open_storepal_pages_dialog(self):
        token = resolve_storepal_token()
        if hasattr(self, "storepal_token_input") and self.storepal_token_input.text().strip():
            token = self.storepal_token_input.text().strip()
        if not token:
            QMessageBox.warning(
                self,
                "StorePal",
                "Сначала войдите: вкладка «Настройки API» → StorePal Login\n"
                "или вставьте STOREPAL_TOKEN.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("StorePal — Privacy + Support")
        dialog.resize(640, 720)
        layout = QVBoxLayout(dialog)

        hint = QLabel(
            "Создаст/обновит Privacy Policy (Markdown) и подставит Privacy + Support URL. "
            "Support page с feedback form появляется сразу после создания app."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        form = QFormLayout()
        mode_combo = QComboBox()
        mode_combo.addItem("Создать новый app", "create")
        mode_combo.addItem("Обновить существующий", "existing")
        form.addRow("Режим", mode_combo)

        apps_combo = QComboBox()
        apps_combo.setEditable(False)
        try:
            apps = storepal_list_apps(token=token)
        except Exception as e:
            apps = []
            self._log(f"StorePal list apps: {e}")
        apps_combo.addItem("— выберите app —", "")
        for app in apps:
            name = app.get("name") or app.get("slug") or "app"
            slug = app.get("slug") or ""
            apps_combo.addItem(f"{name} (/{slug})", slug)
        form.addRow("Существующий app", apps_combo)

        app_name_input = QLineEdit(self._line_text(self.meta_name_input) or "")
        app_name_input.setPlaceholderText("App name")
        slug_input = QLineEdit(storepal_slugify(app_name_input.text() or "my-app"))
        slug_input.setPlaceholderText("my-app")
        email_input = QLineEdit(self._line_text(self.meta_review_email_input) or "")
        email_input.setPlaceholderText("support@example.com")
        developer_input = QLineEdit(
            self._line_text(self.ai_developer_name_input)
            or self._line_text(self.meta_copyright_input).lstrip("© ").strip()
            or ""
        )
        form.addRow("App name", app_name_input)
        form.addRow("Slug", slug_input)
        form.addRow("Support email", email_input)
        form.addRow("Developer", developer_input)
        layout.addLayout(form)

        def sync_slug_from_name():
            if mode_combo.currentData() == "create":
                slug_input.setText(storepal_slugify(app_name_input.text()))

        app_name_input.textChanged.connect(lambda _: sync_slug_from_name())

        def on_mode_changed():
            is_existing = mode_combo.currentData() == "existing"
            apps_combo.setEnabled(is_existing)
            slug_input.setEnabled(not is_existing)
            if is_existing and apps_combo.currentData():
                slug_input.setText(apps_combo.currentData())

        def on_app_picked():
            slug = apps_combo.currentData() or ""
            if slug:
                slug_input.setText(slug)
                # Prefer name from combo text before " ("
                text = apps_combo.currentText()
                if " (" in text and not app_name_input.text().strip():
                    app_name_input.setText(text.split(" (", 1)[0].strip())

        mode_combo.currentIndexChanged.connect(lambda _: on_mode_changed())
        apps_combo.currentIndexChanged.connect(lambda _: on_app_picked())
        on_mode_changed()

        checks_box = QGroupBox("Что указать в Privacy Policy")
        checks_layout = QVBoxLayout(checks_box)
        chk_collects = QCheckBox("Собираем данные пользователя")
        chk_collects.setChecked(bool(self.meta_collects_data_checkbox.isChecked()))
        chk_analytics = QCheckBox("Analytics")
        chk_crash = QCheckBox("Crash / diagnostics")
        chk_ads = QCheckBox("Ads")
        chk_purchases = QCheckBox("In-app purchases / subscriptions")
        for cb in (chk_collects, chk_analytics, chk_crash, chk_ads, chk_purchases):
            checks_layout.addWidget(cb)
        layout.addWidget(checks_box)

        chk_update_privacy = QCheckBox("Загрузить Privacy Policy")
        chk_update_privacy.setChecked(True)
        layout.addWidget(chk_update_privacy)

        privacy_preview = QTextEdit()
        privacy_preview.setPlaceholderText("Privacy Policy markdown preview")
        privacy_preview.setMinimumHeight(180)
        layout.addWidget(QLabel("Privacy markdown (можно править перед загрузкой)"))
        layout.addWidget(privacy_preview)

        def refresh_privacy_preview():
            privacy_preview.setPlainText(
                build_storepal_privacy_markdown(
                    app_name=app_name_input.text().strip(),
                    contact_email=email_input.text().strip(),
                    developer_name=developer_input.text().strip(),
                    collects_data=chk_collects.isChecked(),
                    uses_analytics=chk_analytics.isChecked(),
                    uses_ads=chk_ads.isChecked(),
                    uses_purchases=chk_purchases.isChecked(),
                    uses_crash=chk_crash.isChecked(),
                )
            )

        for widget in (app_name_input, email_input, developer_input):
            widget.textChanged.connect(lambda _: refresh_privacy_preview())
        for cb in (chk_collects, chk_analytics, chk_crash, chk_ads, chk_purchases):
            cb.stateChanged.connect(lambda _: refresh_privacy_preview())
        refresh_privacy_preview()

        buttons_row = QHBoxLayout()
        btn_dashboard = QPushButton("Открыть dashboard")
        btn_dashboard.setObjectName("utility_btn")
        btn_dashboard.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(STOREPAL_DASHBOARD_NEW_APP_URL))
        )
        btn_cancel = QPushButton("Отмена")
        btn_run = QPushButton("Сгенерировать и подставить URL")
        btn_run.setObjectName("main_generate_btn")
        buttons_row.addWidget(btn_dashboard)
        buttons_row.addStretch(1)
        buttons_row.addWidget(btn_cancel)
        buttons_row.addWidget(btn_run)
        layout.addLayout(buttons_row)

        btn_cancel.clicked.connect(dialog.reject)

        def start_generate():
            mode = mode_combo.currentData()
            slug = (apps_combo.currentData() if mode == "existing" else slug_input.text()).strip()
            slug = storepal_slugify(slug)
            if mode == "existing" and not slug:
                QMessageBox.warning(dialog, "StorePal", "Выберите существующий app или смените режим.")
                return
            if not app_name_input.text().strip():
                QMessageBox.warning(dialog, "StorePal", "Укажите App name.")
                return
            if not email_input.text().strip():
                QMessageBox.warning(dialog, "StorePal", "Укажите support email.")
                return

            options = {
                "token": token,
                "mode": "create" if mode == "create" else "existing",
                "app_name": app_name_input.text().strip(),
                "slug": slug,
                "description": self._text_edit_text(self.meta_description_input)[:280],
                "privacy_markdown": privacy_preview.toPlainText(),
                "update_privacy": chk_update_privacy.isChecked(),
            }
            btn_run.setEnabled(False)
            btn_run.setText("Загрузка...")
            self._log(f"StorePal: старт генерации страниц для /{slug}...")

            worker = StorePalPagesWorker(options)
            dialog._storepal_worker = worker

            def on_ready(result):
                urls = result.get("urls") or {}
                privacy_url = urls.get("privacy") or ""
                support_url = urls.get("support") or ""
                if privacy_url:
                    self.meta_privacy_policy_url_input.setText(privacy_url)
                if support_url:
                    self.meta_support_url_input.setText(support_url)
                self._log(f"StorePal privacy: {privacy_url}")
                self._log(f"StorePal support: {support_url}")
                QMessageBox.information(
                    self,
                    "StorePal",
                    f"Готово.\n\nPrivacy:\n{privacy_url}\n\nSupport:\n{support_url}",
                )
                dialog.accept()

            def on_failed(message):
                btn_run.setEnabled(True)
                btn_run.setText("Сгенерировать и подставить URL")
                self._log(f"StorePal error: {message}")
                QMessageBox.warning(dialog, "StorePal", message)

            worker.log_msg.connect(self._log)
            worker.pages_ready.connect(on_ready)
            worker.pages_failed.connect(on_failed)
            worker.start()

        btn_run.clicked.connect(start_generate)
        dialog.exec()

    def _open_local_privacy_formspree_dialog(self):
        form_id = ""
        if hasattr(self, "formspree_form_id_input"):
            form_id = self.formspree_form_id_input.text().strip()
        form_id = normalize_formspree_form_id(form_id or resolve_formspree_form_id())

        if not form_id:
            QMessageBox.warning(
                self,
                "Local Privacy + Formspree",
                "Укажите FORMSPREE_FORM_ID на вкладке «Настройки API».\n"
                "Создайте форму на https://formspree.io и скопируйте id из endpoint "
                "(https://formspree.io/f/xxxx).",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Local Privacy + Formspree")
        dialog.resize(560, 520)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "Privacy/Support → HTML на BrewPage (откроется как нормальная страница). "
            "В Support — форма Formspree (заявки на email формы). Срок ссылки ~30 дней; "
            "для постоянного URL используй StorePal."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        form = QFormLayout()
        app_name_input = QLineEdit(self._line_text(self.meta_name_input) or "")
        email_input = QLineEdit(self._line_text(self.meta_review_email_input) or "")
        developer_input = QLineEdit(
            self._line_text(self.ai_developer_name_input)
            or self._line_text(self.meta_copyright_input).lstrip("© ").strip()
            or ""
        )
        form_id_input = QLineEdit(form_id)
        form.addRow("App name", app_name_input)
        form.addRow("Support email", email_input)
        form.addRow("Developer", developer_input)
        form.addRow("Formspree form id", form_id_input)
        layout.addLayout(form)

        checks_box = QGroupBox("Что указать в Privacy")
        checks_layout = QVBoxLayout(checks_box)
        chk_collects = QCheckBox("Собираем данные пользователя")
        chk_collects.setChecked(bool(self.meta_collects_data_checkbox.isChecked()))
        chk_analytics = QCheckBox("Analytics")
        chk_crash = QCheckBox("Crash / diagnostics")
        chk_ads = QCheckBox("Ads")
        chk_purchases = QCheckBox("In-app purchases / subscriptions")
        for cb in (chk_collects, chk_analytics, chk_crash, chk_ads, chk_purchases):
            checks_layout.addWidget(cb)
        layout.addWidget(checks_box)

        buttons_row = QHBoxLayout()
        btn_fs = QPushButton("Formspree")
        btn_fs.setObjectName("utility_btn")
        btn_fs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://formspree.io/")))
        btn_cancel = QPushButton("Отмена")
        btn_run = QPushButton("Сгенерировать и подставить URL")
        btn_run.setObjectName("main_generate_btn")
        buttons_row.addWidget(btn_fs)
        buttons_row.addStretch(1)
        buttons_row.addWidget(btn_cancel)
        buttons_row.addWidget(btn_run)
        layout.addLayout(buttons_row)
        btn_cancel.clicked.connect(dialog.reject)

        def start_generate():
            if not app_name_input.text().strip():
                QMessageBox.warning(dialog, "Local Privacy + Formspree", "Укажите App name.")
                return
            if not email_input.text().strip():
                QMessageBox.warning(dialog, "Local Privacy + Formspree", "Укажите support email.")
                return
            local_form_id = normalize_formspree_form_id(form_id_input.text())
            if not local_form_id:
                QMessageBox.warning(dialog, "Local Privacy + Formspree", "Укажите Formspree form id.")
                return
            if hasattr(self, "formspree_form_id_input"):
                self.formspree_form_id_input.setText(local_form_id)
                self._save_env_to_file()

            options = {
                "formspree_form_id": local_form_id,
                "app_name": app_name_input.text().strip(),
                "email": email_input.text().strip(),
                "developer": developer_input.text().strip(),
                "collects_data": chk_collects.isChecked(),
                "uses_analytics": chk_analytics.isChecked(),
                "uses_crash": chk_crash.isChecked(),
                "uses_ads": chk_ads.isChecked(),
                "uses_purchases": chk_purchases.isChecked(),
            }
            btn_run.setEnabled(False)
            btn_run.setText("Загрузка...")
            worker = LocalPrivacyFormspreeWorker(options)
            dialog._alt_worker = worker

            def on_ready(result):
                privacy_url = (result or {}).get("privacy") or ""
                support_url = (result or {}).get("support") or ""
                if privacy_url:
                    self.meta_privacy_policy_url_input.setText(privacy_url)
                if support_url:
                    self.meta_support_url_input.setText(support_url)
                self._log(f"Local privacy: {privacy_url}")
                self._log(f"Formspree support page: {support_url}")
                QMessageBox.information(
                    self,
                    "Local Privacy + Formspree",
                    f"Готово.\n\nPrivacy:\n{privacy_url}\n\nSupport:\n{support_url}",
                )
                dialog.accept()

            def on_failed(message):
                btn_run.setEnabled(True)
                btn_run.setText("Сгенерировать и подставить URL")
                self._log(f"Local Privacy/Formspree error: {message}")
                QMessageBox.warning(dialog, "Local Privacy + Formspree", message)

            worker.log_msg.connect(self._log)
            worker.pages_ready.connect(on_ready)
            worker.pages_failed.connect(on_failed)
            worker.start()

        btn_run.clicked.connect(start_generate)
        dialog.exec()

    def _open_local_privacy_web3forms_dialog(self):
        access_key = ""
        if hasattr(self, "web3forms_key_input"):
            access_key = self.web3forms_key_input.text().strip()
        access_key = normalize_web3forms_access_key(access_key or resolve_web3forms_access_key())

        if not access_key:
            QMessageBox.warning(
                self,
                "Local Privacy + Web3Forms",
                "Укажите WEB3FORMS_ACCESS_KEY на вкладке «Настройки API».\n"
                "Ключ бесплатно: https://web3forms.com → Create Access Key (придёт на email).",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Local Privacy + Web3Forms")
        dialog.resize(560, 520)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "Резерв 2: форма Web3Forms + HTML на BrewPage (~30 дней). "
            "Другой вид страниц (тема alt). Для совсем постоянного URL — StorePal."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        form = QFormLayout()
        app_name_input = QLineEdit(self._line_text(self.meta_name_input) or "")
        email_input = QLineEdit(self._line_text(self.meta_review_email_input) or "")
        developer_input = QLineEdit(
            self._line_text(self.ai_developer_name_input)
            or self._line_text(self.meta_copyright_input).lstrip("© ").strip()
            or ""
        )
        key_input = QLineEdit(access_key)
        key_input.setEchoMode(QLineEdit.Password)
        form.addRow("App name", app_name_input)
        form.addRow("Support email", email_input)
        form.addRow("Developer", developer_input)
        form.addRow("Web3Forms access key", key_input)
        layout.addLayout(form)

        checks_box = QGroupBox("Что указать в Privacy")
        checks_layout = QVBoxLayout(checks_box)
        chk_collects = QCheckBox("Собираем данные пользователя")
        chk_collects.setChecked(bool(self.meta_collects_data_checkbox.isChecked()))
        chk_analytics = QCheckBox("Analytics")
        chk_crash = QCheckBox("Crash / diagnostics")
        chk_ads = QCheckBox("Ads")
        chk_purchases = QCheckBox("In-app purchases / subscriptions")
        for cb in (chk_collects, chk_analytics, chk_crash, chk_ads, chk_purchases):
            checks_layout.addWidget(cb)
        layout.addWidget(checks_box)

        buttons_row = QHBoxLayout()
        btn_w3 = QPushButton("Web3Forms")
        btn_w3.setObjectName("utility_btn")
        btn_w3.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://web3forms.com/")))
        btn_cancel = QPushButton("Отмена")
        btn_run = QPushButton("Сгенерировать и подставить URL")
        btn_run.setObjectName("main_generate_btn")
        buttons_row.addWidget(btn_w3)
        buttons_row.addStretch(1)
        buttons_row.addWidget(btn_cancel)
        buttons_row.addWidget(btn_run)
        layout.addLayout(buttons_row)
        btn_cancel.clicked.connect(dialog.reject)

        def start_generate():
            if not app_name_input.text().strip():
                QMessageBox.warning(dialog, "Local Privacy + Web3Forms", "Укажите App name.")
                return
            if not email_input.text().strip():
                QMessageBox.warning(dialog, "Local Privacy + Web3Forms", "Укажите support email.")
                return
            local_key = normalize_web3forms_access_key(key_input.text())
            if not local_key:
                QMessageBox.warning(dialog, "Local Privacy + Web3Forms", "Укажите Web3Forms access key.")
                return
            if hasattr(self, "web3forms_key_input"):
                self.web3forms_key_input.setText(local_key)
                self._save_env_to_file()

            options = {
                "web3forms_access_key": local_key,
                "app_name": app_name_input.text().strip(),
                "email": email_input.text().strip(),
                "developer": developer_input.text().strip(),
                "collects_data": chk_collects.isChecked(),
                "uses_analytics": chk_analytics.isChecked(),
                "uses_crash": chk_crash.isChecked(),
                "uses_ads": chk_ads.isChecked(),
                "uses_purchases": chk_purchases.isChecked(),
            }
            btn_run.setEnabled(False)
            btn_run.setText("Загрузка...")
            worker = LocalPrivacyWeb3FormsWorker(options)
            dialog._alt2_worker = worker

            def on_ready(result):
                privacy_url = (result or {}).get("privacy") or ""
                support_url = (result or {}).get("support") or ""
                if privacy_url:
                    self.meta_privacy_policy_url_input.setText(privacy_url)
                if support_url:
                    self.meta_support_url_input.setText(support_url)
                self._log(f"Local privacy (Web3Forms): {privacy_url}")
                self._log(f"Web3Forms support page: {support_url}")
                QMessageBox.information(
                    self,
                    "Local Privacy + Web3Forms",
                    f"Готово.\n\nPrivacy:\n{privacy_url}\n\nSupport:\n{support_url}",
                )
                dialog.accept()

            def on_failed(message):
                btn_run.setEnabled(True)
                btn_run.setText("Сгенерировать и подставить URL")
                self._log(f"Local Privacy/Web3Forms error: {message}")
                QMessageBox.warning(dialog, "Local Privacy + Web3Forms", message)

            worker.log_msg.connect(self._log)
            worker.pages_ready.connect(on_ready)
            worker.pages_failed.connect(on_failed)
            worker.start()

        btn_run.clicked.connect(start_generate)
        dialog.exec()

    def _on_tab_change(self, index):
        widget = self.tabs.widget(index)
        if index == 0:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 СОЗДАТЬ НОВЫЙ ТЕСТ")
        elif index == 1:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 ОБНОВИТЬ ВЫБРАННЫЕ ДАННЫЕ В ТЕСТЕ")
        elif index == 2:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 Генерация и загрузка метаданных")
        elif widget is getattr(self, "tab_screens_upload", None):
            self.start_btn.setVisible(False)
            self._refresh_screenshot_locales()
        elif widget in (
            getattr(self, "tab_translation", None),
            getattr(self, "tab_icon", None),
            getattr(self, "tab_feedback", None),
            getattr(self, "tab_accounts", None),
            getattr(self, "tab_api", None),
        ):
            self.start_btn.setVisible(False)
            if widget is getattr(self, "tab_icon", None):
                self._update_icon_prompt_source_label()
            if widget is getattr(self, "tab_accounts", None):
                self._refresh_accounts_roster_table()
        else:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 ЗАПУСТИТЬ ПРОЦЕСС")

    def _formspree_api_key(self):
        if hasattr(self, "formspree_api_key_input"):
            typed = self.formspree_api_key_input.text().strip()
            if typed:
                return typed
        return resolve_formspree_api_key()

    def _refresh_formspree_inbox(self):
        form_id = ""
        if hasattr(self, "feedback_form_id_input"):
            form_id = self.feedback_form_id_input.text().strip()
        if not form_id and hasattr(self, "formspree_form_id_input"):
            form_id = self.formspree_form_id_input.text().strip()
        form_id = normalize_formspree_form_id(form_id or resolve_formspree_form_id())
        api_key = self._formspree_api_key()
        if not form_id:
            QMessageBox.warning(
                self,
                "Feedback",
                "Укажите Formspree form id (вкладка Feedback или Настройки API).",
            )
            return
        if not api_key:
            QMessageBox.warning(
                self,
                "Feedback",
                "Укажите FORMSPREE_API_KEY в «Настройки API».\n"
                "Formspree → форма → Settings → HTTP API (Professional/Business).",
            )
            return
        if hasattr(self, "formspree_inbox_worker") and self.formspree_inbox_worker is not None and self.formspree_inbox_worker.isRunning():
            return
        if hasattr(self, "formspree_form_id_input"):
            self.formspree_form_id_input.setText(form_id)
        if hasattr(self, "feedback_form_id_input"):
            self.feedback_form_id_input.setText(form_id)
        self._save_env_to_file()
        self.btn_refresh_feedback.setEnabled(False)
        self.feedback_status_label.setText("Загрузка...")
        self.formspree_inbox_worker = FormspreeInboxWorker(form_id, api_key, limit=50)
        self.formspree_inbox_worker.log_msg.connect(self._log)
        self.formspree_inbox_worker.submissions_ready.connect(self._populate_formspree_inbox)
        self.formspree_inbox_worker.submissions_failed.connect(self._on_formspree_inbox_failed)
        self.formspree_inbox_worker.finished.connect(lambda: self.btn_refresh_feedback.setEnabled(True))
        self.formspree_inbox_worker.start()

    def _on_formspree_inbox_failed(self, message):
        self.feedback_status_label.setText(f"Ошибка: {message}")
        self._log(f"Formspree Inbox error: {message}")
        QMessageBox.warning(self, "Feedback", message)

    def _populate_formspree_inbox(self, submissions):
        rows = list(submissions or [])
        self.feedback_table.setRowCount(0)
        for item in rows:
            if not isinstance(item, dict):
                continue
            date_text = str(item.get("_date") or item.get("date") or "")
            email_text = str(
                item.get("email")
                or item.get("_replyto")
                or item.get("Email")
                or ""
            )
            message_text = str(
                item.get("message")
                or item.get("Message")
                or item.get("body")
                or item.get("text")
                or ""
            )
            # If message empty, join remaining useful fields.
            if not message_text.strip():
                skip = {"_date", "date", "email", "_replyto", "Email", "_status", "id", "_id"}
                bits = []
                for key, value in item.items():
                    if key in skip or isinstance(value, (dict, list)):
                        continue
                    bits.append(f"{key}: {value}")
                message_text = " | ".join(bits)
            row = self.feedback_table.rowCount()
            self.feedback_table.insertRow(row)
            self.feedback_table.setItem(row, 0, QTableWidgetItem(date_text))
            self.feedback_table.setItem(row, 1, QTableWidgetItem(email_text))
            self.feedback_table.setItem(row, 2, QTableWidgetItem(message_text))
        self.feedback_table.resizeRowsToContents()
        self.feedback_status_label.setText(f"Заявок: {self.feedback_table.rowCount()}")

    def _autofill_key_id_from_p8_path(self):
        key_id = self._extract_key_id_from_p8_path()
        if not key_id:
            return
        if self.key_input.text().strip() != key_id:
            self.key_input.setText(key_id)
            self._log(f"KEY_ID автоматически определен из имени .p8: {key_id}")
        self._log(
            "APP_ID нельзя извлечь из .p8: Apple private key содержит только ключ подписи. "
            "APP_ID нужно указать из App Store Connect."
        )
        self._save_env_to_file()

    def _select_p8_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", "Key Files (*.p8);;All Files (*)")
        if file_path:
            self.p8_path_input.setText(file_path)
            self._autofill_key_id_from_p8_path()

    def _pick_xcode_projects_root(self):
        start = ""
        if hasattr(self, "xcode_projects_root_input"):
            start = self.xcode_projects_root_input.text().strip()
        start = start or DEFAULT_XCODE_PROJECTS_ROOT
        folder = QFileDialog.getExistingDirectory(self, "Корень папок проектов", start)
        if not folder:
            return
        self.xcode_projects_root_input.setText(folder)
        self._save_env_to_file()
        self._log(f"Корень Projects: {folder}")

    def _pick_xcode_project_dir(self):
        start = self.xcode_project_input.text().strip()
        if not start and hasattr(self, "xcode_projects_root_input"):
            start = self.xcode_projects_root_input.text().strip()
        start = start or DEFAULT_XCODE_PROJECTS_ROOT
        folder = QFileDialog.getExistingDirectory(self, "Папка Xcode-проекта", start)
        if not folder:
            return
        self.xcode_project_input.setText(folder)
        self._save_env_to_file()
        self._detect_xcode_scheme(silent=True)

    def _auto_fill_xcode_project_for_app(self, app_name="", bundle_id=""):
        """После выбора App ID — подставить папку проекта по имени из корня Projects."""
        if not hasattr(self, "xcode_project_input"):
            return
        app_name = (app_name or getattr(self, "_summary_app_name", "") or "").strip()
        if not app_name:
            return
        root = resolve_xcode_projects_root()
        if hasattr(self, "xcode_projects_root_input"):
            root = self.xcode_projects_root_input.text().strip() or root
        found = find_xcode_project_dir_by_app_name(app_name, root, bundle_id=bundle_id or "")
        if not found:
            self._log(
                f"Xcode проект: в «{root}» не найдена папка для «{app_name}» "
                f"(ожидается имя вроде Spillwheel / River-Aspect)."
            )
            if hasattr(self, "xcode_build_status"):
                self.xcode_build_status.setText(
                    f"Проект не найден для «{app_name}» в {root}"
                )
            return
        self.xcode_project_input.setText(found)
        self._save_env_to_file()
        self._log(f"Xcode проект по имени «{app_name}»: {found}")
        if hasattr(self, "xcode_build_status"):
            self.xcode_build_status.setText(f"Проект: {found}")
        self._detect_xcode_scheme(silent=True)

    def _detect_xcode_scheme(self, silent=False):
        project_dir = self.xcode_project_input.text().strip()
        if not project_dir:
            if not silent:
                QMessageBox.warning(self, "Билд IPA", "Укажите папку Xcode-проекта.")
            return
        try:
            project_path = find_xcodeproj(project_dir)
            scheme = discover_scheme(project_path, self.xcode_scheme_input.text().strip())
            self.xcode_scheme_input.setText(scheme)
            self._save_env_to_file()
            msg = f"Scheme: {scheme} ({os.path.basename(project_path)})"
            self.xcode_build_status.setText(msg)
            self._log(msg)
        except Exception as e:
            if not silent:
                QMessageBox.warning(self, "Билд IPA", str(e))
            self._log(f"Билд IPA: не удалось определить scheme — {e}")

    def _start_xcode_ipa_build(self, upload=False):
        if sys.platform != "darwin":
            QMessageBox.warning(self, "Билд IPA", "Только macOS + Xcode.")
            return
        if getattr(self, "xcode_ipa_worker", None) and self.xcode_ipa_worker.isRunning():
            QMessageBox.information(self, "Билд IPA", "Сборка уже идёт.")
            return

        project_dir = self.xcode_project_input.text().strip()
        if not project_dir:
            QMessageBox.warning(self, "Билд IPA", "Укажите папку Xcode-проекта.")
            return

        issuer = self.issuer_input.text().strip()
        key_id = self.key_input.text().strip()
        p8 = self.p8_path_input.text().strip()
        app_id = self.app_input.text().strip()
        bump = self.xcode_bump_build_checkbox.isChecked()
        need_asc = bump or upload
        if need_asc and not (issuer and key_id and p8 and app_id):
            QMessageBox.warning(
                self,
                "Билд IPA",
                "Для build number / заливки заполните Issuer ID, Key ID, .p8 и App ID "
                "на вкладке «Настройки API».",
            )
            return

        self._save_env_to_file()
        action = "сборка + заливка" if upload else "сборка IPA"
        self.xcode_build_status.setText(f"Идёт {action}… смотрите лог внизу.")
        self.btn_xcode_build_only.setEnabled(False)
        self.btn_xcode_build_upload.setEnabled(False)
        self._log(f"——— Билд IPA: старт ({action}) ———")

        options = {
            "project_dir": project_dir,
            "scheme": self.xcode_scheme_input.text().strip(),
            "team_id": self.xcode_team_input.text().strip(),
            "issuer_id": issuer,
            "key_id": key_id,
            "private_key_path": p8,
            "app_id": app_id,
            "bump_build": bump,
            "upload": upload,
            "scan_leaks": self.xcode_scan_leaks_checkbox.isChecked(),
            "run_bulder": self.xcode_run_bulder_checkbox.isChecked(),
            "need_asc": need_asc,
        }
        self.xcode_ipa_worker = XcodeIpaWorker(options)
        self.xcode_ipa_worker.log_msg.connect(self._log)
        self.xcode_ipa_worker.build_ok.connect(self._on_xcode_ipa_ok)
        self.xcode_ipa_worker.build_failed.connect(self._on_xcode_ipa_failed)
        self.xcode_ipa_worker.finished.connect(self._on_xcode_ipa_finished)
        self.xcode_ipa_worker.start()

    def _on_xcode_ipa_ok(self, result):
        ipa = (result or {}).get("ipa_path", "")
        uploaded = (result or {}).get("uploaded")
        build_no = (result or {}).get("build_number")
        parts = [f"Готово: {ipa}" if ipa else "Готово."]
        if build_no is not None:
            parts.append(f"build {build_no}")
        if uploaded:
            parts.append("залит в App Store Connect")
        msg = " · ".join(parts)
        self.xcode_build_status.setText(msg)
        self._log(msg)
        QMessageBox.information(self, "Билд IPA", msg)

    def _on_xcode_ipa_failed(self, err):
        self.xcode_build_status.setText(f"Ошибка: {err}")
        self._log(f"Билд IPA ошибка: {err}")
        QMessageBox.critical(self, "Билд IPA", str(err)[:2000])

    def _on_xcode_ipa_finished(self):
        self.btn_xcode_build_only.setEnabled(True)
        self.btn_xcode_build_upload.setEnabled(True)

    def _select_folder(self, variant, button=None):
        folder = QFileDialog.getExistingDirectory(self, f"Выберите папку для {variant}")
        if folder:
            self.variants_paths[variant] = folder
            if button is not None:
                button.setText(f"{variant}: {os.path.basename(folder)}")
            self._refresh_variant_cards()

    def _log(self, text):
        self.log_area.append(text)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        print(text)

    def _update_progress(self, percent, time_text):
        self.progress_bar.setValue(percent)
        self.time_label.setText(time_text)

    def _selected_ai_provider(self):
        if not hasattr(self, "ai_provider_combo"):
            return resolve_ai_provider()
        provider = self.ai_provider_combo.currentData()
        if provider in (AI_PROVIDER_AITUNNEL, AI_PROVIDER_ZAI):
            return str(provider)
        return resolve_ai_provider()

    def _selected_ai_model(self, provider=None):
        provider = provider or self._selected_ai_provider()
        if provider == AI_PROVIDER_ZAI:
            if not hasattr(self, "zai_model_combo"):
                return resolve_ai_model(AI_PROVIDER_ZAI)
            model_id = self.zai_model_combo.currentData()
            if model_id:
                return str(model_id).strip()
            return DEFAULT_ZAI_MODEL
        if not hasattr(self, "aitunnel_model_combo"):
            return resolve_ai_model(AI_PROVIDER_AITUNNEL)
        model_id = self.aitunnel_model_combo.currentData()
        if model_id:
            return str(model_id)
        return DEFAULT_AI_MODEL

    def _selected_aitunnel_image_model(self):
        if not hasattr(self, "aitunnel_image_model_combo"):
            return resolve_aitunnel_image_model()
        model_id = self.aitunnel_image_model_combo.currentData()
        if model_id:
            return str(model_id).strip()
        return DEFAULT_AITUNNEL_IMAGE_MODEL

    def _selected_ai_credentials(self):
        provider = self._selected_ai_provider()
        if provider == AI_PROVIDER_ZAI:
            api_key = self.zai_key_input.text().strip() if hasattr(self, "zai_key_input") else resolve_zai_api_key()
        else:
            api_key = self.aitunnel_key_input.text().strip() if hasattr(self, "aitunnel_key_input") else resolve_aitunnel_api_key()
        model = self._selected_ai_model(provider)
        return api_key, model, provider

    def _populate_zai_models(self, catalog=None, select_model=None):
        if not hasattr(self, "zai_model_combo"):
            return
        catalog = catalog or fetch_zai_model_catalog()
        select_model = (select_model or self._selected_ai_model(AI_PROVIDER_ZAI) or DEFAULT_ZAI_MODEL).strip()
        model_ids = [model_id for model_id, _, _ in catalog]
        self.zai_model_combo.blockSignals(True)
        self.zai_model_combo.clear()
        for model_id, label, _cost in catalog:
            self.zai_model_combo.addItem(label, model_id)
        if select_model and select_model not in model_ids:
            self.zai_model_combo.insertItem(0, f"{select_model} (сохранённая)", select_model)
        index = self.zai_model_combo.findData(select_model)
        if index < 0:
            index = self.zai_model_combo.findText(select_model)
        if index >= 0:
            self.zai_model_combo.setCurrentIndex(index)
        elif self.zai_model_combo.count():
            self.zai_model_combo.setCurrentIndex(0)
        self.zai_model_combo.blockSignals(False)

    def _refresh_zai_models(self):
        self._log("Обновляю список GLM моделей Z.AI (дешёвые → дорогие)...")
        catalog = fetch_zai_model_catalog()
        self._populate_zai_models(catalog=catalog, select_model=self._selected_ai_model(AI_PROVIDER_ZAI))
        self._log(f"✅ В списке GLM моделей: {len(catalog)} (до ~${ZAI_MODEL_MAX_TOTAL_COST:.0f}/1M)")

    def _sync_ai_provider_ui(self):
        provider = self._selected_ai_provider()
        use_zai = provider == AI_PROVIDER_ZAI
        if hasattr(self, "aitunnel_settings_widget"):
            self.aitunnel_settings_widget.setVisible(not use_zai)
        if hasattr(self, "zai_settings_widget"):
            self.zai_settings_widget.setVisible(use_zai)

    def _on_ai_provider_changed(self):
        self._sync_ai_provider_ui()

    def _populate_aitunnel_models(self, catalog=None, select_model=None):
        if not hasattr(self, "aitunnel_model_combo"):
            return
        catalog = catalog or fetch_aitunnel_model_catalog()
        select_model = (select_model or self._selected_ai_model(AI_PROVIDER_AITUNNEL) or DEFAULT_AI_MODEL).strip()
        model_ids = [model_id for model_id, _, _ in catalog]
        self.aitunnel_model_combo.blockSignals(True)
        self.aitunnel_model_combo.clear()
        for model_id, label, _cost in catalog:
            self.aitunnel_model_combo.addItem(label, model_id)
        if select_model and select_model not in model_ids:
            self.aitunnel_model_combo.insertItem(0, f"{select_model} (сохранённая)", select_model)
        index = self.aitunnel_model_combo.findData(select_model)
        if index < 0:
            index = self.aitunnel_model_combo.findText(select_model)
        if index >= 0:
            self.aitunnel_model_combo.setCurrentIndex(index)
        elif self.aitunnel_model_combo.count():
            self.aitunnel_model_combo.setCurrentIndex(0)
        self.aitunnel_model_combo.blockSignals(False)

    def _refresh_aitunnel_models(self):
        self._log("Загружаю дешёвые и средние модели AITUNNEL...")
        catalog = fetch_aitunnel_model_catalog()
        self._populate_aitunnel_models(catalog=catalog, select_model=self._selected_ai_model(AI_PROVIDER_AITUNNEL))
        self._log(f"✅ В списке моделей: {len(catalog)} (до ~{int(AITUNNEL_MODEL_MAX_TOTAL_COST)} ₽/1M)")

    def _populate_aitunnel_image_models(self, catalog=None, select_model=None):
        if not hasattr(self, "aitunnel_image_model_combo"):
            return
        catalog = catalog or fetch_aitunnel_image_model_catalog()
        select_model = (select_model or self._selected_aitunnel_image_model() or DEFAULT_AITUNNEL_IMAGE_MODEL).strip()
        model_ids = [model_id for model_id, _, _ in catalog]
        self.aitunnel_image_model_combo.blockSignals(True)
        self.aitunnel_image_model_combo.clear()
        for model_id, label, _cost in catalog:
            self.aitunnel_image_model_combo.addItem(label, model_id)
        if select_model and select_model not in model_ids:
            self.aitunnel_image_model_combo.insertItem(0, f"{select_model} (сохранённая)", select_model)
        index = self.aitunnel_image_model_combo.findData(select_model)
        if index < 0:
            index = self.aitunnel_image_model_combo.findText(select_model)
        if index >= 0:
            self.aitunnel_image_model_combo.setCurrentIndex(index)
        elif self.aitunnel_image_model_combo.count():
            self.aitunnel_image_model_combo.setCurrentIndex(0)
        self.aitunnel_image_model_combo.blockSignals(False)

    def _refresh_aitunnel_image_models(self):
        self._log("Загружаю модели генерации картинок AITUNNEL...")
        catalog = fetch_aitunnel_image_model_catalog()
        self._populate_aitunnel_image_models(
            catalog=catalog,
            select_model=self._selected_aitunnel_image_model(),
        )
        self._log(f"✅ В списке image-моделей: {len(catalog)}")

    def _save_env_to_file(self):
        env_content = (
            f"ISSUER_ID={self.issuer_input.text().strip()}\n"
            f"KEY_ID={self.key_input.text().strip()}\n"
            f"APP_ID={self.app_input.text().strip()}\n"
            f"PRIVATE_KEY_PATH={self.p8_path_input.text().strip()}\n"
        )
        if hasattr(self, "figma_token_input"):
            env_content += f"FIGMA_TOKEN={self.figma_token_input.text().strip()}\n"
        if hasattr(self, "storepal_token_input"):
            env_content += f"STOREPAL_TOKEN={self.storepal_token_input.text().strip()}\n"
        if hasattr(self, "formspree_form_id_input"):
            env_content += f"FORMSPREE_FORM_ID={self.formspree_form_id_input.text().strip()}\n"
        if hasattr(self, "formspree_api_key_input"):
            env_content += f"FORMSPREE_API_KEY={self.formspree_api_key_input.text().strip()}\n"
        if hasattr(self, "web3forms_key_input"):
            env_content += f"WEB3FORMS_ACCESS_KEY={self.web3forms_key_input.text().strip()}\n"
        if hasattr(self, "gitlab_url_input"):
            env_content += f"GITLAB_URL={self.gitlab_url_input.text().strip()}\n"
        if hasattr(self, "gitlab_token_input"):
            env_content += f"GITLAB_TOKEN={self.gitlab_token_input.text().strip()}\n"
        if hasattr(self, "xcode_project_input"):
            env_content += f"XCODE_PROJECT_PATH={self.xcode_project_input.text().strip()}\n"
        if hasattr(self, "xcode_projects_root_input"):
            env_content += f"XCODE_PROJECTS_ROOT={self.xcode_projects_root_input.text().strip()}\n"
        if hasattr(self, "xcode_scheme_input"):
            env_content += f"XCODE_SCHEME={self.xcode_scheme_input.text().strip()}\n"
        if hasattr(self, "xcode_team_input"):
            env_content += f"XCODE_TEAM_ID={self.xcode_team_input.text().strip()}\n"
        if hasattr(self, "ai_provider_combo"):
            env_content += f"AI_PROVIDER={self._selected_ai_provider()}\n"
        if hasattr(self, "aitunnel_key_input"):
            env_content += f"AITUNNEL_API_KEY={self.aitunnel_key_input.text().strip()}\n"
        if hasattr(self, "aitunnel_model_combo"):
            env_content += f"AITUNNEL_MODEL={self._selected_ai_model(AI_PROVIDER_AITUNNEL)}\n"
        if hasattr(self, "aitunnel_image_model_combo"):
            env_content += f"AITUNNEL_IMAGE_MODEL={self._selected_aitunnel_image_model()}\n"
        if hasattr(self, "zai_key_input"):
            env_content += f"ZAI_API_KEY={self.zai_key_input.text().strip()}\n"
        if hasattr(self, "zai_model_combo"):
            env_content += f"ZAI_MODEL={self._selected_ai_model(AI_PROVIDER_ZAI)}\n"
        if hasattr(self, "jira_base_url_input"):
            env_content += f"JIRA_BASE_URL={self.jira_base_url_input.text().strip()}\n"
        if hasattr(self, "jira_email_input"):
            env_content += f"JIRA_EMAIL={self.jira_email_input.text().strip()}\n"
        if hasattr(self, "jira_api_token_input"):
            env_content += f"JIRA_API_TOKEN={self.jira_api_token_input.text().strip()}\n"
        if hasattr(self, "jira_project_key_input"):
            env_content += f"JIRA_PROJECT_KEY={self.jira_project_key_input.text().strip()}\n"
        if hasattr(self, "jira_issue_type_input"):
            env_content += f"JIRA_ISSUE_TYPE={self.jira_issue_type_input.text().strip()}\n"
        if hasattr(self, "jira_issue_key_input"):
            env_content += f"JIRA_ISSUE_KEY={self.jira_issue_key_input.text().strip()}\n"
        if hasattr(self, "translation_workers_spin"):
            env_content += f"TRANSLATION_MAX_WORKERS={self._selected_translation_workers()}\n"
        if hasattr(self, "localization_upload_workers_spin"):
            env_content += f"LOCALIZATION_UPLOAD_MAX_WORKERS={self._selected_localization_upload_workers()}\n"
        if hasattr(self, "screenshot_upload_workers_spin"):
            env_content += f"SCREENSHOT_UPLOAD_MAX_WORKERS={self._selected_screenshot_upload_workers()}\n"
        try:
            with open(ENV_PATH, "w", encoding="utf-8") as f: f.write(env_content)
        except Exception as e:
            self._log(f"Ошибка сохранения .env файла: {e}")

    def _selected_translation_workers(self):
        if hasattr(self, "translation_workers_spin"):
            return _clamp_workers(self.translation_workers_spin.value(), TRANSLATION_MAX_WORKERS)
        return resolve_translation_max_workers()

    def _selected_localization_upload_workers(self):
        if hasattr(self, "localization_upload_workers_spin"):
            return _clamp_workers(self.localization_upload_workers_spin.value(), LOCALIZATION_UPLOAD_MAX_WORKERS)
        return resolve_localization_upload_max_workers()

    def _selected_screenshot_upload_workers(self):
        if hasattr(self, "screenshot_upload_workers_spin"):
            return _clamp_workers(self.screenshot_upload_workers_spin.value(), SCREENSHOT_UPLOAD_MAX_WORKERS)
        return resolve_screenshot_upload_max_workers()

    def _current_value_for_generation_mode(self, mode):
        if mode == "description" or mode == "rewrite_description":
            return self._text_edit_text(self.meta_description_input)
        if mode == "keywords" or mode == "shorten_keywords":
            return self._line_text(self.meta_keywords_input)
        if mode == "subtitle":
            return self._line_text(self.meta_subtitle_input)
        if mode == "promotional_text":
            return self._text_edit_text(self.meta_promotional_text_input)
        if mode == "whats_new":
            return self._text_edit_text(self.meta_whats_new_input)
        if mode == "review_notes":
            return self._text_edit_text(self.meta_review_notes_input)
        if mode == "category":
            return json.dumps({
                "primaryCategory": self._line_text(self.meta_primary_category_input),
                "secondaryCategory": self._line_text(self.meta_secondary_category_input),
            }, ensure_ascii=False)
        if mode == "shorten_to_limits":
            return json.dumps(self._build_metadata_config_from_gui(), ensure_ascii=False)
        if mode == "fix_banned_words":
            return "\n".join([
                self._text_edit_text(self.meta_description_input),
                self._text_edit_text(self.meta_promotional_text_input),
                self._line_text(self.meta_subtitle_input),
                self._line_text(self.meta_keywords_input)
            ])
        return ""

    def _set_ai_buttons_enabled(self, state):
        for button in getattr(self, "ai_generation_buttons", []):
            button.setEnabled(state)
        for button in getattr(self, "ai_field_buttons", []):
            button.setEnabled(state)
        for button in getattr(self, "ai_fix_buttons", []):
            button.setEnabled(state)
        if hasattr(self, "btn_reset_ai_prompt"):
            self.btn_reset_ai_prompt.setEnabled(state)

    def _generate_ai_metadata(self, mode="all", chain_upload=False):
        self._save_env_to_file()
        api_key, model, provider = self._selected_ai_credentials()
        app_context = self._text_edit_text(self.ai_app_context_input)
        user_prompt = self._text_edit_text(self.ai_prompt_input)
        developer_name = self._line_text(self.ai_developer_name_input)
        locale = self.meta_locale_combo.currentData() or "en-US"
        current_value = self._current_value_for_generation_mode(mode)
        provider_label = ai_provider_label(provider)

        if not api_key:
            self._log(f"Ошибка: Введите API key для {provider_label} на вкладке «Настройки API».")
            return False
        if not model:
            self._log(f"Ошибка: Выберите модель для {provider_label}.")
            return False
        if not app_context:
            self._log("Ошибка: Вставьте ТЗ / brief приложения для AI генерации.")
            return False
        if not user_prompt:
            self._log("Ошибка: Вставьте промт генерации для AI.")
            return False
        rewrite_modes = ["rewrite_description", "shorten_keywords", "shorten_to_limits", "fix_banned_words"]
        if mode in rewrite_modes and not current_value:
            self._log("Ошибка: Для этого режима сначала заполните текущее поле, которое нужно переписать или сократить.")
            return False

        self._ai_chain_upload_after = bool(chain_upload)
        self._ai_chain_got_metadata = False
        self._set_ai_buttons_enabled(False)
        mode_labels = {
            "all": "все поля",
            "description": "Description",
            "keywords": "Keywords",
            "subtitle": "Subtitle",
            "promotional_text": "Promotional text",
            "whats_new": "What's New",
            "review_notes": "App Review notes",
            "category": "Categories",
        }
        self._log(f"Отправляю запрос в {provider_label} ({model}). Режим: {mode_labels.get(mode, mode)}")
        self.gemini_worker = GeminiMetadataWorker(
            api_key, model, locale, app_context, user_prompt, developer_name, mode, current_value, ai_provider=provider,
        )
        self.gemini_worker.log_msg.connect(self._log)
        self.gemini_worker.metadata_generated.connect(self._apply_ai_metadata)
        self.gemini_worker.finished.connect(self._on_ai_metadata_finished)
        self.gemini_worker.start()
        return True

    def _on_ai_metadata_finished(self):
        self._set_ai_buttons_enabled(True)
        if getattr(self, "_ai_chain_upload_after", False) and not getattr(self, "_ai_chain_got_metadata", False):
            self._ai_chain_upload_after = False
            if hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)
            if hasattr(self, "btn_privacy_support_oneshot"):
                self.btn_privacy_support_oneshot.setEnabled(True)
            self._log("⚠️ AI генерация не удалась — загрузка в Apple отменена.")
            QMessageBox.warning(
                self,
                "Генерация метаданных",
                "AI не сгенерировал метаданные. Загрузка в Apple отменена.\n"
                "Проверьте ТЗ, промт и API key.",
            )

    def _update_icon_prompt_source_label(self):
        if not hasattr(self, "icon_prompt_source_label"):
            return
        user_prompt = self._text_edit_text(self.ai_prompt_input) if hasattr(self, "ai_prompt_input") else ""
        description = self._text_edit_text(self.meta_description_input) if hasattr(self, "meta_description_input") else ""
        app_context = self._text_edit_text(self.ai_app_context_input) if hasattr(self, "ai_app_context_input") else ""
        parts = []
        if user_prompt:
            parts.append(f"промт AI ({len(user_prompt)} симв.)")
        else:
            parts.append("промт AI пуст")
        if app_context:
            parts.append(f"ТЗ/brief ({len(app_context)} симв.)")
        if description:
            parts.append(f"Description ({len(description)} симв.)")
        else:
            parts.append("Description пуст")
        model = self._selected_aitunnel_image_model()
        self.icon_prompt_source_label.setText(
            f"Источники: {', '.join(parts)}. Image-модель: {model}"
        )

    def _build_icon_generation_prompt(self):
        user_prompt = self._text_edit_text(self.ai_prompt_input) if hasattr(self, "ai_prompt_input") else ""
        app_context = self._text_edit_text(self.ai_app_context_input) if hasattr(self, "ai_app_context_input") else ""
        description = ""
        if hasattr(self, "icon_use_description_checkbox") and self.icon_use_description_checkbox.isChecked():
            description = self._text_edit_text(self.meta_description_input) if hasattr(self, "meta_description_input") else ""
        extra = self._text_edit_text(self.icon_extra_prompt_input) if hasattr(self, "icon_extra_prompt_input") else ""
        app_name = self._line_text(self.meta_name_input) if hasattr(self, "meta_name_input") else ""

        sections = [
            "Create a premium App Store application icon.",
            f"Output must be a square icon suitable for Apple App Store at {ICON_IMAGE_PIXELS}x{ICON_IMAGE_PIXELS} pixels.",
            "Use a clean, distinctive symbol that remains readable at small sizes.",
            "Do NOT include rounded corners, device frames, or App Store badges — Apple applies masking.",
            "Avoid tiny unreadable text. Prefer a bold visual metaphor of the app.",
        ]
        if app_name:
            sections.append(f"App name: {app_name}")
        if app_context:
            sections.append(f"App brief / product context:\n{app_context}")
        if user_prompt:
            sections.append(f"Creative direction / prompt from the app:\n{user_prompt}")
        if description:
            sections.append(f"App Store description:\n{description}")
        if extra:
            sections.append(f"Extra icon instructions:\n{extra}")
        return "\n\n".join(sections).strip()

    def _set_icon_buttons_enabled(self, state):
        if hasattr(self, "btn_generate_icon"):
            self.btn_generate_icon.setEnabled(state)
        if hasattr(self, "btn_save_icon"):
            self.btn_save_icon.setEnabled(bool(state and self._generated_icon_png))

    def _generate_app_icon(self):
        self._save_env_to_file()
        self._update_icon_prompt_source_label()
        api_key = self.aitunnel_key_input.text().strip() if hasattr(self, "aitunnel_key_input") else resolve_aitunnel_api_key()
        model = self._selected_aitunnel_image_model()
        user_prompt = self._text_edit_text(self.ai_prompt_input) if hasattr(self, "ai_prompt_input") else ""
        app_context = self._text_edit_text(self.ai_app_context_input) if hasattr(self, "ai_app_context_input") else ""
        description = ""
        if hasattr(self, "icon_use_description_checkbox") and self.icon_use_description_checkbox.isChecked():
            description = self._text_edit_text(self.meta_description_input) if hasattr(self, "meta_description_input") else ""
        extra = self._text_edit_text(self.icon_extra_prompt_input) if hasattr(self, "icon_extra_prompt_input") else ""

        if not api_key:
            self._log("Ошибка: Введите AITUNNEL API key на вкладке «Настройки API».")
            return
        if not model:
            self._log("Ошибка: Выберите модель AITUNNEL для генерации картинок.")
            return
        if not (user_prompt or app_context or description or extra):
            self._log(
                "Ошибка: Нужен промт из «Метаданные → AI генерация», Description "
                "или доп. указания на этой вкладке."
            )
            return

        prompt = self._build_icon_generation_prompt()
        self._set_icon_buttons_enabled(False)
        self.icon_status_label.setText(f"Генерация через {model}…")
        self._log(f"Отправляю запрос генерации иконки в AITUNNEL ({model}), размер {ICON_IMAGE_SIZE}")
        self.icon_worker = IconGenerationWorker(api_key, model, prompt)
        self.icon_worker.log_msg.connect(self._log)
        self.icon_worker.icon_generated.connect(self._on_icon_generated)
        self.icon_worker.finished.connect(lambda: self._set_icon_buttons_enabled(True))
        self.icon_worker.start()

    def _on_icon_generated(self, png_bytes, meta):
        self._generated_icon_png = png_bytes
        image = QImage.fromData(png_bytes, "PNG")
        if image.isNull():
            self._log("Ошибка: не удалось отобразить сгенерированную иконку.")
            self.icon_status_label.setText("Ошибка превью")
            return
        pixmap = QPixmap.fromImage(image).scaled(
            320, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.icon_preview_label.setPixmap(pixmap)
        cost = meta.get("cost_rub")
        cost_part = f" · {cost} ₽" if cost is not None else ""
        saved_path = self._save_generated_icon(auto=True)
        if saved_path:
            self.icon_status_label.setText(
                f"Готово и сохранено: {os.path.basename(saved_path)} · "
                f"{meta.get('model', '')}{cost_part}"
            )
        else:
            self.icon_status_label.setText(
                f"Готово: PNG {ICON_IMAGE_PIXELS}×{ICON_IMAGE_PIXELS} · {meta.get('model', '')}{cost_part}"
            )
        if hasattr(self, "btn_save_icon"):
            self.btn_save_icon.setEnabled(True)

    def _icon_output_app_name(self):
        if hasattr(self, "meta_name_input"):
            name = self._line_text(self.meta_name_input)
            if name:
                return name
        return "AppIcon"

    def _save_generated_icon(self, auto=False):
        if not self._generated_icon_png:
            if not auto:
                self._log("Ошибка: сначала сгенерируйте иконку.")
            return None
        app_name = self._icon_output_app_name()
        filename = _icon_filename_from_app_name(app_name)
        try:
            file_path = _unique_icon_output_path(ICONS_OUTPUT_DIR, filename)
            with open(file_path, "wb") as f:
                f.write(self._generated_icon_png)
            self._log(f"✅ Иконка сохранена: {file_path}")
            if hasattr(self, "icon_status_label"):
                self.icon_status_label.setText(f"Сохранено: {os.path.basename(file_path)}")
            return file_path
        except Exception as e:
            self._log(f"Ошибка сохранения иконки: {e}")
            return None

    def _open_icons_folder(self):
        try:
            os.makedirs(ICONS_OUTPUT_DIR, exist_ok=True)
            os.system(f'open "{ICONS_OUTPUT_DIR}"')
        except Exception as e:
            self._log(f"Не удалось открыть папку Icons: {e}")

    def _apply_ai_metadata(self, metadata):
        mode = getattr(self.gemini_worker, "generation_mode", "all")
        single_field_modes = {
            "description", "keywords", "subtitle", "promotional_text", "whats_new", "review_notes", "category",
            "rewrite_description", "shorten_keywords",
        }

        if mode in single_field_modes:
            if mode in ("description", "rewrite_description") and metadata.get("description"):
                self.meta_description_input.setPlainText(str(metadata.get("description", ""))[:4000])
                self._log("✅ Description обновлён.")
            elif mode in ("keywords", "shorten_keywords") and metadata.get("keywords"):
                keywords, _ = truncate_keywords_to_limit(str(metadata.get("keywords", "")))
                self.meta_keywords_input.setText(keywords)
                self._log("✅ Keywords обновлены.")
            elif mode == "subtitle" and metadata.get("subtitle"):
                self.meta_subtitle_input.setText(str(metadata.get("subtitle", ""))[:30])
                self._log("✅ Subtitle обновлён.")
            elif mode == "promotional_text" and metadata.get("promotionalText"):
                self.meta_promotional_text_input.setPlainText(str(metadata.get("promotionalText", ""))[:170])
                self._log("✅ Promotional text обновлён.")
            elif mode == "whats_new" and metadata.get("whatsNew"):
                self.meta_whats_new_input.setPlainText(str(metadata.get("whatsNew", "")))
                self._log("✅ What's New обновлён.")
            elif mode == "review_notes" and metadata.get("reviewNotes"):
                self.meta_review_notes_input.setPlainText(str(metadata.get("reviewNotes", "")))
                self._log("✅ App Review notes обновлены.")
            elif mode == "category":
                category_name = metadata.get("categoryName") or ""
                primary_from_text, secondary_from_text = extract_category_ids_from_rationale(category_name)
                primary_ok = self._set_category_combo(
                    self.meta_primary_category_input,
                    metadata.get("primaryCategory", "") or primary_from_text,
                )
                secondary_ok = self._set_category_combo(
                    self.meta_secondary_category_input,
                    metadata.get("secondaryCategory", "") or secondary_from_text,
                )
                if primary_ok or secondary_ok:
                    self._log(
                        "✅ Categories обновлены"
                        f" (primary={self._line_text(self.meta_primary_category_input) or '—'}, "
                        f"secondary={self._line_text(self.meta_secondary_category_input) or '—'})."
                    )
                else:
                    self._log(
                        "⚠️ AI вернул категорию, но не удалось распознать ID. "
                        "Выберите Primary/Secondary вручную перед загрузкой."
                    )
                if category_name:
                    self._log(f"AI rationale: {category_name}")
                return

        if metadata.get("subtitle"):
            subtitle, _ = truncate_metadata_field("subtitle", str(metadata.get("subtitle", "")))
            self.meta_subtitle_input.setText(subtitle)
        if metadata.get("description"):
            self.meta_description_input.setPlainText(str(metadata.get("description", "")))
        if metadata.get("keywords"):
            keywords, _ = truncate_keywords_to_limit(str(metadata.get("keywords", "")))
            self.meta_keywords_input.setText(keywords)
        if metadata.get("promotionalText"):
            promo, _ = truncate_metadata_field("promotionalText", str(metadata.get("promotionalText", "")))
            self.meta_promotional_text_input.setPlainText(promo)
        if metadata.get("whatsNew"):
            self.meta_whats_new_input.setPlainText(str(metadata.get("whatsNew", "")))
        category_name = metadata.get("categoryName") or ""
        primary_from_text, secondary_from_text = extract_category_ids_from_rationale(category_name)
        if metadata.get("primaryCategory") or primary_from_text:
            self._set_category_combo(
                self.meta_primary_category_input,
                metadata.get("primaryCategory", "") or primary_from_text,
            )
        if metadata.get("secondaryCategory") or secondary_from_text:
            self._set_category_combo(
                self.meta_secondary_category_input,
                metadata.get("secondaryCategory", "") or secondary_from_text,
            )
        if metadata.get("reviewNotes"):
            self.meta_review_notes_input.setPlainText(str(metadata.get("reviewNotes", "")))

        if category_name:
            self._log(f"AI rationale: {category_name}")

        if getattr(self, "_ai_chain_upload_after", False):
            self._ai_chain_got_metadata = True
            self._ai_chain_upload_after = False
            self._log("AI метаданные подставлены → загружаю всё в App Store Connect...")
            self._upload_full_metadata_to_apple()

    def _normalize_keywords_locally(self):
        raw = self._line_text(self.meta_keywords_input)
        seen = []
        for item in _split_keyword_parts(raw):
            normalized = re.sub(r"\s+", "", item.strip().lower())
            if normalized and normalized not in seen:
                seen.append(normalized)
        result = ",".join(seen)
        result, _changed = truncate_keywords_to_limit(result, METADATA_FIELD_LIMITS["keywords"])
        self.meta_keywords_input.setText(result)
        self._log(f"Keywords нормализованы локально: {len(result)}/100")

    def _remove_dashes_locally(self):
        replacements = {"—": " ", "–": " ", "-": " "}
        for widget in [
            self.meta_description_input, self.meta_promotional_text_input,
            self.meta_whats_new_input, self.meta_review_notes_input
        ]:
            text = self._text_edit_text(widget)
            for src, dst in replacements.items():
                text = text.replace(src, dst)
            widget.setPlainText(re.sub(r"\s{2,}", " ", text).strip())
        for widget in [self.meta_keywords_input, self.meta_subtitle_input]:
            text = self._line_text(widget)
            for src, dst in replacements.items():
                text = text.replace(src, dst)
            widget.setText(re.sub(r"\s{2,}", " ", text).strip())
        self._log("Dash символы удалены локально из основных ASO-полей.")

    def _truncate_to_limits_locally(self):
        subtitle, _ = truncate_metadata_field("subtitle", self._line_text(self.meta_subtitle_input))
        keywords, _ = truncate_metadata_field("keywords", self._line_text(self.meta_keywords_input))
        promo, _ = truncate_metadata_field("promotionalText", self._text_edit_text(self.meta_promotional_text_input))
        description, _ = truncate_metadata_field("description", self._text_edit_text(self.meta_description_input))
        self.meta_subtitle_input.setText(subtitle)
        self.meta_keywords_input.setText(keywords)
        self.meta_promotional_text_input.setPlainText(promo)
        self.meta_description_input.setPlainText(description)
        self._log("Поля локально сокращены до лимитов Apple (целые слова/keywords с конца).")

    def _run_fix_action(self, mode):
        if mode == "normalize_keywords":
            self._normalize_keywords_locally()
        elif mode == "remove_dashes":
            self._remove_dashes_locally()
        elif mode == "shorten_to_limits":
            self._truncate_to_limits_locally()
            self._generate_ai_metadata("shorten_to_limits")
        elif mode == "fix_banned_words":
            self._generate_ai_metadata("fix_banned_words")

    def _fetch_tests(self):
        self._save_env_to_file()
        api_creds = {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "app_id": self.app_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip()
        }
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все настройки API для загрузки тестов.")
            return

        self.btn_fetch_tests.setEnabled(False)
        self.update_name_combo.clear()
        self._log("Запрашиваю список тестов...")

        self.fetcher = FetchExperimentsWorker(api_creds)
        self.fetcher.log_msg.connect(self._log)
        self.fetcher.experiments_fetched.connect(self._populate_tests_combo)
        self.fetcher.finished.connect(lambda: self.btn_fetch_tests.setEnabled(True))
        self.fetcher.start()

    def _populate_tests_combo(self, exp_names):
        if exp_names:
            self.update_name_combo.addItems(exp_names)
        else:
            self._log("В текущей версии приложения нет созданных тестов.")

    def _refresh_screenshot_locales(self):
        worker = getattr(self, "locale_refresh_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._save_env_to_file()
        api_creds = {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "app_id": self.app_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip()
        }
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все настройки API для обновления локалей.")
            if getattr(self, "_pending_chain_screenshots_upload", False):
                self._pending_chain_screenshots_upload = False
                self._log("Цепочка: локали не получены — upload скринов отменён.")
            return
        self.btn_refresh_locales.setEnabled(False)
        self.locale_refresh_worker = RefreshLocalesWorker(api_creds)
        self.locale_refresh_worker.log_msg.connect(self._log)
        self.locale_refresh_worker.locales_fetched.connect(self._apply_active_screenshot_locales)
        self.locale_refresh_worker.finished.connect(self._on_screenshot_locale_refresh_finished)
        self.locale_refresh_worker.start()

    def _on_screenshot_locale_refresh_finished(self):
        if hasattr(self, "btn_refresh_locales"):
            self.btn_refresh_locales.setEnabled(True)
        if getattr(self, "_pending_chain_screenshots_upload", False):
            self._pending_chain_screenshots_upload = False
            self._log("Цепочка: локали не получены — upload скринов отменён.")

    def _apply_active_screenshot_locales(self, active_locales):
        self.upload_locale_picker.set_available_locales(active_locales)
        self._update_upload_summary()
        if getattr(self, "_pending_chain_screenshots_upload", False):
            self._pending_chain_screenshots_upload = False
            QTimer.singleShot(0, self._finish_translation_screenshots_chain)

    def _prefetch_active_locales(self, silent=True):
        if self._worker_is_running("locale_prefetch_worker"):
            return
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            return
        worker = RefreshLocalesWorker(api_creds)
        if not silent:
            worker.log_msg.connect(self._log)
        else:
            worker.log_msg.connect(lambda msg: None)
        worker.locales_fetched.connect(self._apply_prefetched_locales)
        self._track_worker("locale_prefetch_worker", worker)
        worker.start()

    def _apply_prefetched_locales(self, active_locales):
        if hasattr(self, "upload_locale_picker"):
            self.upload_locale_picker.set_available_locales(active_locales)
            self._update_upload_summary()
        if hasattr(self, "translation_target_picker"):
            self._apply_active_translation_locales(active_locales)
        self._log(f"Prefetch: активных локалей {len(active_locales or [])}.")

    def _optimize_images_before_upload(self):
        return (
            getattr(self, "optimize_images_checkbox", None)
            and self.optimize_images_checkbox.isChecked()
        )

    def _validate_screenshot_upload_prereqs(self):
        target_locales = self.upload_locale_picker.get_selected_locales()
        if not target_locales:
            self._log("Ошибка: Выберите хотя бы одну локаль на вкладке «ЗАГРУЗКА СКРИНОВ».")
            return False
        if not self.upload_jpeg_files:
            self._log("Ошибка: Выберите файлы скриншотов на вкладке «ЗАГРУЗКА СКРИНОВ».")
            return False
        if len(self.upload_jpeg_files) > APP_SCREENSHOT_MAX_PER_SET:
            self._log(
                f"⚠️ Выбрано {len(self.upload_jpeg_files)} файлов. "
                f"Apple допускает максимум {APP_SCREENSHOT_MAX_PER_SET} на локаль — лишние будут пропущены."
            )
        return True

    def _set_screenshot_mode_checkboxes(self, active):
        """Взаимное исключение режимов: '67', '65_direct', '65_crop_png' или None."""
        modes = {
            "67": self.resize_screenshots_checkbox,
            "65_direct": self.iphone_65_direct_checkbox,
            "65_crop_png": self.iphone_65_crop_png_checkbox,
        }
        for key, checkbox in modes.items():
            checkbox.blockSignals(True)
            if active is None:
                checkbox.setEnabled(True)
            elif key == active:
                checkbox.setChecked(True)
                checkbox.setEnabled(True)
            else:
                checkbox.setChecked(False)
                checkbox.setEnabled(False)
            checkbox.blockSignals(False)

    def _on_iphone_65_direct_toggled(self, checked):
        if checked:
            self._set_screenshot_mode_checkboxes("65_direct")
        else:
            self._set_screenshot_mode_checkboxes(None)

    def _on_iphone_65_crop_png_toggled(self, checked):
        if checked:
            self._set_screenshot_mode_checkboxes("65_crop_png")
        else:
            self._set_screenshot_mode_checkboxes(None)

    def _on_resize_screenshots_toggled(self, checked):
        if checked:
            self._set_screenshot_mode_checkboxes("67")
        else:
            self._set_screenshot_mode_checkboxes(None)

    def _on_metadata_upload_finished(self, success):
        self.start_btn.setEnabled(True)
        if not success or not self.chain_screenshots_checkbox.isChecked():
            return
        if not self._validate_screenshot_upload_prereqs():
            self._log("Авто-загрузка скринов пропущена: проверьте вкладку «ЗАГРУЗКА СКРИНОВ».")
            return
        if self._optimize_images_before_upload():
            self._log("Метаданные загружены. Запуск оптимизации → upload скринов...")
        else:
            self._log("Метаданные загружены. Запуск upload скринов (без оптимизации)...")
        self._start_screenshot_upload()

    def _figma_api_token(self):
        if hasattr(self, "figma_token_input"):
            typed = self.figma_token_input.text().strip()
            if typed:
                return typed
        return resolve_figma_token()

    def _pick_downloads_screenshots(self):
        paths = _list_downloads_screenshot_paths()
        if not paths:
            QMessageBox.information(
                self,
                "Загрузки",
                "В Загрузках нет скринов (Simulator Screenshot / Screenshot / Screen Shot).\n\n"
                f"Положи JPG/PNG в:\n{DOWNLOADS_DIR}\n"
                "потом снова нажми «Взять из Загрузки».",
            )
            return
        self._set_upload_jpeg_files(paths)
        self._log(f"Загрузки: взято {len(paths)} скрин(ов) из {DOWNLOADS_DIR}")

    def _pick_desktop_screenshots(self):
        paths = _list_desktop_screenshot_paths()
        if not paths:
            QMessageBox.information(
                self,
                "Desktop",
                f"На рабочем столе нет PNG/JPG/WEBP.\n\nПапка: {DESKTOP_DIR}",
            )
            return
        self._set_upload_jpeg_files(paths)
        self._log(f"Desktop: взято {len(paths)} файл(ов) с {DESKTOP_DIR}")

    def _select_screenshot_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Папка со скриншотами", DOWNLOADS_DIR)
        if not folder:
            return
        names = _list_screenshot_files_in_folder(folder)
        paths = [os.path.join(folder, name) for name in names]
        if not paths:
            QMessageBox.information(self, "Папка", f"В папке нет картинок скриншотов:\n{folder}")
            return
        self._set_upload_jpeg_files(paths)
        self._log(f"Папка: взято {len(paths)} файл(ов) из {folder}")

    def _start_figma_import(self, upload_after=False):
        figma_url = self.figma_url_input.text().strip() if hasattr(self, "figma_url_input") else ""
        token = self._figma_api_token()
        if not token:
            QMessageBox.warning(
                self,
                "Figma",
                "Укажите FIGMA_TOKEN на вкладке «Настройки API» "
                "(Figma → Settings → Security → Personal access tokens).",
            )
            return
        if not figma_url:
            QMessageBox.warning(self, "Figma", "Вставьте ссылку на файл Figma на вкладке «Скриншоты».")
            return
        try:
            parse_figma_url(figma_url)
        except ValueError as e:
            QMessageBox.warning(self, "Figma", str(e))
            return
        if upload_after and not self.upload_locale_picker.get_selected_locales():
            QMessageBox.warning(self, "Figma", "Выберите хотя бы одну локаль перед upload.")
            return
        if hasattr(self, "figma_import_worker") and self.figma_import_worker is not None and self.figma_import_worker.isRunning():
            QMessageBox.information(self, "Figma", "Импорт уже выполняется.")
            return

        self._figma_upload_after_import = bool(upload_after)
        self.btn_figma_import.setEnabled(False)
        self.btn_figma_import_upload.setEnabled(False)
        self._log("Figma: старт импорта скриншотов...")
        self.figma_import_worker = FigmaImportWorker(figma_url, token)
        self.figma_import_worker.log_msg.connect(self._log)
        self.figma_import_worker.import_finished.connect(self._on_figma_import_finished)
        self.figma_import_worker.import_failed.connect(self._on_figma_import_failed)
        self.figma_import_worker.finished.connect(self._on_figma_import_worker_finished)
        self.figma_import_worker.start()

    def _on_figma_import_worker_finished(self):
        self.btn_figma_import.setEnabled(True)
        self.btn_figma_import_upload.setEnabled(True)

    def _on_figma_import_failed(self, message):
        self._log(f"Figma error: {message}")
        QMessageBox.warning(self, "Figma", message)

    def _on_figma_import_finished(self, paths):
        paths = list(paths or [])
        self._set_upload_jpeg_files(paths)
        self._log(f"Figma: в список файлов добавлено {len(paths)} скрин(ов).")
        if getattr(self, "_figma_upload_after_import", False):
            self._figma_upload_after_import = False
            if not self._validate_screenshot_upload_prereqs():
                return
            if self._optimize_images_before_upload():
                self._log("Figma: запуск оптимизации → upload в App Store...")
            else:
                self._log("Figma: запуск upload в App Store (без оптимизации)...")
            self._start_screenshot_upload()
        else:
            QMessageBox.information(
                self,
                "Figma",
                f"Скачано файлов: {len(paths)}.\n"
                "Проверьте список и нажмите «ЗАГРУЗИТЬ СКРИНШОТЫ В APPLE».",
            )

    def _select_jpeg_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы скриншотов", "", SCREENSHOT_FILE_DIALOG_FILTER
        )
        if files:
            self._set_upload_jpeg_files(files)

    def _set_upload_jpeg_files(self, files):
        self.upload_jpeg_files = sorted([
            f for f in files if _is_screenshot_image_file(f)
        ])
        self.lbl_selected_jpegs.setText(f"Выбрано файлов: {len(self.upload_jpeg_files)}")
        self.upload_files_preview.clear()
        for index, file_path in enumerate(self.upload_jpeg_files[:12], start=1):
            self.upload_files_preview.addItem(f"{index}. {os.path.basename(file_path)}")
        if len(self.upload_jpeg_files) > 12:
            self.upload_files_preview.addItem(f"...и еще {len(self.upload_jpeg_files) - 12} файлов")
        self._update_upload_files_warning()
        self._update_upload_summary()

    def _update_upload_files_warning(self):
        if not self.upload_jpeg_files:
            self.upload_files_warning.setText(
                "Выберите или перетащите скриншоты (PNG, JPG, WEBP и др.) для загрузки."
            )
            return
        basenames = [os.path.basename(path) for path in self.upload_jpeg_files]
        numbered = all(re.match(r"^\d+", name) for name in basenames)
        if len(self.upload_jpeg_files) > 1 and not numbered:
            self.upload_files_warning.setText(
                "Проверьте порядок файлов: имена не начинаются с номера, сортировка будет по имени."
            )
        else:
            self.upload_files_warning.setText("Порядок файлов будет по имени после сортировки.")

    def _update_upload_summary(self):
        if not hasattr(self, "upload_summary_label"):
            return
        locale_count = len(self.upload_locale_picker.get_selected_locales()) if hasattr(self, "upload_locale_picker") else 0
        file_count = len(getattr(self, "upload_jpeg_files", []))
        self.upload_summary_label.setText(
            f"Локалей: {locale_count} · Файлов: {file_count} · Всего upload задач: {locale_count * file_count}"
        )

    def _start_screenshot_upload(self):
        self._save_env_to_file()
        api_creds = {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "app_id": self.app_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip()
        }
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все поля в разделе НАСТРОЙКИ API.")
            return
        target_locales = self.upload_locale_picker.get_selected_locales()
        if not target_locales:
            self._log("Ошибка: Выберите хотя бы одну локаль для загрузки скринов.")
            return
        if not self._validate_screenshot_upload_prereqs():
            return
        self.btn_execute_upload.setEnabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Загрузка: 0%")
        self.screenshot_upload_worker = ScreenshotUploadWorker(
            api_creds,
            target_locales,
            self.upload_jpeg_files,
            optimize_images=self._optimize_images_before_upload(),
            version_url_defaults=self._version_url_defaults_from_gui(),
            resize_to_iphone_67=self.resize_screenshots_checkbox.isChecked(),
            iphone_65_direct=self.iphone_65_direct_checkbox.isChecked(),
            iphone_65_crop_png=self.iphone_65_crop_png_checkbox.isChecked(),
            max_workers=self._selected_screenshot_upload_workers(),
        )
        self.screenshot_upload_worker.log_msg.connect(self._log)
        self.screenshot_upload_worker.progress_update.connect(self._update_progress)
        self.screenshot_upload_worker.upload_finished.connect(self._on_screenshot_upload_finished)
        self.screenshot_upload_worker.finished.connect(lambda: self.btn_execute_upload.setEnabled(True))
        self.screenshot_upload_worker.start()

    def _on_screenshot_upload_finished(self, success):
        if not success:
            return
        source_files = list(getattr(self, "upload_jpeg_files", []) or [])
        deleted, skipped = _delete_desktop_screenshot_files(source_files, logger=self._log)
        if deleted:
            self._log(f"✅ Удалено скринов из Загрузки/Desktop: {len(deleted)}")
            remaining = [path for path in source_files if path not in deleted]
            self.upload_jpeg_files = remaining
            if hasattr(self, "upload_files_preview"):
                self.upload_files_preview.clear()
                for path in remaining:
                    self.upload_files_preview.addItem(os.path.basename(path))
            if hasattr(self, "lbl_selected_jpegs"):
                if remaining:
                    self.lbl_selected_jpegs.setText(f"Выбрано файлов: {len(remaining)}")
                else:
                    self.lbl_selected_jpegs.setText("Файлы не выбраны")
            self._update_upload_summary()
            self._update_upload_files_warning()
        elif skipped:
            self._log(
                "Скрины не в Загрузках/Desktop — файлы не удалял "
                f"(источников вне этих папок: {len(skipped)})."
            )

    def _start_process(self):
        self._save_env_to_file()
        
        api_creds = {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "app_id": self.app_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip()
        }

        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все поля в разделе НАСТРОЙКИ API.")
            return

        current_tab_index = self.tabs.currentIndex()
        target_variants = []
        target_locales = []
        
        if current_tab_index == 0:
            mode = "create"
            test_name = self.create_name_input.text().strip()
            traffic = self.create_traffic_input.text().strip() or "75"
            if not test_name:
                self._log("Ошибка: Укажите имя для нового теста.")
                return
        elif current_tab_index == 1:
            mode = "update"
            test_name = self.update_name_combo.currentText().strip()
            traffic = ""
            
            if self.chk_var_a.isChecked(): target_variants.append(self.chk_var_a.property("variant_id"))
            if self.chk_var_b.isChecked(): target_variants.append(self.chk_var_b.property("variant_id"))
            if self.chk_var_c.isChecked(): target_variants.append(self.chk_var_c.property("variant_id"))
            
            target_locales = self.update_locale_picker.get_selected_locales()
            
            if not test_name:
                self._log("Ошибка: Не выбран тест. Нажмите 'Загрузить список'.")
                return
            if not target_variants:
                self._log("Ошибка: Отметьте галочкой хотя бы один вариант для обновления.")
                return
            if not target_locales:
                self._log("Ошибка: Выберите хотя бы одну локаль в таблице.")
                return
        elif current_tab_index == 2:
            self.start_btn.setEnabled(False)
            self.progress_bar.setValue(0)
            self.time_label.setText("Метаданные: 0%")
            self.log_area.clear()
            self._log(
                "Цепочка: Privacy + Support → генерация метаданных AI → "
                "полная загрузка в App Store Connect..."
            )
            if not self._run_privacy_support_oneshot(chain_full_metadata=True):
                self.start_btn.setEnabled(True)
            return
        elif current_tab_index in (3, 4):
            self._log("Для этой вкладки используйте кнопки внутри вкладки.")
            return

        self.start_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Осталось: --:--")
        self.log_area.clear()
        
        self.worker = AutomationWorker(
            mode, api_creds, test_name, traffic, target_variants, target_locales,
            self.variants_paths,
            optimize_images=self._optimize_images_before_upload(),
            max_workers=self._selected_screenshot_upload_workers(),
        )
        self.worker.log_msg.connect(self._log)
        self.worker.progress_update.connect(self._update_progress)
        self.worker.finished.connect(lambda: self.start_btn.setEnabled(True))
        self.worker.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    QTimer.singleShot(0, window._maximize_to_screen)
    sys.exit(app.exec())
