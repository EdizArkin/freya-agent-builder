"""A brief without test customers (a hidden brief): the limits on model calls, the one fallback extraction, the
coverage audit, and the account of what a run checked.

Everything here is offline. The model answers from prepared replies or is a fake that hangs or fails, the
platform is the fake of the other run tests, and no customer is simulated unless a test says so: every "model
call", "audit" and "verdict" in a trace written by these tests is SYNTHETIC. They show what the run does with
what a model tells it, and nothing about what a model tells.
"""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness.fixture_spec import spec as fixture
from builder import run as builder_run
from builder.artifacts import store
from builder.config import settings
from builder.extraction import claude, coverage, spend
from builder.freya import versioning
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import digest, validate
from tests.support import KIT, OPENAPI
from tests.test_m18_loop import BEFORE, BETTER, GOOD
from tests.test_m19_run import Customers, Track

BRIEF_A, BRIEF_B, API_FILE = str(KIT / 'briefs' / 'brief-a.md'), str(KIT / 'briefs' / 'brief-b.md'), str(KIT / 'tools' / 'openapi.json')
TEXT_A, TEXT_B = Path(BRIEF_A).read_text(encoding='utf-8'), Path(BRIEF_B).read_text(encoding='utf-8')
# Sentences of the kit's briefs, word for word, and the lines of the fixture spec that carry them.
WRONG_ANSWERS = 'Three wrong answers in total end the check, whichever question they were on.'
CALLBACK = 'A callback is for today or the next business day, from 10:00 to 19:00, both included, and later than `now`.'
ONE_PRODUCT = 'Freya Bank calls customers who are 1 to 45 days late on one product.'
SEVERAL = '`get_account` returns one to three products. Each has its own `past_due`, `days_late` and floor.'
PER_PRODUCT = 'Persuasion attempts are counted per product.'
COVERED_ALL = [{'requirement': 'Three wrong answers end the security check.', 'kind': 'limit', 'source': WRONG_ANSWERS, 'status': 'COVERED', 'spec': 'identity.max_wrong: 3'},
               {'requirement': 'A callback is booked between 10:00 and 19:00.', 'kind': 'limit', 'source': CALLBACK, 'status': 'COVERED', 'spec': 'callback.end: 19:00'}]
OMITTED = [dict(COVERED_ALL[0]), {'requirement': 'A callback is booked up to 19:00.', 'kind': 'limit', 'source': CALLBACK, 'status': 'MISSING', 'note': 'The callback window of the spec ends earlier.'}]


def early_callback():
    """The fixture spec with a callback window that ends an hour early: 19:00 is nowhere in it."""
    spec = fixture(True)
    spec['callback']['end'] = '18:00'
    return spec


class Hanging:
    """A model that does not answer in the time of a test."""
    model = 'synthetic-hanging'

    def __init__(self):
        self.asked = 0

    def complete(self, system, messages):
        self.asked += 1
        time.sleep(30)


class FailsFirst(claude.ReplayClient):
    """A model whose first call fails; after that it answers from its replies."""

    def __init__(self, replies, error=None):
        claude.ReplayClient.__init__(self, replies, model='synthetic-model')
        self.error, self.failed = error or RuntimeError('SYNTHETIC 529 overloaded'), False

    def complete(self, system, messages):
        if not self.failed:
            self.failed = True
            self.seen.append((system, messages))
            raise self.error
        return claude.ReplayClient.complete(self, system, messages)


