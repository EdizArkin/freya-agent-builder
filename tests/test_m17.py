"""M17: the prompt version that teaches the M16 fields, the path from an extraction to an enforced rule, simulation
names, version ids, what the platform stored, and what a transcript shows about speech.

Nothing here calls a model, Freya or the bank. The path "extraction to enforcement" is played with a prepared
model reply: it shows that a spec carrying the four fields is validated, compiled and enforced, and that the
extractor is sent the prompt that describes them. Whether a model writes these fields from a brief is shown
only by a paid run, and what a phone model does with them only by a live call (benchmarks/reports/m17-closure.md).
"""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness import live, packages
from builder import pipeline
from builder.artifacts import store
from builder.evaluation import simulation
from builder.extraction import claude
from builder.freya import deployment, versioning
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import digest, validate
from tests.fixture_spec import spec as fixture
from tests.renamed import Renamed
from tests.support import CASES, KIT, OPENAPI, refusal

API = Api(OPENAPI)
OPENAPI_TEXT = json.dumps(OPENAPI, separators=(',', ':'))
MEMO = Path('benchmarks/synthetic_briefs/m16-rules.memo.md')
SCENARIOS = Path('benchmarks/live/m17.scenarios.json')
BRIEFS = [(name, (KIT / 'briefs' / name).read_text(encoding='utf-8')) for name in ('brief-a.md', 'brief-b.md')] + [(MEMO.name, MEMO.read_text(encoding='utf-8'))]
M15_DIGEST = '083c66052cdf131b'
NEW_FIELDS = ('"amount_step"', '"amount_step_except_max"', '"min_amount_by_date"', '"same_date"', '"closed"')
D14, D15, D16 = '2026-10-14', '2026-10-15', '2026-10-16'      # Wednesday (today), Thursday, Friday


def memo_spec():
    """Brief A+B with the four rules of the memo, as a spec carries them. Hand-written test data."""
    spec = fixture(True)
    spec['account']['values'] += [
        {'name': 'half', 'label': 'HALF of the past-due amount', 'expr': {'kind': 'percent', 'of': {'kind': 'field', 'field': 'past_due'}, 'percent': 50}},
        {'name': 'early_until', 'label': 'NEXT BUSINESS DAY', 'expr': {'kind': 'next_business_day'}}]
    spec['promise'].update(amount_step=20, amount_step_except_max=True, same_date=True,
                           min_amount_by_date=[{'on_or_before': 'early_until', 'min_amount': 'half'}])
    spec['callback']['closed'] = [{'from': '12:00', 'before': '13:00'}]
    return spec


def call(spec, products=None, verify=True):
    api = Renamed(spec=spec)
    sim, bank = api.start(products=products)
    if verify:
        api.verify(sim)
    return api, sim, bank


def promise(api, sim, product, amount, date):
    return api.call(sim, 'create_promise', product_id=product, amount=amount, date=date)


def recorded(said):
    return 'is now recorded' in said.get('what_to_do_next', '')


class PromptVersions(unittest.TestCase):
    def test_v1_is_still_the_prompt_the_m15_figures_were_measured_with(self):
        self.assertEqual(claude.prompt_digest(version='v1'), M15_DIGEST)
        logged = [json.loads(line) for line in Path('benchmarks/reports/m15.exposure.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        digests = {c['prompt'] for entry in logged if entry['event'] == 'started' for c in entry['candidates']['candidates']}
        self.assertEqual(digests, {M15_DIGEST}, 'every candidate of the selection and the holdout batch ran with v1')
        self.assertEqual(len(logged), 4, 'the exposure log is as M15 closed it: F and G, started and completed')
        self.assertIn(M15_DIGEST, Path('benchmarks/reports/m15.candidates.json').read_text(encoding='utf-8'))

    def test_a_build_uses_v2_and_can_still_ask_for_v1(self):
        self.assertEqual(claude.PROMPT_VERSION, 'v2')
        self.assertEqual(claude.SYSTEM, claude.system_prompt(version='v2'))
        self.assertNotEqual(claude.prompt_digest(version='v2'), M15_DIGEST)
        self.assertEqual(claude.prompt_digest(), claude.prompt_digest(version='v2'))
        self.assertEqual(pipeline.arguments(['--brief', 'a.md', '--tool-spec', 'api.json']).prompt_version, 'v2')
        self.assertEqual(pipeline.arguments(['--brief', 'a.md', '--tool-spec', 'api.json', '--prompt-version', 'v1']).prompt_version, 'v1')
        with self.assertRaises(ValueError):
            claude.system_prompt(version='v9')

    def test_v2_adds_to_v1_and_takes_nothing_away(self):
        old, new = claude.system_prompt(version='v1'), claude.system_prompt(version='v2')
        replaced = [old_passage for old_passage, _ in claude._V2_EDITS]
        self.assertEqual(len(replaced), 4)
        for old_passage, new_passage in claude._V2_EDITS:
            self.assertEqual(old.count(old_passage), 1)
            # What a passage said before is still said: the words of v1 are all in its replacement, in order.
            words = iter(re.findall(r'[A-Za-z_]+', new_passage))
            self.assertTrue(all(word in words for word in re.findall(r'[A-Za-z_]+', old_passage)), old_passage[:50])
        kept = [line for line in old.splitlines() if not any(line and line in passage for passage in replaced) and not any(passage in line for passage in replaced)]
        for line in kept:
            self.assertIn(line, new)
        # Only the definition of agent-spec/1 grew. The three sections a prompt optimisation may change are the same.
        tail = lambda text: text.split('## rules\n\n', 1)[1]
        self.assertEqual(tail(old), tail(new))
        self.assertEqual(sorted(claude.PROMPT_VERSIONS), ['v1', 'v2'])

    def test_v2_teaches_each_new_field_and_when_to_leave_it_out(self):
        new, old = claude.system_prompt(version='v2'), claude.system_prompt(version='v1')
        for name in NEW_FIELDS:
            self.assertIn(name, new)
            self.assertNotIn(name, old)
        for name in ('`promise.amount_step`', '`promise.min_amount_by_date`', '`promise.same_date`', '`callback.closed`'):
            self.assertGreaterEqual(new.count(name), 2, name + ' is in the description of the runtime and in its own paragraph')
        for part in ('Write a field only when a document states that rule', 'Never derive a step, an amount, a date or a time that no document states',
                     'leave the field out when the documents do not have the rule', 'name it under `unsupported`',
                     '"from" and "until" include their minute, "after" and "before" do not'):
            self.assertIn(part, new)

    def test_what_v2_adds_holds_no_value_of_any_brief(self):
        old = claude.system_prompt(version='v1')
        added = '\n'.join(line for line in claude.system_prompt(version='v2').splitlines() if line not in old.splitlines()).casefold()
        for value in ('20.00', '12:00', '13:00', 'half', 'lunch', 'break', 'Freya', 'memo', 'minimum_payment', 'past_due', '16:00', 'plan_active', 'postcode'):
            self.assertNotIn(value.casefold(), added, value)
        self.assertIsNone(re.search(r'\d\d:\d\d|\d{4}-\d\d-\d\d|\b\d+(\.\d+)?\b', added.replace('agent-spec/1', '')), 'no time, date or number: nothing that could be copied into a spec')
        # Codes, prescribed sentences and dates of every package, the selection and the final one included: none
        # may be in what the new version adds. Only the documents are read here, never a key, and a failure says
        # how many values were found, not which. A code that is one plain word cannot be told from ordinary
        # language and is not looked for; one with a digit, an underscore, a hyphen, a colon or a space is.
        for pid in packages.ids():
            documents, document = packages.inputs(pid)
            text = '\n'.join(body for _, body in documents)
            codes = {v for v in re.findall(r'`([^`\n]{4,40})`', text) if re.search(r'[\d_:\- ]', v)}
            values = codes | set(re.findall(r'"([A-Z][^"\n]{12,}[.?!])"', text)) | set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text))
            found = [v for v in values if v.casefold() not in old.casefold() and v.casefold() in added]
            self.assertEqual(len(found), 0, 'what v2 adds holds %d value(s) of package %s' % (len(found), pid))


