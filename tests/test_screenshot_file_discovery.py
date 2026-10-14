import os
import tempfile
import unittest

import app_store_ppo_automation as app


class ScreenshotFileDiscoveryTests(unittest.TestCase):
    def test_downloads_discovery_accepts_any_image_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            image_path = os.path.join(folder, "images.png")
            with open(image_path, "wb") as image_file:
                image_file.write(b"not a real png, only extension matters here")

            original_downloads_dir = app.DOWNLOADS_DIR
            try:
                app.DOWNLOADS_DIR = folder

                self.assertEqual(app._list_downloads_screenshot_paths(), [image_path])
            finally:
                app.DOWNLOADS_DIR = original_downloads_dir


if __name__ == "__main__":
    unittest.main()
