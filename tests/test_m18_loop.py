"""The builder's optimization run: the decision, the limits, the diagnosis, the candidate it proposes itself, what a
step does on the platform and on disk, the run over several steps, and the command.

No test here reaches Freya or a model. The platform is the fake of tests/test_m18_versions.py, the proposing model
answers from prepared replies, and the evaluation is an object that hands out prepared rows. The build and the
deploy are the real ones (pipeline.release), against that fake platform and a registry in a temporary folder.
What none of this shows is a live run: whether a model proposes a useful change, and what a phone model does
with it.
"""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness import evaluation as harness
from benchmarks.harness import live
from builder import pipeline
from builder.artifacts import store
from builder.extraction import claude, spend
from builder.freya import versioning
from builder.optimization import candidate, cli, diagnosis, loop, policy
from builder.spec.openapi import Api
from builder.validation.spec import digest, validate
from benchmarks.harness.fixture_spec import spec as fixture
from tests.support import KIT, OPENAPI
from tests.test_m18_versions import Platform

API = Api(OPENAPI)
CHECK, DIGEST = (lambda s: validate(s, API)), (lambda s: digest(s, API))
FIND = 'Read all promises back together, ask for one confirmation, then call create_promise once per product.'
GOOD = {'case': 'PUB-12', 'hypothesis': 'The sentence lets the agent ask twice.', 'evidence': 'AGENT: Can you pay?', 'risk': 'none seen',
        'field': 'rules.after_verification', 'find': FIND, 'replace': FIND + ' Ask for that confirmation in one message.'}
OTHER = dict(GOOD, replace=FIND + ' Say the amounts as numbers.')
SAID = [{'role': 'assistant', 'message': 'Can you pay?'}, {'role': 'user', 'message': 'Yes.'}]


def row(passed=True, ledger=True, judge=None, unexpected=(), **more):
    return dict({'pass': passed, 'ledger_pass': ledger, 'judge_outcome': judge or ('pass' if passed else 'fail'), 'unexpected_records': list(unexpected)}, **more)


def failed(**more):
    """A case that failed with a transcript to diagnose from."""
    return row(False, failed=['conversation: one ask too many'], messages=SAID, **more)


BEFORE = {'PUB-11': row(), 'PUB-12': failed(), 'PUB-13': row()}
BETTER = {'PUB-11': row(), 'PUB-12': row(), 'PUB-13': row()}
SAFE = ['PUB-11']


class Decision(unittest.TestCase):
    def test_a_safe_candidate_that_fixes_a_case_and_breaks_none_is_accepted(self):
        got = policy.decide(BEFORE, BETTER, SAFE)
        self.assertEqual((got['decision'], got['gate'], got['improved'], got['regressed']), ('accept', 'improvement', ['PUB-12'], []))

    def test_without_a_named_safety_case_nothing_is_promoted(self):
        for none in ([], (), None):
            got = policy.decide(BEFORE, BETTER, none)
            self.assertEqual((got['decision'], got['gate']), ('rollback', 'evidence'))
            self.assertIn('no safety case was named', got['reason'])

    def test_a_higher_score_does_not_buy_an_unsafe_candidate(self):
        before = {'A': row(False), 'B': row(False), 'C': row()}
        for unsafe in (row(False, ledger=False, judge='pass'), row(True, unexpected=['promise [p1, 250.0]']), row(False, ledger=False)):
            got = policy.decide(before, {'A': row(), 'B': row(), 'C': unsafe}, ['A'])
            self.assertEqual((got['decision'], got['gate'], got['unsafe']), ('rollback', 'safety', ['C']))
        got = policy.decide(before, {'A': row(False), 'B': row(), 'C': row()}, ['A'])
        self.assertEqual((got['gate'], got['unsafe']), ('safety', ['A']), 'a safety case that fails rejects, whatever else improved')

    def test_a_swap_a_bank_mismatch_a_regression_and_no_change_all_keep_the_incumbent(self):
        self.assertEqual(policy.decide(BEFORE, {'PUB-11': row(), 'PUB-12': row(), 'PUB-13': row(False)}, SAFE)['gate'], 'regression', 'the same score with other cases passing')
        self.assertEqual(policy.decide(BEFORE, {'PUB-11': row(), 'PUB-12': row(False, ledger=False), 'PUB-13': row()}, SAFE)['gate'], 'safety')
        self.assertEqual(policy.decide(BEFORE, {'PUB-11': row(), 'PUB-12': row(False), 'PUB-13': row(False)}, SAFE)['gate'], 'regression')
        got = policy.decide(BEFORE, dict(BEFORE), SAFE)
        self.assertEqual((got['decision'], got['gate'], got['reason']), ('rollback', 'improvement', 'no case that failed before passes now'))

    def test_missing_or_ambiguous_evidence_is_never_an_improvement(self):
        for broken in ({k: v for k, v in BETTER.items() if k != 'PUB-13'}, dict(BETTER, **{'PUB-13': dict(row(), judge_outcome='running')}),
                       dict(BETTER, **{'PUB-13': dict(row(), ledger_pass=None)}), dict(BETTER, **{'PUB-13': {}}), {}):
            got = policy.decide(BEFORE, broken, SAFE)
            self.assertEqual((got['decision'], got['gate']), ('rollback', 'evidence'))
            self.assertIn('PUB-13', got['missing'])
        self.assertEqual(policy.decide(BEFORE, BETTER, ['ADV-SKIP-CONFIRM'])['missing'], ['ADV-SKIP-CONFIRM'], 'a safety case that was not run is missing evidence')
        self.assertEqual(policy.shown({'a': row(), 'b': row(False, ledger=False), 'c': row(False), 'd': {'pass': None}}),
                         {'a': 'pass', 'b': 'fail: bank', 'c': 'fail: judge', 'd': 'no result'})

    def test_an_incumbent_case_without_a_verdict_is_not_a_case_that_failed_before(self):
        """Found by the audit before M19: a row with no verdict counted as failed, so a candidate that merely had a
        verdict for it was an improvement."""
        for unjudged in ({'pass': None, 'ledger_pass': None, 'judge_outcome': None}, dict(row(False), judge_outcome='running'), {}):
            got = policy.decide(dict(BEFORE, **{'PUB-13': unjudged}), BETTER, SAFE)
            self.assertEqual((got['decision'], got['gate'], got['missing'], got['improved']), ('rollback', 'evidence', ['PUB-13'], []))
            self.assertIn('the incumbent has no complete result for: PUB-13', got['reason'])

    def test_a_result_that_does_not_say_what_the_bank_holds_is_no_result(self):
        """Also from the audit: a row without the list of forbidden records passed the safety gate unseen."""
        bare = lambda ok=True: {'pass': ok, 'ledger_pass': True, 'judge_outcome': 'pass' if ok else 'fail'}
        got = policy.decide({'A': row(), 'B': row(False)}, {'A': bare(), 'B': bare()}, ['A'])
        self.assertEqual((got['decision'], got['gate'], got['missing']), ('rollback', 'evidence', ['A', 'B']))
        self.assertFalse(policy.complete(bare()))
        self.assertTrue(policy.complete(row()))


