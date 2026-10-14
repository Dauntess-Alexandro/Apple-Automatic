import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MacBuildSafetyTests(unittest.TestCase):
    def run_build(self, running, copy_failure=False, signature_failure=False):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            shutil.copyfile(ROOT / 'Mac_build.command', workspace / 'Mac_build.command')
            executable_dir = workspace / '.venv/bin'
            executable_dir.mkdir(parents=True)
            installer = executable_dir / 'pyinstaller'
            installer.write_text('#!/bin/bash\ntouch build_started\nexit 7\n')
            if copy_failure or signature_failure:
                installer.write_text('#!/bin/bash\ntouch build_started\nmkdir -p dist/PPO_Automation.app\nexit 0\n')
                copier = executable_dir / 'ditto'
                copier.write_text('#!/bin/bash\nfor argument in "$@"; do\ncase "$argument" in\n"$HOME"/Applications/*) exit 8;;\nesac\ndone\nexec /usr/bin/ditto "$@"\n')
                copier.chmod(0o755)
                if signature_failure:
                    copier.write_text('#!/bin/bash\nexec /usr/bin/ditto "$@"\n')
                    signer = executable_dir / 'codesign'
                    signer.write_text('#!/bin/bash\nexit 9\n')
                    signer.chmod(0o755)
            installer.chmod(0o755)
            process_check = executable_dir / 'pgrep'
            process_check.write_text(f'#!/bin/bash\nexit {0 if running else 1}\n')
            process_check.chmod(0o755)
            desktop_app = workspace / 'Desktop/PPO_Automation.app'
            desktop_app.mkdir(parents=True)
            sentinel = desktop_app / 'existing'
            sentinel.write_text('keep this app')
            environment = dict(os.environ, HOME=str(workspace),
                               PATH=str(executable_dir) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['bash', str(workspace / 'Mac_build.command')],
                                    env=environment, capture_output=True, text=True, timeout=20)
            return result, (workspace / 'build_started').exists(), sentinel.read_text()

    def test_running_application_blocks_rebuild_before_replacing_files(self):
        result, started, existing = self.run_build(running=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(started)
        self.assertIn('Закройте PPO_Automation', result.stdout)
        self.assertEqual(existing, 'keep this app')

    def test_failed_build_does_not_report_success_or_replace_desktop_app(self):
        result, started, existing = self.run_build(running=False)
        self.assertEqual(result.returncode, 7)
        self.assertTrue(started)
        self.assertNotIn('ГОТОВО!', result.stdout)
        self.assertEqual(existing, 'keep this app')

    def test_failed_copy_preserves_working_desktop_app(self):
        result, started, existing = self.run_build(running=False, copy_failure=True)
        self.assertEqual(result.returncode, 8)
        self.assertTrue(started)
        self.assertEqual(existing, 'keep this app')

    def test_failed_signature_verification_preserves_installed_app(self):
        result, started, existing = self.run_build(running=False, signature_failure=True)
        self.assertEqual(result.returncode, 9)
        self.assertTrue(started)
        self.assertNotIn('ГОТОВО!', result.stdout)
        self.assertEqual(existing, 'keep this app')
