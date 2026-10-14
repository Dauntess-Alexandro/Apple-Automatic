import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml
import github_build_upload as builds


class IpaArtifactNameTests(unittest.TestCase):
    def script(self):
        spec = yaml.safe_load(builds.render_workflow('project', 'com.drape.wardrobe', '6812346623', 'main'))
        steps = spec['jobs']['build']['steps']
        prepare = next((s for s in steps if s.get('name') == 'Prepare IPA artifact'), None)
        self.assertIsNotNone(prepare, 'IPA filenames must be normalized before artifact upload')
        self.assertLess(steps.index(prepare), next(i for i,s in enumerate(steps) if s.get('name') == 'Save signed IPA'))
        return prepare['run']

    def test_colon_filename_is_renamed_without_changing_signed_bytes(self):
        script = self.script()
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'build/ios/ipa'
            directory.mkdir(parents=True)
            (directory / 'Winit: Wardrobe.ipa').write_bytes(b'signed IPA bytes')
            result = subprocess.run(['bash', '-c', script], cwd=folder, capture_output=True, text=True,
                                    env=dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([(p.name, p.read_bytes()) for p in directory.glob('*.ipa')], [('app.ipa', b'signed IPA bytes')])

    def test_missing_or_ambiguous_ipa_stops_upload(self):
        script = self.script()
        for names in [[], ['First.ipa', 'Second.ipa']]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder) / 'build/ios/ipa'
                directory.mkdir(parents=True)
                for name in names:
                    (directory / name).write_bytes(b'ipa')
                result = subprocess.run(['bash', '-c', script], cwd=folder, capture_output=True, text=True,
                                        env=dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']))
                self.assertNotEqual(result.returncode, 0)