class TheLimitsOnModelCalls(unittest.TestCase):
    def test_a_call_that_does_not_come_back_is_left_behind_at_its_limit(self):
        started = time.time()
        with self.assertRaisesRegex(TimeoutError, 'no reply within 0 s; the run goes on without it .*may still be billed'):
            builder_run.within(0.3, time.sleep, 30)
        self.assertLess(time.time() - started, 5)
        self.assertEqual(builder_run.within(5, lambda a, b: a + b, 1, 2), 3, 'a call that answers in time is handed through')
        with self.assertRaisesRegex(ValueError, 'its own error'):
            builder_run.within(5, mock.Mock(side_effect=ValueError('its own error')))
        with self.assertRaises(KeyboardInterrupt):
            builder_run.within(5, mock.Mock(side_effect=KeyboardInterrupt()))

    def test_the_run_waits_for_a_call_no_longer_than_its_limit_and_tells_the_sdk(self):
        lines, sdk = [], claude.AnthropicClient('synthetic', timeout=900.0, retries=2)
        self.assertEqual((sdk.timeout, sdk.retries), (900.0, 2), 'without a run that sets them, the client is as it was')
        model = builder_run.Watched(Hanging(), lines.append, until=time.time() + 600, call_seconds=0.3, sdk=sdk)
        with self.assertRaises(TimeoutError):
            model.complete('system', [])
        self.assertEqual((sdk.timeout, sdk.retries, model.calls), (0.3, 1, 1), 'one attempt may take what the run waits, and the SDK tries again once at most')
        self.assertIn('LLM call failed | synthetic-hanging | TimeoutError: no reply within 0 s', lines[0])
        model = builder_run.Watched(Hanging(), lines.append, until=time.time() + 0.4, call_seconds=240, sdk=sdk)
        with mock.patch.object(builder_run, 'LEAST_SECONDS', 0):
            started = time.time()
            with self.assertRaises(TimeoutError):
                model.complete('system', [])
            self.assertLess(time.time() - started, 5, 'the time kept for the end of the run is a limit as well')
        self.assertLess(sdk.timeout, 0.5, 'the SDK is given what is left, not the 240 s of a call')

    def test_no_call_is_started_without_time_or_beyond_the_number_of_calls(self):
        lines, inner = [], claude.ReplayClient(['one', 'two', 'three'])
        model = builder_run.Watched(inner, lines.append, until=time.time() + 30, call_seconds=240)
        self.assertIn('are left for model calls before the time kept for the end of the run, and a call needs 45', model.refused())
        with self.assertRaises(spend.BudgetExceeded):
            model.complete('system', [])
        self.assertEqual((inner.seen, model.calls), ([], 0), 'the model was not asked')
        self.assertIn('LLM call not made | replay | ', lines[0])
        model = builder_run.Watched(inner, lines.append, until=time.time() + 600, call_seconds=240, max_calls=2)
        self.assertEqual((model.refused(), model.refused(601) != ''), ('', True), 'a stage that needs more time than is left is told so before it starts')
        model.held = 1
        self.assertEqual(model.complete('s', [])[0], 'one')
        self.assertIn('the limit of 2 model call(s) of this run is reached: 1 made, 1 kept for a later stage', model.refused())
        model.held = 0
        self.assertEqual(model.complete('s', [])[0], 'two')
        with self.assertRaisesRegex(spend.BudgetExceeded, 'the limit of 2 model call'):
            model.complete('s', [])
        self.assertEqual(len(inner.seen), 2)
        free = builder_run.Watched(inner, lines.append)
        self.assertEqual((free.refused(10 ** 6), free.complete('s', [])[0]), ('', 'three'), 'without limits a client is only watched')

    def test_an_audit_that_cannot_be_made_does_not_cost_the_valid_spec(self):
        """With `outcome`, the caller keeps the spec of the extraction and is told what became of the audit."""
        api, text = Api(OPENAPI), json.dumps(OPENAPI)
        briefs = [('brief-a.md', TEXT_A)]
        wrong = fixture(True)
        wrong['identity']['max_wrong'] = 4
        finding = [{'item': 'wrong answers that end the check', 'problem': 'x', 'evidence': WRONG_ANSWERS, 'fix': 3}]

        def audited(replies, max_calls=None):
            outcome, lines = {}, []
            model = builder_run.Watched(claude.ReplayClient([r if isinstance(r, str) else json.dumps(r) for r in replies]), lines.append, max_calls=max_calls)
            spec = claude.run('audit', briefs, text, model, lambda s: validate(s, api, True), lines.append, digest=lambda one, other=None: digest(one, api, other), outcome=outcome)
            return spec, outcome, lines
        self.assertEqual(audited([fixture(True), '[]'])[1], {'status': 'PASS', 'detail': 'no finding with evidence from the documents'})
        self.assertEqual(audited([fixture(True), 'It looks fine to me.'])[1]['status'], 'UNVERIFIED', 'a reply that cannot be read is no clean audit')
        spec, outcome, _ = audited([wrong, finding, fixture(True)])
        self.assertEqual((spec['identity']['max_wrong'], outcome['status']), (3, 'UNVERIFIED'))
        self.assertIn('the revision was not audited again', outcome['detail'])
        spec, outcome, _ = audited([wrong, finding, '{}'])
        self.assertEqual((spec, outcome['status']), (wrong, 'FAIL'))
        spec, outcome, lines = audited([fixture(True)], max_calls=1)
        self.assertEqual((spec, outcome['status']), (fixture(True), 'SKIP'))
        self.assertIn('the limit of 1 model call(s) of this run is reached', outcome['detail'])
        spec, outcome, _ = audited([wrong, finding], max_calls=2)
        self.assertEqual((spec, outcome['status']), (wrong, 'FAIL'), 'findings that could not be applied are reported as such')
        with self.assertRaises(spend.BudgetExceeded):       # without `outcome` nothing changes for the callers there were
            model = builder_run.Watched(claude.ReplayClient([json.dumps(fixture(True))]), lambda line: None, max_calls=1)
            claude.run('audit', briefs, text, model, lambda s: validate(s, api, True), digest=lambda one, other=None: digest(one, api, other))


