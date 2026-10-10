"""Small candidate extension of the unchanged v1 provenance envelope."""
from dataclasses import asdict, dataclass
from enum import StrEnum
import json

from builder.compiler.validate import parse_workflow
from builder.policy.codec import IRValidationError, loads as load_v1
from builder.policy.models import PolicyIR, Rule, RuleCategory, SourceRef, ValidationFinding, ValidationReport
from builder.policy.validation import validate_policy

VERSION = 'policy-candidate/1'


class CoverageStatus(StrEnum):
    REPRESENTED = 'represented'
    UNRESOLVED = 'unresolved'
    UNSUPPORTED = 'unsupported'


@dataclass(frozen=True)
class Guard:
    field: str
    op: str
    value: str | int | float | bool | None


@dataclass(frozen=True)
class ParameterValue:
    name: str
    value: str | int | float | bool | None
    source_refs: tuple[SourceRef, ...]


@dataclass(frozen=True)
class Override:
    target: str
    selector: str | None = None


@dataclass(frozen=True)
class CandidateRule:
    envelope: Rule
    statement: str
    action: str
    selector: str
    guard: Guard | None
    parameters: tuple[ParameterValue, ...]
    overrides: tuple[Override, ...]


@dataclass(frozen=True)
class Coverage:
    section_id: str
    status: CoverageStatus
    rule_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CandidatePolicy:
    foundation: PolicyIR
    rules: tuple[CandidateRule, ...]
    coverage: tuple[Coverage, ...]
    schema_version: str = VERSION


def _fail(code, message):
    raise IRValidationError(ValidationReport((ValidationFinding(code, '$', message),), False))


