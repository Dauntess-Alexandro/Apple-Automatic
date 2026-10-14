import json
import os
import unittest
from unittest.mock import patch

import requests

import app_store_ppo_automation as app


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} error", response=self
            )

    def json(self):
        return self._payload


class AppStoreRetryBehaviorTests(unittest.TestCase):
    def test_request_retries_apple_500_before_success(self):
        client = app.ASCClient.__new__(app.ASCClient)
        client.base_url = "https://example.test/v1"
        client.logger = lambda _message: None
        client._generate_token = lambda: "token"
        responses = [
            FakeResponse(500, {"errors": [{"code": "UNEXPECTED_ERROR"}]}),
            FakeResponse(200, {"data": {"id": "ok"}}),
        ]

        def fake_patch(*_args, **_kwargs):
            return responses.pop(0)

        with patch.object(app.requests, "patch", side_effect=fake_patch):
            result = client._request(
                "PATCH",
                "appScreenshots/screenshot-id",
                {"data": {"attributes": {"uploaded": True}}},
                max_retries=2,
            )

        self.assertEqual(result, {"data": {"id": "ok"}})
        self.assertEqual(responses, [])

    def test_screenshot_workers_default_to_previous_speed(self):
        original_default = app.SCREENSHOT_UPLOAD_MAX_WORKERS
        app.SCREENSHOT_UPLOAD_MAX_WORKERS = 4
        try:
            with patch.object(
                app,
                "_read_env_file",
                return_value={},
            ), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(app.resolve_screenshot_upload_max_workers(), 4)
        finally:
            app.SCREENSHOT_UPLOAD_MAX_WORKERS = original_default

    def test_screenshot_workers_honor_user_selected_speed(self):
        original_default = app.SCREENSHOT_UPLOAD_MAX_WORKERS
        app.SCREENSHOT_UPLOAD_MAX_WORKERS = 4
        try:
            with patch.object(
                app,
                "_read_env_file",
                return_value={"SCREENSHOT_UPLOAD_MAX_WORKERS": "9"},
            ), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(app.resolve_screenshot_upload_max_workers(), 9)
        finally:
            app.SCREENSHOT_UPLOAD_MAX_WORKERS = original_default

    def test_screenshot_upload_worker_honors_explicit_workers(self):
        original_default = app.SCREENSHOT_UPLOAD_MAX_WORKERS
        app.SCREENSHOT_UPLOAD_MAX_WORKERS = 4
        try:
            worker = app.ScreenshotUploadWorker(
                api_creds={
                    "issuer": "issuer",
                    "key_id": "key",
                    "p8_path": "/tmp/key.p8",
                    "app_id": "app",
                },
                target_locales=[],
                jpeg_files=[],
                max_workers=9,
            )

            self.assertEqual(worker.max_workers, 9)
        finally:
            app.SCREENSHOT_UPLOAD_MAX_WORKERS = original_default


if __name__ == "__main__":
    unittest.main()
