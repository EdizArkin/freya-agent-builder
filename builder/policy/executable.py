"""Typed finite control-flow profile. Validation does not grant Freya readiness."""
from dataclasses import asdict, dataclass
from enum import StrEnum
import json

from builder.brief.openapi import Contract
from builder.policy.models import PolicyIR, Rule, RuleCategory, RuleScope, RuleStatus, SourceRef
from builder.policy.validation import validate_policy
from builder.policy.codec import loads as load_foundation
from builder.compiler.validate import parse_workflow

VERSION = 'policy-executable/1'


class ValueType(StrEnum):
    STRING = 'string'
    NUMBER = 'number'
    INTEGER = 'integer'
    BOOLEAN = 'boolean'


class ActionRole(StrEnum):
    VERIFY = 'identity_check'
    READ = 'protected_read'
    PROMISE = 'promise'
    NOTE = 'note'
    CALLBACK = 'callback'
    TRANSFER = 'transfer'
    CLOSE = 'close'


@dataclass(frozen=True)
class StateVariable:
    id: str
    type: ValueType
    product: str | None = None


@dataclass(frozen=True)
class Predicate:
    variable: str
    equals: str | int | float | bool


@dataclass(frozen=True)
class Argument:
    field: str
    variable: str


@dataclass(frozen=True)
class ResponseEffect:
    status: str
    pointer: str
    variable: str


@dataclass(frozen=True)
class Action:
    id: str
    role: ActionRole
    operation: str
    arguments: tuple[Argument, ...]
    preconditions: tuple[Predicate, ...]
    postconditions: tuple[Predicate, ...]
    effects: tuple[ResponseEffect, ...]
    success: str | None
    failure: str | None
    mandatory: bool
    source_refs: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ExecutablePolicy:
    schema_version: str
    contract_sha256: str
    variables: tuple[StateVariable, ...]
    actions: tuple[Action, ...]
    initial_action: str
    identity_variable: str
    foundation: PolicyIR
    # Candidate composition is reviewed before graph elaboration; no implicit precedence.
    lineage: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExecutableReport:
    findings: tuple[str, ...]

    @property
    def valid(self):
        return not self.findings

    @property
    def compilable(self):
        # Runtime lowering and semantic approval remain separate work.
        return False


def dumps(policy):
    return json.dumps(asdict(policy), sort_keys=True, separators=(',', ':'), allow_nan=False)


def loads(text):
    """Strict typed decoding; provenance/capability validation is a separate step."""
    def shape(value, names):
        if not isinstance(value, dict) or set(value) != set(names):
            raise ValueError('Executable IR fields invalid.')
    def string(value):
        if not isinstance(value, str) or not value: raise ValueError('Executable IR string invalid.')
        return value
    def rows(value):
        if not isinstance(value, list): raise ValueError('Executable IR array invalid.')
        return value
    data = parse_workflow(text)
    shape(data, ('schema_version', 'contract_sha256', 'variables', 'actions', 'initial_action', 'identity_variable', 'foundation', 'lineage'))
    if data['schema_version'] != VERSION: raise ValueError('Executable IR version invalid.')
    foundation = load_foundation(json.dumps(data['foundation'], allow_nan=False))
    variables, actions = [], []
    for v in rows(data['variables']):
        shape(v, ('id', 'type', 'product'))
        if v['product'] is not None: string(v['product'])
        variables.append(StateVariable(string(v['id']), ValueType(v['type']), v['product']))
    for a in rows(data['actions']):
        shape(a, ('id', 'role', 'operation', 'arguments', 'preconditions', 'postconditions', 'effects', 'success', 'failure', 'mandatory', 'source_refs'))
        def predicates(items):
            out = []
            for p in rows(items):
                shape(p, ('variable', 'equals'))
                if type(p['equals']) not in (str, int, float, bool): raise ValueError('Predicate value invalid.')
                out.append(Predicate(string(p['variable']), p['equals']))
            return tuple(out)
        args, effects, refs = [], [], []
        for arg in rows(a['arguments']):
            shape(arg, ('field', 'variable')); args.append(Argument(string(arg['field']), string(arg['variable'])))
        for e in rows(a['effects']):
            shape(e, ('status', 'pointer', 'variable')); effects.append(ResponseEffect(*(string(e[k]) for k in ('status', 'pointer', 'variable'))))
        # Reuse v1 codec for strict source-reference decoding.
        raw = asdict(foundation)
        raw['rules'] = [{'id': string(a['id']), 'category': 'obligation', 'scope': 'action', 'status': 'candidate', 'source_refs': a['source_refs']}]
        refs = load_foundation(json.dumps(raw)).rules[0].source_refs
        for key in ('success', 'failure'):
            if a[key] is not None: string(a[key])
        if type(a['mandatory']) is not bool: raise ValueError('Mandatory flag invalid.')
        actions.append(Action(string(a['id']), ActionRole(a['role']), string(a['operation']), tuple(args), predicates(a['preconditions']), predicates(a['postconditions']), tuple(effects), a['success'], a['failure'], a['mandatory'], refs))
    lineage = []
    for pair in rows(data['lineage']):
        if not isinstance(pair, list) or len(pair) != 2: raise ValueError('Lineage invalid.')
        lineage.append(tuple(string(v) for v in pair))
    return ExecutablePolicy(VERSION, string(data['contract_sha256']), tuple(variables), tuple(actions), string(data['initial_action']), string(data['identity_variable']), foundation, tuple(lineage))


