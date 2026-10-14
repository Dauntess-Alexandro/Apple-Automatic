"""Import a screenshot batch into the library without changing any placements."""
import concurrent.futures
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tempfile
import zipfile

from PIL import Image, ImageOps
from PySide6.QtCore import QThread, Signal

from asset_library import AssetLibraryUploader


IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def natural_path_key(path):
    return tuple((1, int(part)) if part.isdecimal() else (0, part)
                 for part in re.split(r'(\d+)', str(path).casefold()))


def is_metadata(path):
    path = PurePosixPath(str(path))
    return '__MACOSX' in path.parts or path.name == '.DS_Store' or path.name.startswith('._')


def file_fingerprint(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'Источник содержит ссылку или специальный файл: {path.name}.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, digest.hexdigest()


class ScreenshotImportSource:
    def __init__(self, path):
        original = Path(path).expanduser().absolute()
        if original.is_symlink():
            raise ValueError('Выберите саму папку или ZIP, а не ссылку.')
        self.path = original.resolve()
        self.is_archive = self.path.is_file() and self.path.suffix.lower() == '.zip'
        if not self.path.is_dir() and not self.is_archive:
            raise ValueError('Выберите папку со скриншотами или ZIP-архив.')
        protected = {Path.home(), Path.home() / 'Desktop', Path.home() / 'Downloads'}
        if self.path.is_dir() and (self.path in protected or self.path in Path.cwd().resolve().parents
                                   or self.path == Path.cwd().resolve()):
            raise ValueError('Выберите отдельную папку со скриншотами, а не общую папку.')
        self.fingerprint = self._snapshot()
        if self.is_archive:
            with zipfile.ZipFile(self.path) as archive:
                entries = archive.infolist()
                if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_BYTES:
                    raise ValueError('Распакованный ZIP превышает 512 МБ.')
                names = set()
                for entry in entries:
                    name = PurePosixPath(entry.filename)
                    if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                            or stat.S_ISLNK(entry.external_attr >> 16) or entry.filename in names):
                        raise ValueError('ZIP содержит небезопасные пути, ссылки или повторяющиеся имена.')
                    names.add(entry.filename)
                self.files = sorted([entry.filename for entry in entries if not entry.is_dir()
                                     and not is_metadata(entry.filename)
                                     and PurePosixPath(entry.filename).suffix.lower() in IMAGE_EXTENSIONS], key=natural_path_key)
        else:
            self.files = sorted([str(self.path / name) for name in self.fingerprint['files']
                                 if not is_metadata(name) and Path(name).suffix.lower() in IMAGE_EXTENSIONS],
                                key=lambda name: natural_path_key(Path(name).relative_to(self.path)))
        if not self.files:
            raise ValueError('В выбранном источнике нет скриншотов PNG/JPG/WEBP/BMP/TIFF.')
        self.batch_name = secrets.token_hex(6)

    def _snapshot(self):
        if self.is_archive:
            return file_fingerprint(self.path)
        root = self.path.lstat()
        files, directories = {}, {}
        for base, dirs, names in os.walk(self.path, followlinks=False):
            for name in dirs:
                child = Path(base) / name
                info = child.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f'Папка содержит ссылку: {child.name}.')
                directories[str(child.relative_to(self.path))] = (info.st_dev, info.st_ino)
            for name in names:
                child = Path(base) / name
                files[str(child.relative_to(self.path))] = file_fingerprint(child)
        return {'root': (root.st_dev, root.st_ino), 'files': files, 'directories': directories}

    def verify_unchanged(self):
        if self.path.is_symlink() or not self.path.exists() or self._snapshot() != self.fingerprint:
            raise ValueError('Источник изменился после выбора. Папка/архив сохранены; выберите источник заново.')

    def prepare_files(self, destination):
        self.verify_unchanged()
        prepared = []
        archive = zipfile.ZipFile(self.path) if self.is_archive else None
        try:
            for index, name in enumerate(self.files, 1):
                stream = archive.open(name) if archive else open(name, 'rb')
                with stream, Image.open(stream) as image:
                    image = ImageOps.exif_transpose(image)
                    image.load()
                    if image.format not in {None, 'PNG', 'JPEG', 'WEBP', 'BMP', 'TIFF'}:
                        raise ValueError(f'{name}: неподдерживаемый формат скриншота.')
                    rgba = image.convert('RGBA')
                    ready = Image.new('RGB', image.size, 'white')
                    ready.paste(rgba, mask=rgba.getchannel('A'))
                    target = Path(destination) / f'{self.batch_name}_{index:03d}_{secrets.token_hex(4)}.png'
                    ready.save(target, 'PNG', optimize=True)
                    prepared.append(str(target))
        finally:
            if archive:
                archive.close()
        self.verify_unchanged()
        return prepared

    def delete_after_success(self):
        self.verify_unchanged()
        if self.is_archive:
            self.path.unlink()
            return
        unrelated = [name for name in self.fingerprint['files']
                     if str(self.path / name) not in self.files and not is_metadata(name)]
        if unrelated:
            raise ValueError('В папке есть другие файлы. Исходники сохранены: ' + ', '.join(unrelated[:3]))
        # Delete the recorded files only. rmdir refuses to remove newly added files.
        for name in self.fingerprint['files']:
            (self.path / name).unlink()
        for name in sorted(self.fingerprint['directories'], key=lambda name: len(Path(name).parts), reverse=True):
            (self.path / name).rmdir()
        self.path.rmdir()


