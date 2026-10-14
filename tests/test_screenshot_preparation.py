import hashlib
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import app_store_ppo_automation as app


class ScreenshotPreparationTests(unittest.TestCase):
    def test_recognizes_new_medium_and_large_dimensions(self):
        for size, expected in [
            ((1206, 2622), 'APP_IPHONE_61'), ((2622, 1206), 'APP_IPHONE_61'),
            ((1179, 2556), 'APP_IPHONE_61'), ((2556, 1179), 'APP_IPHONE_61'),
            ((1320, 2868), 'APP_IPHONE_67'), ((2736, 1260), 'APP_IPHONE_67'),
        ]:
            with self.subTest(size=size):
                self.assertEqual(app._display_type_from_screenshot_size(*size)[0], expected)

    def test_auto_crop_preserves_orientation_and_removes_alpha(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for index, size in enumerate([(800, 1800), (1800, 800)]):
                path = os.path.join(folder, f'images{index}.png')
                Image.new('RGBA', size, (255, 0, 0, 80)).save(path)
                paths.append(path)
            preparer = app.ScreenshotPreparer(optimize_images=False)
            try:
                batches = preparer.prepare(paths)
                self.assertEqual(set(batches), {'APP_IPHONE_61'})
                for path, size in zip(batches['APP_IPHONE_61'], [(1206, 2622), (2622, 1206)]):
                    with Image.open(path) as result:
                        self.assertEqual(result.size, size)
                        self.assertEqual(result.mode, 'RGB')
                        self.assertEqual(result.format, 'JPEG')
                with Image.open(paths[0]) as original:
                    self.assertEqual(original.size, (800, 1800))
            finally:
                prepared_paths = [path for batch in batches.values() for path in batch]
                preparer.cleanup()
            self.assertTrue(all(not os.path.exists(path) for path in prepared_paths))

    def test_legacy_modes_also_prepare_required_medium_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'image.png')
            Image.new('RGB', (800, 1800)).save(path)
            for display_type, target, output_format in [
                ('APP_IPHONE_65', (2688, 1242), 'PNG'),
                ('APP_IPHONE_65', (1284, 2778), 'PNG'),
                ('APP_IPHONE_67', (1290, 2796), 'JPEG'),
                ('APP_IPHONE_55', None, 'JPEG'),
            ]:
                with self.subTest(display_type=display_type, target=target):
                    preparer = app.ScreenshotPreparer()
                    try:
                        batches = preparer.prepare([path], display_type=display_type,
                                                   target_size=target, output_format=output_format)
                        self.assertEqual(set(batches), {display_type, 'APP_IPHONE_61'})
                        with Image.open(batches[display_type][0]) as result:
                            self.assertEqual(result.size, target or (1242, 2208))
                            self.assertEqual(result.format, output_format)
                        with Image.open(batches['APP_IPHONE_61'][0]) as result:
                            self.assertEqual(result.size, (1206, 2622))
                    finally:
                        preparer.cleanup()

    def test_valid_medium_file_is_not_cropped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'image.png')
            Image.new('RGB', (1179, 2556), 'green').save(path)
            preparer = app.ScreenshotPreparer()
            try:
                with patch.object(app.ImageOps, 'fit', side_effect=AssertionError('Unexpected crop')):
                    batches = preparer.prepare([path])
                with Image.open(batches['APP_IPHONE_61'][0]) as result:
                    self.assertEqual(result.size, (1179, 2556))
            finally:
                preparer.cleanup()

    def test_unknown_size_in_no_crop_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'image.png')
            Image.new('RGB', (100, 100)).save(path)
            with self.assertRaisesRegex(ValueError, '100.*100'):
                app.resolve_uncropped_screenshot_display_type([path])