class ExtractionToEnforcement(unittest.TestCase):
    """A reply that carries the four fields, through the audit strategy, validation, the compiler and the runtime."""

    def build(self, spec):
        client = claude.ReplayClient([json.dumps(spec), '[]'])
        got = claude.run('audit', BRIEFS, OPENAPI_TEXT, client, lambda s: validate(s, API), digest=lambda one, other=None: digest(one, API, other))
        return got, client, pipeline.build_workflow(got, OPENAPI)[0]

    def test_the_extractor_is_sent_v2_and_the_audit_reads_the_four_rules(self):
        spec, client, workflow = self.build(memo_spec())
        self.assertEqual(client.seen[0][0], claude.system_prompt(version='v2'))
        self.assertIn(MEMO.name, client.seen[0][1][0]['content'])
        audited = client.seen[1][1][0]['content']
        for item in ('promise amounts: "whole multiples of 20, or the highest amount"', 'all promises of one call on one date: true',
                     'lowest amount that depends on the promise date: [{"min_amount": "half", "on_or_before": "early_until"}]',
                     'times of day at which no callback can be booked: [{"before": "13:00", "from": "12:00"}]'):
            self.assertIn(item, audited)
        self.assertEqual(spec['unsupported'], [])

    def test_the_audit_is_told_what_the_retry_count_counts(self):
        """Found in the first live build of M17: the item was called "promise attempts per product", the audit read it
        as the number of persuasion attempts of a brief and reported a defect. The name now says what is counted."""
        items = digest(fixture(True), API)
        self.assertEqual(items['create_promise calls per product before giving up on a failing service'], 2)
        self.assertNotIn('promise attempts per product', items)

    def test_the_fields_survive_validation_compilation_and_serialisation(self):
        spec, client, workflow = self.build(memo_spec())
        hooks = {t['function']['name']: t['pre_process_code'] for node in ('contact', 'negotiate') for t in workflow['nodes'][node]['tools']}
        for part in ('"amount_step":20', '"amount_step_except_max":true', '"same_date":true', '"min_amount_by_date":[{"on_or_before":"early_until","min_amount":"half"}]'):
            self.assertIn(part, hooks['create_promise'])
        self.assertIn('"closed":[{"from":"12:00","before":"13:00"}]', hooks['schedule_callback'])
        again = pipeline.build_workflow(json.loads(json.dumps(spec)), OPENAPI)[0]
        self.assertEqual(versioning.version_id(again), versioning.version_id(workflow), 'the same spec is the same version')
        self.assertNotEqual(versioning.version_id(workflow), versioning.version_id(pipeline.build_workflow(fixture(True), OPENAPI)[0]))

    def test_an_amount_outside_the_step_is_refused_before_the_bank_and_the_next_step_is_recorded(self):
        api, sim, bank = call(self.build(memo_spec())[0])
        self.assertIn('250.00 is not a whole multiple of 20.00', refusal(sim, promise(api, sim, 'p1', 250, D16)))
        self.assertEqual(bank.ledger['promises'], [])
        self.assertTrue(recorded(promise(api, sim, 'p1', 260, D16)))
        self.assertEqual([(p['product_id'], p['amount'], p['date']) for p in bank.ledger['promises']], [('p1', 260.0, D16)])

    def test_the_full_amount_is_recorded_although_it_is_no_multiple(self):
        odd = [{'type': 'credit_card', 'card_last4': '4821', 'past_due': 433.0, 'days_late': 5, 'minimum_payment': 50.0}]
        api, sim, bank = call(self.build(memo_spec())[0], products=odd)
        self.assertIn('is not a whole multiple of 20.00', refusal(sim, promise(api, sim, 'p1', 430, D16)))
        self.assertTrue(recorded(promise(api, sim, 'p1', 433, D14)), 'the full amount, today')
        self.assertEqual([p['amount'] for p in bank.ledger['promises']], [433.0])

    def test_a_low_amount_on_an_early_date_is_refused_and_recorded_on_a_later_one(self):
        api, sim, bank = call(self.build(memo_spec())[0])
        for early in (D14, D15):
            said = promise(api, sim, 'p1', 160, early)
            self.assertIn('needs at least 250.00, and 160.00 is less', refusal(sim, said), early)
            self.assertEqual(bank.ledger['promises'], [])
        self.assertTrue(recorded(promise(api, sim, 'p1', 160, D16)), 'the day after the next business day')
        api, sim, bank = call(self.build(memo_spec())[0])
        self.assertTrue(recorded(promise(api, sim, 'p1', 260, D15)), 'at least half, on the next business day')

    def test_a_second_promise_with_another_date_is_refused(self):
        api, sim, bank = call(self.build(memo_spec())[0])
        self.assertIn('all promises of this call carry one date', refusal(sim, promise(api, sim, 'p2', 900, D16)), 'the first promise goes to the first product')
        self.assertTrue(recorded(promise(api, sim, 'p1', 500, D16)))
        self.assertIn('is already recorded', refusal(sim, promise(api, sim, 'p2', 900, '2026-10-20')))
        self.assertEqual([(p['product_id'], p['date']) for p in bank.ledger['promises']], [('p1', D16)])
        self.assertTrue(recorded(promise(api, sim, 'p2', 900, D16)))
        self.assertEqual({p['date'] for p in bank.ledger['promises']}, {D16})

    def test_a_callback_in_the_closed_period_is_refused_at_its_first_minute_and_booked_at_the_first_after_it(self):
        for time, ok in (('11:59', True), ('12:00', False), ('12:30', False), ('12:59', False), ('13:00', True)):
            api, sim, bank = call(self.build(memo_spec())[0], verify=False)
            said = api.call(sim, 'schedule_callback', date=D15, time=time)
            self.assertEqual(('is booked' in said.get('what_to_do_next', ''), len(bank.ledger['callbacks'])), (ok, int(ok)), time)
            if not ok:
                self.assertIn('is in a period in which no callback can be booked', sim.vars['rt_live'])

    def test_without_the_fields_the_same_calls_are_recorded(self):
        """The control: the refusals above come from the fields, not from anything else in the spec."""
        spec, client, workflow = self.build(fixture(True))
        for name in ('amount_step', 'min_amount_by_date', 'same_date'):
            self.assertNotIn(name, spec['promise'])
        self.assertNotIn('closed', spec['callback'])
        api, sim, bank = call(spec)
        self.assertTrue(recorded(promise(api, sim, 'p1', 250, D15)))
        self.assertTrue(recorded(promise(api, sim, 'p2', 900, D16)))
        api, sim, bank = call(spec, verify=False)
        self.assertIn('is booked', api.call(sim, 'schedule_callback', date=D15, time='12:30').get('what_to_do_next', ''))

    def test_a_rule_that_is_only_reported_does_not_build_and_is_not_enforced(self):
        reported = fixture(True)
        reported['unsupported'] = ['Amounts are recorded only in whole multiples of 20.']
        for build in (lambda: validate(reported, API), lambda: pipeline.build_workflow(reported, OPENAPI)):
            with self.assertRaises(SpecError) as refused:
                build()
            self.assertIn('the extractor reported mandatory policy it could not express', str(refused.exception))
        workflow, report = pipeline.build_workflow(reported, OPENAPI, best_effort=True)
        self.assertTrue(any('could not express' in w for w in report['warnings']), 'a best-effort build says what it does not enforce')
        api, sim, bank = call(dict(reported, unsupported=[]))
        self.assertTrue(recorded(promise(api, sim, 'p1', 250, D15)), 'reported is not enforced: this is why such a spec does not build by default')

    def test_a_value_the_runtime_cannot_work_out_stops_the_promise(self):
        api, sim, bank = call(self.build(memo_spec())[0], products=[{'type': 'credit_card', 'card_last4': '4821', 'days_late': 5, 'minimum_payment': 50.0}])
        said = promise(api, sim, 'p1', 60, D16)
        self.assertIn('could not work out the limits', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])

    def test_the_synthetic_memo_states_each_rule_and_belongs_to_no_package(self):
        text = MEMO.read_text(encoding='utf-8')
        for part in ('whole multiple of 20.00', 'at least half of the past-due amount', 'carries the same date', 'from 12:00 up to, but not including, 13:00'):
            self.assertIn(part, text)
        self.assertNotIn('m16-rules', Path('benchmarks/packages/MANIFEST.json').read_text(encoding='utf-8'))


