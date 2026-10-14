import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from asset_library import AssetLibraryUploader, ScreenshotAssetCatalog


def reference_catalog():
    return {'data': [{'attributes': {
        'features': [{'featureId': feature, 'placementPolicies': [{
            'placementType': 'APP_SCREENSHOT',
            'groupLimits': [{'groupIds': ['medium-runtime', 'large-runtime'], 'maxCount': 10}],
        }]} for feature in ['APP_STORE_VERSIONS', 'PRODUCT_PAGE_OPTIMIZATIONS']],
        'placementProfileGroups': [
            {'placementProfileGroupId': 'medium-runtime', 'displayClassId': 'IPHONE_DYNAMIC_ISLAND_MEDIUM_DISPLAY', 'platform': 'IPHONE_APP_STORE'},
            {'placementProfileGroupId': 'large-runtime', 'displayClassId': 'IPHONE_FACE_ID_LARGE_DISPLAY', 'platform': 'IPHONE_APP_STORE'},
        ],
        'placementTypes': [{'placementTypeId': 'APP_SCREENSHOT',
                            'acceptsAssetCategories': ['APP_SCREENSHOTS_AND_PREVIEWS'],
                            'specMappings': [
                                {'placementGroupId': 'medium-runtime', 'specs': ['medium-spec']},
                                {'placementGroupId': 'large-runtime', 'specs': ['large-spec']},
                            ]}],
        'imageSpecs': [
            {'specId': spec_id, 'dimensions': {'minWidth': width, 'maxWidth': width, 'minHeight': height, 'maxHeight': height},
             'alphaAllowed': False, 'fileExtensions': ['.png', '.jpg', '.jpeg'], 'mimeTypes': ['image/png', 'image/jpeg'],
             'compatiblePlacementTypes': ['APP_SCREENSHOT'], 'maxFileSize': 10000000}
            for spec_id, width, height in [('medium-spec', 1206, 2622), ('large-spec', 1284, 2778)]
        ],
    }}]}


class FakeAssetClient:
    app_id = 'app'
    base_url = 'https://api.appstoreconnect.apple.com/v1'

    def __init__(self):
        self.calls = []
        self.images = {}
        self.placements = {}
        self.fail_next_create = False
        self.fail_processing = False
        self.wrong_spec = False
        self.counter = 0
        self.lose_delete_for = None
        self.hide_failed_create_reads = 0
        self.hidden_reads = {}
        self.fail_create_group = None

    def _request(self, method, endpoint, payload=None, **kwargs):
        self.calls.append((method, endpoint, payload))
        resource, _, query = endpoint.partition('?')
        if resource == 'appAssetLibraryRefData':
            return reference_catalog()
        if resource == 'apps/app/assetLibrary':
            return {'data': {'id': 'library'}}
        if resource == 'appAssetLibraryImages' and method == 'POST':
            image_id = f'image-{len(self.images) + 1}'
            attributes = dict(payload['data']['attributes'])
            attributes.update(state='AWAITING_UPLOAD', specId='wrong' if self.wrong_spec else 'medium-spec',
                              uploadOperations=[{'method': 'PUT', 'url': 'https://example.com/part', 'offset': 0,
                                                 'length': attributes['fileSize'], 'requestHeaders': []}])
            self.images[image_id] = attributes
            return {'data': {'id': image_id, 'attributes': attributes}}
        if resource.startswith('appAssetLibraryImages/'):
            image_id = resource.split('/')[1]
            if method == 'PATCH':
                self.images[image_id]['state'] = 'FAILED' if self.fail_processing else 'PREPARE_FOR_SUBMISSION'
            elif method == 'DELETE':
                del self.images[image_id]
                return {}
            return {'data': {'id': image_id, 'attributes': self.images[image_id]}}
        if resource.endswith('/placements'):
            localization = resource.split('/')[1]
            items = [item for item in self.placements.values() if item['localization'] == localization]
            visible = []
            for item in items:
                remaining = self.hidden_reads.get(item['id'], 0)
                if remaining:
                    self.hidden_reads[item['id']] = remaining - 1
                else:
                    visible.append(item)
            items = visible
            from urllib.parse import parse_qs
            filters = parse_qs(query)
            group = filters.get('filter[placementGroup]', [None])[0]
            placement_type = filters.get('filter[placementType]', [None])[0]
            if placement_type:
                items = [item for item in items if item['attributes']['placementType'] == placement_type]
            if group:
                items = [item for item in items if item['attributes']['placementGroup'] == group]
            return {'data': items}
        if resource == 'appAssetLibraryPlacements' and method == 'POST':
            self.counter += 1
            placement_id = f'placement-{self.counter}'
            data = payload['data']
            target = next(key for key in data['relationships'] if key != 'image')
            self.placements[placement_id] = {
                'id': placement_id, 'localization': data['relationships'][target]['data']['id'],
                'attributes': dict(data['attributes'], state='ACTIVE'),
                'relationships': data['relationships'],
            }
            if self.fail_next_create or self.fail_create_group == data['attributes']['placementGroup']:
                self.fail_next_create = False
                self.fail_create_group = None
                self.hidden_reads[placement_id] = self.hide_failed_create_reads
                raise RuntimeError('ambiguous placement timeout')
            return {'data': self.placements[placement_id]}
        if resource.startswith('appAssetLibraryPlacements/') and method == 'DELETE':
            placement_id = resource.split('/')[1]
            if placement_id not in self.placements:
                raise RuntimeError('404 placement absent')
            del self.placements[placement_id]
            if self.lose_delete_for == placement_id:
                self.lose_delete_for = None
                raise RuntimeError('lost DELETE reply')
            return {}
        if resource == 'appAssetLibraryPlacementOrderingRequests':
            data = payload['data']
            ids = [item['id'] for item in data['relationships']['orderedPlacements']['data']]
            ordered = {key: self.placements[key] for key in ids}
            self.placements = {**{key: item for key, item in self.placements.items() if key not in ordered}, **ordered}
            return {'data': {'id': 'order'}}
        raise AssertionError((method, endpoint))

    def seed_placement(self, localization, image_id, group='medium-runtime', placement_type='APP_SCREENSHOT'):
        self._request('POST', 'appAssetLibraryPlacements', {'data': {
            'attributes': {'placementType': placement_type, 'placementGroup': group},
            'relationships': {'image': {'data': {'type': 'appAssetLibraryImages', 'id': image_id}},
                              'appStoreVersionLocalization': {'data': {'type': 'appStoreVersionLocalizations', 'id': localization}}},
        }})


class AssetLibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'screen.jpg')
        Image.new('RGB', (1206, 2622), 'green').save(self.path)
        self.client = FakeAssetClient()
        self.uploader = AssetLibraryUploader(self.client, max_workers=2)
        self.transfer = patch('asset_library.requests.request', return_value=Mock())
        self.transfer_mock = self.transfer.start()
        self.addCleanup(self.transfer.stop)

    def test_resolves_group_from_live_catalog_instead_of_constants(self):
        catalog = ScreenshotAssetCatalog(reference_catalog())
        match = catalog.match(self.path, 'APP_IPHONE_61', 'APP_STORE_VERSIONS')
        self.assertEqual(match['group'], 'medium-runtime')
        self.assertEqual(match['spec_ids'], {'medium-spec'})
        self.assertEqual(match['max_count'], 10)

    def test_uploads_once_and_reuses_same_ids_for_multiple_locales(self):
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path, self.path]})
        for locale in ['en-US', 'cs', 'ru']:
            self.uploader.replace_localization(locale, prepared)
        self.assertEqual(len(self.client.images), 1)
        self.assertEqual(self.transfer_mock.call_count, 1)
        for locale in ['en-US', 'cs', 'ru']:
            self.assertEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()
                              if item['localization'] == locale], ['image-1', 'image-1'])
        commits = [payload['data']['attributes'] for method, endpoint, payload in self.client.calls
                   if method == 'PATCH' and endpoint.startswith('appAssetLibraryImages/')]
        self.assertEqual(commits, [{'uploaded': True}])

    def test_prevalidates_all_files_before_any_upload(self):
        invalid = os.path.join(self.directory.name, 'invalid.jpg')
        Image.new('RGB', (100, 100)).save(invalid)
        with self.assertRaises(ValueError):
            self.uploader.prepare_batches({'APP_IPHONE_61': [self.path, invalid]})
        self.assertEqual(self.client.images, {})

    def test_rejects_alpha_before_upload(self):
        path = os.path.join(self.directory.name, 'alpha.png')
        Image.new('RGBA', (1206, 2622)).save(path)
        with self.assertRaisesRegex(ValueError, 'альфа|прозрач'):
            self.uploader.prepare_batches({'APP_IPHONE_61': [path]})
        self.assertEqual(self.client.images, {})

    def test_unsupported_feature_and_limits_do_not_write(self):
        for feature, paths in [('UNKNOWN', [self.path]), ('APP_STORE_VERSIONS', [self.path] * 11)]:
            with self.subTest(feature=feature):
                with self.assertRaises(ValueError):
                    self.uploader.prepare_batches({'APP_IPHONE_61': paths}, feature=feature)
        self.assertEqual(self.client.images, {})

    def test_failed_processing_preserves_existing_placements(self):
        self.client.seed_placement('cs', 'old')
        previous = dict(self.client.placements)
        self.client.fail_processing = True
        with self.assertRaises(ValueError):
            self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.assertEqual(self.client.placements, previous)
        self.assertEqual(self.client.images, {})

    def test_mismatched_processed_spec_is_rejected(self):
        self.client.wrong_spec = True
        with self.assertRaisesRegex(ValueError, 'спецификац'):
            self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.assertEqual(self.client.images, {})

    def test_replacement_preserves_previews_other_groups_and_shared_assets(self):
        self.client.seed_placement('cs', 'old')
        self.client.seed_placement('cs', 'other-device', group='large-runtime')
        self.client.seed_placement('cs', 'preview', placement_type='APP_PREVIEW')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.uploader.replace_localization('cs', prepared)
        images = [item['relationships']['image']['data']['id'] for item in self.client.placements.values()]
        self.assertCountEqual(images, ['other-device', 'preview', 'image-1'])
        self.assertFalse(any(method == 'DELETE' and endpoint.startswith('appAssetLibraryImages/')
                             for method, endpoint, _payload in self.client.calls))

    def test_ambiguous_placement_failure_restores_old_order(self):
        self.client.seed_placement('cs', 'old-first')
        self.client.seed_placement('cs', 'old-second')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.client.fail_next_create = True
        with self.assertRaisesRegex(RuntimeError, 'timeout'):
            self.uploader.replace_localization('cs', prepared)
        self.assertEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()],
                         ['old-first', 'old-second'])
        self.assertEqual(len(self.client.images), 1)

    def test_ppo_uses_treatment_relationship_and_feature(self):
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]}, feature='PRODUCT_PAGE_OPTIMIZATIONS')
        self.uploader.replace_localization('treatment-locale', prepared, relationship='appStoreVersionExperimentTreatmentLocalization')
        placement = next(iter(self.client.placements.values()))
        self.assertIn('appStoreVersionExperimentTreatmentLocalization', placement['relationships'])

    def test_pagination_reads_all_existing_placements(self):
        client = Mock()
        client.base_url = self.client.base_url
        client._request.side_effect = [
            {'data': [{'id': 'first'}], 'links': {'next': self.client.base_url + '/page-two?cursor=2'}},
            {'data': [{'id': 'second'}]},
        ]
        uploader = AssetLibraryUploader(client)
        self.assertEqual([item['id'] for item in uploader.list_placements('cs')], ['first', 'second'])
        self.assertEqual(client._request.call_args.args[1], 'page-two?cursor=2')

    def test_waits_for_processing_instead_of_accepting_upload_complete(self):
        client = Mock()
        client._request.side_effect = [
            {'data': {'id': 'image', 'attributes': {'state': 'UPLOAD_COMPLETE'}}},
            {'data': {'id': 'image', 'attributes': {'state': 'PREPARE_FOR_SUBMISSION', 'specId': 'medium-spec'}}},
        ]
        uploader = AssetLibraryUploader(client)
        with patch('asset_library.time.sleep'):
            result = uploader.wait_for_image('image', {'medium-spec'}, max_attempts=2)
        self.assertEqual(result['attributes']['state'], 'PREPARE_FOR_SUBMISSION')
        self.assertEqual(client._request.call_count, 2)

    def test_timeout_preserves_processing_asset_and_old_placements(self):
        self.client.seed_placement('cs', 'old')
        with patch.object(self.uploader, 'wait_for_image', side_effect=TimeoutError('still processing')):
            with self.assertRaises(TimeoutError):
                self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.assertEqual(len(self.client.images), 1)
        self.assertEqual(next(iter(self.client.placements.values()))['relationships']['image']['data']['id'], 'old')

    def test_part_retry_keeps_the_same_reservation(self):
        import requests
        self.transfer_mock.side_effect = [requests.exceptions.Timeout('retry'), Mock()]
        with patch('asset_library.time.sleep'):
            self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.assertEqual(len(self.client.images), 1)
        self.assertEqual(self.transfer_mock.call_count, 2)

    def test_restore_failure_reports_manual_check_without_deleting_shared_image(self):
        self.client.seed_placement('cs', 'old')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        log = Mock()
        self.uploader.logger = log
        with patch.object(self.uploader, '_create_placement', side_effect=RuntimeError('create failed')), patch('asset_library.time.sleep'):
            with self.assertRaises(RuntimeError):
                self.uploader.replace_localization('cs', prepared)
        self.assertTrue(any('вручную' in call.args[0] for call in log.call_args_list))
        self.assertEqual(len(self.client.images), 1)

    def test_missing_old_asset_relationship_aborts_before_deletion(self):
        self.client.seed_placement('cs', 'old')
        previous = next(iter(self.client.placements.values()))
        previous['relationships'].pop('image')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        with self.assertRaises(ValueError):
            self.uploader.replace_localization('cs', prepared)
        self.assertEqual(len(self.client.placements), 1)

    def test_pagination_does_not_follow_untrusted_server(self):
        client = Mock()
        client.base_url = self.client.base_url
        client._request.return_value = {'data': [], 'links': {'next': 'https://example.com/steal-token'}}
        uploader = AssetLibraryUploader(client)
        with self.assertRaises(ValueError):
            uploader.list_placements('cs')
        self.assertEqual(client._request.call_count, 1)

    def test_delete_lost_reply_does_not_prevent_rollback(self):
        self.client.seed_placement('cs', 'old')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.client.fail_next_create = True
        self.client.lose_delete_for = 'placement-2'
        with patch('asset_library.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                self.uploader.replace_localization('cs', prepared)
        self.assertEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()], ['old'])

    def test_late_committed_placement_is_reconciled_before_restoring(self):
        self.client.seed_placement('cs', 'old')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.client.fail_next_create = True
        self.client.hide_failed_create_reads = 2
        with patch('asset_library.time.sleep'):
            with self.assertRaises(RuntimeError):
                self.uploader.replace_localization('cs', prepared)
        self.assertEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()], ['old'])

    def test_unresolved_creation_does_not_claim_confirmed_restoration(self):
        self.client.seed_placement('cs', 'old')
        prepared = self.uploader.prepare_batches({'APP_IPHONE_61': [self.path]})
        self.client.fail_next_create = True
        self.client.hide_failed_create_reads = 100
        log = Mock()
        self.uploader.logger = log
        with patch('asset_library.time.sleep'):
            with self.assertRaises(RuntimeError):
                self.uploader.replace_localization('cs', prepared)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertFalse(any('восстановил предыдущие' in message.lower() for message in messages), messages)
        self.assertTrue(any('не подтвержд' in message and 'вручную' in message for message in messages), messages)

    def test_failure_in_second_group_restores_both_groups(self):
        self.client.seed_placement('cs', 'old-large', group='large-runtime')
        self.client.seed_placement('cs', 'old-medium')
        self.client.fail_create_group = 'medium-runtime'
        prepared = {'APP_IPHONE_65': {'group': 'large-runtime', 'assets': ['new-large']},
                    'APP_IPHONE_61': {'group': 'medium-runtime', 'assets': ['new-medium']}}
        with patch('asset_library.time.sleep'):
            with self.assertRaises(RuntimeError):
                self.uploader.replace_localization('cs', prepared)
        self.assertCountEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()],
                              ['old-large', 'old-medium'])

    def test_catalog_dimension_bounds_are_inclusive(self):
        response = reference_catalog()
        response['data'][0]['attributes']['imageSpecs'][0]['dimensions']['minWidth'] = 1200
        catalog = ScreenshotAssetCatalog(response)
        self.assertEqual(catalog.match(self.path, 'APP_IPHONE_61', 'APP_STORE_VERSIONS')['spec_ids'], {'medium-spec'})
