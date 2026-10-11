"""builder/config/settings.py and builder/tracing/trace.py: how keys are loaded and kept out of every output."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from builder.config import settings
from builder.tracing.trace import Trace


class Environment(unittest.TestCase):
    def load(self, text, environ=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env.sh'
            path.write_bytes(text.encode('utf-8'))
            with mock.patch.dict('os.environ', environ or {}, clear=True), mock.patch('builtins.print') as printed:
                settings.load_env([Path(tmp) / 'missing.sh', path])
                return dict(os.environ), printed

    def test_parsing_comments_quotes_crlf_and_bom(self):
        env, printed = self.load('\ufeff# comment\r\n\r\nexport FREYA_API_KEY="synthetic private # value" # trailing\r\n'
                                 "export FREYA_WORKSPACE_ID='workspace'\r\nFREYA_API_URL=https://synthetic.invalid # trailing\r\n"
                                 'export EMPTY=\r\nnot an assignment\r\nexport BAD NAME=x\r\n')
        self.assertEqual(env, {'FREYA_API_KEY': 'synthetic private # value', 'FREYA_WORKSPACE_ID': 'workspace',
                               'FREYA_API_URL': 'https://synthetic.invalid'})
        printed.assert_not_called()

    def test_process_environment_wins_and_nothing_is_executed(self):
        env, _ = self.load('export FREYA_API_KEY=file-value\nexport OTHER=$(echo run)\nexport HOME_DIR=`pwd`\n', {'FREYA_API_KEY': 'process-value'})
        self.assertEqual(env['FREYA_API_KEY'], 'process-value')
        self.assertEqual((env['OTHER'], env['HOME_DIR']), ('$(echo run)', '`pwd`'))

    def test_search_order_and_locations(self):
        files = settings.env_files(['extra.sh'], 'some/kit')
        self.assertEqual(files, ['extra.sh', settings.ROOT / '.env.sh', settings.ROOT / '.env', Path('some/kit') / 'env.sh'])
        self.assertTrue((settings.ROOT / 'builder' / 'pipeline.py').is_file())
        self.assertEqual(settings.KIT, settings.ROOT / 'freya_starter_kit')

    def test_which_variables_count_as_secrets(self):
        with mock.patch.dict('os.environ', {'FREYA_API_KEY': 'long-enough-value', 'my_token': 'another-long-value', 'SHORT_KEY': 'abc',
                                            'FREYA_API_URL': 'https://synthetic.invalid', 'KEYBOARD': 'not-a-secret-name'}, clear=True):
            self.assertEqual(sorted(settings.secret_values()), ['another-long-value', 'long-enough-value'])


class Tracing(unittest.TestCase):
    def test_trace_and_console_never_show_a_secret(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('builtins.print') as printed, \
             mock.patch.dict('os.environ', {'FREYA_API_KEY': 'synthetic-private-marker', 'FREYA_BANK_TEAM_KEY': 'second-private-marker'}):
            trace = Trace(Path(tmp) / 'deep' / 't.txt')
            trace('FAILED: POST /x -> 401: bad key synthetic-private-marker and second-private-marker')
            text = (Path(tmp) / 'deep' / 't.txt').read_text(encoding='utf-8')
        self.assertNotIn('private-marker', text + str(printed.call_args_list))
        self.assertIn('bad key <redacted> and <redacted>', text)

    def test_format_is_one_timestamped_line_per_event_and_a_new_run_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('builtins.print'):
            path = Path(tmp) / 't.txt'
            path.write_text('old run\n', encoding='utf-8')
            trace = Trace(path)
            trace('build start | slot x')
            trace('build end | ok | 0 s')
            lines = path.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\S* build start \| slot x$')


if __name__ == '__main__':
    unittest.main()