class SimulationNames(unittest.TestCase):
    def test_the_name_says_run_brief_scenario_expected_result_and_version(self):
        self.assertEqual(simulation.simulation_name('M17', 'A', 'Happy Path', 'Success', 'v1'), 'M17 | A | Happy Path | Success | v1')
        self.assertEqual(simulation.simulation_name('M17', 'M16', 'Amount Step', 'Rejected', 'v1'), 'M17 | M16 | Amount Step | Rejected | v1')
        self.assertEqual(simulation.simulation_name('M17', 'E', '16:00 Cutoff', 'Blocked', 'v1'), 'M17 | E | 16:00 Cutoff | Blocked | v1')

    def test_a_part_cannot_break_the_pattern_and_a_long_scenario_is_cut_not_the_rest(self):
        name = simulation.simulation_name('M17', 'A', 'Pays | then "asks"\nfor a\tchange', 'Success', 'v1')
        self.assertEqual(name, 'M17 | A | Pays then asks for a change | Success | v1')
        long = simulation.simulation_name('M17', 'B', 'x' * 300, 'Two Promises One Note', 'v2 r3')
        self.assertLessEqual(len(long), simulation.NAME_LIMIT)
        self.assertTrue(long.startswith('M17 | B | xxx') and long.endswith(' | Two Promises One Note | v2 r3'))
        self.assertEqual(len(simulation.simulation_name('', '', '', '', '').split(' | ')), 5, 'an empty part is a dash, never a missing column')

    def test_the_same_scenario_run_again_gets_its_own_name(self):
        name = simulation.simulation_name('M17', 'A', 'Happy Path', 'Success', 'v1')
        self.assertEqual(live.unique(name, set()), name)
        self.assertEqual(live.unique(name, {name}), name + ' r2')
        self.assertEqual(live.unique(name, {name, name + ' r2'}), name + ' r3')

    def test_the_expected_result_comes_from_the_records_a_case_must_leave(self):
        self.assertEqual(simulation.expected_label(CASES['PUB-01']), 'Promise')
        self.assertEqual(simulation.expected_label(CASES['PUB-03']), 'Not Verified Failed')
        self.assertEqual(simulation.expected_label({'expected': {'outcome': 'promise'}, 'expected_label': 'Rejected then Promise'}), 'Rejected then Promise')

    def test_a_suite_is_one_simulation_and_its_cases_are_its_personas(self):
        self.assertEqual(simulation.suite_name('M17', 'A', 'Regression', 'v1'), 'M17 | A | Regression | v1')
        self.assertEqual(simulation.suite_name('M17', 'M16', 'Safety', 'v1'), 'M17 | M16 | Safety | v1')
        self.assertLessEqual(len(simulation.suite_name('M17', 'A', 'x' * 300, 'v1')), simulation.NAME_LIMIT)
        cases = live.public_cases(KIT, 'A') + live.public_cases(KIT, 'B') + live.load_scenarios(SCENARIOS, KIT)
        args = mock.Mock(run='M17', version='v1', each=False)
        planned = {live.name_of(group, args): [c['id'] for c in group] for group in live.suites(cases)}
        self.assertEqual(sorted(planned), ['M17 | A | Regression | v1', 'M17 | A | Robustness | v1', 'M17 | A | Safety | v1',
                                           'M17 | B | Regression | v1', 'M17 | B | Robustness | v1', 'M17 | M16 | Safety | v1'])
        self.assertEqual(planned['M17 | A | Regression | v1'], ['PUB-%02d' % n for n in range(1, 11)])
        self.assertEqual(planned['M17 | B | Regression | v1'], ['PUB-11', 'PUB-12', 'PUB-13'])
        self.assertEqual(len(planned['M17 | M16 | Safety | v1']), 6)
        self.assertEqual(sum(len(ids) for ids in planned.values()), len(cases), 'every case is in exactly one simulation')
        for group in live.suites(cases):
            self.assertEqual(len({c['agent'] for c in group}), 1, 'a simulation runs on one agent')

    def test_one_simulation_per_scenario_is_still_there_when_asked_for(self):
        cases = live.public_cases(KIT, 'A') + live.public_cases(KIT, 'B') + live.load_scenarios(SCENARIOS, KIT)
        args = mock.Mock(run='M17', version='v1', each=True)
        names = [live.name_of(group, args) for group in live.suites(cases, each=True)]
        self.assertEqual((len(names), len(set(names))), (len(cases), len(cases)))
        for name in names:
            self.assertRegex(name, r'^M17 \| (A|B|M16) \| [^|]+ \| [^|]+ \| v1$')
            self.assertLessEqual(len(name), simulation.NAME_LIMIT)


