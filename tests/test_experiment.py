"""benchmarks/harness/experiment.py and optimize_prompt.py: scoring, cost limits, the tiers, and GEPA with scripted models."""
import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness import experiment, optimize_prompt, packages, rules
from builder.extraction import claude

ORGS = {'Northwind': 'c', 'Harbor Lending': 'd', 'Meridian': 'e', 'Calder': 'f', 'Brightwater': 'g'}


class Scripted:
    """Answers every extraction with the reference spec of the package whose brief it is shown."""

    def __init__(self, change=None, reply=None):
        self.seen, self.model, self.change, self.reply = [], 'claude-sonnet-5-5', change, reply

    def complete(self, system, messages):
        self.seen.append((system, messages))
        if self.reply is not None:
            text = self.reply
        else:
            pid = next(p for org, p in ORGS.items() if org in messages[0]['content'])
            spec = copy.deepcopy(packages.key(pid, packages.references(pid)[-1])['spec'])     # a test may read a key; the code under test may not
            if self.change:
                self.change(spec)
            text = json.dumps(spec)
        return text, {'model': self.model, 'input_tokens': 9000, 'output_tokens': 12000, 'stop_reason': 'end_turn'}, text


class Runs(unittest.TestCase):
    def test_one_run_gives_the_spec_its_score_and_what_it_cost(self):
        client = Scripted()
        row, report = experiment.run_one('c', 'single', client)
        self.assertEqual((row['valid'], row['calls'], row['repair_turns'], row['input_tokens'], row['output_tokens']), (True, 1, 0, 9000, 12000))
        self.assertEqual(row['dollars'], round(9000 * 2 / 1e6 + 12000 * 10 / 1e6, 4))
        self.assertEqual((row['score'], row['critical_recall'], row['correct_policy']), (1.0, 1.0, 1.0))
        self.assertEqual(row['layers'].get('extraction'), None)
        self.assertEqual(client.seen[0][0], claude.SYSTEM)

    def test_a_wrong_limit_costs_points_and_twice_for_a_critical_rule(self):
        row, report = experiment.run_one('c', 'single', Scripted(lambda s: s['identity'].update(max_wrong=3)))
        self.assertLess(row['score'], 1.0)
        self.assertLess(row['critical_recall'], 1.0)
        self.assertEqual(row['layers']['extraction'], 1)

    def test_what_the_schema_cannot_carry_is_not_held_against_an_extraction(self):
        declare = lambda s: s.update(unsupported=['plan_active and days_late decide whether a promise may be recorded at all'])
        row, report = experiment.run_one('c', 'single', Scripted(declare), reference='v1')
        unsupported = [r for r in report['rules'] if r['fit'] == 'unsupported']
        self.assertEqual(len(unsupported), 2)
        self.assertEqual((row['reference'], row['score'], row['critical_recall']), ('v1', 1.0, 1.0), 'reported as unsupported is all an extraction can do with such a rule')
        self.assertEqual(report['summary']['by_layer']['schema'], 3, 'and the rule itself stays the schema layer, not the extraction')

    def test_no_valid_spec_is_a_result_with_score_zero(self):
        client = Scripted(reply='{}')
        row, report = experiment.run_one('c', 'single', client)
        self.assertEqual((row['valid'], row['score'], row['calls'], row['repair_turns'], report), (False, 0.0, 3, 2, None))
        self.assertIn('no valid spec after the repair turns', row['failure'])

    def test_replaced_sections_reach_the_model_and_name_the_run(self):
        sections = {'reading': 'Read the documents in order and apply every later change to the earlier text.'}
        client = Scripted()
        row, _ = experiment.run_one('c', 'single', client, sections)
        self.assertEqual(client.seen[0][0], claude.system_prompt(sections))
        self.assertNotEqual(row['prompt'], experiment.claude_hash())

    def test_results_are_written_when_asked(self):
        folder = tempfile.mkdtemp()
        try:
            client = Scripted()
            client.complete = lambda system, messages, real=client.complete: ('[]', {'model': 'm', 'input_tokens': 1, 'output_tokens': 1, 'stop_reason': 'end_turn'}, '[]') if system == claude.AUDIT else real(system, messages)
            row, _ = experiment.run_one('c', 'audit', client, out=folder, label='r1')
            self.assertEqual(row['calls'], 2)
            self.assertEqual(sorted(p.name for p in (Path(folder) / 'c-audit-r1').iterdir()), ['extraction.json', 'result.json', 'rules.json', 'spec.json', 'trace.txt'])
            self.assertIn('purpose: audit the high-risk content', (Path(folder) / 'c-audit-r1' / 'trace.txt').read_text(encoding='utf-8'))
        finally:
            shutil.rmtree(folder)


