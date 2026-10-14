import unittest

import app_store_ppo_automation as app


class ScreenshotLocaleSeedTests(unittest.TestCase):
    def test_screenshot_seed_can_skip_app_info_localization(self):
        client = app.ASCClient.__new__(app.ASCClient)
        logs = []
        app_info_calls = []
        version_calls = []

        client.logger = logs.append
        client.get_version_localization_by_locale = lambda version_id, locale: None
        client.create_version_localization = (
            lambda version_id, locale, attrs: version_calls.append(
                (version_id, locale, dict(attrs))
            )
            or "version-localization-id"
        )
        client.get_app_info_localization_by_locale = (
            lambda *args: app_info_calls.append(("get", args)) or None
        )
        client.create_app_info_localization = (
            lambda *args: app_info_calls.append(("create", args)) or "app-info-localization-id"
        )
        client.update_app_info_localization = (
            lambda *args: app_info_calls.append(("update", args))
        )

        localization_id, created = client.seed_locale_from_primary(
            "version-id",
            "de-DE",
            {
                "locale": "en-US",
                "app_info_id": "app-info-id",
                "version": {"description": "Main description"},
                "app_info": {"name": "Main name", "subtitle": "Main subtitle"},
            },
            copy_app_info=False,
        )

        self.assertEqual(localization_id, "version-localization-id")
        self.assertTrue(created)
        self.assertEqual(
            version_calls,
            [("version-id", "de-DE", {"description": "Main description"})],
        )
        self.assertEqual(app_info_calls, [])

    def test_seed_copies_app_info_by_default(self):
        client = app.ASCClient.__new__(app.ASCClient)
        app_info_calls = []

        client.logger = lambda _message: None
        client.get_version_localization_by_locale = lambda version_id, locale: None
        client.create_version_localization = (
            lambda version_id, locale, attrs: "version-localization-id"
        )
        client.get_app_info_localization_by_locale = (
            lambda app_info_id, locale: None
        )
        client.create_app_info_localization = (
            lambda *args: app_info_calls.append(("create", args)) or "app-info-localization-id"
        )
        client.update_app_info_localization = (
            lambda *args: app_info_calls.append(("update", args))
        )

        client.seed_locale_from_primary(
            "version-id",
            "de-DE",
            {
                "locale": "en-US",
                "app_info_id": "app-info-id",
                "version": {"description": "Main description"},
                "app_info": {"name": "Main name"},
            },
        )

        self.assertEqual(
            app_info_calls,
            [("create", ("app-info-id", "de-DE", {"name": "Main name"}))],
        )


if __name__ == "__main__":
    unittest.main()
