"""builder/extraction/claude.py: the three extraction strategies and the sectioned prompt, with scripted model replies."""
import copy
import json
import unittest

from builder import pipeline
from builder.extraction import claude
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import digest, validate
from benchmarks.harness.fixture_spec import spec as fixture
from tests.support import KIT, OPENAPI

API = Api(OPENAPI)
OPENAPI_TEXT = json.dumps(OPENAPI)
BRIEFS = [('brief-a.md', (KIT / 'briefs/brief-a.md').read_text(encoding='utf-8'))]
WRONG_ANSWERS = 'Three wrong answers in total end the check, whichever question they were on.'
CALLBACK = 'A callback is for today or the next business day, from 10:00 to 19:00, both included, and later than `now`.'
LIMIT, WINDOW = 'wrong answers that end the check', 'callback'


def variant(**parts):
    spec = fixture(True)
    for path, value in parts.items():
        node = spec
        keys = path.split('__')
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return spec


class Scripted:
    """A model that answers from a list, and keeps what it was asked."""

    def __init__(self, *replies):
        self.replies, self.seen, self.model = [r if isinstance(r, str) else json.dumps(r) for r in replies], [], 'scripted'

    def complete(self, system, messages):
        self.seen.append((system, messages))
        text = self.replies.pop(0)
        return text, {'model': 'scripted', 'input_tokens': 100, 'output_tokens': 40, 'stop_reason': 'end_turn'}, text


def run(strategy, client, **more):
    lines, usage = [], []
    spec = claude.run(strategy, BRIEFS, OPENAPI_TEXT, client, lambda s: validate(s, API, True), lines.append,
                      digest=lambda one, other=None: digest(one, API, other), usage=usage, **more)
    return spec, lines, usage


class Single(unittest.TestCase):
    def test_one_call_and_the_same_loop_as_before(self):
        client = Scripted(fixture(True))
        spec, lines, usage = run('single', client)
        self.assertEqual((spec, len(client.seen)), (fixture(True), 1))
        self.assertEqual([u['purpose'] for u in usage], ['extract agent-spec/1 from the brief'])
        self.assertEqual(claude.extract(BRIEFS, OPENAPI_TEXT, Scripted(fixture(True)), lambda s: validate(s, API)), fixture(True))

    def test_a_reply_the_validator_refuses_is_repaired_and_then_given_up(self):
        broken = variant(promise__amount_param='sum')
        client = Scripted('no json here', broken, fixture(True))
        spec, lines, usage = run('single', client)
        self.assertEqual(spec['promise']['amount_param'], 'amount')
        self.assertIn("promise.amount_param='sum' is not a body field of create_promise", client.seen[2][1][-1]['content'])
        for strategy in claude.STRATEGIES:
            client = Scripted('{}', '{}', '{}', '{}')
            with self.assertRaises(SpecError):
                run(strategy, client)
            self.assertEqual(len(client.seen), 3, 'no strategy goes on with a spec that never validated')

    def test_an_unknown_strategy_and_a_missing_digest_are_errors(self):
        with self.assertRaises(ValueError):
            run('triple', Scripted(fixture(True)))
        with self.assertRaises(ValueError):
            claude.run('audit', BRIEFS, OPENAPI_TEXT, Scripted(fixture(True)), lambda s: validate(s, API))


