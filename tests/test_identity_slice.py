"""Synthetic contract plus execution of compiler-owned hooks; no HTTP calls."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from builder.brief.openapi import load_document
from builder.brief.sources import load_briefs
from builder.policy.executable import (Action, ActionRole, Argument, ExecutablePolicy, Predicate,
    ResponseEffect, StateVariable, ValueType, VERSION, dumps)
from builder.policy.models import PolicyIR
from builder.compiler.identity_slice import compile_slice, SliceError
from builder.compiler.validate import validate_workflow


def fixture(directory):
    path = Path(directory) / 'brief.txt'
    path.write_text('Synthetic slice: check identity; read only on full verification; record completion and hang up.\n', encoding='utf-8')
    bundle = load_briefs([path])
    def operation(properties, required, response):
        return {'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'properties': properties, 'required': required}}}}, 'responses': {'200': {'description': 'Synthetic response', 'content': {'application/json': {'schema': {'type': 'object', 'properties': response}}}}}}
    s = {'type': 'string'}
    document = {'openapi': '3.0.3', 'info': {'title': 'Independently authored synthetic slice', 'version': '1'}, 'paths': {
        '/verify_identity': {'post': operation({'customer_id': s, 'field': {'type': 'string', 'enum': ['synthetic_challenge']}, 'answer': s}, ['customer_id', 'field', 'answer'], {'ok': {'type': 'boolean'}, 'all_verified': {'type': 'boolean'}})},
        '/get_account': {'post': operation({'customer_id': s}, ['customer_id'], {'ok': {'type': 'boolean'}})},
        '/close_call': {'post': operation({'customer_id': s, 'outcome': {'type': 'string', 'enum': ['synthetic_complete']}}, ['customer_id', 'outcome'], {'ok': {'type': 'boolean'}})}}}
    contract = load_document(document).contract
    refs = (bundle.sections[0].reference,)
    actions = (
        Action('verify', ActionRole.VERIFY, 'POST /verify_identity', (Argument('customer_id', 'customer_id'), Argument('field', 'field'), Argument('answer', 'answer')), (), (), (ResponseEffect('200', '/all_verified', 'identity_verified'),), 'read', 'close', True, refs),
        Action('read', ActionRole.READ, 'POST /get_account', (Argument('customer_id', 'customer_id'),), (Predicate('identity_verified', True),), (), (), 'close', 'close', True, refs),
        Action('close', ActionRole.CLOSE, 'POST /close_call', (Argument('customer_id', 'customer_id'), Argument('outcome', 'outcome')), (), (), (), None, None, True, refs))
    policy = ExecutablePolicy(VERSION, contract.source_sha256, tuple(StateVariable(v, ValueType.STRING) for v in ('customer_id', 'field', 'answer', 'outcome')) + (StateVariable('identity_verified', ValueType.BOOLEAN),), actions, 'verify', 'identity_verified', PolicyIR('policy-ir/1', bundle.documents, ()))
    config = {'base_url': 'https://synthetic.invalid/v1', 'identity_condition': 'all_verified', 'inputs': ['customer_id', 'answer'], 'constants': {'field': 'synthetic_challenge', 'outcome': 'synthetic_complete'}}
    return bundle, document, contract, policy, config


class IdentitySliceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.bundle, self.document, self.contract, self.policy, self.config = fixture(temp.name)
        self.workflow = self.compile()

    def compile(self, policy=None, config=None):
        return compile_slice(policy or self.policy, self.contract, self.bundle.source_bytes(), config or self.config)

    def code(self, node):
        return self.workflow['nodes'][node]['pre_actions'][0]['function']['parameters']['properties']['code']['default']

    def test_real_structure_and_terminal(self):
        self.assertTrue(validate_workflow(self.workflow).valid)
        self.assertNotIn('agent_config', self.workflow)
        self.assertEqual(self.workflow['nodes']['slice_close']['pre_actions'][0]['function']['handler'], '__function__:api_call')
        self.assertEqual(self.workflow['nodes']['slice_end']['terminal']['function']['handler'], '__function__:end_call')
        self.assertEqual(json.loads(dumps(self.policy))['schema_version'], VERSION)

    def test_response_gates_success_failure_and_tool_errors(self):
        tool = self.workflow['nodes']['slice_verify']['pre_actions'][0]
        for result, expected in (({'status': 'success', 'status_code': 200, 'data': {'ok': True, 'all_verified': True}}, True),
                                 ({'status': 'success', 'status_code': 200, 'data': {'ok': True, 'all_verified': False}}, False),
                                 ({'status': 'error', 'status_code': 500, 'data': {'ok': True, 'all_verified': True}}, False),
                                 ({'status': 'success', 'status_code': 200, 'data': {'ok': False, 'all_verified': True}}, False),
                                 ({'status': 'success', 'status_code': 200, 'data': {'ok': 'true', 'all_verified': 'true'}}, False),
                                 ({}, False)):
            state = {'identity_verified': True, 'slice_verified_result': 'verified', 'slice_gate': 'verified'}
            exec(tool['pre_process_code'], {'vars': state})
            exec(tool['post_process_code'], {'result': result})
            if isinstance(result.get('data'), dict) and 'slice_verified_result' in result['data']:
                state['slice_verified_result'] = result['data']['slice_verified_result']
            exec(self.code('slice_gate'), {'vars': state})
            self.assertIs(state['identity_verified'], expected)

    def test_initialization_discards_spoofed_state_and_missing_args(self):
        state = {'identity_verified': True, 'slice_gate': 'verified'}
        exec(self.code('slice_init'), {'vars': state})
        self.assertIs(state['identity_verified'], False)
        self.assertEqual(state['slice_inputs'], 'invalid')
        self.assertEqual(self.workflow['nodes']['slice_init']['transitions'][-1]['target_node'], 'slice_end')

    def test_unsafe_paths_and_bindings_are_rejected(self):
        bad = replace(self.policy, initial_action='read')
        with self.assertRaises(SliceError): self.compile(bad)
        a = replace(self.policy.actions[0], failure='read')
        with self.assertRaises(SliceError): self.compile(replace(self.policy, actions=(a, *self.policy.actions[1:])))
        a = replace(self.policy.actions[1], operation='POST /close_call')
        with self.assertRaises(SliceError): self.compile(replace(self.policy, actions=(self.policy.actions[0], a, self.policy.actions[2])))

    def test_missing_domains_and_unsupported_actions(self):
        bad = {**self.config, 'constants': {'field': 'not_in_schema', 'outcome': 'synthetic_complete'}}
        with self.assertRaises(SliceError): self.compile(config=bad)
        bad = {**self.config, 'inputs': ['answer']}
        with self.assertRaises(SliceError): self.compile(config=bad)
        a = replace(self.policy.actions[1], role=ActionRole.PROMISE)
        with self.assertRaises(SliceError): self.compile(replace(self.policy, actions=(self.policy.actions[0], a, self.policy.actions[2])))

    def test_documented_node_placement_mapping_and_no_null_fields(self):
        nodes = self.workflow['nodes']
        self.assertEqual({n['node_type'] for n in nodes.values()}, {'code', 'api_request', 'end_call'})
        for n in nodes.values():
            if n['node_type'] in ('api_request', 'code'):
                self.assertTrue(n['passthrough'])
                self.assertEqual(n['transitions'][-1]['condition'], {'operator': 'always'})
                self.assertNotIn('tools', n)
        self.assertEqual(nodes['slice_verify']['response_mapping'], [{'path': 'data.slice_verified_result', 'variable': 'slice_verified_result'}])
        self.assertNotIn(': null', json.dumps(self.workflow))
        # Only the guarded branch can enter the account action.
        incoming = [(key, t) for key, n in nodes.items() for t in n['transitions'] if t['target_node'] == 'slice_account']
        self.assertEqual(len(incoming), 1)
        self.assertEqual(incoming[0][0], 'slice_gate')
        self.assertEqual(incoming[0][1]['condition']['conditions'][0]['operand'], 'verified')

    def test_fixture_compiles_via_cli_and_original_is_unchanged(self):
        from builder.compiler.identity_slice import main
        from unittest.mock import patch
        root = Path('examples/identity_slice')
        original = (root/'policy.json').read_bytes()
        with patch('builder.compiler.identity_slice.private_write') as writer:
            result = main(['--brief', str(root/'brief.txt'), '--policy', str(root/'policy.json'),
                           '--tool-spec', str(root/'openapi.json'), '--config', str(root/'config.json'),
                           '--output', 'outputs/synthetic-cli.json'])
        self.assertEqual(result, 0)
        self.assertTrue(validate_workflow(writer.call_args.args[1]).valid)
        self.assertEqual((root/'policy.json').read_bytes(), original)
