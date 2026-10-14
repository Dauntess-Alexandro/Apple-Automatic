import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

try:
    import local_project_setup as setup
except ImportError:
    setup = None


class LocalProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(setup, 'Automatic local project preparation is missing')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = {'app_id':'123', 'app_name':'My Wardrobe', 'bundle_id':'com.example.wardrobe'}

    def flutter(self, name, bundle='com.example.wardrobe'):
        root=self.root/name
        (root/'project/ios/Runner.xcodeproj').mkdir(parents=True)
        (root/'project/pubspec.yaml').write_text('name: wardrobe\ndependencies:\n  flutter:\n    sdk: flutter\n')
        (root/'project/ios/Runner.xcodeproj/project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = '+bundle+';')
        return root

    def test_finds_renamed_folder_by_bundle_and_not_a_similarly_named_wrong_app(self):
        correct=self.flutter('Old repo name')
        self.flutter('My Wardrobe', 'com.other.app')
        choices=setup.find_project_matches([self.app],self.root)
        self.assertEqual(len(choices),1)
        self.assertEqual(Path(choices[0]['local_folder']),correct.resolve())
        self.assertEqual(choices[0]['app_directory'],'project')

    def test_multiple_matches_are_returned_without_writing_any_files(self):
        first=self.flutter('One')
        second=self.flutter('Two')
        choices=setup.find_project_matches([self.app],self.root)
        self.assertEqual(len(choices),2)
        for path in [first,second]:
            self.assertFalse((path/'github-actions-ios.yml').exists())

    def test_manual_folder_and_native_project(self):
        native=self.root/'Native'
        project=native/'Example.xcodeproj'
        (project/'xcshareddata/xcschemes').mkdir(parents=True)
        (project/'project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.wardrobe;')
        (project/'xcshareddata/xcschemes/Example.xcscheme').write_text('<Scheme/>')
        choices=setup.find_project_matches([self.app],self.root,manual_folder=str(native))
        self.assertEqual(choices[0]['app_type'],'xcode')
        self.assertEqual(choices[0]['xcode_scheme'],'Example')

    def test_installs_populated_yaml_doc_and_active_workflow_without_private_keys(self):
        root=self.flutter('One')
        choice=setup.find_project_matches([self.app],self.root)[0]
        result=setup.install_project_files(choice,issuer='public-issuer',key_id='public-key')
        self.assertEqual(result['app_id'],'123')
        spec=yaml.safe_load((root/'github-actions-ios.yml').read_text())
        inputs=spec.get('on',spec.get(True))['workflow_dispatch']['inputs']
        self.assertEqual(inputs['bundle_id']['default'],'com.example.wardrobe')
        self.assertEqual(inputs['app_store_apple_id']['default'],'123')
        self.assertEqual(inputs['project_path']['default'],'project')
        active=(root/'.github/workflows/apple-automatic-ios.yml').read_text()
        self.assertIn('name: IOS BUILD',active)
        self.assertIn('com.example.wardrobe',active)
        self.assertTrue((root/'GITHUB_ACTIONS_IOS.md').is_file())
        self.assertNotIn('PRIVATE KEY',(root/'GITHUB_ACTIONS_IOS.md').read_text())
        setup.install_project_files(choice,issuer='public-issuer',key_id='public-key')

    def test_preserves_manual_edits_and_refuses_wrong_account(self):
        root=self.flutter('One')
        choice=setup.find_project_matches([self.app],self.root)[0]
        setup.install_project_files(choice,issuer='issuer',key_id='key')
        doc=root/'GITHUB_ACTIONS_IOS.md'
        doc.write_text('My custom notes')
        with self.assertRaisesRegex(Exception,'измен|существ'):
            setup.install_project_files(choice,issuer='issuer',key_id='key')
        self.assertEqual(doc.read_text(),'My custom notes')
        with self.assertRaisesRegex(Exception,'аккаунт|приложен'):
            setup.install_project_files(choice,issuer='another-issuer',key_id='key')

    def test_parent_projects_repository_is_not_mistaken_for_app_repository(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'remote','add','origin','https://github.com/org/Projects.git'],check=True)
        self.flutter('One')
        choice=setup.find_project_matches([self.app],self.root)[0]
        self.assertEqual(choice['repository'],'')

    def test_symlink_cannot_install_outside_project(self):
        root=self.flutter('One')
        outside=self.root/'outside'
        outside.mkdir()
        (root/'.github').symlink_to(outside,target_is_directory=True)
        choice=setup.find_project_matches([self.app],self.root)[0]
        with self.assertRaises(Exception):
            setup.install_project_files(choice,issuer='issuer',key_id='key')
        self.assertFalse((outside/'workflows/apple-automatic-ios.yml').exists())

    def test_manual_nested_app_uses_actual_checkout_root_for_active_workflow(self):
        root=self.flutter('Checkout')
        subprocess.run(['git','init','-q',str(root)],check=True)
        subprocess.run(['git','-C',str(root),'remote','add','origin','https://github.com/owner/app.git'],check=True)
        choice=setup.find_project_matches([self.app],self.root,manual_folder=str(root/'project'))[0]
        self.assertEqual(choice['local_folder'],str(root.resolve()))
        self.assertEqual(choice['app_directory'],'project')
        self.assertEqual(choice['repository'],'owner/app')
        setup.install_project_files(choice,issuer='issuer',key_id='key')
        self.assertTrue((root/'.github/workflows/apple-automatic-ios.yml').is_file())
        self.assertFalse((root/'project/.github').exists())
