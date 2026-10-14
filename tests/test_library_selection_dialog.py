import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QFileInfo, QCoreApplication, QLibraryInfo, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

import app_store_ppo_automation as app
from asset_library_selection import LibrarySelectionDialog, LibraryRefreshWorker
from test_existing_asset_library import library_image
from test_library_selection_workers import SelectionClient


class LibraryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = os.path.join(QLibraryInfo.path(QLibraryInfo.PluginsPath), 'platforms')
        if any(QFileInfo(os.path.join(source, name)).isHidden() for name in os.listdir(source)):
            directory = tempfile.TemporaryDirectory(prefix='ppo-qt-plugins-')
            cls.addClassCleanup(directory.cleanup)
            destination = os.path.join(directory.name, 'platforms')
            os.mkdir(destination)
            for name in os.listdir(source):
                shutil.copyfile(os.path.join(source, name), os.path.join(destination, name))
            QCoreApplication.addLibraryPath(directory.name)
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.client = SelectionClient()
        self.dialog = LibrarySelectionDialog(lambda logger: self.client, 'version')
        self.addCleanup(self.dialog.close)
        worker = LibraryRefreshWorker(lambda logger: self.client, 'version')
        worker.loaded.connect(self.dialog.apply_snapshot)
        worker.run()
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('medium-runtime'))

    def test_duplicate_names_require_manual_selection_but_locales_are_checked(self):
        self.assertEqual(self.dialog.selected_batches(), {})
        self.assertEqual([target['id'] for target in self.dialog.selected_targets()], ['loc-en', 'loc-cs'])
        self.assertFalse(self.dialog.assign_button.isEnabled())
        self.assertIn('одинаковые имена', self.dialog.notice.text())
        labels = [self.dialog.images_table.item(row, 0).text() for row in range(self.dialog.images_table.rowCount())]
        self.assertEqual(labels, ['same-name.png', 'same-name.png'])
        displayed = [self.dialog.images_table.item(row, 4).text() for row in range(self.dialog.images_table.rowCount())]
        self.assertEqual(displayed, ['batch-1', 'batch-2'])

    def test_refresh_detects_medium_and_selects_all_compatible_images_in_natural_order(self):
        images = [library_image('ten', fileName='10.png'), library_image('two', fileName='2.png'),
                  library_image('one', fileName='1.png'), library_image('bad', fileName='3.png', state='INVALID')]
        snapshot = dict(self.dialog.snapshot, images=images)
        snapshot['groups'] = dict(reversed(list(snapshot['groups'].items())))
        self.dialog.apply_snapshot(snapshot)
        self.assertEqual(self.dialog.group_combo.currentData(), 'medium-runtime')
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['one', 'two', 'ten']})
        self.assertEqual(len(self.dialog.selected_targets()), 2)
        self.assertTrue(self.dialog.assign_button.isEnabled())
        self.assertIn('Dynamic Island', self.dialog.group_combo.currentText())
        self.assertNotIn('IPHONE_APP_STORE', self.dialog.group_combo.currentText())

    def test_detects_other_category_from_spec_and_dimensions(self):
        images = [library_image('large', fileName='1.png', specId='large-spec',
                                imageAsset={'width': 1284, 'height': 2778})]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.assertEqual(self.dialog.group_combo.currentData(), 'large-runtime')
        self.assertEqual(self.dialog.selected_batches(), {'large-runtime': ['large']})

    def test_missing_file_name_requires_manual_selection_without_crashing(self):
        images = [library_image('unnamed', fileName=None)]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.assertEqual(self.dialog.selected_batches(), {})
        self.assertIn('имени', self.dialog.notice.text())

    def test_medium_is_preferred_when_other_compatible_categories_also_exist(self):
        images = [library_image('medium', fileName='1.png'),
                  library_image('large-1', fileName='1.png', specId='large-spec',
                                imageAsset={'width': 1284, 'height': 2778}),
                  library_image('large-2', fileName='2.png', specId='large-spec',
                                imageAsset={'width': 1284, 'height': 2778})]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['medium']})

    def test_single_ppo_treatment_checks_all_its_existing_locales(self):
        self.client.records['appStoreVersionExperiments/experiment/appStoreVersionExperimentTreatments'].pop()
        self.client.records['appStoreVersionExperimentTreatments/treatment-1/appStoreVersionExperimentTreatmentLocalizations'].append(
            {'id': 'ppo-cs-1', 'attributes': {'locale': 'cs'}})
        dialog = LibrarySelectionDialog(lambda logger: self.client, 'ppo')
        self.addCleanup(dialog.close)
        worker = LibraryRefreshWorker(lambda logger: self.client, 'ppo')
        worker.loaded.connect(dialog.apply_snapshot)
        worker.run()
        self.assertEqual([target['id'] for target in dialog.selected_targets()], ['ppo-en-1', 'ppo-cs-1'])

    def test_more_than_limit_is_not_truncated_or_automatically_assigned(self):
        images = [library_image(str(index), fileName=f'{index}.png') for index in range(11)]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.assertEqual(self.dialog.selected_batches(), {})
        self.assertFalse(self.dialog.assign_button.isEnabled())
        self.assertIn('11', self.dialog.notice.text())
        self.assertIn('10', self.dialog.notice.text())

    def test_manual_order_and_removals_survive_category_switch(self):
        images = [library_image('two', fileName='2.png'), library_image('one', fileName='1.png')]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.dialog.chosen_list.setCurrentRow(0)
        self.dialog.remove_selected_image()
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('large-runtime'))
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('medium-runtime'))
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['two']})

    def test_category_switch_never_assigns_images_hidden_in_another_category(self):
        images = [library_image('medium', fileName='1.png'),
                  library_image('large', fileName='1.png', specId='large-spec',
                                imageAsset={'width': 1284, 'height': 2778})]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('large-runtime'))
        self.assertEqual(self.dialog.selected_batches(), {'large-runtime': ['large']})
        self.dialog.chosen_list.setCurrentRow(0)
        self.dialog.remove_selected_image()
        self.assertEqual(self.dialog.selected_batches(), {})
        self.assertFalse(self.dialog.assign_button.isEnabled())
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('medium-runtime'))
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['medium']})

    def test_ppo_checks_all_locales_only_after_choosing_one_treatment(self):
        dialog = LibrarySelectionDialog(lambda logger: self.client, 'ppo')
        self.addCleanup(dialog.close)
        worker = LibraryRefreshWorker(lambda logger: self.client, 'ppo')
        worker.loaded.connect(dialog.apply_snapshot)
        worker.run()
        self.assertEqual(dialog.selected_targets(), [])
        dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData('treatment-2'))
        self.assertEqual([target['id'] for target in dialog.selected_targets()], ['ppo-en-2'])
        dialog.check_visible_targets(Qt.Checked)
        self.assertEqual([target['id'] for target in dialog.selected_targets()], ['ppo-en-2'])
        dialog.scope_combo.setCurrentIndex(dialog.scope_combo.findData('treatment-1'))
        self.assertEqual([target['id'] for target in dialog.selected_targets()], ['ppo-en-1'])

    def test_compact_layout_reserves_space_for_images_and_hides_empty_results(self):
        self.dialog.show()
        self.application.processEvents()
        self.assertGreaterEqual(self.dialog.images_table.viewport().height(), 180)
        self.assertTrue(self.dialog.results_table.isHidden())
        self.assertLess(self.dialog.group_combo.sizeHint().width(), 650)

    def add_row(self, index):
        self.dialog.images_table.selectRow(index)
        self.dialog.add_selected_image()

    def test_explicit_add_move_remove_preserves_order_and_group_selection(self):
        self.add_row(1)
        self.add_row(0)
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['batch-2', 'batch-1']})
        self.dialog.chosen_list.setCurrentRow(1)
        self.dialog.move_selected_image(-1)
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['batch-1', 'batch-2']})
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('large-runtime'))
        self.assertEqual(self.dialog.chosen_list.count(), 0)
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('medium-runtime'))
        self.dialog.chosen_list.setCurrentRow(0)
        self.dialog.remove_selected_image()
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': ['batch-2']})

    def test_limit_and_duplicate_ids_are_enforced_in_dialog(self):
        images = [library_image(f'image-{index}') for index in range(11)]
        snapshot = dict(self.dialog.snapshot, images=images)
        self.dialog.apply_snapshot(snapshot)
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('medium-runtime'))
        for index in range(11):
            self.add_row(index)
        self.assertEqual(self.dialog.chosen_list.count(), 10)
        self.assertIn('10', self.dialog.notice.text())
        self.add_row(0)
        self.assertEqual(self.dialog.chosen_list.count(), 10)

    def test_unready_image_cannot_be_added_and_reason_is_visible(self):
        images = [library_image('bad', state='INVALID', stateDetails='processing failed')]
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=images))
        self.add_row(0)
        self.assertEqual(self.dialog.selected_batches(), {})
        self.assertIn('INVALID', self.dialog.images_table.item(0, 2).text())
        self.assertIn('processing failed', self.dialog.images_table.item(0, 2).text())

    def test_long_image_ids_are_short_visible_full_in_tooltip_and_preserved_in_selection(self):
        identifier = '01234567-89ab-cdef-0123-456789abcdef'
        image = library_image(identifier, createdDate='2026-10-09T10:00:00Z')
        self.dialog.apply_snapshot(dict(self.dialog.snapshot, images=[image]))
        self.dialog.group_combo.setCurrentIndex(self.dialog.group_combo.findData('medium-runtime'))
        item = self.dialog.images_table.item(0, 4)
        self.assertLess(len(item.text()), 16)
        self.assertIn(identifier, item.toolTip())
        self.assertEqual(item.data(Qt.UserRole), identifier)
        self.assertIn('2026-10-09', self.dialog.images_table.item(0, 3).text())
        self.add_row(0)
        self.assertEqual(self.dialog.selected_batches(), {'medium-runtime': [identifier]})

    def test_locale_is_first_and_parent_ids_are_in_tooltip(self):
        item = self.dialog.targets_list.item(0)
        self.assertTrue(item.text().startswith('en-US'))
        self.assertIn('version', item.toolTip())

    def test_compact_locale_labels_still_support_search_by_full_identity(self):
        self.dialog.target_search.setText('loc-cs')
        self.assertTrue(self.dialog.targets_list.item(0).isHidden())
        self.assertFalse(self.dialog.targets_list.item(1).isHidden())
        self.assertEqual(len(self.dialog.selected_targets()), 2)

    def test_async_refresh_clears_stale_selection_and_restores_controls(self):
        self.add_row(0)
        self.dialog.refresh_library()
        self.assertFalse(self.dialog.refresh_button.isEnabled())
        self.assertFalse(self.dialog.assign_button.isEnabled())
        self.dialog.worker.wait(3000)
        self.application.processEvents()
        self.assertEqual(self.dialog.selected_batches(), {})
        self.assertTrue(self.dialog.refresh_button.isEnabled())

    def test_assignment_shows_per_locale_status_and_never_false_global_success(self):
        self.add_row(0)
        for index in range(self.dialog.targets_list.count()):
            self.dialog.targets_list.item(index).setCheckState(Qt.Checked)
        self.client.fail_localization = 'loc-en'
        with patch('asset_library.time.sleep'), patch('asset_library_selection.QMessageBox.question', return_value=QMessageBox.Yes):
            self.dialog.assign_selected()
            deadline = time.monotonic() + 5
            while self.dialog.worker.isRunning() and time.monotonic() < deadline:
                self.application.processEvents()
                time.sleep(0.001)
            self.dialog.worker.wait(1000)
            self.application.processEvents()
        self.assertEqual(self.dialog.results_table.rowCount(), 2)
        self.assertIn('вручную', self.dialog.results_table.item(0, 1).text())
        self.assertIn('Подтверждено', self.dialog.results_table.item(1, 1).text())
        self.assertIn('вручную: 1', self.dialog.notice.text())
        self.assertNotIn('УСПЕШНО', self.dialog.notice.text())

    def test_dialog_cannot_be_closed_while_worker_is_running(self):
        with patch.object(self.dialog, 'is_busy', return_value=True):
            self.dialog.reject()
            self.assertEqual(self.dialog.result(), 0)
            self.assertIn('Дождитесь', self.dialog.notice.text())


class MainWindowLibraryIntegrationTests(unittest.TestCase):
    def test_opening_both_surfaces_launches_shared_dialog_without_upload_worker(self):
        harness = SimpleNamespace(_metadata_api_creds=lambda: {
            'issuer': 'i', 'key_id': 'k', 'p8_path': 'unused.p8', 'app_id': 'app',
        }, _log=Mock())
        for mode in ['version', 'ppo']:
            with patch.object(app, 'LibrarySelectionDialog') as dialog, patch.object(app, 'ASCClient') as client:
                app.MainWindow._open_existing_asset_library(harness, mode)
                self.assertEqual(dialog.call_args.args[1], mode)
                dialog.return_value.refresh_library.assert_called_once()
                dialog.return_value.exec.assert_called_once()
                client.assert_not_called()

    def test_library_dialog_is_not_opened_during_existing_mutation_worker(self):
        harness = SimpleNamespace(_metadata_api_creds=Mock(), _log=Mock(),
                                  screenshot_upload_worker=Mock(isRunning=Mock(return_value=True)))
        with patch.object(app, 'LibrarySelectionDialog') as dialog:
            app.MainWindow._open_existing_asset_library(harness, 'version')
            dialog.assert_not_called()
