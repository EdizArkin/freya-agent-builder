"""Synthetic client only: no imports of the official toolkit or HTTP requests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from builder.freya.integration import FreyaAdapter, IntegrationError, SavedWorkflow, load_workflow, main


WORKFLOW = {'initial_node': 'start', 'nodes': {'start': {'node_type': 'conversation', 'transitions': []}}}


class Client:
    AGENT_LLM = {'provider': 'event-provider', 'model': 'event-model'}

    def __init__(self):
        self.calls = []
        self.workflow = None
        self.versions = []

    def api(self, method, path, body=None, **params):
        self.calls.append((method, path, deepcopy(body), params))
        if params.get('dryRun') == 'true':
            return {'private_response': 'not-for-logs'}
        if method in ('POST', 'PUT') and path.startswith('/api/v2/workflow'):
            self.workflow = deepcopy(body['workflow'])
            self.versions.append({'id': 'version-' + str(len(self.versions) + 1), 'versionNumber': len(self.versions) + 1, 'isPublished': False})
            return {'workflow': {'id': 'workflow-1', 'agentId': 'agent-1'}}
        if path.endswith('/publish'):
            self.versions[-1]['isPublished'] = True
            return {}
        if '/versions/' in path:
            return {'versions': deepcopy(self.versions)}
        return {'workflow': {'id': 'workflow-1', 'agentId': 'agent-1', 'workflow': deepcopy(self.workflow)}}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'FREYA_WORKSPACE_ID': 'workspace-1', 'FREYA_API_KEY': 'synthetic-private-marker'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Client()
        self.adapter = FreyaAdapter(self.client, allow_external=True, dry_run_only=False)

    def test_local_load_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workflow.json'
            path.write_text(json.dumps(WORKFLOW), encoding='utf-8')
            self.assertEqual(load_workflow(path), WORKFLOW)
            path.write_text('{"nodes":{},"nodes":{}}', encoding='utf-8')
            with self.assertRaises(IntegrationError):
                load_workflow(path)

    def test_external_default_is_disabled(self):
        with self.assertRaises(IntegrationError):
            FreyaAdapter(self.client).dry_run(WORKFLOW)
        self.assertEqual(self.client.calls, [])

    def test_dry_run_cannot_write_or_publish(self):
        adapter = FreyaAdapter(self.client, allow_external=True)
        adapter.dry_run(WORKFLOW)
        self.assertEqual(self.client.calls[0][3], {'dryRun': 'true'})
        for action in (lambda: adapter.save(WORKFLOW),
                       lambda: adapter.publish(SavedWorkflow('workflow-1', 'agent-1', 'version-1', 1), approved=True),
                       lambda: adapter.export('workflow-1', 'outputs/unused.json')):
            with self.assertRaises(IntegrationError):
                action()
        self.assertEqual(len(self.client.calls), 1)

    def test_create_then_explicit_publish_exact_version(self):
        original = deepcopy(WORKFLOW)
        saved = self.adapter.save(WORKFLOW)
        self.assertEqual((saved.workflow_id, saved.agent_id), ('workflow-1', 'agent-1'))
        self.assertFalse(self.client.versions[0]['isPublished'])
        with self.assertRaises(IntegrationError):
            self.adapter.publish(saved)
        self.adapter.publish(saved, approved=True)
        self.assertEqual(self.client.calls[-1][2], {'versionId': saved.version_id})
        self.assertEqual(WORKFLOW, original)
        self.assertEqual(self.client.workflow['agent_config']['llmConfig'], self.client.AGENT_LLM)

    def test_update_saves_new_version_without_publish(self):
        first = self.adapter.save(WORKFLOW)
        second = self.adapter.save(WORKFLOW, workflow_id=first.workflow_id)
        self.assertEqual(second.version_number, 2)
        self.assertFalse(any(c[1].endswith('/publish') for c in self.client.calls))
        with self.assertRaises(IntegrationError):
            self.adapter.publish(first, approved=True)

    def test_model_conflict_fails_before_network(self):
        workflow = deepcopy(WORKFLOW)
        workflow['agent_config'] = {'llmConfig': {'provider': 'other'}}
        with self.assertRaises(IntegrationError):
            self.adapter.save(workflow)
        self.assertEqual(self.client.calls, [])

    def test_stored_model_mismatch_blocks_publish(self):
        saved = self.adapter.save(WORKFLOW)
        self.client.workflow.pop('agent_config')
        with self.assertRaises(IntegrationError):
            self.adapter.publish(saved, approved=True)
        self.assertFalse(any(c[1].endswith('/publish') for c in self.client.calls))

    def test_ambiguous_new_versions_block(self):
        original = self.client.api
        def api(method, path, body=None, **params):
            result = original(method, path, body, **params)
            if method == 'POST' and path == '/api/v2/workflow' and not params:
                self.client.versions.append({'id': 'concurrent', 'versionNumber': 2})
            return result
        self.client.api = api
        with self.assertRaises(IntegrationError):
            self.adapter.save(WORKFLOW)

    def test_failure_response_never_logged(self):
        self.client.api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic-private-marker'))
        with self.assertRaises(IntegrationError) as caught:
            self.adapter.dry_run(WORKFLOW)
        self.assertNotIn('synthetic-private-marker', str(caught.exception) + json.dumps(self.adapter.events))

    def test_credentials_rejected(self):
        for addition in ({'prompt': 'synthetic-private-marker'}, {'headers': {'Authorization': 'Bearer synthetic'}}, {'api_key': 'other'}):
            workflow = {**WORKFLOW, **addition}
            with self.assertRaises(IntegrationError):
                self.adapter.dry_run(workflow)
        self.assertEqual(self.client.calls, [])

    def test_export_is_private_and_omits_customer_defaults(self):
        self.adapter.save({**WORKFLOW, 'test_variables': {'private_record': 'synthetic'}})
        with patch('builder.freya.integration.private_write') as writer:
            self.adapter.export('workflow-1', 'outputs/export.json')
            data = writer.call_args.args[1]
            self.assertNotIn('test_variables', data['workflow'])
            self.assertEqual(data['agent_id'], 'agent-1')
        self.client.workflow['api_key'] = 'secret'
        with self.assertRaises(IntegrationError):
            self.adapter.export('workflow-1', 'outputs/export.json')

    def test_output_restriction_and_trace_allowlist(self):
        from builder.freya.integration import private_write
        with self.assertRaises(IntegrationError):
            private_write('examples/export.json', {})
        self.adapter.dry_run(WORKFLOW)
        with patch('builder.freya.integration.private_write') as writer:
            self.adapter.trace('outputs/trace.json')
            self.assertEqual(writer.call_args.args[1], {'operations': [{'operation': 'POST', 'status': 'completed'}]})

    def test_cli_no_opt_in_never_loads_client(self):
        with patch('builder.freya.integration.official_client') as client:
            self.assertEqual(main(['export', '--workflow-id', 'workflow-1']), 1)
            self.assertEqual(main(['publish', '--allow-external']), 1)
            client.assert_not_called()
