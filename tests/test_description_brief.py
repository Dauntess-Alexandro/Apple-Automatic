import os
import tempfile
import unittest
from unittest.mock import patch

import app_store_ppo_automation as app


class DescriptionBriefTests(unittest.TestCase):
    def test_reads_markdown_with_bom_and_rejects_empty_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'description.md')
            with open(path, 'w', encoding='utf-8-sig') as source:
                source.write('Описание Driftbox: перенос файлов по Wi-Fi.')
            self.assertEqual(app.read_description_file(path), 'Описание Driftbox: перенос файлов по Wi-Fi.')
            with open(path, 'w') as source:
                source.write('   ')
            with self.assertRaises(ValueError):
                app.read_description_file(path)

    def test_generates_brief_from_description_without_images(self):
        worker = app.DescriptionBriefWorker('key', 'model', 'No account. Premium subscriptions.', app_name='Driftbox')
        briefs = []
        worker.brief_ready.connect(briefs.append)
        with patch.object(app, 'ai_chat_completion', return_value='Driftbox product brief') as completion:
            worker.run()
        self.assertEqual(briefs, ['Driftbox product brief'])
        messages = completion.call_args.args[3]
        self.assertEqual(messages[0]['role'], 'system')
        self.assertIn('No account. Premium subscriptions.', messages[1]['content'])
        self.assertIn('Driftbox', messages[1]['content'])
        self.assertIsInstance(messages[1]['content'], str)

    def test_empty_ai_response_reports_failure(self):
        worker = app.DescriptionBriefWorker('key', 'model', 'File transfer app')
        errors = []
        worker.brief_failed.connect(errors.append)
        with patch.object(app, 'ai_chat_completion', return_value=' '):
            worker.run()
        self.assertEqual(len(errors), 1)
        self.assertIn('пустое ТЗ', errors[0])
