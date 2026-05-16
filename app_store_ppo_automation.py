import os
import sys
import time
import tempfile
import jwt
import requests
import threading
import concurrent.futures
import json
import re
from dotenv import load_dotenv
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLineEdit, QPushButton, QTextEdit, QFileDialog, QLabel, QGroupBox,
    QProgressBar, QTabWidget, QCheckBox, QScrollArea, QComboBox, QToolButton,
    QDialog, QListWidget, QListWidgetItem, QMessageBox
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import QThread, Signal, Qt, QSize

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
load_dotenv(ENV_PATH)

# TinyPNG: встроенный ключ (если пусто — берётся из TINYPNG_API_KEY в .env)
_BUILTIN_TINYPNG_API_KEY = ""


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

# Поля version localization, которые Apple может отклонить по статусу версии
VERSION_LOCALIZATION_STATE_OPTIONAL = ("whatsNew", "promotionalText")
APP_STORE_VERSION_STATE_OPTIONAL = ("copyright",)
APP_INFO_LOCALIZATION_UNKNOWN = ("privacyPolicyText",)  # tvOS, не iOS


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
            if logger:
                logger(f"⚠️ {key}: обрезано {len(value)} → {limit} символов")
            result[key] = value[:limit]
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
    """Конвертирует iTunes genre ID (6016) или API id (ENTERTAINMENT) в appCategories id."""
    if not category_id:
        return ""
    raw = str(category_id).strip()
    if raw in ITUNES_GENRE_TO_APP_CATEGORY:
        return ITUNES_GENRE_TO_APP_CATEGORY[raw]
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", raw):
        return raw
    return ""


def resolve_tinypng_api_key():
    env_file = _read_env_file()
    return (
        env_file.get("TINYPNG_API_KEY", "").strip()
        or os.getenv("TINYPNG_API_KEY", "").strip()
        or _BUILTIN_TINYPNG_API_KEY.strip()
    )

# Словарь: Код -> (Код_Флага, Понятное название)
DEFAULT_GEMINI_PROMPT = """Описание:
Role:
Act as a Senior Copywriter at a top-tier creative agency. Your goal is to write App Store copy that feels human, evocative, and entirely devoid of AI-speak or marketing clichés.

The Mission:
Based on the provided brief, craft an original App Store Description (max 1500 chars) and Promotional Text (max 100 chars).

Style & Execution:
Avoid these words: revolutionary, seamless, unlock, empower, unleash, ultimate, simple, solution, discover, master, stay organized.
Use staccato and flow. Alternate punchy short sentences with longer descriptive ones.
Use sensory details and specific real-world scenarios when relevant.
No bullet points in the description. No dashes. English language.

Keywords:
Act as a Senior ASO Specialist. Build a zero-waste keyword string under 100 characters including commas.
Exclude words from the App Name and Subtitle. Avoid: best, top, easy, fast, free, app, simple.
Use lowercase words separated only by commas. No dashes.

Category:
Recommend 1 Primary category and 1 Secondary category with a concise practical justification.
Base decisions only on features explicitly stated in the brief. Flag category risks honestly. No dashes.

App Review notes:
Create a brief courteous cover note in English for Apple App Review. 3 to 5 sentences.
Mention the developer name if provided. Main points to cover if true in the brief: no account registration/login, no paid content/subscriptions/IAP, no user-generated content, no ads/tracking, no camera/location/contacts/photos/microphone, no external services required for core functionality, all levels/content are bundled in the app.
Keep it professional, calm, human, and respectful. Do not make it sound like a standard template.
"""

PROMPT_PROFILES_FILE = ".prompt_profiles.json"

DEFAULT_PROMPT_PROFILES = {
    "Human premium ASO": DEFAULT_GEMINI_PROMPT,
    "Strict keywords only": """Act as a Senior ASO specialist. Generate only keywords under 100 characters including commas. Use lowercase, no spaces after commas, no duplicates, no dashes, no generic filler words, and exclude words from the app name and subtitle. Return JSON only.""",
    "Review notes clean": """Create concise App Review notes in English. Professional, calm, human. Mention only facts explicitly provided in the brief. Avoid privacy/legal overexplaining and avoid template language. Return JSON only.""",
    "Category safety": """Recommend primary and secondary App Store categories based only on explicitly stated features. Be conservative and moderation-safe. Explain category mismatch risks in categoryName. Return JSON only.""",
}

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
    os.makedirs(".flags_cache", exist_ok=True)
    missing_flags = []
    
    for code, (cc, name) in LOCALE_MAP.items():
        if not os.path.exists(f".flags_cache/{cc}.png"):
            missing_flags.append(cc)
            
    if missing_flags:
        print("Скачивание иконок флагов для интерфейса (один раз)...")
        unique_ccs = list(set(missing_flags))
        
        def fetch(cc):
            try:
                r = requests.get(f"https://flagcdn.com/w20/{cc}.png", timeout=5)
                if r.status_code == 200:
                    with open(f".flags_cache/{cc}.png", "wb") as f:
                        f.write(r.content)
            except: pass
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(fetch, unique_ccs)