class Audit(unittest.TestCase):
    def test_nothing_found_costs_one_extra_call_and_changes_nothing(self):
        client = Scripted(fixture(True), '[]')
        spec, lines, usage = run('audit', client)
        self.assertEqual((spec, len(client.seen)), (fixture(True), 2))
        system, messages = client.seen[1]
        self.assertEqual(system, claude.AUDIT)
        self.assertIn('- %s: 3' % LIMIT, messages[0]['content'], 'the audit reads what the spec does, not the JSON as written')
        self.assertIn('brief-a.md', messages[0]['content'])
        self.assertIn('audit: 0 finding(s)', ' '.join(lines))

    def test_a_finding_with_evidence_from_the_documents_leads_to_one_revision(self):
        wrong = variant(identity__max_wrong=4)
        finding = [{'item': LIMIT, 'problem': 'The documents end the check after three wrong answers.', 'evidence': WRONG_ANSWERS, 'fix': 3}]
        client = Scripted(wrong, finding, fixture(True))
        spec, lines, usage = run('audit', client)
        self.assertEqual((spec['identity']['max_wrong'], len(client.seen)), (3, 3))
        self.assertIn('- %s: 4' % LIMIT, client.seen[1][1][0]['content'])
        revision = client.seen[2][1]
        self.assertEqual([m['role'] for m in revision], ['user', 'assistant', 'user'])
        self.assertIn(WRONG_ANSWERS, revision[2]['content'])
        self.assertEqual(json.loads(revision[1]['content']), wrong)
        self.assertEqual([u['purpose'] for u in usage][1:], ['audit the high-risk content of the spec against the documents', 'revise the spec after the audit'])

    def test_a_finding_without_a_real_quotation_is_ignored(self):
        wrong = variant(identity__max_wrong=4)
        invented = [{'item': LIMIT, 'problem': 'Should be two.', 'evidence': 'Two wrong answers end the check at this bank.', 'fix': 2},
                    {'item': LIMIT, 'problem': 'No evidence at all.'}, {'item': LIMIT, 'evidence': 'check'}]
        client = Scripted(wrong, invented)
        spec, lines, usage = run('audit', client)
        self.assertEqual((spec, len(client.seen)), (wrong, 2), 'nothing is changed on the word of the auditor alone')

    def test_an_audit_reply_that_cannot_be_read_finds_nothing(self):
        for reply in ('I think this looks fine.', '[{"item": ', '{"item": "x"}', '["text", 3]'):
            client = Scripted(fixture(True), reply)
            spec, _, _ = run('audit', client)
            self.assertEqual((spec, len(client.seen)), (fixture(True), 2), reply)

    def test_a_revision_the_validator_refuses_is_dropped(self):
        wrong = variant(identity__max_wrong=4)
        finding = [{'item': LIMIT, 'problem': 'x', 'evidence': WRONG_ANSWERS}]
        for bad in ('{}', 'sorry', variant(promise__amount_param='sum')):
            client = Scripted(wrong, finding, bad)
            spec, lines, _ = run('audit', client)
            self.assertEqual((spec, len(client.seen)), (wrong, 3), 'the valid spec is kept; there is no second revision')
            self.assertIn('revision dropped', ' '.join(lines))


class Double(unittest.TestCase):
    def test_two_extractions_that_mean_the_same_need_no_adjudication(self):
        other = fixture(True)
        other['rules']['always'] += ' Be polite.'
        other['account']['values'][1]['label'] = 'TOTAL past-due amount'
        client = Scripted(fixture(True), other)
        spec, lines, usage = run('double', client)
        self.assertEqual((spec, len(client.seen)), (fixture(True), 2))
        self.assertEqual(client.seen[0], client.seen[1], 'the two extractions are asked the same and know nothing of each other')
        self.assertIn('double extraction: 0 item(s) differ', ' '.join(lines))

    def test_the_extraction_that_is_right_more_often_is_kept_whole(self):
        wrong = variant(identity__max_wrong=4)
        verdict = [{'item': LIMIT, 'right': 'B', 'evidence': WRONG_ANSWERS}]
        client = Scripted(wrong, fixture(True), verdict)
        spec, lines, usage = run('double', client)
        self.assertEqual((spec, len(client.seen)), (fixture(True), 3))
        system, messages = client.seen[2]
        self.assertEqual(system, claude.ADJUDICATE)
        self.assertIn('- %s\n  A: 4\n  B: 3' % LIMIT, messages[0]['content'])
        self.assertIn('adjudication: 1 decided with evidence, A right 0, B right 1', ' '.join(lines))
        self.assertTrue(any(line.startswith('differs: %s | A: 4 | B: 3' % LIMIT) for line in lines), 'what differed is kept in the trace')
        self.assertTrue(any(line.startswith('decided: %s | right: B' % LIMIT) for line in lines))

    def test_where_each_is_right_once_the_first_is_kept_and_revised_once(self):
        a = variant(identity__max_wrong=4)
        b = variant(callback__end='18:00')
        verdict = [{'item': LIMIT, 'right': 'B', 'evidence': WRONG_ANSWERS}, {'item': WINDOW, 'right': 'A', 'evidence': CALLBACK}]
        client = Scripted(a, b, verdict, fixture(True))
        spec, lines, usage = run('double', client)
        self.assertEqual((spec, len(client.seen)), (fixture(True), 4))
        asked = client.seen[3][1][2]['content']
        self.assertIn(LIMIT, asked)
        self.assertNotIn('- ' + WINDOW, asked, 'only what the kept spec got wrong is revised')
        self.assertEqual(usage[-1]['purpose'], 'revise the spec after the adjudication')

    def test_a_verdict_without_evidence_or_that_cannot_be_read_keeps_the_first(self):
        a, b = variant(identity__max_wrong=4), fixture(True)
        for reply in ('B is right.', [{'item': LIMIT, 'right': 'B', 'evidence': 'Because I say so, obviously.'}], [{'item': 'something else', 'right': 'B', 'evidence': WRONG_ANSWERS}],
                      [{'item': LIMIT, 'right': 'both', 'evidence': WRONG_ANSWERS}]):
            client = Scripted(a, b, reply)
            spec, _, _ = run('double', client)
            self.assertEqual((spec, len(client.seen)), (a, 3), reply)

    def test_neither_right_is_revised_with_the_stated_fix(self):
        a, b = variant(identity__max_wrong=4), variant(identity__max_wrong=5)
        verdict = [{'item': LIMIT, 'right': 'neither', 'evidence': WRONG_ANSWERS, 'fix': 3}]
        client = Scripted(a, b, verdict, fixture(True))
        spec, _, _ = run('double', client)
        self.assertEqual(spec['identity']['max_wrong'], 3)
        self.assertIn('Should be: 3', client.seen[3][1][2]['content'])


