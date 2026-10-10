"""Synthetic client only: no imports of the official toolkit or HTTP requests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
from urllib.error import HTTPError, URLError

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
            # Synthetic platform default; adapter itself must not inject it.
            self.workflow.setdefault('agent_config', {'llmConfig': dict(self.AGENT_LLM)})
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
        loader = patch('builder.freya.integration.load_project_environment')
        loader.start()
        self.addCleanup(loader.stop)
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

    def test_http_categories_official_wrapped_and_typed(self):
        cases = [(401, 'authentication'), (403, 'authentication'), (404, 'endpoint_missing'),
                 (400, 'validation'), (422, 'validation'), (429, 'rate_limit'),
                 (500, 'server'), (503, 'server'), (409, 'http')]
        for status, category in cases:
            for exc in (RuntimeError(f'POST /api/v2/workflow -> {status}: synthetic-private-marker'),
                        HTTPError('https://private.invalid/?token=private', status, 'private', {}, None)):
                with self.subTest(status=status, exception=type(exc).__name__):
                    def fail(*args, **kwargs):
                        raise exc
                    self.client.api = fail
                    with self.assertRaises(IntegrationError) as caught:
                        self.adapter.dry_run(WORKFLOW)
                    diagnostic = caught.exception.diagnostic
                    self.assertEqual(diagnostic['category'], category)
                    self.assertEqual(diagnostic['http_status'], status)
                    self.assertEqual(diagnostic['endpoint'], '/api/v2/workflow')
                    self.assertEqual(set(diagnostic), {'category', 'http_status', 'endpoint', 'hint'})
                    self.assertNotIn('private', json.dumps(diagnostic))

    def test_network_timeout_response_and_unknown(self):
        for exc, category in [(URLError('synthetic-private-marker'), 'network'),
                              (ConnectionResetError('private'), 'network'),
                              (TimeoutError('private'), 'timeout'),
                              (URLError(TimeoutError('private')), 'timeout'),
                              (json.JSONDecodeError('private', 'private', 0), 'response'),
                              (RuntimeError('private -> 401: body'), 'unknown')]:
            with self.subTest(category=category):
                def fail(*args, **kwargs):
                    raise exc
                self.client.api = fail
                with self.assertRaises(IntegrationError) as caught:
                    self.adapter.dry_run(WORKFLOW)
                self.assertEqual(caught.exception.diagnostic['category'], category)
                self.assertIsNone(caught.exception.diagnostic['http_status'])
                self.assertNotIn('private', str(caught.exception))

    def test_configuration_and_cli_redaction(self):
        from builder.freya.integration import official_client
        with patch.dict('os.environ', {'FREYA_API_KEY': ''}):
            with self.assertRaises(IntegrationError) as caught:
                official_client('absent')
            self.assertEqual(caught.exception.diagnostic['category'], 'configuration')
        for url in ('', 'https://user:private@host', 'https://host?token=private', 'https://host/api', 'https://[invalid'):
            with patch.dict('os.environ', {'FREYA_API_URL': url}):
                with self.assertRaises(IntegrationError) as caught:
                    official_client('absent')
                self.assertNotIn('private', str(caught.exception))
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['export']), 1)
        self.assertEqual(json.loads(output.getvalue())['category'], 'configuration')

    def test_current_environment_at_import_and_documented_default(self):
        from builder.freya.integration import official_client
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic_client.py'
            path.write_text('import os\nURL = os.environ.get("FREYA_API_URL", "https://staging-app.freyavoice.ai")\nHEADERS = {"Authorization": "Bearer " + os.environ.get("FREYA_API_KEY", "")}\n', encoding='utf-8')
            with patch.dict('os.environ', {'FREYA_API_URL': 'https://synthetic.invalid', 'FREYA_API_KEY': 'synthetic-first'}):
                first = official_client(path)
                self.assertEqual(first.URL, 'https://synthetic.invalid')
                self.assertEqual(first.HEADERS['Authorization'], 'Bearer synthetic-first')
            with patch.dict('os.environ', {'FREYA_API_KEY': 'synthetic-second'}):
                import os
                os.environ.pop('FREYA_API_URL', None)
                second = official_client(path)
                self.assertEqual(second.URL, 'https://staging-app.freyavoice.ai')
                self.assertEqual(second.HEADERS['Authorization'], 'Bearer synthetic-second')

    def test_trace_and_cli_http_diagnostic_no_private_data(self):
        self.client.api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('POST /api/v2/workflow -> 422: synthetic-private-marker Authorization body'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workflow.json'
            path.write_text(json.dumps(WORKFLOW), encoding='utf-8')
            output = StringIO()
            with patch('builder.freya.integration.official_client', return_value=self.client), redirect_stdout(output), patch('builder.freya.integration.private_write') as writer:
                self.assertEqual(main(['dry-run', '--workflow', str(path), '--allow-external', '--trace', 'outputs/test.json']), 1)
                trace = writer.call_args.args[1]
            self.assertEqual(json.loads(output.getvalue())['http_status'], 422)
            self.assertEqual(trace['operations'][0]['diagnostic']['category'], 'validation')
            self.assertNotIn('synthetic-private-marker', output.getvalue() + json.dumps(trace))

    def test_route_template_excludes_identifiers_and_queries(self):
        from builder.freya.integration import safe_endpoint
        self.assertEqual(safe_endpoint('/api/v2/workflow/private-id'), '/api/v2/workflow/{workflowId}')
        self.assertIsNone(safe_endpoint('/api/v2/workflow?token=private'))

    def test_explicit_failure_envelope_is_sanitized(self):
        self.client.api = lambda *a, **k: {'success': False, 'private': 'synthetic-private-marker'}
        with self.assertRaises(IntegrationError) as caught:
            self.adapter.dry_run(WORKFLOW)
        self.assertEqual(caught.exception.diagnostic['category'], 'response')
        self.assertNotIn('synthetic-private-marker', str(caught.exception))

    def test_dry_run_envelope_matches_official_contract(self):
        self.adapter.dry_run(WORKFLOW)
        method, path, body, params = self.client.calls[-1]
        self.assertEqual((method, path, params), ('POST', '/api/v2/workflow', {'dryRun': 'true'}))
        self.assertEqual(set(body), {'name', 'workflow'})
        self.assertEqual(body['workflow']['nodes'], WORKFLOW['nodes'])
        self.assertNotIn('agent_config', body['workflow'])
        self.assertEqual(body['workflow'], WORKFLOW)

    def test_legacy_comparison_matches_default(self):
        original = deepcopy(WORKFLOW)
        adapter = FreyaAdapter(self.client, allow_external=True)
        adapter.dry_run(WORKFLOW)
        standard = deepcopy(self.client.calls[-1])
        adapter.dry_run(WORKFLOW, omit_injected_agent_config=True)
        comparison = self.client.calls[-1]
        self.assertEqual(standard, comparison)
        self.assertEqual(comparison[2]['workflow'], original)
        self.assertEqual(WORKFLOW, original)

    def test_comparison_preserves_supplied_configuration(self):
        workflow = {**deepcopy(WORKFLOW), 'agent_config': {'voice': 'synthetic', 'llmConfig': dict(self.client.AGENT_LLM)}}
        original = deepcopy(workflow)
        FreyaAdapter(self.client, allow_external=True).dry_run(workflow, omit_injected_agent_config=True)
        self.assertEqual(self.client.calls[-1][2]['workflow'], original)
        self.assertEqual(workflow, original)

    def test_comparison_cannot_bypass_credentials_or_model_checks(self):
        adapter = FreyaAdapter(self.client, allow_external=True)
        for workflow in ({**WORKFLOW, 'api_key': 'synthetic-private'},
                         {**WORKFLOW, 'agent_config': {'llmConfig': {'model': 'other'}}}):
            with self.assertRaises(IntegrationError):
                adapter.dry_run(workflow, omit_injected_agent_config=True)
        self.assertEqual(self.client.calls, [])
        with self.assertRaises(IntegrationError):
            self.adapter.dry_run(WORKFLOW, omit_injected_agent_config=True)

    def test_comparison_cli_restricted_before_client_import(self):
        with patch('builder.freya.integration.official_client') as client:
            self.assertEqual(main(['save', '--omit-injected-agent-config', '--allow-external']), 1)
            client.assert_not_called()

    def test_default_preserves_explicit_config_exactly(self):
        for config in ({'llmConfig': dict(self.client.AGENT_LLM), 'voice': 'synthetic'},
                       {'voice': 'synthetic'}, {'llmConfig': {'model': self.client.AGENT_LLM['model']}}):
            workflow = {**deepcopy(WORKFLOW), 'agent_config': config}
            original = deepcopy(workflow)
            FreyaAdapter(self.client, allow_external=True).dry_run(workflow)
            self.assertEqual(self.client.calls[-1][2]['workflow'], original)
            self.assertEqual(workflow, original)

    def test_save_does_not_inject_request_config(self):
        self.adapter.save(WORKFLOW)
        writes = [c for c in self.client.calls if c[0] == 'POST' and c[1] == '/api/v2/workflow']
        self.assertEqual(len(writes), 2)
        self.assertTrue(all(c[2]['workflow'] == WORKFLOW for c in writes))

    def test_save_workspace_preflight_has_specific_sanitized_errors(self):
        for value, category in (('', 'workspace_missing'), (' ', 'workspace_missing'),
                                ('private/value', 'workspace_invalid')):
            with patch.dict('os.environ', {'FREYA_WORKSPACE_ID': value}):
                with self.assertRaises(IntegrationError) as caught:
                    self.adapter.save(WORKFLOW)
            self.assertEqual(caught.exception.diagnostic['category'], category)
            self.assertIsNone(caught.exception.diagnostic['endpoint'])
            self.assertNotIn('private/value', str(caught.exception))
            self.assertEqual(self.client.calls, [])

    def test_dry_run_does_not_require_workspace(self):
        with patch.dict('os.environ', {'FREYA_WORKSPACE_ID': ''}):
            FreyaAdapter(self.client, allow_external=True).dry_run(WORKFLOW)
        self.assertEqual(self.client.calls[-1][3], {'dryRun': 'true'})

    def test_save_permission_error_is_specific(self):
        with self.assertRaises(IntegrationError) as caught:
            FreyaAdapter(self.client, allow_external=True).save(WORKFLOW)
        self.assertEqual(caught.exception.diagnostic['category'], 'operation_permission')
        self.assertEqual(self.client.calls, [])

    def test_export_post_get_credential_failure_is_specific(self):
        self.adapter.save(WORKFLOW)
        self.client.workflow['api_key'] = 'private-marker'
        with patch('builder.freya.integration.private_write') as writer:
            with self.assertRaises(IntegrationError) as caught:
                self.adapter.export('workflow-1', 'outputs/synthetic.json')
            writer.assert_not_called()
        self.assertEqual(caught.exception.diagnostic['category'], 'artifact_credentials')
        self.assertNotIn('private-marker', str(caught.exception))
        self.assertEqual(self.adapter.events[-1]['status'], 'completed')

    def test_export_bad_readback_identity_is_specific(self):
        self.client.api = lambda *a, **k: {'workflow': {'id': 'other', 'agentId': 'private', 'workflow': {}}}
        with self.assertRaises(IntegrationError) as caught:
            self.adapter.export('workflow-1', 'outputs/synthetic.json')
        self.assertEqual(caught.exception.diagnostic['category'], 'workflow_response')

    def test_artifact_errors_do_not_overwrite_or_leak(self):
        from builder.freya.integration import private_write
        with self.assertRaises(IntegrationError) as caught:
            private_write('examples/private.json', {})
        self.assertEqual(caught.exception.diagnostic['category'], 'artifact_path')
        with patch('pathlib.Path.open', side_effect=FileExistsError('private-marker')):
            with self.assertRaises(IntegrationError) as caught:
                private_write('outputs/synthetic.json', {})
        self.assertEqual(caught.exception.diagnostic['category'], 'artifact_exists')
        with patch('pathlib.Path.open', side_effect=PermissionError('private-marker')):
            with self.assertRaises(IntegrationError) as caught:
                private_write('outputs/synthetic.json', {})
        self.assertEqual(caught.exception.diagnostic['category'], 'artifact_write')
        self.assertNotIn('private-marker', str(caught.exception))

    def test_export_removes_only_confirmed_empty_platform_keys(self):
        self.adapter.save(WORKFLOW)
        self.client.workflow['agent_config'] = {
            'llmConfig': {**self.client.AGENT_LLM, 'apiKey': None},
            'sttConfig': {'apiKey': '', 'provider': 'synthetic'},
            'ttsConfig': {'apiKey': None, 'voice': 'synthetic'}}
        original = deepcopy(self.client.workflow)
        expected = deepcopy(original)
        for name in ('llmConfig', 'sttConfig', 'ttsConfig'):
            del expected['agent_config'][name]['apiKey']
        with patch('builder.freya.integration.private_write') as writer:
            self.adapter.export('workflow-1', 'outputs/synthetic.json')
        self.assertEqual(writer.call_args.args[1]['workflow'], expected)
        self.assertEqual(self.client.workflow, original)

    def test_export_blocks_nonempty_and_unexpected_credentials(self):
        self.adapter.save(WORKFLOW)
        for config in (
                {'llmConfig': {'apiKey': 'private-marker'}},
                {'sttConfig': {'apiKey': ' '}},
                {'ttsConfig': {'apiKey': False}},
                {'llmConfig': {'nested': {'apiKey': ''}}},
                {'otherConfig': {'apiKey': None}},
                {'llmConfig': {'apiKey': None, 'token': ''}}):
            with self.subTest(config=config):
                self.client.workflow['agent_config'] = config
                with patch('builder.freya.integration.private_write') as writer:
                    with self.assertRaises(IntegrationError) as caught:
                        self.adapter.export('workflow-1', 'outputs/synthetic.json')
                    writer.assert_not_called()
                self.assertEqual(caught.exception.diagnostic['category'], 'artifact_credentials')

    def test_empty_platform_keys_remain_blocked_for_save_and_publish(self):
        workflow = {**deepcopy(WORKFLOW), 'agent_config': {'llmConfig': {**self.client.AGENT_LLM, 'apiKey': None}}}
        with self.assertRaises(IntegrationError):
            self.adapter.save(workflow)
        self.assertEqual(self.client.calls, [])
        saved = self.adapter.save(WORKFLOW)
        self.client.workflow['agent_config']['llmConfig']['apiKey'] = ''
        with self.assertRaises(IntegrationError):
            self.adapter.publish(saved, approved=True)
        self.assertFalse(any(call[1].endswith('/publish') for call in self.client.calls))

    def test_read_only_verify_platform_placeholders_and_unknown_state(self):
        saved = self.adapter.save(WORKFLOW)
        self.client.workflow['agent_config']['llmConfig']['apiKey'] = None
        original = deepcopy(self.client.workflow)
        verifier = FreyaAdapter(self.client, allow_external=True, dry_run_only=False, read_only=True)
        self.client.versions[0].pop('isPublished')
        start = len(self.client.calls)
        result = verifier.verify(saved, WORKFLOW)
        self.assertTrue(result['verified'])
        self.assertEqual(result['publication_state'], 'unknown')
        self.assertTrue(all(c[0] == 'GET' for c in self.client.calls[start:]))
        self.assertEqual(self.client.workflow, original)
        with self.assertRaises(IntegrationError):
            verifier.dry_run(WORKFLOW)

    def test_verify_reports_individual_mismatches(self):
        saved = self.adapter.save(WORKFLOW)
        self.client.workflow['agent_config']['llmConfig']['model'] = 'other'
        self.client.workflow['system_prompt'] = 'synthetic-private'
        wrong = SavedWorkflow(saved.workflow_id, 'wrong-agent', saved.version_id, 2)
        result = self.adapter.verify(wrong, WORKFLOW)
        self.assertEqual(result['checks']['agent_identity'], 'FAIL')
        self.assertEqual(result['checks']['version_number'], 'FAIL')
        self.assertEqual(result['checks']['mandated_model'], 'FAIL')
        self.assertEqual(result['checks']['workflow_consistency'], 'FAIL')
        self.assertNotIn('synthetic-private', json.dumps(result))

    def test_verify_retrieval_error_sanitized_and_publication_states(self):
        saved = self.adapter.save(WORKFLOW)
        for flag, state in ((True, 'published'), (False, 'unpublished'), ('false', 'unknown')):
            self.client.versions[0]['isPublished'] = flag
            self.assertEqual(self.adapter.verify(saved, WORKFLOW)['publication_state'], state)
        self.client.api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('GET /api/v2/workflow/workflow-1 -> 401: private'))
        result = self.adapter.verify(saved, WORKFLOW)
        self.assertFalse(result['verified'])
        self.assertEqual(result['publication_state'], 'unknown')
        self.assertEqual(result['diagnostics'][0]['category'], 'authentication')
        self.assertNotIn('private', json.dumps(result))

    def test_unconfirmed_server_transition_defaults_remain_inconsistent(self):
        saved = self.adapter.save(WORKFLOW)
        for field in ('max_turn_transition', 'passthrough_default_transition'):
            for value in (None, '', 'start'):
                with self.subTest(field=field, value=value):
                    self.client.workflow['nodes']['start'] = {**WORKFLOW['nodes']['start'], field: value}
                    result = self.adapter.verify(saved, WORKFLOW)
                    self.assertEqual(result['checks']['workflow_consistency'], 'FAIL')
                    changes = [d for d in result['workflow_diff']['changes'] if d['path'][-1] == field]
                    self.assertEqual(len(changes), 1)
                    self.assertEqual(changes[0]['classification'], 'material_or_unconfirmed')
                    self.assertFalse(result['verified'])

    def test_explicit_transition_targets_must_match(self):
        local = {**deepcopy(WORKFLOW)}
        local['nodes']['start']['max_turn_transition'] = 'start'
        saved = self.adapter.save(local)
        self.assertEqual(self.adapter.verify(saved, local)['checks']['workflow_consistency'], 'PASS')
        self.client.workflow['nodes']['start']['max_turn_transition'] = 'different'
        result = self.adapter.verify(saved, local)
        self.assertEqual(result['checks']['workflow_consistency'], 'FAIL')
        self.assertTrue(any(d['category'] == 'value_changed' for d in result['workflow_diff']['changes']))
