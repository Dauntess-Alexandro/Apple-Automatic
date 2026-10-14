import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

try:
    import github_build_upload as builds
except ImportError:
    builds = None


class BuildServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(builds, 'Build upload service is missing')

    def test_repository_parser_rejects_non_github_and_injection(self):
        for url in ['https://github.com/owner/repo.git', 'git@github.com:owner/repo.git', 'owner/repo']:
            self.assertEqual(builds.parse_repository(url), 'owner/repo')
        for url in ['https://evil.test/owner/repo', 'owner/repo/extra', 'owner/repo\nrun: evil', '../repo']:
            with self.assertRaises(builds.BuildError):
                builds.parse_repository(url)

    def test_nested_flutter_detection_validates_exact_bundle(self):
        files = {'project/pubspec.yaml': 'name: example',
                 'project/ios/Runner.xcodeproj/project.pbxproj': 'PRODUCT_BUNDLE_IDENTIFIER = com.example.app;\nPRODUCT_BUNDLE_IDENTIFIER = com.example.app.RunnerTests;'}
        self.assertEqual(builds.discover_flutter_project(files, 'com.example.app'), 'project')
        with self.assertRaisesRegex(builds.BuildError, 'Bundle'):
            builds.discover_flutter_project(files, 'com.wrong.app')
        files.update({'other/pubspec.yaml': 'name: other',
                      'other/ios/Runner.xcodeproj/project.pbxproj': 'PRODUCT_BUNDLE_IDENTIFIER = com.example.app;'})
        with self.assertRaisesRegex(builds.BuildError, 'несколько'):
            builds.discover_flutter_project(files, 'com.example.app')

    def test_workflow_has_manual_dispatch_optional_push_and_no_plaintext_secrets(self):
        text = builds.render_workflow('project', 'com.example.app', '123456', 'main', False, '3.47.0')
        spec = yaml.safe_load(text)
        triggers = spec.get('on', spec.get(True))
        self.assertIn('workflow_dispatch', triggers)
        self.assertNotIn('push', triggers)
        self.assertIn('--build-number', text)
        self.assertIn('secrets.APP_STORE_CONNECT_PRIVATE_KEY', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertNotIn('pull_request:', text)
        automatic = yaml.safe_load(builds.render_workflow('.', 'com.example.app', '123', 'release/v1', True, '3.47.0'))
        self.assertEqual(automatic.get('on', automatic.get(True))['push']['branches'], ['release/v1'])

    def test_workflow_refuses_unsafe_configuration(self):
        for args in [('../bad', 'com.example.app', '123', 'main', False, '3.47.0'),
                     ('.', 'com.bad; echo evil', '123', 'main', False, '3.47.0'),
                     ('.', 'com.example.app', '123', 'main', False, '$(evil)')]:
            with self.assertRaises(builds.BuildError):
                builds.render_workflow(*args)

    def service(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        creds = builds.AppleCredentials('issuer', 'key', '123', '/unused.p8')
        service = builds.BuildService('owner/repo', creds, Path(self.temp.name), token='token')
        service.github = Mock()
        return service

    def test_identity_mismatch_never_writes_secrets_or_workflow(self):
        service = self.service()
        with patch.object(service, 'inspect', side_effect=builds.BuildError('Bundle mismatch')):
            with self.assertRaises(builds.BuildError):
                service.start()
        service.github.request.assert_not_called()

    def test_unknown_workflow_is_not_overwritten(self):
        service = self.service()
        service.github.request.return_value = {'sha': 'old', 'content': base64.b64encode(b'name: User build').decode()}
        with self.assertRaisesRegex(builds.BuildError, 'чужой'):
            service.install_workflow('main', 'managed replacement')
        self.assertEqual(service.github.request.call_count, 1)

    def test_preferences_exclude_credentials_and_are_per_app(self):
        with tempfile.TemporaryDirectory() as folder:
            store = builds.BuildPreferences(Path(folder) / 'settings.json')
            store.save('123', {'repository': 'owner/one', 'auto_push': True, 'token': 'SECRET', 'issuer': 'SECRET'})
            store.save('456', {'repository': 'owner/two'})
            self.assertEqual(store.load('123')['repository'], 'owner/one')
            self.assertEqual(store.load('456')['repository'], 'owner/two')
            self.assertNotIn('SECRET', store.path.read_text())

    def test_dispatch_uses_returned_run_id_not_latest_run(self):
        service = self.service()
        service.github.request.return_value = {'workflow_run_id': 42, 'html_url': 'https://github.com/owner/repo/actions/runs/42'}
        result = service.dispatch('main')
        self.assertEqual(result['id'], 42)
        args, kwargs = service.github.request.call_args
        self.assertEqual(args[0], 'POST')
        self.assertEqual(kwargs['json']['ref'], 'main')

    def test_secret_encryption_can_be_decrypted_only_by_github_key(self):
        from nacl.public import PrivateKey, SealedBox
        private = PrivateKey.generate()
        encrypted = builds.encrypt_secret(base64.b64encode(bytes(private.public_key)).decode(), 'PRIVATE SECRET')
        self.assertNotIn('PRIVATE', encrypted)
        self.assertEqual(SealedBox(private).decrypt(base64.b64decode(encrypted)), b'PRIVATE SECRET')

    def test_existing_certificate_secret_is_preserved(self):
        service = self.service()
        from nacl.public import PrivateKey
        key = PrivateKey.generate()
        service.github.request.side_effect = [
            {'key': base64.b64encode(bytes(key.public_key)).decode(), 'key_id': 'public'},
            {'secrets': [{'name': 'CERTIFICATE_PRIVATE_KEY'}]}, {}, {}, {},
        ]
        with patch.object(service, 'read_apple_key', return_value='PRIVATE'), patch.object(service, 'certificate_key') as generator:
            service.configure_secrets()
        generator.assert_not_called()
        destinations = [call.args[1] for call in service.github.request.call_args_list if call.args[0] == 'PUT']
        self.assertEqual(len(destinations), 3)
        self.assertFalse(any(path.endswith('/CERTIFICATE_PRIVATE_KEY') for path in destinations))

    def test_github_errors_redact_token(self):
        response = Mock(status_code=403)
        response.json.return_value = {'message': 'denied SECRET_TOKEN'}
        session = Mock()
        session.request.return_value = response
        client = builds.GitHubClient('SECRET_TOKEN', session=session)
        with self.assertRaises(builds.BuildError) as caught:
            client.request('GET', '/user')
        self.assertNotIn('SECRET_TOKEN', str(caught.exception))

if __name__ == '__main__':
    unittest.main()

class BuildSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.creds = builds.AppleCredentials('issuer', 'key', '123', '/unused.p8')

    def test_bundle_mismatch_in_real_inspection_prevents_all_mutations(self):
        service = builds.BuildService('owner/repo', self.creds, self.root, token='token')
        service.github = Mock()
        service.github.request.side_effect = [
            {'login': 'owner'}, {'permissions': {'push': True}, 'default_branch': 'main'},
            {'commit': {'sha': 'head'}},
            {'tree': [{'type': 'blob', 'path': path} for path in ('pubspec.yaml', 'ios/Runner.xcodeproj/project.pbxproj')]},
        ]
        with patch.object(service, 'apple_app', return_value={'app_id': '123', 'app_name': 'App', 'bundle_id': 'com.right.app'}), \
             patch.object(service, 'remote_text', side_effect=['name: app', 'PRODUCT_BUNDLE_IDENTIFIER = com.wrong.app;']):
            with self.assertRaisesRegex(builds.BuildError, 'Bundle'):
                service.start()
        self.assertTrue(all(call.args[0] == 'GET' for call in service.github.request.call_args_list))

    def test_auto_push_excludes_setup_commit_to_avoid_duplicate_launch(self):
        spec = yaml.safe_load(builds.render_workflow('.', 'com.example.app', '123', 'main', True))
        triggers = spec.get('on', spec.get(True))
        self.assertIn(builds.WORKFLOW_PATH, triggers['push']['paths-ignore'])

    def test_certificate_key_is_stable_private_and_not_a_repo_file(self):
        service = builds.BuildService('owner/repo', self.creds, self.root, token='token')
        first = service.certificate_key()
        self.assertEqual(first, service.certificate_key())
        path = next((self.root / '.build-signing').glob('*.pem'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_foreign_workflow_is_detected_before_secrets(self):
        service = builds.BuildService('owner/repo', self.creds, self.root, token='token')
        service.github = Mock()
        service.github.request.return_value = {'content': base64.b64encode(b'name: Existing').decode()}
        snapshot = {'app_directory': '.', 'bundle_id': 'com.example.app', 'branch': 'main'}
        with patch.object(service, 'inspect', return_value=snapshot), patch.object(service, 'configure_secrets') as configure:
            with self.assertRaisesRegex(builds.BuildError, 'чужой'):
                service.start()
            configure.assert_not_called()

class BuildSourceTests(unittest.TestCase):
    def test_workflow_pins_manual_source_to_inspected_commit(self):
        spec = yaml.safe_load(builds.render_workflow('.', 'com.example.app', '123', 'main'))
        trigger = spec.get('on', spec.get(True))
        self.assertIn('source_sha', trigger['workflow_dispatch']['inputs'])
        checkout = spec['jobs']['build']['steps'][0]
        self.assertEqual(checkout['with']['ref'], '${{ inputs.source_sha || github.sha }}')

    def test_dispatch_carries_inspected_sha(self):
        with tempfile.TemporaryDirectory() as folder:
            service = builds.BuildService('owner/repo', builds.AppleCredentials('issuer','key','123','/unused'), Path(folder), token='token')
            service.github = Mock()
            service.github.request.return_value = {'workflow_run_id': 42, 'html_url': 'https://github.com/owner/repo/actions/runs/42'}
            service.dispatch('main', 'a' * 40)
            self.assertEqual(service.github.request.call_args.kwargs['json']['inputs']['source_sha'], 'a' * 40)

class BuildIdentityTests(unittest.TestCase):
    def test_different_key_ids_have_separate_secret_names_but_same_app_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            first = builds.BuildService('owner/repo', builds.AppleCredentials('issuer','KEY1','123','/key'), Path(folder), token='token')
            second = builds.BuildService('owner/repo', builds.AppleCredentials('issuer','KEY2','123','/key'), Path(folder), token='token')
            self.assertEqual(first.repository_identity(), second.repository_identity())
            self.assertNotEqual(first.secret_prefix(), second.secret_prefix())

    def test_bound_workflow_rejects_another_apple_identity_even_during_install(self):
        with tempfile.TemporaryDirectory() as folder:
            service = builds.BuildService('owner/repo', builds.AppleCredentials('issuer','key','123','/key'), Path(folder), token='token')
            service.github = Mock()
            old = builds.render_workflow('.', 'com.other.app', '456', 'main', repository_identity='other-identity')
            service.github.request.return_value = {'content': base64.b64encode(old.encode()).decode(), 'sha': 'old'}
            new = builds.render_workflow('.', 'com.example.app', '123', 'main', repository_identity=service.repository_identity())
            with self.assertRaisesRegex(builds.BuildError, 'другому'):
                service.install_workflow('main', new)
            self.assertTrue(all(call.args[0] == 'GET' for call in service.github.request.call_args_list))

    def test_build_number_uses_recent_ipa_artifacts_when_apple_is_delayed(self):
        text = builds.render_workflow('.', 'com.example.app', '123', 'main')
        self.assertIn('/actions/artifacts', text)
        self.assertIn('artifact_latest', text)
        spec = yaml.safe_load(text)
        self.assertEqual(spec['permissions']['actions'], 'read')

class WorkflowBuildNumberTests(unittest.TestCase):
    def test_next_number_is_above_both_apple_and_recent_uploaded_artifact(self):
        import os
        import subprocess
        import shutil
        spec = yaml.safe_load(builds.render_workflow('.', 'com.example.app', '123', 'main'))
        script = next(step['run'] for step in spec['jobs']['build']['steps'] if step.get('name') == 'Set up signing and next build number')
        for apple_latest, artifact_latest, expected in [('2', '3', '4'), ('8', '3', '9'), ('0', '0', '1'), ('1.2.3', '4', '5')]:
            with self.subTest(apple=apple_latest, artifact=artifact_latest), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                for name, body in {'keychain': 'exit 0', 'xcode-project': 'exit 0',
                                   'app-store-connect': f'if [ "$1" = get-latest-build-number ]; then echo "{apple_latest}"; fi',
                                   'python': f'cat >/dev/null\necho "{artifact_latest}"'}.items():
                    command = root / name
                    command.write_text('#!/bin/sh\n' + body + '\n')
                    command.chmod(0o755)
                environment = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                                   BUNDLE_ID='com.example.app', APP_STORE_APPLE_ID='123', RUNNER_TEMP=folder, GITHUB_ENV=str(root/'env'))
                result = subprocess.run(['bash','-c',script], env=environment, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root/'env').read_text().strip(), 'IOS_BUILD_NUMBER=' + expected)

class AppleConnectivityTests(unittest.TestCase):
    def test_identity_check_retries_network_timeout_without_mutating_github(self):
        import requests
        with tempfile.TemporaryDirectory() as folder:
            service = builds.BuildService('owner/repo', builds.AppleCredentials('issuer','key','123','/key'), Path(folder), token='token')
            service.github = Mock()
            response = Mock(status_code=200)
            response.json.return_value = {'data': {'id': '123', 'attributes': {'name': 'App', 'bundleId': 'com.example.app'}}}
            with patch.object(service, 'read_apple_key', return_value='private'), \
                 patch.object(builds.jwt, 'encode', return_value='signed'), \
                 patch.object(builds.requests, 'get', side_effect=[requests.Timeout(), response]) as get, \
                 patch.object(builds.time, 'sleep'):
                self.assertEqual(service.apple_app()['app_id'], '123')
                self.assertEqual(get.call_count, 2)
                service.github.request.assert_not_called()

    def test_apple_access_denied_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            service = builds.BuildService('owner/repo', builds.AppleCredentials('issuer','key','123','/key'), Path(folder), token='token')
            with patch.object(service, 'read_apple_key', return_value='private'), \
                 patch.object(builds.jwt, 'encode', return_value='signed'), \
                 patch.object(builds.requests, 'get', return_value=Mock(status_code=403)) as get:
                with self.assertRaisesRegex(builds.BuildError, '403'):
                    service.apple_app()
                self.assertEqual(get.call_count, 1)
