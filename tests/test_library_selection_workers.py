import copy
import threading

import requests
import unittest
from unittest.mock import patch

from asset_library_selection import LibraryAssignmentWorker, LibraryRefreshWorker
from test_existing_asset_library import ExistingAssetClient


class SelectionClient(ExistingAssetClient):
    def __init__(self):
        super().__init__()
        self.records = {
            'apps/app/appStoreVersions': [{'id': 'version', 'attributes': {
                'platform': 'IOS', 'appStoreState': 'PREPARE_FOR_SUBMISSION', 'versionString': '2.0',
            }}],
            'appStoreVersions/version/appStoreVersionLocalizations': [
                {'id': 'loc-en', 'attributes': {'locale': 'en-US'}},
                {'id': 'loc-cs', 'attributes': {'locale': 'cs'}},
            ],
            'appStoreVersions/version/appStoreVersionExperiments': [
                {'id': 'experiment', 'attributes': {'name': 'Same test'}},
            ],
            'appStoreVersionExperiments/experiment/appStoreVersionExperimentTreatments': [
                {'id': 'treatment-1', 'attributes': {'name': 'Same treatment'}},
                {'id': 'treatment-2', 'attributes': {'name': 'Same treatment'}},
            ],
            'appStoreVersionExperimentTreatments/treatment-1/appStoreVersionExperimentTreatmentLocalizations': [
                {'id': 'ppo-en-1', 'attributes': {'locale': 'en-US'}},
            ],
            'appStoreVersionExperimentTreatments/treatment-2/appStoreVersionExperimentTreatmentLocalizations': [
                {'id': 'ppo-en-2', 'attributes': {'locale': 'en-US'}},
            ],
        }
        self.fail_localization = None
        self.version_attributes = {'platform': 'IOS', 'appStoreState': 'PREPARE_FOR_SUBMISSION'}

    def get_latest_app_store_version(self):
        return 'version'

    def _request(self, method, endpoint, payload=None, **kwargs):
        resource = endpoint.partition('?')[0]
        if method == 'GET' and resource == 'appStoreVersions/version':
            self.calls.append((method, endpoint, payload))
            return {'data': {'id': 'version', 'attributes': dict(self.version_attributes)}}
        if method == 'GET' and len(resource.split('/')) == 2 and resource.split('/')[0] in {
            'appStoreVersionLocalizations', 'appStoreVersionExperimentTreatmentLocalizations',
        }:
            self.calls.append((method, endpoint, payload))
            identifier = resource.split('/')[1]
            for parent_path, records in self.records.items():
                record = next((item for item in records if item['id'] == identifier), None)
                if record:
                    relationship = 'appStoreVersion' if parent_path.startswith('appStoreVersions/') else 'appStoreVersionExperimentTreatment'
                    return {'data': {**copy.deepcopy(record), 'relationships': {
                        relationship: {'data': {'id': parent_path.split('/')[1]}},
                    }}}
            raise ValueError('Localization no longer exists')
        if method == 'GET' and resource in self.records:
            self.calls.append((method, endpoint, payload))
            return {'data': copy.deepcopy(self.records[resource])}
        if endpoint == 'appAssetLibraryPlacements' and method == 'POST':
            relationships = payload['data']['relationships']
            if any(value['data']['id'] == self.fail_localization for key, value in relationships.items() if key != 'image'):
                raise RuntimeError('placement write failed')
        return super()._request(method, endpoint, payload, **kwargs)


class LibraryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.client = SelectionClient()

    def refresh(self, mode='version'):
        snapshots, errors = [], []
        worker = LibraryRefreshWorker(lambda logger: self.client, mode)
        worker.loaded.connect(snapshots.append)
        worker.error.connect(errors.append)
        worker.run()
        self.assertEqual(errors, [])
        return snapshots[0]

    def assign(self, targets, batches=None, **kwargs):
        worker = LibraryAssignmentWorker(lambda logger: self.client, targets,
            {'medium-runtime': ['batch-2', 'batch-1']} if batches is None else batches, **kwargs)
        rows = []
        worker.completed.connect(rows.extend)
        with patch('asset_library.time.sleep'):
            worker.run()
        return rows

    def test_refresh_reads_only_and_exposes_existing_locale_ids(self):
        snapshot = self.refresh()
        self.assertEqual([target['id'] for target in snapshot['targets']], ['loc-en', 'loc-cs'])
        self.assertEqual([image['id'] for image in snapshot['images']], ['batch-1', 'batch-2'])
        self.assertEqual(snapshot['groups']['medium-runtime']['max_count'], 10)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.client.calls))

    def test_assigns_only_selected_existing_version_locale_and_preserves_images(self):
        target = self.refresh()['targets'][1]
        rows = self.assign([target])
        self.assertEqual([(row['locale'], row['status']) for row in rows], [('cs', 'success')])
        self.assertEqual([item['localization'] for item in self.client.placements.values()], ['loc-cs', 'loc-cs'])
        self.assertEqual([item['relationships']['image']['data']['id'] for item in self.client.placements.values()], ['batch-2', 'batch-1'])
        self.assertFalse(any('appAssetLibraryImages' in endpoint and method != 'GET'
                             for method, endpoint, _ in self.client.calls))

    def test_ppo_uses_ids_not_same_named_treatments_and_never_changes_test_metadata(self):
        snapshot = self.refresh('ppo')
        self.assertEqual([target['id'] for target in snapshot['targets']], ['ppo-en-1', 'ppo-en-2'])
        rows = self.assign(snapshot['targets'])
        self.assertEqual([row['status'] for row in rows], ['success', 'success'])
        self.assertTrue(all('appStoreVersionExperimentTreatmentLocalization' in item['relationships']
                            for item in self.client.placements.values()))
        self.assertTrue(all(method == 'GET' or endpoint.startswith(('appAssetLibraryPlacements', 'appAssetLibraryPlacementOrderingRequests'))
                            for method, endpoint, _ in self.client.calls))

    def test_missing_locale_is_failed_without_creation_while_other_locale_continues(self):
        targets = self.refresh()['targets']
        self.client.records['appStoreVersions/version/appStoreVersionLocalizations'].pop(0)
        rows = self.assign(targets)
        self.assertEqual([row['status'] for row in rows], ['failed', 'success'])
        self.assertTrue(all(item['localization'] == 'loc-cs' for item in self.client.placements.values()))

    def test_one_mutation_failure_is_manual_check_not_global_success_and_other_locale_continues(self):
        targets = self.refresh()['targets']
        self.client.fail_localization = 'loc-en'
        rows = self.assign(targets)
        self.assertEqual([row['status'] for row in rows], ['manual_check', 'success'])
        self.assertIn('placement write failed', rows[0]['message'])

    def test_invalid_selection_fails_all_targets_without_any_mutation(self):
        targets = self.refresh()['targets']
        self.client.calls.clear()
        rows = self.assign(targets, {'medium-runtime': ['not-in-library']})
        self.assertEqual([row['status'] for row in rows], ['failed', 'failed'])
        self.assertTrue(all(method == 'GET' for method, _, _ in self.client.calls))

    def test_foreign_target_and_duplicate_target_are_rejected_without_writes(self):
        target = self.refresh()['targets'][0]
        for targets in [[{**target, 'parent_id': 'other-version'}], [target, target]]:
            self.client.calls.clear()
            rows = self.assign(targets)
            self.assertTrue(rows)
            self.assertTrue(all(row['status'] == 'failed' for row in rows))
            self.assertTrue(all(method == 'GET' for method, _, _ in self.client.calls))

    def test_remaining_invalid_group_is_manual_check_even_when_selected_group_succeeds(self):
        target = self.refresh()['targets'][0]
        self.client.seed_placement(target['id'], 'bad-old', group='large-runtime')
        self.client.placements['placement-1']['attributes'].update(state='INVALID', invalidReasons=['OTHER'])
        rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'manual_check')
        self.assertIn('OTHER', rows[0]['message'])

    def test_refresh_error_is_reported_without_writes(self):
        worker = LibraryRefreshWorker(lambda logger: self.client, 'unsupported')
        errors = []
        worker.error.connect(errors.append)
        worker.run()
        self.assertTrue(errors)
        self.assertEqual(self.client.calls, [])

    def test_version_assignment_rejects_published_or_non_ios_version_before_writes(self):
        targets = self.refresh()['targets']
        for attributes in [{'platform': 'IOS', 'appStoreState': 'READY_FOR_SALE'},
                           {'platform': 'MAC_OS', 'appStoreState': 'PREPARE_FOR_SUBMISSION'}]:
            self.client.version_attributes = attributes
            self.client.calls.clear()
            rows = self.assign(targets)
            self.assertTrue(all(row['status'] == 'failed' for row in rows))
            self.assertTrue(all(method == 'GET' for method, _, _ in self.client.calls))

    def test_ppo_published_version_is_allowed_without_reloading_entire_tree_per_locale(self):
        self.client.version_attributes['appStoreState'] = 'READY_FOR_SALE'
        targets = self.refresh('ppo')['targets']
        self.client.calls.clear()
        rows = self.assign(targets)
        self.assertEqual([row['status'] for row in rows], ['success', 'success'])
        tree_reads = [endpoint for method, endpoint, _ in self.client.calls
                      if method == 'GET' and endpoint == 'appStoreVersions/version/appStoreVersionExperiments?limit=200']
        self.assertEqual(len(tree_reads), 1)
        locale_reads = [endpoint for method, endpoint, _ in self.client.calls
                        if method == 'GET' and endpoint.startswith('appStoreVersionExperimentTreatmentLocalizations/')
                        and '?include=appStoreVersionExperimentTreatment' in endpoint]
        self.assertEqual(len(locale_reads), 2)
        library_reads = [endpoint for method, endpoint, _ in self.client.calls
                         if method == 'GET' and endpoint == 'appAssetLibraries/library/images?limit=200']
        self.assertEqual(len(library_reads), 1)
        selected_image_reads = [endpoint for method, endpoint, _ in self.client.calls
                                if method == 'GET' and endpoint.startswith('appAssetLibraryImages/')]
        self.assertEqual(len(selected_image_reads), 4)

    def test_ppo_discovery_includes_existing_treatments_of_published_and_draft_versions(self):
        self.client.records['apps/app/appStoreVersions'].append({'id': 'published', 'attributes': {
            'platform': 'IOS', 'appStoreState': 'READY_FOR_SALE', 'versionString': '1.0',
        }})
        self.client.records.update({
            'appStoreVersions/published/appStoreVersionExperiments': [{'id': 'published-test', 'attributes': {'name': 'Same test'}}],
            'appStoreVersionExperiments/published-test/appStoreVersionExperimentTreatments': [
                {'id': 'published-treatment', 'attributes': {'name': 'Same treatment'}}],
            'appStoreVersionExperimentTreatments/published-treatment/appStoreVersionExperimentTreatmentLocalizations': [
                {'id': 'published-locale', 'attributes': {'locale': 'en-US'}}],
        })
        snapshot = self.refresh('ppo')
        self.assertEqual([target['id'] for target in snapshot['targets']], ['ppo-en-1', 'ppo-en-2', 'published-locale'])
        self.assertEqual(snapshot['targets'][-1]['version_id'], 'published')
        self.assertIn('1.0', snapshot['targets'][-1]['label'])