class TinyPngCompressor:
    """Сжатие PNG/JPEG через TinyPNG API перед загрузкой в App Store Connect."""

    def __init__(self, api_key, logger_callback=None):
        self.api_key = (api_key or "").strip()
        self.enabled = bool(self.api_key)
        self.logger = logger_callback or (lambda _msg: None)
        self._cache = {}
        self._temp_files = []
        self._lock = threading.Lock()

    def compress(self, file_path):
        if not self.enabled:
            return file_path
        with self._lock:
            if file_path in self._cache:
                return self._cache[file_path]

        original_size = os.path.getsize(file_path)
        try:
            with open(file_path, "rb") as source:
                response = requests.post(
                    "https://api.tinify.com/shrink",
                    auth=(self.api_key, ""),
                    data=source.read(),
                    timeout=120,
                )
            response.raise_for_status()
            output_url = response.json()["output"]["url"]
            compressed = requests.get(output_url, timeout=120)
            compressed.raise_for_status()

            suffix = os.path.splitext(file_path)[1] or ".jpeg"
            fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="tinypng_")
            os.close(fd)
            with open(temp_path, "wb") as out:
                out.write(compressed.content)

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
                f"TinyPNG: {os.path.basename(file_path)} "
                f"{original_size // 1024} KB → {new_size // 1024} KB"
            )
            return temp_path
        except Exception as exc:
            self.logger(f"⚠️ TinyPNG ({os.path.basename(file_path)}): {exc}. Загружаем оригинал.")
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

    # Умная функция запроса с защитой от лимитов Apple
    def _request(self, method, endpoint, payload=None, max_retries=4):
        url = f"{self.base_url}/{endpoint}"
        
        for attempt in range(max_retries):
            token = self._generate_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            try:
                if method == "GET":
                    response = requests.get(url, headers=headers)
                elif method == "POST":
                    response = requests.post(url, headers=headers, json=payload)
                elif method == "PATCH":
                    response = requests.patch(url, headers=headers, json=payload)
                elif method == "DELETE":
                    response = requests.delete(url, headers=headers)
                    response.raise_for_status()
                    return {} 
                else:
                    return None
                    
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                # Если сервер просит притормозить (ошибка 429 или 502/503)
                if e.response is not None and e.response.status_code in [429, 502, 503]:
                    if attempt < max_retries - 1:
                        sleep_time = 2 ** attempt # Экспоненциальная пауза: 1 сек, 2 сек, 4 сек
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
                
                # Здесь тоже может быть сетевая ошибка, добавляем простые попытки
                for attempt in range(3):
                    try:
                        req = requests.put(operation["url"], headers=headers, data=chunk)
                        req.raise_for_status()
                        break
                    except requests.exceptions.RequestException:
                        if attempt == 2: raise
                        time.sleep(1)

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
                app_info_id = client.get_app_info()

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
                            self.log_msg.emit("✅ Обновлены category/возрастные и другие appInfo-поля.")
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
                all_territory_ids = client.get_all_territory_ids()
                if availability_mode == "SELECTED":
                    selected_territory_ids = set(self.metadata_config.get("availability_territories", []))
                    filtered_ids = [tid for tid in all_territory_ids if tid in selected_territory_ids]
                    if not filtered_ids:
                        raise Exception("Availability mode SELECTED: не выбрана ни одна страна.")
                    client.set_app_available_territories(filtered_ids)
                    self.log_msg.emit(f"✅ Availability обновлен: выбрано стран {len(filtered_ids)}.")
                else:
                    client.set_app_available_territories(all_territory_ids)
                    self.log_msg.emit("✅ Availability обновлен: все страны (дефолт).")
                tick("Готово availability")

            if "collects_data" in self.metadata_config:
                collects_data = bool(self.metadata_config.get("collects_data"))
                if not collects_data:
                    client.sync_app_privacy_details([], publish=True)
                    self.log_msg.emit("✅ App Privacy: установлено «не собираем данные».")
                else:
                    self.log_msg.emit("ℹ️ App Privacy: режим «собираем данные», изменения не применялись автоматически.")
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


class GeminiMetadataWorker(QThread):
    log_msg = Signal(str)
    metadata_generated = Signal(dict)
    finished = Signal()

    def __init__(self, api_key, model, locale, app_context, user_prompt, developer_name, generation_mode="all", current_value=""):
        super().__init__()
        self.api_key = api_key
        self.model = model
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
            self.log_msg.emit("Gemini: генерирую ASO-метаданные...")
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
Generate App Store metadata for locale {self.locale}.
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
  "primaryCategory": "Apple App Store category ID if confident, otherwise empty string",
  "secondaryCategory": "Apple App Store category ID if confident, otherwise empty string",
  "categoryName": "human-readable primary and secondary category suggestion with short rationale",
  "reviewNotes": "brief courteous App Review note if requested, otherwise empty string"
}}

User prompt / creative direction:
{self.user_prompt}

Developer name to mention if relevant:
{self.developer_name or "Not provided"}

App brief / technical task:
{self.app_context}