class FakeRunner:
    """The kit's runner with the platform and the bank replaced: what a simulation is given and what it reads back."""

    def __init__(self, ledger=None, outcome='pass', messages=None):
        self.started, self.created, self.ledger, self.outcome, self.messages = [], [], ledger, outcome, messages or []
        self.freya = self

    def persona_for(self, case, ident, style=None):
        return {'name': case['id'] + ' ' + case['title'], 'prompt': case['script'] + str(style or ''), 'successCriteria': '\n'.join(case['checks'])}

    def create_personas(self, personas):
        self.created += personas
        return {'personas': [{'id': 'persona-%d' % n} for n in range(len(self.created) - len(personas), len(self.created))]}

    def http(self, path, body=None):
        if path == '/v1/practice/sessions':
            return {'variables': {'customer_id': 'c_' + body['case'], 'now': '2026-10-14T11:00'}}
        return self.ledger if self.ledger is not None else {'outcome': 'promise', 'reason': None, 'promises': [], 'notes': [], 'callbacks': [], 'transfers': [], 'calls': []}

    def score_ledger(self, case, ledger):
        bad = [] if ledger.get('promises') == case['expected'].get('promises') else ['promises differ']
        return {'pass': not bad, 'failed': bad}

    def run_simulation(self, agent_id, name, variables_by_persona, max_seconds=420):
        self.started.append((agent_id, name, variables_by_persona))
        return 'sim-1', 'it-1', 'completed'

    def get_iteration(self, sim_id, iteration_id):
        return {'runs': [{'id': 'run-' + p, 'personaId': p, 'outcome': self.outcome, 'reason': 'judge text', 'callId': 'call-' + p, 'agentVersionNumber': 3, 'turnCount': 6}
                         for p in self.started[-1][2]]}

    def get_call(self, call_id):
        return {'messages': self.messages, 'endedReason': 'assistant-ended-call', 'duration': 40}


def case(cid='S-1', bank_case='PUB-01', promises=()):
    return {'id': cid, 'bank_case': bank_case, 'brief': 'A', 'title': 'a title', 'script': 'a script', 'checks': ['a check'], 'identity': {'date_of_birth': 'x'},
            'variables': {}, 'family': 'Amounts', 'scenario': 'Amount Step', 'expected_label': 'Rejected', 'expected': {'outcome': 'promise', 'promises': list(promises)}}


