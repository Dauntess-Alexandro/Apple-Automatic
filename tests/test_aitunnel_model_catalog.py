import unittest
from unittest.mock import Mock, patch

import app_store_ppo_automation as app


def chat_model(cost=100, image=False, created=1):
    return {'provider': 'example', 'prompt_cost': cost / 5, 'completion_cost': cost * 4 / 5,
            'created': created, 'modalities': {'input': ['text', 'image'] if image else ['text'],
                                            'output': ['text']}}


class AITunnelCatalogTests(unittest.TestCase):
    def catalog(self, data, function):
        with patch.object(app.requests, 'get', return_value=Mock(json=lambda: data)):
            return function()

    def test_translation_refresh_discovers_new_models_without_manual_whitelist_update(self):
        rows = self.catalog({'new-translation-model': chat_model(created=100),
                             'gpt-6-luna': chat_model(created=10),
                             'too-expensive': chat_model(cost=100000)},
                            app.fetch_aitunnel_translation_model_catalog)
        identifiers = [row[0] for row in rows]
        self.assertIn('new-translation-model', identifiers)
        self.assertIn('gpt-6-luna', identifiers)
        self.assertNotIn('too-expensive', identifiers)
        self.assertNotIn('gpt-4o-mini', identifiers)

    def test_metadata_discovers_new_vision_models_and_excludes_text_only(self):
        rows = self.catalog({'new-vision-model': chat_model(image=True, created=100),
                             'gpt-6.1-sol': chat_model(cost=2400, image=True, created=90),
                             'text-only': chat_model(),
                             'too-expensive': chat_model(cost=100000, image=True)},
                            app.fetch_aitunnel_metadata_model_catalog)
        identifiers = [row[0] for row in rows]
        self.assertIn('new-vision-model', identifiers)
        self.assertIn('gpt-6.1-sol', identifiers)
        self.assertNotIn('text-only', identifiers)
        self.assertNotIn('too-expensive', identifiers)

    def test_unpriced_and_malformed_model_do_not_hide_valid_catalog(self):
        rows = self.catalog({'broken': {'prompt_cost': 'unknown'},
                             'unpriced': {'modalities': {'input': ['text'], 'output': ['text']}},
                             'valid': chat_model()}, app.fetch_aitunnel_translation_model_catalog)
        self.assertEqual([row[0] for row in rows], ['valid'])

    def test_new_unlisted_models_are_sorted_by_release_date(self):
        rows = self.catalog({'older': chat_model(created=1), 'newer': chat_model(created=100)},
                            app.fetch_aitunnel_translation_model_catalog)
        self.assertEqual([row[0] for row in rows], ['newer', 'older'])

    def test_offline_fallback_contains_recent_models(self):
        with patch.object(app.requests, 'get', side_effect=app.requests.ConnectionError('offline')):
            translation = [row[0] for row in app.fetch_aitunnel_translation_model_catalog()]
            metadata = [row[0] for row in app.fetch_aitunnel_metadata_model_catalog()]
            images = [row[0] for row in app.fetch_aitunnel_image_model_catalog()]
        self.assertIn('gpt-6-luna', translation)
        self.assertIn('claude-haiku-5.5', translation)
        self.assertIn('gpt-6.1-sol', metadata)
        self.assertIn('gemini-nano-banana-2.1', images)

    def test_image_refresh_prioritizes_recent_releases_and_skips_edit_only(self):
        rows = self.catalog({'a-older-image': {'supports_generation': True, 'created': 1, 'min_price_per_image': 1},
                             'z-newer-image': {'supports_generation': True, 'created': 100, 'min_price_per_image': 30},
                             'edit-only': {'supports_generation': False, 'created': 1000}},
                            app.fetch_aitunnel_image_model_catalog)
        self.assertEqual([row[0] for row in rows], ['z-newer-image', 'a-older-image'])


if __name__ == '__main__':
    unittest.main()
