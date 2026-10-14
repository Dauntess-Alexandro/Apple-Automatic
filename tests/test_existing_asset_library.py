import copy
import unittest
from unittest.mock import Mock, patch

from asset_library import AssetLibraryUploader
from test_asset_library import FakeAssetClient, reference_catalog


def library_image(identifier, **attributes):
    return {'id': identifier, 'type': 'appAssetLibraryImages', 'attributes': {
        'fileName': 'same-name.png', 'category': 'APP_SCREENSHOTS_AND_PREVIEWS',
        'specId': 'medium-spec', 'state': 'PREPARE_FOR_SUBMISSION',
        'imageAsset': {'width': 1206, 'height': 2622}, **attributes,
    }}


class ExistingAssetClient(FakeAssetClient):
    def __init__(self):
        super().__init__()
        self.catalog_response = reference_catalog()
        self.images = {identifier: library_image(identifier)['attributes'] for identifier in ['batch-1', 'batch-2']}

    def _request(self, method, endpoint, payload=None, **kwargs):
        if endpoint == 'appAssetLibraryRefData':
            self.calls.append((method, endpoint, payload))
            return copy.deepcopy(self.catalog_response)
        if endpoint == 'appAssetLibraries/library/images?limit=200':
            self.calls.append((method, endpoint, payload))
            return {'data': [{'id': identifier, 'attributes': copy.deepcopy(attributes)}
                             for identifier, attributes in self.images.items()]}
        if method == 'GET' and endpoint.startswith('appAssetLibraryPlacements/'):
            self.calls.append((method, endpoint, payload))
            if not endpoint.endswith('?include=image'):
                raise AssertionError('Individual placement recheck must include image')
            return {'data': copy.deepcopy(self.placements[endpoint.split('/')[1].split('?')[0]])}
        response = super()._request(method, endpoint, payload, **kwargs)
        if endpoint == 'appAssetLibraryPlacements' and method == 'POST':
            response['data']['attributes']['state'] = 'ACTIVE'
        return response


class PlacementStateTests(unittest.TestCase):
    def test_unproven_parent_state_is_not_accepted_as_active(self):
        uploader = AssetLibraryUploader(Mock())
        with patch.object(uploader, 'list_placements', return_value=[{
            'id': 'p', 'attributes': {'state': 'PARENT_PREPARE_FOR_SUBMISSION'},
        }]), patch('asset_library.time.sleep'):
            with self.assertRaises(TimeoutError):
                uploader._verify('locale', 'appStoreVersionLocalization', 'group', ['p'])

    def test_active_placement_is_confirmed_without_polling(self):
        uploader = AssetLibraryUploader(Mock())
        with patch.object(uploader, 'list_placements', return_value=[{'id': 'p', 'attributes': {'state': 'ACTIVE'}}]), \
                patch('asset_library.time.sleep') as sleep:
            uploader._verify('locale', 'appStoreVersionLocalization', 'group', ['p'])
        sleep.assert_not_called()

    def test_invalid_placement_fails_promptly_with_details(self):
        uploader = AssetLibraryUploader(Mock())
        with patch.object(uploader, 'list_placements', return_value=[{
            'id': 'p', 'attributes': {'state': 'INVALID', 'invalidReasons': ['SPEC_MISMATCH']},
        }]), patch('asset_library.time.sleep') as sleep:
            with self.assertRaisesRegex(ValueError, 'p.*SPEC_MISMATCH'):
                uploader._verify('locale', 'appStoreVersionLocalization', 'group', ['p'])
        sleep.assert_not_called()