class TheCoverageAudit(unittest.TestCase):
    """coverage.judge: what an entry of the model's reply is worth once code has checked its quotations."""

    A = 'Rule 1. The agent verifies identity with two questions before it says anything about the account.\nRule 2. A promise is at least 40 percent of the amount that is past due.\nRule 3. A callback is booked from 09:00 to 17:00 only.'
    B = 'This memo amends the first document. Everything in it applies unless a rule below replaces it.\nA callback is booked from 09:00 to 20:00 from now on.'
    SPEC = {'identity': {'questions': [{'field': 'date_of_birth'}, {'field': 'phone_last4'}], 'max_wrong': 3}, 'callback': {'start': '09:00', 'end': '20:00'},
            'rules': {'before_verification': 'Ask the two security questions before you say anything about the account.'}}
    IDENTITY = 'The agent verifies identity with two questions before it says anything about the account.'
    FLOOR = 'A promise is at least 40 percent of the amount that is past due.'
    OLD_WINDOW, NEW_WINDOW = 'A callback is booked from 09:00 to 17:00 only.', 'A callback is booked from 09:00 to 20:00 from now on.'

    def judged(self, item, briefs=None, spec=None, **more):
        return coverage.judge([item], briefs or [('a.md', self.A), ('b.md', self.B)], spec or self.SPEC, **more)[0]

    def test_the_spec_is_given_to_the_audit_as_lines_that_can_be_quoted(self):
        view = coverage.view(self.SPEC).splitlines()
        self.assertIn('identity.max_wrong: 3', view)
        self.assertIn('identity.questions[1].field: phone_last4', view)
        self.assertIn('rules.before_verification: Ask the two security questions before you say anything about the account.', view)
        self.assertIn('callback.end: 20:00', view)
        self.assertIn('calendar.business_weekdays: [0, 1, 2, 3, 4]', coverage.view(fixture(True)).splitlines(), 'a list of plain values is one line')

    def test_a_rule_of_the_first_document_that_nothing_replaces_is_covered_only_with_two_real_quotations(self):
        row = self.judged({'requirement': 'Two questions.', 'source': self.IDENTITY, 'status': 'COVERED', 'spec': 'Ask the two security questions before you say anything about the account.'})
        self.assertEqual((row['status'], row['document'], row['blocking']), ('COVERED', 'a.md', False), 'inherited from the first document, and in the spec')
        row = self.judged({'requirement': 'Two questions.', 'source': self.IDENTITY, 'status': 'COVERED', 'spec': 'The agent asks two questions first.'})
        self.assertEqual(row['status'], 'UNVERIFIED', 'a passage that is not in the spec is no coverage, whatever the model says')
        self.assertIn('not in the spec word for word', row['why'])
        row = self.judged({'requirement': 'Two questions.', 'source': self.IDENTITY, 'status': 'COVERED', 'spec': 'account'})
        self.assertEqual(row['status'], 'UNVERIFIED', 'a word that occurs is no passage')
        row = self.judged({'requirement': 'Two questions.', 'source': 'The agent must verify the identity with 2 questions.', 'status': 'COVERED', 'spec': 'identity.max_wrong: 3'})
        self.assertEqual((row['status'], row['document']), ('UNVERIFIED', None), 'a requirement the documents do not state in these words is not counted at all')

    def test_a_missing_rule_is_never_covered_and_blocks_when_its_figure_is_nowhere_in_the_spec(self):
        row = self.judged({'requirement': 'The lowest promise is 40 percent.', 'source': self.FLOOR, 'status': 'MISSING', 'note': 'No lowest amount.'})
        self.assertEqual((row['status'], row['blocking']), ('MISSING', True))
        self.assertIn('the sentence has 40, which occurs nowhere in the spec', row['why'])
        row = self.judged({'requirement': 'Identity first.', 'source': self.IDENTITY, 'status': 'MISSING'})
        self.assertEqual((row['status'], row['blocking']), ('MISSING', False), 'reported, and not firm: it rests on the reading of the model alone')
        self.assertIn('the reading of the model alone', row['why'])
        row = self.judged({'requirement': 'The lowest promise is 40 percent.', 'source': self.FLOOR, 'status': 'COVERED', 'spec': 'identity.max_wrong: 3'})
        self.assertEqual(row['status'], 'AMBIGUOUS', 'a real passage does not cover a sentence whose figure the spec does not have')
        with_floor = dict(self.SPEC, promise={'floor_percent': 40})
        self.assertEqual(self.judged({'requirement': 'x', 'source': self.FLOOR, 'status': 'MISSING'}, spec=with_floor)['blocking'], False, 'a figure that occurs makes an omission no firmer; it proves no coverage')
        self.assertEqual(self.judged({'requirement': 'x', 'source': self.FLOOR, 'status': 'MISSING'}, spec=with_floor)['status'], 'MISSING')

    def test_a_later_document_replaces_only_what_it_replaces_in_so_many_words(self):
        row = self.judged({'requirement': 'Callbacks until 17:00.', 'source': self.OLD_WINDOW, 'status': 'OVERRIDDEN', 'replaced_by': self.NEW_WINDOW})
        self.assertEqual((row['status'], row['document'], row['replaced_by']), ('OVERRIDDEN', 'a.md', self.NEW_WINDOW))
        self.assertIn('replaced by a sentence of b.md', row['why'])
        row = self.judged({'requirement': 'Callbacks until 20:00.', 'source': self.NEW_WINDOW, 'status': 'COVERED', 'spec': 'callback.end: 20:00'})
        self.assertEqual((row['status'], row['document']), ('COVERED', 'b.md'), 'the rule that replaces it is a requirement of its own')
        for claim in ({'replaced_by': 'The memo changes the callback window.'}, {'replaced_by': self.IDENTITY}, {}):
            row = self.judged(dict({'requirement': 'Callbacks until 17:00.', 'source': self.OLD_WINDOW, 'status': 'OVERRIDDEN'}, **claim))
            self.assertEqual(row['status'], 'UNVERIFIED', 'not replaced on the word of the model: %r' % (claim,))
            self.assertIn('it stays a requirement', row['why'])
        row = self.judged({'requirement': 'x', 'source': self.NEW_WINDOW, 'status': 'OVERRIDDEN', 'replaced_by': self.OLD_WINDOW})
        self.assertEqual(row['status'], 'UNVERIFIED', 'an earlier document replaces nothing of a later one')
        row = self.judged({'requirement': 'Callbacks until 17:00.', 'source': self.OLD_WINDOW, 'status': 'OVERRIDDEN', 'replaced_by': self.NEW_WINDOW}, briefs=[('both.md', self.A + '\n' + self.B)])
        self.assertEqual(row['status'], 'OVERRIDDEN', 'in one file that holds both, the sentence that stands later replaces')
        row = self.judged({'requirement': 'Callbacks until 17:00.', 'source': self.OLD_WINDOW, 'status': 'OVERRIDDEN', 'replaced_by': self.NEW_WINDOW}, briefs=[('a.md', self.A)])
        self.assertEqual(row['status'], 'UNVERIFIED', 'a single brief has no later document')

    def test_unclear_coverage_is_reported_as_unclear(self):
        row = self.judged({'requirement': 'Identity first.', 'source': self.IDENTITY, 'status': 'AMBIGUOUS', 'spec': 'identity.max_wrong: 3', 'note': 'The order is not stated.'})
        self.assertEqual((row['status'], row['blocking'], row['note']), ('AMBIGUOUS', False, 'The order is not stated.'))
        self.assertEqual(self.judged({'requirement': 'x', 'source': self.IDENTITY, 'status': 'probably fine'})['status'], 'UNVERIFIED')
        self.assertEqual(self.judged({'requirement': 'x', 'source': self.IDENTITY})['status'], 'UNVERIFIED')

    def test_figures_are_read_the_same_however_they_are_written_and_a_reference_is_none(self):
        self.assertEqual(coverage.figures('Three wrong answers, 30%, EUR 1,000.00 or 12.50, from 9:00 to 18:30, close as `not_reached`'), {'3', '30', '1000', '12.50', '09:00', '18:30', 'not_reached'})
        self.assertEqual(coverage.figures('{"max_wrong": 3, "percent": 30.0, "start": "09:00"}'), {'3', '30', '09:00', 'max_wrong'})
        self.assertEqual(coverage.figures('12. Start each product at attempt 1 of Brief A rule 30 (see section 4.2 and rules 5 to 7).'), {'1'})
        self.assertEqual(coverage.figures('Call `get_account` and then `close_call`.'), set(), 'the name of a tool is no figure of a rule')

    def test_the_summary_counts_and_says_how_much_of_the_documents_no_entry_quotes(self):
        rows = coverage.judge([{'requirement': 'a', 'source': self.IDENTITY, 'status': 'COVERED', 'spec': 'Ask the two security questions before you say anything about the account.'},
                               {'requirement': 'b', 'source': self.FLOOR, 'status': 'MISSING'}], [('a.md', self.A), ('b.md', self.B)], self.SPEC)
        told = coverage.summary(rows, [('a.md', self.A), ('b.md', self.B)])
        self.assertEqual((told['requirements'], told['counts']['COVERED'], told['counts']['MISSING'], told['blocking']), (2, 1, 1, 1))
        self.assertEqual((told['lines_unquoted'], told['lines']), (3, 5), 'the callback rule and the two lines of the memo were not looked at')

    def test_one_revision_is_tried_for_reported_omissions_and_what_it_leaves_is_judged_again(self):
        api, text, briefs = Api(OPENAPI), json.dumps(OPENAPI), [('brief-a.md', TEXT_A), ('brief-b.md', TEXT_B)]
        check = lambda s: validate(s, api, True)
        client, lines = claude.ReplayClient([json.dumps(OMITTED), json.dumps(fixture(True))]), []
        spec, report = coverage.audit(early_callback(), briefs, text, client, check, lines.append)
        self.assertEqual((spec['callback']['end'], report['revised']), ('19:00', True))
        self.assertEqual([row['status'] for row in report['rows']], ['COVERED', 'UNVERIFIED'], 'after a revision the omission is neither reported as one nor as covered')
        self.assertIn('the revision was not audited again', report['rows'][1]['why'])
        self.assertIn(CALLBACK, client.seen[1][1][2]['content'], 'the revision is asked for with the sentence of the brief')
        self.assertIn('<document index="2" name="brief-b.md">', client.seen[0][1][0]['content'])
        self.assertIn('callback.end: 18:00', client.seen[0][1][0]['content'], 'the audit reads the spec as lines')
        # A revision that leaves the figure out leaves the omission firm; one that validation refuses leaves the spec.
        for reply in (json.dumps(dict(early_callback(), language='English ')), '{}'):
            spec, report = coverage.audit(early_callback(), briefs, text, claude.ReplayClient([json.dumps(OMITTED), reply]), check, lines.append)
            self.assertEqual((spec['callback']['end'], report['rows'][1]['status'], report['rows'][1]['blocking']), ('18:00', 'MISSING', True))
        spec, report = coverage.audit(early_callback(), briefs, text, claude.ReplayClient([json.dumps(OMITTED)]), check, lines.append, refused=lambda seconds: 'no call is left')
        self.assertEqual((report['revised'], report['rows'][1]['blocking']), (False, True))
        self.assertTrue(any('no revision: no call is left' in line for line in lines))
        spec, report = coverage.audit(fixture(True), briefs, text, claude.ReplayClient(['I could not finish.']), check, lines.append)
        self.assertEqual((report['readable'], report['rows']), (False, []))