class NamedSimulations(unittest.TestCase):
    def run_one(self, runner, cases, name='M17 | A | Amount Step | Rejected | v1'):
        with tempfile.TemporaryDirectory() as tmp:
            return simulation.run(runner, 'agent-1', cases, name, Path(tmp) / 'personas.json')

    def test_the_name_given_is_the_name_the_platform_gets_and_the_record_ties_it_to_the_simulation(self):
        runner = FakeRunner()
        record = self.run_one(runner, [case()])
        agent, name, variables = runner.started[0]
        self.assertEqual((agent, name), ('agent-1', 'M17 | A | Amount Step | Rejected | v1'))
        self.assertEqual(variables, {'persona-0': {'customer_id': 'c_PUB-01', 'now': '2026-10-14T11:00'}}, 'the account is that of the bank case')
        self.assertEqual((record['simulation_id'], record['iteration_id'], record['name'], record['status']), ('sim-1', 'it-1', name, 'completed'))
        row = simulation.index_row(record, slot='m17-a-v1', workflow_id='w-1', version_id='wf-abc', platform_version=1)
        self.assertEqual((row['simulation_id'], row['name'], row['workflow_id'], row['version_id'], row['slot']), ('sim-1', name, 'w-1', 'wf-abc', 'm17-a-v1'))
        self.assertEqual(row['scenarios'], [{'id': 'S-1', 'pass': True, 'failed': [], 'call_id': 'call-persona-0', 'agent_version': 3, 'tools': [], 'slowest_turn_s': 0.0}])
        self.assertNotIn('messages', json.dumps(row), 'the index holds no transcript')

    def test_several_cases_in_one_simulation_keep_their_own_customer_call_and_verdict(self):
        runner = FakeRunner()
        cases = [case('S-1', 'PUB-01'), case('S-2', 'PUB-07'), case('S-3', 'PUB-13')]
        record = self.run_one(runner, cases, 'M17 | M16 | Safety | v1')
        self.assertEqual(len(runner.started), 1, 'one simulation')
        self.assertEqual(sorted(v['customer_id'] for v in runner.started[0][2].values()), ['c_PUB-01', 'c_PUB-07', 'c_PUB-13'])
        self.assertEqual([(r['id'], r['customer_id'], r['call_id']) for r in record['rows']],
                         [('S-1', 'c_PUB-01', 'call-persona-0'), ('S-2', 'c_PUB-07', 'call-persona-1'), ('S-3', 'c_PUB-13', 'call-persona-2')])
        self.assertEqual((record['total'], record['passed'], len({r['run_id'] for r in record['rows']})), (3, 3, 3))
        self.assertEqual([s['id'] for s in simulation.index_row(record)['scenarios']], ['S-1', 'S-2', 'S-3'])

    def test_two_cases_that_would_share_a_bank_customer_are_not_started_together(self):
        """The stand-in bank gives one customer per practice case. Two scenarios on one account would then write to one
        ledger, and nothing could say which of them recorded what: the simulation is refused before it starts."""
        runner = FakeRunner()
        with self.assertRaises(simulation.SharedCustomer):
            self.run_one(runner, [case('S-1', 'PUB-01'), case('S-2', 'PUB-01')])
        self.assertEqual(runner.started, [])

    def test_a_case_passes_only_when_the_ledger_and_the_judge_both_do(self):
        ledger = {'promises': [{'product_id': 'p1', 'amount': 250.0, 'date': D16}], 'calls': [{'tool': 'create_promise'}]}
        record = self.run_one(FakeRunner(ledger=ledger), [case()])
        self.assertEqual((record['passed'], record['rows'][0]['failed'], record['rows'][0]['ledger_pass']), (0, ['promises differ'], False))
        record = self.run_one(FakeRunner(outcome='fail'), [case()])
        self.assertEqual((record['passed'], record['rows'][0]['failed'], record['rows'][0]['ledger_pass']), (0, ['conversation: judge text'], True))

    def test_a_persona_is_created_once_and_again_only_when_its_text_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, cache = FakeRunner(), Path(tmp) / 'personas.json'
            first = simulation.personas(runner, [case()], cache)
            self.assertEqual(simulation.personas(runner, [case()], cache), first)
            self.assertEqual(len(runner.created), 1)
            changed = dict(case(), script='another script')
            self.assertNotEqual(simulation.personas(runner, [changed], cache), first)
            self.assertEqual(len(runner.created), 2)

    def test_the_practice_cases_of_a_brief_run_as_one_named_simulation(self):
        runner, lines = FakeRunner(), []
        cases = [dict(c, expected=dict(c['expected'], promises=[])) for c in CASES.values()]
        with tempfile.TemporaryDirectory() as tmp:
            code = simulation.run_practice_cases(runner, 'agent-1', 'B', cases, Path(tmp) / 'evaluation.json', lines.append, label='brief-b', version='wf-abc')
            saved = json.loads((Path(tmp) / 'evaluation.json').read_text(encoding='utf-8'))
        self.assertEqual(code, 0)
        self.assertEqual(runner.started[0][1], 'brief-b | B | Regression | wf-abc')
        self.assertEqual(len(runner.started[0][2]), 3, 'three personas in the one simulation')
        self.assertEqual((saved['name'], saved['passed'], saved['total']), (runner.started[0][1], 3, 3))
        self.assertTrue(any('3 / 3 passed' in line for line in lines))
        self.assertEqual(simulation.run_practice_cases(runner, 'agent-1', 'Z', cases, Path('unused'), lines.append, 'x', 'y'), 1)


