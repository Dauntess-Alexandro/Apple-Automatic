import os
import tempfile
import unittest

from PIL import Image

import app_store_ppo_automation as app


class PPOScreenshotDisplayTypeTests(unittest.TestCase):
    def _write_png(self, folder, name, size):
        path = os.path.join(folder, name)
        Image.new("RGB", size, "white").save(path, "PNG")
        return path

    def test_detects_iphone_55_from_landscape_size(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_png(folder, "screen.png", (2208, 1242))

            display_type, label = app.resolve_ppo_screenshot_display_type(
                folder, ["screen.png"]
            )

        self.assertEqual(display_type, app.APP_IPHONE_55_DISPLAY_TYPE)
        self.assertIn("5.5", label)

    def test_detects_iphone_65_from_landscape_size(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_png(folder, "screen.png", (2688, 1242))

            display_type, label = app.resolve_ppo_screenshot_display_type(
                folder, ["screen.png"]
            )

        self.assertEqual(display_type, app.APP_IPHONE_65_DISPLAY_TYPE)
        self.assertIn("6.5", label)

    def test_uses_required_medium_auto_crop_for_unknown_size(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_png(folder, "screen.png", (1000, 1000))

            display_type, label = app.resolve_ppo_screenshot_display_type(
                folder, ["screen.png"]
            )

        self.assertEqual(display_type, 'APP_IPHONE_61')
        self.assertIn("по умолчанию", label)


if __name__ == "__main__":
    unittest.main()
