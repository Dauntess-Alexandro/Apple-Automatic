import concurrent.futures
import re
import time
from pathlib import Path

import requests

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHeaderView, QHBoxLayout,
    QFileDialog, QGroupBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from asset_library import AssetLibraryUploader, SURFACE_TYPES
from asset_library_import import LibraryImportWorker, ScreenshotImportSource


MODES = {
    "version": ("APP_STORE_VERSIONS", "appStoreVersionLocalization"),
    "ppo": ("PRODUCT_PAGE_OPTIMIZATIONS", "appStoreVersionExperimentTreatmentLocalization"),
}


EDITABLE_APP_STORE_VERSION_STATES = (
    "PREPARE_FOR_SUBMISSION", "INVALID_BINARY", "REJECTED",
    "METADATA_REJECTED", "DEVELOPER_REJECTED",
)

LIBRARY_ASSIGNMENT_MAX_WORKERS = 12
LIBRARY_ASSIGNMENT_ATTEMPTS = 4


def is_transient_assignment_error(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                            requests.exceptions.ChunkedEncodingError, TimeoutError)):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            return exc.response is not None and exc.response.status_code in {429, 500, 502, 503, 504}
        exc = exc.__cause__ or exc.__context__
    return False


def short_id(identifier):
    return identifier if len(identifier) <= 12 else identifier[:10] + "…"


def compatible_images(snapshot, group):
    images = []
    for image in snapshot["images"]:
        try:
            snapshot["catalog"].validate_existing_image(image, group)
        except ValueError:
            continue
        images.append(image)
    return images


def image_name_key(image):
    name = ((image.get("attributes") or {}).get("fileName") or "").casefold()
    return tuple((1, int(part)) if part.isdecimal() else (0, part) for part in re.split(r"(\d+)", name))


def group_label(group):
    display = group.get("displayClassId", "")
    labels = {
        "IPHONE_DYNAMIC_ISLAND_MEDIUM_DISPLAY": "iPhone Dynamic Island — Medium",
        "IPHONE_DYNAMIC_ISLAND_LARGE_DISPLAY": "iPhone Dynamic Island — Large",
        "IPHONE_FACE_ID_LARGE_DISPLAY": "iPhone Face ID — Large",
    }
    name = labels.get(display) or display.replace("_DISPLAY", "").replace("_", " ").title()
    return f"{name or 'Другая категория Apple'} · до {group['max_count']} скринов"


def target_tooltip(target):
    return (f"{target['label']}\nВерсия: {target['version_id']}\n"
            f"Тест: {target.get('experiment_id') or '—'}\nВетка/версия: {target['parent_id']}\nЛокализация: {target['id']}")


def validate_version(client, version_id, mode):
    attributes = client._request("GET", f"appStoreVersions/{version_id}")["data"].get("attributes") or {}
    if attributes.get("platform") != "IOS":
        raise ValueError("Назначение доступно только для версии iOS.")
    if mode == "version" and attributes.get("appStoreState") not in EDITABLE_APP_STORE_VERSION_STATES:
        raise ValueError(f"Обычная версия должна быть редактируемой: подготовка к отправке или отклонённая версия. Текущий статус: {attributes.get('appStoreState')}.")
    return attributes


def load_existing_targets(client, mode, uploader):
    if mode not in MODES:
        raise ValueError("Неизвестный режим назначения из библиотеки.")
    relationship = MODES[mode][1]
    targets = []

    def add_locales(endpoint, parent_id, version_id, label, experiment_id=None):
        for record in uploader._list(endpoint + "?limit=200"):
            locale = (record.get("attributes") or {}).get("locale")
            if not record.get("id") or not locale:
                raise ValueError("Apple не вернула ID/язык существующей локализации.")
            targets.append({"id": record["id"], "locale": locale, "parent_id": parent_id,
                            "version_id": version_id, "experiment_id": experiment_id,
                            "relationship": relationship, "mode": mode,
                            "label": f"{locale} · {label} · локаль [{short_id(record['id'])}]"})

    if mode == "version":
        version_id = client.get_latest_app_store_version()
        attributes = validate_version(client, version_id, mode)
        label = f"Версия {attributes.get('versionString', '')} [{short_id(version_id)}]"
        add_locales(f"appStoreVersions/{version_id}/appStoreVersionLocalizations", version_id, version_id, label)
    else:
        versions = uploader._list(f"apps/{client.app_id}/appStoreVersions?filter[platform]=IOS&limit=200")
        for version in versions:
            attributes = version.get("attributes") or {}
            if attributes.get("platform") != "IOS":
                continue
            version_id = version["id"]
            version_label = f"Версия {attributes.get('versionString', '')} [{short_id(version_id)}] ({attributes.get('appStoreState', '?')})"
            experiments = uploader._list(f"appStoreVersions/{version_id}/appStoreVersionExperiments?limit=200")
            for experiment in experiments:
                treatments = uploader._list(
                    f"appStoreVersionExperiments/{experiment['id']}/appStoreVersionExperimentTreatments?limit=200"
                )
                for treatment in treatments:
                    label = (f"{version_label} / {(experiment.get('attributes') or {}).get('name', '?')} [{short_id(experiment['id'])}] / "
                             f"{(treatment.get('attributes') or {}).get('name', '?')} [{short_id(treatment['id'])}]")
                    add_locales(f"appStoreVersionExperimentTreatments/{treatment['id']}/appStoreVersionExperimentTreatmentLocalizations",
                                treatment["id"], version_id, label, experiment["id"])
    return targets


