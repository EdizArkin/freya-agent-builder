"""Deterministic lowering of an explicitly configured three-action identity slice."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from builder.brief.openapi import load_contract
from builder.brief.sources import load_briefs
from builder.compiler.validate import validate_workflow, parse_workflow
from builder.policy.executable import loads, validate, ActionRole, Predicate
from builder.freya.integration import FreyaAdapter, private_write


class SliceError(ValueError):
    pass


def compile_slice(policy, contract, sources, configuration):
    """Configuration is data: endpoints, input names, constant argument domains.

    Postconditions are checked after successful response mapping; only exact
    topology/semantics below is admitted. This is not full brief compliance.
    """
    if set(configuration) != {'base_url', 'inputs', 'constants', 'identity_condition'}:
        raise SliceError('configuration_fields')
    if configuration['identity_condition'] != 'all_verified':
        raise SliceError('identity_condition_unsupported')
    url = urlparse(configuration['base_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise SliceError('endpoint_invalid')
    if not isinstance(configuration['constants'], dict) or not isinstance(configuration['inputs'], list):
        raise SliceError('configuration_types')
    report = validate(policy, contract, sources)
    # Domain constraints are discharged below against literal values only.
    blockers = set(report.findings) - {'argument_constraints_unproven'}
    if blockers: raise SliceError('policy_invalid')
    if len(policy.actions) != 3 or {a.role for a in policy.actions} != {ActionRole.VERIFY, ActionRole.READ, ActionRole.CLOSE}:
        raise SliceError('unsupported_actions')
    verify = next(a for a in policy.actions if a.role == ActionRole.VERIFY)
    read = next(a for a in policy.actions if a.role == ActionRole.READ)
    close = next(a for a in policy.actions if a.role == ActionRole.CLOSE)
    if (policy.initial_action != verify.id or verify.success != read.id or verify.failure != close.id
            or read.success != close.id or read.failure != close.id):
        raise SliceError('unsafe_topology')
    if verify.preconditions or verify.postconditions or read.postconditions or close.preconditions or close.postconditions:
        raise SliceError('unsupported_predicates')
    if read.preconditions != (Predicate(policy.identity_variable, True),):
        raise SliceError('identity_gate_invalid')
    if len(verify.effects) != 1 or verify.effects[0].pointer != '/all_verified' or verify.effects[0].status != '200' or verify.effects[0].variable != policy.identity_variable or read.effects or close.effects:
        raise SliceError('unsupported_response_effects')
    if policy.identity_variable != 'identity_verified':
        raise SliceError('identity_state_name')
    variables = {v.id: v for v in policy.variables}
    if any(not a.mandatory for a in policy.actions): raise SliceError('slice_action_must_be_mandatory')
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v) or v.startswith('slice_') for v in variables):
        raise SliceError('unsafe_variable_name')
    inputs, constants = set(configuration['inputs']), configuration['constants']
    if any(not isinstance(v, str) for v in configuration['inputs']) or len(inputs) != len(configuration['inputs']):
        raise SliceError('input_invalid')
    if inputs & constants.keys() or (inputs | constants.keys()) != (variables.keys() - {'identity_verified'}):
        raise SliceError('input_domains_missing')
    if 'identity_verified' in inputs or 'identity_verified' in constants:
        raise SliceError('untrusted_identity')
    if any(isinstance(v, str) and ('{{' in v or '}}' in v) for v in constants.values()):
        raise SliceError('constant_template_unsupported')
    ops = {o.identity: o for o in contract.operations}
    for action in policy.actions:
        op = ops[action.operation]
        if op.method != 'POST' or '{' in op.path:
            raise SliceError('operation_unsupported')
        schema = next(m.schema for m in op.request_content if m.media_type == 'application/json')
        if schema.keys() - {'type', 'properties', 'required', 'description', 'additionalProperties'}:
            raise SliceError('request_schema_unsupported')
        for arg in action.arguments:
            prop = schema['properties'][arg.field]
            if prop.keys() - {'type', 'enum', 'description', 'title'}:
                raise SliceError('argument_constraint_unsupported')
            if arg.variable in constants:
                value = constants[arg.variable]
                kind = {str: 'string', bool: 'boolean', int: 'integer', float: 'number'}.get(type(value))
                if kind != prop['type'] and not (kind == 'integer' and prop['type'] == 'number'):
                    raise SliceError('constant_type_invalid')
                if 'enum' in prop and value not in prop['enum']: raise SliceError('constant_domain_invalid')
            elif prop.get('enum'):
                raise SliceError('input_enum_unproven')
    # The IR role alone cannot grant verification authority. The explicit profile
    # fixes operation semantics; arguments still derive from the supplied schema.
    if ops[verify.operation].path != '/verify_identity' or ops[read.operation].path != '/get_account' or ops[close.operation].path != '/close_call':
        raise SliceError('operation_role_mismatch')
    response = next(r for r in ops[verify.operation].responses if r.status == '200')
    properties = next(m.schema for m in response.content if m.media_type == 'application/json')['properties']
    if any(properties.get(k, {}).get('type') != 'boolean' for k in ('ok', 'all_verified')):
        raise SliceError('identity_response_schema')
    def transition(target, condition=None):
        return {'target_node': target, 'description': 'Deterministic slice routing', 'condition': condition or {'operator': 'always'}}
    def flag(target, variable, operand):
        return transition(target, {'logic': 'or', 'conditions': [{'variable': variable, 'operator': 'equals', 'operand': operand}]})
    def node(name, kind, tool, transitions):
        return {'name': name, 'node_type': kind, 'passthrough': True,
                'on_enter': {'type': 'silent'}, 'pre_actions': [tool],
                'passthrough_default_transition': transitions[-1]['target_node'], 'transitions': transitions}
    def code(name, source, transitions):
        return node(name, 'code', {'type': 'function', 'function': {'name': 'code_exec', 'handler': '__function__:code_exec', 'parameters': {'type': 'object', 'properties': {'code': {'type': 'string', 'default': source}, 'timeout_ms': {'type': 'number', 'default': 10000}}}}}, transitions)
    def api(action, name, target):
        op = ops[action.operation]
        schema = next(m.schema for m in op.request_content if m.media_type == 'application/json')
        body = deepcopy(schema)
        body['properties'] = {arg.field: {**schema['properties'][arg.field], 'default': '{{' + arg.variable + '}}'} for arg in action.arguments}
        tool = {'type': 'function', 'config': {'maxRetries': 0, 'timeoutMs': 10000}, 'function': {'name': name, 'handler': '__function__:api_call', 'parameters': {'type': 'object', 'properties': {'url': {'type': 'string', 'default': configuration['base_url'].rstrip('/') + op.path}, 'method': {'type': 'string', 'default': 'POST'}, 'headers': {'type': 'object', 'properties': {'Content-Type': {'type': 'string', 'default': 'application/json'}}}, 'body': body}}}}
        n = node(name, 'api_request', tool, [transition(target)])
        if action.role == ActionRole.VERIFY:
            tool['pre_process_code'] = "vars['identity_verified'] = False\nvars['slice_verified_result'] = 'rejected'\nvars['slice_gate'] = 'rejected'\n"
            tool['post_process_code'] = "try:\n    d = result.get('data') if isinstance(result, dict) else None\n    passed = isinstance(d, dict) and result.get('status') == 'success' and result.get('status_code') == 200 and d.get('ok') is True and d.get('all_verified') is True\n    if isinstance(d, dict):\n        d['slice_verified_result'] = 'verified' if passed else 'rejected'\nexcept Exception:\n    pass\n"
            n['response_mapping'] = [{'path': 'data.slice_verified_result', 'variable': 'slice_verified_result'}]
        return n
    initialization = "vars['identity_verified'] = False\nvars['slice_verified_result'] = 'rejected'\nvars['slice_gate'] = 'rejected'\nvars['slice_inputs'] = 'invalid'\ntry:\n"
    for key, value in sorted(constants.items()): initialization += '    vars[' + repr(key) + '] = ' + repr(value) + '\n'
    checks = []
    for key in sorted(inputs):
        typ = variables[key].type.value
        if typ != 'string': raise SliceError('input_type_unsupported')
        checks.append('isinstance(vars.get(' + repr(key) + '), str) and bool(vars.get(' + repr(key) + '))')
    initialization += "    vars['slice_inputs'] = 'valid' if " + (' and '.join(checks) or 'True') + " else 'invalid'\nexcept Exception:\n    pass\n"
    gate = "vars['identity_verified'] = False\nvars['slice_gate'] = 'rejected'\ntry:\n    if vars.get('slice_verified_result') == 'verified':\n        vars['identity_verified'] = True\n        vars['slice_gate'] = 'verified'\nexcept Exception:\n    pass\n"
    nodes = {
        'slice_init': code('slice_init', initialization, [flag('slice_verify', 'slice_inputs', 'valid'), transition('slice_end')]),
        'slice_verify': api(verify, 'slice_verify', 'slice_gate'),
        'slice_gate': code('slice_gate', gate, [flag('slice_account', 'slice_gate', 'verified'), transition('slice_close')]),
        'slice_account': api(read, 'slice_account', 'slice_close'),
        'slice_close': api(close, 'slice_close', 'slice_end'),
        'slice_end': {'name': 'slice_end', 'node_type': 'end_call', 'on_enter': {'type': 'silent'}, 'disable_inactivity_monitor': True, 'transitions': [], 'terminal': {'type': 'function', 'function': {'name': 'slice_end', 'handler': '__function__:end_call', 'parameters': {'type': 'object', 'properties': {'message_mode': {'type': 'string', 'default': 'none'}}}}}},
    }
    workflow = {'initial_node': 'slice_init', 'nodes': nodes, 'global_nodes': {}}
    FreyaAdapter._no_credentials(workflow)
    if not validate_workflow(workflow).valid: raise SliceError('generated_workflow_invalid')
    return workflow


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('policy', 'tool-spec', 'config', 'output'): p.add_argument('--' + key, required=True)
    p.add_argument('--brief', action='append', required=True)
    args = p.parse_args(argv)
    try:
        policy = loads(Path(args.policy).read_text(encoding='utf-8'))
        source = load_briefs(args.brief)
        contract = load_contract(args.tool_spec)
        if not contract.valid or contract.diagnostics: raise SliceError('contract_invalid')
        config = parse_workflow(Path(args.config).read_text(encoding='utf-8'))
        workflow = compile_slice(policy, contract.contract, source.source_bytes(), config)
        private_write(args.output, workflow)
        print('PASS: identity slice compiled and locally validated; server/runtime acceptance untested.')
        return 0
    except SliceError as exc:
        print('FAIL: ' + str(exc))  # Compiler-owned fixed codes only.
        return 1
    except Exception:
        print('FAIL: check typed policy, explicit configuration, source provenance and supported slice contract privately.')
        return 1


if __name__ == '__main__': raise SystemExit(main())