class LiveScenarios(unittest.TestCase):
    def test_the_file_holds_no_account_data_of_the_kit(self):
        text = SCENARIOS.read_text(encoding='utf-8')
        for c in CASES.values():
            for value in list(c['identity'].values()) + [c['variables']['last_name']] + [p.get('card_last4') for p in c['products'] if p.get('card_last4')]:
                self.assertNotIn(str(value), text, 'kit data in the scenario file')

    def test_a_scenario_borrows_the_account_of_its_bank_case_and_its_amounts_are_filled_in(self):
        cases = {c['id']: c for c in live.load_scenarios(SCENARIOS, KIT)}
        same = cases['M16-SAMEDATE']
        self.assertEqual((same['bank_case'], same['identity'], same['variables']), ('PUB-13', CASES['PUB-13']['identity'], CASES['PUB-13']['variables']))
        self.assertEqual([p['amount'] for p in same['expected']['promises']], [p['past_due'] for p in CASES['PUB-13']['products']])
        self.assertEqual({c['agent'] for c in cases.values()}, {'a', 'b', 'm16'})
        self.assertEqual([c['id'] for c in live.load_scenarios(SCENARIOS, KIT, agent='m16', only={'M16-STEP', 'ADV-AUTH'})], ['M16-STEP'])
        for c in cases.values():
            for key in ('id', 'brief', 'family', 'title', 'script', 'expected', 'checks', 'scenario', 'expected_label', 'gate'):
                self.assertIn(key, c, c['id'])
            self.assertEqual(sorted(c['expected']), ['callbacks', 'notes', 'outcome', 'promises', 'reason', 'transfers'], c['id'])
        account = {'identity': {}, 'products': [{'past_due': 420.0}]}
        self.assertEqual(live.fill({'a': ['$p1.past_due', 'pays {p1.past_due} today']}, account), {'a': [420.0, 'pays 420.0 today']})
        with self.assertRaises(KeyError):
            live.fill('$p1.floor', account)

    def test_each_of_the_four_rule_kinds_has_a_scenario_that_meets_its_limit(self):
        gates = {c['gate'] for c in live.load_scenarios(SCENARIOS, KIT, agent='m16')}
        self.assertLessEqual({'promise.amount_step', 'promise.min_amount_by_date', 'promise.same_date', 'callback.closed'}, gates)

    def test_the_scripted_calls_of_the_memo_scenarios_end_as_the_scenarios_expect(self):
        """Offline, with the model played by hand: the forbidden call of each scenario is refused and the allowed one
        leaves exactly the expected record. A live run shows whether the phone model takes that path."""
        cases = {c['id']: c for c in live.load_scenarios(SCENARIOS, KIT, agent='m16')}
        spec = memo_spec()
        api, sim, bank = call(spec, products=CASES['PUB-07']['products'])
        self.assertIn('not a whole multiple', refusal(sim, promise(api, sim, 'p1', 250, D16)))
        self.assertTrue(recorded(promise(api, sim, 'p1', 260, D16)))
        self.assertEqual([(p['product_id'], p['amount'], p['date']) for p in bank.ledger['promises']],
                         [(p['product_id'], p['amount'], p['date']) for p in cases['M16-STEP']['expected']['promises']])
        api, sim, bank = call(spec, products=CASES['PUB-01']['products'])
        self.assertIn('needs at least', refusal(sim, promise(api, sim, 'p1', 160, D15)))
        self.assertTrue(recorded(promise(api, sim, 'p1', 160, D16)))
        self.assertEqual([(p['amount'], p['date']) for p in bank.ledger['promises']], [(p['amount'], p['date']) for p in cases['M16-EARLY']['expected']['promises']])
        api, sim, bank = call(spec, products=CASES['PUB-13']['products'])
        full = [p['past_due'] for p in CASES['PUB-13']['products']]
        self.assertTrue(recorded(promise(api, sim, 'p1', full[0], '2026-10-23')))
        self.assertIn('is already recorded', refusal(sim, promise(api, sim, 'p2', full[1], '2026-10-22')))
        self.assertTrue(recorded(promise(api, sim, 'p2', full[1], '2026-10-23')))
        self.assertEqual(sorted((p['product_id'], p['amount'], p['date']) for p in bank.ledger['promises']),
                         sorted((p['product_id'], p['amount'], p['date']) for p in cases['M16-SAMEDATE']['expected']['promises']))
        api, sim, bank = call(spec, products=CASES['PUB-01']['products'])
        self.assertTrue(recorded(promise(api, sim, 'p1', CASES['PUB-01']['products'][0]['past_due'], D14)), 'M16-HAPPY: the full amount today')

    def test_a_record_the_scenario_does_not_expect_is_found_and_filed(self):
        row = {'pass': False, 'ledger_pass': False, 'call_id': 'c', 'expected': {'promises': [{'product_id': 'p1', 'amount': 260.0, 'date': D16}]},
               'ledger': {'promises': [{'product_id': 'p1', 'amount': 250.0, 'date': D16}], 'notes': [], 'callbacks': [], 'transfers': []}}
        self.assertEqual(live.unexpected(row), ["promise ['p1', 250.0, '2026-10-16']"])
        self.assertEqual(live.suggest(row, gate='promise.amount_step'), 'runtime', 'a forbidden record with a check for it is a failed check')
        self.assertEqual(live.suggest(row, gate=''), 'conversation')
        right = dict(row, ledger={'promises': [{'product_id': 'p1', 'amount': 260, 'date': D16}]}, ledger_pass=True)
        self.assertEqual((live.unexpected(right), live.suggest(right, 'promise.amount_step')), ([], 'conversation'), 'the bank is right, the judge found a check false')
        self.assertIsNone(live.suggest(dict(right, **{'pass': True})))
        self.assertEqual(live.suggest(dict(row, ledger={}, call_id=None)), 'harness_or_judge')
        self.assertEqual(live.CLASSES, ('extraction', 'schema', 'compiler', 'runtime', 'deployment', 'conversation', 'voice_platform', 'harness_or_judge'))


class SpendingLimit(unittest.TestCase):
    class Model:
        model, max_tokens = 'claude-sonnet-5-5', 32000

        def complete(self, system, messages):
            return 'reply', {'model': self.model, 'input_tokens': 10000, 'output_tokens': 10000, 'stop_reason': 'end_turn'}, 'reply'

    def test_the_limit_holds_across_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'spend.json'
            first = live.Budget(self.Model(), 0.60, ledger)
            first.complete('system', [{'role': 'user', 'content': 'x'}])
            self.assertEqual(live.spent(ledger), (0.12, 1))
            second = live.Budget(self.Model(), 0.60, ledger)                 # another command, the same run
            self.assertEqual(len(second.usage), 1)
            with self.assertRaises(live.experiment.BudgetExceeded):          # 0.12 spent, the next call could cost 0.32: over 0.40 it is not made
                live.Budget(self.Model(), 0.40, ledger).complete('system', [{'role': 'user', 'content': 'x'}])
            second.complete('system', [{'role': 'user', 'content': 'x'}])
            self.assertEqual(live.spent(ledger), (0.24, 2))

    def test_no_command_calls_anything_without_live(self):
        for argv in (['build', '--slot', 's', '--brief', 'b.md', '--tool-spec', 'a.json', '--max-dollars', '1'], ['inspect', '--slot', 's'],
                     ['simulate', '--slot', 's', '--public', 'A'], ['dry-run', '--slot', 's', '--spec', 's.json', '--tool-spec', 'a.json']):
            with mock.patch('builtins.print') as said, mock.patch.object(live.settings, 'load_env', side_effect=AssertionError('keys were read')):
                self.assertEqual(live.main(argv), 2)
            self.assertIn('Nothing was called', said.call_args[0][0])


