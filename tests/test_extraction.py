"""builder/extraction/claude.py: the extraction and repair loop, with the model replaced by prepared replies."""
import json
import unittest
from unittest import mock

from builder.config import settings
from builder.extraction import claude
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import validate
from tests.fixture_spec import spec as fixture
from tests.support import KIT, OPENAPI

OPENAPI_TEXT = (KIT / 'tools/openapi.json').read_text(encoding='utf-8')
API = Api(OPENAPI)
BRIEFS = [('brief-a.md', (KIT / 'briefs/brief-a.md').read_text(encoding='utf-8'))]


class Extraction(unittest.TestCase):
    def test_accepts_a_fenced_reply_and_sends_every_document(self):
        client = claude.ReplayClient(['Here it is:\n```json\n' + json.dumps(fixture()) + '\n```'])
        lines = []
        result = claude.extract(BRIEFS + [('memo.txt', 'A later memo.')], OPENAPI_TEXT, client, lambda s: validate(s, API), lines.append)
        self.assertEqual(result['spec_version'], 'agent-spec/1')
        sent = client.seen[0][1][0]['content']
        for part in ('<tool_api>', 'name="brief-a.md"', 'index="2" name="memo.txt"', 'verify_identity'):
            self.assertIn(part, sent)
        self.assertIn('purpose: extract agent-spec/1', lines[0])

    def test_compiler_errors_go_back_to_the_model_once(self):
        broken = fixture()
        broken['promise']['amount_param'] = 'sum'
        client = claude.ReplayClient(['not json at all', json.dumps(broken), json.dumps(fixture())])
        result = claude.extract(BRIEFS, OPENAPI_TEXT, client, lambda s: validate(s, API))
        self.assertEqual(result['promise']['amount_param'], 'amount')
        self.assertIn("promise.amount_param='sum' is not a body field of create_promise", client.seen[2][1][-1]['content'])
        with self.assertRaises(SpecError):
            claude.extract(BRIEFS, OPENAPI_TEXT, claude.ReplayClient(['{}', '{}', '{}']), lambda s: validate(s, API))

    def test_prompt_holds_no_brief_values(self):
        for value in ('2026-10-26', 'Freya', 'minimum_payment', 'phone_last4', 'wrong_number', 'restructuring'):
            self.assertNotIn(value, claude.SYSTEM)

    def test_the_repair_loop_is_bounded_and_keeps_the_conversation(self):
        client = claude.ReplayClient(['{}'] * 5)
        lines = []
        with self.assertRaises(SpecError):
            claude.extract(BRIEFS, OPENAPI_TEXT, client, lambda s: validate(s, API), lines.append)
        self.assertEqual(len(client.seen), 3, 'one extraction and at most two repairs')
        self.assertEqual([m['role'] for m in client.seen[2][1]], ['user', 'assistant', 'user', 'assistant', 'user'])
        self.assertEqual(sum('model call' in line for line in lines), 3)
        self.assertEqual(sum('spec rejected' in line for line in lines), 3)

    def test_model_selection_order(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(claude.AnthropicClient().model, settings.DEFAULT_MODEL)
            self.assertEqual(settings.DEFAULT_MODEL, 'claude-sonnet-5-5')
        with mock.patch.dict('os.environ', {'BUILDER_LLM_MODEL': 'from-env'}, clear=True):
            self.assertEqual(claude.AnthropicClient().model, 'from-env')
            client = claude.AnthropicClient('from-flag')
            self.assertEqual((client.model, client.max_tokens, client.effort), ('from-flag', 32000, 'high'))

    def test_json_is_found_inside_surrounding_text(self):
        self.assertEqual(claude.parse_json('note {"a": {"b": 1}} trailing'), {'a': {'b': 1}})
        for text in ('', 'no object here', '{"a": '):
            with self.assertRaises(ValueError):
                claude.parse_json(text)


if __name__ == '__main__':
    unittest.main()