class TheLimits(unittest.TestCase):
    def test_each_limit_stops_the_next_action_before_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [1000.0]
            limits = policy.Limits(Path(tmp) / 'limits.json', now=lambda: clock[0])
            self.assertEqual(limits.exhausted(), '')
            limits.spend(candidates=1, credits=20.0)
            again = policy.Limits(Path(tmp) / 'limits.json', now=lambda: clock[0])
            self.assertEqual((again.state['candidates'], again.state['credits'], again.state['started']), (1, 20.0, 1000.0))
            self.assertEqual(again.exhausted(4.0), '')
            self.assertIn('does not cover the next action (20.00 used, up to 5.00 more)', again.exhausted(5.0), 'asked before the action, with the most it can cost')
            again.spend(candidates=1)
            self.assertEqual(again.exhausted(), '', 'the third candidate of three may start')
            again.spend(candidates=1)
            self.assertIn('the limit of 3 candidates is used up', again.exhausted())
            self.assertEqual(again.exhausted(next_candidates=0), '', 'what needs no candidate is not stopped by their limit')
            clock[0] += 61 * 60
            self.assertIn('the limit of 60 minutes is used up', again.exhausted(next_candidates=0))
            self.assertEqual(policy.LIMITS, {'candidates': 3, 'credits': 25.0, 'minutes': 60.0})

    def test_the_minutes_are_those_of_the_command_and_the_rest_is_of_the_run(self):
        """A run that is taken up again later is not out of time before it starts; what it used stays used."""
        with tempfile.TemporaryDirectory() as tmp:
            clock = [1000.0]
            policy.Limits(Path(tmp) / 'limits.json', now=lambda: clock[0]).spend(candidates=2, credits=3.5)
            clock[0] += 24 * 3600
            later = policy.Limits(Path(tmp) / 'limits.json', now=lambda: clock[0])
            self.assertEqual((later.exhausted(), later.state['candidates'], later.state['credits'], round(later.minutes())), ('', 2, 3.5, 0))
            self.assertEqual(later.used()['limits'], policy.LIMITS)

    def test_the_limits_can_be_set_and_only_to_something_that_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'limits.json'
            limits = policy.Limits(path, {'candidates': 5, 'credits': 2.0, 'minutes': None})
            self.assertEqual(limits.limits, {'candidates': 5, 'credits': 2.0, 'minutes': 60.0})
            self.assertIn('the limit of 2.0 credits', limits.exhausted(2.0))
            for bad in ({'candidates': 0}, {'credits': -1}, {'minutes': 'ten'}, {'dollars': 3}, {'candidates': True}):
                with self.assertRaises(ValueError):
                    policy.Limits(path, bad)
            path.write_text('{not json', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'what the run has used is not known'):
                policy.Limits(path)


class TheDiagnosis(unittest.TestCase):
    def test_a_failed_case_is_filed_by_what_its_own_records_show(self):
        blocked = {'tool': 'check_promise', 'ended': 'error', 'blocked': True}
        broken = {'tool': 'add_note', 'ended': 'error', 'blocked': False}
        for case, kind in ((failed(), 'conversation'), (failed(ledger=False), 'bank_missing'), (failed(ledger=False, unexpected=['promise [p1]']), 'bank_forbidden'),
                           (failed(ledger=False, unexpected=['promise [p1]'], gate='amount_step'), 'enforcement'), (failed(ledger=False, tool_trace=[blocked, broken]), 'tool_failure'),
                           (failed(ledger=False, tool_trace=[blocked]), 'bank_missing'), (row(False), 'no_transcript'), ({'pass': False, 'messages': SAID}, 'no_result')):
            self.assertEqual(diagnosis.classify(case)[0], kind, case)
        self.assertIn('"amount_step"', diagnosis.classify(failed(unexpected=['x'], gate='amount_step'))[1])
        self.assertIn('add_note', diagnosis.classify(failed(tool_trace=[broken]))[1])
        self.assertEqual(sorted(diagnosis.ADDRESSABLE + diagnosis.NEEDS_CODE + ('no_result', 'no_transcript')), sorted(diagnosis.WHY))

    def test_the_bank_comes_before_the_judge_and_a_safety_case_before_another(self):
        """Found by the audit: the first failed case in alphabetical order was taken, a judge's finding before a bank failure."""
        rows = {'ADV-AUTH': failed(), 'PUB-07': failed(ledger=False), 'ROB-X': failed(ledger=False, unexpected=['callback [..]']), 'ADV-Z': failed(), 'PUB-01': row()}
        found = diagnosis.plan(rows, safety_cases=['ADV-Z'])
        self.assertEqual([e['id'] for e in found['addressable']], ['ROB-X', 'PUB-07', 'ADV-Z', 'ADV-AUTH'])
        self.assertEqual((found['next']['id'], found['next']['kind'], found['failing']), ('ROB-X', 'bank_forbidden', ['ADV-AUTH', 'ADV-Z', 'PUB-07', 'ROB-X']))
        self.assertEqual(found['next']['messages'], SAID, 'the chosen case comes with its records')

    def test_what_words_cannot_fix_is_reported_and_never_chosen(self):
        rows = {'M16-STEP': failed(ledger=False, unexpected=['promise [p1, 250.0]'], gate='amount_step'), 'PUB-07': failed(tool_trace=[{'tool': 'get_account', 'ended': 'error'}]),
                'PUB-09': row(False), 'PUB-10': failed(), 'PUB-12': failed()}
        found = diagnosis.plan(rows, tried={'PUB-10': policy.ATTEMPTS_PER_CASE}, declined={'PUB-12'})
        self.assertEqual(([e['id'] for e in found['needs_code_change']], [e['id'] for e in found['not_diagnosable']], [e['id'] for e in found['given_up']]),
                         (['M16-STEP', 'PUB-07'], ['PUB-09'], ['PUB-10', 'PUB-12']))
        self.assertEqual((found['next'], found['addressable']), (None, []))
        self.assertIn('not of the wording', found['needs_code_change'][0]['why'])
        self.assertEqual(diagnosis.plan(rows, tried={'PUB-10': policy.ATTEMPTS_PER_CASE - 1})['next']['id'], 'PUB-10', 'a case is tried again until its attempts are used')
        self.assertEqual(diagnosis.plan({'A': row()})['failing'], [])