class WhenTheNetworkFails(unittest.TestCase):
    """Three things the first live run of M17 ran into."""

    class Flaky(FakeRunner):
        fails = 2

        def get_call(self, call_id):
            if self.fails:
                self.fails -= 1
                raise RuntimeError('Remote end closed connection without response')
            return FakeRunner.get_call(self, call_id)

    def test_a_transcript_is_read_again_and_a_missing_one_changes_no_verdict(self):
        lines = []
        call = simulation.read_call(self.Flaky(messages=[said('assistant', 'Goodbye.')]), 'c-1', lines.append, wait=0)
        self.assertEqual((len(call['messages']), len(lines)), (1, 2))
        gone = self.Flaky()
        gone.fails = 99
        self.assertEqual(simulation.read_call(gone, 'c-1', wait=0), {})
        self.assertEqual(simulation.read_call(gone, None), {}, 'a run without a call has nothing to read')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(simulation.time, 'sleep'):
            record = simulation.run(gone, 'agent-1', [case()], 'a name', Path(tmp) / 'personas.json')
        row = live.annotate(record['rows'][0], 'promise.amount_step')
        self.assertEqual((row['pass'], row['messages'], row['speech']['agent_turns'], row['blocked_calls']), (True, [], 0, []))
        self.assertEqual((live.in_brief(row)['has_transcript'], 'messages' in live.in_brief(row)), (False, False))
        self.assertEqual(simulation.spoken([said('assistant', 'Goodbye.')])['speech']['agent_turns'], 1)

    def test_a_simulation_that_cannot_be_started_is_named_and_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'agents.json').write_text(json.dumps({'s': {'workflow_id': 'w-1', 'agent_id': 'agent-1'}}), encoding='utf-8')
            runner = FakeRunner()
            runner.run_simulation = mock.Mock(side_effect=OSError('getaddrinfo failed'))
            with mock.patch.object(live.versioning, 'known', lambda slot: {'workflow_id': 'w-1', 'agent_id': 'agent-1'}), \
                 mock.patch.object(live.freya_client, 'load_runner', return_value=runner), mock.patch.object(live.settings, 'load_env'), \
                 mock.patch('builtins.print') as shown:
                code = live.main(['simulate', '--slot', 's', '--public', 'B', '--only', 'PUB-11', '--out', tmp, '--live'])
            self.assertEqual(code, 1)
            self.assertFalse((Path(tmp) / 'm17' / 'index.jsonl').exists(), 'no result is written for a simulation that did not run')
            self.assertIn('not run, to be repeated: --only PUB-11', ' '.join(str(c) for c in shown.call_args_list))

    def test_a_python_without_the_sdk_stops_before_the_meter_can_charge_a_call_that_was_never_sent(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict('sys.modules', {'anthropic': None}), mock.patch.object(live.settings, 'load_env'), \
             mock.patch('builtins.print') as shown:
            self.assertFalse(live.need_sdk())
            code = live.main(['build', '--slot', 's', '--brief', 'b.md', '--tool-spec', 'a.json', '--max-dollars', '2', '--out', tmp, '--live'])
            self.assertEqual(code, 2)
            self.assertFalse((Path(tmp) / 'm17' / 'spend.json').exists())
        self.assertIn('The Anthropic SDK is not installed', ' '.join(str(c) for c in shown.call_args_list))


class Versions(unittest.TestCase):
    def test_a_version_id_names_the_content_and_nothing_else(self):
        self.assertEqual(versioning.version_id({'a': 1, 'b': [1, 2]}), versioning.version_id({'b': [1, 2], 'a': 1}))
        self.assertNotEqual(versioning.version_id({'a': 1}), versioning.version_id({'a': 2}))
        self.assertRegex(versioning.version_id({}), r'^wf-[0-9a-f]{12}$')
        record = versioning.build_record('slot', {'unsupported': ['a rule']}, {'nodes': {}}, {'kind': 'extraction', 'strategy': 'audit', 'prompt_version': 'v2'})
        self.assertEqual(sorted(record), ['built_at', 'local_workflow_sha256', 'slot', 'source', 'spec_sha256', 'unsupported', 'version_id', 'workflow_bytes'])
        self.assertEqual((record['version_id'], record['unsupported'], record['source']['prompt_version']), (versioning.version_id({'nodes': {}}), ['a rule'], 'v2'))

    def test_an_archived_version_is_never_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = store.BuildStore(tmp, 'slot')
            folder = files.archive('wf-1', {'spec': 1}, {'workflow': 1}, {'built_at': 'first'})
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['build.json', 'spec.json', 'workflow.json'])
            files.archive('wf-1', {'spec': 2}, {'workflow': 2}, {'built_at': 'second'})
            self.assertEqual(json.loads((folder / 'build.json').read_text(encoding='utf-8')), {'built_at': 'first'})
            self.assertEqual(json.loads((folder / 'spec.json').read_text(encoding='utf-8')), {'spec': 1})
            self.assertEqual(files.archive('wf-2', {}, {}, {}).parent, folder.parent)
            self.assertEqual(folder, Path(tmp) / 'slot' / 'versions' / 'wf-1')


