import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile

import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates/github-actions-ios.yml'


class UniversalIosWorkflowTests(unittest.TestCase):
    def step(self, name):
        self.assertTrue(TEMPLATE.is_file(), 'Portable GitHub Actions iOS template is missing')
        spec = yaml.safe_load(TEMPLATE.read_text())
        step = next((s for s in spec['jobs']['build']['steps'] if s.get('name') == name), None)
        self.assertIsNotNone(step, 'Missing workflow stage: ' + name)
        return step

    def execute(self, step, root, **extra):
        environment = dict(os.environ, PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'],
                           GITHUB_WORKSPACE=str(root), GITHUB_ENV=str(root/'environment'),
                           PROJECT_PATH_INPUT='auto', XCODE_SCHEME_INPUT='auto', XCODE_CONTAINER_INPUT='auto',
                           BUNDLE_ID='com.example.app', APP_STORE_APPLE_ID='123', RUNNER_TEMP=str(root))
        environment.update(extra)
        return subprocess.run(['bash','-c',step['run']], cwd=root, env=environment,
                              capture_output=True,text=True,timeout=10)

    def test_detects_nested_flutter_and_ignores_dependency_projects(self):
        step = self.step('Detect iOS project')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for app in [root/'project',root/'packages/example']:
                (app/'ios/Runner.xcodeproj').mkdir(parents=True)
                (app/'lib').mkdir()
                (app/'pubspec.yaml').write_text('dependencies:\n  flutter:\n    sdk: flutter\n')
                (app/'lib/main.dart').write_text('void main() {}')
                (app/'ios/Runner.xcodeproj/project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.app;')
            result=self.execute(step,root)
            self.assertEqual(result.returncode,0,result.stderr)
            values=(root/'environment').read_text()
            self.assertIn('APP_TYPE=flutter',values)
            self.assertIn('APP_DIRECTORY='+str((root/'project').resolve()),values)

    def test_detects_native_project_and_shared_scheme(self):
        step=self.step('Detect iOS project')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            project=root/'apps/My App/MyApp.xcodeproj'
            (project/'xcshareddata/xcschemes').mkdir(parents=True)
            (project/'project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.app;')
            (project/'xcshareddata/xcschemes/MyApp.xcscheme').write_text('<Scheme/>')
            result=self.execute(step,root)
            self.assertEqual(result.returncode,0,result.stderr)
            values=(root/'environment').read_text()
            self.assertIn('APP_TYPE=xcode',values)
            self.assertIn('XCODE_SCHEME=MyApp',values)

    def test_ambiguous_projects_and_outside_paths_stop_before_signing(self):
        step=self.step('Detect iOS project')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ['One','Two']:
                project=root/(name+'.xcodeproj')
                project.mkdir()
                (project/'project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.app;')
            for extra in [{},{'PROJECT_PATH_INPUT':'..'},{'PROJECT_PATH_INPUT':'$(touch BAD)'}]:
                result=self.execute(step,root,**extra)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse((root/'BAD').exists())

    def test_workspace_generated_by_pods_is_selected_after_dependencies(self):
        detect = self.step('Detect iOS project')
        resolve = self.step('Resolve Xcode container')
        for requested in ['auto', 'Native/App.xcworkspace']:
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                app = root / 'Native'
                project = app / 'App.xcodeproj'
                (project / 'xcshareddata/xcschemes').mkdir(parents=True)
                (project / 'project.pbxproj').write_text('PRODUCT_BUNDLE_IDENTIFIER = com.example.app;')
                (project / 'xcshareddata/xcschemes/App.xcscheme').write_text('<Scheme/>')
                (app / 'Podfile').write_text("platform :ios, '15.0'")
                result = self.execute(detect, root, XCODE_CONTAINER_INPUT=requested)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # pod install creates this workspace after the initial detection.
                (app / 'App.xcworkspace').mkdir()
                result = self.execute(resolve, root, APP_DIRECTORY=str(app), XCODE_CONTAINER_INPUT=requested)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('XCODE_CONTAINER=' + str((app / 'App.xcworkspace').resolve()), (root / 'environment').read_text())

    def test_verifies_ipa_identity_build_and_safe_filename(self):
        step=self.step('Verify and prepare IPA')
        for bundle,number,ok in [('com.example.app','7',True),('com.other.app','7',False),('com.example.app','6',False)]:
            with self.subTest(bundle=bundle,number=number),tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                ipa=root/'build/ios/ipa/Winit: Wardrobe.ipa'
                ipa.parent.mkdir(parents=True)
                with zipfile.ZipFile(ipa,'w') as archive:
                    archive.writestr('Payload/Runner.app/Info.plist',plistlib.dumps({'CFBundleIdentifier':bundle,'CFBundleVersion':number}))
                original=ipa.read_bytes()
                result=self.execute(step,root,APP_DIRECTORY=str(root),IOS_BUILD_NUMBER='7')
                self.assertEqual(result.returncode==0,ok,result.stdout+result.stderr)
                if ok:
                    final=root/'build/ios/ipa/app.ipa'
                    self.assertEqual(final.read_bytes(),original)
                    self.assertIn('IPA_PATH='+str(final),(root/'environment').read_text())