class ParallelLibraryWorkerTests(unittest.TestCase):
    setUp = LibraryWorkerTests.setUp
    refresh = LibraryWorkerTests.refresh
    assign = LibraryWorkerTests.assign

    def test_twelve_locales_reach_assignment_concurrently(self):
        records = [{'id': f'loc-{i}', 'attributes': {'locale': f'locale-{i}'}} for i in range(12)]
        self.client.records['appStoreVersions/version/appStoreVersionLocalizations'] = records
        targets = self.refresh()['targets']
        barrier = threading.Barrier(12, timeout=3)
        original = self.client._request

        def request(method, endpoint, payload=None, **kwargs):
            if method == 'GET' and endpoint.startswith('appStoreVersionLocalizations/') and '?include=' in endpoint:
                barrier.wait()
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign(targets)
        self.assertEqual([row['status'] for row in rows], ['success'] * 12)
        self.assertEqual([row['id'] for row in rows], [target['id'] for target in targets])

    def test_transient_locale_read_is_retried_before_assignment(self):
        target = self.refresh()['targets'][0]
        original = self.client._request
        attempts = 0

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal attempts
            if endpoint.startswith('appStoreVersionLocalizations/') and '?include=' in endpoint:
                attempts += 1
                if attempts < 3:
                    raise requests.exceptions.Timeout('temporary network failure')
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'success')
        self.assertEqual(attempts, 3)
        self.assertEqual(len(self.client.placements), 2)

    def test_transient_failures_stop_after_four_attempts(self):
        target = self.refresh()['targets'][0]
        original = self.client._request
        attempts = 0

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal attempts
            if endpoint.startswith('appStoreVersionLocalizations/') and '?include=' in endpoint:
                attempts += 1
                raise requests.exceptions.ConnectionError('offline')
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'failed')
        self.assertEqual(attempts, 4)
        self.assertEqual(self.client.placements, {})

    def test_failed_order_is_retried_after_confirmed_restoration(self):
        target = self.refresh()['targets'][0]
        original = self.client._request
        failed = False
        attempts = 0

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal failed, attempts
            if endpoint.startswith('appStoreVersionLocalizations/') and '?include=' in endpoint:
                attempts += 1
            if method == 'POST' and endpoint == 'appAssetLibraryPlacementOrderingRequests' and not failed:
                failed = True
                raise requests.exceptions.Timeout('order timeout')
            # Force verification to fail once as well, then allow rollback.
            if method == 'GET' and '/placements?' in endpoint and failed is True:
                failed = 'recovered'
                raise requests.exceptions.Timeout('verification timeout')
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'success')
        self.assertEqual(attempts, 2)
        self.assertEqual(len(self.client.placements), 2)

    def test_ambiguous_create_is_not_replayed(self):
        target = self.refresh()['targets'][0]
        original = self.client._request
        attempts = 0

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal attempts
            if method == 'POST' and endpoint == 'appAssetLibraryPlacements':
                attempts += 1
                raise requests.exceptions.Timeout('unknown create outcome')
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'manual_check')
        self.assertEqual(attempts, 1)

    def test_transient_image_read_is_retried_without_duplicate_placements(self):
        target = self.refresh()['targets'][0]
        original = self.client._request
        attempts = 0

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal attempts
            if endpoint == 'appAssetLibraryImages/batch-1':
                attempts += 1
                if attempts == 1:
                    raise requests.exceptions.Timeout('image read timeout')
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'success')
        self.assertEqual(attempts, 2)
        self.assertEqual(len(self.client.placements), 2)

    def test_transient_read_on_retry_still_uses_remaining_attempts(self):
        target = self.refresh()['targets'][0]
        original = self.client._request
        locale_reads = 0
        image_reads = 0

        def request(method, endpoint, payload=None, **kwargs):
            nonlocal locale_reads, image_reads
            if endpoint.startswith('appStoreVersionLocalizations/') and '?include=' in endpoint:
                locale_reads += 1
                if locale_reads == 2:
                    raise requests.exceptions.Timeout('temporary locale read failure')
            if endpoint == 'appAssetLibraryImages/batch-1':
                image_reads += 1
                if image_reads == 1:
                    raise requests.exceptions.Timeout('temporary image read failure')
            return original(method, endpoint, payload, **kwargs)

        with patch.object(self.client, '_request', side_effect=request):
            rows = self.assign([target])
        self.assertEqual(rows[0]['status'], 'success')
        self.assertEqual(locale_reads, 3)
        self.assertEqual(len(self.client.placements), 2)