def upload_screenshot_source(client, source, logger=None, progress=None):
    logger = logger or (lambda message: None)
    progress = progress or (lambda done, total: None)
    uploader = AssetLibraryUploader(client, logger)
    uploader._initialize()
    with tempfile.TemporaryDirectory(prefix='asset-library-import-') as directory:
        paths = source.prepare_files(directory)
        matches = [uploader.catalog.match_screenshot(path) for path in paths]
        counts = {}
        for match in matches:
            for group, limit in match['groups'].items():
                counts[group] = counts.get(group, 0) + 1
                if counts[group] > limit:
                    raise ValueError(f'Набор {group}: лимит Apple — {limit} скриншотов. Разделите источник на наборы.')
        images = [None] * len(paths)
        logger(f'Asset Library: загружаю {len(paths)} скриншотов; порядок сохранён отдельно от случайных имён.')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(uploader.upload_image, path, match['spec_ids']): index
                       for index, (path, match) in enumerate(zip(paths, matches))}
            failures = []
            for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
                try:
                    images[futures[future]] = future.result()
                except Exception as exc:
                    failures.append(exc)
                progress(done, len(paths))
            if failures:
                logger('Загрузка не завершена; исходники сохранены.')
                if len(failures) == 1:
                    raise failures[0]
                raise RuntimeError('; '.join(str(exc) for exc in failures))
        source.verify_unchanged()
        groups = uploader.catalog.screenshot_groups('APP_STORE_VERSIONS')
        batches = {}
        for image in images:
            for group_id, group in groups.items():
                if (image.get('attributes') or {}).get('specId') in group['spec_ids']:
                    batches.setdefault(group_id, []).append(image['id'])
        return {'images': images, 'batches': batches, 'source': source, 'catalog': uploader.catalog, 'groups': groups}


class LibraryImportWorker(QThread):
    completed = Signal(dict)
    error = Signal(str)
    progress = Signal(int, int)
    log_msg = Signal(str)

    def __init__(self, client_factory, source, parent=None):
        super().__init__(parent)
        self.client_factory = client_factory
        self.source = source

    def run(self):
        try:
            result = upload_screenshot_source(self.client_factory(self.log_msg.emit), self.source,
                                              self.log_msg.emit, self.progress.emit)
            self.completed.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
