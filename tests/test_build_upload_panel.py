import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QLineEdit
import test_library_selection_dialog as dialog_tests
from github_build_upload import AppleCredentials, BuildPreferences

try:
    import build_upload_panel as panel
except ImportError:
    panel = None


class BuildPanelTests(unittest.TestCase):
    setUpClass = classmethod(dialog_tests.LibraryDialogTests.setUpClass.__func__)

    def setUp(self):
        self.assertIsNotNone(panel, 'Build upload panel is missing')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        key_path = self.root / 'key1.p8'
        key_path.write_text('fixture')
        self.creds = AppleCredentials('issuer-1', 'key-1', '123', str(key_path))
        self.widget = panel.BuildUploadPanel(lambda: self.creds, self.root)
        self.addCleanup(self.widget.close)

    def test_action_reads_live_shared_credentials_without_duplicate_fields(self):
        self.widget.repository_input.setText('owner/repo')
        self.creds = AppleCredentials('issuer-2', 'key-2', '456', '/key2.p8')
        with patch.object(panel, 'BuildWorker') as Worker:
            self.widget.start_build()
            self.assertEqual(Worker.call_args.kwargs['credentials'], self.creds)
            Worker.return_value.start.assert_called_once()
        names = [item.objectName() for item in self.widget.findChildren(QLineEdit)]
        self.assertNotIn('issuer_input', names)
        self.assertNotIn('key_input', names)

    def test_app_change_loads_own_repository_and_clears_previous_run(self):
        self.widget.preferences.save('123', {'repository': 'owner/one', 'last_run': {'id': 9, 'repository': 'owner/one'}})
        self.widget.current_app_id = None
        self.widget.refresh_credentials()
        self.assertEqual(self.widget.repository_input.text(), 'owner/one')
        self.creds = AppleCredentials('other', 'other', '456', '/other.p8')
        self.widget.refresh_credentials()
        self.assertEqual(self.widget.repository_input.text(), '')
        self.assertIsNone(self.widget.last_run)

    def test_switching_apps_preserves_unsent_project_options(self):
        self.widget.repository_input.setText('owner/draft')
        self.widget.auto_push_checkbox.setChecked(True)
        self.widget.flutter_input.setText('3.46.0')
        self.creds = AppleCredentials('other', 'key', '456', '/key.p8')
        self.widget.refresh_credentials()
        saved = self.widget.preferences.load('123')
        self.assertEqual(saved['repository'], 'owner/draft')
        self.assertEqual(saved['flutter_version'], '3.46.0')
        self.assertTrue(saved['auto_push'])

    def test_success_message_explains_apple_processing(self):
        self.widget.apply_run({'id': 42, 'status': 'completed', 'conclusion': 'success', 'jobs': []})
        self.assertIn('Apple', self.widget.status_label.text())
        self.assertIn('обраб', self.widget.status_label.text())
        self.assertNotIn('опубликован', self.widget.status_label.text())

    def test_failure_never_shows_upload_success(self):
        self.widget.apply_run({'id': 42, 'status': 'completed', 'conclusion': 'failure',
                               'jobs': [{'name': 'Build', 'steps': [{'name': 'Build IPA', 'conclusion': 'failure', 'status': 'completed'}]}]})
        self.assertIn('Сборка приложения', self.widget.status_label.text())
        self.assertNotIn('загружен', self.widget.status_label.text())

    def test_run_saved_before_monitoring_and_token_not_persisted(self):
        self.widget.repository_input.setText('owner/repo')
        self.widget.token_input.setText('TOP_SECRET')
        self.widget.remember_run({'id': 42, 'repository': 'owner/repo', 'app_id': '123',
                                  'html_url': 'https://github.com/owner/repo/actions/runs/42'})
        data = self.widget.preferences.load('123')
        self.assertEqual(data['last_run']['id'], 42)
        self.assertNotIn('TOP_SECRET', self.widget.preferences.path.read_text())

    def test_resume_tracks_stored_repository_after_input_was_changed(self):
        self.widget.last_run = {'id': 42, 'repository': 'owner/original', 'app_id': '123'}
        self.widget.repository_input.setText('owner/different')
        with patch.object(panel, 'BuildWorker') as Worker:
            self.widget.resume_observation()
            self.assertEqual(Worker.call_args.kwargs['repository'], 'owner/original')
            self.assertEqual(Worker.call_args.kwargs['run_id'], 42)

    def test_shutdown_requests_stop_without_cancelling_cloud_run(self):
        worker = Mock()
        worker.isRunning.return_value = True
        worker.wait.return_value = False
        self.widget.worker = worker
        self.assertFalse(self.widget.prepare_close())
        worker.requestInterruption.assert_called_once()
        self.assertIn('наблюден', self.widget.status_label.text())
        self.widget.worker = None

    def test_worker_uses_exact_run_then_stops_on_terminal_status(self):
        service = Mock()
        service.inspect.return_value = {}
        service.start.return_value = {'id': 77, 'repository': 'owner/repo', 'app_id': '123'}
        service.observe.return_value = {'id': 77, 'status': 'completed', 'conclusion': 'success', 'jobs': []}
        with patch.object(panel, 'BuildService', return_value=service):
            worker = panel.BuildWorker(repository='owner/repo', credentials=self.creds, config_dir=self.root,
                                       action='start', token='', auto_push=False, flutter_version='3.47.0')
            remembered = []
            worker.launched.connect(remembered.append)
            worker.run()
        service.observe.assert_called_once_with(77)
        self.assertEqual(remembered[0]['id'], 77)

if __name__ == '__main__':
    unittest.main()

class BuildIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(dialog_tests.LibraryDialogTests.setUpClass.__func__)

    def test_build_tab_hides_unrelated_process_panel_and_api_button_navigates(self):
        import app_store_ppo_automation as app
        with patch.object(app, 'ensure_flags_downloaded'), \
             patch.object(app.MainWindow, '_has_complete_api_credentials', return_value=False), \
             patch.object(app.MainWindow, '_save_env_to_file'), \
             patch.object(app.MainWindow, '_refresh_accounts_roster_status'):
            window = app.MainWindow()
        self.addCleanup(window.close)
        build_index = next(i for i in range(window.tabs.count()) if window.tabs.widget(i) is window.tab_build_upload)
        window.tabs.setCurrentIndex(build_index)
        self.assertTrue(window.start_btn.isHidden())
        self.assertTrue(window.main_splitter.widget(1).isHidden())
        window.tab_build_upload.api_button.click()
        self.assertEqual(window.tabs.widget(window.tabs.currentIndex()), window.tab_api)
        self.assertFalse(window.main_splitter.widget(1).isHidden())

    def test_worker_persists_id_before_queued_gui_signal_is_delivered(self):
        service = Mock()
        service.inspect.return_value = {}
        service.start.return_value = {'id': 88, 'repository': 'owner/repo', 'app_id': '123'}
        service.observe.return_value = {'id': 88, 'status': 'completed', 'conclusion': 'success', 'jobs': []}
        with tempfile.TemporaryDirectory() as folder, patch.object(panel, 'BuildService', return_value=service):
            key_path = Path(folder) / 'key.p8'
            key_path.write_text('fixture')
            worker = panel.BuildWorker(repository='owner/repo', credentials=AppleCredentials('issuer','key','123',str(key_path)),
                                       config_dir=Path(folder), action='start')
            worker.run()
            saved = BuildPreferences(Path(folder) / '.build_upload.json').load('123')
            self.assertEqual(saved['last_run']['id'], 88)
