"""M19: the whole builder in one run (builder/run.py, builder/build.py), its .log trace, the bounded wait for a
simulation, the check of the credits between batches, and where the starter kit is.

Everything here is offline. The platform is a fake that keeps versions and exports what was saved last, the model
answers from prepared replies, and the test customers are prepared results: every "model call", "workflow" and
"verdict" in a trace written by these tests is SYNTHETIC. They show that the run does the right thing with what
it is given, and nothing about what a model or a phone agent does.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness import evaluation as harness
from benchmarks.harness.fixture_spec import spec as fixture
from builder import run as builder_run
from builder.artifacts import store
from builder.config import settings
from builder.evaluation import simulation
from builder.extraction import claude
from builder.freya import versioning
from builder.optimization import loop, policy
from builder.tracing.runlog import RunLog, redact
from tests.support import KIT, OPENAPI
from tests.test_m17 import FakeRunner, case
from tests.test_m18_loop import BEFORE, BETTER, GOOD, Evaluated, Exporting, failed, row

BRIEF_A, BRIEF_B, API_FILE = str(KIT / 'briefs' / 'brief-a.md'), str(KIT / 'briefs' / 'brief-b.md'), str(KIT / 'tools' / 'openapi.json')


class TheTrace(unittest.TestCase):
    def test_every_line_has_its_time_and_run_and_is_on_disk_at_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            shown = []
            log = RunLog(Path(tmp) / 'traces' / 'r1.log', 'r1', shown.append)
            log('run start | SYNTHETIC')
            log('model call: replay | purpose: extract agent-spec/1 from the brief | 3 s | in 10 out 20 tokens | stop end_turn')
            log('model call: replay | purpose: audit | 1 s | in 1 out 2 tokens | stop end_turn')
            log('two\nlines')
            on_disk = (Path(tmp) / 'traces' / 'r1.log').read_text(encoding='utf-8').splitlines()       # read while the run is still going
            self.assertEqual(len(on_disk), 5)
            for line in on_disk:
                self.assertRegex(line, r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} \| r1 \| ')
            self.assertIn('| LLM call 1 | replay | purpose: extract agent-spec/1 from the brief', on_disk[1])
            self.assertIn('| LLM call 2 | replay | purpose: audit', on_disk[2])
            self.assertEqual((log.model_calls, shown, log.path.suffix), (2, on_disk, '.log'))
            log.close()
            log('after the end')                        # a late line is printed and cannot fail
            self.assertEqual(len(shown), 6)

    def test_credentials_never_reach_a_trace(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'FREYA_API_KEY': 'fak_synthetic0123456789', 'ANTHROPIC_API_KEY': 'value-of-a-key-123'}):
            log = RunLog(Path(tmp) / 'r.log', 'r', None)
            log('FAILED | POST /x -> 401 with fak_synthetic0123456789 and value-of-a-key-123')
            log("headers {'Authorization': 'Bearer abcdefghijklmnop.qrstuv', 'Content-Type': 'application/json'}")
            log('api_key=sk-ant-0123456789abcdef and {"token": "tttttttttttt"} | in 100 out 200 tokens')
            text = log.path.read_text(encoding='utf-8')
            log.close()
        for secret in ('fak_synthetic0123456789', 'value-of-a-key-123', 'abcdefghijklmnop', 'sk-ant-0123456789abcdef', 'tttttttttttt'):
            self.assertNotIn(secret, text)
        self.assertIn('in 100 out 200 tokens', text, 'what is no credential stays readable')
        self.assertEqual(redact('nothing secret: workflow 729bb5d8, 3 tokens'), 'nothing secret: workflow 729bb5d8, 3 tokens')
        self.assertEqual(redact('password=hunter2hunter2 x'), 'password=<redacted> x')

    def test_a_run_never_writes_into_the_log_of_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, third = (RunLog(Path(tmp) / 'hidden-1.log', name, None) for name in ('a', 'b', 'c'))
            for log in (first, second, third):
                log('SYNTHETIC line of ' + log.run_id)
                log.close()
            self.assertEqual([p.name for p in (first.path, second.path, third.path)], ['hidden-1.log', 'hidden-1-2.log', 'hidden-1-3.log'])
            self.assertEqual(first.path.read_text(encoding='utf-8').count('\n'), 1)

    def test_a_console_that_cannot_show_a_character_does_not_end_the_run(self):
        """Seen on 2026-10-11 on a Windows console with code page 1254: printing an arrow raised UnicodeEncodeError."""
        class Narrow:
            encoding = 'cp1254'

            def __init__(self):
                self.got = []

            def write(self, text):
                text.encode('cp1254')
                self.got.append(text)

            def flush(self):
                pass
        with tempfile.TemporaryDirectory() as tmp:
            console = Narrow()
            with mock.patch.object(sys, 'stdout', console):
                log = RunLog(Path(tmp) / 'r.log', 'r')
                log('brief says \u2192 pay \u20ba500')
                log.close()
            self.assertIn('\u2192', log.path.read_text(encoding='utf-8'), 'the file has the line as it is')
            self.assertIn('\\u2192', ''.join(console.got))


class KitLike(FakeRunner):
    """A runner whose client waits as the kit's does: it asks the platform again and again until the simulation is done."""

    def __init__(self, polls_until_done=10 ** 9, **kwargs):
        FakeRunner.__init__(self, **kwargs)
        self.polls, self.asked = polls_until_done, []

    def api(self, method, path, body=None, **params):
        self.asked.append((method, path))
        if path.endswith('/iterations'):
            return {'iterations': [{'id': 'it-7'}]}
        self.polls -= 1
        return {'status': 'completed' if self.polls <= 0 else 'running'}

    def run_simulation(self, agent_id, name, variables_by_persona, max_seconds=420):
        self.started.append((agent_id, name, variables_by_persona))
        while self.api('GET', '/api/v2/simulations/sim-1')['status'] != 'completed':
            pass
        return 'sim-1', 'it-1', 'completed'