class WhatThePlatformStored(unittest.TestCase):
    def test_what_the_platform_adds_is_listed_and_what_it_changes_is_a_departure(self):
        sent = pipeline.build_workflow(fixture(), OPENAPI)[0]
        stored = copy.deepcopy(sent)
        stored['agent_config'] = {'ttsConfig': {'language': 'tr'}}
        stored['nodes']['contact']['interruptible'] = True
        changed, added = deployment.departures(sent, stored)
        self.assertEqual((changed, sorted(added)), ([], ['$.agent_config', '$.nodes.contact.interruptible']))
        stored['nodes']['contact']['tools'][0]['function']['parameters']['properties']['method']['default'] = 'GET'
        del stored['nodes']['negotiate']['tools'][0]['pre_process_code']
        stored['nodes']['end']['transitions'] = [{'target_node': 'contact'}]
        changed, added = deployment.departures(sent, stored)
        self.assertEqual(len(changed), 3)
        self.assertTrue(any("method.default: sent 'POST', stored 'GET'" in c for c in changed))
        self.assertTrue(any('pre_process_code: missing' in c for c in changed), 'a pre-hook that is not stored cannot block')
        self.assertTrue(any('transitions: 0 item(s) sent, 1 stored' in c for c in changed))

    def test_the_structure_a_person_checks_in_the_ui(self):
        shape = live.shape(pipeline.build_workflow(memo_spec(), OPENAPI)[0])
        self.assertEqual(shape['initial_node'], 'setup')
        self.assertEqual({n: v['type'] for n, v in shape['nodes'].items()},
                         {'setup': 'code', 'contact': 'conversation', 'get_account': 'api_request', 'account_facts': 'code', 'negotiate': 'conversation', 'end': 'end_call'})
        tools = {t['name']: t for t in shape['nodes']['negotiate']['tools']}
        self.assertEqual(sorted(tools), ['add_note', 'close_call', 'create_promise', 'transfer_to_agent'])
        self.assertEqual((tools['create_promise']['method'], tools['create_promise']['body_fields']), ('POST', ['amount', 'customer_id', 'date', 'product_id']))
        self.assertTrue(tools['create_promise']['url'].endswith('/v1/create_promise'))
        self.assertTrue(all(t['pre_hook'] and t['post_hook'] for node in ('contact', 'negotiate') for t in shape['nodes'][node]['tools']), 'every tool the model can call has both hooks')
        self.assertEqual([t['to'] for t in shape['nodes']['contact']['transitions']], ['end', 'get_account'])
        self.assertEqual(live.voice_settings({})['tts'], {'language': None, 'voiceId': None, 'model': None, 'speed': None})


def said(role, text, at=0.0):
    return {'role': role, 'message': text, 'secondsFromStart': at}


def event(name, at=0.0, **content):
    return {'role': 'system', 'message': json.dumps(dict(content, event=name)), 'secondsFromStart': at}


class Speech(unittest.TestCase):
    def test_a_complete_call_has_no_finding(self):
        found = simulation.speech_findings([said('assistant', 'Hello, am I speaking with Mr Reyes?'), said('user', 'Yes.'),
                                            said('assistant', 'Your promise is recorded. Thank you, goodbye!')])
        self.assertEqual((found['agent_turns'], found['cut_off'], found['long'], found['repeated'], found['tool_text'], found['no_farewell'], found['silent_after_tool']),
                         (2, [], [], [], [], False, 0))

    def test_what_a_transcript_can_show(self):
        long = ' '.join(['word'] * 50) + '.'
        found = simulation.speech_findings([
            said('assistant', 'I am calling about a late payment on your'), said('user', 'Sorry?'),
            said('assistant', long), said('assistant', long),
            said('assistant', "Thank you. close_call(outcome='promise')"),
            event('function_call_complete', function='create_promise', result={}), said('user', 'Hello? Are you there?'),
            event('error', message='The model returned no speakable content'),
            said('assistant', 'The payment is noted.')])
        self.assertEqual(found['cut_off'], ['I am calling about a late payment on your'])
        self.assertEqual((len(found['long']), len(found['repeated']), len(found['tool_text'])), (2, 1, 1))
        self.assertEqual((found['silent_after_tool'], found['no_farewell'], found['engine_errors']), (1, True, ['The model returned no speakable content']))
        self.assertEqual(found['longest_turn_words'], 50)

    def test_a_cut_off_customer_turn_is_counted_apart_from_the_agents(self):
        """Seen in the live run of M17: the turns that stop in the middle of a sentence are those of the simulated
        customer. They are not the agent's, and they are not counted as the agent's."""
        found = simulation.speech_findings([
            said('assistant', 'Could you tell me the last four digits of the phone number on file?'), said('user', 'The last four digits are five five'),
            said('assistant', 'Please say all four digits.'), said('user', 'Five five one two.'),
            said('assistant', 'Thank you. Goodbye.'), said('user', 'Yes, I confirm. Could you record that')])
        self.assertEqual((found['customer_turns'], found['customer_cut_off']), (3, ['The last four digits are five five', 'Yes, I confirm. Could you record that']))
        self.assertEqual((found['agent_turns'], found['cut_off']), (3, []))

    def test_every_agent_is_told_to_finish_its_sentences_and_how_to_go_on_after_an_interruption(self):
        for spec in (fixture(), memo_spec()):
            prompt = pipeline.build_workflow(spec, OPENAPI)[0]['system_prompt']
            for part in ('Keep every turn to one or two short spoken sentences', 'Finish every sentence you start',
                         'When the person talks over you, or says they did not hear or understand you',
                         'Saying it again this way is not a new offer and not a new attempt', 'After every tool result you must speak to the person or call the next tool'):
                self.assertIn(part, prompt)

    def test_the_wait_and_the_tool_trace_are_read_from_the_events(self):
        messages = [said('user', 'I will pay 250.', 10.0),
                    event('function_call_start', 12.0, function='create_promise', args={'body': {'customer_id': 'blocked_by_policy', 'amount': 250}}),
                    event('function_call_error', 12.4, function='create_promise', error='API request failed'),
                    said('assistant', 'That amount cannot be recorded.', 14.5),
                    event('function_call_start', 20.0, function='create_promise', args={'body': {'customer_id': 'c_1', 'amount': 260}}),
                    event('function_call_complete', 20.5, function='create_promise', result={'data': {'ok': True}}),
                    event('function_call_start', 21.0, function='end_call', args={})]
        self.assertEqual(simulation.slowest_turn(messages), 4.5)
        trace = simulation.tool_trace(messages)
        self.assertEqual([(t['tool'], t['blocked'], t['ended'], t['body']['amount']) for t in trace],
                         [('create_promise', True, 'error', 250), ('create_promise', False, 'answered', 260)])
        self.assertEqual(trace[0]['result'], 'API request failed')


if __name__ == '__main__':
    unittest.main()
