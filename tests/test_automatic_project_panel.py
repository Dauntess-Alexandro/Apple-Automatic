import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import unittest
from PySide6.QtWidgets import QApplication
import test_library_selection_dialog as dialogs
from github_build_upload import AppleCredentials
import build_upload_panel as panel


class AutomaticProjectPanelTests(unittest.TestCase):
    setUpClass=classmethod(dialogs.LibraryDialogTests.setUpClass.__func__)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.key=self.root/'key.p8'
        self.key.write_text('fixture')
        self.creds=AppleCredentials('issuer','key','123',str(self.key))
        self.widget=panel.BuildUploadPanel(lambda:self.creds,self.root)
        self.addCleanup(self.widget.close)

    def test_existing_credentials_do_not_prepare_or_touch_project_on_startup(self):
        self.assertTrue(hasattr(self.widget,'auto_project_timer'),'Automatic project preparation is missing')
        self.assertFalse(self.widget.auto_project_timer.isActive())

    def test_changed_keys_schedule_preparation_even_when_app_id_is_empty(self):
        self.assertTrue(hasattr(self.widget,'auto_project_timer'),'Automatic project preparation is missing')
        self.creds=AppleCredentials('new-issuer','new-key','',str(self.key))
        self.widget.refresh_credentials()
        self.assertTrue(self.widget.auto_project_timer.isActive())
        self.widget.auto_project_timer.stop()

    def test_preparation_uses_background_worker_and_does_not_start_cloud_build(self):
        self.assertTrue(hasattr(self.widget,'prepare_local_project'),'Local project preparation is missing')
        with patch.object(panel,'BuildWorker') as Worker:
            self.widget.prepare_local_project()
            self.assertEqual(Worker.call_args.kwargs['action'],'prepare')
            Worker.return_value.start.assert_called_once()

    def test_prepared_app_id_updates_shared_fields_and_local_folder(self):
        self.assertTrue(hasattr(self.widget,'apply_prepared_project'),'Local project preparation is missing')
        ids=[]
        self.widget.app_id_detected.connect(ids.append)
        result={'app_id':'123','app_name':'Example','bundle_id':'com.example.app','local_folder':str(self.root/'Example'),
                'repository':'owner/example','app_type':'flutter','app_directory':'.','credential_identity':self.creds}
        self.widget.apply_prepared_project(result)
        self.assertEqual(ids,['123'])
        self.assertEqual(self.widget.repository_input.text(),'owner/example')
        self.assertIn('com.example.app',self.widget.project_label.text())
        self.assertEqual(self.widget.preferences.load('123')['local_folder'],str(self.root/'Example'))

    def test_stale_lookup_result_does_not_change_newly_selected_app(self):
        self.assertTrue(hasattr(self.widget,'apply_prepared_project'),'Local project preparation is missing')
        self.widget.repository_input.setText('owner/current')
        self.widget.apply_prepared_project({'credential_identity':AppleCredentials('old','old','1',str(self.key)),
                                           'app_id':'1','repository':'owner/old'})
        self.assertEqual(self.widget.repository_input.text(),'owner/current')

    def test_discovered_new_app_does_not_inherit_prior_push_preference_or_run(self):
        self.widget.auto_push_checkbox.setChecked(True)
        self.widget.flutter_input.setText('3.46.0')
        self.widget.last_run={'id':9,'repository':'owner/old','app_id':'123'}
        result={'app_id':'456','app_name':'New','bundle_id':'com.example.new','local_folder':str(self.root/'New'),
                'repository':'owner/new','app_type':'flutter','app_directory':'.','credential_identity':self.creds}
        self.widget.apply_prepared_project(result)
        saved=self.widget.preferences.load('456')
        self.assertFalse(saved['auto_push'])
        self.assertEqual(saved['flutter_version'],'3.47.0')
        self.assertFalse(saved['last_run'])

    def test_background_lookup_installs_files_and_updates_shared_app_without_cloud_build(self):
        import time
        project=self.root/'Projects/Example'
        (project/'ios/Runner.xcodeproj').mkdir(parents=True)
        (project/'pubspec.yaml').write_text('dependencies:\n  flutter:\n    sdk: flutter\n')
        (project/'ios/Runner.xcodeproj/project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.app;')
        self.creds=AppleCredentials('issuer','key','',str(self.key))
        self.widget.refresh_credentials()
        self.widget.auto_project_timer.stop()
        self.widget.projects_root_input.setText(str(self.root/'Projects'))
        def selected(app_id):
            from dataclasses import replace
            self.creds=replace(self.creds,app_id=app_id)
            self.widget.refresh_credentials()
        self.widget.app_id_detected.connect(selected)
        with patch.object(panel.AppleAppClient,'apple_apps',return_value=[{'app_id':'456','app_name':'Example','bundle_id':'com.example.app'}]), patch.object(panel,'BuildService') as cloud:
            self.widget.prepare_local_project()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not (project/'.ios-build-files.json').exists():
                QApplication.processEvents()
                time.sleep(.01)
            # Drain queued prepared/finished signals as well.
            while self.widget.worker is not None and time.monotonic()<deadline:
                QApplication.processEvents()
                time.sleep(.01)
            self.assertTrue((project/'.github/workflows/apple-automatic-ios.yml').exists())
            self.assertEqual(self.creds.app_id,'456')
            self.assertIn('com.example.app',self.widget.project_label.text())
            self.assertFalse(self.widget.auto_project_timer.isActive())
            cloud.assert_not_called()

    def test_multiple_match_selection_still_installs_when_modal_processes_worker_finished(self):
        one={'app_id':'123','app_name':'One','bundle_id':'com.example.app','local_folder':str(self.root/'One'),
             'repository':'owner/one','app_type':'flutter','app_directory':'.'}
        two=dict(one,app_name='Two',local_folder=str(self.root/'Two'))
        result={'choices':[one,two],'apps':[one],'credential_identity':self.creds}
        finished = [False]
        def complete_once():
            if not finished[0]:
                finished[0] = True
                self.widget.worker_finished()
        def choose(*args):
            complete_once()
            return args[3][1],True
        with patch.object(panel.QInputDialog,'getItem',side_effect=choose), patch.object(self.widget,'_install_local_choice') as install:
            self.widget.choose_local_project(result)
            complete_once()
            QApplication.processEvents()
            install.assert_called_once_with(two,self.creds)