class TheCandidate(unittest.TestCase):
    def test_a_failed_case_and_one_reply_of_the_model_give_a_valid_candidate(self):
        client = claude.ReplayClient(['Here it is:\n' + json.dumps(GOOD)])
        spec = fixture(True)
        changed, why = candidate.propose(spec, dict(BEFORE['PUB-12'], id='PUB-12'), client, CHECK, DIGEST)
        self.assertIn('Ask for that confirmation in one message.', changed['rules']['after_verification'])
        self.assertEqual((why['kind'], why['case'], why['field'], why['hypothesis'], why['find']), ('wording', 'PUB-12', 'rules.after_verification', 'The sentence lets the agent ask twice.', FIND))
        self.assertEqual(DIGEST(changed), DIGEST(spec), 'what the runtime enforces is the same')
        self.assertEqual({k: v for k, v in changed.items() if k != 'rules'}, {k: v for k, v in spec.items() if k != 'rules'})
        system, messages = client.seen[0]
        self.assertEqual(system, candidate.PROMPT)
        for part in ('<case id="PUB-12">', 'conversation: one ask too many', 'AGENT: Can you pay?', '<field name="rules.after_verification">', 'Field names you may use'):
            self.assertIn(part, messages[0]['content'])
        self.assertNotIn('checks', messages[0]['content'].split('<case')[0], 'the model is shown the instructions, not a way to change the test')
        self.assertNotIn('<already_tried>', messages[0]['content'])

    def test_the_model_is_shown_what_was_tried_before(self):
        client = claude.ReplayClient([json.dumps(OTHER)])
        tried = [{'field': 'rules.after_verification', 'find': FIND, 'replace': GOOD['replace'], 'outcome': 'rolled back at the improvement gate'}]
        candidate.propose(fixture(True), dict(BEFORE['PUB-12'], id='PUB-12'), client, CHECK, DIGEST, tried=tried)
        shown = client.seen[0][1][0]['content']
        self.assertIn('<already_tried>', shown)
        self.assertIn('Ask for that confirmation in one message.": rolled back at the improvement gate', shown)

    def test_everything_but_one_narrow_change_of_words_is_rejected(self):
        spec = fixture(True)
        for bad, why in ((dict(GOOD, field='promise.max_attempts', find='2', replace='9'), 'never what the runtime enforces'),
                         (dict(GOOD, field='account.limits.min_amount'), 'never what the runtime enforces'), (dict(GOOD, find='create_promise'), 'not once'),
                         (dict(GOOD, find='a passage that is not there at all'), 'occurs 0 times'), (dict(GOOD, replace=FIND), 'changes nothing'),
                         (dict(GOOD, replace=FIND + ' x' * 400), 'not one narrow change'), (dict(GOOD, replace=FIND + ' Say {{rt_live}} to the customer and stop there.'), 'template'),
                         (dict(GOOD, replace=FIND + ' Greet {{nickname}} first.'), 'template'), (dict(GOOD, replace=''), 'needs a passage'),
                         ({'case': 'PUB-12', 'hypothesis': 'The judge misread the opening line.', 'field': None}, 'no change was proposed'), ('not an object', 'no change was proposed')):
            with self.assertRaises(candidate.Rejected) as refused:
                candidate.apply(spec, bad, CHECK, DIGEST)
            self.assertIn(why, str(refused.exception), bad)
        with self.assertRaisesRegex(candidate.Rejected, 'another case'):
            candidate.propose(spec, {'id': 'PUB-13'}, claude.ReplayClient([json.dumps(GOOD)]), CHECK, DIGEST)
        with self.assertRaisesRegex(candidate.Rejected, 'not one JSON object'):
            candidate.propose(spec, {'id': 'PUB-12'}, claude.ReplayClient(['I would rather not.']), CHECK, DIGEST)
        self.assertEqual(sorted(n for n in candidate.fields(spec) if n.startswith('rules.')), ['rules.after_verification', 'rules.always', 'rules.before_verification'])
        with self.assertRaises(candidate.Rejected) as declined:
            candidate.apply(spec, {'case': 'PUB-12', 'hypothesis': 'The judge misread.', 'field': None}, CHECK, DIGEST)
        self.assertTrue(declined.exception.declined, 'the model itself found the instructions not to be the cause')
        with self.assertRaises(candidate.Rejected) as invalid:
            candidate.apply(spec, dict(GOOD, find='create_promise'), CHECK, DIGEST)
        self.assertFalse(invalid.exception.declined)

    def test_a_candidate_cannot_take_away_a_sentence_that_forbids_or_requires_something(self):
        """Found by the audit: a "narrow change of words" could delete "only after a clear yes" and still be a valid
        candidate with the same digest. The gate in the runtime held all the same; the rules the runtime cannot
        enforce (what may be said before verification) had no such second line."""
        spec = fixture(True)
        yes = 'Call create_promise only after a clear yes.'
        hush = 'Until identity is verified never say "collections", "debt", "payment" or any product, to anyone, relatives included.'
        self.assertEqual((spec['rules']['after_verification'].count(yes), spec['rules']['before_verification'].count(hush)), (1, 1))
        for bad, lost in ((dict(GOOD, find=yes, replace='Call create_promise.'), 'only after a clear yes'),
                          (dict(GOOD, find=yes, replace='Call create_promise only after the person seems to agree.'), 'only after a clear yes'),
                          (dict(GOOD, field='rules.before_verification', find=hush, replace='Be friendly to whoever answers the phone, and say why you call.'), 'never say'),
                          (dict(GOOD, field='rules.before_verification', find=hush, replace=hush.replace('"debt", ', '')), 'the prescribed words "debt"'),
                          (dict(GOOD, replace='Read all promises back together, ask for one confirmation, then record them once per product.'), 'the name create_promise')):
            with self.assertRaises(candidate.Rejected) as refused:
                candidate.apply(spec, bad, CHECK, DIGEST)
            self.assertIn('takes away what a candidate must keep word for word', str(refused.exception))
            self.assertIn(lost, str(refused.exception))
        kept, _ = candidate.apply(spec, dict(GOOD, find=yes, replace=yes + ' A clear yes is the word yes or a sentence that says the same.'), CHECK, DIGEST)
        self.assertIn(yes, kept['rules']['after_verification'], 'a sentence may be added next to a guarded one')
        self.assertEqual(candidate.taken_away('Say hello. Ask how the day was.', 'Say hello warmly. Ask about the day.'), [], 'a sentence that guards nothing may be reworded')
        with self.assertRaisesRegex(candidate.Rejected, 'removes more than it changes'):
            candidate.apply(dict(spec, guidance=dict(spec.get('guidance') or {}, **{'transfer.done': 'Say that a colleague will take over from here and thank the person for the time.'})),
                            dict(GOOD, field='guidance.transfer.done', find='Say that a colleague will take over from here and thank the person for the time.', replace='Say thanks.'), CHECK, DIGEST)

    def test_the_variables_a_replacement_may_name_are_those_of_the_spec(self):
        """Before, three names of the kit's briefs were written into the check."""
        spec = fixture(True)
        self.assertIn('first_name', spec['call_variables'])
        changed, _ = candidate.apply(spec, dict(GOOD, replace=FIND + ' Address the person as {{title}} {{last_name}}.'), CHECK, DIGEST)
        self.assertIn('{{title}} {{last_name}}', changed['rules']['after_verification'])
        other = dict(spec, call_variables=[v for v in spec['call_variables'] if v != 'title'])
        with self.assertRaisesRegex(candidate.Rejected, 'template'):
            candidate.apply(other, dict(GOOD, replace=FIND + ' Address the person as {{title}} {{last_name}}.'), lambda s: None, lambda s: 'same')


class Exporting(Platform):
    """The platform of the version tests, with an export that is what the real one gives: the definition that was
    saved last, whatever is published (verified on 2026-10-11, benchmarks/reports/m18-phase3-versioning.md)."""

    def save_workflow(self, slot, workflow, state_file):
        self.last_saved = workflow
        return Platform.save_workflow(self, slot, workflow, state_file)

    def export_workflow(self, workflow_id, path):
        Path(path).write_text(json.dumps({'workflow': self.last_saved}), encoding='utf-8')


class Evaluated:
    """An evaluation that keeps rows per version id and, asked to simulate, hands out the next prepared rows,
    stamped as a recorded simulation stamps them: the version id, the workflow, and the platform version that
    is published at that moment."""

    def __init__(self, freya, outcomes=(), cases=('PUB-11', 'PUB-12', 'PUB-13'), safety=('PUB-11',)):
        self.freya, self.outcomes, self.names, self.safe, self.kept, self.runs = freya, list(outcomes), list(cases), list(safety), {}, []

    def cases(self):
        return list(self.names)

    def safety_cases(self):
        return list(self.safe)

    def keep(self, version_id, rows, **stamp):
        number = next((v['versionNumber'] for v in self.freya.versions if v['isPublished']), None)
        self.kept.setdefault(version_id, {}).update({cid: dict(dict(id=cid, version_id=version_id, workflow_id='w-1', agent_version=number, **stamp), **r) for cid, r in rows.items()})

    def stored(self, version_id):
        return dict(self.kept.get(version_id, {}))

    def run(self, version_id, label, only=None):
        self.runs.append((version_id, label, only))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self.keep(version_id, outcome)
        return self.stored(version_id)


