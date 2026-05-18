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
    QFrame, QSplitter, QSizePolicy,
    QDialog, QListWidget, QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem
)
from PySide6.QtGui import QIcon, QFont, QGuiApplication, QColor
from PySide6.QtCore import QThread, Signal, Qt, QSize

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
load_dotenv(ENV_PATH)

# UI scale tuned for 1080p–2K displays
UI_FONT_BASE_PT = 14
UI_FONT_TITLE_PT = 20
UI_FONT_SECTION_PT = 12
UI_LOCALE_GRID_COLUMNS = 5
UI_FORM_MAX_WIDTH = 980
UI_LOG_MIN_HEIGHT = 320
UI_LOCALE_COLUMN_MIN_WIDTH = 170


def default_window_size():
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1560, 980
    avail = screen.availableGeometry()
    width = min(max(int(avail.width() * 0.82), 1400), 2100)
    height = min(max(int(avail.height() * 0.88), 860), 1320)
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
Recommend 1 Primary category and 1 Secondary category with a concise practical justification in English.
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

TRANSLATION_FIELDS = [
    ("description", "Description", 4000, "version"),
    ("keywords", "Keywords", 100, "version"),
    ("promotionalText", "Promotional Text", 170, "version"),
    ("whatsNew", "What's New", 4000, "version"),
    ("subtitle", "Subtitle", 30, "app_info"),
]

TRANSLATION_MAX_WORKERS = int(os.getenv("TRANSLATION_MAX_WORKERS", "3"))
TRANSLATION_REQUEST_INTERVAL_SECONDS = float(os.getenv("TRANSLATION_REQUEST_INTERVAL_SECONDS", "2.2"))
TRANSLATION_FALLBACK_MODEL = os.getenv("TRANSLATION_FALLBACK_MODEL", "gemini-2.5-flash-lite")

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
}

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

    def ensure_version_localization(self, version_id, locale):
        existing = self.get_version_localization_by_locale(version_id, locale)
        if existing:
            return existing["id"], False
        created_id = self.create_version_localization(version_id, locale, {})
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

    def __init__(self, api_key, model, source_locale, target_locales, fields, profile_name, source_payload):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.source_locale = source_locale
        self.target_locales = target_locales
        self.fields = fields
        self.profile_name = profile_name
        self.source_payload = source_payload
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

    def _parse_gemini_error(self, response):
        try:
            payload = response.json()
        except ValueError:
            return response.text[:500]
        error = payload.get("error", {})
        message = error.get("message") or response.text[:500]
        if "Quota exceeded" in message or error.get("status") == "RESOURCE_EXHAUSTED":
            return "quota exceeded: " + message.splitlines()[0]
        return message.splitlines()[0] if message else response.text[:500]

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

        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        }
        response = None
        for attempt in range(4):
            self._wait_for_translation_slot()
            response = requests.post(
                endpoint,
                headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                json=payload,
                timeout=90,
            )
            if response.status_code not in (429, 500, 502, 503, 504):
                if response.status_code >= 400:
                    raise Exception(self._parse_gemini_error(response))
                break
            if attempt == 3:
                raise Exception(self._parse_gemini_error(response))
            time.sleep(2 ** attempt)
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback", {})
            raise Exception(f"empty Gemini candidates: {feedback or 'no details'}")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            finish_reason = candidate.get("finishReason", "UNKNOWN")
            raise Exception(f"Gemini returned empty text (finishReason={finish_reason})")
        return text

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
                "warning": f"Ошибка Gemini: {field_exc}",
            }

    def run(self):
        rows = []
        try:
            tasks = []
            for locale in self.target_locales:
                for field_name in self.fields:
                    source_text = str(self.source_payload.get(field_name, "") or "").strip()
                    tasks.append((locale, field_name, source_text))

            total = max(1, len(tasks))
            done = 0
            max_workers = min(TRANSLATION_MAX_WORKERS, total)
            self.log_msg.emit(f"Параллельный перевод: {total} задач, потоков: {max_workers}")
            self.progress_update.emit(0, f"Перевод: 0/{total}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_task = {
                    executor.submit(self._translate_task, locale, field_name, source_text): (locale, field_name)
                    for locale, field_name, source_text in tasks
                }
                for future in concurrent.futures.as_completed(future_to_task):
                    locale, field_name = future_to_task[future]
                    rows.append(future.result())
                    done += 1
                    self.log_msg.emit(f"Перевод {done}/{total}: {locale} · {field_name}")
                    percent = int(done * 100 / total)
                    self.progress_update.emit(percent, f"Перевод: {done}/{total}")
            self.translations_ready.emit(rows)
            self.progress_update.emit(100, "Перевод завершен")
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

    def __init__(self, api_creds, rows):
        super().__init__()
        self.api_creds = api_creds
        self.rows = rows

    def run(self):
        summary = {"locales": 0, "fields": 0, "errors": 0}
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
            grouped = {}
            for row in self.rows:
                if row.get("status") != "ok":
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

            total_locales = max(1, len(grouped))
            done_locales = 0
            for locale, payloads in grouped.items():
                try:
                    if payloads["version"]:
                        loc_id, created = client.ensure_version_localization(version_id, locale)
                        attrs = sanitize_version_localization_attributes(payloads["version"], self.log_msg.emit)
                        if attrs:
                            client.update_version_localization(loc_id, attrs)
                            self.log_msg.emit(
                                f"✅ [{locale}] version localization {'создана' if created else 'обновлена'}."
                            )
                            summary["fields"] += len(attrs)
                    if payloads["app_info"]:
                        loc_id, created = client.ensure_app_info_localization(app_info_id, locale)
                        attrs = sanitize_app_info_localization_attributes(payloads["app_info"], self.log_msg.emit)
                        if attrs:
                            client.update_app_info_localization(loc_id, attrs)
                            self.log_msg.emit(
                                f"✅ [{locale}] app info localization {'создана' if created else 'обновлена'}."
                            )
                            summary["fields"] += len(attrs)
                    summary["locales"] += 1
                except Exception as locale_exc:
                    summary["errors"] += 1
                    self.log_msg.emit(f"❌ [{locale}] Ошибка upload: {locale_exc}")
                done_locales += 1
                percent = int((done_locales / total_locales) * 100)
                self.progress_update.emit(percent, f"Локализации: {done_locales}/{total_locales}")
            self.upload_finished.emit(summary)
        except Exception as e:
            summary["errors"] += 1
            self.log_msg.emit(f"Критическая ошибка upload локализаций: {e}")
            self.upload_finished.emit(summary)
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
- All user-facing copy must be in English unless the user prompt explicitly requests another language.
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


