"""benchmarks/packages with benchmarks/harness/packages.py and rules.py: the frozen keys, the separation of
inputs and answers, the rule-level evaluator and the three tiers of evidence."""
import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness import packages, rules
from builder.extraction import claude
from builder.spec.openapi import Api
from builder.validation.spec import validate

TIERS = {'c': 'development', 'd': 'development', 'e': 'development', 'f': 'selection', 'g': 'final'}


def by_id(report):
    return {r['id']: r for r in report['rules']}


def copy_of(names, with_key=True):
    """A temporary packages directory holding copies of some packages."""
    folder = Path(tempfile.mkdtemp())
    for name in names:
        shutil.copytree(packages.PACKAGES / name, folder / name, ignore=None if with_key else shutil.ignore_patterns('key'))
    return folder


class Keys(unittest.TestCase):
    def test_every_key_fits_its_inputs(self):
        self.assertEqual({pid: packages.tier(pid) for pid in packages.ids()}, TIERS)
        for pid in TIERS:
            self.assertEqual(rules.key_problems(pid), [], pid)
            k = packages.key(pid)
            self.assertEqual(sorted(k['fit']), sorted(r['id'] for r in k['rules']), pid)
            self.assertTrue(all(c.get('because') for c in k['checks'] if c['kind'] == 'fact'), 'every expected number says how it was worked out')

    def test_the_inputs_of_the_development_packages_carry_no_answers(self):
        for pid in 'cde':
            documents, _ = packages.inputs(pid)
            text = '\n'.join(body for _, body in documents)
            for part in ('Scenarios', 'agent-spec', 'Synthetic.', '.yaml', 'Not expressible', 'S%s-0' % pid.upper(), 'benchmark'):
                self.assertNotIn(part, text, (pid, part))

    def test_the_inputs_of_the_selection_and_final_packages_carry_no_answers(self):
        for pid in 'fg':
            documents, api = packages.inputs(pid)
            text = '\n'.join(body for _, body in documents) + '\n' + json.dumps(api)
            for part in ('benchmark', 'because', 'unsupported', 'reference', 'expected', 'agent-spec', 'holdout', 'selection', 'known gap', 'not evaluated'):
                self.assertNotIn(part, text.lower(), (pid, part))
            self.assertIsNone(re.search(r'\b[FG]-\d\d\b|\b[FG]\.[cfpst]\d', text), pid)
            k = packages.key(pid)
            dates = {c['expect']['latest_date'] for c in k['checks'] if c['kind'] == 'fact' and 'latest_date' in c['expect']}
            self.assertGreaterEqual(len(dates), 2)
            for date in dates:
                self.assertNotIn(date, text, 'no worked-out deadline is part of an input')
            self.assertEqual(sorted(r['id'] for r in k['rules'] if r.get('known_gap')), {'f': ['F-18', 'F-19'], 'g': ['G-03', 'G-14']}[pid])

    def test_a_key_that_does_not_fit_is_reported(self):
        k = packages.key('g')
        k['rules'][0]['evidence'] = 'A sentence that is not in the brief.'
        k['checks'][0]['rules'] = ['G-99']
        k['checks'][1]['kind'] = 'guess'
        next(c for c in k['checks'] if c['kind'] == 'calls').pop('asserts')
        del k['fit']['G-05']
        with mock.patch.object(packages, 'key', return_value=k):
            text = ' | '.join(rules.key_problems('g'))
        for expected in ('G-01: the evidence is not a verbatim part of the input documents', 'refers to unknown rule G-99', "unknown kind 'guess'",
                         'G-05: no valid entry in reference.fit.json', 'a calls check has to say whether it asserts policy or enforcement'):
            self.assertIn(expected, text)


