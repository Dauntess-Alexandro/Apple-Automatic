"""Build-upload tab, keeping all network work outside the GUI thread."""
from pathlib import Path
from dataclasses import replace
import webbrowser

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QInputDialog, QLineEdit, QProgressBar, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QTextEdit, QVBoxLayout, QWidget,
)

from github_build_upload import (
    AppleAppClient, AppleCredentials, BuildError, BuildPreferences, BuildService, ObservationStopped,
    parse_repository, repository_from_folder,
)

from local_project_setup import find_project_matches, install_project_files

STEP_NAMES = {
    'Set up job': 'Подготовка сервера', 'Complete job': 'Завершение',
    'Check required repository secrets': 'Проверка ключей Apple',
    'Set up Flutter': 'Подготовка Flutter',
    'Install release tools and dependencies': 'Установка зависимостей',
    'Run tests': 'Проверка проекта',
    'Set up signing and next build number': 'Подпись и номер билда',
    'Prepare IPA artifact': 'Подготовка файла билда',
    'Build IPA': 'Сборка приложения', 'Save signed IPA': 'Сохранение билда',
    'Save private debugging symbols': 'Сохранение символов отладки',
    'Upload to App Store Connect': 'Загрузка в App Store Connect',
}
STATE_NAMES = {'queued': 'В очереди', 'in_progress': 'Выполняется', 'completed': 'Завершено',
               'success': 'Готово', 'failure': 'Ошибка', 'cancelled': 'Отменено',
               'skipped': 'Пропущено', 'timed_out': 'Превышено время',
               'waiting': 'Ожидание', 'pending': 'Ожидание', 'action_required': 'Требуется действие'}


