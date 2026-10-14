import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import app_store_ppo_automation as app
from test_asset_library import FakeAssetClient


class SharedScreenshotWorkerTests(unittest.TestCase):
    credentials = {'issuer': 'i', 'key_id': 'k', 'p8_path': 'key.p8', 'app_id': 'app'}

    def make_client(self):
        client = FakeAssetClient()
        client.get_latest_app_store_version = Mock(return_value='version')
        client.get_primary_copy_payload = Mock(return_value={'locale': 'en-US'})
        client.seed_locale_from_primary = Mock(side_effect=lambda version, locale, payload, **kwargs: (locale, False))
        client.get_version_locales = Mock(return_value=['en-US', 'cs', 'ru'])
        client.create_ppo_experiment = Mock(return_value='experiment')
        client.create_treatment = Mock(return_value='treatment')
        client.create_treatment_localization = Mock(side_effect=lambda treatment, locale: locale)
        client.update_ppo_experiment = Mock()
        client.get_experiments = Mock(return_value=[{'id': 'experiment', 'attributes': {'name': 'Test'}}])
        client.get_treatments = Mock(return_value=[{'id': 'treatment', 'attributes': {'name': 'Treatment C'}}])
        client.get_localizations = Mock(return_value=[{'id': locale, 'attributes': {'locale': locale}} for locale in ['en-US', 'cs', 'ru']])
        return client

    def test_screenshot_worker_defaults_to_shared_upload_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name, color in [('1.png', 'red'), ('2.png', 'green')]:
                path = os.path.join(folder, name)
                Image.new('RGBA', (1206, 2622), color).save(path)
                paths.append(path)
            client = self.make_client()
            worker = app.ScreenshotUploadWorker(self.credentials, ['cs', 'en-US', 'ru'], paths)
            successes, logs = [], []
            worker.upload_finished.connect(successes.append)
            worker.log_msg.connect(logs.append)
            with patch.object(app, 'ASCClient', return_value=client), patch('asset_library.requests.request', return_value=Mock()) as transfer:
                worker.run()
            self.assertEqual(successes, [True], logs)
            self.assertEqual(transfer.call_count, 2)
            self.assertEqual(len(client.images), 2)
            ordered = [[item['relationships']['image']['data']['id'] for item in client.placements.values()
                        if item['localization'] == locale] for locale in ['en-US', 'cs', 'ru']]
            self.assertEqual(ordered, [ordered[0]] * 3)
            self.assertEqual(len(ordered[0]), 2)
            self.assertEqual(client.seed_locale_from_primary.call_args_list[0].args[1], 'en-US')
            self.assertTrue(all(os.path.isfile(path) for path in paths))

    def test_ppo_create_and_update_reuse_images_across_treatment_locales(self):
        with tempfile.TemporaryDirectory() as folder:
            Image.new('RGB', (1206, 2622), 'blue').save(os.path.join(folder, '1.png'))
            for mode in ['create', 'update']:
                with self.subTest(mode=mode):
                    client = self.make_client()
                    worker = app.AutomationWorker(mode, self.credentials, 'Test', 100, ['Variant C'],
                                                  ['en-US', 'cs', 'ru'], {'Variant C': folder})
                    logs = []
                    worker.log_msg.connect(logs.append)
                    with patch.object(app, 'ASCClient', return_value=client), patch('asset_library.requests.request', return_value=Mock()) as transfer:
                        worker.run()
                    self.assertEqual(transfer.call_count, 1, logs)
                    self.assertEqual(len(client.placements), 3, logs)
                    self.assertTrue(all('appStoreVersionExperimentTreatmentLocalization' in item['relationships']
                                        for item in client.placements.values()))
                    self.assertTrue(any('УСПЕШНО ЗАВЕРШЕН' in line for line in logs), logs)

    def test_processing_failure_does_not_touch_locale_or_report_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, '1.png')
            Image.new('RGB', (1206, 2622)).save(path)
            client = self.make_client()
            client.fail_processing = True
            worker = app.ScreenshotUploadWorker(self.credentials, ['en-US', 'cs'], [path])
            successes = []
            worker.upload_finished.connect(successes.append)
            with patch.object(app, 'ASCClient', return_value=client), patch('asset_library.requests.request', return_value=Mock()):
                worker.run()
            self.assertEqual(successes, [False])
            client.seed_locale_from_primary.assert_not_called()
            self.assertEqual(client.placements, {})