class Separation(unittest.TestCase):
    def test_an_extraction_runs_with_the_key_removed(self):
        reply = json.dumps(packages.key('g')['spec'])
        folder = copy_of(['g'], with_key=False)
        try:
            with mock.patch.object(packages, 'PACKAGES', folder):
                documents, document = packages.inputs('g')
                self.assertFalse((folder / 'g' / 'key').exists())
                with self.assertRaises(FileNotFoundError):
                    packages.key('g')
                client = claude.ReplayClient([reply])
                spec = claude.extract(documents, json.dumps(document), client, lambda s: validate(s, Api(document)))
        finally:
            shutil.rmtree(folder)
        self.assertEqual(spec['spec_version'], 'agent-spec/1')
        sent = client.seen[0][1][0]['content']
        self.assertIn('Brightwater Mutual', sent)
        for part in ('G.c6', 'because', 'reference', 'whichever is greater: both give'):
            self.assertNotIn(part, sent, 'nothing of the key reaches the model')

    def test_the_freeze_notices_a_changed_input_key_or_reference(self):
        folder = copy_of(['f', 'g'])
        try:
            with mock.patch.object(packages, 'PACKAGES', folder), mock.patch.object(packages, 'MANIFEST', folder / 'MANIFEST.json'):
                self.assertEqual(packages.verify(), ['there is no MANIFEST.json: the packages are not frozen'])
                packages.freeze()
                self.assertEqual(packages.verify(), [])
                brief = folder / 'g' / 'input' / 'brief.md'
                brief.write_bytes(brief.read_bytes().replace(b'\n', b'\r\n'))
                self.assertEqual(packages.verify(), [], 'a checkout that changes line endings changes nothing')
                brief.write_text(brief.read_text(encoding='utf-8').replace('25.00', '30.00'), encoding='utf-8')
                key = folder / 'f' / 'key' / 'checks.json'
                key.write_text(key.read_text(encoding='utf-8').replace('2026-10-26', '2026-10-27'), encoding='utf-8')
                spec = folder / 'f' / 'key' / 'reference.spec.json'
                spec.write_text(spec.read_text(encoding='utf-8').replace('"max_attempts": 3', '"max_attempts": 4'), encoding='utf-8')
                self.assertEqual(packages.verify(), ['f: key/checks.json changed since the freeze', 'f: reference/reference.spec.json changed since the freeze',
                                                     'g: inputs/brief.md changed since the freeze'])
                self.assertEqual(packages.verify('g'), ['g: inputs/brief.md changed since the freeze'])
        finally:
            shutil.rmtree(folder)