Hard safety rules:
- Base claims only on the app brief.
- Avoid unsupported privacy, medical, financial, login, subscription, tracking, or ads claims unless explicitly stated in the brief.
- Never use em dashes or en dashes if the user prompt forbids dashes.
- Keep keywords lowercase and comma-separated.
"""
            endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.7,
                    "responseMimeType": "application/json"
                }
            }
            response = requests.post(
                endpoint,
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            data = response.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)
            if not text:
                raise Exception("Gemini вернул пустой ответ.")
            generated = self._extract_json(text)
            self.metadata_generated.emit(generated)
            self.log_msg.emit("✅ Gemini сгенерировал метаданные. Проверьте поля перед загрузкой в Apple.")
        except Exception as e:
            error_text = str(e)
            if hasattr(e, "response") and e.response is not None:
                error_text += f" | {e.response.text}"
            self.log_msg.emit(f"Ошибка Gemini: {error_text}")
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

    def __init__(self, mode, api_creds, test_name, traffic, target_variants, target_locales, variants_paths, tinypng_api_key=""):
        super().__init__()
        self.mode = mode 
        self.api_creds = api_creds
        self.test_name = test_name
        self.traffic = traffic
        self.target_variants = target_variants
        self.target_locales = target_locales 
        self.variants_paths = variants_paths
        self.tinypng_api_key = tinypng_api_key

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
                    files = sorted([f for f in os.listdir(f_path) if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
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

            total_tasks = sum([1 + len(task["files"]) for task in upload_tasks])
            completed_tasks = 0
            start_time = time.time()
            progress_lock = threading.Lock() # Блокировка для безопасного обновления прогресса
            tinypng = TinyPngCompressor(self.tinypng_api_key, self.log_msg.emit)
            if tinypng.enabled:
                self.log_msg.emit("TinyPNG: сжатие скринов перед загрузкой в App Store Connect...")

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
                
                sets = client.get_screenshot_sets(task['loc_id'])
                target_set_id = None
                
                for s in sets:
                    if s["attributes"]["screenshotDisplayType"] == "APP_IPHONE_67":
                        target_set_id = s["id"]
                    for old_sc in client.get_screenshots(s["id"]):
                        client.delete_screenshot(old_sc["id"])
                        
                if not target_set_id:
                    target_set_id = client.create_screenshot_set(task['loc_id'], "APP_IPHONE_67")
                tick() # Тик после создания сета/очистки

                for index, filename in enumerate(task['files'], start=1):
                    filepath = os.path.join(task['folder'], filename)
                    ext = os.path.splitext(filename)[1].lower() 
                    new_filename = f"{index}{ext}"
                    upload_path = tinypng.compress(filepath)
                    client.upload_screenshot(target_set_id, upload_path, new_filename)
                    tick() # Тик после загрузки файла

            self.log_msg.emit(f"Начинаем многопоточную загрузку ({len(upload_tasks)} локалей)...")
            
            # Запускаем пул потоков (5 параллельных загрузок)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(upload_worker, t) for t in upload_tasks]
                    
                    # Ожидаем завершения всех потоков и отлавливаем ошибки
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            future.result()
                        except Exception as e:
                            raise Exception(f"Сбой в одном из потоков: {str(e)}")
            finally:
                tinypng.cleanup()

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

class ScreenshotUploadWorker(QThread):
    log_msg = Signal(str)
    progress_update = Signal(int, str)
    finished = Signal()

    def __init__(self, api_creds, target_locales, jpeg_files, tinypng_api_key=""):
        super().__init__()
        self.api_creds = api_creds
        self.target_locales = target_locales
        self.jpeg_files = jpeg_files
        self.tinypng_api_key = tinypng_api_key

    def run(self):
        tinypng = None
        try:
            tinypng = TinyPngCompressor(self.tinypng_api_key, self.log_msg.emit)
            if tinypng.enabled:
                self.log_msg.emit("TinyPNG: сжатие скринов перед загрузкой...")
                prepared_files = [tinypng.compress(path) for path in self.jpeg_files]
            else:
                prepared_files = self.jpeg_files

            client = ASCClient(
                self.api_creds["issuer"], self.api_creds["key_id"],
                self.api_creds["p8_path"], self.api_creds["app_id"], self.log_msg.emit
            )
            version_id = client.get_latest_app_store_version()
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
                    localization_id = loc_to_id.get(locale)
                    if not localization_id:
                        self.log_msg.emit(f"[{locale}] Локаль не открыта, создаем...")
                        localization_id = local_client.create_version_localization(version_id, locale, {})
                        loc_to_id[locale] = localization_id
                        time.sleep(0.5)
                self.log_msg.emit(f"[{locale}] Подготовка screenshot set APP_IPHONE_67...")
                sets = local_client._request("GET", f"appStoreVersionLocalizations/{localization_id}/appScreenshotSets").get("data", [])
                target_set = next((s for s in sets if s.get("attributes", {}).get("screenshotDisplayType") == "APP_IPHONE_67"), None)
                if target_set is None:
                    payload = {
                        "data": {
                            "type": "appScreenshotSets",
                            "attributes": {"screenshotDisplayType": "APP_IPHONE_67"},
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

                for idx, file_path in enumerate(prepared_files, start=1):
                    file_name = f"{idx}_{os.path.basename(self.jpeg_files[idx - 1])}"
                    self.log_msg.emit(f"[{locale}] Upload {idx}/{len(prepared_files)}: {file_name}")
                    local_client.upload_screenshot(target_set_id, file_path, file_name)
                    tick()
                    time.sleep(1)

            max_workers = min(5, max(1, len(self.target_locales)))
            self.log_msg.emit(f"Запускаем параллельную загрузку скринов: потоков {max_workers}.")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(upload_locale, locale) for locale in self.target_locales]
                for future in concurrent.futures.as_completed(futures):
                    future.result()

            self.log_msg.emit("✅ Upload скриншотов завершен.")
        except Exception as e:
            self.log_msg.emit(f"Ошибка Upload: {str(e)}")
        finally:
            if tinypng:
                tinypng.cleanup()
            self.finished.emit()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        ensure_flags_downloaded()
        
        self.setWindowTitle("PPO Automation Control (PRO Mode)")
        self.resize(900, 980)
        self.variants_paths = {"Variant A": "", "Variant B": "", "Variant C": ""}
        self._setup_ui()
        self._apply_styles()
        self._ensure_tinypng_key_visible()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.lbl_api = QLabel("НАСТРОЙКИ API (Авто-сохранение)")
        layout.addWidget(self.lbl_api)

        self.issuer_input = QLineEdit(os.getenv("ISSUER_ID", ""))
        self.issuer_input.setPlaceholderText("ISSUER_ID")
        layout.addWidget(self.issuer_input)

        self.key_input = QLineEdit(os.getenv("KEY_ID", ""))
        self.key_input.setPlaceholderText("KEY_ID")
        layout.addWidget(self.key_input)

        self.app_input = QLineEdit(os.getenv("APP_ID", ""))
        self.app_input.setPlaceholderText("APP_ID (Apple ID)")
        layout.addWidget(self.app_input)

        key_layout = QHBoxLayout()
        self.p8_path_input = QLineEdit(os.getenv("PRIVATE_KEY_PATH", ""))
        self.p8_path_input.setPlaceholderText("Путь к файлу AuthKey_...p8")
        self.p8_path_input.editingFinished.connect(self._autofill_key_id_from_p8_path)
        self.btn_select_p8 = QPushButton("Выбрать .p8")
        self.btn_select_p8.clicked.connect(self._select_p8_file)
        key_layout.addWidget(self.p8_path_input)
        key_layout.addWidget(self.btn_select_p8)
        layout.addLayout(key_layout)

        self.tinypng_key_input = QLineEdit(resolve_tinypng_api_key())
        self.tinypng_key_input.setPlaceholderText("TinyPNG API key (сохраняется в .env)")
        self.tinypng_key_input.editingFinished.connect(self._save_env_to_file)
        layout.addWidget(QLabel("TinyPNG API key (сжатие скринов, всегда включено):"))
        layout.addWidget(self.tinypng_key_input)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.tab_create = QWidget()
        create_layout = QVBoxLayout(self.tab_create)
        self.create_name_input = QLineEdit()
        self.create_name_input.setPlaceholderText("Имя нового теста (например: New Icon Test)")
        self.create_traffic_input = QLineEdit("75")
        self.create_traffic_input.setPlaceholderText("Трафик (например: 75)")
        create_layout.addWidget(QLabel("Введите имя для нового теста в App Store Connect:"))
        create_layout.addWidget(self.create_name_input)
        create_layout.addWidget(QLabel("Процент трафика:"))
        create_layout.addWidget(self.create_traffic_input)
        create_layout.addStretch()
        self.tabs.addTab(self.tab_create, "🆕 СОЗДАТЬ НОВЫЙ ТЕСТ")

        self.tab_update = QWidget()
        update_layout = QVBoxLayout(self.tab_update)
        
        update_layout.addWidget(QLabel("1. Выберите существующий тест из App Store Connect:"))
        
        test_select_layout = QHBoxLayout()
        self.update_name_combo = QComboBox()
        self.btn_fetch_tests = QPushButton("🔄 Загрузить список")
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

        update_layout.addWidget(QLabel("3. Выберите локали для обновления:"))
        
        btn_layout = QHBoxLayout()
        btn_sel_all = QPushButton("Выбрать все")
        btn_desel_all = QPushButton("Снять все")
        btn_sel_all.clicked.connect(lambda: self._toggle_locales(True))
        btn_desel_all.clicked.connect(lambda: self._toggle_locales(False))
        btn_layout.addWidget(btn_sel_all)
        btn_layout.addWidget(btn_desel_all)
        update_layout.addLayout(btn_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_widget = QWidget()
        grid = QGridLayout(scroll_widget)
        
        self.locale_checkboxes = []
        sorted_locales = sorted(LOCALE_MAP.items(), key=lambda item: item[1][1])
        
        row, col = 0, 0
        for code, (cc, name) in sorted_locales:
            cb = QCheckBox(f" {name}")
            cb.setProperty("locale_code", code)
            
            icon_path = f".flags_cache/{cc}.png"
            if os.path.exists(icon_path):
                cb.setIcon(QIcon(icon_path))
                cb.setIconSize(QSize(20, 15))
                
            self.locale_checkboxes.append(cb)
            grid.addWidget(cb, row, col)
            col += 1
            if col > 3:
                col = 0
                row += 1
                
        scroll_area.setWidget(scroll_widget)
        update_layout.addWidget(scroll_area)
        
        self.tabs.addTab(self.tab_update, "🔄 ОБНОВИТЬ СУЩЕСТВУЮЩИЙ")

        self.tab_metadata = QWidget()
        metadata_layout = QVBoxLayout(self.tab_metadata)
        metadata_layout.addWidget(QLabel("Первичная загрузка метаданных приложения через GUI:"))
        metadata_layout.addWidget(QLabel(
            "Заполните поля ниже — программа сама соберет payload для App Store Connect. "
            "JSON оставлен только для продвинутых custom_requests."
        ))

        metadata_buttons_layout = QHBoxLayout()
        self.btn_load_metadata_json = QPushButton("📄 Импорт JSON в поля")
        self.btn_load_metadata_json.clicked.connect(self._load_metadata_json)
        self.btn_insert_metadata_example = QPushButton("🧩 Заполнить пример")
        self.btn_insert_metadata_example.clicked.connect(self._insert_metadata_example)
        self.btn_preview_metadata = QPushButton("🔍 Preview & Validate")
        self.btn_preview_metadata.clicked.connect(self._preview_metadata)
        self.btn_pull_metadata = QPushButton("⬇️ Pull from Apple")
        self.btn_pull_metadata.clicked.connect(self._pull_metadata_from_apple)
        metadata_buttons_layout.addWidget(self.btn_load_metadata_json)
        metadata_buttons_layout.addWidget(self.btn_insert_metadata_example)
        metadata_buttons_layout.addWidget(self.btn_preview_metadata)
        metadata_buttons_layout.addWidget(self.btn_pull_metadata)
        metadata_layout.addLayout(metadata_buttons_layout)

        metadata_scroll = QScrollArea()
        metadata_scroll.setWidgetResizable(True)
        metadata_widget = QWidget()
        metadata_form_layout = QVBoxLayout(metadata_widget)

        locale_group = QGroupBox("Локаль")
        locale_form = QFormLayout(locale_group)
        self.meta_locale_combo = QComboBox()
        for code, (_, name) in sorted(LOCALE_MAP.items(), key=lambda item: item[1][1]):
            self.meta_locale_combo.addItem(f"{name} ({code})", code)
        default_locale_index = self.meta_locale_combo.findData("en-US")
        if default_locale_index >= 0:
            self.meta_locale_combo.setCurrentIndex(default_locale_index)
        locale_select_layout = QHBoxLayout()
        self.btn_fetch_primary_locale = QPushButton("🌐 Взять primary locale из Apple")
        self.btn_fetch_primary_locale.clicked.connect(self._fetch_primary_locale)
        locale_select_layout.addWidget(self.meta_locale_combo, stretch=1)
        locale_select_layout.addWidget(self.btn_fetch_primary_locale)
        locale_form.addRow("Язык:", locale_select_layout)
        metadata_form_layout.addWidget(locale_group)

        version_group = QGroupBox("Version metadata")
        version_form = QFormLayout(version_group)
        self.meta_description_input = QTextEdit()
        self.meta_description_input.setFixedHeight(110)
        self.meta_description_input.setPlaceholderText("Description / описание приложения")
        self.meta_keywords_input = QLineEdit()
        self.meta_keywords_input.setPlaceholderText("keyword1,keyword2,keyword3")
        self.meta_promotional_text_input = QTextEdit()
        self.meta_promotional_text_input.setFixedHeight(70)
        self.meta_promotional_text_input.setPlaceholderText("Promotional text")
        self.meta_whats_new_input = QTextEdit()
        self.meta_whats_new_input.setFixedHeight(80)
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
        if not hasattr(self, "ai_field_buttons"):
            self.ai_field_buttons = []
        self._add_metadata_field_row(version_form, "Description:", self.meta_description_input, "description")
        self._add_metadata_field_row(version_form, "Keywords:", self.meta_keywords_input, "keywords")
        self._add_metadata_field_row(version_form, "Promotional text:", self.meta_promotional_text_input, "promotional_text")
        self._add_metadata_field_row(version_form, "What's New:", self.meta_whats_new_input, "whats_new")
        version_form.addRow("", self.meta_include_whats_new_checkbox)
        version_form.addRow("Support URL:", self.meta_support_url_input)
        version_form.addRow("Marketing URL:", self.meta_marketing_url_input)
        metadata_form_layout.addWidget(version_group)

        app_info_group = QGroupBox("App info")
        app_info_form = QFormLayout(app_info_group)
        self.meta_name_input = QLineEdit()
        self.meta_name_input.setPlaceholderText("App name")
        self.meta_subtitle_input = QLineEdit()
        self.meta_subtitle_input.setPlaceholderText("Subtitle")
        self.meta_copyright_input = QLineEdit()
        self.meta_copyright_input.setPlaceholderText("© Your Developer Name")
        self.meta_privacy_policy_url_input = QLineEdit()
        self.meta_privacy_policy_url_input.setPlaceholderText("https://example.com/privacy")
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
        app_info_form.addRow("Name:", self.meta_name_input)
        self._add_metadata_field_row(app_info_form, "Subtitle:", self.meta_subtitle_input, "subtitle")
        app_info_form.addRow("Copyright:", self.meta_copyright_input)
        app_info_form.addRow("Privacy Policy URL:", self.meta_privacy_policy_url_input)
        app_info_form.addRow("Privacy Choices URL:", self.meta_privacy_choices_url_input)
        self._add_metadata_field_row(app_info_form, "Primary Category:", self.meta_primary_category_input, "category")
        self._add_metadata_field_row(app_info_form, "Secondary Category:", self.meta_secondary_category_input, "category")
        app_info_form.addRow("Age rating default:", self.meta_kids_age_band_input)
        app_info_form.addRow("App availability:", self.meta_availability_mode_input)
        app_info_form.addRow("", self.meta_select_territories_btn)
        app_info_form.addRow("Data collection:", self.meta_collects_data_checkbox)
        metadata_form_layout.addWidget(app_info_group)

        review_group = QGroupBox("App Review notes")
        review_form = QFormLayout(review_group)
        self.meta_review_first_name_input = QLineEdit()
        self.meta_review_last_name_input = QLineEdit()
        self.meta_review_phone_input = QLineEdit()
        self.meta_review_phone_input.setPlaceholderText("+1 555 010 0611")
        self.meta_review_email_input = QLineEdit()
        self.meta_review_notes_input = QTextEdit()
        self.meta_review_notes_input.setFixedHeight(90)
        review_form.addRow("Contact first name:", self.meta_review_first_name_input)
        review_form.addRow("Contact last name:", self.meta_review_last_name_input)
        review_form.addRow("Contact phone:", self.meta_review_phone_input)
        review_form.addRow("Contact email:", self.meta_review_email_input)
        self._add_metadata_field_row(review_form, "Notes:", self.meta_review_notes_input, "review_notes")
        metadata_form_layout.addWidget(review_group)

        ai_group = QGroupBox("AI генерация (Gemini)")
        ai_layout = QVBoxLayout(ai_group)
        ai_form = QFormLayout()
        self.gemini_key_input = QLineEdit(os.getenv("GEMINI_API_KEY", ""))
        self.gemini_key_input.setPlaceholderText("Gemini API key")
        self.gemini_key_input.setEchoMode(QLineEdit.Password)
        self.gemini_model_input = QLineEdit(os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
        self.gemini_model_input.setPlaceholderText("gemini-2.5-flash")
        self.ai_developer_name_input = QLineEdit("")
        self.ai_developer_name_input.setPlaceholderText("Developer name для App Review notes")
        self.ai_developer_name_input.textChanged.connect(self._apply_developer_name_to_review_fields)
        self.ai_app_context_input = QTextEdit()
        self.ai_app_context_input.setFixedHeight(110)
        self.ai_app_context_input.setPlaceholderText("Вставьте ТЗ: смысл приложения, функционал, аудитория, ограничения, что точно есть/нет...")
        self.ai_prompt_profile_combo = QComboBox()
        self.ai_prompt_profiles = self._load_prompt_profiles()
        self.ai_prompt_profile_combo.addItems(self.ai_prompt_profiles.keys())
        self.ai_prompt_input = QTextEdit()
        self.ai_prompt_input.setFixedHeight(180)
        self.ai_prompt_input.setPlaceholderText("Вставьте свой промт для Gemini: стиль description, правила keywords, category, notes...")
        self.ai_prompt_input.setPlainText(self.ai_prompt_profiles.get("Human premium ASO", DEFAULT_GEMINI_PROMPT))
        ai_form.addRow("Gemini API key:", self.gemini_key_input)
        ai_form.addRow("Model:", self.gemini_model_input)
        ai_form.addRow("Developer name:", self.ai_developer_name_input)
        ai_form.addRow("ТЗ / brief приложения:", self.ai_app_context_input)
        ai_form.addRow("Prompt profile:", self.ai_prompt_profile_combo)
        ai_form.addRow("Промт генерации:", self.ai_prompt_input)
        ai_layout.addLayout(ai_form)
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
        ai_layout.addLayout(profile_buttons_layout)

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
        ai_layout.addLayout(ai_buttons_grid)

        fix_group = QGroupBox("Fix with AI / quick fixes")
        fix_grid = QGridLayout(fix_group)
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
        ai_layout.addWidget(fix_group)
        metadata_form_layout.addWidget(ai_group)

        custom_group = QGroupBox("Дополнительно (необязательно)")
        custom_layout = QVBoxLayout(custom_group)
        custom_layout.addWidget(QLabel("Для app privacy / availability можно вставить массив custom_requests в JSON-формате:"))
        self.metadata_text = QTextEdit()
        self.metadata_text.setFixedHeight(90)
        self.metadata_text.setPlaceholderText('[{"method":"PATCH","endpoint":"...","payload":{...}}]')
        custom_layout.addWidget(self.metadata_text)
        metadata_form_layout.addWidget(custom_group)

        self.chain_screenshots_checkbox = QCheckBox(
            "После метаданных: TinyPNG → загрузка скринов (файлы и локали — вкладка «ЗАГРУЗКА СКРИНОВ»)"
        )
        self.chain_screenshots_checkbox.setChecked(True)
        metadata_form_layout.addWidget(self.chain_screenshots_checkbox)

        metadata_scroll.setWidget(metadata_widget)
        metadata_layout.addWidget(metadata_scroll)
        self.tabs.addTab(self.tab_metadata, "🧾 ПЕРВИЧНАЯ ЗАГРУЗКА")
        self.tab_screens_upload = QWidget()
        screens_upload_layout = QVBoxLayout(self.tab_screens_upload)
        screens_upload_layout.addWidget(QLabel("Загрузка скринов (.jpeg) в App Store Connect на target 6.9 inch."))
        screens_upload_layout.addWidget(QLabel(
            "Порядок: «ПЕРВИЧНАЯ ЗАГРУЗКА» (метаданные) → TinyPNG → upload сюда. "
            "Или включите авто-цепочку на вкладке метаданных."
        ))
        self.btn_refresh_locales = QPushButton("🔄 Обновить локали")
        self.btn_refresh_locales.clicked.connect(self._refresh_screenshot_locales)
        screens_upload_layout.addWidget(self.btn_refresh_locales)

        upload_scroll = QScrollArea()
        upload_scroll.setWidgetResizable(True)
        upload_scroll_widget = QWidget()
        upload_grid = QGridLayout(upload_scroll_widget)
        self.upload_locale_checkboxes = []
        sorted_locales = sorted(LOCALE_MAP.items(), key=lambda item: item[1][1])
        row, col = 0, 0
        for code, (cc, name) in sorted_locales:
            cb = QCheckBox(f" {name}")
            cb.setProperty("locale_code", code)
            icon_path = f".flags_cache/{cc}.png"
            if os.path.exists(icon_path):
                cb.setIcon(QIcon(icon_path))
                cb.setIconSize(QSize(20, 15))
            self.upload_locale_checkboxes.append(cb)
            upload_grid.addWidget(cb, row, col)
            col += 1
            if col > 3:
                col = 0
                row += 1
        upload_scroll.setWidget(upload_scroll_widget)
        screens_upload_layout.addWidget(upload_scroll)

        files_layout = QHBoxLayout()
        self.btn_select_jpegs = QPushButton("📎 Выбрать .jpeg файлы")
        self.btn_select_jpegs.clicked.connect(self._select_jpeg_files)
        self.lbl_selected_jpegs = QLabel("Файлы не выбраны")
        files_layout.addWidget(self.btn_select_jpegs)
        files_layout.addWidget(self.lbl_selected_jpegs, stretch=1)
        screens_upload_layout.addLayout(files_layout)
        self.upload_jpeg_files = []
        self.btn_execute_upload = QPushButton("⬆️ Upload")
        self.btn_execute_upload.clicked.connect(self._start_screenshot_upload)
        screens_upload_layout.addWidget(self.btn_execute_upload)
        self.tabs.addTab(self.tab_screens_upload, "🖼️ ЗАГРУЗКА СКРИНОВ")

        self.lbl_folders = QLabel("ВЫБОР СКРИНШОТОВ (Для PPO режимов)")
        self.lbl_folders.setContentsMargins(0, 10, 0, 5)
        layout.addWidget(self.lbl_folders)

        self.btn_a = QPushButton("Выбрать папку: Variant A")
        self.btn_a.clicked.connect(lambda: self._select_folder("Variant A", self.btn_a))
        layout.addWidget(self.btn_a)

        self.btn_b = QPushButton("Выбрать папку: Variant B")
        self.btn_b.clicked.connect(lambda: self._select_folder("Variant B", self.btn_b))
        layout.addWidget(self.btn_b)

        self.btn_c = QPushButton("Выбрать папку: Variant C")
        self.btn_c.clicked.connect(lambda: self._select_folder("Variant C", self.btn_c))
        layout.addWidget(self.btn_c)

        self.screenshot_controls = [self.lbl_folders, self.btn_a, self.btn_b, self.btn_c]

        progress_layout = QHBoxLayout()
        progress_layout.setContentsMargins(0, 10, 0, 0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.time_label = QLabel("Осталось: --:--")
        self.time_label.setFixedWidth(120)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.time_label)
        layout.addLayout(progress_layout)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        layout.addWidget(self.log_area)

        self.start_btn = QPushButton("🚀 ЗАПУСТИТЬ ПРОЦЕСС")
        self.start_btn.setObjectName("start_btn") 
        self.start_btn.setMinimumHeight(45)
        self.start_btn.clicked.connect(self._start_process)
        layout.addWidget(self.start_btn)

        self.tabs.currentChanged.connect(self._on_tab_change)
        self._on_tab_change(self.tabs.currentIndex())

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget { 
                background-color: #1E1E1E; 
                color: #D4D4D4; 
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
                font-size: 13px; 
            }
            QLabel { font-weight: 600; color: #E0E0E0; }
            QPushButton { 
                background-color: #2D2D30; 
                border: 1px solid #3E3E42; 
                border-radius: 6px;
                padding: 8px; 
                color: #FFFFFF;
                font-weight: bold; 
            }
            QPushButton:hover { background-color: #3E3E42; border-color: #00D100; }
            QPushButton:disabled { background-color: #252526; border-color: #2D2D30; color: #7A7A7A; }
            
            QPushButton#start_btn {
                background-color: #00D100;
                color: #050805;
                font-size: 14px;
                border: none;
            }
            QPushButton#start_btn:hover { background-color: #00ED00; }
            QPushButton#start_btn:disabled { background-color: #2D2D30; color: #7A7A7A; }

            QPushButton#utility_btn {
                padding: 6px;
                font-size: 12px;
                color: #D4D4D4;
            }
            QPushButton#main_generate_btn {
                background-color: #00D100;
                color: #050805;
                font-size: 15px;
                border: none;
                font-weight: 700;
            }
            QPushButton#main_generate_btn:hover { background-color: #00ED00; }
            QPushButton#main_generate_btn:disabled { background-color: #2D2D30; color: #7A7A7A; }
            
            QLineEdit, QTextEdit, QComboBox { 
                background-color: #252526; 
                border: 1px solid #3E3E42; 
                border-radius: 5px;
                padding: 8px; 
                color: #FFFFFF; 
                selection-background-color: #00D100;
                selection-color: #050805;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #00D100; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #252526; color: #FFFFFF; selection-background-color: #3E3E42; border-radius: 5px; }
            
            QTabWidget::pane { border: 1px solid #3E3E42; border-radius: 6px; background-color: #252526; margin-top: -1px; }
            QTabBar::tab { 
                background: #1E1E1E; border: 1px solid #3E3E42; border-bottom-color: #3E3E42;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
                padding: 10px 20px; margin-right: 2px; color: #A0A0A0; 
            }
            QTabBar::tab:selected { background: #252526; border-bottom-color: #252526; color: #00D100; font-weight: bold; }
            QTabBar::tab:hover:!selected { background: #2D2D30; }
            
            QProgressBar { border: 1px solid #3E3E42; border-radius: 6px; background-color: #252526; text-align: center; color: #FFFFFF; font-weight: bold; height: 25px; }
            QProgressBar::chunk { background-color: #00D100; border-radius: 5px; }
            
            QCheckBox { color: #D4D4D4; spacing: 8px; padding: 4px; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #3E3E42; border-radius: 4px; background: #1E1E1E; }
            QCheckBox::indicator:hover { border: 1px solid #00D100; }
            QCheckBox::indicator:checked { background: #00D100; border: 1px solid #00D100; }
            
            QScrollArea { border: 1px solid #3E3E42; border-radius: 6px; background-color: #252526; }
            QScrollBar:vertical { border: none; background: #1E1E1E; width: 12px; margin: 0px; }
            QScrollBar::handle:vertical { background: #3E3E42; min-height: 20px; border-radius: 6px; }
            QScrollBar::handle:vertical:hover { background: #555555; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
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

    def _pull_metadata_from_apple(self):
        self._save_env_to_file()
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все настройки API для Pull from Apple.")
            return

        locale = self.meta_locale_combo.currentData() or "en-US"
        self.btn_pull_metadata.setEnabled(False)
        self.metadata_fetcher = FetchCurrentMetadataWorker(api_creds, locale)
        self.metadata_fetcher.log_msg.connect(self._log)
        self.metadata_fetcher.metadata_fetched.connect(self._apply_pulled_metadata)
        self.metadata_fetcher.finished.connect(lambda: self.btn_pull_metadata.setEnabled(True))
        self.metadata_fetcher.start()

    def _apply_pulled_metadata(self, metadata_config):
        if not metadata_config:
            self._log("В Apple не найдены метаданные для выбранной локали.")
            return
        self._apply_metadata_config_to_gui(metadata_config)

    def _fetch_primary_locale(self):
        self._save_env_to_file()
        api_creds = self._metadata_api_creds()
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все настройки API для получения primary locale.")
            return

        self.btn_fetch_primary_locale.setEnabled(False)
        self.primary_locale_fetcher = FetchPrimaryLocaleWorker(api_creds)
        self.primary_locale_fetcher.log_msg.connect(self._log)
        self.primary_locale_fetcher.primary_locale_fetched.connect(self._apply_primary_locale)
        self.primary_locale_fetcher.finished.connect(lambda: self.btn_fetch_primary_locale.setEnabled(True))
        self.primary_locale_fetcher.start()

    def _apply_primary_locale(self, locale):
        self._set_metadata_locale(locale)
        self._log(f"Primary locale выбрана в форме: {locale}")

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
        self._set_combo_data(self.meta_primary_category_input, app_info.get("primaryCategory", ""))
        self._set_combo_data(self.meta_secondary_category_input, app_info.get("secondaryCategory", ""))
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
        availability_mode = self.meta_availability_mode_input.currentData() or "ALL"
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
        metadata_config["availability_mode"] = availability_mode
        if availability_mode == "SELECTED":
            metadata_config["availability_territories"] = self.meta_selected_territories
        metadata_config["collects_data"] = self.meta_collects_data_checkbox.isChecked()
        if app_store_version:
            metadata_config["app_store_version"] = app_store_version
        if app_review_detail:
            metadata_config["app_review_detail"] = app_review_detail
        if custom_requests:
            metadata_config["custom_requests"] = custom_requests
        return metadata_config

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
            keyword_items = [item.strip() for item in keywords.split(",") if item.strip()]
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
        if not self.meta_include_whats_new_checkbox.isChecked():
            results.append("ℹ️ What's New не будет отправлен (первая версия / галочка выключена)")

        for line in results:
            self._log(line)
        self._log("Payload preview:")
        self._log(json.dumps(metadata_config, ensure_ascii=False, indent=2))

    def _toggle_locales(self, state):
        for cb in self.locale_checkboxes:
            cb.setChecked(state)

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

    def _set_screenshot_controls_visible(self, visible):
        for control in self.screenshot_controls:
            control.setVisible(visible)

    def _on_tab_change(self, index):
        self._set_screenshot_controls_visible(index in (0, 1))
        if index == 0:
            self.start_btn.setText("🚀 СОЗДАТЬ НОВЫЙ ТЕСТ")
        elif index == 1:
            self.start_btn.setText("🚀 ОБНОВИТЬ ВЫБРАННЫЕ ДАННЫЕ В ТЕСТЕ")
        elif index == 2:
            self.start_btn.setText("🚀 ЗАГРУЗИТЬ МЕТАДАННЫЕ ПРИЛОЖЕНИЯ")
        elif index == 3:
            self.start_btn.setText("⬆️ ЗАГРУЗКА СКРИНОВ: используйте кнопку в табе")
        elif index == 4:
            self.start_btn.setText("🔐 ЗАГРУЗИТЬ APP PRIVACY")
            self._preview_app_privacy()
        else:
            self.start_btn.setText("🚀 ЗАПУСТИТЬ ПРОЦЕСС")

    def _autofill_key_id_from_p8_path(self):
        filename = os.path.basename(self.p8_path_input.text().strip())
        if not filename:
            return
        if filename.startswith("AuthKey_") and filename.lower().endswith(".p8"):
            key_id = filename[len("AuthKey_"):-3]
            if key_id and not self.key_input.text().strip():
                self.key_input.setText(key_id)
                self._log(f"KEY_ID автоматически определен из имени .p8: {key_id}")
            self._log("Issuer ID нельзя извлечь из .p8 файла — Apple хранит его отдельно в App Store Connect.")

    def _select_p8_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", "Key Files (*.p8);;All Files (*)")
        if file_path:
            self.p8_path_input.setText(file_path)
            self._autofill_key_id_from_p8_path()

    def _select_folder(self, variant, button):
        folder = QFileDialog.getExistingDirectory(self, f"Выберите папку для {variant}")
        if folder:
            self.variants_paths[variant] = folder
            button.setText(f"{variant}: {os.path.basename(folder)}")

    def _log(self, text):
        self.log_area.append(text)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        print(text)

    def _update_progress(self, percent, time_text):
        self.progress_bar.setValue(percent)
        self.time_label.setText(time_text)

    def _save_env_to_file(self):
        tinypng_key = self._tinypng_api_key()
        env_content = (
            f"ISSUER_ID={self.issuer_input.text().strip()}\n"
            f"KEY_ID={self.key_input.text().strip()}\n"
            f"APP_ID={self.app_input.text().strip()}\n"
            f"PRIVATE_KEY_PATH={self.p8_path_input.text().strip()}\n"
            f"TINYPNG_API_KEY={tinypng_key}\n"
        )
        if hasattr(self, "gemini_key_input"):
            env_content += f"GEMINI_API_KEY={self.gemini_key_input.text().strip()}\n"
        if hasattr(self, "gemini_model_input"):
            env_content += f"GEMINI_MODEL={self.gemini_model_input.text().strip()}\n"
        try:
            with open(ENV_PATH, "w", encoding="utf-8") as f: f.write(env_content)
        except Exception as e:
            self._log(f"Ошибка сохранения .env файла: {e}")

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

    def _generate_ai_metadata(self, mode="all"):
        self._save_env_to_file()
        api_key = self.gemini_key_input.text().strip()
        model = self.gemini_model_input.text().strip() or "gemini-2.5-flash"
        app_context = self._text_edit_text(self.ai_app_context_input)
        user_prompt = self._text_edit_text(self.ai_prompt_input)
        developer_name = self._line_text(self.ai_developer_name_input)
        locale = self.meta_locale_combo.currentData() or "en-US"
        current_value = self._current_value_for_generation_mode(mode)

        if not api_key:
            self._log("Ошибка: Введите Gemini API key в блоке AI генерации.")
            return
        if not app_context:
            self._log("Ошибка: Вставьте ТЗ / brief приложения для AI генерации.")
            return
        if not user_prompt:
            self._log("Ошибка: Вставьте промт генерации для Gemini.")
            return
        rewrite_modes = ["rewrite_description", "shorten_keywords", "shorten_to_limits", "fix_banned_words"]
        if mode in rewrite_modes and not current_value:
            self._log("Ошибка: Для этого режима сначала заполните текущее поле, которое нужно переписать или сократить.")
            return

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
        self._log(f"Отправляю запрос в Gemini. Режим: {mode_labels.get(mode, mode)}")
        self.gemini_worker = GeminiMetadataWorker(api_key, model, locale, app_context, user_prompt, developer_name, mode, current_value)
        self.gemini_worker.log_msg.connect(self._log)
        self.gemini_worker.metadata_generated.connect(self._apply_ai_metadata)
        self.gemini_worker.finished.connect(lambda: self._set_ai_buttons_enabled(True))
        self.gemini_worker.start()

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
                self.meta_keywords_input.setText(str(metadata.get("keywords", ""))[:100])
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
                if metadata.get("primaryCategory"):
                    self._set_combo_data(self.meta_primary_category_input, str(metadata.get("primaryCategory", "")))
                if metadata.get("secondaryCategory"):
                    self._set_combo_data(self.meta_secondary_category_input, str(metadata.get("secondaryCategory", "")))
                self._log("✅ Categories обновлены.")
            category_name = metadata.get("categoryName")
            if category_name:
                self._log(f"Gemini предлагает категорию: {category_name}. Проверьте category ID перед отправкой.")
            return

        if metadata.get("subtitle"):
            self.meta_subtitle_input.setText(str(metadata.get("subtitle", ""))[:30])
        if metadata.get("description"):
            self.meta_description_input.setPlainText(str(metadata.get("description", "")))
        if metadata.get("keywords"):
            self.meta_keywords_input.setText(str(metadata.get("keywords", ""))[:100])
        if metadata.get("promotionalText"):
            self.meta_promotional_text_input.setPlainText(str(metadata.get("promotionalText", ""))[:170])
        if metadata.get("whatsNew"):
            self.meta_whats_new_input.setPlainText(str(metadata.get("whatsNew", "")))
        if metadata.get("primaryCategory"):
            self._set_combo_data(self.meta_primary_category_input, str(metadata.get("primaryCategory", "")))
        if metadata.get("secondaryCategory"):
            self._set_combo_data(self.meta_secondary_category_input, str(metadata.get("secondaryCategory", "")))
        if metadata.get("reviewNotes"):
            self.meta_review_notes_input.setPlainText(str(metadata.get("reviewNotes", "")))

        category_name = metadata.get("categoryName")
        if category_name:
            self._log(f"Gemini предлагает категорию: {category_name}. Проверьте category ID перед отправкой.")

    def _normalize_keywords_locally(self):
        raw = self._line_text(self.meta_keywords_input)
        seen = []
        for item in raw.split(","):
            normalized = re.sub(r"\s+", "", item.strip().lower())
            if normalized and normalized not in seen:
                seen.append(normalized)
        result = ",".join(seen)[:100]
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
        self.meta_subtitle_input.setText(self._line_text(self.meta_subtitle_input)[:30])
        self.meta_keywords_input.setText(self._line_text(self.meta_keywords_input)[:100])
        self.meta_promotional_text_input.setPlainText(self._text_edit_text(self.meta_promotional_text_input)[:170])
        self.meta_description_input.setPlainText(self._text_edit_text(self.meta_description_input)[:4000])
        self._log("Поля локально обрезаны до базовых лимитов App Store.")

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
        self._save_env_to_file()
        api_creds = {
            "issuer": self.issuer_input.text().strip(),
            "key_id": self.key_input.text().strip(),
            "app_id": self.app_input.text().strip(),
            "p8_path": self.p8_path_input.text().strip()
        }
        if not all(api_creds.values()):
            self._log("Ошибка: Заполните все настройки API для обновления локалей.")
            return
        self.btn_refresh_locales.setEnabled(False)
        self.locale_refresh_worker = RefreshLocalesWorker(api_creds)
        self.locale_refresh_worker.log_msg.connect(self._log)
        self.locale_refresh_worker.locales_fetched.connect(self._apply_active_screenshot_locales)
        self.locale_refresh_worker.finished.connect(lambda: self.btn_refresh_locales.setEnabled(True))
        self.locale_refresh_worker.start()

    def _apply_active_screenshot_locales(self, active_locales):
        active = set(active_locales)
        for cb in self.upload_locale_checkboxes:
            cb.setChecked(cb.property("locale_code") in active)

    def _ensure_tinypng_key_visible(self):
        key = resolve_tinypng_api_key()
        if key and hasattr(self, "tinypng_key_input"):
            self.tinypng_key_input.setText(key)

    def _tinypng_api_key(self):
        if hasattr(self, "tinypng_key_input"):
            typed = self.tinypng_key_input.text().strip()
            if typed:
                return typed
        return resolve_tinypng_api_key()

    def _validate_screenshot_upload_prereqs(self):
        target_locales = [cb.property("locale_code") for cb in self.upload_locale_checkboxes if cb.isChecked()]
        if not target_locales:
            self._log("Ошибка: Выберите хотя бы одну локаль на вкладке «ЗАГРУЗКА СКРИНОВ».")
            return False
        if not self.upload_jpeg_files:
            self._log("Ошибка: Выберите .jpeg файлы на вкладке «ЗАГРУЗКА СКРИНОВ».")
            return False
        if not self._tinypng_api_key():
            self._log("Ошибка: Укажите TinyPNG API key в настройках API.")
            return False
        return True

    def _on_metadata_upload_finished(self, success):
        self.start_btn.setEnabled(True)
        if not success or not self.chain_screenshots_checkbox.isChecked():
            return
        if not self._validate_screenshot_upload_prereqs():
            self._log("Авто-загрузка скринов пропущена: проверьте вкладку «ЗАГРУЗКА СКРИНОВ».")
            return
        self._log("Метаданные загружены. Запуск TinyPNG → upload скринов...")
        self._start_screenshot_upload()

    def _select_jpeg_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите .jpeg файлы", "", "JPEG Files (*.jpeg *.jpg)")
        if files:
            self.upload_jpeg_files = sorted([f for f in files if f.lower().endswith((".jpeg", ".jpg"))])
            self.lbl_selected_jpegs.setText(f"Выбрано файлов: {len(self.upload_jpeg_files)}")

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
        target_locales = [cb.property("locale_code") for cb in self.upload_locale_checkboxes if cb.isChecked()]
        if not target_locales:
            self._log("Ошибка: Выберите хотя бы одну локаль для загрузки скринов.")
            return
        if not self._validate_screenshot_upload_prereqs():
            return
        self.btn_execute_upload.setEnabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Загрузка: 0%")
        self.screenshot_upload_worker = ScreenshotUploadWorker(
            api_creds, target_locales, self.upload_jpeg_files, self._tinypng_api_key()
        )
        self.screenshot_upload_worker.log_msg.connect(self._log)
        self.screenshot_upload_worker.progress_update.connect(self._update_progress)
        self.screenshot_upload_worker.finished.connect(lambda: self.btn_execute_upload.setEnabled(True))
        self.screenshot_upload_worker.start()

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
            
            target_locales = [cb.property("locale_code") for cb in self.locale_checkboxes if cb.isChecked()]
            
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
            try:
                metadata_config = self._build_metadata_config_from_gui()
            except json.JSONDecodeError as e:
                self._log(f"Ошибка JSON в custom_requests: строка {e.lineno}, колонка {e.colno}: {e.msg}")
                return
            except ValueError as e:
                self._log(f"Ошибка custom_requests: {e}")
                return

            if not metadata_config:
                self._log("Ошибка: Заполните хотя бы одно поле метаданных.")
                return
            if self.chain_screenshots_checkbox.isChecked() and not self._validate_screenshot_upload_prereqs():
                return

            self.start_btn.setEnabled(False)
            self.progress_bar.setValue(0)
            self.time_label.setText("Метаданные: 0%")
            self.log_area.clear()

            self.worker = MetadataWorker(api_creds, metadata_config)
            self.worker.log_msg.connect(self._log)
            self.worker.progress_update.connect(self._update_progress)
            self.worker.finished_ok.connect(self._on_metadata_upload_finished)
            self.worker.start()
            return
        elif current_tab_index == 4:
            self._upload_app_privacy()
            return

        tinypng_api_key = self._tinypng_api_key()
        if not tinypng_api_key:
            self._log("Ошибка: Укажите TinyPNG API key в настройках API.")
            return

        self.start_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Осталось: --:--")
        self.log_area.clear()
        
        self.worker = AutomationWorker(
            mode, api_creds, test_name, traffic, target_variants, target_locales,
            self.variants_paths, tinypng_api_key
        )
        self.worker.log_msg.connect(self._log)
        self.worker.progress_update.connect(self._update_progress)
        self.worker.finished.connect(lambda: self.start_btn.setEnabled(True))
        self.worker.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