class LibraryRefreshWorker(QThread):
    loaded = Signal(dict)
    error = Signal(str)
    log_msg = Signal(str)

    def __init__(self, client_factory, mode, parent=None):
        super().__init__(parent)
        self.client_factory = client_factory
        self.mode = mode

    def run(self):
        try:
            if self.mode not in MODES:
                raise ValueError("Неизвестный режим назначения из библиотеки.")
            client = self.client_factory(self.log_msg.emit)
            uploader = AssetLibraryUploader(client, self.log_msg.emit)
            images = uploader.list_existing_images()
            feature = MODES[self.mode][0]
            self.loaded.emit({"images": images, "groups": uploader.catalog.screenshot_groups(feature),
                              "catalog": uploader.catalog, "targets": load_existing_targets(client, self.mode, uploader)})
        except Exception as exc:
            self.error.emit(str(exc))


class LibraryAssignmentWorker(QThread):
    completed = Signal(list)
    progress = Signal(int, int)
    log_msg = Signal(str)

    def __init__(self, client_factory, targets, batches, cleanup_spec_mismatch=False, parent=None):
        super().__init__(parent)
        self.client_factory = client_factory
        self.targets = [dict(target) for target in targets]
        self.batches = {group: list(identifiers) for group, identifiers in batches.items()}
        self.cleanup_spec_mismatch = cleanup_spec_mismatch

    def run(self):
        rows = []

        def result(target, status, message):
            row = {**target, "status": status, "message": message}
            rows.append(row)
            marker = {"success": "✅", "failed": "❌", "manual_check": "⚠️"}[status]
            self.log_msg.emit(f"{marker} {target.get('label', target.get('id', '?'))}: {message}")
            self.progress.emit(len(rows), len(self.targets))

        try:
            if not self.targets:
                raise ValueError("Выберите существующие локали.")
            mode = self.targets[0]["mode"]
            if mode not in MODES or any(target["mode"] != mode for target in self.targets):
                raise ValueError("Нельзя смешивать режимы назначения.")
            if len({target["id"] for target in self.targets}) != len(self.targets):
                raise ValueError("Локализация выбрана несколько раз.")
            client = self.client_factory(self.log_msg.emit)
            uploader = AssetLibraryUploader(client, self.log_msg.emit)
            available = {target["id"]: target for target in load_existing_targets(client, mode, uploader)}
            uploader.prepare_existing_batches(self.batches, MODES[mode][0])
        except Exception as exc:
            for target in self.targets:
                result(target, "failed", f"До изменений: {exc}")
            self.completed.emit(rows)
            return

        def assign_target(target):
            mutation_started = False
            try:
                local_client = self.client_factory(self.log_msg.emit)
                local_uploader = AssetLibraryUploader(local_client, self.log_msg.emit)
                local_uploader.catalog = uploader.catalog
                local_uploader.library_id = uploader.library_id
                local_uploader._library_image_ids = set(uploader._library_image_ids)
                for attempt in range(LIBRARY_ASSIGNMENT_ATTEMPTS):
                    try:
                        current = available.get(target["id"])
                        identity = ("id", "locale", "parent_id", "version_id", "experiment_id", "relationship", "mode")
                        if current is None or any(current.get(key) != target.get(key) for key in identity):
                            raise ValueError("Выбранная локаль/ветка больше не существует в текущей версии; обновите список.")
                        validate_version(local_client, target["version_id"], mode)
                        parent_relationship = "appStoreVersion" if mode == "version" else "appStoreVersionExperimentTreatment"
                        fresh = local_client._request("GET", f"{SURFACE_TYPES[target['relationship']]}/{target['id']}?include={parent_relationship}")["data"]
                        parent_id = ((((fresh.get("relationships") or {}).get(parent_relationship) or {}).get("data")) or {}).get("id")
                        if (fresh.get("id") != target["id"] or parent_id != target["parent_id"]
                                or (fresh.get("attributes") or {}).get("locale") != target["locale"]):
                            raise ValueError("Локаль/ветка изменилась; обновите список. Новые локали не создаются.")
                        mutation_started = True
                        warnings = local_uploader.replace_existing_localization(
                            target["id"], self.batches, target["relationship"], self.cleanup_spec_mismatch
                        )
                        break
                    except Exception as exc:
                        safe_to_retry = not mutation_started or getattr(exc, "asset_library_retry_safe", False)
                        if safe_to_retry:
                            mutation_started = False
                        if not safe_to_retry or not is_transient_assignment_error(exc) or attempt == LIBRARY_ASSIGNMENT_ATTEMPTS - 1:
                            raise
                        delay = 2 ** attempt
                        self.log_msg.emit(
                            f"⚠️ {target.get('label', target['id'])}: {exc}; повтор {attempt + 2}/{LIBRARY_ASSIGNMENT_ATTEMPTS} через {delay} с."
                        )
                        time.sleep(delay)
                if warnings:
                    return target, "manual_check", "Выбранный порядок проверен, но остались ошибки Apple: " + "; ".join(warnings)
                return target, "success", "ID, порядок и готовность размещений подтверждены Apple."
            except Exception as exc:
                status = "manual_check" if mutation_started else "failed"
                suffix = " Проверьте локаль вручную; библиотечные файлы сохранены." if mutation_started else " Изменения не начинались или прежнее состояние восстановлено и проверено."
                return target, status, str(exc) + suffix

        self.log_msg.emit(f"Asset Library: назначаю скрины в {len(self.targets)} локалей, до {LIBRARY_ASSIGNMENT_MAX_WORKERS} воркеров.")
        ordered_rows = [None] * len(self.targets)
        with concurrent.futures.ThreadPoolExecutor(max_workers=LIBRARY_ASSIGNMENT_MAX_WORKERS) as executor:
            futures = {executor.submit(assign_target, target): index for index, target in enumerate(self.targets)}
            for future in concurrent.futures.as_completed(futures):
                target, status, message = future.result()
                result(target, status, message)
                ordered_rows[futures[future]] = rows[-1]
        rows = ordered_rows
        self.completed.emit(rows)