class Evaluation(unittest.TestCase):
    def test_no_reference_spec_contradicts_its_key(self):
        for pid in TIERS:
            report = rules.evaluate(pid)
            self.assertEqual(report['reference_problems'], [], pid)
            for rule in report['rules']:
                self.assertNotEqual(rule['layer'], 'key', (pid, rule))
                if rule['fit'] == 'unsupported':
                    self.assertEqual((rule['layer'], rule['reference'], rule['end_to_end']), ('schema', 'cannot carry the rule', 'unsupported'), rule['id'])
                if rule['fit'] == 'structured' and rule['layer'] is None:
                    self.assertIn(rule['end_to_end'], ('enforced', 'encoded, enforcement not played', 'text only'), rule['id'])
                    self.assertEqual(rule['end_to_end'] == 'enforced', rule['enforcement_evidence'] != 'none', rule['id'])
            self.assertEqual(report['summary']['contract']['problems'], [], pid)
            self.assertEqual(report['static_contract'], [], pid)

    def test_what_the_reference_specs_show_today(self):
        layers = lambda pid: {r['id']: r['layer'] for r in rules.evaluate(pid)['rules'] if r['layer'] in ('schema', 'compiler/runtime')}
        self.assertEqual(layers('g'), {'G-03': 'schema', 'G-14': 'schema'})
        self.assertEqual(layers('c'), {'C-04': 'schema', 'C-08': 'schema', 'C-11': 'schema'})
        # Package F renames the call id. Before the call fields were read from the tool API, a block that
        # detaches a request reached the bank there (F-04, F-25). Nothing but the two known gaps is left.
        self.assertEqual(layers('f'), {'F-18': 'schema', 'F-19': 'schema'})

    def test_the_reference_spec_itself_is_a_correct_extraction(self):
        report = rules.evaluate('g', packages.key('g')['spec'])
        got = report['summary']['extraction']
        self.assertEqual((got['incorrect'], got['missing'], got['partial'], got['accuracy'], got['critical_recall'], got['valid']), (0, 0, 0, 1.0, 1.0, True))
        self.assertEqual(got['unsupported_rules'], 2)
        rule = by_id(report)
        self.assertEqual(rule['G-14']['extraction'], 'unsupported by schema: text only')
        self.assertEqual(rule['G-23']['end_to_end'], 'partial', 'a rule the schema carries in part is never reported as enforced')
        self.assertEqual((rule['G-16']['policy_evidence'], rule['G-16']['enforcement_evidence'], rule['G-16']['end_to_end']), ('5/5', '1/1', 'enforced'))
        self.assertEqual((rule['G-15']['policy_evidence'], rule['G-15']['enforcement_evidence'], rule['G-15']['end_to_end']), ('1/1', 'none', 'encoded, enforcement not played'))
        self.assertEqual(rule['G-20']['extraction'], 'correct (text only, weak evidence)')
        self.assertEqual(rule['G-17']['extraction'], 'not evaluated')

    def test_policy_and_enforcement_are_two_kinds_of_evidence(self):
        g = by_id(rules.evaluate('g'))
        self.assertEqual((g['G-21']['policy_evidence'], g['G-21']['enforcement_evidence'], g['G-21']['end_to_end']), ('2/2', '1/1', 'enforced'))
        f = by_id(rules.evaluate('f'))
        # That the notes reach the bank when the agent sends them, and that a close before them is refused,
        # are counted apart.
        self.assertEqual((f['F-25']['policy_evidence'], f['F-25']['enforcement_evidence'], f['F-25']['layer']), ('2/2', '1/1', None))
        summary = rules.evaluate('f')['summary']
        self.assertEqual(summary['behaviour']['policy']['passed'], summary['behaviour']['policy']['run'])
        self.assertEqual(summary['behaviour']['enforcement']['run'] - summary['behaviour']['enforcement']['passed'], 2, 'the two rules unsupported by design')

    def test_consent_before_recording_stays_critical_and_is_not_called_confirmed(self):
        report = rules.evaluate('f')
        rule = by_id(report)['F-21']
        self.assertEqual((rule['critical'], rule['weak_only'], rule['unconfirmed'], rule['end_to_end']), (True, True, True, 'text only'))
        self.assertEqual((rule['policy_evidence'], rule['enforcement_evidence'], rule['weak_evidence']), ('none', 'none', '2/2'))
        self.assertIn('not confirmed by behaviour offline', rule['note'])
        self.assertIn('F-21', report['summary']['critical']['weak_evidence_only'])
        self.assertIn('F-21', report['summary']['critical']['not_confirmed_by_behaviour'])
        self.assertEqual(report['summary']['critical']['not_evaluated'], ['F-03', 'F-42', 'F-44', 'F-48'])
        g = by_id(rules.evaluate('g'))['G-18']
        self.assertEqual((g['critical'], g['unconfirmed'], g['end_to_end'], g['enforcement_evidence']), (True, True, 'partial', '1/1'))
        spec = copy.deepcopy(packages.key('f')['spec'])
        spec['rules']['after_verification'] = spec['rules']['after_verification'].replace('"Shall I record that?"', 'whether to record it')
        rule = by_id(rules.evaluate('f', spec))['F-21']
        self.assertEqual((rule['extraction'], rule['weak_evidence'], rule['layer']), ('missing', '0/2', 'extraction'))

    def test_spoken_labels_ignore_capitals_and_everything_else_is_exact(self):
        self.assertEqual(rules.differences({'label': 'card ending 5530'}, {'label': 'Card  Ending 5530'}, ''), [])
        self.assertEqual(len(rules.differences({'label': 'card ending 5530'}, {'label': 'card 5530'}, '')), 1)
        self.assertEqual(len(rules.differences({'latest_date': '2026-10-26'}, {'latest_date': '2026-10-27'}, '')), 1)
        spec = copy.deepcopy(packages.key('f')['spec'])
        spec['account']['values'][0]['expr']['cases'][0]['expr']['template'] = 'Card ending {card_tail}'
        spec['close']['queue_reasons']['payment_plans'] = 'Plan'
        rule = by_id(rules.evaluate('f', spec))
        self.assertEqual(rule['F-10']['extraction'], 'correct')
        self.assertNotEqual(rule['F-32']['extraction'], 'correct', 'a reason code is a value of the tool API and is compared exactly')

    def test_wrong_missing_and_undeclared_policy_are_told_apart(self):
        spec = copy.deepcopy(packages.key('g')['spec'])
        spec['identity']['max_wrong'] = 4
        spec['unsupported'] = ['Rule 3: no callback from 12:30 to 13:30 (lunch) cannot be expressed.']
        spec['rules']['after_verification'] = spec['rules']['after_verification'].replace(
            ' All promises of one call carry the same date: if the member wants different dates, explain that one date applies to all and agree one.', '')
        spec['rules']['after_verification'] = spec['rules']['after_verification'].replace('"Do I have that right?"', 'whether that is right')
        rule = by_id(rules.evaluate('g', spec))
        self.assertEqual((rule['G-07']['extraction'], rule['G-07']['layer'], rule['G-07']['end_to_end']), ('partial', 'extraction', 'failed'))
        self.assertEqual(rule['G-03']['extraction'], 'unsupported by schema: declared')
        self.assertEqual(rule['G-14']['extraction'], 'unsupported by schema: silent')
        self.assertEqual((rule['G-05']['extraction'], rule['G-05']['layer']), ('correct', None))
        spec = copy.deepcopy(packages.key('f')['spec'])
        spec['calendar']['holidays'].remove('2026-10-16')
        spec['identity']['questions'].reverse()
        rule = by_id(rules.evaluate('f', spec))
        self.assertEqual(rule['F-12']['extraction'], 'missing', 'the memo\'s holiday appears nowhere in the spec')
        self.assertEqual(rule['F-06']['extraction'], 'incorrect', 'the question is there, in the wrong place')
        self.assertEqual(rule['F-53']['extraction'], 'incorrect')
        self.assertEqual(rule['F-08']['extraction'], 'correct', 'the limit of two is kept although the tool API says three')
        self.assertEqual((rule['F-04']['extraction'], rule['F-04']['layer']), ('correct', None))

    def test_a_spec_that_does_not_validate_is_wrong_everywhere_and_says_so(self):
        report = rules.evaluate('g', {'spec_version': 'agent-spec/1'})
        got = report['summary']['extraction']
        self.assertEqual((got['valid'], got['correct']), (False, 0))
        self.assertTrue(report['spec_problems'])
        self.assertEqual(report['summary']['behaviour']['passed'], 0)

    def test_mutated_reference_specs_are_attributed_to_their_rules(self):
        for pid in ('c', 'f', 'g'):
            rows = rules.mutation_baseline(pid)
            self.assertGreaterEqual(len(rows), 4)
            for row in rows:
                self.assertTrue(row['ok'], row)
                self.assertTrue(set(row['expected']) <= set(row['flagged']))
        spec = rules.mutate({'a': [{'b': 1}, {'b': 2}], 'c': {'d': 3}}, [{'path': '/a/1/b', 'set': 5}, {'path': '/a/0', 'remove': True}, {'path': '/c/d', 'remove': True}])
        self.assertEqual(spec, {'a': [{'b': 5}], 'c': {}})