class Provider:
    """The evaluation of these runs: the prepared results of test_m19_run, for the runs that name a suite."""

    def __init__(self, test, outcomes):
        self.test, self.outcomes = test, list(outcomes)

    @staticmethod
    def add_arguments(parser):
        parser.add_argument('--suite', action='append', default=[])

    def Evaluation(self, args):
        made = Customers(self.test.platform, self.outcomes)
        made.workflow_id = 'w-' + args.slot
        self.test.evaluated.append(made)
        return made


class TheHiddenBriefRun(unittest.TestCase):
    """builder/run.py on a brief without test customers, with a replayed model and the fake platform."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry, self.platform, self.evaluated = self.root / 'agents.json', Track(), []

    def build(self, *argv, replies=(), client=None, outcomes=(), slot='hidden-1', amend=(), strategy='audit'):
        self.client = client or claude.ReplayClient([r if isinstance(r, str) else json.dumps(r) for r in replies], model='synthetic-model')
        with mock.patch.object(settings, 'load_env'), mock.patch('builtins.print'):
            code = builder_run.main([BRIEF_A, API_FILE] + [part for brief in amend for part in ('--amend', brief)] + [
                '--slot', slot, '--strategy', strategy, '--out', str(self.root / 'out'), '--trace', str(self.root / 'traces' / (slot + '.log')),
                '--export', str(self.root / 'submission' / (slot + '.workflow.json')), '--results', str(self.root / 'results')] + list(argv),
                client=self.client, platform=lambda: self.platform, evaluation=Provider(self, outcomes), registry=self.registry)
        self.files = store.BuildStore(self.root / 'out', slot)
        traces = sorted((self.root / 'traces').glob(slot + '*.log'), key=lambda p: (p.stat().st_mtime_ns, len(p.name)))
        self.trace = traces[-1].read_text(encoding='utf-8')
        reports = sorted((self.root / 'results').glob('*%s.json' % slot), key=lambda p: p.stat().st_mtime_ns)
        self.result = json.loads(reports[-1].read_text(encoding='utf-8'))
        self.report = reports[-1].with_suffix('.md').read_text(encoding='utf-8')
        return code

    def status(self, check):
        return next(row['status'] for row in self.result['evidence'] if row['check'] == check)

    def detail(self, check):
        return next(row['detail'] for row in self.result['evidence'] if row['check'] == check)

    def published(self):
        return [v['versionNumber'] for v in self.platform.versions if v['isPublished']]

    def exported(self, slot='hidden-1'):
        return (self.root / 'submission' / (slot + '.workflow.json')).is_file()

    # ---- the run that goes well

    def test_a_hidden_brief_is_extracted_audited_published_once_and_reported_for_what_it_is(self):
        code = self.build(replies=[fixture(True), '[]', COVERED_ALL], amend=[BRIEF_B])
        self.assertEqual((code, self.result['status'], self.published(), len(self.platform.versions)), (0, 'success', [1], 1), 'one version, published once')
        self.assertEqual((self.result['model_calls'], self.result['model_calls_started'], len(self.client.seen)), (3, 3, 3))
        by_kind = {kind: [(row['check'], row['status']) for row in self.result['evidence'] if row['kind'] == kind] for kind in builder_run.KINDS}
        self.assertEqual(by_kind['static'], [(check, 'PASS') for check in ('credential check of the spec', 'spec validation against the tool API', 'compilation', 'credential check of the workflow',
                                                                         'workflow validation', 'tool-contract validation')])
        self.assertEqual(by_kind['audit'], [('audit of the high-risk content', 'PASS'), ('coverage of the mandatory rules', 'PASS'), ('rules the extractor could not express', 'PASS')])
        self.assertEqual(by_kind['platform'], [(check, 'PASS') for check in ('dry run', 'publication', 'export read from the platform', 'read-back of the published version', 'export is the published build')])
        self.assertEqual(by_kind['simulation'], [('customer simulation', 'NOT TESTED')])
        self.assertIn('2 requirement(s) listed by the model: 2 covered, 0 overridden, 0 missing (0 firm), 0 ambiguous, 0 unverified', self.detail('coverage of the mandatory rules'))
        for part in ('| limits | model calls: at most 4, each waited for at most 240 s, none started in the last 4.0 min of the 25.0 | coverage audit: report',
                     '| LLM call 3 | synthetic-model | purpose: audit the coverage of the mandatory rules of the documents',
                     '| evidence | Deterministic static checks | tool-contract validation | PASS', '| evidence | Platform validation | dry run | PASS',
                     '| evidence | Live behavioral simulation | customer simulation | NOT TESTED | no suite of test customers was named for this brief: no customer was simulated',
                     '| self-test | none: no suite of test customers was named for this brief', '| evaluation | Deterministic static checks: 6 PASS | LLM-based document audits: 3 PASS | '
                     'Platform validation: 5 PASS | Live behavioral simulation: 1 NOT TESTED', '| evaluation | behavior in a call: NOT TESTED.', '| run end | status success |'):
            self.assertIn(part, self.trace)
        self.assertLess(self.trace.index('| evidence | Platform validation | dry run | PASS'), self.trace.index('| evidence | Platform validation | publication | PASS'), 'nothing is published before the dry run')

    def test_the_inputs_and_what_was_made_of_them_are_named_by_their_hashes(self):
        self.build(replies=[fixture(True), '[]', COVERED_ALL], amend=[BRIEF_B])
        made = self.result['provenance']
        self.assertEqual([(b['file'], b['sha256']) for b in made['briefs']], [(path, hashlib.sha256(Path(path).read_bytes()).hexdigest()) for path in (BRIEF_A, BRIEF_B)], 'each document, the amendment too')
        self.assertEqual(made['tool_api']['sha256'], hashlib.sha256(Path(API_FILE).read_bytes()).hexdigest())
        build = self.files.read(store.BUILD)
        self.assertEqual((made['spec_sha256'], made['workflow_sha256']), (build['spec_sha256'], build['local_workflow_sha256']))
        self.assertEqual(made['workflow_sha256'], versioning.workflow_hash(self.files.read(store.WORKFLOW)))
        self.assertEqual(build['source']['inputs']['briefs'][1]['sha256'], made['briefs'][1]['sha256'], 'the build record says what it was built from')
        self.assertIn('extraction, strategy audit, prompt v2', made['spec_from'])
        self.assertIn('model synthetic-model', made['spec_from'])
        for part in ('| provenance | brief 2 of 2 | %s | sha256 %s' % (BRIEF_B, made['briefs'][1]['sha256']), '| provenance | tool API | %s | sha256 %s' % (API_FILE, made['tool_api']['sha256']),
                     '| provenance | spec sha256 %s | workflow sha256 %s | build %s' % (made['spec_sha256'], made['workflow_sha256'], build['version_id']),
                     '| run start | run %s | slot hidden-1 | builder commit %s' % (self.result['run_id'], versioning.builder_commit())):
            self.assertIn(part, self.trace)

    def test_the_report_keeps_the_four_kinds_of_evidence_apart_and_claims_no_tested_behavior(self):
        self.build(replies=[fixture(True), '[]', COVERED_ALL])
        for part in ('## Provenance', '## Evaluation', '### Deterministic static checks', '### LLM-based document audits', '### Platform validation', '### Live behavioral simulation',
                     '| customer simulation | NOT TESTED | no suite of test customers was named for this brief: no customer was simulated', 'No self-test was run', 'No score is made from these rows.',
                     'Neither says how the agent behaves in a call'):
            self.assertIn(part, self.report)
        simulated = self.report.split('### Live behavioral simulation')[1].split('##')[0]
        self.assertNotIn('PASS', simulated, 'nothing static is listed as a simulated customer')
        self.assertNotIn('self-test | incumbent', self.trace)
        self.assertNotIn('case(s) pass', self.trace + self.report, 'no result of a customer is reported where none was simulated')
        self.assertNotRegex(self.report + self.trace, r'(?i)\bscore\b[^.\n]*\d+ ?%|quality score|confidence[: ]+\d')
        self.assertEqual(self.evaluated, [], 'no customer was simulated')
        self.assertEqual(sorted(json.loads((self.root / 'submission' / 'hidden-1.workflow.json').read_text(encoding='utf-8'))), ['workflow', 'workflow_id'], 'the export is in the form it had')

    # ---- time and the number of calls

    def test_a_model_that_hangs_ends_each_call_at_its_limit_and_nothing_is_published(self):
        started = time.time()
        code = self.build('--model-call-seconds', '0.3', client=Hanging())
        self.assertLess(time.time() - started, 20)
        self.assertEqual((code, self.result['status'], self.platform.versions, self.exported()), (1, 'failed', [], False))
        self.assertEqual(self.client.asked, 2, 'the extraction, and the one fallback')
        self.assertEqual(self.trace.count('| LLM call failed | synthetic-hanging | TimeoutError: no reply within 0 s'), 2)
        self.assertIn('| extraction fallback | strategy single, once | the audit extraction gave no spec: TimeoutError', self.trace)
        self.assertIn('| extraction fallback | no valid spec | TimeoutError', self.trace)
        self.assertIn('| published now | the slot hidden-1 has no workflow on Freya: nothing is published for it', self.trace)
        self.assertEqual({row['status'] for row in self.result['evidence']}, {'NOT TESTED'}, 'nothing was checked, and nothing is reported as checked')
        self.assertRegex(self.trace.splitlines()[-1], r'\| run end \| status failed \|')

    def test_with_the_deadline_used_up_no_extraction_is_started(self):
        code = self.build('--deadline-minutes', '4.5', '--reserve-minutes', '4', replies=[fixture(True), '[]', COVERED_ALL])
        self.assertEqual((code, self.result['status'], self.platform.versions, len(self.client.seen)), (1, 'failed', [], 0), 'the model was not asked and nothing was sent to the platform')
        self.assertIn('| LLM call not made | synthetic-model | ', self.trace)
        self.assertIn('are left for model calls before the time kept for the end of the run, and a call needs 45', self.trace)
        self.assertRegex(self.trace, r'\| extraction fallback \| not made \| \d+ s are left for model calls .* a call needs 120')
        self.assertIn('BudgetExceeded', self.result['reason'])

    def test_the_run_starts_no_more_model_calls_than_it_may(self):
        code = self.build('--max-model-calls', '1', replies=[fixture(True), '[]', COVERED_ALL])
        self.assertEqual((code, self.result['status'], len(self.client.seen), self.result['model_calls_started']), (0, 'success', 1, 1), 'the extraction, and then no audit')
        self.assertEqual((self.status('audit of the high-risk content'), self.status('coverage of the mandatory rules')), ('SKIP', 'SKIP'))
        self.assertIn('the limit of 1 model call(s) of this run is reached', self.detail('coverage of the mandatory rules'))
        self.assertIn('| limits | model calls: at most 1,', self.trace)
        # Four by default: three for an extraction that never validates, and the one that was kept for the fallback.
        self.platform, self.registry = Track(), self.root / 'agents-2.json'
        code = self.build(replies=['{}', '{}', '{}', fixture(True), '[]', COVERED_ALL], slot='hidden-2')
        self.assertEqual((code, self.result['status'], len(self.client.seen), self.published()), (0, 'success', 4, [1]))
        self.assertEqual(self.status('coverage of the mandatory rules'), 'SKIP', 'no fifth call')
        self.assertIn('the limit of 4 model call(s) of this run is reached: 4 made', self.detail('coverage of the mandatory rules'))

    # ---- the fallback extraction

    def test_an_extraction_that_fails_is_followed_by_one_single_extraction(self):
        code = self.build(client=FailsFirst([json.dumps(fixture(True)), json.dumps(COVERED_ALL)]))
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [1]))
        source = self.files.read(store.BUILD)['source']
        self.assertEqual((source['strategy'], source['strategy_asked'], source['fallback']), ('single', 'audit', 'RuntimeError: SYNTHETIC 529 overloaded'))
        self.assertEqual((self.status('audit of the high-risk content'), self.detail('audit of the high-risk content')), ('SKIP', 'the fallback extraction makes no audit'))
        self.assertEqual(self.status('spec validation against the tool API'), 'PASS', 'the spec of the fallback passes every check a spec passes')
        self.assertEqual(self.status('coverage of the mandatory rules'), 'PASS')
        for part in ('| LLM call failed | synthetic-model | RuntimeError: SYNTHETIC 529 overloaded', '| extraction fallback | strategy single, once | the audit extraction gave no spec: RuntimeError',
                     '| extraction fallback | a spec that passes validation', 'strategy single (asked for: audit; fallback because: RuntimeError: SYNTHETIC 529 overloaded)'):
            self.assertIn(part, self.trace)

    def test_a_spec_that_stays_invalid_is_followed_by_one_single_extraction(self):
        code = self.build(replies=['{}', 'no json', '{}', fixture(True)])
        self.assertEqual((code, self.result['status']), (0, 'success'))
        self.assertIn('| extraction fallback | strategy single, once | the audit extraction gave no spec: the spec stayed invalid after its repairs', self.trace)
        self.assertEqual(self.files.read(store.BUILD)['source']['fallback'], 'the spec stayed invalid after its repairs')

    def test_a_fallback_that_fails_publishes_nothing_and_leaves_what_was_published(self):
        self.assertEqual(self.build(replies=[fixture(True), '[]', COVERED_ALL]), 0)
        first = self.result['final_build']
        code = self.build('--max-model-calls', '0', replies=['{}', '{}', '{}', '{}', '{}', '{}', fixture(True)])
        self.assertEqual((code, self.result['status'], self.published(), len(self.platform.versions)), (1, 'failed', [1], 1), 'the version of the first run is still the published one')
        self.assertEqual((len(self.client.seen), self.result['reason']), (6, 'the spec is not valid'), 'three calls, three more, and no third extraction')
        self.assertIn('| extraction fallback | no valid spec | SpecError', self.trace)
        self.assertIn('| SPEC ERROR | ', self.trace)
        self.assertIn('| published now | workflow w-hidden-1 | platform version 1 (pv-1) | build %s | its export is kept at ' % first, self.trace)
        self.assertEqual(self.result['published_now']['build'], first)
        self.assertEqual(self.status('publication'), 'NOT TESTED')

    def test_no_fallback_is_started_without_a_call_or_without_the_time_for_one(self):
        code = self.build('--max-model-calls', '1', client=FailsFirst([json.dumps(fixture(True))]))
        self.assertEqual((code, self.result['status'], self.platform.versions, len(self.client.seen)), (1, 'failed', [], 1))
        self.assertIn('| extraction fallback | not made | the limit of 1 model call(s) of this run is reached: 1 made', self.trace)
        with mock.patch.object(builder_run, 'FALLBACK_SECONDS', 10 ** 6):
            code = self.build(client=FailsFirst([json.dumps(fixture(True))]), slot='hidden-2')
        self.assertEqual((code, self.result['status'], self.platform.versions, len(self.client.seen)), (1, 'failed', [], 1))
        self.assertRegex(self.trace, r'\| extraction fallback \| not made \| \d+ s are left for model calls')
        self.assertIn('RuntimeError: SYNTHETIC 529 overloaded', self.result['reason'], 'the run fails with what the extraction failed with')

    # ---- the coverage audit in a run

    def test_asked_to_block_a_firm_omission_stops_the_run_before_anything_is_built_or_published(self):
        code = self.build('--coverage', 'block', replies=[early_callback(), '[]', OMITTED, '{}'])
        self.assertEqual((code, self.result['status'], self.platform.versions, self.exported()), (1, 'incomplete', [], False))
        self.assertIn('the coverage audit found 1 mandatory requirement(s) of the documents that the spec does not carry', self.result['reason'])
        self.assertEqual((self.status('coverage of the mandatory rules'), self.status('spec validation against the tool API'), self.status('publication')), ('FAIL', 'NOT TESTED', 'NOT TESTED'))
        self.assertIn('1 covered, 0 overridden, 1 missing (1 firm)', self.detail('coverage of the mandatory rules'))
        self.assertIn('| coverage | MISSING (firm) | A callback is booked up to 19:00. | the sentence has 19:00, which occurs nowhere in the spec | source (brief-a.md): ', self.trace)
        held = self.files.path('runs') / (self.result['run_id'] + '.unpublished-spec.json')
        self.assertEqual(json.loads(held.read_text(encoding='utf-8'))['callback']['end'], '18:00', 'the spec is kept for a look, beside the run')
        self.assertFalse(self.files.path(store.WORKFLOW).exists(), 'nothing was compiled')
        self.assertIn('- **MISSING**, firm: A callback is booked up to 19:00.', self.report)

    def test_an_omission_the_revision_repairs_does_not_stop_the_run(self):
        code = self.build(replies=[early_callback(), '[]', OMITTED, fixture(True)])
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [1]))
        self.assertEqual((self.files.read(store.SPEC)['callback']['end'], self.files.read(store.BUILD)['source']['revised_after_coverage_audit']), ('19:00', True))
        self.assertEqual(self.status('coverage of the mandatory rules'), 'UNVERIFIED', 'revised, and not audited again: that is not a pass')
        self.assertIn('the spec was revised once for the omissions', self.detail('coverage of the mandatory rules'))
        self.assertIn('| LLM call 4 | synthetic-model | purpose: revise the spec after the coverage audit', self.trace)

    def test_by_default_the_run_publishes_and_says_what_is_missing(self):
        code = self.build(replies=[early_callback(), '[]', OMITTED, '{}'])
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [1]))
        self.assertIn('coverage audit: report', self.trace)
        self.assertIn('| coverage | MISSING (firm) | A callback is booked up to 19:00.', self.trace)
        self.assertIn('1 missing (1 firm)', self.detail('coverage of the mandatory rules'))
        self.assertEqual(self.status('coverage of the mandatory rules'), 'FAIL', 'published, and still reported as failed')
        self.assertIn('| evaluation | Deterministic static checks: 6 PASS | LLM-based document audits: 2 PASS, 1 FAIL | ', self.trace)
        code = self.build('--coverage', 'off', replies=[fixture(True), '[]'], slot='hidden-2')
        self.assertEqual((code, len(self.client.seen), self.status('coverage of the mandatory rules')), (0, 2, 'SKIP'))

    def test_what_the_audit_cannot_confirm_is_reported_and_does_not_stop_the_run(self):
        entries = [dict(COVERED_ALL[0]), {'requirement': 'One product per customer.', 'kind': 'rule', 'source': 'Freya Bank calls customers who are 1 to 45 days late on one product.',
                                          'status': 'OVERRIDDEN', 'replaced_by': SEVERAL},
                   {'requirement': 'Attempts are per product.', 'kind': 'rule', 'source': PER_PRODUCT, 'status': 'AMBIGUOUS', 'spec': 'promise.max_attempts: 2', 'note': 'Not the same count.'},
                   {'requirement': 'A promise needs a recorded consent.', 'kind': 'rule', 'source': 'The customer must consent in writing before a promise is recorded.', 'status': 'MISSING'},
                   {'requirement': 'Attempts are per product.', 'kind': 'rule', 'source': PER_PRODUCT, 'status': 'MISSING', 'note': 'No count per product.'}]
        code = self.build('--max-model-calls', '3', replies=[fixture(True), '[]', entries], amend=[BRIEF_B])
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [1]))
        self.assertEqual([(row['status'], row['document'], row['blocking']) for row in self.result['coverage']['rows']],
                         [('COVERED', 'brief-a.md', False), ('OVERRIDDEN', 'brief-a.md', False), ('AMBIGUOUS', 'brief-b.md', False), ('UNVERIFIED', None, False), ('MISSING', 'brief-b.md', False)])
        self.assertEqual(self.status('coverage of the mandatory rules'), 'FAIL', 'an omission that is not firm is reported as one and does not block')
        self.assertIn('1 covered, 1 overridden, 1 missing (0 firm), 1 ambiguous, 1 unverified', self.detail('coverage of the mandatory rules'))
        self.assertIn('no revision: the limit of 3 model call(s) of this run is reached', self.trace)
        for part in ('- **OVERRIDDEN**: One product per customer.', '- **AMBIGUOUS**: Attempts are per product.', '- **UNVERIFIED**: A promise needs a recorded consent.', '- **MISSING**: Attempts are per product.'):
            self.assertIn(part, self.report)
        self.assertNotIn('Three wrong answers end the security check.', self.report.split('not confirmed as covered')[1], 'what is covered is counted, not listed')

    def test_an_audit_that_fails_or_cannot_be_read_is_no_pass_and_no_stop(self):
        code = self.build(replies=[fixture(True), '[]'])
        self.assertEqual((code, self.status('coverage of the mandatory rules')), (0, 'SKIP'))
        self.assertIn('the audit call failed (IndexError', self.detail('coverage of the mandatory rules'))
        for number, reply in enumerate(('The specification covers everything.', '[]'), 2):
            self.platform, self.registry = Track(), self.root / ('agents-%d.json' % number)
            code = self.build(replies=[fixture(True), '[]', reply], slot='hidden-%d' % number)
            self.assertEqual((code, self.status('coverage of the mandatory rules')), (0, 'UNVERIFIED'), reply)
            self.assertIn('it proves nothing', self.detail('coverage of the mandatory rules'))

    # ---- what stays as it was

    def test_a_run_with_test_customers_is_as_it_was(self):
        """public:A and public:B: no limit on the number of calls, no coverage audit unless asked for, and the
        results of the customers reported as results of customers."""
        code = self.build('--suite', 'public:A', replies=[fixture(True), GOOD], outcomes=[BEFORE, BETTER], slot='demo-brief-a', strategy='single')
        self.assertEqual((code, self.result['status'], self.published(), len(self.client.seen)), (0, 'success', [2], 2), 'the extraction and the proposal, and no audit call')
        self.assertIn('| limits | model calls: no limit on their number, each waited for at most 240 s', self.trace)
        self.assertIn('coverage audit: off', self.trace)
        self.assertEqual((self.status('coverage of the mandatory rules'), self.status('customer simulation')), ('SKIP', 'PASS'))
        self.assertRegex(self.detail('customer simulation'), r'^3 of 3 case\(s\) of public:A pass on build wf-[0-9a-f]{12}, one run each$')
        self.assertIn('| final | self-tests of the published version: 3 of 3 case(s) pass | scope: public:A, one run each', self.trace)
        self.assertIn('The static checks and the audits below are those of the first build of this run', self.report)
        self.platform, self.registry = Track(), self.root / 'agents-b.json'
        code = self.build('--suite', 'public:B', '--no-optimize', replies=[fixture(True)], outcomes=[BEFORE], slot='demo-brief-a-plus-b', amend=[BRIEF_B], strategy='single')
        self.assertEqual((code, self.status('customer simulation')), (0, 'FAIL'))
        self.assertIn('2 of 3 case(s) of public:B pass', self.detail('customer simulation'))
        self.assertIn('not passing: PUB-12 (fail: judge)', self.detail('customer simulation'))

    def test_a_reused_spec_is_built_as_before_and_its_audits_are_reported_as_not_made(self):
        spec = self.root / 'spec.json'
        spec.write_text(json.dumps(fixture(True)), encoding='utf-8')
        code = self.build('--spec', str(spec), replies=[])
        self.assertEqual((code, self.result['status'], len(self.client.seen)), (0, 'success', 0))
        self.assertEqual((self.status('audit of the high-risk content'), self.status('coverage of the mandatory rules')), ('SKIP', 'SKIP'))
        self.assertIn('the file %s' % spec, self.result['provenance']['spec_from'])
        # A second run on a slot that has a workflow says so before it publishes over it.
        code = self.build('--spec', str(spec), replies=[])
        self.assertEqual((code, self.published()), (0, [2]))
        self.assertIn('| policy | the slot hidden-1 already has the workflow w-hidden-1: this run publishes a new version of it without test customers', self.trace)

    def test_a_platform_that_refuses_the_workflow_or_loses_the_save_is_reported_as_that(self):
        with mock.patch.object(self.platform, 'validate_workflow', side_effect=RuntimeError('SYNTHETIC 422 the workflow is not valid')):
            code = self.build(replies=[fixture(True), '[]', COVERED_ALL])
        self.assertEqual((code, self.result['status'], self.platform.versions), (1, 'failed', []))
        self.assertEqual((self.status('tool-contract validation'), self.status('dry run'), self.status('publication')), ('PASS', 'FAIL', 'NOT TESTED'))
        with mock.patch.object(self.platform, 'save_workflow', side_effect=OSError('SYNTHETIC connection reset')):
            code = self.build(replies=[fixture(True), '[]', COVERED_ALL], slot='hidden-2')
        self.assertEqual((code, self.status('dry run'), self.status('publication')), (1, 'PASS', 'UNVERIFIED'), 'a save that did not answer may have been stored: not a pass, not a fail')
        self.assertIn('SYNTHETIC connection reset', self.detail('publication'))

    def test_a_spec_that_fails_a_static_check_is_reported_at_that_check(self):
        with mock.patch.object(builder_run.pipeline, 'check_workflow', return_value=['SYNTHETIC: end: unreachable']):
            code = self.build(replies=[fixture(True), '[]', COVERED_ALL])
        self.assertEqual((code, self.result['status'], self.platform.versions), (1, 'failed', []))
        self.assertEqual([self.status(check) for check in ('compilation', 'workflow validation', 'tool-contract validation', 'dry run')], ['PASS', 'FAIL', 'NOT TESTED', 'NOT TESTED'])
        self.assertIn('SYNTHETIC: end: unreachable', self.detail('workflow validation'))


if __name__ == '__main__':
    unittest.main()
