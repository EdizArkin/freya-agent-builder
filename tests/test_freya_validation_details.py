"""Untrusted synthetic validation bodies; no server requests."""
import json
import unittest
from builder.freya.integration import classify_failure, safe_validation_details


class ValidationDetailsTests(unittest.TestCase):
    def test_allowlisted_fields_and_codes_only(self):
        body = json.dumps({'data': {'errors': [{'path': ['workflow', 'nodes', 'private-name', 'max_turn_transition'],
                          'code': 'invalid_type', 'message': 'private-marker', 'received': 'private-marker'}]}})
        error = classify_failure(RuntimeError('POST /api/v2/workflow -> 400: ' + body), 'POST', '/api/v2/workflow')
        self.assertEqual(error.diagnostic['validation_details'], [{'path': ['workflow', 'nodes', '*', 'max_turn_transition'], 'code': 'invalid_type'}])
        self.assertNotIn('private', str(error))

    def test_arbitrary_messages_codes_and_truncated_json_suppressed(self):
        for body in ('{"errors":', 'private-marker', '{"message":"private-marker"}',
                     '{"errors":[{"path":["workflow"],"code":"private-marker"}]}'):
            self.assertEqual(safe_validation_details(body), [])

    def test_bounded_results_and_numeric_paths_redacted(self):
        body = json.dumps({'issues': [{'path': ['tools', 999, 'function'], 'code': 'required'}] * 100})
        details = safe_validation_details(body)
        self.assertEqual(len(details), 20)
        self.assertEqual(details[0]['path'], ['tools', '*', 'function'])
        self.assertEqual(safe_validation_details(' ' * 16385), [])