class RenamedBank(unittest.TestCase):
    def test_the_stand_in_bank_speaks_the_package_names(self):
        names = packages.key('f')['bank']
        bank = rules.MappedBank(names, 'c_1', {'birth_date': '1985-03-04', 'account_last3': '417'}, [{'kind': 'card', 'overdue_amount': 400.0, 'days_overdue': 12}])
        status, out = bank.call('verify_identity', {'case_id': 'c_1', 'question': 'birth_date', 'given_answer': '1985-03-04'})
        self.assertEqual((status, out['success'], out['match'], out['tries_left']), (200, True, True, 3))
        bank.call('verify_identity', {'case_id': 'c_1', 'question': 'account_last3', 'given_answer': '417'})
        status, out = bank.call('get_account', {'case_id': 'c_1'})
        self.assertEqual(out['accounts'][0]['account_ref'], 'p1')
        bank.call('create_promise', {'case_id': 'c_1', 'account_ref': 'p1', 'pay_amount': 400, 'pay_date': '2026-10-15'})
        self.assertEqual(bank.ledger['promises'], [{'product_id': 'p1', 'amount': 400, 'date': '2026-10-15'}])

    def test_a_stray_field_under_the_old_name_does_not_pass_for_the_call_id(self):
        names = packages.key('f')['bank']
        bank = rules.MappedBank(names, 'c_1', {'birth_date': '1985-03-04'}, [])
        self.assertEqual(bank.call('schedule_callback', {'case_id': 'blocked_by_policy', 'callback_date': '2026-10-15', 'callback_time': '10:00'})[0], 404)
        status, out = bank.call('schedule_callback', {'case_id': 'c_1', 'customer_id': 'blocked_by_policy', 'callback_date': '2026-10-15', 'callback_time': '10:00'})
        self.assertEqual((status, out['success']), (200, True), 'the bank of this API knows the call by case_id and ignores customer_id')
        self.assertEqual(bank.ledger['callbacks'], [{'date': '2026-10-15', 'time': '10:00'}])


class Tiers(unittest.TestCase):
    def test_selection_and_final_packages_are_opened_on_the_record_or_not_at_all(self):
        folder = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(rules, 'EXPOSURE', folder / 'exposure.jsonl'):
                self.assertIsNone(rules.expose('c', None, {}))
                self.assertEqual(rules.exposures(), [])
                with self.assertRaises(PermissionError):
                    rules.expose('f', None, {'prompt': 'p0'})
                entry = rules.expose('f', 'one batch of the frozen candidates', {'prompt': 'p0'})
                self.assertEqual((entry['tier'], entry['label']), ('selection', 'model-selection evidence'))
                with self.assertRaises(PermissionError):
                    rules.expose('g', 'final run', {'prompt': 'p0'})
                entry = rules.expose('g', 'final run', {'prompt': 'p0'}, frozen_candidates='candidates.json')
                self.assertEqual(entry['label'], 'final holdout')
                for pid, config in (('g', {'prompt': 'p0'}), ('g', {'prompt': 'p1'}), ('f', {'prompt': 'p0'})):
                    with self.assertRaises(PermissionError, msg='a second look is never silent, not even at the same configuration'):
                        rules.expose(pid, 'again', config, frozen_candidates='candidates.json')
                entry = rules.expose('g', 'another prompt', {'prompt': 'p1'}, relabel='model-selection evidence')
                self.assertEqual(entry['label'], 'model-selection evidence')
                self.assertEqual([e['package'] for e in rules.exposures()], ['f', 'g', 'g'])
        finally:
            shutil.rmtree(folder)


if __name__ == '__main__':
    unittest.main()
