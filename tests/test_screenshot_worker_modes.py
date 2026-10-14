import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import app_store_ppo_automation as app


class ScreenshotWorkerModesTests(unittest.TestCase):
    credentials = {'issuer': 'issuer', 'key_id': 'key', 'p8_path': 'key.p8', 'app_id': 'app'}

    def make_client(self):
        client = Mock()
        client.get_latest_app_store_version.return_value = 'version'
        client.get_primary_copy_payload.return_value = {}
        client.get_version_localization_records.return_value = []
        client.seed_locale_from_primary.return_value = ('locale', False)
        client.get_version_locales.return_value = ['en-US']
        client.create_ppo_experiment.return_value = 'experiment'
        client.create_treatment.return_value = 'treatment'
        client.create_treatment_localization.return_value = 'locale'
        client.get_experiments.return_value = [{'id': 'experiment', 'attributes': {'name': 'Test'}}]
        client.get_treatments.return_value = [{'id': 'treatment', 'attributes': {'name': 'Treatment B'}}]
        client.get_localizations.return_value = [{'id': 'locale', 'attributes': {'locale': 'en-US'}}]
        sets = [{'id': kind, 'attributes': {'screenshotDisplayType': kind}}
                for kind in ['APP_IPHONE_55', 'APP_IPHONE_61', 'APP_IPHONE_65', 'APP_IPHONE_67']]
        client.get_screenshot_sets.return_value = sets
        client._request.return_value = {'data': sets}
        client.get_screenshots.return_value = []
        client.clear_screenshot_set.return_value = 0
        return client

    def test_standard_default_uploads_auto_cropped_medium(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'images.png')
            Image.new('RGB', (1800, 800)).save(path)
            client = self.make_client()
            uploaded = []

            def inspect_upload(set_id, file_path, filename):
                with Image.open(file_path) as result:
                    uploaded.append((set_id, result.size))

            client.upload_screenshot.side_effect = inspect_upload
            worker = app.ScreenshotUploadWorker(self.credentials, ['en-US'], [path], use_asset_library=False)
            success = []
            worker.upload_finished.connect(success.append)
            with patch.object(app, 'ASCClient', return_value=client), patch.object(app.time, 'sleep'):
                worker.run()
            self.assertEqual(success, [True])
            self.assertEqual(uploaded, [('APP_IPHONE_61', (2622, 1206))])
            client.wait_for_screenshot_set.assert_called_once_with('APP_IPHONE_61', 1)

    def test_ppo_create_and_update_preserve_55_and_add_medium(self):
        with tempfile.TemporaryDirectory() as folder:
            Image.new('RGB', (2208, 1242)).save(os.path.join(folder, 'images.png'))
            for mode in ['create', 'update']:
                with self.subTest(mode=mode):
                    client = self.make_client()
                    uploaded = []

                    def inspect_upload(set_id, file_path, filename):
                        with Image.open(file_path) as result:
                            uploaded.append((set_id, result.size))

                    client.upload_screenshot.side_effect = inspect_upload
                    worker = app.AutomationWorker(mode, self.credentials, 'Test', 100,
                                                  ['Variant B'], ['en-US'], {'Variant B': folder}, use_asset_library=False)
                    logs = []
                    worker.log_msg.connect(logs.append)
                    with patch.object(app, 'ASCClient', return_value=client), patch.object(app.time, 'sleep'):
                        worker.run()
                    self.assertCountEqual(uploaded, [('APP_IPHONE_55', (2208, 1242)), ('APP_IPHONE_61', (2622, 1206))])
                    self.assertEqual(client.wait_for_screenshot_set.call_count, 2)
                    self.assertTrue(any('УСПЕШНО ЗАВЕРШЕН' in line for line in logs), logs)

    def test_corrupt_file_does_not_delete_existing_screenshots(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'images.png')
            with open(path, 'wb') as source:
                source.write(b'invalid image')
            client = self.make_client()
            workers = [app.ScreenshotUploadWorker(self.credentials, ['en-US'], [path]),
                       app.AutomationWorker('create', self.credentials, 'Test', 100,
                                            ['Variant B'], ['en-US'], {'Variant B': folder})]
            for worker in workers:
                with patch.object(app, 'ASCClient', return_value=client):
                    worker.run()
            client.clear_screenshot_set.assert_not_called()
            client.delete_screenshot.assert_not_called()
            client.create_ppo_experiment.assert_not_called()

    def test_processing_error_reports_locale_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'images.png')
            Image.new('RGB', (1206, 2622)).save(path)
            client = self.make_client()
            client.wait_for_screenshot_set.side_effect = ValueError('Apple rejected image')
            worker = app.ScreenshotUploadWorker(self.credentials, ['cs'], [path], use_asset_library=False)
            success, logs = [], []
            worker.upload_finished.connect(success.append)
            worker.log_msg.connect(logs.append)
            with patch.object(app, 'ASCClient', return_value=client), patch.object(app.time, 'sleep'):
                worker.run()
            self.assertEqual(success, [False])
            self.assertTrue(any('cs' in line and 'Apple rejected image' in line for line in logs), logs)