class _LibrarySelectionPanel:
    allow_import = False

    def __init__(self, client_factory, mode="version", parent=None):
        super().__init__(parent)
        self.client_factory = client_factory
        self.mode = mode
        self.snapshot = None
        self.worker = None
        self.prepare_operation = None
        self.selections = {}
        self.import_source = None
        self.pending_import = None
        self.import_confirmations = {}
        self.import_target_ids = set()
        self.assignment_batches = {}
        self.assignment_target_ids = set()
        self._refresh_after_import = False
        self.setObjectName("assetLibraryPanel")
        self.setStyleSheet("""
            QWidget#assetLibraryPanel { background: #0b0b14; color: #e8e8f0; }
            QWidget#assetLibraryPanel QWidget { font-size: 13px; }
            QWidget#assetLibraryPanel QLabel { color: #c4b5fd; background: transparent; }
            QWidget#assetLibraryPanel QPushButton {
                background: #1a1a2e; color: #e8e8f0; border: 1px solid #343451;
                border-radius: 6px; padding: 6px 10px; min-height: 18px;
            }
            QWidget#assetLibraryPanel QPushButton:hover { border-color: #a855f7; }
            QWidget#assetLibraryPanel QPushButton:disabled { color: #6b7280; background: #12121e; }
            QWidget#assetLibraryPanel QLineEdit, QWidget#assetLibraryPanel QComboBox {
                background: #10101d; color: #e8e8f0; border: 1px solid #343451;
                border-radius: 6px; padding: 6px 8px; min-height: 18px;
            }
            QWidget#assetLibraryPanel QTableWidget, QWidget#assetLibraryPanel QListWidget {
                background: #10101d; color: #e8e8f0; border: 1px solid #343451;
                border-radius: 5px; padding: 0px; gridline-color: #2a2a45;
                selection-background-color: #61349c;
            }
            QWidget#assetLibraryPanel QHeaderView::section {
                background: #202033; color: #c4b5fd; border: 0px; padding: 5px;
            }
            QWidget#assetLibraryPanel QListWidget::item { padding: 3px; }
            QWidget#assetLibraryPanel QCheckBox { color: #e8e8f0; spacing: 6px; padding: 2px; }
        """)
        self.setWindowTitle("Взять из Asset Library — " + ("версия приложения" if mode == "version" else "существующие ветки PPO"))
        if isinstance(self, QDialog):
            self.resize(1150, 830)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        hint = QLabel(
            "Категория, скрины по имени и все существующие локали выбираются автоматически. "
            "Назначается только текущая категория. Проверьте порядок и нажмите «Назначить». "
            "Файлы библиотеки и метаданные не меняются."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if self.allow_import:
            self._build_import_controls(layout)
        self.refresh_button = QPushButton("Обновить библиотеку и существующие локали")
        self.refresh_button.clicked.connect(self.refresh_library)
        layout.addWidget(self.refresh_button)
        self.editor = QWidget()
        editor_layout = QVBoxLayout(self.editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(8)
        group_row = QHBoxLayout()
        group_row.addWidget(QLabel("Категория:"))
        self.group_combo = QComboBox()
        self.group_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.group_combo.setMinimumContentsLength(30)
        self.group_combo.currentIndexChanged.connect(self.render_images)
        group_row.addWidget(self.group_combo, 1)
        editor_layout.addLayout(group_row)
        self.scope_combo = QComboBox(self.editor)
        self.scope_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.scope_combo.currentIndexChanged.connect(self.apply_target_scope)
        if mode == "ppo":
            scope_row = QHBoxLayout()
            scope_row.addWidget(QLabel("Ветка PPO:"))
            scope_row.addWidget(self.scope_combo, 1)
            editor_layout.addLayout(scope_row)
        else:
            self.scope_combo.hide()
        self.image_search = QLineEdit()
        self.image_search.setPlaceholderText("Фильтр изображений по имени или полному ID")
        self.image_search.textChanged.connect(self.filter_images)
        editor_layout.addWidget(self.image_search)
        self.images_table = QTableWidget(0, 6)
        self.images_table.setHorizontalHeaderLabels(["Имя файла", "Размеры", "Статус / причина", "Загружено / создано", "ID изображения", "Совместимость"])
        self.images_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.images_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.images_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.images_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((170, 100, 160, 160, 120, 180)):
            self.images_table.setColumnWidth(column, width)
        self.images_table.horizontalHeader().setStretchLastSection(True)
        self.images_table.verticalHeader().setDefaultSectionSize(28)
        self.images_table.setMinimumHeight(220)
        editor_layout.addWidget(self.images_table, 3)
        add_row = QHBoxLayout()
        self.add_button = QPushButton("Добавить выделенный скрин ↓")
        self.add_button.clicked.connect(self.add_selected_image)
        add_row.addWidget(self.add_button)
        self.count_label = QLabel("Выбрано: 0")
        add_row.addWidget(self.count_label)
        editor_layout.addLayout(add_row)
        splitter = QSplitter(Qt.Horizontal)
        ordered_panel = QWidget()
        ordered_layout = QVBoxLayout(ordered_panel)
        ordered_layout.setContentsMargins(0, 0, 0, 0)
        ordered_layout.addWidget(QLabel("Выбранные скрины — порядок показа:"))
        self.chosen_list = QListWidget()
        ordered_layout.addWidget(self.chosen_list)
        order_row = QHBoxLayout()
        for title, handler in [("↑ Выше", lambda: self.move_selected_image(-1)),
                               ("↓ Ниже", lambda: self.move_selected_image(1)),
                               ("Убрать", self.remove_selected_image)]:
            button = QPushButton(title)
            button.clicked.connect(handler)
            order_row.addWidget(button)
        ordered_layout.addLayout(order_row)
        splitter.addWidget(ordered_panel)
        target_panel = QWidget()
        target_layout = QVBoxLayout(target_panel)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(QLabel("Существующие локали:"))
        self.target_search = QLineEdit()
        self.target_search.setPlaceholderText("Фильтр локалей / веток / версии / ID")
        self.target_search.textChanged.connect(self.filter_targets)
        target_layout.addWidget(self.target_search)
        self.targets_list = QListWidget()
        self.targets_list.itemChanged.connect(self.update_assign_enabled)
        target_layout.addWidget(self.targets_list)
        target_row = QHBoxLayout()
        select_targets = QPushButton("Выбрать все")
        select_targets.clicked.connect(lambda: self.check_visible_targets(Qt.Checked))
        clear_targets = QPushButton("Снять отметки")
        clear_targets.clicked.connect(lambda: self.check_visible_targets(Qt.Unchecked))
        target_row.addWidget(select_targets)
        target_row.addWidget(clear_targets)
        target_layout.addLayout(target_row)
        splitter.addWidget(target_panel)
        splitter.setSizes([550, 550])
        editor_layout.addWidget(splitter, 2)
        self.cleanup_checkbox = QCheckBox(
            "Убрать несовместимые старые размещения других категорий"
        )
        self.cleanup_checkbox.setToolTip(
            "Только APP_SCREENSHOT / INVALID / строго SPEC_MISMATCH, после проверки выбранных ассетов. "
            "Не затрагивает корректные группы, превью и изображения библиотеки."
        )
        editor_layout.addWidget(self.cleanup_checkbox)
        layout.addWidget(self.editor, 4)
        self.results_table = QTableWidget(0, 3)
        self.results_table.setHorizontalHeaderLabels(["Локаль / ветка / ID", "Результат", "Подробности"])
        self.results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setMaximumHeight(145)
        self.results_table.hide()
        layout.addWidget(self.results_table, 1)
        self.notice = QLabel("Обновите библиотеку для автоматического выбора.")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.PlainText)
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        self.assign_button = QPushButton("Назначить скрины и проверить в Apple")
        self.assign_button.clicked.connect(self.assign_selected)
        footer.addWidget(self.assign_button)
        if isinstance(self, QDialog):
            close_button = QPushButton("Закрыть")
            close_button.clicked.connect(self.reject)
            footer.addWidget(close_button)
        layout.addLayout(footer)
        self.editor.setEnabled(False)
        self.assign_button.setEnabled(False)

    def _build_import_controls(self, layout):
        panel = QGroupBox("1. Загрузить новые скриншоты в Asset Library")
        controls = QVBoxLayout(panel)
        buttons = QHBoxLayout()
        self.import_folder_button = QPushButton("Выбрать папку со скринами")
        self.import_folder_button.clicked.connect(self.choose_import_folder)
        self.import_zip_button = QPushButton("Выбрать ZIP-архив")
        self.import_zip_button.clicked.connect(self.choose_import_zip)
        self.import_button = QPushButton("Загрузить в Asset Library")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.start_import)
        for button in (self.import_folder_button, self.import_zip_button, self.import_button):
            buttons.addWidget(button)
        controls.addLayout(buttons)
        self.import_label = QLabel("Папка или ZIP не выбраны.")
        self.import_label.setWordWrap(True)
        self.import_label.setTextFormat(Qt.PlainText)
        controls.addWidget(self.import_label)
        self.import_preview = QListWidget()
        self.import_preview.setMaximumHeight(100)
        self.import_preview.hide()
        controls.addWidget(self.import_preview)
        hint = QLabel("Случайные имена; порядок — по исходным именам: 1, 2, …, 10. Размеры сохраняются. "
                      "После загрузки новый набор будет выбран ниже. Папка/ZIP удалятся только после "
                      "успешного назначения всего набора всем выбранным локалям и проверки Apple.")
        hint.setWordWrap(True)
        controls.addWidget(hint)
        layout.addWidget(panel)

    def choose_import_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Папка со скриншотами")
        if path:
            self.select_import_source(path)

    def choose_import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "ZIP со скриншотами", "", "ZIP-архивы (*.zip)")
        if path:
            self.select_import_source(path)

    def select_import_source(self, path):
        if self.is_busy():
            return
        try:
            source = ScreenshotImportSource(path)
        except Exception as exc:
            self.import_source = None
            self.import_preview.clear()
            self.import_preview.hide()
            self.set_busy(False)
            self.import_label.setText(str(exc))
            return
        self.import_source = source
        self.import_label.setText(f"{source.path}\nСкриншотов: {len(source.files)}")
        self.import_preview.clear()
        for index, name in enumerate(source.files, 1):
            self.import_preview.addItem(f"{index}. {name if source.is_archive else str(Path(name).relative_to(source.path))}")
        self.import_preview.show()
        self.set_busy(False)

    def start_import(self):
        if self.is_busy() or self.import_source is None:
            return
        if self.prepare_operation is not None and not self.prepare_operation():
            return
        self.notice.setText("Проверка всех файлов и требований Apple перед загрузкой…")
        self.worker = LibraryImportWorker(self.client_factory, self.import_source, self)
        self.worker.log_msg.connect(self.log_msg.emit)
        self.worker.completed.connect(self.apply_import_result)
        self.worker.error.connect(self.show_import_error)
        self.worker.progress.connect(lambda done, total: self.notice.setText(f"Обработано Apple: {done}/{total}"))
        self.worker.finished.connect(self.finish_import)
        self.set_busy(True)
        self.worker.start()

    def apply_import_result(self, result):
        if self.pending_import:
            self.log_msg.emit(f"Предыдущий источник сохранён: {self.pending_import['source'].path}")
        self.pending_import = result
        self.import_confirmations = {}
        self.import_target_ids = set()
        self.import_label.setText(f"Загружено в Asset Library: {len(result['images'])}. "
                                  f"Источник сохранён до назначения локалям: {result['source'].path}")
        self.import_source = None
        if self.snapshot is not None:
            snapshot = self.snapshot
            if "catalog" in result:
                snapshot = dict(snapshot, catalog=result["catalog"], groups=result["groups"])
            self.apply_snapshot(snapshot)
            self.notice.setText("Новый набор выбран в исходном порядке. Проверьте локали и нажмите «Назначить».")
        else:
            self._refresh_after_import = True

    def finish_import(self):
        self.set_busy(False)
        if self._refresh_after_import:
            self._refresh_after_import = False
            self.refresh_library()

    def show_import_error(self, message):
        self.notice.setText("Загрузка не завершена; папка/архив сохранены. " + message)
        self.log_msg.emit(self.notice.text())

    def _cleanup_import_after_assignment(self, rows):
        if not self.pending_import or not rows:
            return
        selected_ids = {identifier for identifiers in self.assignment_batches.values() for identifier in identifiers}
        uploaded_ids = {image["id"] for image in self.pending_import["images"]}
        if selected_ids & uploaded_ids:
            self.import_target_ids.update(self.assignment_target_ids)
        for row in rows:
            confirmed = self.import_confirmations.setdefault(row["id"], {})
            for group, identifiers in self.assignment_batches.items():
                confirmed[group] = set(identifiers) & uploaded_ids if row["status"] == "success" else set()
        complete = bool(self.import_target_ids) and all(
            uploaded_ids <= set().union(*self.import_confirmations.get(target, {}).values())
            for target in self.import_target_ids
        )
        if (not complete or not self.assignment_target_ids
                or {row["id"] for row in rows} != self.assignment_target_ids
                or any(row["status"] != "success" for row in rows)):
            self.log_msg.emit("Исходная папка/ZIP сохранены: назначение всего загруженного набора ещё не подтверждено.")
            return
        source = self.pending_import["source"]
        try:
            source.delete_after_success()
        except (OSError, ValueError) as exc:
            self.import_label.setText(f"Назначение проверено. Источник сохранён: {exc}")
            self.log_msg.emit(self.import_label.text())
            return
        self.pending_import = None
        self.import_confirmations = {}
        self.import_target_ids = set()
        message = f"Назначение проверено Apple. Удалён источник: {source.path}"
        if self.import_source is None:
            self.import_preview.clear()
            self.import_preview.hide()
            self.import_label.setText(message)
        self.log_msg.emit(message)

    def is_busy(self):
        return self.worker is not None and self.worker.isRunning()

    def set_busy(self, busy):
        self.refresh_button.setEnabled(not busy)
        if self.allow_import:
            self.import_folder_button.setEnabled(not busy)
            self.import_zip_button.setEnabled(not busy)
            self.import_button.setEnabled(not busy and self.import_source is not None)
        self.editor.setEnabled(not busy and self.snapshot is not None)
        self.assign_button.setEnabled(not busy and bool(self.selected_batches()) and bool(self.selected_targets()))

    def refresh_library(self):
        if self.is_busy():
            return
        if self.prepare_operation is not None and not self.prepare_operation():
            return
        self.reset_library()
        self.notice.setText("Чтение библиотеки и существующих локалей Apple…")
        self.worker = LibraryRefreshWorker(self.client_factory, self.mode, self)
        self.worker.log_msg.connect(self.log_msg.emit)
        self.worker.loaded.connect(self.apply_snapshot)
        self.worker.error.connect(self.show_error)
        self.worker.finished.connect(lambda: self.set_busy(False))
        self.set_busy(True)
        self.worker.start()

    def reset_library(self):
        self.snapshot = None
        self.selections = {}
        self.targets_list.clear()
        self.images_table.setRowCount(0)
        self.chosen_list.clear()
        self.group_combo.clear()
        self.scope_combo.clear()
        self.results_table.hide()
        self.set_busy(self.is_busy())

    def clear_context(self):
        if self.pending_import:
            self.log_msg.emit(f"Приложение/ключ изменились. Исходники сохранены: {self.pending_import['source'].path}")
        self.pending_import = None
        self.import_confirmations = {}
        self.import_target_ids = set()
        self.assignment_batches = {}
        self.assignment_target_ids = set()
        self.reset_library()
        if self.allow_import:
            self.import_label.setText("Приложение/ключ изменились. Обновите библиотеку; исходники предыдущей загрузки сохранены.")

    def apply_snapshot(self, snapshot):
        if self.pending_import:
            images = {image["id"]: image for image in self.pending_import["images"]}
            images.update({image["id"]: image for image in snapshot["images"]})
            snapshot = dict(snapshot, images=list(images.values()))
        self.snapshot = snapshot
        self.selections = ({group: list(identifiers) for group, identifiers in self.pending_import["batches"].items()
                            if group in snapshot["groups"]} if self.pending_import else {})
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        best_group = None
        best_score = (-1, -1)
        for group_id, group in snapshot["groups"].items():
            self.group_combo.addItem(group_label(group), group_id)
            self.group_combo.setItemData(self.group_combo.count() - 1,
                                         f"{group.get('displayClassId', '')}\n{group.get('platform', '')}\n{group_id}", Qt.ToolTipRole)
            compatible_count = (len(self.selections.get(group_id, [])) if self.pending_import
                                else len(compatible_images(snapshot, group)))
            medium = group.get("displayClassId") == "IPHONE_DYNAMIC_ISLAND_MEDIUM_DISPLAY"
            score = (int(medium and compatible_count > 0), compatible_count)
            if score > best_score:
                best_group, best_score = group_id, score
        if best_group is not None:
            self.group_combo.setCurrentIndex(self.group_combo.findData(best_group))
        self.group_combo.blockSignals(False)
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("Выберите ветку — её локали отметятся автоматически", None)
        parents = {}
        for target in snapshot["targets"]:
            parents.setdefault(target["parent_id"], target)
        for parent_id, target in parents.items():
            label = target["label"].partition(" · ")[2].rsplit(" · ", 1)[0] or parent_id
            self.scope_combo.addItem(label, parent_id)
            self.scope_combo.setItemData(self.scope_combo.count() - 1, target_tooltip(target), Qt.ToolTipRole)
        if len(parents) == 1:
            self.scope_combo.setCurrentIndex(1)
        self.scope_combo.blockSignals(False)
        self.targets_list.clear()
        for target in snapshot["targets"]:
            item = QListWidgetItem(target["locale"])
            item.setToolTip(target_tooltip(target))
            item.setData(Qt.UserRole, target)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            selected = self.mode == "version" or target["parent_id"] == self.scope_combo.currentData()
            item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
            self.targets_list.addItem(item)
        self.filter_targets()
        self.render_images()
        self.set_busy(self.is_busy())

    def apply_target_scope(self):
        for index in range(self.targets_list.count()):
            item = self.targets_list.item(index)
            selected = self.mode == "version" or item.data(Qt.UserRole)["parent_id"] == self.scope_combo.currentData()
            item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
        self.filter_targets()
        self.update_assign_enabled()

    def auto_select_group(self, group_id, group):
        images = compatible_images(self.snapshot, group)
        names = [((image.get("attributes") or {}).get("fileName") or "").strip().casefold() for image in images]
        self.selections[group_id] = []
        if len(images) > group["max_count"]:
            message = f"Подходящих скринов: {len(images)}, лимит Apple: {group['max_count']}. Выберите нужный набор вручную."
        elif len(set(names)) != len(names) or any(not name for name in names):
            message = "В библиотеке одинаковые имена или нет имени. Выберите нужную пачку вручную по ID и дате."
        elif not images:
            message = "Нет готовых скринов для этой категории. Проверьте размеры и статус в таблице."
        else:
            self.selections[group_id] = [image["id"] for image in sorted(images, key=image_name_key)]
            message = f"Автоматически выбрано скринов: {len(images)} — по имени. Порядок можно изменить ниже."
        if self.mode == "ppo" and self.scope_combo.currentData() is None:
            message += " Выберите ветку PPO."
        self.notice.setText(message)

    def show_error(self, message):
        self.notice.setText("Ошибка чтения; назначения не выполнялись: " + message)
        self.log_msg.emit(self.notice.text())

    def render_images(self):
        self.images_table.setRowCount(0)
        self.chosen_list.clear()
        if not self.snapshot:
            return
        group = self.snapshot["groups"].get(self.group_combo.currentData())
        if not group:
            return
        group_id = self.group_combo.currentData()
        self.group_combo.setToolTip(self.group_combo.currentData(Qt.ToolTipRole))
        if group_id not in self.selections:
            self.auto_select_group(group_id, group)
        self.images_table.setRowCount(len(self.snapshot["images"]))
        for row, resource in enumerate(self.snapshot["images"]):
            attributes = resource.get("attributes") or {}
            dimensions = attributes.get("imageAsset") or {}
            state = f"{attributes.get('state', '?')} {attributes.get('stateDetails') or ''}"
            try:
                self.snapshot["catalog"].validate_existing_image(resource, group)
                compatible = "Можно выбрать"
            except ValueError as exc:
                compatible = str(exc)
            uploaded = attributes.get("uploadedDate") or attributes.get("uploadDate") or attributes.get("createdDate") or "—"
            values = [attributes.get("fileName", "?"), f"{dimensions.get('width', '?')}×{dimensions.get('height', '?')}",
                      state, uploaded, short_id(resource["id"]), compatible]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == 4:
                    item.setToolTip(resource["id"])
                    item.setData(Qt.UserRole, resource["id"])
                elif column == 5:
                    item.setToolTip(f"{compatible}\nСпецификация: {attributes.get('specId', '?')}")
                self.images_table.setItem(row, column, item)
        self.filter_images()
        self.render_order()

    def filter_images(self):
        query = self.image_search.text().casefold()
        for row in range(self.images_table.rowCount()):
            text = (self.images_table.item(row, 0).text() + " " + self.images_table.item(row, 4).toolTip()).casefold()
            self.images_table.setRowHidden(row, query not in text)

    def filter_targets(self):
        query = self.target_search.text().casefold()
        for index in range(self.targets_list.count()):
            item = self.targets_list.item(index)
            wrong_scope = self.mode == "ppo" and item.data(Qt.UserRole)["parent_id"] != self.scope_combo.currentData()
            searchable = (item.text() + " " + item.toolTip()).casefold()
            item.setHidden(wrong_scope or query not in searchable)

    def check_visible_targets(self, state):
        for index in range(self.targets_list.count()):
            item = self.targets_list.item(index)
            if not item.isHidden() or state == Qt.Unchecked:
                item.setCheckState(state)

    def render_order(self):
        self.chosen_list.clear()
        group_id = self.group_combo.currentData()
        images = {item["id"]: item for item in self.snapshot["images"]} if self.snapshot else {}
        identifiers = self.selections.get(group_id, [])
        for index, identifier in enumerate(identifiers, 1):
            name = (images[identifier].get("attributes") or {}).get("fileName", "?")
            item = QListWidgetItem(f"{index}. {name} [{short_id(identifier)}]")
            item.setToolTip(identifier)
            self.chosen_list.addItem(item)
        self.count_label.setText(f"Выбрано в текущей категории: {len(identifiers)}")
        self.update_assign_enabled()

    def add_selected_image(self):
        rows = self.images_table.selectionModel().selectedRows()
        if not rows or not self.snapshot or self.is_busy():
            return
        row = rows[0].row()
        if self.images_table.isRowHidden(row):
            return
        group_id = self.group_combo.currentData()
        group = self.snapshot["groups"][group_id]
        resource = self.snapshot["images"][row]
        try:
            self.snapshot["catalog"].validate_existing_image(resource, group)
            identifiers = self.selections.get(group_id, [])
            if resource["id"] in identifiers:
                raise ValueError("Этот ID уже выбран в текущей группе.")
            if len(identifiers) >= group["max_count"]:
                raise ValueError(f"Лимит Apple: {group['max_count']} изображений в группе.")
            self.selections[group_id] = identifiers + [resource["id"]]
            self.render_order()
            self.chosen_list.setCurrentRow(self.chosen_list.count() - 1)
            self.notice.setText("Порядок изменён только в выборе; Apple ещё не изменена.")
        except ValueError as exc:
            self.notice.setText(str(exc))

    def move_selected_image(self, offset):
        identifiers = self.selections.get(self.group_combo.currentData(), [])
        index = self.chosen_list.currentRow()
        if self.is_busy() or index < 0 or not 0 <= index + offset < len(identifiers):
            return
        identifiers[index], identifiers[index + offset] = identifiers[index + offset], identifiers[index]
        self.render_order()
        self.chosen_list.setCurrentRow(index + offset)

    def remove_selected_image(self):
        identifiers = self.selections.get(self.group_combo.currentData(), [])
        index = self.chosen_list.currentRow()
        if self.is_busy() or index < 0:
            return
        identifiers.pop(index)
        self.render_order()

    def selected_batches(self):
        group = self.group_combo.currentData()
        identifiers = self.selections.get(group, [])
        return {group: list(identifiers)} if identifiers else {}

    def selected_targets(self):
        return [self.targets_list.item(index).data(Qt.UserRole) for index in range(self.targets_list.count())
                if self.targets_list.item(index).checkState() == Qt.Checked
                and (self.mode == "version" or self.targets_list.item(index).data(Qt.UserRole)["parent_id"] == self.scope_combo.currentData())]

    def update_assign_enabled(self):
        self.assign_button.setEnabled(not self.is_busy() and bool(self.selected_batches()) and bool(self.selected_targets()))

    def assign_selected(self):
        if self.is_busy():
            return
        if self.prepare_operation is not None and not self.prepare_operation():
            return
        batches, targets = self.selected_batches(), self.selected_targets()
        if self.is_busy() or not batches or not targets:
            return
        details = "\n".join(f"{group}: {' → '.join(identifiers)}" for group, identifiers in batches.items())
        confirmation = (f"Заменить только выбранные группы у {len(targets)} существующих локалей?\n{details}\n"
                        "Библиотечные файлы и метаданные не меняются. Результат будет показан по каждой локали.")
        if self.cleanup_checkbox.isChecked():
            confirmation += "\nВключено удаление только старых INVALID/SPEC_MISMATCH размещений других групп."
        if QMessageBox.question(self, "Подтвердить назначение", confirmation, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.assignment_batches = {group: list(identifiers) for group, identifiers in batches.items()}
        self.assignment_target_ids = {target["id"] for target in targets}
        self.results_table.setRowCount(0)
        self.results_table.show()
        self.worker = LibraryAssignmentWorker(self.client_factory, targets, batches, self.cleanup_checkbox.isChecked(), self)
        self.worker.log_msg.connect(self.log_msg.emit)
        self.worker.completed.connect(self.show_results)
        self.worker.progress.connect(lambda done, total: self.notice.setText(f"Проверено локалей: {done}/{total}"))
        self.worker.finished.connect(lambda: self.set_busy(False))
        self.set_busy(True)
        self.notice.setText("Проверка версии, локалей, каталога и готовности изображений перед изменениями…")
        self.worker.start()

    def show_results(self, rows):
        labels = {"success": "Подтверждено Apple", "failed": "Ошибка до изменений", "manual_check": "Проверить вручную"}
        self.results_table.setRowCount(len(rows))
        for row, result in enumerate(rows):
            for column, text in enumerate((result["label"], labels[result["status"]], result["message"])):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.results_table.setItem(row, column, item)
        counts = {status: sum(row["status"] == status for row in rows) for status in labels}
        self.notice.setText(f"Итог по локалям: подтверждено: {counts['success']}; ошибок: {counts['failed']}; проверить вручную: {counts['manual_check']}.")
        self.log_msg.emit(self.notice.text())
        self._cleanup_import_after_assignment(rows)

    def reject(self):
        if self.is_busy():
            self.notice.setText("Дождитесь завершения проверки/назначения. Закрытие во время изменений заблокировано.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.is_busy():
            self.notice.setText("Дождитесь завершения проверки/назначения.")
            event.ignore()
        else:
            super().closeEvent(event)


class LibrarySelectionWidget(_LibrarySelectionPanel, QWidget):
    """The screenshot library and its upload controls embedded in the main tab."""
    log_msg = Signal(str)
    allow_import = True


class LibrarySelectionDialog(_LibrarySelectionPanel, QDialog):
    """Existing PPO chooser, sharing the same selection and assignment behavior."""
    log_msg = Signal(str)
