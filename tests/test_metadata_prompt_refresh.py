import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app_store_ppo_automation as app


class MetadataPromptRefreshTests(unittest.TestCase):
    def test_new_sonnet_is_available_in_metadata_catalog(self):
        self.assertIn('claude-sonnet-5.5', app.DEFAULT_AITUNNEL_METADATA_MODELS)

    def test_full_generation_requests_review_notes(self):
        worker = app.GeminiMetadataWorker(
            'key', 'model', 'en-US', 'Offline file manager with paid WebDAV.',
            app.DEFAULT_GEMINI_PROMPT, '', app_name='Driftbox',
        )
        with patch.object(app, 'ai_chat_completion', return_value=json.dumps({'description': 'Browse files.', 'reviewNotes': 'Driftbox transfers files over Wi-Fi.'})) as completion:
            worker.run()
        prompt = completion.call_args.args[3][0]['content']
        self.assertNotIn('Leave reviewNotes empty', prompt)
        self.assertIn('Generate non-empty reviewNotes for all or review_notes mode', prompt)
        self.assertIn('Editorial angle', prompt)

    def test_review_generation_does_not_invent_device_testing(self):
        self.assertNotIn('iPhone 15', app.REVIEW_NOTES_LETTER_INSTRUCTIONS)
        self.assertNotIn('Apple often rejects', app.REVIEW_NOTES_LETTER_INSTRUCTIONS)

    def test_full_generation_applies_notes(self):
        window = SimpleNamespace(
            gemini_worker=SimpleNamespace(generation_mode='all'),
            _apply_generated_review_notes=Mock(),
        )
        metadata = {'reviewNotes': 'Driftbox transfers files over Wi-Fi.'}
        app.MainWindow._apply_ai_metadata(window, metadata)
        window._apply_generated_review_notes.assert_called_once_with(metadata)

    def test_empty_notes_in_full_generation_reports_failure(self):
        worker = app.GeminiMetadataWorker('key', 'model', 'en-US', 'File manager', app.DEFAULT_GEMINI_PROMPT, '')
        generated = []
        logs = []
        worker.metadata_generated.connect(generated.append)
        worker.log_msg.connect(logs.append)
        with patch.object(app, 'ai_chat_completion', return_value='{"description":"File manager","reviewNotes":""}'):
            worker.run()
        self.assertEqual(generated, [])
        self.assertTrue(any('пустые Notes' in message for message in logs))

    def test_single_field_generation_keeps_notes_disabled(self):
        worker = app.GeminiMetadataWorker('key', 'model', 'en-US', 'File manager', app.DEFAULT_GEMINI_PROMPT, '', generation_mode='description')
        with patch.object(app, 'ai_chat_completion', return_value='{"description":"File manager"}') as completion:
            worker.run()
        self.assertIn('Leave reviewNotes empty', completion.call_args.args[3][0]['content'])