class ExistingLibraryTests(unittest.TestCase):
    def setUp(self):
        self.client = ExistingAssetClient()
        self.uploader = AssetLibraryUploader(self.client)

    def assert_read_only(self):
        self.assertTrue(all(method == 'GET' for method, _, _ in self.client.calls), self.client.calls)

    def test_listing_keeps_same_named_batches_and_paginates(self):
        client = Mock(base_url=self.client.base_url, app_id='app')
        client._request.side_effect = [reference_catalog(), {'data': {'id': 'library'}},
            {'data': [library_image('batch-1')], 'links': {'next': self.client.base_url + '/page-two?cursor=2'}},
            {'data': [library_image('batch-2')]}]
        result = AssetLibraryUploader(client).list_existing_images()
        self.assertEqual([item['id'] for item in result], ['batch-1', 'batch-2'])
        self.assertEqual(client._request.call_args_list[2].args, ('GET', 'appAssetLibraries/library/images?limit=200'))

    def test_prepares_explicit_ids_in_user_order_without_upload(self):
        result = self.uploader.prepare_existing_batches({'medium-runtime': ['batch-2', 'batch-1']})
        self.assertEqual(result, {'medium-runtime': {'group': 'medium-runtime', 'assets': ['batch-2', 'batch-1']}})
        self.assert_read_only()

    def test_uses_dynamic_group_spec_and_feature_limits(self):
        attrs = self.client.catalog_response['data'][0]['attributes']
        attrs['placementProfileGroups'][0]['placementProfileGroupId'] = 'new-runtime'
        attrs['placementTypes'][0]['specMappings'][0] = {'placementGroupId': 'new-runtime', 'specs': ['new-spec']}
        attrs['imageSpecs'][0]['specId'] = 'new-spec'
        attrs['features'][1]['placementPolicies'][0]['groupLimits'] = [{'groupIds': ['new-runtime'], 'maxCount': 1}]
        self.client.images['batch-1']['specId'] = 'new-spec'
        prepared = self.uploader.prepare_existing_batches({'new-runtime': ['batch-1']}, feature='PRODUCT_PAGE_OPTIMIZATIONS')
        self.assertEqual(prepared['new-runtime']['assets'], ['batch-1'])
        with self.assertRaises(ValueError):
            self.uploader.prepare_existing_batches({'new-runtime': ['batch-1']}, feature='APP_STORE_VERSIONS')
        self.assert_read_only()

    def test_rejects_bad_selections_before_mutation(self):
        cases = [{}, {'medium-runtime': []}, {'unknown': ['batch-1']},
                 {'medium-runtime': ['foreign-id']}, {'medium-runtime': ['batch-1', 'batch-1']},
                 {'medium-runtime': [str(index) for index in range(11)]}]
        for selection in cases:
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                self.uploader.prepare_existing_batches(selection)
        self.assert_read_only()

    def test_rejects_unready_wrong_category_spec_and_dimensions(self):
        for change in [{'state': 'UPLOAD_COMPLETE'}, {'state': 'INVALID', 'stateDetails': 'broken'},
                       {'specId': 'large-spec'}, {'category': 'APP_ICON'},
                       {'imageAsset': {'width': 100, 'height': 100}}, {'imageAsset': None}]:
            with self.subTest(change=change):
                self.client.images['batch-1'] = library_image('batch-1', **change)['attributes']
                with self.assertRaises(ValueError):
                    self.uploader.prepare_existing_batches({'medium-runtime': ['batch-1']})
        self.assert_read_only()

    def test_all_groups_validated_before_any_assignment_or_cleanup(self):
        self.client.seed_placement('cs', 'old')
        previous = copy.deepcopy(self.client.placements)
        self.client.calls.clear()
        with self.assertRaises(ValueError):
            self.uploader.replace_existing_localization('cs', {
                'medium-runtime': ['batch-1'], 'large-runtime': ['batch-2'],
            }, cleanup_spec_mismatch=True)
        self.assertEqual(self.client.placements, previous)
        self.assert_read_only()

    def test_non_ios_platform_groups_are_rejected_before_any_deletion(self):
        for platform in ['MAC_APP_STORE', 'TV_APP_STORE', 'VISION_APP_STORE']:
            with self.subTest(platform=platform):
                self.client.catalog_response['data'][0]['attributes']['placementProfileGroups'][0]['platform'] = platform
                self.client.calls.clear()
                with self.assertRaises(ValueError):
                    self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']}, cleanup_spec_mismatch=True)
                self.assert_read_only()

    def test_revalidates_live_catalog_and_image_before_each_replacement(self):
        self.uploader.prepare_existing_batches({'medium-runtime': ['batch-1']})
        self.client.images['batch-1']['state'] = 'REJECTED'
        self.client.calls.clear()
        with self.assertRaises(ValueError):
            self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']})
        self.assert_read_only()

    def test_replacement_preserves_other_valid_groups_previews_and_library_images(self):
        self.client.seed_placement('cs', 'old')
        self.client.seed_placement('cs', 'valid-other', group='large-runtime')
        self.client.seed_placement('cs', 'preview', placement_type='APP_PREVIEW')
        images = copy.deepcopy(self.client.images)
        self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-2', 'batch-1']})
        assigned = [item['relationships']['image']['data']['id'] for item in self.client.placements.values()]
        self.assertEqual(assigned, ['valid-other', 'preview', 'batch-2', 'batch-1'])
        self.assertEqual(self.client.images, images)

    def test_cleanup_only_exact_invalid_screenshot_spec_mismatch(self):
        attributes = [
            {'state': 'INVALID', 'invalidReasons': ['SPEC_MISMATCH']},
            {'state': 'INVALID', 'invalidReasons': ['SPEC_MISMATCH', 'OTHER']},
            {'state': 'ACTIVE', 'invalidReasons': ['SPEC_MISMATCH']},
            {'state': 'INVALID'},
            {'state': 'INVALID', 'invalidReasons': ['SPEC_MISMATCH']},
        ]
        for index, extra in enumerate(attributes):
            self.client.seed_placement('cs', f'other-{index}', group='large-runtime',
                                       placement_type='APP_PREVIEW' if index == 4 else 'APP_SCREENSHOT')
            self.client.placements[f'placement-{index + 1}']['attributes'].update(extra)
        self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']}, cleanup_spec_mismatch=True)
        self.assertNotIn('placement-1', self.client.placements)
        self.assertTrue(all(f'placement-{index}' in self.client.placements for index in range(2, 6)))
        self.assertEqual(set(self.client.images), {'batch-1', 'batch-2'})

    def test_cleanup_is_opt_in_and_does_not_run_if_assignment_fails(self):
        self.client.seed_placement('cs', 'invalid', group='large-runtime')
        self.client.placements['placement-1']['attributes'].update(state='INVALID', invalidReasons=['SPEC_MISMATCH'])
        self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']})
        self.assertIn('placement-1', self.client.placements)
        self.client.fail_next_create = True
        with patch('asset_library.time.sleep'), self.assertRaises(RuntimeError):
            self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-2']}, cleanup_spec_mismatch=True)
        self.assertIn('placement-1', self.client.placements)

    def test_cleanup_live_state_details_requires_exact_reason_and_fresh_candidate(self):
        for identifier, details in [
            ('old-exact', [{'code': 'SPEC_MISMATCH', 'description': 'SPEC_MISMATCH'}]),
            ('old-mixed', [{'code': 'SPEC_MISMATCH'}, {'code': 'OTHER'}]),
            ('old-unknown', [{'description': 'SPEC_MISMATCH'}]),
        ]:
            self.client.seed_placement('cs', identifier, group='large-runtime')
            placement = self.client.placements[f'placement-{self.client.counter}']
            placement['attributes'].update(state='INVALID', stateDetails=details)
        self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']}, cleanup_spec_mismatch=True)
        self.assertNotIn('placement-1', self.client.placements)
        self.assertIn('placement-2', self.client.placements)
        self.assertIn('placement-3', self.client.placements)
        self.assertIn(('GET', 'appAssetLibraryPlacements/placement-1?include=image', None), self.client.calls)

    def test_cleanup_skips_candidate_that_becomes_active_on_recheck(self):
        self.client.seed_placement('cs', 'old', group='large-runtime')
        self.client.placements['placement-1']['attributes'].update(
            state='INVALID', stateDetails=[{'code': 'SPEC_MISMATCH', 'description': 'SPEC_MISMATCH'}])
        original = self.client._request

        def request(method, endpoint, payload=None, **kwargs):
            if endpoint == 'appAssetLibraryPlacements/placement-1?include=image':
                self.client.placements['placement-1']['attributes']['state'] = 'ACTIVE'
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']}, cleanup_spec_mismatch=True)
        self.assertIn('placement-1', self.client.placements)

    def test_restoration_lost_create_reply_reconciles_and_restores_all_original_images(self):
        self.client.seed_placement('cs', 'old-first')
        self.client.seed_placement('cs', 'old-second')
        original = self.client._request
        lost_reply = False

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal lost_reply
            if method == 'POST' and endpoint == 'appAssetLibraryPlacements':
                image_id = payload['data']['relationships']['image']['data']['id']
                if image_id == 'batch-1':
                    raise RuntimeError('new placement rejected')
                response = original(method, endpoint, payload, **kwargs)
                if image_id == 'old-first' and not lost_reply:
                    lost_reply = True
                    raise RuntimeError('restoration reply lost')
                return response
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request), patch('asset_library.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'new placement rejected'):
                self.uploader.replace_existing_localization('cs', {'medium-runtime': ['batch-1']})
        self.assertEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()],
                         ['old-first', 'old-second'])
