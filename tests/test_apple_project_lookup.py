from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import yaml

import github_build_upload as builds
from local_project_setup import render_native_workflow


class AppleProjectLookupTests(unittest.TestCase):
    def test_lists_apps_without_app_id_or_github_login_and_follows_apple_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key.p8';key.write_text('fixture')
            client=builds.AppleAppClient(builds.AppleCredentials('issuer','key','',str(key)))
            def app(number):
                return {'id':str(number),'attributes':{'name':'Example','bundleId':'com.example.app'+str(number)}}
            with patch.object(client,'apple_request',side_effect=[{'data':[app(1)],'links':{'next':'https://api.appstoreconnect.apple.com/v1/apps?cursor=2'}}, {'data':[app(2)]}]), patch.object(builds,'resolve_github_token') as github:
                self.assertEqual([a['app_id'] for a in client.apple_apps()],['1','2'])
                github.assert_not_called()

    def test_untrusted_pagination_never_receives_apple_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key.p8';key.write_text('fixture')
            client=builds.AppleAppClient(builds.AppleCredentials('issuer','key','',str(key)))
            with patch.object(client,'apple_request',return_value={'data':[],'links':{'next':'https://evil.test/v1/apps?cursor=2'}}) as request:
                with self.assertRaises(builds.BuildError):
                    client.apple_apps()
                self.assertEqual(request.call_count,1)

    def test_explicit_app_id_only_looks_up_that_app(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key.p8';key.write_text('fixture')
            client=builds.AppleAppClient(builds.AppleCredentials('issuer','key','123',str(key)))
            with patch.object(client,'apple_app',return_value={'app_id':'123'}) as app:
                self.assertEqual(client.apple_apps(),[{'app_id':'123'}])
                app.assert_called_once()

    def test_native_managed_workflow_pins_identity_source_and_secrets(self):
        choice={'app_directory':'apps/Example','app_id':'123','bundle_id':'com.example.app','xcode_scheme':'Example'}
        text=render_native_workflow(choice,secret_prefix='APP_ONE_',repository_identity='id',auto_push=True,branch='release/v1')
        spec=yaml.safe_load(text)
        self.assertEqual(spec['name'],'IOS BUILD')
        self.assertEqual(spec['on']['push']['branches'],['release/v1'])
        self.assertEqual(spec['env']['BUNDLE_ID'],'com.example.app')
        self.assertIn('secrets.APP_ONE_APP_STORE_CONNECT_PRIVATE_KEY',text)
        steps=spec['jobs']['build']['steps']
        checkout=next(s for s in steps if s.get('uses','').startswith('actions/checkout@'))
        self.assertEqual(checkout['with']['ref'],'${{ inputs.source_sha || github.sha }}')
        upload=next(s for s in steps if s.get('name')=='Upload to App Store Connect')
        self.assertNotIn('if',upload)

    def test_remote_native_inspection_supports_nested_shared_scheme(self):
        service=builds.BuildService('owner/repo',builds.AppleCredentials('issuer','key','123','/unused'),Path('/tmp'),token='token')
        project='apps/Example/Example.xcodeproj'
        scheme=project+'/xcshareddata/xcschemes/Example.xcscheme'
        pbx=project+'/project.pbxproj'
        service.github=Mock()
        service.github.request.side_effect=[{'login':'owner'},{'permissions':{'push':True},'default_branch':'main'},
                                            {'commit':{'sha':'a'*40}}, {'tree':[{'type':'blob','path':p} for p in [pbx,scheme]]}]
        with patch.object(service,'apple_app',return_value={'app_id':'123','app_name':'Example','bundle_id':'com.example.app'}), patch.object(service,'remote_text',return_value='PRODUCT_BUNDLE_IDENTIFIER = com.example.app;'):
            result=service.inspect()
        self.assertEqual(result['app_type'],'xcode')
        self.assertEqual(result['app_directory'],'apps/Example')
        self.assertEqual(result['xcode_scheme'],'Example')

    def test_unrelated_flutter_does_not_hide_matching_native_app(self):
        service=builds.BuildService('owner/repo',builds.AppleCredentials('issuer','key','123','/unused'),Path('/tmp'),token='token')
        pbx='apps/Example/Example.xcodeproj/project.pbxproj'
        texts={pbx:'PRODUCT_BUNDLE_IDENTIFIER = com.example.app;',
               'samples/pubspec.yaml':'name: sample',
               'samples/ios/Runner.xcodeproj/project.pbxproj':'PRODUCT_BUNDLE_IDENTIFIER = com.other.sample;'}
        scheme='apps/Example/Example.xcodeproj/xcshareddata/xcschemes/Example.xcscheme'
        service.github=Mock()
        service.github.request.side_effect=[{'login':'owner'},{'permissions':{'push':True},'default_branch':'main'},
                                            {'commit':{'sha':'a'*40}}, {'tree':[{'type':'blob','path':p} for p in [*texts,scheme]]}]
        with patch.object(service,'apple_app',return_value={'app_id':'123','app_name':'Example','bundle_id':'com.example.app'}), patch.object(service,'remote_text',side_effect=lambda path,ref:texts[path]):
            result=service.inspect()
        self.assertEqual(result['app_type'],'xcode')

    def test_native_and_flutter_with_same_bundle_are_ambiguous(self):
        service=builds.BuildService('owner/repo',builds.AppleCredentials('issuer','key','123','/unused'),Path('/tmp'),token='token')
        texts={'Native/Example.xcodeproj/project.pbxproj':'PRODUCT_BUNDLE_IDENTIFIER = com.example.app;',
               'Flutter/pubspec.yaml':'name: sample',
               'Flutter/ios/Runner.xcodeproj/project.pbxproj':'PRODUCT_BUNDLE_IDENTIFIER = com.example.app;'}
        scheme='Native/Example.xcodeproj/xcshareddata/xcschemes/Example.xcscheme'
        service.github=Mock()
        service.github.request.side_effect=[{'login':'owner'},{'permissions':{'push':True},'default_branch':'main'},
                                            {'commit':{'sha':'a'*40}}, {'tree':[{'type':'blob','path':p} for p in [*texts,scheme]]}]
        with patch.object(service,'apple_app',return_value={'app_id':'123','app_name':'Example','bundle_id':'com.example.app'}), patch.object(service,'remote_text',side_effect=lambda path,ref:texts[path]):
            with self.assertRaises(builds.BuildError):
                service.inspect()
