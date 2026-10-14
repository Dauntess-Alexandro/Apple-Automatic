"""Find local iOS sources and prepare reviewable build files without network writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import yaml

from github_build_upload import BuildError, WORKFLOW_MARKER, WORKFLOW_PATH, render_workflow, repository_from_folder

IGNORED = {'.git', '.dart_tool', 'build', 'Pods', 'node_modules', 'packages', 'shared',
           'example', 'test', '.symlinks', 'vendor', '.venv', 'venv', '__pycache__'}
BUNDLE_PATTERN = r'PRODUCT_BUNDLE_IDENTIFIER\s*=\s*"?([A-Za-z0-9_.-]+)"?\s*;'
MANIFEST = '.ios-build-files.json'


class WorkflowDumper(yaml.SafeDumper):
    pass


def _string(dumper, value):
    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else None)


WorkflowDumper.add_representer(str, _string)


def resource_path(relative):
    return Path(__file__).resolve().parent / relative


def workflow_spec(choice):
    spec = yaml.safe_load(resource_path('templates/github-actions-ios.yml').read_text(encoding='utf-8'))
    defaults = {'bundle_id': choice['bundle_id'], 'app_store_apple_id': str(choice['app_id']),
                'project_path': choice['app_directory'], 'xcode_scheme': choice.get('xcode_scheme') or 'auto'}
    for name, value in defaults.items():
        spec['on']['workflow_dispatch']['inputs'][name]['default'] = value
    spec['name'] = 'IOS BUILD'
    return spec


def dump_workflow(spec):
    return yaml.dump(spec, Dumper=WorkflowDumper, sort_keys=False, allow_unicode=True, width=120)


def render_native_workflow(choice, *, secret_prefix='', repository_identity='', auto_push=False,
                           branch='main', flutter_version='3.47.0'):
    """The same portable build logic, with fixed identity for the desktop launcher."""
    spec = workflow_spec(choice)
    inputs = spec['on']['workflow_dispatch']['inputs']
    inputs['request_id'] = {'description': 'Build request', 'type': 'string', 'required': False}
    inputs['source_sha'] = {'description': 'Inspected source commit', 'type': 'string', 'required': False}
    if auto_push:
        spec['on']['push'] = {'branches': [branch], 'paths-ignore': [WORKFLOW_PATH]}
    spec['run-name'] = 'IOS BUILD - ${{ inputs.request_id || github.sha }}'
    spec['env'].update(BUNDLE_ID=choice['bundle_id'], APP_STORE_APPLE_ID=str(choice['app_id']),
                       PROJECT_PATH_INPUT=choice['app_directory'], XCODE_SCHEME_INPUT=choice.get('xcode_scheme') or 'auto',
                       XCODE_CONTAINER_INPUT='auto')
    for step in spec['jobs']['build']['steps']:
        if step.get('uses', '').startswith('actions/checkout@'):
            step['with'] = {'ref': '${{ inputs.source_sha || github.sha }}'}
        if step.get('name') == 'Set up Flutter':
            step['with']['flutter-version'] = flutter_version
        if step.get('name') == 'Run tests':
            step['if'] = "env.APP_TYPE == 'flutter'"
        if step.get('name') == 'Upload to App Store Connect':
            step.pop('if', None)
    text = dump_workflow(spec)
    if secret_prefix:
        text = text.replace('secrets.APP_STORE_CONNECT_', 'secrets.' + secret_prefix + 'APP_STORE_CONNECT_')
    return WORKFLOW_MARKER + '\n# Apple identity: ' + repository_identity + '\n' + text


def discover_local_sources(folder):
    """Read each source once; dependency copies and symlinks are never candidates."""
    entries = []
    for current, directories, files in os.walk(folder, followlinks=False):
        path = Path(current)
        directories[:] = sorted(d for d in directories if d not in IGNORED and not (path / d).is_symlink())
        manifest = path / 'pubspec.yaml'
        pbx = path / 'ios/Runner.xcodeproj/project.pbxproj'
        if manifest.is_file() and pbx.is_file() and re.search(r'sdk:\s*flutter\b', manifest.read_text(errors='replace')):
            bundles = set(re.findall(BUNDLE_PATTERN, pbx.read_text(errors='replace')))
            entries.append((path, 'flutter', bundles, ''))
            directories[:] = []
            continue
        for name in list(directories):
            if name.endswith('.xcodeproj'):
                project = path / name
                pbx = project / 'project.pbxproj'
                if pbx.is_file():
                    bundles = set(re.findall(BUNDLE_PATTERN, pbx.read_text(errors='replace')))
                    schemes = {p.stem for p in project.glob('xcshareddata/xcschemes/*.xcscheme') if not p.is_symlink()}
                    schemes.update(p.stem for p in path.glob('*.xcworkspace/xcshareddata/xcschemes/*.xcscheme') if not p.is_symlink())
                    # Multiple schemes remain explicit: the universal workflow asks for a choice.
                    entries.append((path, 'xcode', bundles, next(iter(schemes)) if len(schemes) == 1 else 'auto'))
                directories.remove(name)
            elif name.endswith('.xcworkspace'):
                directories.remove(name)
    return entries


def find_project_matches(apps, projects_root, manual_folder=''):
    root = Path(projects_root).expanduser().resolve()
    search = Path(manual_folder).expanduser().resolve() if manual_folder else root
    if not search.is_dir():
        raise BuildError('Папка Projects не найдена. Укажите папку для поиска или выберите проект вручную.')
    choices, seen = [], set()
    for source, kind, bundles, scheme in discover_local_sources(search):
        # Use the nearest actual checkout, never inherit the Projects container repository.
        repository_root = None
        for parent in (source, *source.parents):
            if parent == root or (not manual_folder and not parent.is_relative_to(root)):
                break
            if (parent / '.git').exists():
                repository_root = parent
                break
        if repository_root is None:
            relative = source.relative_to(search)
            repository_root = search if manual_folder or not relative.parts else search / relative.parts[0]
        for app in apps:
            if app['bundle_id'] not in bundles:
                continue
            identity = (app['app_id'], str(source))
            if identity in seen:
                continue
            seen.add(identity)
            repository = ''
            if (repository_root / '.git').exists() and repository_root != root:
                try:
                    repository = repository_from_folder(str(repository_root))
                except BuildError:
                    pass
            choices.append(dict(app, local_folder=str(repository_root), app_directory=source.relative_to(repository_root).as_posix(),
                                app_type=kind, xcode_scheme=scheme, repository=repository))
    return sorted(choices, key=lambda c: (c['app_name'].casefold(), c['local_folder'], c['app_directory']))


def install_project_files(choice, *, issuer, key_id):
    root = Path(choice['local_folder']).resolve()
    if not root.is_dir():
        raise BuildError('Папка проекта больше не существует.')
    identity = hashlib.sha256((issuer + '/' + str(choice['app_id'])).encode()).hexdigest()
    prefix = 'APPLE_AUTOMATIC_' + hashlib.sha256((issuer + '/' + str(choice['app_id']) + '/' + key_id).encode()).hexdigest()[:16].upper() + '_'
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink():
        raise BuildError('Файл настройки проекта не должен быть ссылкой.')
    old = {}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text())
        except (ValueError, OSError):
            raise BuildError('Не удалось проверить существующие файлы настройки проекта.') from None
        if old.get('identity') != identity:
            raise BuildError('Папка уже привязана к другому приложению или аккаунту Apple.')
    portable = dump_workflow(workflow_spec(choice))
    if choice['app_type'] == 'flutter':
        active = render_workflow(choice['app_directory'], choice['bundle_id'], str(choice['app_id']), 'main',
                                 secret_prefix=prefix, repository_identity=identity)
    else:
        active = render_native_workflow(choice, secret_prefix=prefix, repository_identity=identity)
    intro = ('# Файлы IOS BUILD подготовлены программой\n\n'
             f'Приложение: {choice["app_name"]}\n\nApp ID: `{choice["app_id"]}` · Bundle ID: `{choice["bundle_id"]}`\n\n'
             'Рабочий файл уже находится в `.github/workflows/apple-automatic-ios.yml`.\n'
             'Отправьте файлы вместе с исходниками через GitHub Desktop. Кнопка «Собрать и загрузить»\n'
             'в программе настроит защищённые ключи GitHub и запустит сборку.\n'
             'Корневой `github-actions-ios.yml` — переносимая копия для самостоятельного запуска;\n'
             'не добавляйте её вторым workflow в этот же репозиторий.\n\n---\n\n')
    payload = {'github-actions-ios.yml': portable, WORKFLOW_PATH: active,
               'GITHUB_ACTIONS_IOS.md': intro + resource_path('docs/GITHUB_ACTIONS_IOS.md').read_text(encoding='utf-8')}
    # Check every destination before any write. Preserve manual edits and external symlinks.
    for name, text in payload.items():
        path = root / name
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)):
            raise BuildError('Путь файла сборки содержит ссылку за пределы проекта или символическую ссылку.')
        if path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != old.get('files', {}).get(name) and path.read_text(encoding='utf-8') != text:
                raise BuildError(f'Файл {name} уже существует или изменён вручную. Сохраните свою версию перед подготовкой.')
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in payload.items()}
    payload[MANIFEST] = json.dumps({'identity': identity, 'files': hashes}, ensure_ascii=False, indent=2) + '\n'
    for name, text in payload.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return dict(choice)
