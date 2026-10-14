import unittest

import app_store_ppo_automation as app


class PPOManualCheckSummaryTests(unittest.TestCase):
    def test_formats_manual_check_locales(self):
        summary = app.format_ppo_manual_check_summary({
            ("Treatment C", "de-DE", "German"),
            ("Treatment B", "en-US", "English (U.S.)"),
        })

        self.assertIn("Проверь вручную", summary)
        self.assertIn("Treatment B: English (U.S.) (en-US)", summary)
        self.assertIn("Treatment C: German (de-DE)", summary)

    def test_detects_retry_log_as_manual_check_reason(self):
        self.assertTrue(
            app.is_ppo_manual_check_log_message(
                "⚠️ Сетевая ошибка (appScreenshots), повтор 2/6 через 1 с..."
            )
        )


if __name__ == "__main__":
    unittest.main()
