import json
import os
import tempfile
import unittest
from unittest.mock import patch

from liga_kit.cli import build_parser
from liga_kit.config import Settings
from liga_kit.report import write_report


class LigaConfigCliTests(unittest.TestCase):
    def test_settings_defaults_match_approved_integration(self):
        settings = Settings(kit_token='x')
        self.assertEqual(
            settings.feed_url,
            'https://ligadivanov.ru/acrit.export/ym_vendormodel_2023.xml',
        )
        self.assertEqual(settings.warehouse_names, ('СПБ', 'МСК'))

    def test_settings_requires_kit_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                Settings.from_env()

    def test_settings_reads_kit_token_without_samson_secret(self):
        with patch.dict(os.environ, {'YANDEX_KIT_TOKEN':' token '}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.kit_token, 'token')

    def test_cli_parser_supports_dry_run_limit_and_report(self):
        args = build_parser().parse_args([
            '--dry-run',
            '--max-items','3',
            '--report','/tmp/liga.json',
        ])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.max_items, 3)
        self.assertEqual(args.report, '/tmp/liga.json')

    def test_report_is_written_atomically_as_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'state', 'last_sync.json')
            write_report(path, {'status':'ok','offers_seen':2})
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['offers_seen'], 2)


if __name__ == '__main__':
    unittest.main()
