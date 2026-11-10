import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import credential_env


class CredentialAliasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'keys.env'
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def load_file(self, text):
        self.path.write_text(text, encoding='utf-8')
        credential_env.load({'credentials_env_file': str(self.path)})

    def test_alias_from_file_and_environment_without_file(self):
        self.load_file('GRAPHRAG_API_KEY=file-alias\nGRAPHRAG_API_KEY2=second\nUNRELATED=ignored\n')
        self.assertEqual(os.environ['BLABLADOR_KEY'], 'file-alias')
        self.assertEqual(os.environ['GRAPHRAG_API_KEY2'], 'second')
        self.assertNotIn('UNRELATED', os.environ)
        del os.environ['BLABLADOR_KEY']
        os.environ['GRAPHRAG_API_KEY'] = 'shell-alias'
        credential_env.load({})
        self.assertEqual(os.environ['BLABLADOR_KEY'], 'shell-alias')

    def test_explicit_canonical_keys_precede_aliases(self):
        os.environ.update(BLABLADOR_KEY='shell-canonical', GRAPHRAG_API_KEY='shell-alias')
        self.load_file('BLABLADOR_KEY=file-canonical\nGRAPHRAG_API_KEY=file-alias\n')
        self.assertEqual(os.environ['BLABLADOR_KEY'], 'shell-canonical')
        del os.environ['BLABLADOR_KEY']
        self.load_file('BLABLADOR_KEY=file-canonical\nGRAPHRAG_API_KEY=file-alias\n')
        self.assertEqual(os.environ['BLABLADOR_KEY'], 'file-canonical')

    def test_environment_alias_precedes_file_alias(self):
        os.environ['GRAPHRAG_API_KEY'] = 'shell-alias'
        self.load_file('GRAPHRAG_API_KEY=file-alias\n')
        self.assertEqual(os.environ['BLABLADOR_KEY'], 'shell-alias')

    def test_empty_values_and_no_configured_file(self):
        credential_env.load({})
        self.assertNotIn('BLABLADOR_KEY', os.environ)
        self.load_file('BLABLADOR_KEY=\nGRAPHRAG_API_KEY=\n')
        self.assertNotIn('BLABLADOR_KEY', os.environ)
        os.environ['BLABLADOR_KEY'] = ''
        self.load_file('GRAPHRAG_API_KEY=file-alias\n')
        self.assertEqual(os.environ['BLABLADOR_KEY'], 'file-alias')

    def test_configured_missing_path_still_fails_and_values_are_not_interpolated(self):
        with self.assertRaisesRegex(ValueError, 'nicht gefunden'):
            credential_env.load({'credentials_env_file': str(self.path)})
        with self.assertRaises(ValueError):
            credential_env.load({'credentials_env_file': self.temp.name})
        self.load_file('GRAPHRAG_API_KEY=${DO_NOT_EXPAND}\n')
        self.assertEqual(os.environ['BLABLADOR_KEY'], '${DO_NOT_EXPAND}')


if __name__ == '__main__':
    unittest.main()