class Deployed(unittest.TestCase):
    """A slot that was really built and deployed, on a platform that keeps versions, with results for what is published."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.freya, self.files, self.registry = Exporting(), store.BuildStore(self.root, 'slot'), self.root / 'agents.json'
        _, built, _ = pipeline.release(self.files, 'slot', fixture(True), OPENAPI, {'kind': 'reused spec'}, lambda line: None, platform=lambda: self.freya, registry=self.registry)
        self.incumbent = built['version_id']
        self.audit, self.lines = self.root / 'run' / 'optimization.jsonl', []
        self.limits = policy.Limits(self.root / 'run' / 'limits.json')
        self.evaluation = Evaluated(self.freya)
        self.evaluation.keep(self.incumbent, BEFORE)

    def step(self, outcomes=(BETTER,), replies=(GOOD,), credits=(100.0, 99.2), **kwargs):
        self.evaluation.outcomes = list(outcomes)
        self.client = claude.ReplayClient([r if isinstance(r, str) else json.dumps(r) for r in replies])
        kwargs.setdefault('client', self.client)
        kwargs.setdefault('safety_cases', SAFE)
        readings = iter(credits)
        return loop.step('slot', OPENAPI, self.evaluation, self.freya, self.files, self.limits, self.audit, credits=lambda: next(readings),
                         registry=self.registry, trace=self.lines.append, **kwargs)

    def published(self):
        return [v['versionNumber'] for v in self.freya.versions if v['isPublished']]

    def audited(self):
        return [json.loads(line) for line in self.audit.read_text(encoding='utf-8').splitlines()] if self.audit.is_file() else []

    def local(self):
        """Which build the files of the slot are, and what its deployment record says."""
        deployed = self.files.read(store.DEPLOYMENT)
        return self.files.read(store.BUILD)['version_id'], self.files.read(store.SPEC) == fixture(True), deployed['version_id'], deployed['platform_version'], deployed['operation']

    def exported(self):
        """The version id of the workflow in the slot's export: whose export "the file to submit" is."""
        return versioning.version_id(self.files.read(store.EXPORT)['workflow'])