class Limits(unittest.TestCase):
    def test_a_run_stops_before_the_call_that_would_pass_the_limit(self):
        inner = Scripted()
        meter = experiment.Metered(inner, max_dollars=0.20)        # one call costs 0.138
        experiment.run_one('c', 'single', meter)
        experiment.run_one('d', 'single', meter)
        with self.assertRaises(experiment.BudgetExceeded):
            experiment.run_one('e', 'single', meter)
        self.assertEqual(len(inner.seen), 2)
        meter = experiment.Metered(Scripted(), max_calls=1)
        experiment.run_one('c', 'single', meter)
        with self.assertRaises(experiment.BudgetExceeded):
            experiment.run_one('c', 'single', meter)

    def test_the_limit_holds_even_if_every_call_runs_to_the_output_cap(self):
        class Greedy(Scripted):
            max_tokens = 32000

            def complete(self, system, messages):
                text, used, content = Scripted.complete(self, system, messages)
                return text, dict(used, input_tokens=20000, output_tokens=32000), content

        inner = Greedy()
        meter = experiment.Metered(inner, max_dollars=1.0)
        with self.assertRaises(experiment.BudgetExceeded):
            for pid in 'cde':
                experiment.run_one(pid, 'single', meter)
        self.assertEqual(len(inner.seen), 2, 'the third call could have passed the limit, so it was not made')
        self.assertLessEqual(experiment.dollars(meter.usage, meter.model), 1.0)
        tokens_in, tokens_out = meter.most(*inner.seen[0])
        self.assertEqual(tokens_out, 32000)
        self.assertEqual(tokens_in, (len(inner.seen[0][0]) + len(inner.seen[0][1][0]['content'])) // 2 + 500, 'two characters are counted as one token')
        meter = experiment.Metered(Greedy(), max_dollars=0.30)
        with self.assertRaises(experiment.BudgetExceeded):
            experiment.run_one('c', 'single', meter)
        self.assertEqual(meter.client.seen, [], 'a limit that does not cover one whole call makes none')

    def test_a_call_that_fails_is_charged_as_if_it_had_cost_the_most(self):
        class Failing(Scripted):
            max_tokens = 32000

            def complete(self, system, messages):
                self.seen.append((system, messages))
                raise RuntimeError('the model stopped at max_tokens=32000')

        meter = experiment.Metered(Failing(), max_dollars=1.0)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                experiment.run_one('c', 'single', meter)
        with self.assertRaises(experiment.BudgetExceeded):
            experiment.run_one('c', 'single', meter)
        self.assertEqual((len(meter.client.seen), meter.usage[0]['assumed']), (2, True))
        self.assertGreater(experiment.dollars(meter.usage, meter.model), 0.64)

    def test_prices_follow_the_model_the_api_names(self):
        self.assertEqual(experiment.price('claude-sonnet-5-5-20260901'), (2.0, 10.0))
        self.assertEqual(experiment.price('something-new'), experiment.DEAREST)
        self.assertEqual(experiment.dollars([{'model': 'claude-opus-5-5', 'input_tokens': 1000000, 'output_tokens': 1000000}]), 24.0)
        self.assertFalse(claude.AnthropicClient('claude-sonnet-5-5', fallback=False).fallback)

    def test_the_command_calls_nothing_without_being_told_to(self):
        with mock.patch.object(claude.AnthropicClient, 'complete', side_effect=AssertionError('network')), mock.patch('builtins.print'):
            self.assertEqual(experiment.main(['--plan', '--strategies', 'single', 'audit', 'double', '--repeat', '3']), 0)
            self.assertEqual(experiment.main(['--packages', 'c']), 2)
            self.assertEqual(optimize_prompt.main(['--plan']), 0)
            self.assertEqual(optimize_prompt.main([]), 2)
            with self.assertRaises(PermissionError):
                optimize_prompt.main(['--packages', 'c', 'f', '--plan'])

    def test_the_planning_figures_add_up(self):
        rows, totals = experiment.estimate(['single', 'audit', 'double'], 3, 1)
        by = {r['strategy']: r for r in rows}
        self.assertEqual((by['single']['calls'], by['single']['calls_max']), (3.0, 9))
        self.assertEqual(by['single']['dollars'], round(3 * (9000 * 2 + 12000 * 10) / 1e6, 2))
        self.assertGreater(by['double']['dollars'], by['audit']['dollars'])
        self.assertGreater(totals['dollars_max'], totals['dollars'])


class Tiers(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.log = mock.patch.object(rules, 'EXPOSURE', self.folder / 'exposure.jsonl')
        self.log.start()

    def tearDown(self):
        self.log.stop()
        shutil.rmtree(self.folder)

    def test_a_selection_or_final_package_is_refused_before_any_model_sees_it(self):
        for pid in ('f', 'g'):
            client = Scripted()
            with self.assertRaises(PermissionError):
                experiment.run_one(pid, 'single', client)
            self.assertEqual(client.seen, [], 'the brief of a holdout package went to no model')
        client = Scripted()
        with self.assertRaises(PermissionError):
            experiment.run_one('g', 'single', client, purpose='final run')        # the candidates are not frozen
        self.assertEqual((client.seen, rules.exposures()), ([], []))

    def test_an_authorised_run_is_on_the_record_before_the_model_is_called(self):
        seen_at_call = []

        class Watching(Scripted):
            def complete(self, system, messages):
                seen_at_call.append(len(rules.exposures()))
                return Scripted.complete(self, system, messages)

        row, _ = experiment.run_one('f', 'single', Watching(), purpose='selection batch', config={'commit': 'abc'})
        self.assertEqual(seen_at_call, [1])
        entry = rules.exposures()[0]
        self.assertEqual((entry['package'], entry['label'], entry['purpose'], entry['config']['strategy'], entry['config']['commit']),
                         ('f', 'model-selection evidence', 'selection batch', 'single', 'abc'))
        self.assertEqual(row['tier'], 'selection')
        experiment.run_one('g', 'single', Scripted(), purpose='final run', candidates='candidates.json')
        for pid, strategy in (('g', 'audit'), ('g', 'single'), ('f', 'single')):
            client = Scripted()
            with self.assertRaises(PermissionError):
                experiment.run_one(pid, strategy, client, purpose='once more', candidates='candidates.json')
            self.assertEqual(client.seen, [], 'a second look, even at the same configuration, reaches no model')
        self.assertEqual([e['package'] for e in rules.exposures()], ['f', 'g'])

    def test_an_optimisation_takes_development_packages_only(self):
        self.assertEqual(optimize_prompt.development(['c', 'd', 'e']), ['c', 'd', 'e'])
        for ids in (['f'], ['c', 'g'], ['c', 'd', 'e', 'f']):
            with self.assertRaises(PermissionError):
                optimize_prompt.development(ids)
            with self.assertRaises(PermissionError):
                optimize_prompt.Adapter(Scripted(), ids)
        client = Scripted()
        adapter = optimize_prompt.Adapter(client, ['c'])
        if importlib.util.find_spec('gepa'):
            with self.assertRaises(PermissionError):
                adapter.evaluate(['f'], dict(claude.SECTIONS))
        self.assertEqual((client.seen, rules.exposures()), ([], []))


class Candidates(unittest.TestCase):
    forbidden = optimize_prompt.brief_values(['c', 'd', 'e'])

    def test_the_seed_is_a_candidate_and_these_are_not(self):
        seed = dict(claude.SECTIONS)
        self.assertEqual(optimize_prompt.guard(seed, self.forbidden), '')
        self.assertIn('exactly the sections', optimize_prompt.guard({k: v for k, v in seed.items() if k != 'reading'}, self.forbidden))
        self.assertIn('exactly the sections', optimize_prompt.guard(dict(seed, output='{}'), self.forbidden))
        self.assertIn('is empty', optimize_prompt.guard(dict(seed, reading='  '), self.forbidden))
        self.assertIn('grew beyond', optimize_prompt.guard(dict(seed, reading=seed['reading'] * 4), self.forbidden))
        for value in ('arrears_team', 'Northwind Credit Union', 'plan_active', '2026-10-19', 'Your promise is on file.'):
            self.assertIn(value, self.forbidden)
            refused = optimize_prompt.guard(dict(seed, structuring=seed['structuring'] + ' For example ' + value + '.'), self.forbidden)
            self.assertIn('a value of a development brief', refused, value)

    def test_values_the_prompt_already_names_are_not_forbidden(self):
        for value in self.forbidden:
            self.assertNotIn(value.casefold(), claude.SYSTEM.casefold())
        self.assertNotIn('create_promise', self.forbidden)

    def test_feedback_names_only_what_the_extraction_got_wrong(self):
        row, report = experiment.run_one('c', 'single', Scripted(lambda s: s['identity'].update(max_wrong=3)))
        text = optimize_prompt.feedback(report, packages.key('c')['rules'])
        self.assertIn('C-05', text)
        self.assertIn('Two wrong answers in total end the check', text)
        for rule in ('C-08', 'C-11', 'C-12'):
            self.assertNotIn(rule + ' (', text, 'a rule the schema cannot carry, or that was right, is no feedback for a prompt')
        self.assertEqual(optimize_prompt.feedback(None, []), 'No valid specification was produced.')
        _, clean = experiment.run_one('c', 'single', Scripted())
        self.assertEqual(optimize_prompt.feedback(clean, packages.key('c')['rules']), 'Every rule that could be judged was extracted correctly.')

        dropped = {'checks': [], 'rules': [{'id': 'X-1', 'layer': 'schema', 'extraction': 'unsupported by schema: silent'},
                                           {'id': 'X-2', 'layer': 'schema', 'extraction': 'unsupported by schema: declared'}]}
        self.assertEqual(optimize_prompt.feedback(dropped, []).split(': ')[-1], 'X-1', 'a rule dropped without a word is named; a declared one is not')


@unittest.skipUnless(importlib.util.find_spec('gepa'), 'the gepa development dependency is not installed')
class Gepa(unittest.TestCase):
    def test_a_whole_optimisation_with_scripted_models_stays_inside_its_limits(self):
        folder = Path(tempfile.mkdtemp())
        proposals = []

        def reflect(prompt):
            proposals.append(prompt if isinstance(prompt, str) else json.dumps(prompt))
            if len(proposals) == 1:
                return '```\nSend escalations to arrears_team as Northwind Credit Union does.\n```'       # copies a brief: must be refused
            return '```\n' + claude.SECTIONS['reading'] + '\n- Apply every amendment before you write a single field.\n```'

        # The scripted extractor gets one limit wrong unless the prompt carries the sentence a reflection adds.
        class Learner(Scripted):
            def complete(self, system, messages):
                self.change = None if 'Apply every amendment' in system else (lambda s: s['identity'].update(max_wrong=9))
                return Scripted.complete(self, system, messages)

        client = experiment.Metered(Learner(), max_calls=40)
        read = set()
        real_key, real_inputs = packages.key, packages.inputs

        def key(pid, *args, **more):
            read.add(('key', pid))
            return real_key(pid, *args, **more)

        def inputs(pid):
            read.add(('inputs', pid))
            return real_inputs(pid)

        try:
            with mock.patch.object(rules, 'EXPOSURE', folder / 'exposure.jsonl'), mock.patch.object(packages, 'key', key), mock.patch.object(packages, 'inputs', inputs):
                best, result = optimize_prompt.optimize(client, reflect, ['c', 'd'], max_metric_calls=14, minutes=5, run_dir=str(folder / 'run'))
                self.assertEqual(rules.exposures(), [], 'no selection or final package was touched')
            self.assertEqual({pid for _, pid in read}, {'c', 'd'}, 'neither the documents nor the key of any other package were opened')
            for prompt in proposals:
                for word in ('Calder', 'Brightwater'):
                    self.assertNotIn(word, prompt, 'what a reflection is shown comes from the development packages')
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.assertLessEqual(len(client.usage), 14 + 2, 'the number of extractions respects the limit')
        self.assertGreaterEqual(len(proposals), 1)
        self.assertEqual(sorted(best), sorted(claude.MUTABLE))
        self.assertEqual(optimize_prompt.guard(best, optimize_prompt.brief_values(['c', 'd'])), '')
        self.assertTrue(claude.system_prompt(best).startswith(claude.FIXED))
        for system, messages in client.client.seen:
            self.assertTrue(system.startswith(claude.FIXED), 'every prompt that was tried keeps the definition of agent-spec/1')
            self.assertNotIn('Calder', messages[0]['content'])
            self.assertNotIn('Brightwater', messages[0]['content'])
        scores = list(result.val_aggregate_scores)
        self.assertGreaterEqual(max(scores), scores[0])
        if len(proposals) > 1:
            self.assertIn('Apply every amendment', best['reading'] + best['rules_writing'] + best['structuring'])


class References(unittest.TestCase):
    def test_the_frozen_packages_and_the_later_references_match_their_manifests(self):
        self.assertEqual(packages.verify(), [])
        self.assertEqual(packages.verify_references(), [])
        self.assertEqual([packages.references(pid) for pid in 'cdefg'], [['v1', 'v2']] * 3 + [['v1']] * 2)
        for pid in 'fg':
            with self.assertRaises(FileNotFoundError):
                packages.key(pid, 'v2')
        for pid in 'cde':
            old, new = packages.key(pid), packages.key(pid, 'v2')
            self.assertEqual((old['rules'], old['checks']), (new['rules'], new['checks']), 'only the spec and the fit table have a second version')
            self.assertNotEqual(old['spec'], new['spec'])
            self.assertEqual(rules.key_problems(pid, 'v2'), [])

    def test_a_later_reference_for_a_holdout_package_is_never_used(self):
        folder = Path(tempfile.mkdtemp())
        try:
            for pid in 'fg':
                (folder / 'v2' / pid).mkdir(parents=True)
                (folder / 'v2' / pid / 'reference.spec.json').write_text('{}', encoding='utf-8')
            with mock.patch.object(packages, 'REFERENCES', folder):
                self.assertEqual([packages.references(pid) for pid in 'fg'], [['v1'], ['v1']])
                self.assertEqual(len([line for line in packages.verify_references() if 'not allowed' in line]), 2)
        finally:
            shutil.rmtree(folder)

    def test_the_nine_rules_count_against_the_new_reference_and_not_against_the_old(self):
        changed = {'c': ['C-04', 'C-08', 'C-11'], 'd': ['D-06', 'D-11', 'D-17'], 'e': ['E-10', 'E-16', 'E-18']}
        for pid, ids in changed.items():
            old, new = packages.key(pid)['spec'], packages.key(pid, 'v2')['spec']
            own = rules.evaluate(pid, new, 'v2')
            self.assertEqual([r['id'] for r in own['rules'] if r['fit'] == 'unsupported'], [])
            self.assertTrue(all(str(r['extraction']).startswith(('correct', 'not evaluated')) for r in own['rules']))
            behind = rules.evaluate(pid, old, 'v2')
            self.assertEqual(sorted(r['id'] for r in behind['rules'] if r['layer'] == 'extraction'), ids, 'an extraction that leaves them out is now told so')
            self.assertEqual(experiment.score(rules.evaluate(pid, new, 'v1'))['score'], experiment.score(rules.evaluate(pid, old, 'v1'))['score'],
                             'against the old reference encoding them made no difference: that is why there is a new one')
        partial = {r['id']: r['end_to_end'] for pid in 'de' for r in rules.evaluate(pid, None, 'v2')['rules'] if r['id'] in ('D-11', 'E-18')}
        self.assertEqual(partial, {'D-11': 'partial', 'E-18': 'partial'}, 'extracted correctly is not the same as enforced in full')


class Scoring(unittest.TestCase):
    """No way of leaving a rule out may score better than writing it down."""

    def scored(self, pid, change=None, reference='v2'):
        spec = copy.deepcopy(packages.key(pid, reference)['spec'])
        if change:
            change(spec)
        report = rules.evaluate(pid, spec, reference)
        return experiment.score(report), report

    def test_leaving_out_a_rule_the_schema_carries_costs_points(self):
        def no_floor(s):
            del s['account']['limits']['earliest_date']

        def no_eligibility(s):
            del s['account']['limits']['promise_allowed']

        def no_order(s):
            del s['promise']['resolve_first']

        def no_transfer_limit(s):
            del s['transfer']['max_transfers']

        for pid, change, rule in (('c', no_floor, 'C-11'), ('c', no_eligibility, 'C-08'), ('d', no_order, 'D-06'), ('e', no_transfer_limit, 'E-16')):
            full, _ = self.scored(pid)
            less, report = self.scored(pid, change)
            self.assertEqual(full['score'], 1.0)
            self.assertLess(less['score'], full['score'], rule)
            self.assertEqual(less['weight'], full['weight'], 'the rule still counts: leaving it out does not shrink what is asked')
            self.assertIn(rule, less['critical_missed'])
            self.assertLess(less['critical_recall'], 1.0)

    def test_calling_a_supported_rule_unsupported_does_not_buy_it_back(self):
        def give_up(s):
            del s['account']['limits']['promise_allowed']
            s['unsupported'] = ['A promise only when plan_active is false and days_late is 60 or less: no field for this.']

        def just_leave_out(s):
            del s['account']['limits']['promise_allowed']

        left_out, _ = self.scored('c', just_leave_out)
        gave_up, report = self.scored('c', give_up)
        self.assertEqual(gave_up['score'], left_out['score'], 'declared or not, a rule that is not encoded scores nothing')
        self.assertEqual((gave_up['wrongly_unsupported'], left_out['wrongly_unsupported']), (['C-08'], []))
        self.assertIn('C-08', optimize_prompt.feedback(report, packages.key('c')['rules']).split('although a structured field carries them: ')[1])
        encoded_and_declared, _ = self.scored('c', lambda s: s.update(unsupported=['plan_active, days_late']))
        self.assertEqual((encoded_and_declared['score'], encoded_and_declared['wrongly_unsupported']), (1.0, []))

    def test_keeping_quiet_about_a_rule_the_schema_cannot_carry_costs_points(self):
        # Against the first reference package C has such rules; C-08 has words the key can look for.
        declared, _ = self.scored('c', lambda s: s.update(unsupported=['plan_active and days_late decide whether a promise is allowed']), 'v1')
        in_text, _ = self.scored('c', None, 'v1')
        self.assertEqual(declared['score'], 1.0)
        self.assertLess(in_text['score'], declared['score'])
        self.assertEqual(in_text['weight'], declared['weight'])
        report = {'rules': [dict(id='X-1', critical=True, extraction='unsupported by schema: silent'), dict(id='X-2', critical=False, extraction='correct'),
                            dict(id='X-3', critical=False, extraction='unsupported by schema: not detectable (the key names no anchor)'),
                            dict(id='X-4', critical=True, extraction='not evaluated'), dict(id='X-5', critical=False, extraction='something new')],
                  'summary': {'extraction': {'accuracy': 1.0, 'critical_recall': None, 'incorrect_policy_rate': 0.0, 'unsupported_declared': 0, 'unsupported_rules': 2, 'judged': 1}}}
        s = experiment.score(report)
        self.assertEqual((s['score'], s['weight'], s['critical_recall'], s['critical_missed'], s['unsupported_silent']), (0.25, 4.0, 0.0, ['X-1'], ['X-1']),
                         'a verdict the score does not know counts as wrong, never as absent')

    def test_no_mutation_of_a_reference_scores_above_it_and_the_weight_never_moves(self):
        for pid in 'cde':
            k = packages.key(pid)
            base = experiment.score(rules.evaluate(pid, k['spec']))
            self.assertTrue(k['mutations'])
            for m in k['mutations']:
                got = experiment.score(rules.evaluate(pid, rules.mutate(k['spec'], m['ops'])))
                self.assertLess(got['score'], base['score'], '%s %s' % (pid, m['id']))
                self.assertEqual(got['weight'], base['weight'], '%s %s' % (pid, m['id']))


class Latency(unittest.TestCase):
    def test_a_run_reports_how_long_it_took_and_where_the_time_went(self):
        class Slow(Scripted):
            def complete(self, system, messages):
                import time
                time.sleep(0.25)
                return Scripted.complete(self, system, messages)

        client = Slow()
        client.complete = lambda system, messages, real=client.complete: (lambda r: ('[]', r[1], '[]') if system == claude.AUDIT else r)(real(system, messages))
        row, _ = experiment.run_one('c', 'audit', client)
        self.assertEqual(row['calls'], 2)
        self.assertGreaterEqual(row['seconds'], 0.5)
        self.assertGreaterEqual(row['model_seconds'], 0.4)
        self.assertLessEqual(row['model_seconds'], row['seconds'] + 0.2)
        self.assertGreaterEqual(row['slowest_call_seconds'], 0.2)
        self.assertGreater(row['evaluation_seconds'], 0.0)
        self.assertRegex(row['started_at'], r'^\d{4}-\d{2}-\d{2}T')
        summary = experiment.summarise([row, dict(row, seconds=9.0)])[0]
        self.assertEqual((summary['seconds_max'], summary['runs']), (9.0, 2))
        self.assertEqual(summary['seconds_median'], round((row['seconds'] + 9.0) / 2, 2))


class Batch(unittest.TestCase):
    """One selection batch: all frozen candidates, logged first, evaluated last, never repeated."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.log = mock.patch.object(rules, 'EXPOSURE', self.folder / 'exposure.jsonl')
        self.log.start()
        (self.folder / 'better.json').write_text(json.dumps({'reading': 'Read every document, the latest first, and apply every change it makes to the earlier ones.'}), encoding='utf-8')
        self.file = self.folder / 'candidates.json'
        self.file.write_text(json.dumps({'candidates': [{'name': 'p0-single', 'strategy': 'single', 'repeat': 2},
                                                        {'name': 'p1-single', 'strategy': 'single', 'sections': 'better.json'}]}), encoding='utf-8')
        self.candidates = experiment.load_candidates(self.file)
        self.out = self.folder / 'out'

    def tearDown(self):
        self.log.stop()
        shutil.rmtree(self.folder)

    def run_batch(self, client, **more):
        return experiment.run_batch('f', self.candidates, client, 'selection batch', str(self.out), 'commit-1', **more)

    def events(self):
        return [(e['package'], e['event']) for e in rules.exposures()]

    def test_the_whole_batch_is_one_entry_written_before_the_first_call(self):
        seen = []

        class Watching(Scripted):
            def complete(self, system, messages):
                seen.append((self.keys_read, [e['event'] for e in rules.exposures()]))
                return Scripted.complete(self, system, messages)

        client = Watching()
        client.keys_read = 0
        real = rules.evaluate

        def evaluate(*args, **more):
            client.keys_read += 1
            return real(*args, **more)

        with mock.patch.object(rules, 'evaluate', evaluate):
            rows, summary = self.run_batch(client)
        self.assertEqual(seen, [(0, ['started'])] * 3, 'logged before the first call; no key read until the last extraction is back')
        self.assertEqual(self.events(), [('f', 'started'), ('f', 'completed')])
        started = rules.exposures()[0]
        self.assertEqual((started['label'], started['purpose'], started['candidates']['commit'], started['candidates']['runs']), ('model-selection evidence', 'selection batch', 'commit-1', 3))
        self.assertEqual([c['name'] for c in started['candidates']['candidates']], ['p0-single', 'p1-single'])
        self.assertEqual([(r['candidate'], r['label'], r['reference']) for r in rows], [('p0-single', 'r1', 'v1'), ('p0-single', 'r2', 'v1'), ('p1-single', 'r1', 'v1')])
        self.assertEqual({r['prompt'] for r in rows}, {experiment.claude_hash(), experiment.claude_hash(self.candidates[1]['sections'])})
        self.assertEqual([s['candidate'] for s in summary['summary']], ['p0-single', 'p1-single'])
        self.assertEqual(client.seen[2][0], claude.system_prompt(self.candidates[1]['sections']))
        self.assertTrue((Path(summary['folder']) / 'summary.json').exists())

    def test_a_second_batch_is_refused_and_reaches_no_model(self):
        self.run_batch(Scripted())
        for more in ({}, {'resume': True}):
            client = Scripted()
            with self.assertRaises(PermissionError):
                self.run_batch(client, **more)
            self.assertEqual(client.seen, [])
        other = [dict(self.candidates[0], name='p0-audit', strategy='audit', repeat=1)]
        client = Scripted()
        with self.assertRaises(PermissionError):
            experiment.run_batch('f', other, client, 'another look', str(self.out), 'commit-1')
        self.assertEqual((client.seen, self.events()), ([], [('f', 'started'), ('f', 'completed')]))
        with self.assertRaises(PermissionError):
            experiment.run_one('f', 'single', client, purpose='a single run after the batch')
        self.assertEqual(client.seen, [])
        rows, _ = experiment.run_batch('f', other, self.answers_audit(), 'after a fix', str(self.out), 'commit-2', relabel='post-exposure')
        self.assertEqual(rules.exposures()[2]['label'], 'post-exposure', 'a further look is possible only under another name')

    def answers_audit(self):
        client = Scripted()
        client.complete = lambda system, messages, real=client.complete: (lambda r: ('[]', r[1], '[]') if system == claude.AUDIT else r)(real(system, messages))
        return client

    def test_an_interrupted_batch_evaluates_nothing_and_continues_without_repeating(self):
        inner = Scripted()
        meter = experiment.Metered(inner, max_calls=2)
        real, judged = rules.evaluate, []
        with mock.patch.object(rules, 'evaluate', lambda *a, **k: judged.append(a) or real(*a, **k)):
            rows, state = self.run_batch(meter)
            self.assertEqual((rows, state['state'], state['runs_done'], state['runs_planned'], judged), (None, 'open', 2, 3, []))
            self.assertEqual(self.events(), [('f', 'started'), ('f', 'interrupted')])
            self.assertFalse(list(self.out.rglob('result.json')), 'no score exists for a batch that is not complete')
            # Not from the start, not with other candidates, not with other code.
            for attempt in (lambda c: self.run_batch(c), lambda c: experiment.run_batch('f', self.candidates[:1], c, 'x', str(self.out), 'commit-1', resume=True),
                            lambda c: experiment.run_batch('f', self.candidates, c, 'x', str(self.out), 'commit-2', resume=True)):
                client = Scripted()
                with self.assertRaises(PermissionError):
                    attempt(client)
                self.assertEqual(client.seen, [])
            again = Scripted()
            rows, summary = self.run_batch(again, resume=True)
        self.assertEqual((len(again.seen), len(rows), len(judged)), (1, 3, 3), 'the two runs that were made are not made again')
        self.assertEqual(self.events(), [('f', 'started'), ('f', 'interrupted'), ('f', 'resumed'), ('f', 'completed')])
        self.assertEqual(sum(e['event'] == 'started' for e in rules.exposures()), 1, 'one exposure, however many attempts it took')

    def test_results_that_were_made_cannot_be_thrown_away_and_made_again(self):
        self.run_batch(experiment.Metered(Scripted(), max_calls=2))
        shutil.rmtree(self.out)
        client = Scripted()
        with self.assertRaises(PermissionError):
            self.run_batch(client, resume=True)
        self.assertEqual(client.seen, [])

    def test_a_batch_that_cannot_finish_inside_its_limit_does_not_start(self):
        client = Scripted()
        with self.assertRaises(experiment.BudgetExceeded):
            self.run_batch(client, max_dollars=0.20)
        self.assertEqual((client.seen, rules.exposures()), ([], []), 'refused before the log and before any model')

    def test_an_extraction_without_a_valid_spec_is_a_result_and_is_not_retried(self):
        client = Scripted(reply='{}')
        rows, _ = experiment.run_batch('f', self.candidates[1:], client, 'selection batch', str(self.out), 'commit-1')
        self.assertEqual((len(rows), rows[0]['valid'], rows[0]['score'], len(client.seen)), (1, False, 0.0, 3))
        self.assertEqual(self.events()[-1], ('f', 'completed'))

    def test_candidates_are_frozen_with_their_text_and_checked_before_anything_runs(self):
        first = experiment.batch_id('f', self.candidates, 'm', 'commit-1')
        (self.folder / 'better.json').write_text(json.dumps({'reading': 'Another text for the reading section, long enough to be one.'}), encoding='utf-8')
        self.assertNotEqual(experiment.batch_id('f', experiment.load_candidates(self.file), 'm', 'commit-1'), first, 'an edited sections file is another batch')
        self.assertNotEqual(experiment.batch_id('f', self.candidates, 'm', 'commit-2'), first)
        self.assertNotEqual(experiment.batch_id('f', self.candidates, 'other-model', 'commit-1'), first)
        for bad in ([], [{'name': 'a', 'strategy': 'triple'}], [{'name': 'a', 'strategy': 'single'}, {'name': 'a', 'strategy': 'audit'}],
                    [{'name': 'a', 'strategy': 'single'}, {'name': 'b', 'strategy': 'single'}], [{'name': 'a', 'strategy': 'single', 'repeat': 0}],
                    [{'name': 'a', 'strategy': 'single', 'best_of': 5}]):
            self.file.write_text(json.dumps({'candidates': bad}), encoding='utf-8')
            with self.assertRaises(ValueError):
                experiment.load_candidates(self.file)
        (self.folder / 'schema.json').write_text(json.dumps({'output': 'x'}), encoding='utf-8')
        self.file.write_text(json.dumps({'candidates': [{'name': 'a', 'strategy': 'single', 'sections': 'schema.json'}]}), encoding='utf-8')
        with self.assertRaises(ValueError):
            experiment.load_candidates(self.file)
        with self.assertRaises(PermissionError):
            experiment.run_batch('c', self.candidates, Scripted(), 'x', str(self.out), 'commit-1')

    def test_the_command_runs_a_holdout_package_only_as_a_batch(self):
        with mock.patch.object(claude.AnthropicClient, 'complete', side_effect=AssertionError('network')), mock.patch('builtins.print'), mock.patch('sys.stderr'):
            for argv in (['--packages', 'f', '--live', '--max-dollars', '5'], ['--packages', 'c', 'g', '--plan'], ['--batch', 'c', '--candidates', str(self.file), '--purpose', 'x'],
                         ['--batch', 'f', '--purpose', 'x'], ['--batch', 'f', '--candidates', str(self.file)]):
                with self.assertRaises(SystemExit):
                    experiment.main(argv)
            self.assertEqual(experiment.main(['--batch', 'f', '--candidates', str(self.file), '--purpose', 'x', '--plan']), 0)
            self.assertEqual(experiment.main(['--batch', 'f', '--candidates', str(self.file), '--purpose', 'x']), 2)
        self.assertEqual(rules.exposures(), [], 'a plan and a refused command log nothing')


if __name__ == '__main__':
    unittest.main()
