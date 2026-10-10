"""Synthetic path diagnostics; no remote workflows or credentials."""
import unittest
from unittest.mock import patch
from builder.freya.integration import export_credential_findings


class ExportCredentialPathsTests(unittest.TestCase):
    def test_empty_and_nonempty_fields_classified_without_values(self):
        findings = export_credential_findings({'agent_config': {'llmConfig': {'apiKey': None}, 'sttConfig': {'api_key': 'private-marker'}}})
        self.assertEqual(findings[0], {'path': ['workflow', 'agent_config', 'llmConfig', 'apiKey'], 'classification': 'credential_field_empty'})
        self.assertEqual(findings[1]['classification'], 'credential_field_potentially_sensitive')
        self.assertNotIn('private-marker', str(findings))

    def test_unknown_keys_and_indices_are_masked(self):
        findings = export_credential_findings({'nodes': {'customer-private': {'tools': [{'secret': 'private-marker'}]}}})
        self.assertEqual(findings[0]['path'], ['workflow', 'nodes', '*', 'tools', '*', 'secret'])
        self.assertNotIn('customer-private', str(findings))

    def test_known_secret_text_and_url_userinfo(self):
        with patch.dict('os.environ', {'FREYA_API_KEY': 'synthetic-private-marker'}):
            findings = export_credential_findings({'body': 'synthetic-private-marker', 'url': 'https://private:private@example.invalid'})
        self.assertEqual({f['classification'] for f in findings}, {'known_process_secret', 'url_credentials'})
        self.assertNotIn('private', str(findings))

    def test_findings_are_bounded(self):
        self.assertEqual(len(export_credential_findings([{'secret': 'synthetic'}] * 100)), 20)