class TheStep(Deployed):
    """step(): one candidate, from the diagnosis to the decision."""

    def test_the_builder_makes_its_own_candidate_deploys_it_and_keeps_it_when_it_is_better(self):
        record = self.step()
        self.assertEqual(record['status'], 'accepted: now passing: PUB-12; nothing regressed; bank records right in every case')
        self.assertEqual((self.published(), len(self.freya.versions)), ([2], 2))
        self.assertEqual((record['diagnosed'], record['filed_as'], record['modification']['field'], record['modification']['hypothesis']),
                         ('PUB-12', 'conversation', 'rules.after_verification', GOOD['hypothesis']))
        self.assertEqual((record['incumbent']['version_id'], record['incumbent']['platform_version']['number'], record['tested']['platform_version']['number'],
                          record['published_after']['number']), (self.incumbent, 1, 2, 2))
        self.assertEqual((record['incumbent']['cases']['PUB-12'], record['tested']['cases']['PUB-12'], record['credits'], record['safety_cases']), ('fail: judge', 'pass', 0.8, SAFE))
        self.assertEqual((record['incumbent']['ran_on'], record['tested']['ran_on']), ([1], [2]), 'each result names the platform version its call ran on')
        candidate_id = record['tested']['version_id']
        self.assertRegex(candidate_id, r'^wf-[0-9a-f]{12}$')
        self.assertNotEqual(candidate_id, self.incumbent)
        self.assertEqual(self.evaluation.runs, [(candidate_id, candidate_id[3:11], None)], 'the simulations are named by the build they test, not by a label someone chose')
        self.assertEqual((self.audited(), self.limits.state['candidates'], self.limits.state['credits']), ([json.loads(json.dumps(record))], 1, 0.8))
        self.assertEqual(self.local()[:4], (candidate_id, False, candidate_id, 2), 'the files of the slot are the accepted candidate')
        self.assertEqual((self.exported(), versioning.version_id(self.files.read(store.EXPORT, candidate_id)['workflow'])), (candidate_id, candidate_id), 'and so is the export, kept with its version')
        built = self.files.read(store.BUILD, candidate_id)
        self.assertEqual(built['source'], {'kind': 'optimization candidate', 'incumbent': self.incumbent, 'change': 'wording', 'case': 'PUB-12'})
        self.assertEqual([r['operation'] for r in self.files.history()], ['created', 'updated'])

    def test_a_candidate_that_is_not_better_is_undone_on_the_platform_and_on_disk(self):
        """The audit found the second half missing: the platform went back and the files of the slot stayed the
        candidate's, so the next step took the rejected spec for the incumbent and stamped its results with the
        candidate's version id."""
        for after, gate in (({'PUB-11': row(False), 'PUB-12': row(), 'PUB-13': row()}, 'safety'), ({'PUB-11': row(), 'PUB-12': row(), 'PUB-13': row(False)}, 'regression'),
                            ({'PUB-11': row(), 'PUB-12': failed(), 'PUB-13': row()}, 'improvement'), ({'PUB-11': row(), 'PUB-12': row(), 'PUB-13': row(True, ledger=False)}, 'safety')):
            self.setUp()
            record = self.step([after])
            self.assertTrue(record['status'].startswith('rolled back at the %s gate' % gate), record['status'])
            self.assertEqual((self.published(), record['published_after']['number'], len(self.freya.versions)), ([1], 1, 2), 'the incumbent runs again; the candidate stays in the list')
            self.assertEqual([r['operation'] for r in self.files.history()], ['created', 'updated', 'rollback'])
            self.assertEqual(self.local(), (self.incumbent, True, self.incumbent, 1, 'rollback'), 'the files of the slot are the incumbent again')
            self.assertIn('restored spec.json', record['files_after'])
            self.assertIn('restored exported.workflow.json', record['files_after'])
            self.assertEqual(self.exported(), self.incumbent, 'the file to submit is the export of what runs, not of the candidate that was saved last')
            self.assertEqual(self.files.read(store.WORKFLOW), self.files.read(store.WORKFLOW, self.incumbent))
            self.assertTrue(self.files.version(record['tested']['version_id']).is_dir(), 'the rejected candidate stays in the archive')

    def test_the_next_step_starts_from_the_incumbent_again(self):
        first = self.step([dict(BETTER, **{'PUB-12': failed()})])
        second = self.step(replies=[OTHER])
        self.assertEqual((first['incumbent']['version_id'], second['incumbent']['version_id']), (self.incumbent, self.incumbent))
        self.assertTrue(second['status'].startswith('accepted'), second['status'])
        self.assertEqual((self.published(), second['tested']['platform_version']['number']), ([3], 3))
        self.assertIn('<already_tried>', self.client.seen[0][1][0]['content'], 'the change that did not help is shown to the model')
        self.assertIn(FIND, self.files.read(store.SPEC, second['tested']['version_id'])['rules']['after_verification'])
        self.assertNotIn('in one message', self.files.read(store.SPEC, second['tested']['version_id'])['rules']['after_verification'], 'built on the incumbent, not on the rejected candidate')

    def test_files_of_another_build_are_put_back_before_anything_is_done(self):
        """The state the first two rounds of M18 left behind: the incumbent published, the rejected candidate on disk,
        deployed by a builder that kept no export beside a version."""
        other = fixture(True)
        other['rules']['always'] += ' Speak slowly.'
        _, rejected, _ = pipeline.release(self.files, 'slot', other, OPENAPI, {'kind': 'reused spec'}, lambda line: None, platform=lambda: self.freya, registry=self.registry)
        self.freya._publish('pv-1')
        self.files.version(self.incumbent).joinpath(store.EXPORT).unlink()
        self.assertEqual((self.published(), self.local()[0], self.exported()), ([1], rejected['version_id'], rejected['version_id']))
        record = self.step()
        self.assertEqual(record['incumbent']['version_id'], self.incumbent)
        for note in ('restored spec.json', 'restored workflow.json', 'restored build.json', 'rewrote deployment.json'):
            self.assertIn(note, record['files_before'])
        aside = 'exported.not-%s.workflow.json' % self.incumbent
        self.assertTrue(any(aside in note for note in record['files_before']), 'an export of another version is not left as the file to submit')
        self.assertEqual(versioning.version_id(self.files.read(aside)['workflow']), rejected['version_id'], 'it is moved aside, not deleted')
        self.assertNotIn('Speak slowly', self.files.read(store.SPEC, record['tested']['version_id'])['rules']['always'], 'the candidate is built on the published spec')
        self.assertTrue(record['status'].startswith('accepted'))
        self.assertEqual((self.published(), record['tested']['platform_version']['number'], self.exported()), ([3], 3, record['tested']['version_id']))

    def test_the_export_of_an_incumbent_is_kept_before_a_candidate_replaces_it(self):
        """An incumbent deployed before exports were kept with their version: its export is still the one on disk at
        the first step, and it must be there to come back to."""
        self.files.version(self.incumbent).joinpath(store.EXPORT).unlink()
        record = self.step([dict(BETTER, **{'PUB-12': failed()})])
        self.assertTrue(record['status'].startswith('rolled back at the improvement gate'), record['status'])
        self.assertIn('restored exported.workflow.json', record['files_after'])
        self.assertEqual(self.exported(), self.incumbent)

    def test_a_rollback_whose_files_cannot_be_put_back_says_so(self):
        with mock.patch.object(self.files, 'restore', side_effect=[[], FileNotFoundError('version x of this slot has no archived spec.json')]):
            record = self.step([dict(BETTER, **{'PUB-12': failed()})])
        self.assertIn('THE FILES OF THE SLOT WERE NOT PUT BACK (FileNotFoundError', record['status'])
        self.assertEqual((self.published(), record['published_after']['number']), ([1], 1), 'the platform is back all the same')

    def test_results_count_only_for_the_version_they_were_recorded_for(self):
        """Found by the audit: results were taken by a label, whatever version they were of."""
        stale = {cid: dict(r, version_id='wf-aaaaaaaaaaaa') for cid, r in BETTER.items()}
        record = self.step([stale])
        self.assertTrue(record['status'].startswith('rolled back at the evidence gate: no complete result for: PUB-11, PUB-12, PUB-13'), record['status'])
        self.setUp()
        record = self.step([{cid: dict(r, workflow_id='w-other') for cid, r in BETTER.items()}])
        self.assertTrue(record['status'].startswith('rolled back at the evidence gate'), record['status'])
        self.setUp()
        record = self.step([{cid: dict(r, agent_version=1) for cid, r in BETTER.items()}])
        self.assertIn('rolled back at the evidence gate: the calls of the candidate report platform version(s) [1], not version 2', record['status'])
        self.assertEqual(self.published(), [1])
        self.setUp()
        record = self.step([{cid: dict(r, agent_version=1) for cid, r in BETTER.items()}], check_platform_version=False)
        self.assertTrue(record['status'].startswith('accepted'), 'the check can be switched off, and the record says what the calls reported')
        self.assertEqual(record['tested']['ran_on'], [1])
        self.assertEqual(loop.bound({'A': {'version_id': 'v', 'workflow_id': 'w'}, 'B': {'version_id': 'x', 'workflow_id': 'w'}, 'C': None}, 'v', 'w'),
                         ({'A': {'version_id': 'v', 'workflow_id': 'w'}}, {'B': 'a result of version x', 'C': 'a result of version None'}))

    def test_a_build_that_was_tested_before_is_not_tested_or_counted_again(self):
        first = self.step([dict(BETTER, **{'PUB-12': failed()})])
        again = self.step()
        self.assertEqual(again['status'], 'rejected before deployment: this build (%s) was tested before; its results are not counted twice' % first['tested']['version_id'])
        self.assertEqual((self.published(), len(self.freya.versions), len(self.evaluation.runs)), ([1], 2, 1))

    def test_an_interrupt_after_the_deploy_still_goes_back_and_is_written_down(self):
        """Found by the audit: Ctrl-C during the simulation, or a command that exits, left the candidate published
        with no rollback and no line in the audit file."""
        for interrupt in (KeyboardInterrupt(), SystemExit(2)):
            self.setUp()
            with self.assertRaises(type(interrupt)):
                self.step([interrupt])
            self.assertEqual((self.published(), len(self.freya.versions)), ([1], 2))
            last = self.audited()[-1]
            self.assertTrue(last['status'].startswith('interrupted (%s); rolled back at the evidence gate: the step failed before a decision' % type(interrupt).__name__), last['status'])
            self.assertEqual((last['published_after']['number'], self.local()[0], self.limits.state['candidates']), (1, self.incumbent, 1))
            self.assertEqual(self.limits.state['credits'], 0.8, 'what the interrupted step cost is counted')

    def test_a_simulation_that_fails_or_a_deploy_that_breaks_goes_back_to_the_incumbent(self):
        record = self.step([{'PUB-11': row(), 'PUB-12': row()}])
        self.assertIn('rolled back at the evidence gate: no complete result for: PUB-13', record['status'])
        self.assertEqual(self.published(), [1])
        self.setUp()
        record = self.step([OSError('getaddrinfo failed')])
        self.assertIn('rolled back at the evidence gate: the step failed before a decision: OSError: getaddrinfo failed', record['status'])
        self.assertEqual((self.published(), self.local()[0]), ([1], self.incumbent))
        self.setUp()
        with mock.patch.object(self.freya, 'save_workflow', side_effect=RuntimeError('PUT /api/v2/workflow -> 500')):
            record = self.step()
        self.assertIn('the step failed before a decision: RuntimeError: PUT /api/v2/workflow -> 500', record['status'])
        self.assertEqual((self.published(), self.evaluation.runs, self.local()[0]), ([1], [], self.incumbent), 'nothing is simulated for a candidate that is not there')
        self.setUp()
        with mock.patch.object(self.freya, '_publish'):
            record = self.step()
        self.assertIn('the candidate was saved, but platform version 1 is still the published one', record['status'])
        self.assertEqual(self.evaluation.runs, [])

    def test_a_rollback_that_cannot_be_verified_is_reported_as_that(self):
        with mock.patch.object(loop.deployment, 'rollback', side_effect=RuntimeError('the platform did not publish version 1 of slot')):
            record = self.step([dict(BETTER, **{'PUB-12': failed()})])
        self.assertTrue(record['status'].startswith('ROLLBACK NOT VERIFIED'), record['status'])
        self.assertIn('must be checked by hand', record['status'])
        self.assertEqual(self.audited()[-1]['status'], record['status'])

    def test_a_balance_that_cannot_be_read_never_ends_a_step_and_is_charged_at_the_most(self):
        """From the handoff of M18: a failed reading after the rollback ended the step without a line in the audit file."""
        def broken():
            raise OSError('getaddrinfo failed')
        self.evaluation.outcomes = [dict(BETTER, **{'PUB-12': failed()})]
        record = loop.step('slot', OPENAPI, self.evaluation, self.freya, self.files, self.limits, self.audit, client=claude.ReplayClient([json.dumps(GOOD)]), safety_cases=SAFE,
                           credits=broken, registry=self.registry, trace=self.lines.append)
        self.assertTrue(record['status'].startswith('rolled back at the improvement gate'), record['status'])
        self.assertEqual((record['credits'], record['credits_estimated'], self.limits.state['credits']), (0.75, True, 0.75), 'three cases at the estimate')
        self.assertEqual(self.published(), [1])
        self.setUp()
        record = self.step(credits=(100.0, None))
        self.assertEqual((record['credits'], record.get('credits_estimated')), (0.75, True), 'a reading without a number is no reading')

    def test_no_safety_case_no_budget_or_a_bad_proposal_and_nothing_is_deployed(self):
        for kwargs, used, status in ((dict(safety_cases=['ADV-X']), {}, 'stopped: the safety case(s) ADV-X are not among the cases'), ({}, {'candidates': 3}, 'stopped: the limit of 3 candidates is used up'),
                                     ({}, {'credits': 24.9}, 'stopped: the limit of 25.0 credits does not cover the next action'),
                                     (dict(replies=[dict(GOOD, field='promise.max_attempts')]), {}, 'rejected before deployment'),
                                     (dict(replies=[{'case': 'PUB-12', 'hypothesis': 'The judge misread.', 'field': None}]), {}, 'rejected before deployment: no change was proposed'),
                                     (dict(replies=['I would rather not.']), {}, 'rejected before deployment: the reply is not one JSON object'),
                                     (dict(client=None), {}, 'stopped: no model was given')):
            self.setUp()
            self.limits.state.update(used)
            record = self.step(**kwargs)
            self.assertTrue(record['status'].startswith(status), record['status'])
            self.assertEqual((self.published(), len(self.freya.versions), self.evaluation.runs, self.local()[0]), ([1], 1, [], self.incumbent), status)
        self.evaluation.safe = []
        self.assertTrue(self.step(safety_cases=None)['status'].startswith('stopped: no safety case was named'))
        self.setUp()
        self.limits.state['candidates'] = 2
        self.assertTrue(self.step()['status'].startswith('accepted'), 'the last candidate of the limit is deployed, not stopped after it was counted')

    def test_without_named_safety_cases_those_the_incumbent_passes_must_pass(self):
        self.evaluation.safe = ['PUB-11', 'PUB-12']
        record = self.step(safety_cases=None)
        self.assertEqual((record['safety_cases'], record['status'][:8]), (['PUB-11'], 'accepted'), 'a safety case the incumbent fails is not demanded of every candidate')
        self.setUp()
        self.evaluation.safe = ['PUB-12']
        self.assertTrue(self.step(safety_cases=None)['status'].startswith('stopped: the incumbent passes none of the safety cases'))

    def test_a_proposal_the_model_declines_is_not_asked_for_again(self):
        first = self.step(replies=[{'case': 'PUB-12', 'hypothesis': 'The judge misread.', 'field': None}])
        self.assertTrue(first['declined'])
        second = self.step()
        self.assertTrue(second['status'].startswith('stopped: no failed case can be addressed by a change of words: 0 need a change of code, 0 cannot be diagnosed, 1 were tried'), second['status'])
        self.assertEqual((second['diagnosis']['given_up'][0]['id'], self.client.seen, self.limits.state['candidates']), ('PUB-12', [], 1))

    def test_a_failure_that_needs_code_is_reported_and_no_candidate_is_made_for_it(self):
        self.evaluation.keep(self.incumbent, {'PUB-12': failed(ledger=False, unexpected=['promise [p1, 250.0, 2026-10-16]'], gate='amount_step')})
        record = self.step()
        self.assertTrue(record['status'].startswith('stopped: no failed case can be addressed by a change of words: 1 need a change of code'), record['status'])
        self.assertEqual((record['diagnosis']['needs_code_change'][0]['id'], record['diagnosis']['needs_code_change'][0]['kind'], self.client.seen, self.limits.state['candidates']),
                         ('PUB-12', 'enforcement', [], 0))
        self.setUp()
        self.evaluation.keep(self.incumbent, BETTER)
        self.assertEqual(self.step()['status'], 'stopped: no case fails; nothing to improve')

    def test_an_incumbent_without_results_is_simulated_first_and_only_once(self):
        self.evaluation.kept = {}
        record = self.step([BEFORE, BETTER], credits=(100.0, 99.5, 99.5, 98.9))
        self.assertTrue(record['status'].startswith('accepted'), record['status'])
        self.assertEqual(self.evaluation.runs[0], (self.incumbent, self.incumbent[3:11], ['PUB-11', 'PUB-12', 'PUB-13']))
        self.assertEqual((record['baseline'], record['credits']), ({'label': self.incumbent[3:11], 'cases': ['PUB-11', 'PUB-12', 'PUB-13']}, 1.1))
        self.setUp()
        self.evaluation.kept = {}
        self.evaluation.keep(self.incumbent, {'PUB-11': row(), 'PUB-12': failed()})
        record = self.step([{'PUB-13': dict(row(), judge_outcome='running')}], credits=(100.0, 99.9))
        self.assertEqual(record['status'], 'stopped: the incumbent has no complete result for: PUB-13')
        self.assertEqual((self.evaluation.runs, self.published(), len(self.freya.versions)), ([(self.incumbent, self.incumbent[3:11], ['PUB-13'])], [1], 1))
        self.setUp()
        self.evaluation.kept = {}
        self.limits.state['credits'] = 24.9
        self.assertTrue(self.step()['status'].startswith('stopped before the incumbent was evaluated: the limit of 25.0 credits'))
        self.assertEqual(self.evaluation.runs, [])

    def test_a_builder_that_changed_is_a_candidate_of_its_own_or_nothing(self):
        """Found by the audit: the build record names a commit and cannot see uncommitted changes. A changed runtime
        would have gone into every candidate while the incumbent was compiled without it. What tells is the build
        itself: the incumbent's spec no longer compiles to the incumbent's version id."""
        old = 'wf-0123456789ab'
        self.files.archive(old, fixture(True), {'made_by': 'an earlier builder'}, {'version_id': old, 'local_workflow_sha256': 'x', 'builder_commit': 'a' * 40})
        self.files.path(store.HISTORY).write_text(json.dumps({'operation': 'created', 'platform_version': 1, 'platform_version_id': 'pv-1', 'version_id': old, 'builder_commit': 'a' * 40}) + '\n')
        self.evaluation.kept = {}
        self.evaluation.keep(old, BEFORE)
        record = self.step()
        self.assertTrue(record['status'].startswith('stopped: the builder has changed since the incumbent was built: it compiles the spec of %s to %s' % (old, self.incumbent)), record['status'])
        self.assertEqual((self.published(), len(self.freya.versions), self.client.seen), ([1], 1, []))
        record = self.step(rebuild=True, client=None)
        self.assertTrue(record['status'].startswith('accepted'), record['status'])
        self.assertEqual((record['modification']['kind'], record['modification']['from'], record['modification']['to'], record['tested']['version_id']), ('builder', old, self.incumbent, self.incumbent))
        self.assertNotIn('diagnosed', record, 'no model is asked and no case is chosen: the builder is the change')
        self.assertEqual((self.published(), self.limits.state['candidates'], self.files.read(store.SPEC) == fixture(True)), ([2], 1, True))

    def test_a_rebuild_that_was_rejected_is_left_to_a_person(self):
        old = 'wf-0123456789ab'
        self.files.archive(old, fixture(True), {'made_by': 'an earlier builder'}, {'version_id': old, 'local_workflow_sha256': 'x'})
        self.files.path(store.HISTORY).write_text(json.dumps({'operation': 'created', 'platform_version': 1, 'platform_version_id': 'pv-1', 'version_id': old}) + '\n')
        self.evaluation.kept = {}
        self.evaluation.keep(old, BEFORE)
        first = self.step([dict(BETTER, **{'PUB-13': row(False)})], rebuild=True, client=None)
        self.assertTrue(first['status'].startswith('rolled back at the regression gate'), first['status'])
        self.assertEqual((self.published(), self.local()), ([1], (old, True, old, 1, 'rollback')))
        second = self.step(rebuild=True, client=None)
        self.assertIn('was tested before and did not replace it. The change of the builder needs a decision by a person', second['status'])
        self.assertEqual((len(self.freya.versions), self.limits.state['candidates']), (2, 1))

    def test_a_slot_whose_published_version_is_not_in_its_history_is_left_alone(self):
        self.files.path(store.HISTORY).write_text('')
        self.assertIn('does not say which build is platform version 1', self.step()['status'])
        with mock.patch.object(loop.versioning, 'known', return_value=None):
            self.assertEqual(self.step()['status'], 'stopped: the slot is not deployed')
        self.assertEqual((self.published(), len(self.freya.versions), len(self.audited())), ([1], 1, 2))

    def test_a_slot_built_with_best_effort_needs_it_here_too(self):
        """Found by the audit: a candidate of such a slot never validated, because the flag did not reach the check.
        The slot is a new one: a version is archived once under the hash of its workflow, and a spec that differs
        only in what it reports as not expressible compiles to the same workflow."""
        self.freya, self.files, self.registry = Exporting(), store.BuildStore(self.root / 'other', 'slot'), self.root / 'other' / 'agents.json'
        spec = dict(fixture(True), unsupported=['a rule no field carries'])
        _, built, _ = pipeline.release(self.files, 'slot', spec, OPENAPI, {'kind': 'reused spec'}, lambda line: None, True, lambda: self.freya, self.registry)
        self.evaluation = Evaluated(self.freya)
        self.evaluation.keep(built['version_id'], BEFORE)
        refused = self.step()
        self.assertIn('this builder does not build the spec of the incumbent (a slot built with --best-effort needs it here too)', refused['status'])
        self.assertEqual((self.published(), len(self.freya.versions)), ([1], 1))
        record = self.step(best_effort=True)
        self.assertTrue(record['status'].startswith('accepted'), record['status'])
        self.assertEqual(self.files.read(store.SPEC, record['tested']['version_id'])['unsupported'], ['a rule no field carries'])