def validate(policy: ExecutablePolicy, contract: Contract, sources) -> ExecutableReport:
    findings = set()
    if policy.schema_version != VERSION: findings.add('unsupported_version')
    if policy.contract_sha256 != contract.source_sha256: findings.add('contract_changed')
    variables = {v.id: v for v in policy.variables}
    actions = {a.id: a for a in policy.actions}
    if len(variables) != len(policy.variables) or len(actions) != len(policy.actions): findings.add('duplicate_id')
    if policy.initial_action not in actions: findings.add('initial_action_missing')
    if any(r.id not in actions for r in policy.foundation.rules): findings.add('mandatory_rule_unmapped')
    if not variables.get(policy.identity_variable) or variables[policy.identity_variable].type != ValueType.BOOLEAN:
        findings.add('identity_state_missing')
    if any(type(v.type) is not ValueType or not v.id or (v.product is not None and not isinstance(v.product, str)) for v in policy.variables):
        findings.add('state_type')
    evidence = PolicyIR('policy-ir/1', policy.foundation.documents, tuple(
        Rule(a.id, RuleCategory.OBLIGATION, RuleScope.ACTION, RuleStatus.CANDIDATE, a.source_refs) for a in policy.actions))
    if not validate_policy(evidence, sources).valid or not validate_policy(policy.foundation, sources).valid:
        findings.add('provenance_invalid')
    ops = {op.identity: op for op in contract.operations}
    protected = {ActionRole.READ, ActionRole.PROMISE, ActionRole.NOTE}
    def value_type(value):
        return {str: 'string', int: 'integer', float: 'number', bool: 'boolean'}.get(type(value))
    for a in policy.actions:
        if type(a.role) is not ActionRole or type(a.mandatory) is not bool: findings.add('action_type')
        for predicates in (a.preconditions, a.postconditions):
            seen = {}
            for p in predicates:
                v = variables.get(p.variable)
                if not v or (value_type(p.equals) != v.type.value and not (v.type == ValueType.NUMBER and type(p.equals) is int)):
                    findings.add('predicate_type')
                if p.variable in seen and (type(seen[p.variable]), seen[p.variable]) != (type(p.equals), p.equals):
                    findings.add('contradictory_predicates')
                seen[p.variable] = p.equals
        if a.role in protected and Predicate(policy.identity_variable, True) not in a.preconditions:
            findings.add('identity_gate_missing')
        if a.role == ActionRole.CLOSE and (a.success is not None or a.failure is not None): findings.add('close_not_terminal')
        if a.role != ActionRole.CLOSE and (a.success not in actions or a.failure not in actions): findings.add('failure_or_success_path_missing')
        op = ops.get(a.operation)
        if not op:
            findings.add('binding_missing'); continue
        if op.parameters: findings.add('parameter_location_unsupported')
        schemas = [m.schema for m in op.request_content if m.media_type == 'application/json']
        if len(schemas) != 1 or not isinstance(schemas[0], dict) or schemas[0].get('type') != 'object':
            findings.add('request_schema_unsupported'); continue
        schema = schemas[0]
        args = {arg.field: arg.variable for arg in a.arguments}
        if len(args) != len(a.arguments): findings.add('duplicate_argument')
        if set(schema.get('required', [])) - args.keys(): findings.add('required_argument_missing')
        for field, var in args.items():
            prop = schema.get('properties', {}).get(field)
            if not isinstance(prop, dict) or var not in variables: findings.add('argument_binding_invalid'); continue
            if prop.get('type') != variables[var].type.value: findings.add('argument_type')
            if prop.keys() - {'type', 'description', 'title'}:
                findings.add('argument_constraints_unproven')
        for effect in a.effects:
            responses = [r for r in op.responses if r.status == effect.status]
            response_schema = [m.schema for r in responses for m in r.content if m.media_type == 'application/json']
            current = response_schema[0] if len(response_schema) == 1 else None
            if not effect.pointer.startswith('/') or '~' in effect.pointer: current = None
            else:
                for field in effect.pointer[1:].split('/'):
                    current = current.get('properties', {}).get(field) if isinstance(current, dict) else None
            if not isinstance(current, dict) or effect.variable not in variables or current.get('type') != variables[effect.variable].type.value:
                findings.add('response_binding_invalid')
            if effect.variable == policy.identity_variable and a.role != ActionRole.VERIFY:
                findings.add('identity_write_forbidden')
    # Conservative finite graph: loops, product iteration and arbitrary retry cycles are unsupported.
    reached, active = set(), set()
    def visit(ident):
        if ident in active: findings.add('cycle_unsupported'); return
        if ident in reached or ident not in actions: return
        reached.add(ident); active.add(ident)
        a = actions[ident]
        for target in (a.success, a.failure):
            if target is not None: visit(target)
        active.remove(ident)
    visit(policy.initial_action)
    if any(a.mandatory and a.id not in reached for a in policy.actions): findings.add('mandatory_action_unreachable')
    if not any(a.role == ActionRole.CLOSE and a.id in reached for a in policy.actions): findings.add('close_missing')
    if not any(a.role == ActionRole.VERIFY and any(e.variable == policy.identity_variable for e in a.effects) for a in policy.actions):
        findings.add('identity_response_missing')
    if policy.lineage: findings.add('executable_amendment_semantics_unsupported')
    if any(v.product is not None for v in policy.variables): findings.add('product_iteration_unsupported')
    if any(a.role in (ActionRole.PROMISE, ActionRole.NOTE, ActionRole.CALLBACK, ActionRole.TRANSFER) for a in policy.actions):
        findings.add('stateful_bank_semantics_unapproved')
    return ExecutableReport(tuple(sorted(findings)))
