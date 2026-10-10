"""Synthetic source files and response doubles only."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from builder.brief.sources import SourceError, digest, load_briefs
from builder.llm.extraction import ModelConfig
from builder.policy.candidate import dumps, loads, check_coverage
from builder.policy.codec import IRValidationError
from builder.policy.composition import compose
from builder.policy.pipeline import extract_briefs


def response(bundle):
    rules, coverage = [], []
    for i, section in enumerate(bundle.sections):
        ident = f'rule-{i}'
        rules.append({'envelope': {'id': ident, 'category': 'obligation', 'scope': 'action',
            'status': 'candidate', 'source_refs': [asdict(section.reference)]},
            'statement': 'Synthetic requirement', 'action': f'action-{i}', 'selector': '*',
            'guard': None, 'parameters': [], 'overrides': []})
        coverage.append({'section_id': section.id, 'status': 'represented', 'rule_ids': [ident],
                         'reason': 'Synthetic coverage assertion'})
    return {'schema_version': 'policy-candidate/1', 'rules': rules, 'coverage': coverage}


class MockProvider:
    def __init__(self, mutate=None):
        self.requests = []
        self.mutate = mutate

    def extract(self, request):
        self.requests.append(request)
        data = response(request.sources)
        if self.mutate:
            self.mutate(data, request)
        return json.dumps(data)


class BriefExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = [Path(self.temp.name) / 'arbitrary.txt', Path(self.temp.name) / 'change.md']
        self.paths[0].write_bytes('Base requirement. Café.\r\n'.encode())
        self.paths[1].write_bytes(b'# Amendment\r\nDifferent requirement.\r\n')
        self.bundle = load_briefs(self.paths)

    def parsed(self, mutate=None):
        data = response(self.bundle)
        if mutate:
            mutate(data)
        return loads(json.dumps(data), self.bundle)

    def test_ingestion_originals_ids_and_offsets(self):
        self.assertEqual([d.input_order for d in self.bundle.documents], [0, 1])
        swapped = load_briefs(list(reversed(self.paths)))
        self.assertEqual({d.id for d in swapped.documents}, {d.id for d in self.bundle.documents})
        for section in self.bundle.sections:
            raw = self.bundle.source_bytes()[section.reference.document_id]
            text = raw.decode()
            ref = section.reference
            self.assertEqual(text[ref.start_offset:ref.end_offset], section.text)
            self.assertEqual(digest(section.text.encode()), ref.span_sha256)
        self.assertEqual(self.paths[0].read_bytes(), self.bundle.originals[0][1])

    def test_sections_plain_preamble_nested(self):
        self.paths[0].write_bytes(b'Preamble\n# First\nA\n## Nested\nB\n# Last\nC\n')
        bundle = load_briefs([self.paths[0]])
        self.assertEqual(len(bundle.sections), 4)
        self.assertEqual(''.join(s.text for s in bundle.sections), self.paths[0].read_text())

    def test_ingestion_failures(self):
        for paths in ([], [self.paths[0], self.paths[0]], [Path(self.temp.name) / 'absent']):
            with self.assertRaises(SourceError):
                load_briefs(paths)
        self.paths[0].write_bytes(b'\xff')
        with self.assertRaises(SourceError):
            load_briefs([self.paths[0]])

    def test_mock_pipeline_and_roundtrip(self):
        provider = MockProvider()
        result = extract_briefs(self.paths, provider, config=ModelConfig('synthetic', 'test'))
        self.assertTrue(result.validation.valid, result.validation.findings)
        self.assertTrue(result.coverage.valid)
        self.assertFalse(result.compilable)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(loads(dumps(result.candidate), result.sources), result.candidate)
        self.assertEqual(dumps(loads(dumps(result.candidate), result.sources)), dumps(result.candidate))

    def test_invalid_provenance(self):
        for field, value in (('span_sha256', '0' * 64), ('document_id', 'missing'), ('end_offset', 9999)):
            data = response(self.bundle)
            data['rules'][0]['envelope']['source_refs'][0][field] = value
            with self.assertRaises(IRValidationError):
                loads(json.dumps(data), self.bundle)

    def test_unknown_and_unsupported(self):
        for mutation in (lambda d: d.update(extra=True),
                         lambda d: d['rules'][0].update(extra=True),
                         lambda d: d['rules'][0].update(guard={'field': 'fact', 'op': 'eval', 'value': 'code'}),
                         lambda d: d['rules'][0].update(parameters=[{'name': 'x', 'value': {}, 'source_refs': []}])):
            with self.assertRaises(IRValidationError):
                self.parsed(mutation)

    def test_inheritance_without_implicit_override(self):
        result = compose(self.parsed())
        self.assertEqual(len(result.rules), 2)
        self.assertEqual(result.lineage, ())

    def test_full_override_and_lineage(self):
        def mutate(d):
            d['rules'][1].update(action='action-0', overrides=[{'target': 'rule-0', 'selector': None}])
        policy = self.parsed(mutate)
        result = compose(policy)
        self.assertTrue(result.report.valid)
        self.assertEqual([r.rule.envelope.id for r in result.rules], ['rule-1'])
        self.assertEqual(result.lineage, (('rule-0', 'rule-1', None),))
        self.assertEqual(len(policy.rules), 2)

    def test_scoped_override_preserves_residual(self):
        def mutate(d):
            d['rules'][1].update(action='action-0', selector='segment-X',
                overrides=[{'target': 'rule-0', 'selector': 'segment-X'}])
        result = compose(self.parsed(mutate))
        self.assertTrue(result.report.valid)
        self.assertEqual(result.rules[0].excluded_selectors, ('segment-X',))
        self.assertEqual(len(result.rules), 2)

    def test_missing_cycle_and_scope_override(self):
        def missing(d):
            d['rules'][1]['overrides'] = [{'target': 'absent', 'selector': None}]
        self.assertIn('override_missing', {f.code for f in compose(self.parsed(missing)).report.findings})
        def cycle(d):
            d['rules'][1]['action'] = 'action-0'
            for i in range(2):
                d['rules'][i]['overrides'] = [{'target': f'rule-{1-i}', 'selector': None}]
        result = compose(self.parsed(cycle))
        self.assertIn('override_cycle', {f.code for f in result.report.findings})
        self.assertEqual(result.rules, ())
        def invalid_scope(d):
            d['rules'][1].update(action='action-0', selector='X', overrides=[{'target': 'rule-0', 'selector': None}])
        self.assertFalse(compose(self.parsed(invalid_scope)).report.valid)

    def test_contradiction_not_resolved_by_order(self):
        def mutate(d):
            d['rules'][1]['action'] = 'action-0'
            d['rules'][1]['envelope']['category'] = 'prohibition'
        self.assertIn('contradictory_rules', {f.code for f in compose(self.parsed(mutate)).report.findings})

    def test_simple_guards_and_disjoint_selectors(self):
        def mutate(d):
            d['rules'][1]['action'] = 'action-0'
            d['rules'][1]['envelope']['category'] = 'prohibition'
            for i in range(2):
                d['rules'][i]['guard'] = {'field': 'mode', 'op': 'equals', 'value': str(i)}
        self.assertTrue(compose(self.parsed(mutate)).report.valid)

    def test_source_linked_parameters(self):
        def mutate(d):
            for row in d['rules']:
                row['parameters'] = [{'name': 'limit', 'value': 4, 'source_refs': row['envelope']['source_refs']}]
        policy = self.parsed(mutate)
        self.assertEqual(policy.rules[0].parameters[0].value, 4)
        self.assertEqual(loads(dumps(policy), self.bundle), policy)

    def test_conflicting_parameter_values(self):
        def mutate(d):
            for i, row in enumerate(d['rules']):
                row['action'] = 'action-0'
                row['parameters'] = [{'name': 'limit', 'value': i, 'source_refs': row['envelope']['source_refs']}]
        self.assertIn('contradictory_values', {f.code for f in compose(self.parsed(mutate)).report.findings})

    def test_missing_and_false_coverage(self):
        policy = self.parsed(lambda d: d['coverage'].pop())
        self.assertIn('coverage_missing', {f.code for f in check_coverage(policy, self.bundle).findings})
        policy = self.parsed(lambda d: d['coverage'][0].update(rule_ids=['rule-1']))
        self.assertIn('coverage_evidence', {f.code for f in check_coverage(policy, self.bundle).findings})

    def test_unsupported_unresolved_retained(self):
        for status in ('unsupported', 'unresolved'):
            def mutate(d, request):
                d['coverage'][0].update(status=status, rule_ids=[], reason='Requirement is outside the profile')
                d['rules'].pop(0)
            result = extract_briefs(self.paths, MockProvider(mutate), config=ModelConfig('fake', 'fake'), max_repairs=0)
            self.assertFalse(result.coverage.valid)
            self.assertEqual(result.candidate.coverage[0].status.value, status)
            self.assertEqual(result.sources.source_bytes(), self.bundle.source_bytes())

    def test_bounded_repairs(self):
        def mutate(d, request):
            if request.attempt == 0:
                d['rules'][0]['envelope']['source_refs'][0]['span_sha256'] = '0' * 64
        provider = MockProvider(mutate)
        result = extract_briefs(self.paths, provider, config=ModelConfig('fake', 'fake'), max_repairs=1)
        self.assertEqual(result.attempts, 2)
        self.assertTrue(result.validation.valid)
        self.assertTrue(provider.requests[1].repair_findings)
        provider = MockProvider(lambda d, r: d.update(schema_version='bad'))
        result = extract_briefs(self.paths, provider, config=ModelConfig('fake', 'fake'), max_repairs=2)
        self.assertEqual(result.attempts, 3)
        self.assertFalse(result.validation.valid)

    def test_no_provider_and_config_limits(self):
        result = extract_briefs(self.paths)
        self.assertEqual(result.attempts, 0)
        self.assertIsNone(result.candidate)
        self.assertFalse(result.coverage.valid)
        for limit in (-1, 3, True):
            with self.assertRaises(ValueError):
                extract_briefs(self.paths, max_repairs=limit)

    def test_provider_error_is_sanitized(self):
        class Failing:
            def extract(self, request):
                raise RuntimeError('private_marker')
        result = extract_briefs(self.paths, Failing(), config=ModelConfig('fake', 'fake'), max_repairs=0)
        self.assertNotIn('private_marker', json.dumps(result.validation.to_dict()))

    def test_guard_override_cannot_expand_context(self):
        def mutate(d):
            d['rules'][0]['guard'] = {'field': 'mode', 'op': 'equals', 'value': True}
            d['rules'][1].update(action='action-0', overrides=[{'target': 'rule-0', 'selector': None}])
        self.assertIn('override_guard', {f.code for f in compose(self.parsed(mutate)).report.findings})

    def test_guard_scalar_types_are_distinct(self):
        def mutate(d):
            for i, row in enumerate(d['rules']):
                row['action'] = 'action-0'
                row['guard'] = {'field': 'mode', 'op': 'equals', 'value': [True, 1][i]}
            d['rules'][1]['envelope']['category'] = 'prohibition'
        self.assertTrue(compose(self.parsed(mutate)).report.valid)

    def test_duplicate_coverage_and_unsupported_category(self):
        def mutate(d):
            d['coverage'].append(d['coverage'][0])
            d['rules'][0]['envelope']['category'] = 'calculation'
        codes = {f.code for f in check_coverage(self.parsed(mutate), self.bundle).findings}
        self.assertTrue({'coverage_reference', 'unsupported_category'} <= codes)

    def test_invalid_model_configuration(self):
        for config in (None, ModelConfig('', 'x'), ModelConfig('x', 3)):
            with self.assertRaises(ValueError):
                extract_briefs(self.paths, MockProvider(), config=config)


if __name__ == '__main__':
    unittest.main()