class BuildWorker(QThread):
    message = Signal(str)
    inspected = Signal(dict)
    project_choices = Signal(dict)
    prepared = Signal(dict)
    launched = Signal(dict)
    updated = Signal(dict)
    failed = Signal(str)

    def __init__(self, *, repository, credentials, config_dir, action, token='',
                 auto_push=False, flutter_version='3.47.0', run_id=None, projects_root='', manual_folder='', choice=None, lookup_all=False, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.credentials = credentials
        self.config_dir = config_dir
        self.action = action
        self.token = token
        self.auto_push = auto_push
        self.flutter_version = flutter_version
        self.run_id = run_id
        self.projects_root = projects_root
        self.manual_folder = manual_folder
        self.choice = choice
        self.lookup_all = lookup_all

    def pause(self, seconds=15):
        for _ in range(seconds * 4):
            if self.isInterruptionRequested():
                raise ObservationStopped('Наблюдение остановлено. Сборка продолжает работать в GitHub.')
            self.msleep(250)

    def run(self):
        try:
            if self.action == 'prepare':
                self.message.emit('Ищу приложение в Apple и проект на компьютере…')
                lookup = replace(self.credentials, app_id='') if self.lookup_all else self.credentials
                client = AppleAppClient(lookup, log=self.message.emit, stopped=self.isInterruptionRequested)
                apps = client.apple_apps()
                client.check_stop()
                choices = find_project_matches(apps, self.projects_root, self.manual_folder)
                self.project_choices.emit({'choices': choices, 'apps': apps, 'credential_identity': self.credentials})
                return
            if self.action == 'install':
                if self.isInterruptionRequested():
                    return
                result = install_project_files(self.choice, issuer=self.credentials.issuer, key_id=self.credentials.key_id)
                self.prepared.emit(dict(result, credential_identity=self.credentials))
                return
            service = BuildService(self.repository, self.credentials, self.config_dir, token=self.token,
                                   log=self.message.emit, stopped=self.isInterruptionRequested)
            if self.action in ('inspect', 'start'):
                self.credentials.validate()
                snapshot = service.inspect()
                self.inspected.emit(snapshot)
                if self.action == 'inspect':
                    return
                run = service.start(auto_push=self.auto_push, flutter_version=self.flutter_version, inspected=snapshot)
                self.run_id = run['id']
                # Save in this thread before emitting: closeEvent may be waiting
                # for us while the GUI cannot process queued signals.
                try:
                    preferences = BuildPreferences(Path(self.config_dir) / '.build_upload.json')
                    values = preferences.load(self.credentials.app_id)
                    values['last_run'] = run
                    values.setdefault('repository', self.repository)
                    preferences.save(self.credentials.app_id, values)
                except OSError:
                    self.message.emit('Не удалось сохранить номер сборки. Проверьте Actions перед повторным запуском.')
                # Persist the ID even when stop was requested during dispatch.
                self.launched.emit(run)
            errors = 0
            while True:
                try:
                    snapshot = service.observe(self.run_id)
                    errors = 0
                except ObservationStopped:
                    raise
                except BuildError:
                    errors += 1
                    if errors >= 3:
                        raise
                    self.message.emit('GitHub временно не отвечает. Повторяю проверку статуса…')
                    self.pause()
                    continue
                self.updated.emit(snapshot)
                if snapshot.get('status') == 'completed':
                    return
                self.pause()
        except ObservationStopped as exc:
            self.message.emit(str(exc))
        except BuildError as exc:
            self.failed.emit(str(exc))
        except Exception:
            # Do not expose exception bodies: remote or crypto errors can contain credentials.
            self.failed.emit('Не удалось завершить операцию. Проверьте настройки и откройте сборку в GitHub; ключи в журнал не выводятся.')
        finally:
            self.token = ''


class BuildUploadPanel(QScrollArea):
    open_api_requested = Signal()
    app_id_detected = Signal(str)

    def __init__(self, credentials_provider, config_dir, parent=None):
        super().__init__(parent)
        self.credentials_provider = credentials_provider
        self.config_dir = Path(config_dir)
        self.preferences = BuildPreferences(self.config_dir / '.build_upload.json')
        self.worker = None
        self._credential_identity = self.credentials_provider()
        self._applying_preparation = False
        self._lookup_all = False
        self._pending_lookup = None
        self._prepared_identity = None
        self.local_folder = ''
        self.auto_project_timer = QTimer(self)
        self.auto_project_timer.setSingleShot(True)
        self.auto_project_timer.setInterval(1200)
        self.auto_project_timer.timeout.connect(self.prepare_local_project)
        self.last_run = None
        self._loading_preferences = False
        self.current_app_id = None
        self.worker_app_id = None
        self.worker_repository = None
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel('Загрузка билда')
        title.setStyleSheet('font-size: 24px; font-weight: 700;')
        layout.addWidget(title)
        hint = QLabel('Найдите проект, подготовьте файлы IOS BUILD и запустите сборку для TestFlight.')
        hint.setProperty('role', 'hint')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        api_row = QHBoxLayout()
        self.credentials_label = QLabel()
        self.credentials_label.setWordWrap(True)
        api_row.addWidget(self.credentials_label, 1)
        self.api_button = QPushButton('Настройки API')
        self.api_button.setObjectName('utility_btn')
        self.api_button.clicked.connect(self.open_api_requested.emit)
        api_row.addWidget(self.api_button)
        layout.addLayout(api_row)

        project_box = QGroupBox('Проект')
        project_layout = QVBoxLayout(project_box)
        search_row = QHBoxLayout()
        self.projects_root_input = QLineEdit(str(Path.home() / 'Downloads/Projects'))
        self.projects_root_input.setPlaceholderText('Папка Projects с приложениями')
        search_row.addWidget(self.projects_root_input, 1)
        self.prepare_button = QPushButton('Найти и подготовить')
        self.prepare_button.setObjectName('utility_btn')
        self.prepare_button.clicked.connect(self.prepare_local_project)
        search_row.addWidget(self.prepare_button)
        project_layout.addWidget(QLabel('Папка для поиска проектов'))
        project_layout.addLayout(search_row)
        self.local_folder_label = QLabel('Проект найдётся по Bundle ID. Если совпадений нет — выберите папку вручную.')
        self.local_folder_label.setWordWrap(True)
        self.local_folder_label.setTextFormat(Qt.PlainText)
        project_layout.addWidget(self.local_folder_label)
        repo_row = QHBoxLayout()
        self.repository_input = QLineEdit()
        self.repository_input.setPlaceholderText('https://github.com/владелец/репозиторий')
        repo_row.addWidget(self.repository_input, 1)
        self.folder_button = QPushButton('Указать проект…')
        self.folder_button.setObjectName('utility_btn')
        self.folder_button.clicked.connect(self.select_folder)
        repo_row.addWidget(self.folder_button)
        project_layout.addLayout(repo_row)
        source_hint = QLabel('Собирается последняя версия основной ветки в GitHub. Изменения на компьютере сначала отправьте через GitHub Desktop.')
        source_hint.setProperty('role', 'hint')
        source_hint.setWordWrap(True)
        project_layout.addWidget(source_hint)
        self.auto_push_checkbox = QCheckBox('После запуска автоматически собирать новые изменения основной ветки')
        project_layout.addWidget(self.auto_push_checkbox)
        self.project_label = QLabel('Папка приложения, Bundle ID и ветка определятся автоматически.')
        self.project_label.setWordWrap(True)
        self.project_label.setTextFormat(Qt.PlainText)
        project_layout.addWidget(self.project_label)
        layout.addWidget(project_box)

        self.advanced_button = QPushButton('Дополнительные настройки ▸')
        self.advanced_button.setObjectName('utility_btn')
        self.advanced_button.setCheckable(True)
        layout.addWidget(self.advanced_button, alignment=Qt.AlignLeft)
        self.advanced_box = QGroupBox('GitHub и Flutter')
        advanced = QFormLayout(self.advanced_box)
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setPlaceholderText('Не нужен, если выполнен вход в GitHub Desktop')
        advanced.addRow('GitHub-токен', self.token_input)
        self.flutter_input = QLineEdit('3.47.0')
        advanced.addRow('Версия Flutter', self.flutter_input)
        permissions = QLabel('Токен нужен только при отсутствии доступа через GitHub Desktop. Права: Contents, Workflows, Actions и Secrets — запись. Токен используется только в текущем сеансе.')
        permissions.setWordWrap(True)
        permissions.setProperty('role', 'hint')
        advanced.addRow(permissions)
        self.advanced_box.hide()
        self.advanced_button.toggled.connect(self.advanced_box.setVisible)
        layout.addWidget(self.advanced_box)

        actions = QHBoxLayout()
        self.inspect_button = QPushButton('Проверить проект')
        self.inspect_button.setObjectName('utility_btn')
        self.inspect_button.clicked.connect(self.inspect_project)
        actions.addWidget(self.inspect_button)
        self.start_button = QPushButton('Собрать и загрузить')
        self.start_button.setObjectName('start_btn')
        self.start_button.setMinimumHeight(46)
        self.start_button.clicked.connect(self.start_build)
        actions.addWidget(self.start_button, 1)
        layout.addLayout(actions)
        self.status_label = QLabel('Готово к запуску. Ключи Apple берутся из «Настройки API».')
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)

        run_actions = QHBoxLayout()
        self.github_button = QPushButton('Открыть сборку в GitHub')
        self.github_button.setObjectName('utility_btn')
        self.github_button.clicked.connect(self.open_github)
        self.github_button.setEnabled(False)
        run_actions.addWidget(self.github_button)
        self.apple_button = QPushButton('Открыть TestFlight')
        self.apple_button.setObjectName('utility_btn')
        self.apple_button.clicked.connect(self.open_testflight)
        run_actions.addWidget(self.apple_button)
        self.resume_button = QPushButton('Продолжить наблюдение')
        self.resume_button.setObjectName('utility_btn')
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self.resume_observation)
        run_actions.addWidget(self.resume_button)
        self.stop_button = QPushButton('Остановить наблюдение')
        self.stop_button.setObjectName('utility_btn')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_observation)
        run_actions.addWidget(self.stop_button)
        layout.addLayout(run_actions)

        self.steps_table = QTableWidget(0, 2)
        self.steps_table.setHorizontalHeaderLabels(['Этап', 'Результат'])
        self.steps_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.steps_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.steps_table.verticalHeader().hide()
        self.steps_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.steps_table.setMinimumHeight(210)
        layout.addWidget(self.steps_table)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(140)
        self.log_area.setPlaceholderText('Ход настройки и сборки')
        layout.addWidget(self.log_area)
        footer = QLabel('После загрузки Apple обрабатывает билд. Отправка на проверку App Store выполняется отдельно. Время сборки зависит от очереди GitHub и проекта.')
        footer.setWordWrap(True)
        footer.setProperty('role', 'hint')
        layout.addWidget(footer)
        layout.addStretch()
        self.refresh_credentials()
        self.projects_root_input.editingFinished.connect(self.save_options_safely)
        self.repository_input.editingFinished.connect(self.save_options_safely)
        self.flutter_input.editingFinished.connect(self.save_options_safely)
        self.auto_push_checkbox.toggled.connect(self.save_options_safely)

    def refresh_credentials(self, *_):
        credentials = self.credentials_provider()
        previous = self._credential_identity
        self._credential_identity = credentials
        if credentials != previous and not self._applying_preparation:
            self.auto_project_timer.stop()
            changed_keys = (credentials.issuer, credentials.key_id, credentials.p8_path) != (previous.issuer, previous.key_id, previous.p8_path)
            self._lookup_all = changed_keys or (self._lookup_all and credentials.app_id == previous.app_id)
            if credentials.app_id != previous.app_id and not changed_keys:
                self._lookup_all = False
            if all((credentials.issuer, credentials.key_id, credentials.p8_path)) and Path(credentials.p8_path).expanduser().is_file():
                self.auto_project_timer.start()
        app_id = credentials.app_id
        self.credentials_label.setText(f'Из «Настройки API»: App ID {app_id or "не выбран"} · Key ID {credentials.key_id or "не указан"}')
        if self.is_busy() or app_id == self.current_app_id:
            return
        if self.current_app_id is not None:
            self.save_options_safely()
        self.current_app_id = app_id
        self._loading_preferences = True
        values = self.preferences.load(app_id)
        self.repository_input.setText(values.get('repository', ''))
        self.local_folder = values.get('local_folder', '')
        self.projects_root_input.setText(values.get('projects_root') or str(Path.home() / 'Downloads/Projects'))
        self.local_folder_label.setText(self.local_folder or 'Проект найдётся по Bundle ID. Можно указать папку вручную.')
        self.auto_push_checkbox.setChecked(bool(values.get('auto_push', False)))
        self.flutter_input.setText(values.get('flutter_version', '3.47.0'))
        self.last_run = values.get('last_run') or None
        self._loading_preferences = False
        self.resume_button.setEnabled(bool(self.last_run))
        self.github_button.setEnabled(bool(self.last_run and self.last_run.get('html_url')))
        self.project_label.setText('Папка приложения, Bundle ID и ветка определятся автоматически.')
        self.steps_table.setRowCount(0)
        self.progress.hide()
        self.status_label.setText('Есть сохранённая сборка — можно продолжить наблюдение.' if self.last_run else 'Готово к запуску. Ключи Apple берутся из «Настройки API».')

    def is_busy(self):
        return self.worker is not None and self.worker.isRunning()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Папка приложения или его репозитория', self.projects_root_input.text())
        if folder:
            self.prepare_local_project(manual_folder=folder)

    def prepare_local_project(self, checked=False, *, manual_folder=''):
        self.auto_project_timer.stop()
        if self.is_busy():
            self.auto_project_timer.start()
            return
        credentials = self.credentials_provider()
        try:
            credentials.validate(require_app_id=False)
        except BuildError as exc:
            self.show_error(str(exc))
            return
        self._pending_lookup = None
        self.set_busy(True)
        worker = BuildWorker(repository='', credentials=credentials, config_dir=self.config_dir,
                             action='prepare', projects_root=self.projects_root_input.text().strip(),
                             manual_folder=manual_folder, lookup_all=self._lookup_all, parent=self)
        self.worker = worker
        worker.message.connect(self.add_message)
        worker.project_choices.connect(self.choose_local_project)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        worker.start()

    def choose_local_project(self, result):
        # Resolve choices after the lookup thread finishes; modal dialogs run a nested event loop.
        if result['credential_identity'] == self.credentials_provider():
            self._pending_lookup = result

    def _resolve_local_project(self, result):
        if result['credential_identity'] != self.credentials_provider():
            return
        choices = result['choices']
        apps = result['apps']
        if not choices:
            if len(apps) == 1:
                self._apply_detected_app(apps[0])
                self.show_error(f'В Apple у «{apps[0]["app_name"]}» Bundle ID {apps[0]["bundle_id"]}, а в Projects проекта '
                                'с таким Bundle ID нет. Проверьте PRODUCT_BUNDLE_IDENTIFIER в Xcode или нажмите «Указать проект…».')
                return
            self.show_error('Совпадений по Bundle ID в Projects нет. Нажмите «Указать проект…». Для поиска нужны исходники iOS с Bundle ID в Xcode.')
            return
        choice = choices[0]
        if len(choices) > 1:
            labels = [f'{c["app_name"]} · {c["bundle_id"]} · {c["local_folder"]}/{c["app_directory"]}' for c in choices]
            label, accepted = QInputDialog.getItem(self, 'Выберите проект', 'Найдено несколько совпадений:', labels, 0, False)
            if not accepted:
                self.add_message('Выбор проекта отменён. Файлы не изменены.')
                return
            choice = choices[labels.index(label)]
        self._install_local_choice(choice, result['credential_identity'])

    def _install_local_choice(self, choice, identity):
        if identity != self.credentials_provider() or self.is_busy():
            return
        self.set_busy(True)
        worker = BuildWorker(repository='', credentials=identity, config_dir=self.config_dir,
                             action='install', choice=choice, parent=self)
        self.worker = worker
        worker.prepared.connect(self.apply_prepared_project)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        worker.start()

    def _apply_detected_app(self, result):
        self._applying_preparation = True
        self.auto_project_timer.stop()
        try:
            self.app_id_detected.emit(str(result['app_id']))
            self._credential_identity = self.credentials_provider()
            self._lookup_all = False
        finally:
            self._applying_preparation = False

    def apply_prepared_project(self, result):
        if result.get('credential_identity') != self.credentials_provider():
            return
        self._apply_detected_app(result)
        if self.current_app_id != str(result['app_id']):
            values = self.preferences.load(str(result['app_id']))
            self.last_run = values.get('last_run') or None
            self._loading_preferences = True
            self.auto_push_checkbox.setChecked(bool(values.get('auto_push', False)))
            self.flutter_input.setText(values.get('flutter_version', '3.47.0'))
            self._loading_preferences = False
        self.current_app_id = str(result['app_id'])
        self.local_folder = result['local_folder']
        self.local_folder_label.setText(self.local_folder)
        self.repository_input.setText(result.get('repository', ''))
        self.project_label.setText(f'{result["app_name"]} · App ID {result["app_id"]} · {result["bundle_id"]}\n'
                                   f'Папка приложения: {result["app_directory"]} · {result["app_type"]}')
        self.save_options(result['app_id'])
        self.add_message('Файлы IOS BUILD и инструкция подготовлены. Отправьте их с исходниками через GitHub Desktop. Сборка запускается кнопкой «Собрать и загрузить».')

    def options(self):
        return {'local_folder': self.local_folder, 'projects_root': self.projects_root_input.text().strip(),
                'repository': self.repository_input.text().strip(),
                'auto_push': self.auto_push_checkbox.isChecked(),
                'flutter_version': self.flutter_input.text().strip(), 'last_run': self.last_run or {}}

    def save_options(self, app_id=None):
        self.preferences.save(app_id or self.current_app_id, self.options())

    def save_options_safely(self, *_):
        if self._loading_preferences or self.is_busy():
            return
        try:
            self.save_options()
        except OSError:
            self.show_error('Не удалось сохранить настройки вкладки.')

    def inspect_project(self):
        self.launch('inspect')

    def start_build(self):
        self.launch('start')

    def resume_observation(self):
        if self.last_run:
            self.launch('observe', self.last_run)

    def launch(self, action, run=None):
        if self.is_busy():
            return
        try:
            credentials = self.credentials_provider()
            repository = parse_repository(run['repository'] if run else self.repository_input.text())
            if run and str(run.get('app_id', credentials.app_id)) != credentials.app_id:
                raise BuildError('Сохранённая сборка относится к другому App ID.')
            self.save_options(credentials.app_id)
        except (BuildError, OSError) as exc:
            self.show_error(str(exc) if isinstance(exc, BuildError) else 'Не удалось сохранить настройки вкладки.')
            return
        self.worker_app_id = credentials.app_id
        self.worker_repository = repository
        self.set_busy(True)
        self.log_area.clear()
        self.steps_table.setRowCount(0)
        self.status_label.setText('Подключаюсь к GitHub…')
        worker = BuildWorker(repository=repository, credentials=credentials, config_dir=self.config_dir,
                             action=action, token=self.token_input.text().strip(),
                             auto_push=self.auto_push_checkbox.isChecked(), flutter_version=self.flutter_input.text().strip(),
                             run_id=run['id'] if run else None, parent=self)
        self.worker = worker
        worker.message.connect(self.add_message)
        worker.inspected.connect(self.show_project)
        worker.launched.connect(self.remember_run)
        worker.updated.connect(self.apply_run)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        worker.start()

    def set_busy(self, busy):
        for control in (self.projects_root_input, self.prepare_button, self.repository_input, self.folder_button, self.auto_push_checkbox,
                        self.flutter_input, self.token_input, self.start_button, self.inspect_button, self.api_button):
            control.setEnabled(not busy)
        self.resume_button.setEnabled(not busy and bool(self.last_run))
        self.stop_button.setEnabled(busy and bool(self.last_run))
        if busy:
            self.progress.setRange(0, 0)
            self.progress.show()
        else:
            self.progress.hide()
            self.stop_button.setEnabled(False)

    def add_message(self, message):
        # Plain text only: remote names or errors must not become rich text.
        self.log_area.moveCursor(self.log_area.textCursor().MoveOperation.End)
        self.log_area.insertPlainText(message + '\n')
        self.status_label.setText(message)

    def show_project(self, snapshot):
        self.project_label.setText(f'{snapshot["app_name"]} · {snapshot["bundle_id"]}\n'
                                   f'Ветка: {snapshot["branch"]} · Папка: {snapshot["app_directory"]} · GitHub: {snapshot["github_user"]}')
        self.add_message('Проект и приложение Apple совпадают. Можно запускать сборку.')

    def remember_run(self, run):
        self.last_run = run
        self.github_button.setEnabled(bool(run.get('html_url')))
        self.stop_button.setEnabled(True)
        try:
            self.save_options(run.get('app_id') or self.worker_app_id)
        except OSError:
            self.add_message('Не удалось сохранить номер сборки. Ссылка доступна по кнопке GitHub.')
        self.status_label.setText(f'Сборка #{run["id"]} запущена. Ожидаю сервер GitHub…')

    def apply_run(self, run):
        steps = []
        for job in run.get('jobs', []):
            job_steps = job.get('steps', [])
            if not job_steps:
                steps.append((job.get('name', 'Сборка'), job.get('conclusion') or job.get('status', 'queued')))
            for step in job_steps:
                name = step.get('name', 'Этап')
                if name.startswith('Post '):
                    name = 'Очистка после сборки'
                elif name.startswith('Run actions/checkout@'):
                    name = 'Получение исходников'
                elif name.startswith('Run actions/setup-python@'):
                    name = 'Подготовка инструментов'
                steps.append((STEP_NAMES.get(name, name), step.get('conclusion') or step.get('status', 'queued')))
        self.steps_table.setRowCount(len(steps))
        for row, (name, status) in enumerate(steps):
            self.steps_table.setItem(row, 0, QTableWidgetItem(name))
            self.steps_table.setItem(row, 1, QTableWidgetItem(STATE_NAMES.get(status, status)))
        if run.get('status') == 'completed':
            conclusion = run.get('conclusion')
            if conclusion == 'success':
                self.status_label.setText('Билд загружен в App Store Connect. Apple может ещё обрабатывать его — проверьте TestFlight.')
            else:
                failed = next((name for name, state in steps if state in ('failure', 'timed_out', 'cancelled')), '')
                self.status_label.setText(f'Сборка завершилась: {STATE_NAMES.get(conclusion, conclusion or "ошибка")}. {failed}. Подробности — в GitHub.')
        else:
            active = next((name for name, state in steps if state == 'in_progress'), '')
            self.status_label.setText(active or STATE_NAMES.get(run.get('status'), 'Ожидаю сервер GitHub…'))

    def show_error(self, message):
        self.status_label.setText(message)
        self.log_area.insertPlainText(message + '\n')

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        self.set_busy(False)
        if worker:
            worker.deleteLater()
        # Defer installation until lookup's worker has completely finished.
        pending, self._pending_lookup = self._pending_lookup, None
        self.refresh_credentials()
        if pending:
            QTimer.singleShot(0, lambda: self._resolve_local_project(pending))

    def stop_observation(self):
        if self.is_busy():
            self.worker.requestInterruption()
            self.stop_button.setEnabled(False)
            self.status_label.setText('Останавливаю наблюдение. Сборка продолжит работать в GitHub.')

    def prepare_close(self):
        self.auto_project_timer.stop()
        if not self.is_busy():
            self.save_options_safely()
            return True
        self.worker.requestInterruption()
        if not self.worker.wait(1000):
            self.status_label.setText('Завершаю наблюдение. Закройте окно через несколько секунд; сборка продолжит работать в GitHub.')
            return False
        return True

    def open_github(self):
        if not self.last_run:
            return
        # Construct from validated repository and numeric ID, never trust remote links.
        try:
            repository = parse_repository(self.last_run['repository'])
            run_id = int(self.last_run['id'])
        except (BuildError, KeyError, TypeError, ValueError):
            return
        webbrowser.open(f'https://github.com/{repository}/actions/runs/{run_id}')

    def open_testflight(self):
        app_id = self.credentials_provider().app_id
        if app_id.isdigit():
            webbrowser.open(f'https://appstoreconnect.apple.com/apps/{app_id}/testflight/ios')
