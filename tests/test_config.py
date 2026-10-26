from pathlib import Path
import tempfile
import unittest

from server import load_runtime_env, configured_app, Store


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'.env'

    def load(self, text, environment=None):
        self.path.write_text(text)
        return load_runtime_env(self.path, {} if environment is None else environment)

    def test_credentials_from_file_and_environment_override(self):
        config=self.load('APP_USER=family\nAPP_PASSWORD=file-password\n', {'APP_PASSWORD':'explicit-password'})
        self.assertEqual(config['APP_USER'],'family')
        self.assertEqual(config['APP_PASSWORD'],'explicit-password')

    def test_comments_quotes_literal_special_characters(self):
        config=self.load('''# Comment
export APP_USER = "Family name" # Comment
APP_PASSWORD='pa$word # literal'
TZ=America/New_York # Comment
UNKNOWN=ignored
''')
        self.assertEqual(config['APP_USER'],'Family name')
        self.assertEqual(config['APP_PASSWORD'],'pa$word # literal')
        self.assertEqual(config['TZ'],'America/New_York')
        self.assertNotIn('UNKNOWN',config)
        self.assertEqual(self.load('APP_PASSWORD=abc#123')['APP_PASSWORD'],'abc#123')

    def test_missing_file_supports_existing_environment_deployment(self):
        config=load_runtime_env(self.path,{'APP_PASSWORD':'env-password'})
        self.assertEqual(config['APP_PASSWORD'],'env-password')
        with self.assertRaisesRegex(ValueError,'/data/.env'):
            configured_app(load_runtime_env(self.path,{}))

    def test_invalid_file_does_not_disclose_password(self):
        for text in ['APP_PASSWORD="super-secret', 'not-an-assignment']:
            with self.assertRaises(ValueError) as error:
                self.load(text)
            self.assertNotIn('super-secret',str(error.exception))

    def test_literal_empty_values(self):
        self.assertEqual(self.load('APP_PASSWORD=""')['APP_PASSWORD'],'')
        with self.assertRaises(ValueError):
            configured_app(self.load('APP_PASSWORD='))

    def test_configured_application_uses_file_database(self):
        database=Path(self.temp.name)/'custom.sqlite3'
        config=self.load(f'APP_USER=family\nAPP_PASSWORD=test-only\nDB_PATH={database}\n')
        app=configured_app(config)
        self.assertTrue(callable(app))
        self.assertEqual(Store(database).read()['revision'],0)


if __name__=='__main__':
    unittest.main()