class CollapsibleApiPanel(QGroupBox):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(True)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 14, 16, 14)
        root_layout.setSpacing(10)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "hint")
        root_layout.addWidget(self.summary_label)

        self.body_widget = QWidget()
        self.body_layout = QVBoxLayout(self.body_widget)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        root_layout.addWidget(self.body_widget)
        self.toggled.connect(self._on_toggled)
        self._on_toggled(True)

    def _on_toggled(self, expanded):
        self.body_widget.setVisible(expanded)

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
                jpeg_count = sum(
                    1 for name in os.listdir(folder_path)
                    if name.lower().endswith((".jpeg", ".jpg"))
                )
                if jpeg_count:
                    label = f"{label} · {jpeg_count} jpeg"
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
    def __init__(self, locales_map, title="Локали", columns=UI_LOCALE_GRID_COLUMNS, parent=None):
        super().__init__(title, parent)
        self._locales_map = locales_map
        self._columns = max(2, columns)
        self._checkboxes = {}
        self._all_codes = []
        self._filtered_codes = []
        self._build_ui()
        self._populate_locales()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

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

        self.selection_label = QLabel()
        self.selection_label.setProperty("role", "hint")
        layout.addWidget(self.selection_label)

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

    def _populate_locales(self):
        sorted_locales = sorted(self._locales_map.items(), key=lambda item: item[1][1])
        self._all_codes = [code for code, _ in sorted_locales]
        for code, (country_code, locale_name) in sorted_locales:
            cb = QCheckBox(f" {locale_name} ({code})")
            cb.setProperty("locale_code", code)
            cb.setMinimumWidth(UI_LOCALE_COLUMN_MIN_WIDTH)
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            icon_path = f".flags_cache/{country_code}.png"
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
        self.setMaximumHeight(height)
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
        title = QLabel("Перетащите .jpg/.jpeg сюда")
        title.setProperty("role", "section")
        hint = QLabel("Или используйте кнопку выбора файлов ниже.")
        hint.setProperty("role", "hint")
        layout.addWidget(title)
        layout.addWidget(hint)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            valid = any(
                url.toLocalFile().lower().endswith((".jpg", ".jpeg"))
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
            if url.toLocalFile().lower().endswith((".jpg", ".jpeg"))
        ]
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
        else:
            event.ignore()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        ensure_flags_downloaded()
        
        self.setWindowTitle("PPO Automation")
        self._initial_width, self._initial_height = default_window_size()
        self.resize(self._initial_width, self._initial_height)
        self.setMinimumSize(1280, 820)
        self.variants_paths = {"Variant A": "", "Variant B": "", "Variant C": ""}
        self._summary_app_name = ""
        self._summary_app_version = ""
        self.app_version_fetcher = None
        self.metadata_fetcher = None
        self.apps_fetcher = None
        self._setup_ui()
        self._apply_styles()
        self._apply_display_polish()
        self._ensure_tinypng_key_visible()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        sorted_locales = sorted(LOCALE_MAP.items(), key=lambda item: item[1][1])

        self.api_panel = CollapsibleApiPanel("Настройки API")
        api_layout = self.api_panel.body_layout

        api_caption = QLabel("API credentials и ключ TinyPNG сохраняются в .env автоматически.")
        api_caption.setProperty("role", "hint")
        api_layout.addWidget(api_caption)

        apple_grid = QGridLayout()
        apple_grid.setHorizontalSpacing(10)
        apple_grid.setVerticalSpacing(6)
        issuer_label = QLabel("Issuer ID")
        issuer_label.setProperty("role", "section")
        key_id_label = QLabel("Key ID")
        key_id_label.setProperty("role", "section")
        app_id_label = QLabel("App ID")
        app_id_label.setProperty("role", "section")
        p8_label = QLabel("Private key (.p8)")
        p8_label.setProperty("role", "section")

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

        apple_grid.addWidget(issuer_label, 0, 0)
        apple_grid.addWidget(key_id_label, 0, 1)
        apple_grid.addWidget(app_id_label, 0, 2, 1, 2)
        apple_grid.addWidget(self.issuer_input, 1, 0)
        apple_grid.addWidget(self.key_input, 1, 1)
        apple_grid.addWidget(self.app_input, 1, 2)
        apple_grid.addWidget(self.btn_fetch_app_id, 1, 3)
        apple_grid.addWidget(p8_label, 2, 0, 1, 4)
        apple_grid.addWidget(self.p8_path_input, 3, 0, 1, 3)
        apple_grid.addWidget(self.btn_select_p8, 3, 3)
        apple_grid.setColumnStretch(0, 2)
        apple_grid.setColumnStretch(1, 1)
        apple_grid.setColumnStretch(2, 2)
        apple_grid.setColumnStretch(3, 0)
        api_layout.addLayout(apple_grid)

        api_separator = QFrame()
        api_separator.setFrameShape(QFrame.HLine)
        api_separator.setStyleSheet("QFrame { color: #26324A; margin: 4px 0; }")
        api_layout.addWidget(api_separator)

        tinypng_label = QLabel("TinyPNG API key (сжатие скриншотов)")
        tinypng_label.setProperty("role", "section")
        api_layout.addWidget(tinypng_label)
        self.tinypng_key_input = QLineEdit(resolve_tinypng_api_key())
        self.tinypng_key_input.setPlaceholderText("TinyPNG API key (сохраняется в .env)")
        self.tinypng_key_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.tinypng_key_input)

        gemini_label = QLabel("Gemini API (AI генерация метаданных)")
        gemini_label.setProperty("role", "section")
        api_layout.addWidget(gemini_label)
        gemini_row = QHBoxLayout()
        self.gemini_key_input = QLineEdit(os.getenv("GEMINI_API_KEY", ""))
        self.gemini_key_input.setPlaceholderText("Gemini API key")
        self.gemini_key_input.setEchoMode(QLineEdit.Password)
        self.gemini_key_input.editingFinished.connect(self._save_env_to_file)
        self.gemini_model_input = QLineEdit(os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
        self.gemini_model_input.setPlaceholderText("gemini-2.5-flash")
        self.gemini_model_input.editingFinished.connect(self._save_env_to_file)
        gemini_row.addWidget(self.gemini_key_input, stretch=2)
        gemini_row.addWidget(self.gemini_model_input, stretch=1)
        api_layout.addLayout(gemini_row)

        jira_separator = QFrame()
        jira_separator.setFrameShape(QFrame.HLine)
        jira_separator.setStyleSheet("QFrame { color: #26324A; margin: 4px 0; }")
        api_layout.addWidget(jira_separator)

        jira_label = QLabel("Jira Cloud (экспорт метаданных)")
        jira_label.setProperty("role", "section")
        api_layout.addWidget(jira_label)
        self.jira_base_url_input = QLineEdit(os.getenv("JIRA_BASE_URL", ""))
        self.jira_base_url_input.setPlaceholderText("https://your-company.atlassian.net")
        self.jira_base_url_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.jira_base_url_input)
        self.jira_email_input = QLineEdit(os.getenv("JIRA_EMAIL", ""))
        self.jira_email_input.setPlaceholderText("your@email.com")
        self.jira_email_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.jira_email_input)
        self.jira_api_token_input = QLineEdit(os.getenv("JIRA_API_TOKEN", ""))
        self.jira_api_token_input.setPlaceholderText("Jira API Token (Account → Security → API tokens)")
        self.jira_api_token_input.setEchoMode(QLineEdit.Password)
        self.jira_api_token_input.editingFinished.connect(self._save_env_to_file)
        api_layout.addWidget(self.jira_api_token_input)
        jira_project_row = QHBoxLayout()
        self.jira_project_key_input = QLineEdit(os.getenv("JIRA_PROJECT_KEY", ""))
        self.jira_project_key_input.setPlaceholderText("APP")
        self.jira_project_key_input.setMaximumWidth(120)
        self.jira_project_key_input.editingFinished.connect(self._save_env_to_file)
        self.jira_issue_type_input = QLineEdit(os.getenv("JIRA_ISSUE_TYPE", "Story"))
        self.jira_issue_type_input.setPlaceholderText("Story")
        self.jira_issue_type_input.setMaximumWidth(100)
        self.jira_issue_type_input.editingFinished.connect(self._save_env_to_file)
        jira_project_row.addWidget(QLabel("Project:"))
        jira_project_row.addWidget(self.jira_project_key_input)
        jira_project_row.addWidget(QLabel("Type:"))
        jira_project_row.addWidget(self.jira_issue_type_input)
        jira_project_row.addStretch()
        api_layout.addLayout(jira_project_row)
        root_layout.addWidget(self.api_panel)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.tabs = QTabWidget()
        left_layout.addWidget(self.tabs)
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
        self.tabs.addTab(self.tab_create, "Новый тест")

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
        
        self.tabs.addTab(self.tab_update, "Обновить тест")

        self.tab_metadata = QWidget()
        metadata_layout = QVBoxLayout(self.tab_metadata)
        metadata_layout.setContentsMargins(4, 4, 4, 4)
        metadata_layout.addWidget(make_page_header(
            "Метаданные приложения",
            "Заполните поля ниже — программа соберет payload для App Store Connect. "
            "JSON оставлен только для продвинутых custom_requests."
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

        metadata_scroll = QScrollArea()
        metadata_scroll.setWidgetResizable(True)
        metadata_widget = QWidget()
        metadata_form_layout = QVBoxLayout(metadata_widget)

        source_group = QGroupBox("Источник данных")
        source_layout = QHBoxLayout(source_group)
        source_layout.setContentsMargins(12, 14, 12, 10)
        source_layout.setSpacing(12)
        source_summary_row = QHBoxLayout()
        self.meta_source_locale_label = QLabel("Локаль: —")
        self.meta_source_locale_label.setProperty("role", "section")
        self.meta_source_version_label = QLabel("Версия: —")
        self.meta_source_version_label.setProperty("role", "section")
        source_summary_row.addWidget(self.meta_source_locale_label, stretch=1)
        source_summary_row.addWidget(self.meta_source_version_label, stretch=1)
        source_layout.addLayout(source_summary_row, stretch=1)
        self.btn_pull_metadata = QPushButton("⬇️ Pull")
        self.btn_pull_metadata.setObjectName("start_btn")
        self.btn_pull_metadata.setMinimumHeight(46)
        self.btn_pull_metadata.setMinimumWidth(125)
        self.btn_pull_metadata.clicked.connect(self._pull_metadata_from_apple)
        source_layout.addWidget(self.btn_pull_metadata)
        metadata_form_layout.addWidget(source_group)

        locale_group = QGroupBox("Локаль")
        locale_form = QFormLayout(locale_group)
        self.meta_locale_combo = QComboBox()
        for code, (_, name) in sorted_locales:
            self.meta_locale_combo.addItem(f"{name} ({code})", code)
        default_locale_index = self.meta_locale_combo.findData("en-US")
        if default_locale_index >= 0:
            self.meta_locale_combo.setCurrentIndex(default_locale_index)
        locale_form.addRow("Язык:", self.meta_locale_combo)

        self.meta_app_version_input = QLineEdit("")
        self.meta_app_version_input.setPlaceholderText("Версия из App Store Connect")
        self.meta_app_version_input.setReadOnly(True)
        locale_form.addRow("Версия:", self.meta_app_version_input)
        metadata_form_layout.addWidget(locale_group)

        jira_row = QHBoxLayout()
        self.jira_issue_key_input = QLineEdit(os.getenv("JIRA_ISSUE_KEY", ""))
        self.jira_issue_key_input.setPlaceholderText("T7-1506")
        self.jira_issue_key_input.setMaximumWidth(130)
        self.jira_issue_key_input.editingFinished.connect(self._save_env_to_file)
        self.btn_create_jira = QPushButton("📋 Обновить карточку Jira")
        self.btn_create_jira.setObjectName("start_btn")
        self.btn_create_jira.setMinimumHeight(42)
        self.btn_create_jira.setMinimumWidth(200)
        self.btn_create_jira.clicked.connect(self._update_jira_issue)
        jira_hint = QLabel("Записывает метаданные в существующую Jira карточку")
        jira_hint.setProperty("role", "hint")
        jira_row.addWidget(QLabel("Issue:"))
        jira_row.addWidget(self.jira_issue_key_input)
        jira_row.addWidget(self.btn_create_jira)
        jira_row.addWidget(jira_hint, stretch=1)
        metadata_form_layout.addLayout(jira_row)

        version_group = QGroupBox("Version metadata")
        version_form = QFormLayout(version_group)
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
        self.meta_review_notes_input.setFixedHeight(112)
        review_form.addRow("Contact first name:", self.meta_review_first_name_input)
        review_form.addRow("Contact last name:", self.meta_review_last_name_input)
        review_form.addRow("Contact phone:", self.meta_review_phone_input)
        review_form.addRow("Contact email:", self.meta_review_email_input)
        self._add_metadata_field_row(review_form, "Notes:", self.meta_review_notes_input, "review_notes")
        metadata_form_layout.addWidget(review_group)

        ai_group = QGroupBox("AI генерация (Gemini)")
        ai_layout = QVBoxLayout(ai_group)
        ai_form = QFormLayout()
        gemini_settings_hint = QLabel("Gemini API key и model теперь находятся в верхнем блоке «Настройки API».")
        gemini_settings_hint.setProperty("role", "hint")
        ai_layout.addWidget(gemini_settings_hint)
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
        self.ai_prompt_input.setPlaceholderText("Вставьте свой промт для Gemini: стиль description, правила keywords, category, notes...")
        self.ai_prompt_input.setPlainText(self.ai_prompt_profiles.get("Human premium ASO", DEFAULT_GEMINI_PROMPT))
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
        self.metadata_text.setFixedHeight(112)
        self.metadata_text.setPlaceholderText('[{"method":"PATCH","endpoint":"...","payload":{...}}]')
        custom_layout.addWidget(self.metadata_text)
        metadata_form_layout.addWidget(custom_group)

        self.chain_screenshots_checkbox = QCheckBox(
            "После метаданных: TinyPNG → загрузка скринов (файлы и локали — вкладка «ЗАГРУЗКА СКРИНОВ»)"
        )
        self.chain_screenshots_checkbox.setChecked(False)
        metadata_form_layout.addWidget(self.chain_screenshots_checkbox)

        metadata_scroll.setWidget(metadata_widget)
        metadata_layout.addWidget(metadata_scroll)
        self.tabs.addTab(self.tab_metadata, "Метаданные")

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
            "Pull source из Apple, перевод через Gemini, preview/edit и batch upload в App Store Connect."
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

        self.tabs.addTab(self.tab_translation, "Перевод локализации")

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
            "Порядок: «Метаданные» → TinyPNG → upload сюда. "
            "Или включите авто-цепочку на вкладке метаданных."
        ))
        self.btn_refresh_locales = QPushButton("🔄 Взять активные локали из Apple")
        self.btn_refresh_locales.setObjectName("utility_btn")
        self.btn_refresh_locales.clicked.connect(self._refresh_screenshot_locales)
        screens_upload_layout.addWidget(self.btn_refresh_locales)
        self.upload_locale_picker = LocalePickerWidget(LOCALE_MAP, "Локали для скриншотов", columns=6)
        self.upload_locale_picker.set_compact_height(445)
        screens_upload_layout.addWidget(self.upload_locale_picker)

        upload_files_group = QGroupBox("Файлы и запуск")
        upload_files_layout = QVBoxLayout(upload_files_group)
        upload_files_layout.setSpacing(10)
        self.upload_summary_label = QLabel("Локалей: 0 · Файлов: 0 · Всего upload задач: 0")
        self.upload_summary_label.setProperty("role", "section")
        upload_files_layout.addWidget(self.upload_summary_label)
        self.screenshot_drop_zone = ScreenshotDropZone()
        self.screenshot_drop_zone.files_dropped.connect(self._set_upload_jpeg_files)
        upload_files_layout.addWidget(self.screenshot_drop_zone)
        files_layout = QHBoxLayout()
        self.btn_select_jpegs = QPushButton("📎 Выбрать .jpeg файлы")
        self.btn_select_jpegs.setObjectName("utility_btn")
        self.btn_select_jpegs.clicked.connect(self._select_jpeg_files)
        self.lbl_selected_jpegs = QLabel("Файлы не выбраны")
        self.lbl_selected_jpegs.setProperty("role", "hint")
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
        self.tabs.addTab(self.tab_screens_upload, "Скриншоты")

        self.start_btn = QPushButton("🚀 ЗАПУСТИТЬ ПРОЦЕСС")
        self.start_btn.setObjectName("start_btn") 
        self.start_btn.setMinimumHeight(52)
        self.start_btn.clicked.connect(self._start_process)
        root_layout.addWidget(self.start_btn)

        for widget in [
            self.issuer_input, self.key_input, self.app_input, self.p8_path_input,
            self.tinypng_key_input, self.gemini_key_input, self.gemini_model_input
        ]:
            widget.textChanged.connect(self._update_api_summary)
        self.app_input.textChanged.connect(self._on_app_id_changed)
        self._update_api_summary()
        if self._has_complete_api_credentials():
            self.api_panel.setChecked(False)
            if self.app_input.text().strip():
                self._fetch_app_version(silent=True)
                self._pull_metadata_from_apple()
        self._refresh_variant_cards()
        self.tabs.currentChanged.connect(self._on_tab_change)
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
        if self._has_complete_api_credentials() and self.app_input.text().strip():
            self._fetch_app_version(silent=True)

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
            self.api_panel.set_summary(summary, status="warn")
        else:
            summary = f"Готово к запуску | {details}"
            self.api_panel.set_summary(summary, status="ok")

    def _extract_key_id_from_p8_path(self):
        filename = os.path.basename(self.p8_path_input.text().strip())
        if not filename:
            return ""
        match = re.match(r"^AuthKey_([A-Za-z0-9]+)\.p8$", filename, re.IGNORECASE)
        return match.group(1) if match else ""

    def _apply_display_polish(self):
        app = QApplication.instance()
        if app is not None:
            app.setFont(QFont("Segoe UI", UI_FONT_BASE_PT))
        self.log_area.setFont(QFont("Cascadia Mono", 12))
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget { 
                background-color: #0B1020; 
                color: #EAF0FF; 
                font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, Helvetica, Arial, sans-serif; 
                font-size: 14px; 
            }
            QLabel { color: #EAF0FF; font-weight: 500; background: transparent; }
            QLabel[role="title"] { font-size: 20px; color: #F7FAFF; font-weight: 700; letter-spacing: 0.2px; }
            QLabel[role="section"] { font-size: 11px; color: #9FB2D8; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; }
            QLabel[role="hint"] { color: #93A4C8; font-weight: 400; font-size: 13px; }
            QLabel[role="label"] { color: #C7D2FE; font-weight: 600; font-size: 13px; }
            QLabel[status="ok"] { color: #4ADE80; font-weight: 600; }
            QLabel[status="warn"] { color: #FBBF24; font-weight: 600; }

            QWidget#page_header {
                background: transparent;
                border: none;
            }

            QWidget#execution_panel {
                background-color: #0D1728;
                border: 1px solid #26324A;
                border-top: 3px solid #6D8DFF;
                border-radius: 10px;
                padding: 8px;
            }

            QGroupBox {
                border: 1px solid #26324A;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 14px;
                background-color: #111827;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #C7D2FE;
                font-weight: 700;
            }
            QGroupBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #334155;
                border-radius: 4px;
                background: #0F172A;
            }
            QGroupBox::indicator:hover {
                border: 1px solid #7C9CFF;
            }
            QGroupBox::indicator:checked {
                background: #6D8DFF;
                border: 1px solid #8EA5FF;
            }

            QPushButton { 
                background-color: #172033; 
                border: 1px solid #2C3B5A; 
                border-radius: 8px;
                padding: 9px 12px; 
                color: #F7FAFF;
                font-weight: 600; 
            }
            QPushButton:hover { background-color: #1E2A44; border-color: #4F8CFF; }
            QPushButton:disabled { background-color: #101828; border-color: #1E293B; color: #64748B; }
            
            QPushButton#start_btn {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7C9CFF, stop:1 #5A7AE8);
                color: #07111F;
                font-size: 15px;
                border: none;
                border-radius: 10px;
                font-weight: 700;
                padding: 12px 16px;
            }
            QPushButton#start_btn:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8EAAFF, stop:1 #6D8DFF); }
            QPushButton#start_btn:disabled { background: #1E293B; color: #64748B; }

            QPushButton#upload_cta_btn {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7C9CFF, stop:1 #5A7AE8);
                color: #07111F;
                font-size: 16px;
                border: none;
                border-radius: 12px;
                font-weight: 800;
                padding: 14px 18px;
            }
            QPushButton#upload_cta_btn:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8EAAFF, stop:1 #6D8DFF); }
            QPushButton#upload_cta_btn:disabled { background: #1E293B; color: #64748B; }

            QPushButton#utility_btn {
                padding: 5px 10px;
                font-size: 12px;
                border-radius: 6px;
                color: #DCE7FF;
                font-weight: 500;
                background-color: #151F32;
            }
            QPushButton#utility_btn:hover { background-color: #1D2A44; }
            QPushButton#main_generate_btn {
                background-color: #6D8DFF;
                color: #07111F;
                font-size: 15px;
                border: none;
                font-weight: 700;
            }
            QPushButton#main_generate_btn:hover { background-color: #7C9CFF; }
            QPushButton#main_generate_btn:disabled { background-color: #1E293B; color: #64748B; }
            
            QLineEdit, QTextEdit, QComboBox { 
                background-color: #0F172A; 
                border: 1px solid #2B3A55; 
                border-radius: 8px;
                padding: 7px 10px; 
                color: #F8FAFC; 
                min-height: 24px;
                selection-background-color: #6D8DFF;
                selection-color: #07111F;
            }
            QTextEdit { padding: 8px 10px; }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #7C9CFF; background-color: #111C33; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #111827; color: #F8FAFC; selection-background-color: #243B63; border-radius: 5px; }
            
            QTabWidget::pane { border: 1px solid #26324A; border-radius: 10px; background-color: #0E1628; margin-top: -1px; top: -1px; }
            QTabBar::tab {
                background: #111827; border: 1px solid #26324A;
                border-top-left-radius: 8px; border-top-right-radius: 8px;
                padding: 8px 18px; margin-right: 4px; color: #9FB2D8;
                min-width: 96px;
                border-top: 2px solid transparent;
            }
            QTabBar::tab:selected { background: #18243A; border-color: #4F8CFF; border-top: 2px solid #6D8DFF; color: #F7FAFF; font-weight: 700; }
            QTabBar::tab:hover:!selected { background: #172033; color: #DCE7FF; }
            
            QProgressBar { border: 1px solid #2B3A55; border-radius: 8px; background-color: #0F172A; text-align: center; color: #F8FAFC; font-weight: 700; height: 30px; }
            QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5A7AE8, stop:1 #7C9CFF); border-radius: 6px; }
            
            QCheckBox { color: #E2E8F0; spacing: 9px; padding: 4px 2px; background: transparent; }
            QCheckBox:hover { background: transparent; }
            QGroupBox QCheckBox { min-height: 22px; }
            QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #334155; border-radius: 5px; background: #0F172A; }
            QCheckBox::indicator:hover { border: 1px solid #7C9CFF; }
            QCheckBox::indicator:checked { background: #6D8DFF; border: 1px solid #8EA5FF; }
            
            QScrollArea { border: 1px solid #26324A; border-radius: 10px; background-color: #0E1628; }
            QTableWidget {
                background-color: #0E1628;
                border: 1px solid #26324A;
                border-radius: 8px;
                gridline-color: #26324A;
            }
            QHeaderView::section {
                background-color: #13213A;
                color: #C7D2FE;
                border: 1px solid #26324A;
                padding: 6px;
                font-weight: 600;
            }
            QTextEdit#log_area {
                background-color: #08111F;
                border: 1px solid #26324A;
                border-radius: 10px;
                color: #DCE7FF;
                padding: 10px;
            }
            QScrollBar:vertical { border: none; background: #0B1020; width: 12px; margin: 0px; }
            QScrollBar::handle:vertical { background: #334155; min-height: 20px; border-radius: 6px; }
            QScrollBar::handle:vertical:hover { background: #475569; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }

            QFrame#variant_card {
                border: 1px solid #26324A;
                border-radius: 10px;
                background-color: #111827;
            }
            QFrame#variant_card[has_path="true"] {
                border-color: #4F8CFF;
                background-color: #13213A;
            }

            QFrame#drop_zone {
                border: 2px dashed #4F8CFF;
                border-radius: 12px;
                background-color: #0F172A;
                min-height: 80px;
            }
            QFrame#drop_zone:hover {
                background-color: #13213A;
                border-color: #8EA5FF;
            }

            QSplitter::handle {
                background-color: #1E293B;
                width: 8px;
                border-radius: 4px;
                margin: 4px 2px;
            }
            QSplitter::handle:hover { background-color: #4F8CFF; }
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
            "translation_upload_worker",
            "translation_worker",
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

    def _set_translation_buttons_enabled(self, enabled):
        for btn in [
            getattr(self, "btn_translation_auto_source", None),
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
        api_key = self.gemini_key_input.text().strip()
        model = self.gemini_model_input.text().strip() or "gemini-2.5-flash"
        if not api_key:
            self._log("Ошибка: укажите Gemini API key в верхнем блоке настроек API.")
            return

        source_locale = self.translation_source_locale_combo.currentData() or "en-US"
        profile = self.translation_profile_combo.currentText().strip() or "ASO natural"
        self._set_translation_buttons_enabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Перевод: 0%")
        self.translation_worker = GeminiLocalizationTranslationWorker(
            api_key=api_key,
            model=model,
            source_locale=source_locale,
            target_locales=target_locales,
            fields=fields,
            profile_name=profile,
            source_payload=self.translation_source_payload,
        )
        self.translation_worker.log_msg.connect(self._log)
        self.translation_worker.progress_update.connect(self._update_progress)
        self.translation_worker.translations_ready.connect(self._populate_translation_table)
        self.translation_worker.finished.connect(lambda: self._set_translation_buttons_enabled(True))
        self.translation_worker.start()

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

    def _status_for_row(self, field_key, translation_text):
        text = str(translation_text or "").strip()
        if not text:
            return "warn", "Пустой перевод"
        limit = self._field_limit(field_key)
        if limit and len(text) > limit:
            return "error", f"Превышен лимит {len(text)}/{limit}"
        if field_key == "keywords":
            if " " in text:
                return "info", "Keywords: лучше без пробелов"
            items = [v.strip().lower() for v in text.split(",") if v.strip()]
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
        for row_idx in range(self.translation_table.rowCount()):
            field_item = self.translation_table.item(row_idx, 1)
            translation_item = self.translation_table.item(row_idx, 3)
            status_item = self.translation_table.item(row_idx, 4)
            warning_item = self.translation_table.item(row_idx, 5)
            if field_item is None or translation_item is None:
                continue
            if (
                preserve_existing_errors
                and status_item is not None
                and warning_item is not None
                and status_item.text().strip().lower() in {"error", "ошибка"}
                and warning_item.text().strip()
            ):
                self._apply_row_status_style(row_idx, "error", warning_item.text().strip())
                continue
            field_key = field_item.data(Qt.UserRole) or field_item.text().strip()
            status, warning = self._status_for_row(field_key, translation_item.text())
            self._apply_row_status_style(row_idx, status, warning)
        self.translation_table_busy = False
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
            return
        ready_rows = [row for row in rows if row.get("status") == "ok"]
        if not ready_rows:
            self._log("Нет строк со статусом «готово». Исправьте ошибки и повторите validate.")
            return
        self._set_translation_buttons_enabled(False)
        self.translation_upload_worker = LocalizationUploadWorker(self._metadata_api_creds(), rows)
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

    def _on_tab_change(self, index):
        if index == 0:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 СОЗДАТЬ НОВЫЙ ТЕСТ")
        elif index == 1:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 ОБНОВИТЬ ВЫБРАННЫЕ ДАННЫЕ В ТЕСТЕ")
        elif index == 2:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 ЗАГРУЗИТЬ МЕТАДАННЫЕ ПРИЛОЖЕНИЯ")
        elif index in (3, 4):
            self.start_btn.setVisible(False)
        else:
            self.start_btn.setVisible(True)
            self.start_btn.setText("🚀 ЗАПУСТИТЬ ПРОЦЕСС")

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
        self.upload_locale_picker.set_available_locales(active_locales)
        self._update_upload_summary()

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
        target_locales = self.upload_locale_picker.get_selected_locales()
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
            self._set_upload_jpeg_files(files)

    def _set_upload_jpeg_files(self, files):
        self.upload_jpeg_files = sorted([
            f for f in files
            if f and f.lower().endswith((".jpeg", ".jpg"))
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
            self.upload_files_warning.setText("Выберите или перетащите .jpg/.jpeg файлы для загрузки.")
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
        elif current_tab_index in (3, 4):
            self._log("Для этой вкладки используйте кнопки внутри вкладки.")
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