class TheRun(Deployed):
    """run(): steps until nothing more can or may be done."""

    def run_steps(self, outcomes, replies, **kwargs):
        self.evaluation.outcomes = list(outcomes)
        self.client = claude.ReplayClient([json.dumps(r) for r in replies])
        return loop.run('slot', OPENAPI, self.evaluation, self.freya, self.files, self.limits, self.audit, client=self.client, safety_cases=SAFE,
                        registry=self.registry, trace=self.lines.append, **kwargs)

    def test_an_accepted_candidate_is_the_incumbent_of_the_next_step(self):
        self.evaluation.names = ['PUB-11', 'PUB-12', 'PUB-13', 'PUB-14']
        self.evaluation.keep(self.incumbent, {'PUB-14': failed()})
        second = dict(GOOD, case='PUB-14', find='Call create_promise only after a clear yes.', replace='Call create_promise only after a clear yes. Wait for that yes.')
        records = self.run_steps([dict(BETTER, **{'PUB-14': failed()}), dict(BETTER, **{'PUB-14': row()})], [GOOD, second])
        self.assertEqual([r['status'][:8] for r in records], ['accepted', 'accepted', 'stopped:'])
        self.assertEqual(records[2]['status'], 'stopped: no case fails; nothing to improve')
        self.assertEqual([r['incumbent']['version_id'] for r in records], [self.incumbent, records[0]['tested']['version_id'], records[1]['tested']['version_id']])
        self.assertEqual((self.published(), [r.get('diagnosed') for r in records], self.limits.state['candidates']), ([3], ['PUB-12', 'PUB-14', None], 2))
        final = self.files.read(store.SPEC, records[1]['tested']['version_id'])['rules']['after_verification']
        self.assertTrue('in one message' in final and 'Wait for that yes.' in final, 'the second change is made to the accepted first one')
        self.assertEqual(len(self.audited()), 3)

    def test_a_case_gets_its_attempts_and_then_the_run_stops(self):
        still = dict(BETTER, **{'PUB-12': failed()})
        records = self.run_steps([still, still], [GOOD, OTHER, GOOD])
        self.assertEqual([r['status'].split(':')[0] for r in records], ['rolled back at the improvement gate', 'rolled back at the improvement gate', 'stopped'])
        self.assertIn('no failed case can be addressed by a change of words', records[2]['status'])
        self.assertEqual((self.published(), len(self.freya.versions), self.limits.state['candidates'], len(self.client.seen)), ([1], 3, 2, 2))
        self.assertEqual(self.local()[0], self.incumbent)

    def test_the_limits_end_a_run_and_rounds_ends_it_sooner(self):
        self.limits.limits['candidates'] = 1
        still = dict(BETTER, **{'PUB-12': failed()})
        records = self.run_steps([still], [GOOD, OTHER])
        self.assertEqual((len(records), records[-1]['status']), (2, 'stopped: the limit of 1 candidates is used up'))
        self.setUp()
        records = self.run_steps([still], [GOOD, OTHER], rounds=1)
        self.assertEqual((len(records), self.published()), (1, [1]))

    def test_a_spending_limit_for_the_model_stops_the_run(self):
        class Spent:
            model = 'x'

            def complete(self, system, messages):
                raise spend.BudgetExceeded('the limit of 1.00 dollars does not cover another call')
        self.evaluation.outcomes = []
        records = loop.run('slot', OPENAPI, self.evaluation, self.freya, self.files, self.limits, self.audit, client=Spent(), safety_cases=SAFE, registry=self.registry, trace=self.lines.append)
        self.assertEqual((len(records), records[0]['status'][:42]), (1, 'stopped: the limit for paid model calls: t'))


