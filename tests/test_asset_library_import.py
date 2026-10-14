import importlib.util
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import asset_library
from test_asset_library import FakeAssetClient


class AssetImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name) / 'screens'
        self.folder.mkdir()
        self.client = FakeAssetClient()
        self.transfer = patch('asset_library.requests.request', return_value=Mock())
        self.transfer.start()
        self.addCleanup(self.transfer.stop)

    def source_class(self):
        self.assertIsNotNone(importlib.util.find_spec("asset_library_import"), "Folder/ZIP asset import is missing")
        import asset_library_import
        return asset_library_import.ScreenshotImportSource

    def image(self, name, color='green', size=(1206, 2622)):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', size, color).save(path)
        return path

    def upload(self, source):
        import asset_library_import
        return asset_library_import.upload_screenshot_source(self.client, source)

    def test_folder_uploads_to_library_only_in_original_numeric_order_with_random_names(self):
        for name, color in [('10.png', 'blue'), ('2.png', 'red'), ('1.png', 'green')]:
            self.image(name, color)
        source = self.source_class()(self.folder)
        result = self.upload(source)
        self.assertEqual([Path(path).name for path in source.files], ['1.png', '2.png', '10.png'])
        names = [image['attributes']['fileName'] for image in result['images']]
        self.assertTrue(all(re.fullmatch(r'[a-z0-9]+_\d{3}_[a-z0-9]+\.png', name) for name in names))
        self.assertEqual(names, sorted(names))
        self.assertEqual(result['batches']['medium-runtime'], [image['id'] for image in result['images']])
        self.assertEqual(self.client.placements, {})
        self.assertTrue(self.folder.exists())
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ['1.png', '10.png', '2.png'])

    def test_zip_upload_and_cleanup_after_confirmed_success(self):
        image = self.image('1.png')
        archive = Path(self.directory.name) / 'screens.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.write(image, 'folder/1.png')
            zipped.writestr('__MACOSX/._1.png', 'metadata')
        source = self.source_class()(archive)
        self.upload(source)
        self.assertTrue(archive.exists())
        source.delete_after_success()
        self.assertFalse(archive.exists())
        self.assertTrue(image.exists())

    def test_invalid_file_prevents_any_upload_and_preserves_every_source(self):
        self.image('1.png')
        self.image('2.png', size=(100, 100))
        source = self.source_class()(self.folder)
        with self.assertRaises(ValueError):
            self.upload(source)
        self.assertEqual(self.client.images, {})
        self.assertEqual(len(list(self.folder.iterdir())), 2)

    def test_over_limit_is_rejected_without_silently_dropping_files(self):
        for index in range(11):
            self.image(f'{index}.png')
        with self.assertRaisesRegex(ValueError, '10|лимит'):
            self.upload(self.source_class()(self.folder))
        self.assertEqual(self.client.images, {})
        self.assertEqual(len(list(self.folder.iterdir())), 11)

    def test_upload_failure_keeps_folder(self):
        self.image('1.png')
        self.client.fail_processing = True
        with self.assertRaises(ValueError):
            self.upload(self.source_class()(self.folder))
        self.assertTrue((self.folder / '1.png').exists())

    def test_modified_or_extra_files_block_deletion(self):
        for mutation in ['changed', 'extra']:
            with self.subTest(mutation=mutation):
                image = self.image('1.png')
                source = self.source_class()(self.folder)
                if mutation == 'changed':
                    self.image('1.png', 'blue')
                else:
                    (self.folder / 'notes.txt').write_text('keep this')
                with self.assertRaises(ValueError):
                    source.delete_after_success()
                self.assertTrue(image.exists())
                for child in self.folder.iterdir():
                    child.unlink()

    def test_folder_with_unrelated_files_is_not_deleted(self):
        self.image('1.png')
        (self.folder / 'notes.txt').write_text('keep this')
        source = self.source_class()(self.folder)
        with self.assertRaises(ValueError):
            source.delete_after_success()
        self.assertTrue((self.folder / 'notes.txt').exists())
        self.assertTrue((self.folder / '1.png').exists())

    def test_changed_zip_is_not_deleted(self):
        archive = Path(self.directory.name) / 'screens.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.write(self.image('1.png'), '1.png')
        source = self.source_class()(archive)
        with zipfile.ZipFile(archive, 'a') as zipped:
            zipped.writestr('new.txt', 'new')
        with self.assertRaises(ValueError):
            source.delete_after_success()
        self.assertTrue(archive.exists())

    def test_rejects_zip_traversal_and_symlink_sources(self):
        archive = Path(self.directory.name) / 'screens.zip'
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('../escape.png', 'bad')
        with self.assertRaises(ValueError):
            self.source_class()(archive)
        real = self.image('1.png')
        (self.folder / '2.png').symlink_to(real)
        with self.assertRaises(ValueError):
            self.source_class()(self.folder)

    def test_successful_folder_cleanup_removes_only_selected_source(self):
        self.image('nested/1.png')
        sibling = Path(self.directory.name) / 'keep.txt'
        sibling.write_text('keep')
        source = self.source_class()(self.folder)
        source.delete_after_success()
        self.assertFalse(self.folder.exists())
        self.assertEqual(sibling.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
