import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import app_store_ppo_automation as app


class ScreenshotNoCropTests(unittest.TestCase):
    def test_preserves_dimensions_and_optimizes_before_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'image.jpg')
            exif = Image.Exif()
            exif[270] = 'private metadata'
            Image.new('RGB', (1284, 2778), 'green').save(path, exif=exif)
            client = Mock()
            client.get_latest_app_store_version.return_value = 'version'
            client.get_primary_copy_payload.return_value = {}
            client.get_version_localization_records.return_value = []
            client.seed_locale_from_primary.return_value = ('locale', False)
            client._request.return_value = {'data': [
                {'id': 'set', 'attributes': {'screenshotDisplayType': app.APP_IPHONE_65_DISPLAY_TYPE}},
                {'id': 'medium', 'attributes': {'screenshotDisplayType': 'APP_IPHONE_61'}},
            ]}
            client.clear_screenshot_set.return_value = 0
            uploaded = []

            def inspect_upload(set_id, file_path, name):
                with Image.open(file_path) as result:
                    uploaded.append((set_id, result.size, result.format, len(result.getexif())))

            client.upload_screenshot.side_effect = inspect_upload
            worker = app.ScreenshotUploadWorker(
                {'issuer': 'i', 'key_id': 'k', 'p8_path': 'p', 'app_id': 'a'},
                ['en-US'], [path], no_crop=True, optimize_images=True,
                resize_to_iphone_67=True, iphone_65_crop_png=True,
                use_asset_library=False,
            )
            success = []
            worker.upload_finished.connect(success.append)
            with patch.object(app, 'ASCClient', return_value=client), patch.object(app.time, 'sleep'):
                worker.run()
            self.assertEqual(success, [True])
            self.assertEqual(uploaded, [('set', (1284, 2778), 'JPEG', 0), ('medium', (1206, 2622), 'JPEG', 0)])
            self.assertEqual(client.wait_for_screenshot_set.call_count, 2)
            with Image.open(path) as original:
                self.assertEqual(original.size, (1284, 2778))
                self.assertEqual(original.getexif()[270], 'private metadata')

    def test_detects_display_type_and_rejects_mixed_diagonals(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name, size in [('landscape.png', (2688, 1242)), ('portrait.png', (1290, 2796))]:
                path = os.path.join(folder, name)
                Image.new('RGB', size).save(path)
                paths.append(path)
            self.assertEqual(app.resolve_uncropped_screenshot_display_type(paths[:1]), app.APP_IPHONE_65_DISPLAY_TYPE)
            with self.assertRaises(ValueError):
                app.resolve_uncropped_screenshot_display_type(paths)
