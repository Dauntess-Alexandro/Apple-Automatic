import unittest

import app_store_ppo_automation as app


class FakePPOClient:
    def __init__(self):
        self.experiments = [
            {"id": "exp-1", "attributes": {"name": "Weather PPO"}},
        ]
        self.treatments = [
            {"id": "treat-b", "attributes": {"name": "Treatment B"}},
            {"id": "treat-c", "attributes": {"name": "Treatment C"}},
        ]
        self.localizations = {
            "treat-b": [
                {"attributes": {"locale": "en-US"}},
                {"attributes": {"locale": "de-DE"}},
            ],
            "treat-c": [
                {"attributes": {"locale": "fr-FR"}},
                {"attributes": {"locale": "en-US"}},
            ],
        }

    def get_experiments(self, version_id):
        return self.experiments

    def get_treatments(self, experiment_id):
        return self.treatments

    def get_localizations(self, treatment_id):
        return self.localizations[treatment_id]


class PPOUpdateLocalesTests(unittest.TestCase):
    def test_collects_locales_from_selected_existing_treatment(self):
        locales = app.collect_ppo_update_locales(
            FakePPOClient(),
            "version-id",
            "Weather PPO",
            ["Variant B"],
        )

        self.assertEqual(locales, ["de-DE", "en-US"])

    def test_collects_union_when_multiple_treatments_selected(self):
        locales = app.collect_ppo_update_locales(
            FakePPOClient(),
            "version-id",
            "Weather PPO",
            ["Variant B", "Variant C"],
        )

        self.assertEqual(locales, ["de-DE", "en-US", "fr-FR"])


if __name__ == "__main__":
    unittest.main()
