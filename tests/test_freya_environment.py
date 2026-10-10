"""Synthetic configuration files only; no real credential files are read."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder.freya.environment import EnvironmentConfigError, load_project_environment
from builder.freya.integration import official_client, IntegrationError


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '.env.sh'

    def write(self, text):
        self.path.write_text(text, encoding='utf-8')

    def test_parsing_comments_quotes_and_crlf(self):
        self.write('# comment\n\nexport FREYA_API_KEY="synthetic private # value" # trailing\n'
                   "export FREYA_WORKSPACE_ID='workspace'\n"
                   'export FREYA_API_URL=https://synthetic.invalid # trailing\n'
                   'export FREYA_SIM_CONCURRENCY=5\n')
        env = {}
        self.assertIsNone(load_project_environment(self.path, environ=env))
        self.assertEqual(env['FREYA_API_KEY'], 'synthetic private # value')
        self.assertEqual(env['FREYA_WORKSPACE_ID'], 'workspace')
        self.assertEqual(env['FREYA_API_URL'], 'https://synthetic.invalid')

    def test_precedence_and_empty_values(self):
        self.write('export FREYA_API_KEY=file-value\nexport FREYA_WORKSPACE_ID=from-file\n')
        env = {'FREYA_API_KEY': 'process-value', 'FREYA_WORKSPACE_ID': '', 'OTHER': 'untouched'}
        load_project_environment(self.path, environ=env)
        self.assertEqual(env, {'FREYA_API_KEY': 'process-value', 'FREYA_WORKSPACE_ID': 'from-file', 'OTHER': 'untouched'})
        env['FREYA_API_KEY'] = ' '
        load_project_environment(self.path, environ=env)
        self.assertEqual(env['FREYA_API_KEY'], ' ')

    def test_missing_preserves_environment(self):
        env = {'FREYA_API_KEY': 'existing'}
        load_project_environment(self.path, environ=env)
        self.assertEqual(env, {'FREYA_API_KEY': 'existing'})

    def test_rejects_shell_and_unknown_and_duplicates_atomically(self):
        for line in ('FREYA_API_KEY=private', 'export FREYA_API_KEY=$(private)',
                     'export FREYA_API_KEY=`private`', 'export FREYA_API_KEY=$OTHER',
                     'export FREYA_API_KEY=private;echo', 'export FREYA_API_KEY="private',
                     'export FREYA_API_KEY="private" extra', 'export FREYA_API_URL=x\nexport FREYA_API_URL=y'):
            with self.subTest(line=line):
                self.write('export FREYA_WORKSPACE_ID=workspace\n' + line)
                env = {}
                with self.assertRaises(EnvironmentConfigError):
                    load_project_environment(self.path, environ=env)
                self.assertEqual(env, {})

    def test_secret_redaction_and_no_output(self):
        self.write('export FREYA_API_KEY="private-marker" trailing')
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            with self.assertRaises(EnvironmentConfigError) as caught:
                load_project_environment(self.path, environ={})
        self.assertNotIn('private-marker', str(caught.exception))
        self.assertEqual(output.getvalue(), '')

    def test_bom_and_empty_assignment(self):
        self.write('\ufeffexport FREYA_API_KEY=\n')
        env = {}
        load_project_environment(self.path, environ=env)
        self.assertEqual(env, {'FREYA_API_KEY': ''})

    def test_decode_error_is_sanitized(self):
        self.path.write_bytes(b'\xffprivate-marker')
        with self.assertRaises(EnvironmentConfigError) as caught:
            load_project_environment(self.path, environ={})
        self.assertNotIn('private-marker', str(caught.exception))

    def test_load_before_client_import(self):
        self.write('export FREYA_API_KEY=synthetic-private\nexport FREYA_API_URL=https://synthetic.invalid\n')
        client = Path(self.temp.name) / 'client.py'
        client.write_text('import os\nURL=os.environ["FREYA_API_URL"]\nKEY=os.environ["FREYA_API_KEY"]\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True), patch('builder.freya.environment.PROJECT_ROOT', Path(self.temp.name)):
            loaded = official_client(client)
            self.assertEqual(loaded.URL, 'https://synthetic.invalid')
            self.assertEqual(loaded.KEY, 'synthetic-private')

    def test_bad_file_blocks_client_import_safely(self):
        self.write('export FREYA_API_KEY="private-marker" invalid')
        with patch('builder.freya.environment.PROJECT_ROOT', Path(self.temp.name)), patch('builder.freya.integration.importlib.util.spec_from_file_location') as importer:
            with self.assertRaises(IntegrationError) as caught:
                official_client('unused')
            importer.assert_not_called()
            self.assertNotIn('private-marker', str(caught.exception))

    def test_unrelated_exports_are_not_loaded(self):
        self.write('export FREYA_API_URL=https://synthetic.invalid\r\nexport OTHER=$(never-executed)\r\n')
        env = {'FREYA_API_KEY': 'process-private'}
        load_project_environment(self.path, environ=env)
        self.assertEqual(env, {'FREYA_API_KEY': 'process-private', 'FREYA_API_URL': 'https://synthetic.invalid'})

    def test_parse_diagnostic_has_safe_reason_and_line(self):
        self.write('# comment\nexport FREYA_API_KEY="private-marker" trailing\n')
        with patch('builder.freya.environment.PROJECT_ROOT', Path(self.temp.name)):
            with self.assertRaises(IntegrationError) as caught:
                official_client('unused')
        self.assertEqual(caught.exception.diagnostic['category'], 'environment_parsing')
        self.assertEqual(caught.exception.diagnostic['line'], 2)
        self.assertEqual(caught.exception.diagnostic['reason'], 'quoted_syntax')
        self.assertNotIn('private-marker', str(caught.exception))

    def test_client_missing_and_initialization_distinguished(self):
        with patch('builder.freya.integration.load_project_environment'), patch.dict('os.environ', {'FREYA_API_KEY': 'synthetic', 'FREYA_API_URL': 'https://synthetic.invalid'}):
            with self.assertRaises(IntegrationError) as caught:
                official_client(Path(self.temp.name) / 'absent.py')
            self.assertEqual(caught.exception.diagnostic['category'], 'client_missing')
            client = Path(self.temp.name) / 'broken.py'
            client.write_text('raise ValueError("private-marker")', encoding='utf-8')
            with self.assertRaises(IntegrationError) as caught:
                official_client(client)
            self.assertEqual(caught.exception.diagnostic['category'], 'client_initialization')
            self.assertNotIn('private-marker', str(caught.exception))

    def test_workflow_missing_and_invalid_distinguished(self):
        from builder.freya.integration import load_workflow
        with self.assertRaises(IntegrationError) as caught:
            load_workflow(self.path)
        self.assertEqual(caught.exception.diagnostic['category'], 'workflow_missing')
        self.write('invalid-private-marker')
        with self.assertRaises(IntegrationError) as caught:
            load_workflow(self.path)
        self.assertEqual(caught.exception.diagnostic['category'], 'workflow_configuration')