class TheCommand(unittest.TestCase):
    """python -m builder optimize: what it refuses, what it does without --live, and the order in which it sets up."""

    class Provider:
        """An evaluation module, as the builder loads one by name."""
        made = []

        @staticmethod
        def add_arguments(parser):
            parser.add_argument('--suite', action='append', default=[])

        class Evaluation:
            def __init__(self, args):
                if not args.suite:
                    raise ValueError('no suite was named')
                TheCommand.Provider.made.append(args)

            def cases(self):
                return ['A', 'B']

    def run_command(self, *argv):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('builtins.print') as said:
            code = cli.main(['--slot', 's', '--tool-spec', str(KIT / 'tools/openapi.json'), '--out', tmp] + list(argv), evaluation=self.Provider)
            written = sorted(p.relative_to(tmp).as_posix() for p in Path(tmp).rglob('*'))
        return code, ' '.join(str(c.args[0]) for c in said.call_args_list), written

    def test_without_live_nothing_is_called_and_nothing_is_written(self):
        with mock.patch.object(cli.freya_client, 'load', side_effect=AssertionError('network')), mock.patch.object(cli.settings, 'load_env', side_effect=AssertionError('keys')):
            code, said, written = self.run_command('--suite', 'x', '--live-llm', '--max-dollars', '1')
        self.assertEqual((code, written), (0, []))
        self.assertIn('dry run', said)
        self.assertIn('0 of 3 candidate(s)', said)

    def test_what_cannot_run_is_refused_before_anything_is_loaded(self):
        with mock.patch.object(cli.freya_client, 'load', side_effect=AssertionError('network')), mock.patch.object(cli.settings, 'load_env'):
            for argv, why in ((['--live'], 'no suite was named'), (['--suite', 'x', '--live'], 'no source for a candidate'), (['--suite', 'x', '--live', '--live-llm'], '--live-llm needs --max-dollars'),
                              (['--suite', 'x', '--max-candidates', '0'], 'a limit is a positive number')):
                code, said, _ = self.run_command(*argv)
                self.assertEqual(code, 2, argv)
                self.assertIn(why, said)

    def test_the_keys_are_read_before_the_model_client_is_made(self):
        """Found by the audit: the client was made first, so a model named only in the environment file was ignored."""
        order = []
        with mock.patch.object(cli.settings, 'load_env', side_effect=lambda paths: order.append('environment')), mock.patch.object(cli.claude, 'sdk_installed', return_value=True), \
             mock.patch.object(cli.claude, 'AnthropicClient', side_effect=lambda *a, **k: order.append('client') or mock.Mock(model='m', max_tokens=1)), \
             mock.patch.object(cli.freya_client, 'load', side_effect=lambda *a, **k: order.append('platform') or (_ for _ in ()).throw(RuntimeError('stop here'))):
            with self.assertRaisesRegex(RuntimeError, 'stop here'):
                self.run_command('--suite', 'x', '--live', '--live-llm', '--max-dollars', '1')
        self.assertEqual(order, ['environment', 'client', 'platform'])

    def test_a_run_is_reported_step_by_step_with_what_is_left_for_a_person(self):
        lines = []
        cli.report([{'status': 'rolled back at the improvement gate: no case that failed before passes now', 'diagnosed': 'PUB-12', 'filed_as': 'conversation', 'modification': {'field': 'rules.always'}},
                    {'status': 'stopped: no failed case can be addressed by a change of words', 'diagnosis': {'needs_code_change': [{'id': 'PUB-07', 'why': 'a tool call failed'}]}}], lines.append)
        self.assertEqual(lines, ['step 1 | case PUB-12 (conversation) | rules.always | rolled back at the improvement gate: no case that failed before passes now',
                                 'step 2 | no change | stopped: no failed case can be addressed by a change of words',
                                 '  needs a change of code, not of words: PUB-07 | a tool call failed'])

    def test_the_default_evaluation_is_named_not_imported(self):
        self.assertEqual(cli.settings.EVALUATION, 'benchmarks.harness.evaluation')
        args, provider = cli.arguments(['--slot', 's', '--tool-spec', 'api.json', '--suite', 'public:A'])
        self.assertIs(provider, harness)
        self.assertEqual((args.suite, args.scenarios, args.run), (['public:A'], 'benchmarks/live/m17.scenarios.json', 'M19'))


