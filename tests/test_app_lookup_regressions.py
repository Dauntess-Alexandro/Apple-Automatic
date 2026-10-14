import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from PySide6.QtCore import Qt

import app_store_ppo_automation as app
from asset_library_selection import validate_version


class VersionLookupTests(unittest.TestCase):
    def client_with_versions(self, records):
        client = app.ASCClient.__new__(app.ASCClient)
        client.app_id = 'test-app'
        client.logger = lambda _message: None

        def request(method, endpoint):
            self.assertEqual(method, 'GET')
            query = parse_qs(endpoint.partition('?')[2])
            self.assertNotIn('sort', query, 'Apple does not support sorting this endpoint')
            states = query.get('filter[appStoreState]', [''])[0].split(',')
            platform = query.get('filter[platform]', [None])[0]
            versions = [record for record in records
                        if record['attributes']['appStoreState'] in states
                        and (platform is None or record['attributes']['platform'] == platform)]
            if query.get('sort') == ['-createdDate']:
                versions.sort(key=lambda record: record['attributes']['createdDate'], reverse=True)
            return {'data': versions}

        client._request = request
        return client

    def version(self, identifier, state, date='2026-10-09', platform='IOS'):
        return {'id': identifier, 'attributes': {
            'appStoreState': state, 'platform': platform, 'createdDate': date,
        }}

    def test_rejected_versions_are_found_and_allowed_for_screenshots(self):
        for state in ['DEVELOPER_REJECTED', 'REJECTED', 'METADATA_REJECTED', 'INVALID_BINARY']:
            with self.subTest(state=state):
                client = self.client_with_versions([self.version('rejected-version', state)])
                self.assertEqual(client.get_latest_app_store_version(), 'rejected-version')
                validation_client = Mock()
                validation_client._request.return_value = {'data': self.version('rejected-version', state)}
                self.assertEqual(validate_version(validation_client, 'rejected-version', 'version')['appStoreState'], state)

    def test_editable_draft_is_preferred_to_newer_published_version(self):
        client = self.client_with_versions([
            self.version('live', 'READY_FOR_SALE', '2026-10-09'),
            self.version('draft', 'DEVELOPER_REJECTED', '2026-10-08'),
        ])
        self.assertEqual(client.get_latest_app_store_version(), 'draft')

    def test_latest_editable_ios_version_is_selected(self):
        client = self.client_with_versions([
            self.version('old', 'PREPARE_FOR_SUBMISSION', '2026-10-07'),
            self.version('mac', 'DEVELOPER_REJECTED', '2026-10-09', 'MAC_OS'),
            self.version('current', 'DEVELOPER_REJECTED', '2026-10-08'),
        ])
        self.assertEqual(client.get_latest_app_store_version(), 'current')

    def test_published_version_remains_available_for_reading_and_ppo(self):
        client = self.client_with_versions([self.version('live', 'READY_FOR_SALE')])
        self.assertEqual(client.get_latest_app_store_version(), 'live')

    def test_review_version_is_not_editable(self):
        client = Mock()
        client._request.return_value = {'data': self.version('review', 'IN_REVIEW')}
        with self.assertRaises(ValueError):
            validate_version(client, 'review', 'version')


class WorkerCompletionTests(unittest.TestCase):
    def test_app_lookup_workers_only_finish_after_run_returns(self):
        credentials = {'issuer': 'i', 'key_id': 'k', 'p8_path': 'unused', 'app_id': 'app'}
        for worker_type in [app.FetchAppsWorker, app.FetchAppVersionWorker, app.FetchMetadataSourceWorker,
                            app.FetchPrimaryLocaleWorker, app.RefreshLocalesWorker]:
            with self.subTest(worker=worker_type.__name__):
                reached_tail = threading.Event()
                release_tail = threading.Event()
                finished_events = []

                class HeldWorker(worker_type):
                    def run(self):
                        super().run()
                        reached_tail.set()
                        release_tail.wait(5)

                worker = HeldWorker(credentials)
                worker.finished.connect(lambda: finished_events.append('finished'), Qt.DirectConnection)
                with patch.object(app, 'ASCClient') as client:
                    client.return_value.list_apps.return_value = []
                    client.return_value.get_latest_app_store_version_info.return_value = {}
                    client.return_value.get_app_primary_locale.return_value = 'en-US'
                    client.return_value.get_version_locales.return_value = []
                    try:
                        worker.start()
                        self.assertTrue(reached_tail.wait(3))
                        self.assertEqual(finished_events, [], 'finished emitted while run is still executing')
                    finally:
                        release_tail.set()
                        self.assertTrue(worker.wait(3000))
                self.assertEqual(finished_events, ['finished'])
