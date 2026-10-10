"""Synthetic briefs and OpenAPI contracts only."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from builder.brief.openapi import load_document
from builder.brief.sources import load_briefs
from builder.policy.models import PolicyIR
from builder.policy.executable import (VERSION, Action, ActionRole, Argument, ExecutablePolicy,
    Predicate, ResponseEffect, StateVariable, ValueType, dumps, loads, validate)


class ExecutableTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'synthetic.txt'; path.write_text('Check identity before lookup and close.\n')
        self.sources = load_briefs([path]); refs = (self.sources.sections[0].reference,)
        op = lambda: {'requestBody': {'content': {'application/json': {'schema': {'type': 'object', 'properties': {'subject': {'type': 'string'}}, 'required': ['subject']}}}}, 'responses': {'200': {'description': 'synthetic', 'content': {'application/json': {'schema': {'type': 'object', 'properties': {'verified': {'type': 'boolean'}}}}}}}}
        self.contract = load_document({'openapi': '3.0.3', 'info': {'title': 'synthetic', 'version': '1'}, 'paths': {p: {'post': op()} for p in ('/check', '/read', '/close')}}).contract
        args = (Argument('subject', 'subject'),)
        actions = (
            Action('check', ActionRole.VERIFY, 'POST /check', args, (), (), (ResponseEffect('200', '/verified', 'verified'),), 'read', 'close', True, refs),
            Action('read', ActionRole.READ, 'POST /read', args, (Predicate('verified', True),), (), (), 'close', 'close', True, refs),
            Action('close', ActionRole.CLOSE, 'POST /close', args, (), (), (), None, None, True, refs))
        self.policy = ExecutablePolicy(VERSION, self.contract.source_sha256, (StateVariable('subject', ValueType.STRING), StateVariable('verified', ValueType.BOOLEAN)), actions, 'check', 'verified', PolicyIR('policy-ir/1', self.sources.documents, ()))

    def result(self, policy): return validate(policy, self.contract, self.sources.source_bytes())

    def test_valid_finite_profile_and_roundtrip(self):
        self.assertTrue(self.result(self.policy).valid, self.result(self.policy).findings)
        self.assertFalse(self.result(self.policy).compilable)
        self.assertEqual(loads(dumps(self.policy)), self.policy)
        self.assertEqual(dumps(loads(dumps(self.policy))), dumps(self.policy))

    def test_strict_fields_versions_and_predicates(self):
        for change in ('extra', 'version', 'operator'):
            data = json.loads(dumps(self.policy))
            if change == 'extra': data['extra'] = True
            elif change == 'version': data['schema_version'] = 'policy-ir/1'
            else: data['actions'][1]['preconditions'][0]['eval'] = 'code'
            with self.assertRaises(ValueError): loads(json.dumps(data))

    def test_identity_missing_and_unauthorized_writes(self):
        actions = list(self.policy.actions)
        actions[1] = replace(actions[1], preconditions=(), effects=(ResponseEffect('200', '/verified', 'verified'),))
        findings = self.result(replace(self.policy, actions=tuple(actions))).findings
        self.assertIn('identity_gate_missing', findings)
        self.assertIn('identity_write_forbidden', findings)

    def test_binding_and_response_types(self):
        a = replace(self.policy.actions[0], operation='missing')
        self.assertIn('binding_missing', self.result(replace(self.policy, actions=(a, *self.policy.actions[1:]))).findings)
        a = replace(self.policy.actions[0], effects=(ResponseEffect('200', '/unknown', 'verified'),))
        self.assertIn('response_binding_invalid', self.result(replace(self.policy, actions=(a, *self.policy.actions[1:]))).findings)

    def test_paths_contradictions_and_required_args(self):
        a = replace(self.policy.actions[1], arguments=(), failure='missing', preconditions=(Predicate('verified', True), Predicate('verified', False)))
        findings = self.result(replace(self.policy, actions=(self.policy.actions[0], a, self.policy.actions[2]))).findings
        self.assertTrue({'required_argument_missing', 'contradictory_predicates', 'failure_or_success_path_missing'} <= set(findings))

    def test_unreachable_cycles_and_changed_contract(self):
        extra = replace(self.policy.actions[2], id='unreachable')
        self.assertIn('mandatory_action_unreachable', self.result(replace(self.policy, actions=(*self.policy.actions, extra))).findings)
        a = replace(self.policy.actions[0], success='check')
        self.assertIn('cycle_unsupported', self.result(replace(self.policy, actions=(a, *self.policy.actions[1:]))).findings)
        self.assertIn('contract_changed', self.result(replace(self.policy, contract_sha256='changed')).findings)

    def test_provenance_and_product_blockers(self):
        a = replace(self.policy.actions[0], source_refs=())
        self.assertIn('provenance_invalid', self.result(replace(self.policy, actions=(a, *self.policy.actions[1:]))).findings)
        variables = (replace(self.policy.variables[0], product='synthetic-product'), self.policy.variables[1])
        self.assertIn('product_iteration_unsupported', self.result(replace(self.policy, variables=variables)).findings)
