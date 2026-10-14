import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton
from PIL import Image

import app_store_ppo_automation as app
import asset_library_selection as selection
import test_library_selection_dialog as dialog_tests
from test_library_selection_workers import SelectionClient
from test_existing_asset_library import library_image
from asset_library_import import ScreenshotImportSource


class EmbeddedLibraryTests(unittest.TestCase):
    setUpClass = classmethod(dialog_tests.LibraryDialogTests.setUpClass.__func__)

    def setUp(self):
        self.assertTrue(hasattr(selection, 'LibrarySelectionWidget'), 'Embedded Asset Library is missing')
        self.client = SelectionClient()
        self.widget = selection.LibrarySelectionWidget(lambda logger: self.client)
        self.addCleanup(self.widget.close)
        refresh = selection.LibraryRefreshWorker(lambda logger: self.client, 'version')
        refresh.loaded.connect(self.widget.apply_snapshot)
        refresh.run()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name) / 'screens'
        self.folder.mkdir()
        Image.new('RGB', (1206, 2622), 'green').save(self.folder / '1.png')

    def imported(self):
        source = ScreenshotImportSource(self.folder)
        result = {'source': source, 'images': [library_image('new-2'), library_image('new-1')],
                  'batches': {'medium-runtime': ['new-2', 'new-1']}}
        self.widget.apply_import_result(result)
        return result

    def test_import_selects_only_new_batch_in_original_order_without_assignment(self):
        self.imported()
        self.assertFalse(isinstance(self.widget, QDialog))
        self.assertEqual(self.widget.selected_batches(), {'medium-runtime': ['new-2', 'new-1']})
        self.assertEqual(self.client.placements, {})
        self.assertTrue(self.folder.exists())
        self.assertEqual(self.widget.chosen_list.count(), 2)
        self.assertTrue(self.widget.assign_button.isEnabled())

    def test_import_before_first_refresh_preserves_new_batch_after_loading_locales(self):
        self.widget.snapshot = None
        self.imported()
        worker = selection.LibraryRefreshWorker(lambda logger: self.client, 'version')
        worker.loaded.connect(self.widget.apply_snapshot)
        worker.run()
        self.assertEqual(self.widget.selected_batches(), {'medium-runtime': ['new-2', 'new-1']})
        self.assertEqual(len(self.widget.selected_targets()), 2)

    def test_partial_assignment_preserves_source_then_full_success_deletes_it(self):
        self.imported()
        self.widget.assignment_batches = {'medium-runtime': ['new-2', 'new-1']}
        self.widget.assignment_target_ids = {'loc-en', 'loc-cs'}
        self.widget.show_results([
            {'id': 'loc-en', 'label': 'en-US', 'status': 'success', 'message': 'ok'},
            {'id': 'loc-cs', 'label': 'cs', 'status': 'manual_check', 'message': 'timeout'},
        ])
        self.assertTrue(self.folder.exists())
        self.widget.show_results([
            {'id': 'loc-en', 'label': 'en-US', 'status': 'success', 'message': 'ok'},
            {'id': 'loc-cs', 'label': 'cs', 'status': 'success', 'message': 'ok'},
        ])
        self.assertFalse(self.folder.exists())

    def test_missing_results_or_changed_batch_never_deletes_source(self):
        self.imported()
        self.widget.assignment_batches = {'medium-runtime': ['new-2', 'new-1']}
        self.widget.assignment_target_ids = {'loc-en', 'loc-cs'}
        self.widget.show_results([{'id': 'loc-en', 'label': 'en-US', 'status': 'success', 'message': 'ok'}])
        self.assertTrue(self.folder.exists())
        self.widget.assignment_target_ids = {'loc-en'}
        self.widget.assignment_batches = {'medium-runtime': ['old-image']}
        self.widget.show_results([{'id': 'loc-en', 'label': 'en-US', 'status': 'success', 'message': 'ok'}])
        self.assertTrue(self.folder.exists())

    def test_selecting_another_folder_does_not_delete_it_after_previous_assignment(self):
        self.imported()
        other = Path(self.directory.name) / 'other'
        other.mkdir()
        Image.new('RGB', (1206, 2622), 'blue').save(other / '1.png')
        self.widget.select_import_source(other)
        self.widget.assignment_batches = {'medium-runtime': ['new-2', 'new-1']}
        self.widget.assignment_target_ids = {'loc-en'}
        self.widget.show_results([{'id': 'loc-en', 'label': 'en-US', 'status': 'success', 'message': 'ok'}])
        self.assertTrue((other / '1.png').exists())

    def test_real_upload_then_existing_assignment_cleans_source_only_after_all_locales(self):
        original_request = self.client._request

        def request(method, endpoint, payload=None, **kwargs):
            response = original_request(method, endpoint, payload, **kwargs)
            if method == 'POST' and endpoint == 'appAssetLibraryImages':
                response['data']['attributes']['imageAsset'] = {'width': 1206, 'height': 2622}
            return response

        self.client._request = request
        self.widget.select_import_source(self.folder)
        with patch('asset_library.requests.request', return_value=Mock()):
            self.widget.start_import()
            self.widget.worker.wait(5000)
            self.application.processEvents()
        self.assertTrue(self.folder.exists())
        self.assertEqual(self.client.placements, {})
        self.assertEqual(len(self.client.images), 3)
        imported = self.widget.selected_batches()['medium-runtime']
        self.assertEqual(len(imported), 1)
        with patch('asset_library_selection.QMessageBox.question', return_value=selection.QMessageBox.Yes):
            self.widget.assign_selected()
            self.widget.worker.wait(5000)
            self.application.processEvents()
        self.assertFalse(self.folder.exists())
        self.assertEqual(len(self.client.images), 3)
        self.assertEqual(self.widget.results_table.rowCount(), 2)
        for target in ['loc-en', 'loc-cs']:
            actual = [placement['relationships']['image']['data']['id']
                      for placement in self.client.placements.values() if placement['localization'] == target]
            self.assertEqual(actual, imported)

    def test_changing_app_clears_previous_import_selection_and_preserves_source(self):
        self.imported()
        self.widget.clear_context()
        self.assertEqual(self.widget.selected_batches(), {})
        self.assertIsNone(self.widget.pending_import)
        self.assertTrue(self.folder.exists())
        self.assertIsNone(self.widget.snapshot)

    def test_mixed_device_source_is_deleted_after_each_group_is_confirmed_for_all_locales(self):
        self.imported()
        self.widget.pending_import['batches'] = {'medium-runtime': ['new-2'], 'large-runtime': ['new-1']}
        self.widget.assignment_target_ids = {'loc-en', 'loc-cs'}
        rows = [{'id': 'loc-en', 'label': 'en-US', 'status': 'success', 'message': 'ok'},
                {'id': 'loc-cs', 'label': 'cs', 'status': 'success', 'message': 'ok'}]
        self.widget.assignment_batches = {'medium-runtime': ['new-2']}
        self.widget.show_results(rows)
        self.assertTrue(self.folder.exists())
        self.widget.assignment_batches = {'large-runtime': ['new-1']}
        self.widget.show_results(rows)
        self.assertFalse(self.folder.exists())

    def test_invalid_source_choice_clears_previous_upload_choice(self):
        self.widget.select_import_source(self.folder)
        self.assertTrue(self.widget.import_button.isEnabled())
        self.widget.select_import_source(Path(self.directory.name) / 'missing.zip')
        self.assertIsNone(self.widget.import_source)
        self.assertFalse(self.widget.import_button.isEnabled())
        self.assertEqual(self.widget.import_preview.count(), 0)

    def test_all_import_controls_disabled_during_assignment(self):
        self.widget.set_busy(True)
        self.assertFalse(self.widget.import_folder_button.isEnabled())
        self.assertFalse(self.widget.import_zip_button.isEnabled())
        self.assertFalse(self.widget.import_button.isEnabled())

    def test_main_screen_contains_embedded_library_and_no_legacy_upload_actions(self):
        with patch.object(app, 'ensure_flags_downloaded'), \
                patch.object(app.MainWindow, '_has_complete_api_credentials', return_value=False), \
                patch.object(app.MainWindow, '_save_env_to_file'), \
                patch.object(app.MainWindow, '_refresh_accounts_roster_status'):
            window = app.MainWindow()
        self.addCleanup(window.close)
        panels = window.tab_screens_upload.findChildren(selection.LibrarySelectionWidget)
        self.assertEqual(len(panels), 1)
        buttons = [button.text() for button in window.tab_screens_upload.findChildren(QPushButton)]
        self.assertFalse(any('Figma' in text or 'ЗАГРУЗИТЬ СКРИНШОТЫ В APPLE' in text for text in buttons))
        self.assertTrue(any('папку' in text.lower() for text in buttons))
        self.assertTrue(any('zip' in text.lower() for text in buttons))


if __name__ == '__main__':
    unittest.main()