class TheBoundedWait(unittest.TestCase):
    """Found on 2026-10-11: a simulation stayed "running" on the platform and the kit waited for it without end."""

    def test_a_wait_ends_at_its_limit_and_leaves_what_is_needed_to_read_the_simulation_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, pending = KitLike(), simulation.Pending(Path(tmp) / 'p.json')
            mine = runner.api
            with self.assertRaisesRegex(simulation.TimedOut, 'may still be running on the platform'):
                simulation.run(runner, 'agent-1', [case()], 'a name', Path(tmp) / 'personas.json', pending=pending, wait_seconds=-1)
            self.assertTrue(pending.path.is_file(), 'it was started: what is known of it stays')
            self.assertEqual(runner.api, mine, 'the client is as it was')
            record = simulation.run(KitLike(polls_until_done=3), 'agent-1', [case()], 'a name', Path(tmp) / 'personas.json')
            self.assertEqual((record['status'], record['passed']), ('completed', 1), 'a simulation that finishes in time is not touched')

    def test_the_limit_is_every_call_at_its_longest_and_some_minutes_more(self):
        with mock.patch.dict(os.environ, {'FREYA_SIM_CONCURRENCY': ''}):
            self.assertEqual([simulation.wait_limit(n) for n in (1, 5, 6, 10, 18)], [720, 720, 1140, 1140, 1980])
            self.assertEqual(simulation.wait_limit(5, max_seconds=60), 360)
        with mock.patch.dict(os.environ, {'FREYA_SIM_CONCURRENCY': '2'}):
            self.assertEqual(simulation.wait_limit(5), 420 * 3 + 300)

    def test_a_stop_file_ends_the_wait_as_ctrl_c_does(self):
        """Ctrl-C does not reach a run that was started without a keyboard. A file does."""
        with tempfile.TemporaryDirectory() as tmp:
            stop, clock = Path(tmp) / 'STOP', [0.0]
            api = simulation.bounded(lambda method, path, body=None, **params: 'answered', 100, stop, now=lambda: clock[0])
            self.assertEqual(api('GET', '/x'), 'answered')
            clock[0] = 100.0
            self.assertEqual(api('GET', '/x'), 'answered', 'at the limit, not beyond it')
            stop.write_text('')
            with self.assertRaisesRegex(KeyboardInterrupt, 'stop requested'):
                api('GET', '/x')
            stop.unlink()
            clock[0] = 100.1
            with self.assertRaises(simulation.TimedOut):
                api('POST', '/api/v2/simulations', {})
            self.assertFalse(issubclass(simulation.TimedOut, (RuntimeError, OSError)), "neither of the kit's loops and no retry may catch it")
            runner = KitLike()
            stop.write_text('')
            with self.assertRaises(KeyboardInterrupt):
                simulation.run(runner, 'agent-1', [case()], 'a name', Path(tmp) / 'personas.json', stop_file=stop)

    def test_the_iteration_of_a_simulation_is_read_by_the_route_the_kit_uses(self):
        runner = KitLike()
        self.assertEqual(simulation.iteration_of(runner, 'sim-9'), 'it-7')
        self.assertEqual(runner.asked, [('GET', '/api/v2/simulations/sim-9/iterations')])
        for answer in ({'iterations': []}, {'iterations': [{'id': 'a'}, {'id': 'b'}]}, {'iterations': [{}]}, 'not a list'):
            runner.api = lambda method, path, body=None, answer=answer, **params: answer
            with self.assertRaises(ValueError):
                simulation.iteration_of(runner, 'sim-9')

    def test_the_evaluation_starts_and_waits_for_nothing_beyond_the_deadline_of_the_run(self):
        import argparse
        import time
        calls = []
        args = argparse.Namespace(suite=['public:B'], scenarios='benchmarks/live/m17.scenarios.json', kit=str(KIT), out='unused', run='x', slot='s', deadline=time.time() + 600)
        with tempfile.TemporaryDirectory() as tmp, mock.patch('builtins.print'):
            args.out = tmp
            harness.Evaluation(args, calls.append).run('wf-aaa', 'aaa')
            self.assertEqual(calls[0][-2], '--wait-seconds')
            self.assertTrue(500 < int(calls[0][-1]) <= 600)
            args.deadline = time.time() + 30
            del calls[:]
            self.assertEqual(harness.Evaluation(args, calls.append).run('wf-aaa', 'aaa'), {})
            self.assertEqual(calls, [], 'with less than a minute left no simulation is started')