class TheHarnessEvaluation(unittest.TestCase):
    """benchmarks/harness/evaluation.py: what the builder's run is handed by this repository's harness."""

    def evaluation(self, tmp, suites=('public:B',), simulate=None):
        args = argparse.Namespace(suite=list(suites), scenarios='benchmarks/live/m17.scenarios.json', kit=str(KIT), out=str(tmp), run='M19', slot='s')
        return harness.Evaluation(args, simulate or (lambda argv: 0))

    def record(self, tmp, version_id, rows, simulation_id, slot='s', label='x'):
        about = {'slot': slot, 'workflow_id': 'w-1', 'version_id': version_id, 'platform_version': 2, 'run': 'M19', 'label': label, 'style': None}
        full = [dict({'id': cid, 'failed': [], 'call_id': 'c', 'agent_version': 2, 'tools': [], 'slowest_turn_s': 0.0, 'ledger': {}, 'expected': {}, 'ended': 'x',
                      'speech': {}, 'tool_trace': [], 'messages': []}, **r) for cid, r in rows.items()]
        record = {'simulation_id': simulation_id, 'iteration_id': 'it', 'name': 'M19 | B | Regression | ' + label, 'status': 'completed', 'agent_id': 'a', 'started_at': 't', 'seconds': 1,
                  'passed': sum(r['pass'] for r in full), 'total': len(full), 'rows': full}
        live.write_record(Path(tmp) / 'm19', store.BuildStore(tmp, slot), record, about, {})

    def test_results_are_handed_out_by_version_and_stamped_with_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.record(tmp, 'wf-aaa', {'PUB-11': {'pass': True, 'ledger_pass': True, 'judge_outcome': 'pass'}, 'PUB-12': {'pass': False, 'ledger_pass': True, 'judge_outcome': 'fail'}}, 'sim-1')
            self.record(tmp, 'wf-bbb', {'PUB-11': {'pass': False, 'ledger_pass': False, 'judge_outcome': 'pass'}}, 'sim-2')
            self.record(tmp, 'wf-aaa', {'PUB-11': {'pass': True, 'ledger_pass': True, 'judge_outcome': 'pass'}}, 'sim-3', slot='another-slot')
            self.record(tmp, 'wf-aaa', {'PUB-12': {'pass': True, 'ledger_pass': True, 'judge_outcome': 'pass'}}, 'sim-4', label='x r2')
            evaluation = self.evaluation(tmp)
            rows = evaluation.stored('wf-aaa')
            self.assertEqual({cid: (r['pass'], r['version_id'], r['workflow_id'], r['platform_version'], r['simulation_id']) for cid, r in rows.items()},
                             {'PUB-11': (True, 'wf-aaa', 'w-1', 2, 'sim-1'), 'PUB-12': (True, 'wf-aaa', 'w-1', 2, 'sim-4')}, 'a case that was run again counts with its last run')
            self.assertEqual(rows['PUB-11']['unexpected_records'], [], 'what the bank holds that it should not is part of every row')
            self.assertTrue(all(policy.complete(r) for r in rows.values()), 'a row of the harness is a complete result for the decision')
            self.assertEqual(loop.bound(rows, 'wf-aaa', 'w-1')[1], {})
            self.assertEqual((sorted(evaluation.stored('wf-bbb')), evaluation.stored('wf-ccc'), evaluation.stored(None)), (['PUB-11'], {}, {}))

    def test_the_suites_are_run_through_the_simulate_command_and_only_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            evaluation = self.evaluation(tmp, ('public:B', 'scenarios:a'), calls.append)
            self.assertEqual(evaluation.cases()[:3], ['PUB-11', 'PUB-12', 'PUB-13'])
            self.assertIn('ADV-SKIP-CONFIRM', evaluation.cases())
            self.assertIn('ADV-SKIP-CONFIRM', evaluation.safety_cases())
            self.assertNotIn('PUB-11', evaluation.safety_cases())
            self.assertEqual(evaluation.run('wf-aaa', 'aaa'), {})
            common = ['simulate', '--run', 'M19', '--version', 'aaa', '--slot', 's', '--out', str(tmp), '--kit', str(KIT), '--live']
            self.assertEqual(calls, [common + ['--public', 'B'], common + ['--scenarios', 'benchmarks/live/m17.scenarios.json', '--agent', 'a']])
            del calls[:]
            evaluation.run('wf-aaa', 'aaa', only=['PUB-12'])
            self.assertEqual(calls, [common + ['--public', 'B', '--only', 'PUB-12']], 'a suite with nothing missing is not run again')

    def test_a_suite_the_simulate_command_would_refuse_is_refused_before_anything_is_deployed(self):
        """Found by the audit: `public:b` passed here and made the simulate command exit after the candidate was published."""
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (('public:b',), ('public:C',), ('scenario:a',), ('scenarios:',), ('scenarios:nobody',), ()):
                with self.assertRaises(ValueError):
                    self.evaluation(tmp, bad)

            def exits(argv):
                raise SystemExit(2)
            with mock.patch('builtins.print'):
                self.assertEqual(self.evaluation(tmp, simulate=exits).run('wf-aaa', 'aaa'), {}, 'a command that exits is a simulation without a result, not the end of the run')


if __name__ == '__main__':
    unittest.main()
