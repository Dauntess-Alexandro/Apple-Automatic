import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class ScreenshotCheckCliTests(unittest.TestCase):
    def run_check(self, invalid=False):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'images.png'
            if invalid:
                source.write_bytes(b'not an image')
            else:
                Image.new('RGBA', (1206, 2622), (45, 90, 135, 255)).save(source)
            original = source.read_bytes()
            report = Path(folder) / 'report.json'
            environment = dict(os.environ, QT_QPA_PLATFORM='no_ui_allowed_for_this_test')
            result = subprocess.run([sys.executable, str(ROOT / 'app_store_ppo_automation.py'),
                                     '--check-screenshots', str(report), str(source)],
                                    env=environment, capture_output=True, timeout=30)
            self.assertTrue(report.is_file(), result.stderr.decode(errors='replace'))
            self.assertEqual(source.read_bytes(), original)
            return result, json.loads(report.read_text())

    def test_offline_check_prepares_images_without_starting_ui(self):
        result, report = self.run_check()
        self.assertEqual(result.returncode, 0)
        self.assertTrue(report['ok'])
        self.assertEqual(report['batches']['APP_IPHONE_61'], [
            {'source': 'images.png', 'size': [1206, 2622], 'mode': 'RGB', 'format': 'JPEG'}
        ])

    def test_offline_check_reports_bad_image_and_preserves_source(self):
        result, report = self.run_check(invalid=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report['ok'])
        self.assertIn('cannot identify image file', report['error'])

    def test_report_cannot_overwrite_input_or_alias(self):
        for alias_type in ['same_path', 'symlink', 'hardlink']:
            with self.subTest(alias_type=alias_type), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / 'images.png'
                Image.new('RGB', (1206, 2622)).save(source)
                original = source.read_bytes()
                report = source if alias_type == 'same_path' else Path(folder) / 'report.json'
                if alias_type == 'symlink':
                    report.symlink_to(source)
                elif alias_type == 'hardlink':
                    os.link(source, report)
                result = subprocess.run([sys.executable, str(ROOT / 'app_store_ppo_automation.py'),
                                         '--check-screenshots', str(report), str(source)],
                                        capture_output=True, timeout=30)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(result.returncode, 2)