class TwoBatches(Evaluated):
    """An evaluation of two simulations: PUB-11 and PUB-12 in one, PUB-13 in the other, each asked for before it starts."""

    def run(self, version_id, label, only=None, allow=None):
        self.runs.append((version_id, label, only))
        outcome = self.outcomes.pop(0)
        for batch in (['PUB-11', 'PUB-12'], ['PUB-13']):
            if allow and allow(len(batch)):
                continue
            self.keep(version_id, {cid: outcome[cid] for cid in batch})
        return self.stored(version_id)


class TheCredits(unittest.TestCase):
    """Found on 2026-10-11: five test calls cost 5.13 credits where 1.25 were projected, and the next batch would have started."""

    def setUp(self):
        from tests.test_m18_loop import Deployed
        self.base = Deployed('setUp')
        self.base.setUp()
        self.addCleanup(self.base.tmp.cleanup)
        self.base.evaluation = TwoBatches(self.base.freya)
        self.base.evaluation.keep(self.base.incumbent, BEFORE)

    def test_the_projection_is_for_a_call_that_runs_to_its_limit(self):
        self.assertGreaterEqual(loop.CREDITS_PER_CASE, 1.03, 'what such a call was seen to cost')

    def test_a_batch_is_not_started_when_the_earlier_ones_already_used_the_budget(self):
        base = self.base
        base.limits.limits['credits'] = 12.0
        record = base.step([BETTER], credits=(100.0, 100.0, 89.0, 89.0))        # before, before batch 1, before batch 2, after
        self.assertIn('rolled back at the evidence gate: no complete result for: PUB-13', record['status'])
        self.assertTrue(any('a batch of 1 call(s) is not started' in line and '11.00 credits used by this evaluation so far' in line for line in base.lines), base.lines[-6:])
        self.assertEqual((record['credits'], record.get('credits_estimated'), base.limits.state['credits'], base.published()), (11.0, None, 11.0, [1]))
        self.assertEqual(sorted(base.evaluation.stored(record['tested']['version_id'])), ['PUB-11', 'PUB-12'], 'the batch that ran keeps its results')

    def test_without_a_reading_the_earlier_batches_count_at_the_projection(self):
        base = self.base
        base.limits.limits['credits'] = 3 * loop.CREDITS_PER_CASE + 0.01

        def unreadable():
            raise OSError('getaddrinfo failed')
        base.evaluation.outcomes = [BETTER]
        record = loop.step('slot', OPENAPI, base.evaluation, base.freya, base.files, base.limits, base.audit,
                           client=claude.ReplayClient([json.dumps(GOOD)]), safety_cases=['PUB-11'], credits=unreadable, registry=base.registry, trace=base.lines.append)
        self.assertTrue(record['status'].startswith('accepted'), record['status'])
        self.assertEqual((record['credits'], record['credits_estimated']), (3 * loop.CREDITS_PER_CASE, True), 'both batches fit, and are charged at what they can have cost')

    def test_the_results_of_every_version_are_in_the_trace_case_by_case(self):
        base = self.base
        base.step([dict(BETTER, **{'PUB-13': row(True, ledger=False)})], credits=(100.0, 100.0, 99.0, 98.0))
        text = '\n'.join(base.lines)
        self.assertIn('self-test | incumbent %s | case PUB-12 | FAIL | bank ok | forbidden records 0 | judge fail' % base.incumbent, text)
        self.assertIn('self-test | incumbent %s | 2 of 3 case(s) pass | 0 with wrong bank records | 0 without a result' % base.incumbent, text)
        self.assertRegex(text, r'self-test \| candidate wf-[0-9a-f]{12} \| case PUB-13 \| FAIL \| bank WRONG \| forbidden records 0 \| judge pass \| simulation None \| platform version 2')
        self.assertRegex(text, r'self-test \| candidate wf-[0-9a-f]{12} \| 2 of 3 case\(s\) pass \| 1 with wrong bank records')