class Common(unittest.TestCase):
    def test_every_strategy_returns_a_spec_the_validator_accepts_and_traces_every_call(self):
        scripts = {'single': [fixture(True)], 'audit': [fixture(True), '[]'], 'double': [fixture(True), fixture(True)]}
        for strategy, replies in scripts.items():
            client = Scripted(*replies)
            spec, lines, usage = run(strategy, client)
            self.assertEqual(validate(spec, API, True), [])
            self.assertEqual(sum('model call: scripted | purpose: ' in line for line in lines), len(client.seen), strategy)
            self.assertEqual((len(usage), sum(u['input_tokens'] for u in usage)), (len(client.seen), 100 * len(client.seen)))

    def test_the_digest_is_the_same_for_two_ways_of_writing_one_policy(self):
        a, b = fixture(True), fixture(True)
        b['account']['values'][4]['expr'] = {'kind': 'by', 'cases': [{'when': {'days_late': {'max': 7}}, 'expr': {'kind': 'business_days_after', 'n': 12}},
                                                                    {'when': {}, 'expr': {'kind': 'business_days_after', 'n': 7}}]}
        self.assertEqual(digest(a, API, b), digest(b, API, a))
        c = copy.deepcopy(b)
        c['account']['values'][4]['expr']['cases'][0]['when'] = {'days_late': {'max': 8}}
        differ = [k for k, v in digest(a, API, c).items() if digest(c, API, a)[k] != v]
        self.assertTrue(differ and all('days_late=8' in k for k in differ), 'a moved boundary shows on the product at that boundary and nowhere else')

    def test_a_true_or_false_field_of_a_product_is_sampled_as_true_and_false(self):
        from benchmarks.harness import packages
        from builder.validation.spec import sample_products
        _, document = packages.inputs('c')
        api, old, new = Api(document), packages.key('c')['spec'], packages.key('c', 'v2')['spec']
        self.assertEqual({p['plan_active'] for p in sample_products(new, api)}, {True, False})
        # Two ways of writing "only when no plan is active and at most 60 days late" are one policy, and the digest says so.
        other = copy.deepcopy(new)
        value = next(v for v in other['account']['values'] if v['name'] == other['account']['limits']['promise_allowed'])
        value['expr'] = {'kind': 'by', 'cases': [{'when': {'plan_active': False, 'days_late': {'max': 60}}, 'expr': {'kind': 'const', 'value': True}},
                                                 {'when': {}, 'expr': {'kind': 'const', 'value': False}}]}
        validate(other, api, True)
        self.assertEqual(digest(new, api, other), digest(other, api, new))
        self.assertEqual(validate(old, api, True), [])

    def test_the_prompt_is_sections_and_only_three_of_them_can_be_replaced(self):
        self.assertEqual(claude.system_prompt(), claude.SYSTEM)
        self.assertEqual(claude.MUTABLE, ('rules_writing', 'structuring', 'reading'))
        changed = claude.system_prompt({'reading': 'Read every document twice before you write anything at all.'})
        self.assertTrue(changed.startswith(claude.FIXED))
        self.assertIn('Read every document twice', changed)
        self.assertNotIn(claude.SECTIONS['reading'], changed)
        for part in ('"spec_version": "agent-spec/1"', '## EXPR', '## FLAG', '## guidance events', '"promise_allowed"', 'note_recorded:<reason>'):
            self.assertIn(part, claude.FIXED, 'the definition of agent-spec/1 is in the part that cannot be replaced')
        for name in ('schema', 'fixed', 'FIXED', 'output'):
            with self.assertRaises(ValueError):
                claude.system_prompt({name: 'x'})
        client = Scripted(fixture(True))
        run('single', client, system=changed)
        self.assertEqual(client.seen[0][0], changed)

    def test_the_command_takes_the_strategy_and_defaults_to_one_call(self):
        a = pipeline.arguments(['--brief', 'a.md', '--tool-spec', 'api.json'])
        self.assertEqual(a.strategy, 'single')
        self.assertEqual(pipeline.arguments(['--brief', 'a.md', '--tool-spec', 'api.json', '--strategy', 'audit']).strategy, 'audit')


if __name__ == '__main__':
    unittest.main()