class ScreenshotDeliveryTests(unittest.TestCase):
    def make_client(self):
        client = object.__new__(app.ASCClient)
        client.logger = Mock()
        client._request = Mock()
        return client

    def test_commit_contains_full_file_checksum(self):
        client = self.make_client()
        client._request.return_value = {'data': {'id': 'shot', 'attributes': {'uploadOperations': []}}}
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'screenshot bytes')
            source.flush()
            client.upload_screenshot('set', source.name, 'image.jpg')
        attributes = client._request.call_args.args[2]['data']['attributes']
        self.assertEqual(attributes, {'uploaded': True, 'sourceFileChecksum': hashlib.md5(b'screenshot bytes').hexdigest()})

    def test_waits_until_all_screenshots_are_complete(self):
        client = self.make_client()
        client.get_screenshots = Mock(side_effect=[
            [{'id': 'a', 'attributes': {'assetDeliveryState': {'state': 'UPLOAD_COMPLETE'}}}],
            [{'id': 'a', 'attributes': {'assetDeliveryState': {'state': 'COMPLETE'}}}],
        ])
        with patch.object(app.time, 'sleep'):
            client.wait_for_screenshot_set('set', 1, max_attempts=2)
        self.assertEqual(client.get_screenshots.call_count, 2)

    def test_failed_processing_is_not_reported_as_success(self):
        client = self.make_client()
        client.get_screenshots = Mock(return_value=[{'id': 'a', 'attributes': {
            'fileName': 'image.jpg', 'assetDeliveryState': {
                'state': 'FAILED', 'errors': [{'code': 'INVALID_DIMENSIONS', 'description': 'Wrong size'}],
            },
        }}])
        with self.assertRaisesRegex(ValueError, 'image.jpg.*Wrong size'):
            client.wait_for_screenshot_set('set', 1, max_attempts=1)

    def test_incomplete_processing_times_out(self):
        client = self.make_client()
        client.get_screenshots = Mock(return_value=[])
        with self.assertRaises(TimeoutError):
            client.wait_for_screenshot_set('set', 1, max_attempts=1)

    def test_failed_processing_cleans_all_rejected_records_only(self):
        client = self.make_client()
        client.get_screenshots = Mock(return_value=[
            {'id': 'bad1', 'attributes': {'fileName': 'one.jpg', 'assetDeliveryState': {'state': 'FAILED'}}},
            {'id': 'good', 'attributes': {'assetDeliveryState': {'state': 'COMPLETE'}}},
            {'id': 'bad2', 'attributes': {'fileName': 'two.jpg', 'assetDeliveryState': {'state': 'FAILED'}}},
        ])
        client.delete_screenshot = Mock()
        with self.assertRaisesRegex(ValueError, 'one.jpg.*two.jpg'):
            client.wait_for_screenshot_set('set', 3, max_attempts=1)
        self.assertEqual([call.args[0] for call in client.delete_screenshot.call_args_list], ['bad1', 'bad2'])

    def test_removes_only_failed_or_mismatched_legacy_assets(self):
        client = self.make_client()
        client.get_screenshots = Mock(return_value=[
            {'id': 'wrong', 'attributes': {'imageAsset': {'width': 1206, 'height': 2622}, 'assetDeliveryState': {'state': 'COMPLETE'}}},
            {'id': 'failed', 'attributes': {'assetDeliveryState': {'state': 'FAILED'}}},
            {'id': 'valid', 'attributes': {'imageAsset': {'width': 1290, 'height': 2796}, 'assetDeliveryState': {'state': 'COMPLETE'}}},
            {'id': 'processing', 'attributes': {'assetDeliveryState': {'state': 'UPLOAD_COMPLETE'}}},
        ])
        client.delete_screenshot = Mock()
        removed = client.remove_invalid_screenshots([
            {'id': 'large', 'attributes': {'screenshotDisplayType': 'APP_IPHONE_67'}},
            {'id': 'medium', 'attributes': {'screenshotDisplayType': 'APP_IPHONE_61'}},
            {'id': 'ipad', 'attributes': {'screenshotDisplayType': 'APP_IPAD_PRO_3GEN_129'}},
        ], replacing_types={'APP_IPHONE_61'})
        self.assertEqual(removed, 2)
        self.assertEqual([call.args[0] for call in client.delete_screenshot.call_args_list], ['wrong', 'failed'])
        client.get_screenshots.assert_called_once_with('large')

    def test_upload_failure_cleans_reserved_record_before_retry(self):
        client = self.make_client()
        client._request.return_value = {'data': {'id': 'shot', 'attributes': {'uploadOperations': [
            {'requestHeaders': [], 'offset': 0, 'length': 5, 'url': 'https://example.com/upload'},
        ]}}}
        client.delete_screenshot = Mock()
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'image')
            source.flush()
            with patch.object(app.requests, 'put', side_effect=app.requests.exceptions.Timeout('timeout')), patch.object(app.time, 'sleep'):
                with self.assertRaises(app.requests.exceptions.Timeout):
                    client.upload_screenshot('set', source.name, 'image.jpg')
        client.delete_screenshot.assert_called_once_with('shot')
