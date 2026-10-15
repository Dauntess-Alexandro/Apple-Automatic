"""GitHub Actions setup and uploads; no Qt or application-global credentials."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from nacl.public import PublicKey, SealedBox

WORKFLOW_PATH = '.github/workflows/apple-automatic-ios.yml'
WORKFLOW_NAME = 'apple-automatic-ios.yml'
WORKFLOW_MARKER = '# Managed by Apple Automatic — Flutter iOS upload v1'
GITHUB_API = 'https://api.github.com'


class BuildError(Exception):
    """A safe, user-facing error (never includes private credentials)."""


class ObservationStopped(BuildError):
    pass


def parse_repository(value: str) -> str:
    value = value.strip()
    value = re.sub(r'^(https://github\.com/|git@github\.com:)', '', value)
    value = re.sub(r'\.git/?$', '', value).rstrip('/')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise BuildError('Укажите репозиторий GitHub: владелец/название или его ссылку.')
    if any(part in ('.', '..') for part in value.split('/')):
        raise BuildError('Некорректный адрес репозитория.')
    return value


def repository_from_folder(folder: str) -> str:
    try:
        result = subprocess.run(['git', '-C', folder, 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=10, check=True)
        return parse_repository(result.stdout)
    except (OSError, subprocess.SubprocessError):
        raise BuildError('В этой папке нет GitHub-репозитория с адресом origin.') from None


def resolve_github_token(typed: str = '') -> str:
    if typed.strip():
        return typed.strip()
    if os.getenv('GITHUB_TOKEN', '').strip():
        return os.environ['GITHUB_TOKEN'].strip()
    commands = []
    if sys.platform == 'darwin':
        commands.append(['security', 'find-generic-password', '-s', 'GitHub - https://api.github.com', '-w'])
    commands.append(['gh', 'auth', 'token', '--hostname', 'github.com'])
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=55)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
    raise BuildError('Войдите в GitHub Desktop или укажите GitHub-токен в дополнительных настройках вкладки.')


def encrypt_secret(public_key: str, value: str) -> str:
    box = SealedBox(PublicKey(base64.b64decode(public_key)))
    return base64.b64encode(box.encrypt(value.encode('utf-8'))).decode('ascii')


@dataclass(frozen=True)
class AppleCredentials:
    issuer: str
    key_id: str
    app_id: str
    p8_path: str

    def validate(self, require_app_id=True):
        if not all((self.issuer, self.key_id, self.p8_path)) or (require_app_id and not self.app_id):
            raise BuildError('Заполните Issuer ID, Key ID, App ID и файл .p8 в «Настройки API».')
        if self.app_id and not self.app_id.isdigit():
            raise BuildError('App ID должен быть числовым идентификатором из App Store Connect.')
        if not Path(self.p8_path).expanduser().is_file():
            raise BuildError('Файл .p8 не найден. Выберите его в «Настройки API».')


class BuildPreferences:
    ALLOWED = {'repository', 'auto_push', 'flutter_version', 'last_run', 'local_folder', 'projects_root'}

    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self):
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError):
            return {}

    def load(self, app_id: str) -> dict:
        item = self._read().get(app_id, {})
        return {k: v for k, v in item.items() if k in self.ALLOWED} if isinstance(item, dict) else {}

    def save(self, app_id: str, values: dict):
        if not app_id:
            return
        data = self._read()
        clean = {k: v for k, v in values.items() if k in self.ALLOWED}
        if 'last_run' in clean:
            run = clean['last_run']
            clean['last_run'] = {k: run[k] for k in ('id', 'html_url', 'repository', 'app_id') if k in run}
        data[app_id] = clean
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)
        self.path.chmod(0o600)


class GitHubClient:
    def __init__(self, token: str, session=None):
        self.token = token
        self.session = session or requests.Session()

    def request(self, method: str, path: str, *, allow_missing=False, **kwargs):
        try:
            response = self.session.request(method, GITHUB_API + path,
                headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                         'X-GitHub-Api-Version': '2026-03-10'}, timeout=(10, 40), **kwargs)
        except requests.RequestException:
            raise BuildError('Не удалось связаться с GitHub. Проверьте интернет; при запуске не повторяйте запрос, пока не проверите список сборок.') from None
        if response.status_code == 404 and allow_missing:
            return None
        if response.status_code >= 400:
            messages = {401: 'Вход в GitHub истёк. Войдите снова в GitHub Desktop или обновите токен.',
                        403: 'GitHub отказал в доступе. Нужны права Contents, Workflows, Actions и Secrets: запись.',
                        404: 'Репозиторий или сборка не найдены. Проверьте ссылку и аккаунт GitHub Desktop.',
                        409: 'Ветка изменилась во время настройки. Запустите настройку ещё раз.',
                        422: 'GitHub не принял настройку. Проверьте права на workflows и правила выбранной ветки.'}
            raise BuildError(messages.get(response.status_code, f'GitHub вернул ошибку {response.status_code}. Откройте репозиторий для проверки.'))
        if response.status_code == 204:
            return {}
        return response.json()


def discover_flutter_project(files: dict[str, str], bundle_id: str) -> str:
    candidates = []
    available = set()
    for path in files:
        if not (path == 'pubspec.yaml' or path.endswith('/pubspec.yaml')):
            continue
        root = path[:-len('pubspec.yaml')].rstrip('/')
        pbx = (root + '/' if root else '') + 'ios/Runner.xcodeproj/project.pbxproj'
        if pbx not in files:
            continue
        bundles = set(re.findall(r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*"?([A-Za-z0-9_.-]+)"?\s*;', files[pbx]))
        bundles = {item for item in bundles if not item.endswith(('.RunnerTests', '.RunnerUITests'))}
        available.update(bundles)
        if bundles == {bundle_id}:
            candidates.append(root or '.')
    if len(candidates) > 1:
        raise BuildError('В репозитории несколько Flutter-приложений с этим Bundle ID. Разделите их на отдельные репозитории.')
    if not candidates:
        if available:
            raise BuildError('Bundle ID проекта не совпадает с приложением из «Настройки API». Проверьте App ID и репозиторий.')
        raise BuildError('Не найден Flutter-проект iOS с явным Bundle ID в ios/Runner.xcodeproj. Сначала добавьте исходники в GitHub.')
    return candidates[0]


def render_workflow(app_directory: str, bundle_id: str, app_id: str, branch: str,
                    auto_push=False, flutter_version='3.47.0', *, secret_prefix='', repository_identity='') -> str:
    if (not re.fullmatch(r'[A-Za-z0-9_./ -]+', app_directory)
            or any(part == '..' for part in app_directory.split('/')) or app_directory.startswith('/')
            or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', bundle_id)
            or not app_id.isdigit() or not re.fullmatch(r'[A-Za-z0-9_./-]+', branch)
            or not re.fullmatch(r'\d+\.\d+\.\d+', flutter_version)):
        raise BuildError('Небезопасный или неподдерживаемый путь, Bundle ID, ветка или версия Flutter.')
    trigger = '  workflow_dispatch:\n    inputs:\n      request_id:\n        description: Build request\n        type: string\n        required: false\n      source_sha:\n        description: Inspected source commit\n        type: string\n        required: false\n'
    if auto_push:
        trigger += f'  push:\n    branches: [{json.dumps(branch)}]\n    paths-ignore: [{json.dumps(WORKFLOW_PATH)}]\n'
    header = f'''{WORKFLOW_MARKER}
name: IOS BUILD
run-name: IOS BUILD - ${{{{ inputs.request_id || github.sha }}}}
on:
{trigger}
permissions:
  contents: read
  actions: read
concurrency:
  group: ios-app-store-connect
  cancel-in-progress: false
env:
  APP_DIRECTORY: {json.dumps(app_directory)}
  BUNDLE_ID: {json.dumps(bundle_id)}
  APP_STORE_APPLE_ID: {json.dumps(app_id)}
'''
    if repository_identity:
        header = header.replace(WORKFLOW_MARKER + '\n', WORKFLOW_MARKER + '\n# Apple identity: ' + repository_identity + '\n', 1)
    jobs = _WORKFLOW_JOBS.replace('__FLUTTER_VERSION__', flutter_version)
    if secret_prefix:
        if not re.fullmatch(r'[A-Z0-9_]+', secret_prefix):
            raise BuildError('Некорректное имя защищённых ключей.')
        jobs = jobs.replace('secrets.APP_STORE_CONNECT_', 'secrets.' + secret_prefix + 'APP_STORE_CONNECT_')
    return header + jobs


class AppleAppClient:
    def __init__(self, credentials, *, log=None, stopped=None):
        self.credentials = credentials
        self.log = log or (lambda message: None)
        self.stopped = stopped or (lambda: False)
        self._apple_key = None

    def check_stop(self):
        if self.stopped():
            raise ObservationStopped('Наблюдение остановлено. Запущенная сборка продолжает работать в GitHub.')

    def read_apple_key(self):
        if self._apple_key is None:
            self.credentials.validate(require_app_id=False)
            try:
                raw = Path(self.credentials.p8_path).expanduser().read_text(encoding='utf-8')
                parsed = serialization.load_pem_private_key(raw.encode(), password=None)
                if not isinstance(parsed, ec.EllipticCurvePrivateKey) or not isinstance(parsed.curve, ec.SECP256R1):
                    raise ValueError('Expected Apple EC key')
                self._apple_key = raw
            except (OSError, ValueError, TypeError):
                raise BuildError('Файл .p8 повреждён или не является ключом App Store Connect.') from None
        return self._apple_key

    def apple_request(self, url):
        key = self.read_apple_key()
        now = int(time.time())
        try:
            token = jwt.encode({'iss': self.credentials.issuer, 'iat': now, 'exp': now + 600, 'aud': 'appstoreconnect-v1'},
                               key, algorithm='ES256', headers={'kid': self.credentials.key_id, 'typ': 'JWT'})
        except (ValueError, TypeError):
            raise BuildError('Не удалось подписать запрос Apple. Проверьте файл .p8 и Key ID.') from None
        for attempt in range(3):
            self.check_stop()
            try:
                response = requests.get(url,
                                        headers={'Authorization': 'Bearer ' + token}, timeout=(10, 40))
            except requests.RequestException:
                if attempt == 2:
                    raise BuildError('Не удалось проверить приложение в App Store Connect. Проверьте интернет и повторите запуск.') from None
            else:
                if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                    break
            self.log('Apple временно не отвечает. Повторяю проверку подключения…')
            time.sleep(2 ** attempt)
        if response.status_code != 200:
            raise BuildError(f'Apple не подтвердил доступ к приложению (HTTP {response.status_code}). Проверьте Issuer ID, Key ID, App ID и права ключа.')
        return response.json()

    @staticmethod
    def app_information(app):
        return {'app_id': str(app['id']), 'app_name': app['attributes']['name'], 'bundle_id': app['attributes']['bundleId']}

    def apple_app(self):
        if not self.credentials.app_id.isdigit():
            raise BuildError('App ID должен быть числовым идентификатором из App Store Connect.')
        return self.app_information(self.apple_request('https://api.appstoreconnect.apple.com/v1/apps/' + self.credentials.app_id)['data'])

    def apple_apps(self):
        """List apps visible to these keys, without requiring a GitHub login."""
        self.credentials.validate(require_app_id=False)
        if self.credentials.app_id:
            return [self.apple_app()]
        url = 'https://api.appstoreconnect.apple.com/v1/apps?limit=200'
        result, visited = [], set()
        while url:
            if not url.startswith('https://api.appstoreconnect.apple.com/v1/apps?') or url in visited:
                raise BuildError('Apple вернул некорректную страницу списка приложений.')
            visited.add(url)
            page = self.apple_request(url)
            result.extend(self.app_information(app) for app in page.get('data', []))
            url = page.get('links', {}).get('next')
        if not result:
            raise BuildError('У этого ключа Apple нет доступных приложений.')
        return result


class BuildService(AppleAppClient):
    def __init__(self, repository: str, credentials: AppleCredentials, config_dir: Path,
                 *, token='', log=None, stopped=None):
        self.repository = parse_repository(repository)
        self.credentials = credentials
        self.config_dir = Path(config_dir)
        self.log = log or (lambda message: None)
        self.stopped = stopped or (lambda: False)
        self.github = GitHubClient(resolve_github_token(token))
        self.base = '/repos/' + self.repository
        self._apple_key = None

    def remote_text(self, path, ref):
        item = self.github.request('GET', self.base + '/contents/' + quote(path, safe='/'), params={'ref': ref})
        try:
            return base64.b64decode(item['content']).decode('utf-8')
        except (KeyError, ValueError, UnicodeError):
            raise BuildError('GitHub не вернул содержимое файла проекта. Проверьте размер и доступ.') from None

    def inspect(self):
        self.log('Проверяю аккаунт GitHub и доступ к приложению Apple…')
        self.check_stop()
        user = self.github.request('GET', '/user')
        repo = self.github.request('GET', self.base)
        if not repo.get('permissions', {}).get('push'):
            raise BuildError('У выбранного аккаунта GitHub нет прав записи в репозиторий.')
        if repo.get('archived'):
            raise BuildError('Репозиторий архивирован. Сначала снимите архив в GitHub.')
        branch = repo['default_branch']
        app = self.apple_app()
        self.check_stop()
        branch_data = self.github.request('GET', self.base + '/branches/' + quote(branch, safe=''))
        head_sha = branch_data['commit']['sha']
        tree = self.github.request('GET', self.base + '/git/trees/' + head_sha, params={'recursive': '1'})
        if tree.get('truncated'):
            raise BuildError('Репозиторий слишком большой для автоматического поиска проекта.')
        paths = {item['path'] for item in tree.get('tree', []) if item['type'] == 'blob'}
        from local_project_setup import BUNDLE_PATTERN, IGNORED
        candidates, flutter_projects = [], set()
        pubspecs = sorted(p for p in paths if (p == 'pubspec.yaml' or p.endswith('/pubspec.yaml'))
                          and not any(part in IGNORED for part in Path(p).parts))
        for pubspec in pubspecs:
            self.check_stop()
            root = pubspec[:-len('pubspec.yaml')]
            pbx = root + 'ios/Runner.xcodeproj/project.pbxproj'
            if pbx in paths:
                flutter_projects.add(pbx)
                text = self.remote_text(pbx, head_sha)
                bundles = {item for item in re.findall(BUNDLE_PATTERN, text)
                           if not item.endswith(('.RunnerTests', '.RunnerUITests'))}
                if app['bundle_id'] in bundles:
                    if bundles != {app['bundle_id']}:
                        raise BuildError('Flutter-проект содержит несколько Bundle ID. Выберите конфигурацию перед сборкой.')
                    candidates.append((root.rstrip('/') or '.', 'flutter', ''))
        # Compare native and Flutter candidates together, including mixed repositories.
        for pbx in sorted(p for p in paths if p.endswith('.xcodeproj/project.pbxproj')):
            if pbx in flutter_projects or any(part in IGNORED for part in Path(pbx).parts):
                continue
            self.check_stop()
            if app['bundle_id'] not in re.findall(BUNDLE_PATTERN, self.remote_text(pbx, head_sha)):
                continue
            project = Path(pbx).parent
            scheme_paths = [p for p in paths if p.endswith('.xcscheme')
                            and Path(p).parent.name == 'xcschemes'
                            and Path(p).parent.parent.name == 'xcshareddata'
                            and Path(p).parent.parent.parent.parent == project.parent]
            schemes = {Path(p).stem for p in scheme_paths}
            if len(schemes) != 1:
                raise BuildError('Для нативного проекта нужна одна общая схема Xcode. Сохраните shared scheme в репозитории.')
            candidates.append((project.parent.as_posix(), 'xcode', next(iter(schemes))))
        if len(candidates) != 1:
            raise BuildError('Не найден единственный iOS-проект с Bundle ID выбранного приложения. Проверьте исходники и App ID.')
        directory, kind, scheme = candidates[0]
        return dict(app, repository=self.repository, branch=branch, head_sha=head_sha,
                    app_directory=directory, app_type=kind, xcode_scheme=scheme, github_user=user.get('login', ''),
                    has_tests=any(p.startswith(('' if directory == '.' else directory + '/') + 'test/') for p in paths))

    def certificate_key(self):
        identity = hashlib.sha256((self.credentials.issuer + '/' + self.repository).encode()).hexdigest()[:24]
        folder = self.config_dir / '.build-signing'
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / (identity + '.pem')
        if not path.exists():
            private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            value = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()).decode()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(value)
        path.chmod(0o600)
        return path.read_text()

    def repository_identity(self):
        return hashlib.sha256((self.credentials.issuer + '/' + self.credentials.app_id).encode()).hexdigest()

    def secret_prefix(self):
        identity = self.credentials.issuer + '/' + self.credentials.app_id + '/' + self.credentials.key_id
        return 'APPLE_AUTOMATIC_' + hashlib.sha256(identity.encode()).hexdigest()[:16].upper() + '_'

    def check_workflow_owner(self, text):
        if not text.startswith(WORKFLOW_MARKER + '\n'):
            raise BuildError('По адресу Apple Automatic уже находится чужой workflow. Переименуйте его в GitHub.')
        match = re.search(r'^# Apple identity: (.+)$', text, re.M)
        if not match or match.group(1) != self.repository_identity():
            raise BuildError('Этот репозиторий привязан к другому приложению или аккаунту Apple. Используйте отдельный репозиторий для каждого приложения.')

    def configure_secrets(self):
        self.check_stop()
        self.log('Подключаю ключи Apple к защищённым секретам GitHub…')
        public = self.github.request('GET', self.base + '/actions/secrets/public-key')
        existing = self.github.request('GET', self.base + '/actions/secrets/CERTIFICATE_PRIVATE_KEY', allow_missing=True)
        prefix = self.secret_prefix()
        secrets = {prefix + 'APP_STORE_CONNECT_ISSUER_ID': self.credentials.issuer,
                   prefix + 'APP_STORE_CONNECT_KEY_IDENTIFIER': self.credentials.key_id,
                   prefix + 'APP_STORE_CONNECT_PRIVATE_KEY': self.read_apple_key()}
        if existing is None:
            secrets['CERTIFICATE_PRIVATE_KEY'] = self.certificate_key()
        for name, value in secrets.items():
            self.check_stop()
            self.github.request('PUT', self.base + '/actions/secrets/' + name,
                                json={'key_id': public['key_id'], 'encrypted_value': encrypt_secret(public['key'], value)})

    def install_workflow(self, branch, text):
        self.check_stop()
        path = self.base + '/contents/' + WORKFLOW_PATH
        current = self.github.request('GET', path, params={'ref': branch}, allow_missing=True)
        if current:
            try:
                old = base64.b64decode(current['content']).decode('utf-8')
            except (KeyError, ValueError, UnicodeError):
                raise BuildError('Не удалось проверить существующий файл сборки.') from None
            self.check_workflow_owner(old)
            if old == text:
                return
        self.log('Настраиваю сборку в репозитории…')
        body = {'message': 'iOS build ' + uuid.uuid4().hex[:8], 'branch': branch,
                'content': base64.b64encode(text.encode()).decode()}
        if current:
            body['sha'] = current['sha']
        self.github.request('PUT', path, json=body)

    def dispatch(self, branch, source_sha=None):
        self.check_stop()
        request_id = uuid.uuid4().hex
        inputs = {'request_id': request_id}
        if source_sha:
            if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
                raise BuildError('GitHub вернул некорректный номер версии исходников.')
            inputs['source_sha'] = source_sha
        result = self.github.request('POST', self.base + '/actions/workflows/' + WORKFLOW_NAME + '/dispatches',
                                     json={'ref': branch, 'inputs': inputs})
        if result.get('workflow_run_id'):
            return {'id': int(result['workflow_run_id']), 'html_url': result['html_url'],
                    'repository': self.repository, 'app_id': self.credentials.app_id}
        # Older GitHub API responses may be empty. Match this request only.
        for _ in range(12):
            listing = self.github.request('GET', self.base + '/actions/workflows/' + WORKFLOW_NAME + '/runs',
                                          params={'event': 'workflow_dispatch', 'branch': branch, 'per_page': 30})
            matches = [run for run in listing.get('workflow_runs', []) if request_id in run.get('display_title', '')]
            if len(matches) == 1:
                run = matches[0]
                return {'id': run['id'], 'html_url': run['html_url'], 'repository': self.repository, 'app_id': self.credentials.app_id}
            time.sleep(2)
        raise BuildError('Запрос сборки отправлен, но GitHub пока не вернул её номер. Проверьте Actions перед повторным запуском.')

    def start(self, *, auto_push=False, flutter_version='3.47.0', inspected=None):
        snapshot = inspected or self.inspect()
        text = render_workflow(snapshot['app_directory'], snapshot['bundle_id'], self.credentials.app_id,
                               snapshot['branch'], auto_push, flutter_version,
                               secret_prefix=self.secret_prefix(), repository_identity=self.repository_identity())
        if snapshot.get('app_type') == 'xcode':
            from local_project_setup import render_native_workflow
            text = render_native_workflow(dict(snapshot, app_id=self.credentials.app_id),
                                          secret_prefix=self.secret_prefix(), repository_identity=self.repository_identity(),
                                          auto_push=auto_push, branch=snapshot['branch'], flutter_version=flutter_version)
        # Validate ownership before writing any credentials into the repository.
        current = self.github.request('GET', self.base + '/contents/' + WORKFLOW_PATH,
                                      params={'ref': snapshot['branch']}, allow_missing=True)
        if current:
            self.check_workflow_owner(base64.b64decode(current.get('content', '')).decode('utf-8'))
        self.configure_secrets()
        self.install_workflow(snapshot['branch'], text)
        # A new workflow can take a few seconds to become available after commit.
        for _ in range(15):
            self.check_stop()
            workflow = self.github.request('GET', self.base + '/actions/workflows/' + WORKFLOW_NAME, allow_missing=True)
            if workflow:
                if workflow.get('state') == 'disabled_manually':
                    self.github.request('PUT', self.base + '/actions/workflows/' + WORKFLOW_NAME + '/enable')
                break
            time.sleep(2)
        else:
            raise BuildError('GitHub ещё регистрирует файл сборки. Повторите запуск через минуту.')
        self.log('Запускаю сборку…')
        return self.dispatch(snapshot['branch'], snapshot['head_sha'])

    def observe(self, run_id: int):
        self.check_stop()
        run = self.github.request('GET', self.base + '/actions/runs/' + str(int(run_id)))
        jobs = self.github.request('GET', self.base + '/actions/runs/' + str(int(run_id)) + '/jobs', params={'per_page': 100})
        return dict(run, jobs=jobs.get('jobs', []))


_WORKFLOW_JOBS = r'''
jobs:
  credentials:
    name: Check Apple credentials
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check required repository secrets
        shell: bash
        env:
          APP_STORE_CONNECT_ISSUER_ID: ${{ secrets.APP_STORE_CONNECT_ISSUER_ID }}
          APP_STORE_CONNECT_KEY_IDENTIFIER: ${{ secrets.APP_STORE_CONNECT_KEY_IDENTIFIER }}
          APP_STORE_CONNECT_PRIVATE_KEY: ${{ secrets.APP_STORE_CONNECT_PRIVATE_KEY }}
          CERTIFICATE_PRIVATE_KEY: ${{ secrets.CERTIFICATE_PRIVATE_KEY }}
        run: |
          set -euo pipefail
          for name in APP_STORE_CONNECT_ISSUER_ID APP_STORE_CONNECT_KEY_IDENTIFIER APP_STORE_CONNECT_PRIVATE_KEY CERTIFICATE_PRIVATE_KEY; do
            if [[ -z "${!name}" ]]; then
              echo "::error::Missing repository secret $name."
              exit 1
            fi
          done
  build:
    name: Build and upload iOS
    needs: credentials
    runs-on: macos-26
    timeout-minutes: 60
    defaults:
      run:
        shell: bash
        working-directory: ${{ env.APP_DIRECTORY }}
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6
        with:
          ref: ${{ inputs.source_sha || github.sha }}
      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: '3.12'
      - name: Set up Flutter
        uses: subosito/flutter-action@1a449444c387b1966244ae4d4f8c696479add0b2 # v2
        with:
          flutter-version: '__FLUTTER_VERSION__'
          channel: stable
          cache: true
      - name: Install release tools and dependencies
        run: |
          python -m pip install codemagic-cli-tools==0.70.0
          flutter pub get
      - name: Run tests
        run: |
          if [[ -d test ]] && find test -name '*_test.dart' -print -quit | grep -q .; then
            flutter test
          else
            echo 'No Flutter tests in this project.'
          fi
      - name: Set up signing and next build number
        env:
          APP_STORE_CONNECT_ISSUER_ID: ${{ secrets.APP_STORE_CONNECT_ISSUER_ID }}
          APP_STORE_CONNECT_KEY_IDENTIFIER: ${{ secrets.APP_STORE_CONNECT_KEY_IDENTIFIER }}
          APP_STORE_CONNECT_PRIVATE_KEY: ${{ secrets.APP_STORE_CONNECT_PRIVATE_KEY }}
          CERTIFICATE_PRIVATE_KEY: ${{ secrets.CERTIFICATE_PRIVATE_KEY }}
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          keychain initialize
          app-store-connect fetch-signing-files "$BUNDLE_ID" --type IOS_APP_STORE --create
          keychain add-certificates
          xcode-project use-profiles --export-options-plist "$RUNNER_TEMP/export_options.plist"
          latest="$(app-store-connect get-latest-build-number "$APP_STORE_APPLE_ID" --platform IOS --all-versions)"
          latest="${latest:-0}"
          if [[ ! "$latest" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]; then
            echo '::error::Apple returned an invalid build number.'
            exit 1
          fi
          artifact_latest="$(python - <<'PYARTIFACT'
          import json, os, re, urllib.request
          url = os.environ['GITHUB_API_URL'] + '/repos/' + os.environ['GITHUB_REPOSITORY'] + '/actions/artifacts?per_page=100'
          request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'})
          with urllib.request.urlopen(request, timeout=30) as response:
              artifacts = json.load(response)['artifacts']
          numbers = [int(match.group(1)) for artifact in artifacts
                     if (match := re.fullmatch(r'ios-ipa-([0-9]+)', artifact['name']))]
          print(max(numbers, default=0))
          PYARTIFACT
          )"
          major="${latest%%.*}"
          if (( artifact_latest > 10#$major )); then
            major="$artifact_latest"
          fi
          next=$((10#$major + 1))
          echo "IOS_BUILD_NUMBER=$next" >> "$GITHUB_ENV"
      - name: Build IPA
        run: |
          /usr/libexec/PlistBuddy -c 'Set :CFBundleVersion $(FLUTTER_BUILD_NUMBER)' ios/Runner/Info.plist
          flutter build ipa --release --obfuscate \
            --split-debug-info="$RUNNER_TEMP/private-symbols" \
            --build-number="$IOS_BUILD_NUMBER" \
            --export-options-plist="$RUNNER_TEMP/export_options.plist"
      - name: Prepare IPA artifact
        run: |
          set -euo pipefail
          python - <<'PYIPA'
          from pathlib import Path
          files = list(Path('build/ios/ipa').glob('*.ipa'))
          if len(files) != 1:
              raise SystemExit(f'Expected exactly one IPA; found {len(files)}. Upload stopped.')
          target = files[0].with_name('app.ipa')
          if files[0] != target:
              files[0].rename(target)
          print('IPA artifact ready: app.ipa')
          PYIPA
      - name: Save signed IPA
        uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f # v6, Node.js 24
        with:
          name: ios-ipa-${{ env.IOS_BUILD_NUMBER }}
          path: ${{ env.APP_DIRECTORY }}/build/ios/ipa/*.ipa
          if-no-files-found: error
          retention-days: 3
      - name: Save private debugging symbols
        uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f # v6, Node.js 24
        with:
          name: ios-private-symbols-${{ env.IOS_BUILD_NUMBER }}
          path: ${{ runner.temp }}/private-symbols
          if-no-files-found: error
          retention-days: 30
      - name: Upload to App Store Connect
        env:
          APP_STORE_CONNECT_ISSUER_ID: ${{ secrets.APP_STORE_CONNECT_ISSUER_ID }}
          APP_STORE_CONNECT_KEY_IDENTIFIER: ${{ secrets.APP_STORE_CONNECT_KEY_IDENTIFIER }}
          APP_STORE_CONNECT_PRIVATE_KEY: ${{ secrets.APP_STORE_CONNECT_PRIVATE_KEY }}
        run: app-store-connect publish --path 'build/ios/ipa/*.ipa'
'''
