"""builder/validation/safety.py: no generated artifact may carry a credential."""
import json
import unittest
from unittest import mock

from builder.pipeline import build_workflow
from builder.validation.safety import leaks
from builder.validation.workflow import check_workflow
from benchmarks.harness.fixture_spec import spec as fixture
from tests.support import OPENAPI


def built(brief_b=True):
    return build_workflow(fixture(brief_b), OPENAPI)[0]


class Credentials(unittest.TestCase):
    def test_generated_artifacts_carry_no_credentials(self):
        with mock.patch.dict('os.environ', {'FREYA_API_KEY': 'synthetic-private-marker', 'ANTHROPIC_API_KEY': 'another-private-marker'}):
            workflow = built()
            self.assertEqual(leaks(workflow), [])
            self.assertEqual(leaks(fixture(True)), [])
            text = json.dumps(workflow)
        self.assertNotIn('private-marker', text)
        self.assertNotIn('Authorization', text)

    def test_leaks_are_found_located_and_never_echoed(self):
        with mock.patch.dict('os.environ', {'FREYA_API_KEY': 'synthetic-private-marker'}):
            found = leaks({'nodes': {'customer 42 private': {'tools': [{'headers': {'Authorization': {'default': 'x'}}}],
                                                             'task_message': 'use synthetic-private-marker now'}},
                           'a': 'key fak_abcdef123456', 'b': 'Bearer abcdefghijklmnop', 'c': 'see https://user:pw@bank.invalid/v1 for more',
                           'd': {'api_key': ''}, 'e': 'sk-ant-abc123456'})
        text = ' | '.join(found)
        for part in ('$.nodes.*.tools[0].headers: credential field', '$.nodes.*.task_message: a secret from the environment',
                     '$.a: credential-like text', '$.b: credential-like text', '$.c: credentials inside a URL', '$.d: credential field', '$.e: credential-like text'):
            self.assertIn(part, text)
        for private in ('synthetic-private-marker', 'customer 42', 'abcdef123456', 'user:pw'):
            self.assertNotIn(private, text)

    def test_ordinary_text_is_not_a_leak_and_findings_are_bounded(self):
        self.assertEqual(leaks({'t': 'Say the token of appreciation; visit https://bank.invalid/help. The bearer of this card pays.'}), [])
        self.assertEqual(len(leaks([{'secret': 'x'}] * 100)), 20)

    def test_a_leaking_workflow_fails_local_validation(self):
        workflow = built()
        workflow['nodes']['contact']['tools'][0]['function']['parameters']['properties']['headers']['properties']['Authorization'] = {'type': 'string', 'default': 'Bearer abcdefghijklmnop'}
        self.assertIn('credential leak at', ' | '.join(check_workflow(workflow)))


if __name__ == '__main__':
    unittest.main()
