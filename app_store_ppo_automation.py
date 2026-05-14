import os
import sys
import time
import jwt
import requests
import concurrent.futures
from dotenv import load_dotenv
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QPushButton, QTextEdit, QFileDialog, QLabel, 
    QProgressBar, QTabWidget, QCheckBox, QScrollArea, QComboBox
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import QThread, Signal, Qt, QSize

load_dotenv()

# Словарь: Код -> (Код_Флага, Понятное название)
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

    def _request(self, method, endpoint, payload=None):
        token = self._generate_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"{self.base_url}/{endpoint}"
        
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

    def get_version_locales(self, version_id):
        endpoint = f"appStoreVersions/{version_id}/appStoreVersionLocalizations"
        data = self._request("GET", endpoint)
        locales = []
        for loc in data.get("data", []):
            locales.append(loc["attributes"]["locale"])
        return locales

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
        
        with open(file_path, "rb") as f:
            for operation in upload_operations:
                headers = {h["name"]: h["value"] for h in operation["requestHeaders"]}
                offset = operation["offset"]
                length = operation["length"]
                f.seek(offset)
                chunk = f.read(length)
                
                req = requests.put(operation["url"], headers=headers, data=chunk)
                req.raise_for_status()

        commit_payload = {
            "data": {
                "type": "appScreenshots",
                "id": screenshot_id,
                "attributes": {"uploaded": True}
            }
        }
        self._request("PATCH", f"appScreenshots/{screenshot_id}", commit_payload)

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

    def __init__(self, mode, api_creds, test_name, traffic, target_variants, target_locales, variants_paths):
        super().__init__()
        self.mode = mode 
        self.api_creds = api_creds
        self.test_name = test_name
        self.traffic = traffic
        self.target_variants = target_variants
        self.target_locales = target_locales 
        self.variants_paths = variants_paths

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

            completed_tasks = 0
            start_time = time.time()

            if self.mode == "create":
                app_locales = client.get_version_locales(version_id)
                num_locales = len(app_locales)
                self.log_msg.emit(f"Найдено локалей в приложении: {num_locales}")
                
                total_tasks = 1 
                for v_name, f_path, files in active_variants:
                    total_tasks += 1 + num_locales 
                    total_tasks += num_locales * (1 + len(files))

                def tick():
                    nonlocal completed_tasks
                    completed_tasks += 1
                    percent = int((completed_tasks / total_tasks) * 100)
                    elapsed = time.time() - start_time
                    avg = elapsed / completed_tasks
                    rem = avg * (total_tasks - completed_tasks)
                    m, s = divmod(int(rem), 60)
                    self.progress_update.emit(percent, f"Осталось: ~{m:02d}:{s:02d}")

                self.log_msg.emit(f"Создание теста '{self.test_name}'...")
                experiment_id = client.create_ppo_experiment(version_id, self.test_name, self.traffic)
                tick()
                
                treatment_ids = {}
                localization_ids = {}
                
                self.log_msg.emit("ШАГ 1: Создание структуры теста...")
                for variant_name in self.variants_paths.keys():
                    treatment_id = client.create_treatment(experiment_id, variant_name)
                    treatment_ids[variant_name] = treatment_id
                    tick()
                    
                    localization_ids[variant_name] = {}
                    for locale in app_locales:
                        loc_id = client.create_treatment_localization(treatment_id, locale)
                        localization_ids[variant_name][locale] = loc_id
                        tick()
                        
                client.update_ppo_experiment(experiment_id, self.traffic)
                self.log_msg.emit("✅ Структура готова!")
                
                self.log_msg.emit("ШАГ 2: Загрузка скриншотов...")
                for variant_name, folder_path, files in active_variants:
                    for locale in app_locales:
                        localization_id = localization_ids[variant_name][locale]
                        
                        sets = client.get_screenshot_sets(localization_id)
                        target_set_id = None
                        for s in sets:
                            if s["attributes"]["screenshotDisplayType"] == "APP_IPHONE_67":
                                target_set_id = s["id"]
                            for old_sc in client.get_screenshots(s["id"]):
                                client.delete_screenshot(old_sc["id"])
                        
                        if not target_set_id:
                            target_set_id = client.create_screenshot_set(localization_id, "APP_IPHONE_67")
                        tick()

                        for index, filename in enumerate(files, start=1):
                            filepath = os.path.join(folder_path, filename)
                            ext = os.path.splitext(filename)[1].lower() 
                            new_filename = f"{index}{ext}"
                            self.log_msg.emit(f"[{variant_name} | {locale}] Загрузка {new_filename}...")
                            client.upload_screenshot(target_set_id, filepath, new_filename)
                            tick()

            elif self.mode == "update":
                self.log_msg.emit(f"Поиск теста '{self.test_name}'...")
                experiments = client.get_experiments(version_id)
                exp = next((e for e in experiments if e["attributes"]["name"] == self.test_name), None)
                if not exp:
                    raise Exception(f"Тест '{self.test_name}' не найден.")
                
                exp_id = exp["id"]
                treatments = client.get_treatments(exp_id)
                treatment_map = {t["attributes"]["name"]: t["id"] for t in treatments}

                update_tasks = []
                for v_name, f_path, files in active_variants:
                    if v_name not in self.target_variants:
                        continue 
                        
                    # --- УМНЫЙ ПОИСК ИМЕНИ ВАРИАНТА ---
                    # Если пользователь ищет "Variant C", а в Apple он называется "Treatment C"
                    actual_treatment_name = None
                    if v_name in treatment_map:
                        actual_treatment_name = v_name
                    else:
                        alt_name = v_name.replace("Variant", "Treatment")
                        if alt_name in treatment_map:
                            actual_treatment_name = alt_name

                    if not actual_treatment_name:
                        self.log_msg.emit(f"⚠️ Вариант '{v_name}' (или Treatment) не найден в Apple. Пропуск.")
                        continue
                        
                    t_id = treatment_map[actual_treatment_name]
                    locs = client.get_localizations(t_id)
                    
                    for loc in locs:
                        locale_code = loc["attributes"]["locale"]
                        if locale_code not in self.target_locales:
                            continue 
                            
                        update_tasks.append({
                            "variant": actual_treatment_name, "locale": locale_code, "loc_id": loc["id"],
                            "folder": f_path, "files": files
                        })

                if not update_tasks:
                    raise Exception("Не найдено подходящих локалей или вариантов для обновления. Проверьте галочки.")

                total_tasks = sum([1 + len(task["files"]) for task in update_tasks])

                def tick():
                    nonlocal completed_tasks
                    completed_tasks += 1
                    percent = int((completed_tasks / total_tasks) * 100)
                    elapsed = time.time() - start_time
                    avg = elapsed / completed_tasks
                    rem = avg * (total_tasks - completed_tasks)
                    m, s = divmod(int(rem), 60)
                    self.progress_update.emit(percent, f"Осталось: ~{m:02d}:{s:02d}")

                self.log_msg.emit(f"Найдено локалей для точечного обновления: {len(update_tasks)}")
                
                for task in update_tasks:
                    friendly_name = LOCALE_MAP.get(task['locale'], ("", task['locale']))[1]
                    self.log_msg.emit(f"[{task['variant']} | {friendly_name}] Очистка и заливка...")
                    sets = client.get_screenshot_sets(task['loc_id'])
                    target_set_id = None
                    
                    for s in sets:
                        if s["attributes"]["screenshotDisplayType"] == "APP_IPHONE_67":
                            target_set_id = s["id"]
                        for old_sc in client.get_screenshots(s["id"]):
                            client.delete_screenshot(old_sc["id"])
                            
                    if not target_set_id:
                        target_set_id = client.create_screenshot_set(task['loc_id'], "APP_IPHONE_67")
                    tick()

                    for index, filename in enumerate(task['files'], start=1):
                        filepath = os.path.join(task['folder'], filename)
                        ext = os.path.splitext(filename)[1].lower() 
                        new_filename = f"{index}{ext}"
                        client.upload_screenshot(target_set_id, filepath, new_filename)
                        tick()

            self.progress_update.emit(100, "Завершено!")
            self.log_msg.emit("=== ПРОЦЕСС УСПЕШНО ЗАВЕРШЕН! ===")
        except Exception as e:
            self.progress_update.emit(0, "Ошибка выполнения")
            self.log_msg.emit(f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
        finally:
            self.finished.emit()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        ensure_flags_downloaded()
        
        self.setWindowTitle("PPO Automation Control (PRO Mode)")
        self.resize(850, 950)
        self.variants_paths = {"Variant A": "", "Variant B": "", "Variant C": ""}
        self._setup_ui()
        self._apply_styles()

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
        self.btn_select_p8 = QPushButton("Выбрать .p8")
        self.btn_select_p8.clicked.connect(self._select_p8_file)
        key_layout.addWidget(self.p8_path_input)
        key_layout.addWidget(self.btn_select_p8)
        layout.addLayout(key_layout)

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
        
        # Сохраняем логические ключи для бэкенда
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

        self.lbl_folders = QLabel("ВЫБОР СКРИНШОТОВ (Для обоих режимов)")
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

    def _toggle_locales(self, state):
        for cb in self.locale_checkboxes:
            cb.setChecked(state)

    def _on_tab_change(self, index):
        if index == 0:
            self.start_btn.setText("🚀 СОЗДАТЬ НОВЫЙ ТЕСТ")
        else:
            self.start_btn.setText("🚀 ОБНОВИТЬ ВЫБРАННЫЕ ДАННЫЕ В ТЕСТЕ")

    def _select_p8_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", "", "Key Files (*.p8);;All Files (*)")
        if file_path: self.p8_path_input.setText(file_path)

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
        env_content = (
            f"ISSUER_ID={self.issuer_input.text().strip()}\n"
            f"KEY_ID={self.key_input.text().strip()}\n"
            f"APP_ID={self.app_input.text().strip()}\n"
            f"PRIVATE_KEY_PATH={self.p8_path_input.text().strip()}\n"
        )
        try:
            with open(".env", "w", encoding="utf-8") as f: f.write(env_content)
        except Exception as e:
            self._log(f"Ошибка сохранения .env файла: {e}")

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
        else:
            mode = "update"
            test_name = self.update_name_combo.currentText().strip()
            traffic = ""
            
            # Используем скрытые ключи свойств для передачи в бэкенд ("Variant A", и т.д.)
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

        self.start_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.time_label.setText("Осталось: --:--")
        self.log_area.clear()
        
        self.worker = AutomationWorker(mode, api_creds, test_name, traffic, target_variants, target_locales, self.variants_paths)
        self.worker.log_msg.connect(self._log)
        self.worker.progress_update.connect(self._update_progress)
        self.worker.finished.connect(lambda: self.start_btn.setEnabled(True))
        self.worker.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())