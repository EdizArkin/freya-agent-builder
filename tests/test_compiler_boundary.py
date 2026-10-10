"""Current candidate data cannot bypass semantic/runtime readiness."""
import json
from pathlib import Path
import tempfile
import unittest
from builder.brief.sources import load_briefs
from builder.compiler.compile import compile_candidate, main
from test_brief_extraction import response


class CompilerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.brief = Path(self.temp.name) / 'synthetic.txt'
        self.brief.write_text('Synthetic requirement.\n', encoding='utf-8')
        self.sources = load_briefs([self.brief])

    def test_structurally_valid_candidate_still_cannot_compile(self):
        result = compile_candidate(json.dumps(response(self.sources)), self.sources)
        self.assertFalse(result['compiled'])
        self.assertIsNone(result['workflow'])
        self.assertIn('ir_not_executable', result['findings'])

    def test_contradiction_is_reported_not_converted_to_prompts(self):
        data = response(self.sources)
        other = json.loads(json.dumps(data['rules'][0]))
        other['envelope'].update(id='other', category='prohibition')
        data['rules'].append(other)
        self.assertIn('contradictory_rules', compile_candidate(json.dumps(data), self.sources)['findings'])

    def test_missing_mandatory_rules_and_no_output(self):
        data = response(self.sources)
        data['rules'] = []
        data['coverage'] = []
        result = compile_candidate(json.dumps(data), self.sources)
        self.assertIn('mandatory_rules_missing', result['findings'])
        policy = Path(self.temp.name) / 'policy.json'
        policy.write_text(json.dumps(data), encoding='utf-8')
        output = Path(self.temp.name) / 'workflow.json'
        self.assertEqual(main(['--brief', str(self.brief), '--policy', str(policy), '--output', str(output)]), 1)
        self.assertFalse(output.exists())