def _shape(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        _fail('candidate_fields', 'Candidate object has missing or unknown fields; consult the profile.')


def _string(value):
    if not isinstance(value, str) or not value.strip():
        _fail('candidate_type', 'Expected a nonempty string in candidate data.')


def _array(value):
    if not isinstance(value, list):
        _fail('candidate_type', 'Expected an array in candidate data.')
    return value


def _scalar(value):
    if value is not None and type(value) not in (str, int, float, bool):
        _fail('unsupported_value', 'Only JSON scalar parameter and guard values are supported.')


def loads(text, bundle) -> CandidatePolicy:
    try:
        data = parse_workflow(text)
    except (ValueError, TypeError, RecursionError):
        _fail('candidate_json', 'Expected strict finite JSON without duplicate keys.')
    _shape(data, ('schema_version', 'rules', 'coverage'))
    if data['schema_version'] != VERSION:
        _fail('candidate_version', 'Unsupported candidate schema version.')
    rows = _array(data['rules'])
    for row in rows:
        _shape(row, ('envelope', 'statement', 'action', 'selector', 'guard', 'parameters', 'overrides'))
    foundation = load_v1(json.dumps({'schema_version': 'policy-ir/1',
        'documents': [asdict(d) for d in bundle.documents], 'rules': [r['envelope'] for r in rows]}, allow_nan=False))
    report = validate_policy(foundation, bundle.source_bytes())
    if not report.valid:
        raise IRValidationError(report)
    rules = []
    for row, envelope in zip(rows, foundation.rules):
        if envelope.condition is not None:
            _fail('candidate_condition', 'Use the candidate guard field; embedded v1 conditions are unsupported in composition.')
        for key in ('statement', 'action', 'selector'):
            _string(row[key])
        guard = row['guard']
        if guard is not None:
            _shape(guard, ('field', 'op', 'value'))
            _string(guard['field'])
            if guard['op'] != 'equals':
                _fail('unsupported_operator', 'Only equals guards are supported; classify other requirements as unsupported.')
            _scalar(guard['value'])
            guard = Guard(**guard)
        params = []
        names = set()
        for value in _array(row['parameters']):
            _shape(value, ('name', 'value', 'source_refs'))
            _string(value['name'])
            _scalar(value['value'])
            if value['name'] in names:
                _fail('duplicate_parameter', 'Parameter names must be unique within a rule.')
            names.add(value['name'])
            # Reuse v1 strict parsing and provenance validation for parameter citations.
            evidence = asdict(envelope)
            evidence['source_refs'] = value['source_refs']
            p = load_v1(json.dumps({'schema_version': 'policy-ir/1', 'documents': [asdict(d) for d in bundle.documents], 'rules': [evidence]}, allow_nan=False))
            pr = validate_policy(p, bundle.source_bytes())
            if not pr.valid:
                raise IRValidationError(pr)
            params.append(ParameterValue(value['name'], value['value'], p.rules[0].source_refs))
        overrides = []
        for override in _array(row['overrides']):
            _shape(override, ('target', 'selector'))
            _string(override['target'])
            if override['selector'] is not None:
                _string(override['selector'])
            overrides.append(Override(**override))
        rules.append(CandidateRule(envelope, row['statement'], row['action'], row['selector'], guard, tuple(params), tuple(overrides)))
    coverage = []
    for item in _array(data['coverage']):
        _shape(item, ('section_id', 'status', 'rule_ids', 'reason'))
        _string(item['section_id'])
        _string(item['reason'])
        for ident in _array(item['rule_ids']):
            _string(ident)
        try:
            status = CoverageStatus(item['status'])
        except (ValueError, TypeError):
            _fail('coverage_status', 'Coverage must be represented, unresolved, or unsupported.')
        coverage.append(Coverage(item['section_id'], status, tuple(item['rule_ids']), item['reason']))
    return CandidatePolicy(foundation, tuple(rules), tuple(coverage))


def dumps(policy: CandidatePolicy) -> str:
    # Documents remain bound to the bundle, not editable extractor output.
    return json.dumps({'schema_version': policy.schema_version,
        'rules': [asdict(r) for r in policy.rules], 'coverage': [asdict(c) for c in policy.coverage]},
        sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def check_coverage(policy, bundle):
    findings, records = [], {}
    sections = {s.id: s for s in bundle.sections}
    rules = {r.envelope.id: r for r in policy.rules}
    for index, record in enumerate(policy.coverage):
        loc = f'coverage[{index}]'
        if record.section_id not in sections or record.section_id in records:
            findings.append(ValidationFinding('coverage_reference', loc, 'Unknown or duplicate source-section classification.'))
            continue
        records[record.section_id] = record
        section = sections[record.section_id]
        if record.status != CoverageStatus.REPRESENTED:
            findings.append(ValidationFinding('coverage_' + record.status.value, loc, 'Source requirement is explicitly unresolved or unsupported.'))
        elif not record.rule_ids:
            findings.append(ValidationFinding('coverage_rules', loc, 'Represented sections must identify candidate rules.'))
        for ident in record.rule_ids:
            rule = rules.get(ident)
            if rule is None or not any(ref.document_id == section.reference.document_id and
                section.reference.start_offset <= ref.start_offset < ref.end_offset <= section.reference.end_offset
                for ref in rule.envelope.source_refs):
                findings.append(ValidationFinding('coverage_evidence', loc, 'Coverage rule must cite a span within the classified section.'))
    for index, section in enumerate(bundle.sections):
        if section.id not in records:
            findings.append(ValidationFinding('coverage_missing', f'sections[{index}]', 'Potentially normative source section has no classification.'))
    for index, rule in enumerate(policy.rules):
        if rule.envelope.status.value != 'candidate':
            findings.append(ValidationFinding('rule_unresolved', f'rules[{index}]', 'Non-candidate rules are preserved but not effective.'))
        if rule.envelope.category not in (RuleCategory.OBLIGATION, RuleCategory.PROHIBITION, RuleCategory.GUARD):
            findings.append(ValidationFinding('unsupported_category', f'rules[{index}]', 'This candidate profile does not interpret this rule category.'))
    return ValidationReport(tuple(findings), True)