class Track(Exporting):
    """The fake platform for one slot: its workflow and agent are named after the slot, as two real workflows differ."""

    def save_workflow(self, slot, workflow, state_file):
        saved = Exporting.save_workflow(self, slot, workflow, state_file)
        state = json.loads(Path(state_file).read_text())
        state[slot].update(workflow_id='w-' + slot, agent_id='a-' + slot)
        Path(state_file).write_text(json.dumps(state))
        return dict(saved, workflow_id='w-' + slot, agent_id='a-' + slot)

    def export_workflow(self, workflow_id, path):
        Path(path).write_text(json.dumps({'workflow_id': workflow_id, 'workflow': self.last_saved}), encoding='utf-8')


class Customers(Evaluated):
    def keep(self, version_id, rows, **stamp):
        Evaluated.keep(self, version_id, rows, **dict(stamp, simulation_id='SYNTHETIC-sim'))
        for cid in rows:
            self.kept[version_id][cid]['workflow_id'] = self.workflow_id

    def run(self, version_id, label, only=None, allow=None):
        return Evaluated.run(self, version_id, label, only)


class TheWholeRun(unittest.TestCase):
    """builder/run.py from a brief to a verified export, with a replayed model, a fake platform and prepared results."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = self.root / 'agents.json'
        self.platforms, self.evaluations = {}, {}

    def provider(self, outcomes):
        test = self

        class Provider:
            @staticmethod
            def add_arguments(parser):
                parser.add_argument('--suite', action='append', default=[])

            @staticmethod
            def Evaluation(args):
                made = Customers(test.platforms[args.slot], outcomes)
                made.workflow_id = 'w-' + args.slot
                test.evaluations[args.slot] = made
                return made
        return Provider

    def run_builder(self, *argv, replies=(), outcomes=(), slot='demo-a', brief=BRIEF_A, extract=True):
        self.platforms.setdefault(slot, Track())
        self.client = claude.ReplayClient(([json.dumps(fixture(True))] if extract else []) + [json.dumps(r) for r in replies])
        with mock.patch.object(settings, 'load_env'), mock.patch('builtins.print'):
            code = builder_run.main([brief, API_FILE, '--slot', slot, '--strategy', 'single', '--out', str(self.root / 'out'), '--trace', str(self.root / 'traces' / (slot + '.log')),
                                     '--export', str(self.root / 'workflows' / (slot + '.workflow.json')), '--results', str(self.root / 'results')] + list(argv),
                                    client=self.client, platform=lambda: self.platforms[slot], evaluation=self.provider(list(outcomes)), registry=self.registry)
        self.files = store.BuildStore(self.root / 'out', slot)
        traces = sorted((self.root / 'traces').glob(slot + '*.log'), key=lambda p: (p.stat().st_mtime_ns, len(p.name)))
        self.trace = traces[-1].read_text(encoding='utf-8') if traces else ''
        reports = sorted((self.root / 'results').glob('*%s.json' % slot))
        self.result = json.loads(reports[-1].read_text(encoding='utf-8')) if reports else {}
        return code

    def exported(self, slot='demo-a'):
        return json.loads((self.root / 'workflows' / (slot + '.workflow.json')).read_text(encoding='utf-8'))

    def published(self, slot='demo-a'):
        return [v['versionNumber'] for v in self.platforms[slot].versions if v['isPublished']]

    def test_a_brief_without_test_customers_is_built_deployed_verified_and_exported(self):
        """The run of a hidden brief: nothing to test it with, and the trace says so."""
        code = self.run_builder()
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [1]))
        build = self.result['final_build']
        self.assertEqual(versioning.version_id(self.exported()['workflow']), build, 'the export is the build that is published')
        self.assertEqual((self.exported()['workflow_id'], self.result['workflow_id'], self.result['agent_id']), ('w-demo-a', 'w-demo-a', 'a-demo-a'))
        self.assertEqual(self.files.read(store.BUILD)['source']['kind'], 'extraction', 'the agent comes out of the extraction, not out of a file')
        self.assertEqual(self.client.replies, [], 'the model was asked for the spec')
        for part in ('| run start | run ', '| inputs | brief %s | tool API %s' % (BRIEF_A, API_FILE), '| configuration | extraction single', '| LLM call 1 | replay | purpose: extract agent-spec/1 from the brief',
                     '| freya | workflow w-demo-a | agent a-demo-a | workflow name "demo-a ..." | build %s | platform version 1 | 6 steps | 9 tools | created' % build,
                     '| self-test | none: no suite of test customers was named for this brief', '| final | build %s | platform version 1 (pv-1) is published, read back from Freya' % build,
                     '| run end | status success |'):
            self.assertIn(part, self.trace)
        self.assertIn('No self-test was run', (self.root / 'results' / (self.result['run_id'] + '.md')).read_text(encoding='utf-8'))
        self.assertEqual(self.result['builder_commit'], versioning.builder_commit())

    def test_the_whole_pipeline_with_an_accepted_candidate(self):
        code = self.run_builder('--suite', 'public:A', replies=[GOOD], outcomes=[BEFORE, BETTER])
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [2]))
        initial, candidate = self.result['versions']
        self.assertEqual((initial['role'], initial['kept'], candidate['role'], candidate['kept']), ('initial version', False, 'candidate 1 (accepted)', True))
        self.assertEqual((self.result['final_build'], versioning.version_id(self.exported()['workflow'])), (candidate['version_id'], candidate['version_id']))
        self.assertEqual(self.result['steps_taken'][-1], 'stopped: no case fails; nothing to improve', 'the run went on from the accepted candidate until nothing failed')
        self.assertEqual((self.evaluations['demo-a'].runs[0][0], self.evaluations['demo-a'].runs[0][2]), (initial['version_id'], ['PUB-11', 'PUB-12', 'PUB-13']), 'the first version was tested first')
        for part in ('| LLM call 1 |', '| LLM call 2 | replay | purpose: propose one change for the failed case PUB-12', 'self-test | incumbent %s | 2 of 3 case(s) pass' % initial['version_id'],
                     'self-test | candidate %s | 3 of 3 case(s) pass' % candidate['version_id'], '| optimization | step 1 | case PUB-12 (conversation) | accepted: now passing: PUB-12',
                     '| final | self-tests of the published version: 3 of 3 case(s) pass | scope: public:A, one run each'):
            self.assertIn(part, self.trace)
        report = (self.root / 'results' / (self.result['run_id'] + '.md')).read_text(encoding='utf-8')
        self.assertIn('## initial version: %s' % initial['version_id'], report)
        self.assertIn('2 of 3 case(s) pass, one run each. Failed: PUB-12 (fail: judge)', report)
        self.assertIn('## candidate 1 (accepted): %s' % candidate['version_id'], report)

    def test_a_rejected_candidate_is_never_what_is_exported(self):
        """After a rollback the platform's own export is the candidate that was saved last. What is handed out is the
        export kept with the build the platform reports as published."""
        worse = dict(BETTER, **{'PUB-13': row(True, ledger=False)})
        code = self.run_builder('--suite', 'public:A', '--rounds', '1', replies=[GOOD], outcomes=[BEFORE, worse])
        self.assertEqual((code, self.result['status'], self.published()), (0, 'success', [1]))
        initial, candidate = self.result['versions']
        self.assertEqual((candidate['role'], candidate['kept'], initial['kept']), ('candidate 1 (rejected, rolled back)', False, True))
        self.assertIn('rollback at the safety gate', candidate['decision'])
        self.assertEqual(versioning.version_id(self.platforms['demo-a'].last_saved), candidate['version_id'], 'the platform would export the candidate')
        self.assertEqual((self.result['final_build'], versioning.version_id(self.exported()['workflow'])), (initial['version_id'], initial['version_id']))
        self.assertEqual(self.files.read(store.BUILD)['version_id'], initial['version_id'])
        for part in ('| optimization | step 1 | case PUB-12 (conversation) | rolled back at the safety gate', '| final | build %s | platform version 1 (pv-1) is published' % initial['version_id'],
                     '| final | self-tests of the published version: 2 of 3 case(s) pass | failed: PUB-12'):
            self.assertIn(part, self.trace)

    def test_two_tracks_share_nothing(self):
        self.run_builder('--suite', 'public:A', '--no-optimize', outcomes=[BETTER], slot='demo-brief-a')
        first = dict(self.result)
        self.run_builder('--amend', BRIEF_B, '--suite', 'public:B', '--no-optimize', outcomes=[BETTER], slot='demo-brief-a-plus-b')
        second = self.result
        for key in ('run_id', 'workflow_id', 'agent_id', 'trace', 'export'):
            self.assertNotEqual(first[key], second[key], key)
        self.assertEqual((second['briefs'], second['workflow_id']), ([BRIEF_A, BRIEF_B], 'w-demo-brief-a-plus-b'))
        self.assertEqual(sorted(p.name for p in (self.root / 'out').iterdir()), ['demo-brief-a', 'demo-brief-a-plus-b'], 'one folder per track')
        self.assertIn('brief-b.md', self.client.seen[0][1][0]['content'], 'the model was given both documents, in order')
        self.assertLess(self.client.seen[0][1][0]['content'].index('name="brief-a.md"'), self.client.seen[0][1][0]['content'].index('name="brief-b.md"'))
        self.assertIn('| optimization | step 1 | no candidate | stopped: no case fails', self.trace)

    def test_a_run_that_must_make_its_own_workflow_does_not_take_over_an_existing_one(self):
        self.assertEqual(self.run_builder('--new'), 0)
        code = self.run_builder('--new')
        self.assertEqual((code, self.result['status'], self.published(), len(self.platforms['demo-a'].versions)), (1, 'incomplete', [1], 1))
        self.assertIn('INCOMPLETE | the slot demo-a already has the workflow w-demo-a', self.trace)
        self.assertEqual(len(list((self.root / 'traces').glob('demo-a*.log'))), 2, 'each run has a trace of its own')

    def test_an_interrupt_leaves_a_complete_trace_and_the_incumbent(self):
        code = self.run_builder('--suite', 'public:A', replies=[GOOD], outcomes=[BEFORE, KeyboardInterrupt()])
        self.assertEqual((code, self.result['status'], self.published()), (130, 'interrupted', [1]))
        self.assertIn('| INTERRUPTED |', self.trace)
        self.assertIn('interrupted (KeyboardInterrupt); rolled back at the evidence gate', self.trace)
        self.assertRegex(self.trace.splitlines()[-1], r'\| run end \| status interrupted \|')
        self.assertFalse((self.root / 'workflows' / 'demo-a.workflow.json').exists(), 'an interrupted run hands out no export')

    def test_an_export_that_is_not_the_published_build_is_not_handed_out(self):
        self.assertEqual(self.run_builder(), 0)
        build = self.result['final_build']
        kept = self.files.version(build) / store.EXPORT
        kept.write_text(json.dumps({'workflow_id': 'w-demo-a', 'workflow': {'nodes': {}}}), encoding='utf-8')
        (self.root / 'workflows' / 'demo-a.workflow.json').unlink()
        code = self.run_builder('--spec', str(self.files.path(store.SPEC)), extract=False)
        self.assertEqual((code, self.result['status']), (1, 'incomplete'))
        self.assertIn('INCOMPLETE | the export kept with build %s is not that build on this workflow' % build, self.trace)
        self.assertFalse((self.root / 'workflows' / 'demo-a.workflow.json').exists())

    def test_a_model_that_fails_is_in_the_trace_and_nothing_is_deployed(self):
        class Down:
            model = 'synthetic-model'

            def complete(self, system, messages):
                raise RuntimeError('529 overloaded, header Authorization: Bearer abcdefghijklmnopqrstuvwx')
        self.platforms['demo-a'] = Track()
        with mock.patch.object(settings, 'load_env'), mock.patch('builtins.print'):
            code = builder_run.main([BRIEF_A, API_FILE, '--slot', 'demo-a', '--out', str(self.root / 'out'), '--trace', str(self.root / 'traces' / 'demo-a.log')], client=Down(),
                                    platform=lambda: self.platforms['demo-a'], evaluation=self.provider([]), registry=self.registry)
        trace = (self.root / 'traces' / 'demo-a.log').read_text(encoding='utf-8')
        self.assertEqual((code, self.platforms['demo-a'].versions), (1, []))
        self.assertIn('| LLM call failed | synthetic-model | RuntimeError: 529 overloaded', trace)
        self.assertIn('| run end | status failed |', trace)
        self.assertNotIn('abcdefghijklmnop', trace)

    def test_no_self_test_is_started_that_could_run_into_the_time_kept_for_the_end(self):
        code = self.run_builder('--suite', 'public:A', '--deadline-minutes', '5', '--reserve-minutes', '4', outcomes=[BEFORE])
        self.assertEqual((code, self.result['status'], self.result['untested']), (0, 'success', 'no time left before the deadline'))
        self.assertEqual(self.evaluations, {}, 'no customer was simulated')

    def test_the_final_version_can_be_tested_again_and_each_run_is_reported(self):
        code = self.run_builder('--suite', 'public:A', '--no-optimize', '--repeat', '3', outcomes=[BETTER, BETTER, dict(BETTER, **{'PUB-12': failed()})])
        self.assertEqual((code, len(self.result['repetitions'])), (0, 3))
        self.assertEqual([builder_run.score(rep)[0] for rep in self.result['repetitions']], ['3 of 3', '3 of 3', '2 of 3'])
        self.assertIn('Pass in all 3 repetitions: 2 of 3.', (self.root / 'results' / (self.result['run_id'] + '.md')).read_text(encoding='utf-8'))


class TheCommandAndTheKit(unittest.TestCase):
    def test_the_command_of_the_submission_form_runs_from_anywhere(self):
        """python3 builder/build.py {brief_path} {tool_api_path}: started as a script, from another folder."""
        build = settings.ROOT / 'builder' / 'build.py'
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, '-B', str(build), BRIEF_A if Path(BRIEF_A).is_absolute() else str(settings.ROOT / BRIEF_A), str(settings.KIT / 'tools' / 'openapi.json'), '--plan'],
                                  capture_output=True, text=True, cwd=tmp)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('extract (model, strategy audit) the spec', done.stdout)
            self.assertIn('Nothing was called.', done.stdout)
            spec = Path(tmp) / 'spec.json'
            spec.write_text(json.dumps(fixture()), encoding='utf-8')
            done = subprocess.run([sys.executable, '-B', str(build), str(settings.KIT / 'briefs' / 'brief-a.md'), str(settings.KIT / 'tools' / 'openapi.json'), '--spec', str(spec), '--no-deploy',
                                   '--out', str(Path(tmp) / 'out'), '--slot', 'Hidden-1'], capture_output=True, text=True, cwd=tmp)
            self.assertEqual(done.returncode, 1, 'built and not deployed is not a finished agent')
            self.assertIn('| build | wf-', done.stdout)
            self.assertIn('| run end | status incomplete |', done.stdout, 'the trace is what the command prints, so that `| tee traces/x.log` keeps it')
            logs = list((Path(tmp) / 'out' / 'hidden-1' / 'traces').glob('*.log'))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].read_text(encoding='utf-8').strip().splitlines(), done.stdout.strip().splitlines())
        help_text = subprocess.run([sys.executable, '-B', '-m', 'builder', 'run', '--help'], capture_output=True, text=True, cwd=str(settings.ROOT))
        self.assertEqual(help_text.returncode, 0, help_text.stderr)
        for flag in ('--amend', '--export', '--trace', '--deadline-minutes', '--max-credits', '--max-dollars', '--max-candidates', '--repeat', '--suite', '--new'):
            self.assertIn(flag, help_text.stdout)

    def test_the_starter_kit_is_found_where_it_is_now_and_nothing_names_the_old_place(self):
        self.assertEqual((settings.KIT, KIT), (settings.ROOT / 'freya_starter_kit', settings.ROOT / 'freya_starter_kit'))
        for name in ('freya.py', 'tools/run_cases.py', 'tools/openapi.json', 'public-test-set.json', 'briefs/brief-a.md', 'briefs/brief-b.md'):
            self.assertTrue((settings.KIT / name).is_file(), name)
        self.assertTrue(settings.KIT.is_absolute(), 'found from the place of the code, not from the folder a command is started in')
        old = 'local' + '_freya'
        for folder in ('builder', 'benchmarks/harness', 'tests'):
            for path in (settings.ROOT / folder).rglob('*.py'):
                self.assertNotIn(old, path.read_text(encoding='utf-8'), path.name)
        for name in ('README.md', 'ARCHITECTURE.md', 'benchmarks/README.md'):
            self.assertNotIn(old + '/starter_kit', (settings.ROOT / name).read_text(encoding='utf-8'), name)
        ignored = (settings.ROOT / '.gitignore').read_text(encoding='utf-8')
        for line in ('freya_starter_kit/env.sh', 'freya_starter_kit/.personas.json', 'freya_starter_kit/results/', '/demo/*/traces/*', '!/demo/*/*/.gitkeep'):
            self.assertIn(line, ignored.splitlines(), line)

    def test_the_submission_form_is_written_only_from_what_is_there(self):
        from builder import manifest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            made, problems = manifest.form('team', 'https://example.org/repo', ['hidden-1', 'hidden-2'], root, 'c' * 40)
            self.assertEqual(sorted(made), ['commit', 'repo', 'run_command', 'team', 'traces', 'workflows'], 'the six fields of the form, and no other')
            self.assertEqual((made['run_command'], made['workflows']['hidden-2'], made['traces']['hidden-1']),
                             ('python3 builder/build.py {brief_path} {tool_api_path}', 'submission/hidden-2.workflow.json', 'traces/hidden-1.log'))
            self.assertEqual(len(problems), 4, 'nothing is there yet')
            for folder in ('submission', 'traces'):
                (root / folder).mkdir()
            for name in ('hidden-1', 'hidden-2'):
                (root / 'submission' / (name + '.workflow.json')).write_text(json.dumps({'workflow': {'nodes': {}}}))
                (root / 'traces' / (name + '.log')).write_text('t | r | run start | run r | slot x | builder commit %s\nt | r | run end | status success | 9 s\n' % ('c' * 40))
            self.assertEqual(manifest.form('team', 'u', ['hidden-1', 'hidden-2'], root, 'c' * 40)[1], [])
            self.assertIn('was not written by a run of commit dddddddddddd', manifest.form('team', 'u', ['hidden-1'], root, 'd' * 40)[1][0])
            self.assertEqual(manifest.form('team', 'u', ['hidden-1', 'hidden-2'], root)[0]['commit'], 'c' * 40, 'the commit of a form is the one that built the agents')
            (root / 'traces' / 'hidden-2.log').write_text('t | r | run end | status incomplete | 9 s\n')
            self.assertEqual(manifest.form('team', 'u', ['hidden-2'], root, 'c' * 40)[1], ['traces/hidden-2.log is not the trace of a run that ended in success'])
        with mock.patch('builtins.print'):
            self.assertEqual(manifest.main(['--team', 't', '--repo', 'u']), 1, 'no hidden brief was built: there is no form to send')

    def test_the_folders_of_the_submission_and_the_demos_are_there_and_empty(self):
        self.assertEqual(sorted(p.name for p in (settings.ROOT / 'submission').iterdir() if p.name != '.gitkeep' and not p.name.startswith('hidden-')), [])
        for track in ('brief-a', 'brief-a-plus-b'):
            for part in ('traces', 'workflows', 'results'):
                self.assertTrue((settings.ROOT / 'demo' / track / part / '.gitkeep').is_file(), track + '/' + part)


if __name__ == '__main__':
    unittest.main()
