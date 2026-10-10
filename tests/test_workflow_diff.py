"""Synthetic sanitized structural comparison."""
import json
import unittest
from builder.freya.integration import structural_diff


class StructuralDiffTests(unittest.TestCase):
    def test_categories_and_no_values_or_unknown_key_names(self):
        a = {'nodes': {'private-node': {'task_message': 'private-a', 'tools': []}}, 'initial_node': 'private-node', 'old-private': 1}
        b = {'nodes': {'private-node': {'task_message': 'private-b', 'tools': {}}}, 'initial_node': 'private-node', 'new-private': 2}
        diff = structural_diff(a, b)
        self.assertEqual({d['category'] for d in diff['changes']}, {'added', 'removed', 'value_changed', 'type_changed'})
        self.assertNotIn('private', json.dumps(diff))

    def test_known_defaults_and_empty_keys(self):
        a = {'nodes': {'start': {}}}
        b = {'nodes': {'start': {'assertions': [], 'transitions': []}}, 'agent_config': {'llmConfig': {'apiKey': None}}}
        diff = structural_diff(a, b)
        self.assertEqual({d['classification'] for d in diff['changes']}, {'known_platform_default', 'empty_optional_metadata', 'material_or_unconfirmed'})
        self.assertEqual(structural_diff({'agent_config': {'llmConfig': {}}},
                                         {'agent_config': {'llmConfig': {'apiKey': ''}}})['changes'], [])

    def test_nonempty_key_and_nested_key_not_ignored(self):
        diff = structural_diff({'agent_config': {'llmConfig': {}}}, {'agent_config': {'llmConfig': {'apiKey': 'private-secret', 'nested': {'apiKey': None}}}})
        self.assertEqual(len(diff['changes']), 2)
        self.assertNotIn('private-secret', json.dumps(diff))

    def test_array_order_types_and_truncation(self):
        diff = structural_diff({'tools': [True, 'a']}, {'tools': [1, 'b', {}]})
        self.assertEqual([d['category'] for d in diff['changes']], ['type_changed', 'value_changed', 'added'])
        diff = structural_diff({'tools': [0] * 200}, {'tools': [1] * 200})
        self.assertEqual(len(diff['changes']), 100)
        self.assertTrue(diff['truncated'])
