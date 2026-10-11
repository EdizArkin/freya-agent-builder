"""What is kept when a live run is cut short, and how it is read later: the pending record of a simulation, its
recovery into an ordinary record, reads that are tried again, and the files of a slot after a rollback.

Nothing here calls Freya or the bank. The runner is the fake of tests/test_m17.py. What a real platform answers
to the request that creates a simulation was not looked at for these tests: the pending record notes whatever
ids an answer carries, and a recovery is given the two ids it needs by the person who runs it.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.harness import experiment, live
from builder.artifacts import store
from builder.config import settings
from builder.evaluation import simulation
from builder.freya import client as freya_client
from builder.tracing.trace import Trace
from tests.test_m17 import FakeRunner, case

KNOWN = {'workflow_id': 'w-1', 'agent_id': 'agent-1'}


class Later(FakeRunner):
    """A runner in another process than the one that started the simulation: it knows nothing but what it is given."""

    def __init__(self, personas, **kwargs):
        FakeRunner.__init__(self, **kwargs)
        self.personas, self.asked = list(personas), []

    def http(self, path, body=None):
        self.asked.append(path)
        return FakeRunner.http(self, path, body)

    def run_simulation(self, *args, **kwargs):
        raise AssertionError('a recovery starts nothing')

    def create_personas(self, personas):
        raise AssertionError('a recovery creates nothing')

    def get_iteration(self, sim_id, iteration_id):
        self.asked.append((sim_id, iteration_id))
        return {'runs': [{'id': 'run-' + p, 'personaId': p, 'outcome': self.outcome, 'reason': 'judge text', 'callId': 'call-' + p, 'agentVersionNumber': 2, 'turnCount': 6} for p in self.personas]}


class ThePendingRecord(unittest.TestCase):
    def test_what_is_known_of_a_simulation_is_on_disk_before_it_is_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = simulation.Pending(Path(tmp) / 'pending' / 'a.json', about={'slot': 's', 'version_id': 'wf-abc'})
            runner, seen = FakeRunner(), []
            start = runner.run_simulation
            runner.run_simulation = lambda *a, **k: seen.append(json.loads(pending.path.read_text(encoding='utf-8'))) or start(*a, **k)
            record = simulation.run(runner, 'agent-1', [case('S-1', 'PUB-01'), case('S-2', 'PUB-07')], 'M19 | A | Safety | abc', Path(tmp) / 'personas.json', pending=pending)
            kept = seen[0]
            self.assertEqual((kept['name'], kept['agent_id'], kept['about']), ('M19 | A | Safety | abc', 'agent-1', {'slot': 's', 'version_id': 'wf-abc'}))
            self.assertEqual(kept['cases'], [{'id': 'S-1', 'bank_case': 'PUB-01', 'persona_id': 'persona-0', 'customer_id': 'c_PUB-01'},
                                             {'id': 'S-2', 'bank_case': 'PUB-07', 'persona_id': 'persona-1', 'customer_id': 'c_PUB-07'}])
            self.assertEqual([r['customer_id'] for r in record['rows']], ['c_PUB-01', 'c_PUB-07'])
            self.assertTrue(pending.path.is_file(), 'the caller clears it, once the record is on disk')
            pending.clear()
            pending.clear()
            self.assertFalse(pending.path.exists())

    def test_it_stays_when_the_simulation_does_not_come_back_and_is_not_written_for_one_that_never_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending, runner = simulation.Pending(Path(tmp) / 'p.json'), FakeRunner()
            runner.run_simulation = mock.Mock(side_effect=OSError('getaddrinfo failed'))
            with self.assertRaises(OSError):
                simulation.run(runner, 'agent-1', [case()], 'a name', Path(tmp) / 'personas.json', pending=pending)
            self.assertEqual(json.loads(pending.path.read_text(encoding='utf-8'))['cases'][0]['customer_id'], 'c_PUB-01')
            never = simulation.Pending(Path(tmp) / 'q.json')
            with self.assertRaises(simulation.SharedCustomer):
                simulation.run(FakeRunner(), 'agent-1', [case('S-1', 'PUB-01'), case('S-2', 'PUB-01')], 'a name', Path(tmp) / 'personas.json', pending=never)
            self.assertFalse(never.path.exists(), 'nothing was started, so nothing is pending')

    def test_the_ids_the_platform_answers_with_are_noted_and_reads_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending = simulation.Pending(Path(tmp) / 'p.json')
            pending.write(name='n')
            answers = {'POST': {'id': 'sim-9', 'iteration': {'id': 'it-3', 'status': 'running'}, 'name': 'n', 'apiKey': 'not an id'}, 'GET': {'id': 'x'}}
            api = pending.watch(lambda method, path, body=None, **params: answers[method])
            self.assertEqual((api('POST', '/api/v2/simulations', {'a': 1}), api('GET', '/api/v2/simulations/sim-9')), (answers['POST'], answers['GET']))
            kept = json.loads(pending.path.read_text(encoding='utf-8'))
            self.assertEqual(kept['requests'], [{'method': 'POST', 'path': '/api/v2/simulations', 'ids': {'id': 'sim-9', 'iteration.id': 'it-3'}}])
            self.assertNotIn('not an id', pending.path.read_text(encoding='utf-8'), 'only ids are kept of an answer')
            self.assertEqual(pending.watch(lambda method, path, body=None, **params: 'not a dict')('POST', '/x'), 'not a dict', 'an answer it cannot read changes nothing')


class TheRecovery(unittest.TestCase):
    def pending(self):
        return {'name': 'M19 | A | Safety | abc', 'agent_id': 'agent-1', 'started_at': '2026-10-11T11:45:00+0300',
                'cases': [{'id': 'S-1', 'bank_case': 'PUB-01', 'persona_id': 'persona-7', 'customer_id': 'c_then_1'}, {'id': 'S-2', 'bank_case': 'PUB-07', 'persona_id': 'persona-8', 'customer_id': 'c_then_2'}]}

    def test_a_finished_simulation_is_read_with_the_customers_it_was_started_with(self):
        """The bank gives a new customer for every session. The records of a simulation are under the customers of
        that day, and only the pending record knows them."""
        runner = Later(['persona-7', 'persona-8'])
        record = simulation.recover(runner, self.pending(), [case('S-1', 'PUB-01'), case('S-2', 'PUB-07')], 'sim-9', 'it-3')
        self.assertEqual(runner.asked, [('sim-9', 'it-3'), '/v1/practice/ledger/c_then_1', '/v1/practice/ledger/c_then_2'], 'no new bank session is opened')
        self.assertEqual([(r['id'], r['customer_id'], r['call_id'], r['agent_version'], r['pass']) for r in record['rows']],
                         [('S-1', 'c_then_1', 'call-persona-7', 2, True), ('S-2', 'c_then_2', 'call-persona-8', 2, True)])
        self.assertEqual((record['simulation_id'], record['iteration_id'], record['name'], record['status'], record['recovered'], record['started_at']),
                         ('sim-9', 'it-3', 'M19 | A | Safety | abc', 'recovered', True, '2026-10-11T11:45:00+0300'))

    def test_what_cannot_be_recovered_is_no_result(self):
        with self.assertRaisesRegex(ValueError, 'does not hold the case'):
            simulation.recover(Later([]), self.pending(), [case('S-3', 'PUB-13')], 'sim-9', 'it-3')
        with self.assertRaises(ValueError):
            simulation.recover(Later([]), self.pending(), [], 'sim-9', 'it-3')
        record = simulation.recover(Later(['persona-7']), self.pending(), [case('S-1', 'PUB-01'), case('S-2', 'PUB-07')], 'sim-9', 'it-3')
        lost = record['rows'][1]
        self.assertEqual((lost['pass'], lost['judge_outcome'], lost['failed']), (False, None, ['conversation: no verdict']), 'a case the platform has no run for has no verdict, and fails')
        gone = Later(['persona-7', 'persona-8'])
        gone.get_iteration = mock.Mock(side_effect=RuntimeError('GET /api/v2/simulations/sim-9 -> 404'))
        with self.assertRaises(RuntimeError):
            simulation.recover(gone, self.pending(), [case('S-1', 'PUB-01'), case('S-2', 'PUB-07')], 'sim-9', 'it-3')


class TheCommands(unittest.TestCase):
    """live.py simulate and recover, with the runner replaced."""

    def command(self, tmp, runner, *argv):
        with mock.patch.object(live.versioning, 'known', lambda slot: dict(KNOWN)), mock.patch.object(live.freya_client, 'load_runner', return_value=runner), \
             mock.patch.object(live.settings, 'load_env'), mock.patch('builtins.print') as shown:
            code = live.main(list(argv) + ['--run', 'M19', '--out', str(tmp)])
        return code, '\n'.join(' '.join(str(a) for a in c.args) for c in shown.call_args_list)

    def lines(self, tmp):
        index = Path(tmp) / 'm19' / 'index.jsonl'
        return [json.loads(line) for line in index.read_text(encoding='utf-8').splitlines()] if index.is_file() else []

    def test_a_simulation_that_ran_leaves_a_record_and_nothing_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            store.BuildStore(tmp, 's').write_json(store.BUILD, {'version_id': 'wf-abc'})
            store.BuildStore(tmp, 's').write_json(store.DEPLOYMENT, {'platform_version': 3})
            code, said = self.command(tmp, FakeRunner(), 'simulate', '--slot', 's', '--public', 'B', '--only', 'PUB-11', '--version', 'abc', '--live')
            line = self.lines(tmp)[0]
            self.assertEqual((line['slot'], line['version_id'], line['platform_version'], line['label'], line['workflow_id']), ('s', 'wf-abc', 3, 'abc', 'w-1'))
            self.assertEqual(line['scenarios'][0]['unexpected_records'], [])
            self.assertEqual(list((Path(tmp) / 'm19' / 'pending').glob('*.json')), [])
            self.assertEqual((Path(tmp) / 's' / 'versions' / 'wf-abc' / 'results.jsonl').read_text(encoding='utf-8').count('\n'), 1, 'the same line, kept with the version it tested')

    def test_a_simulation_that_was_cut_short_is_recovered_once_and_for_the_version_it_ran_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = store.BuildStore(tmp, 's')
            files.write_json(store.BUILD, {'version_id': 'wf-candidate'})
            files.write_json(store.DEPLOYMENT, {'platform_version': 2})
            runner = FakeRunner()
            runner.run_simulation = mock.Mock(side_effect=RuntimeError('GET /api/v2/simulations/sim-9 -> 504'))
            code, said = self.command(tmp, runner, 'simulate', '--slot', 's', '--public', 'B', '--only', 'PUB-11,PUB-12', '--version', 'cand', '--live')
            self.assertEqual((code, self.lines(tmp)), (1, []), 'a simulation without a result is never counted')
            self.assertIn('it may have run on Freya; kept for `recover`', said)
            waiting = list((Path(tmp) / 'm19' / 'pending').glob('*.json'))
            self.assertEqual(len(waiting), 1)
            kept = json.loads(waiting[0].read_text(encoding='utf-8'))
            self.assertEqual((kept['about']['version_id'], kept['about']['platform_version'], kept['source']['public'], [c['id'] for c in kept['cases']]), ('wf-candidate', 2, 'B', ['PUB-11', 'PUB-12']))

            # Meanwhile the candidate was rolled back and the files of the slot are the incumbent's again.
            files.write_json(store.BUILD, {'version_id': 'wf-incumbent'})
            files.write_json(store.DEPLOYMENT, {'platform_version': 1})
            code, said = self.command(tmp, FakeRunner(), 'recover')
            self.assertEqual(code, 0)
            self.assertIn('M19 | B | Regression | cand | started', said)
            self.assertIn('1 pending. To read one: recover --name', said)
            self.assertEqual(self.lines(tmp), [], 'listing reads nothing and writes nothing')
            code, said = self.command(tmp, FakeRunner(), 'recover', '--simulation-id', 'sim-9', '--iteration-id', 'it-3')
            self.assertEqual(code, 2, 'reading the platform and the bank needs --live')

            later = Later([c['persona_id'] for c in kept['cases']])
            code, said = self.command(tmp, later, 'recover', '--simulation-id', 'sim-9', '--iteration-id', 'it-3', '--live')
            self.assertEqual(code, 0, said)
            line = self.lines(tmp)[0]
            self.assertEqual((line['simulation_id'], line['version_id'], line['platform_version'], line['label'], line['status'], [s['id'] for s in line['scenarios']]),
                             ('sim-9', 'wf-candidate', 2, 'cand', 'recovered', ['PUB-11', 'PUB-12']), 'stamped with the version it ran on, not with what the slot holds now')
            self.assertEqual(later.asked[1:], ['/v1/practice/ledger/' + c['customer_id'] for c in kept['cases']])
            self.assertTrue((Path(tmp) / 's' / 'versions' / 'wf-candidate' / 'results.jsonl').is_file())
            self.assertFalse((Path(tmp) / 's' / 'versions' / 'wf-incumbent').exists())
            self.assertEqual(list((Path(tmp) / 'm19' / 'pending').glob('*.json')), [])
            code, said = self.command(tmp, later, 'recover', '--simulation-id', 'sim-9', '--iteration-id', 'it-3', '--live')
            self.assertEqual((code, len(self.lines(tmp))), (0, 1), 'nothing is pending any more, and nothing is written twice')
            self.assertIn('nothing is pending', said)

    def test_a_simulation_that_already_has_a_record_is_not_recorded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            store.BuildStore(tmp, 's').write_json(store.BUILD, {'version_id': 'wf-abc'})
            self.command(tmp, FakeRunner(), 'simulate', '--slot', 's', '--public', 'B', '--only', 'PUB-11', '--version', 'abc', '--live')
            pending = simulation.Pending(Path(tmp) / 'm19' / 'pending' / 'left.json', about={'slot': 's', 'version_id': 'wf-abc'}, source={'public': 'B'}, gates={})
            pending.write(name='left over', agent_id='agent-1', started_at='t', cases=[{'id': 'PUB-11', 'bank_case': 'PUB-11', 'persona_id': 'persona-0', 'customer_id': 'c_x'}])
            code, said = self.command(tmp, Later(['persona-0']), 'recover', '--simulation-id', 'sim-1', '--iteration-id', 'it-1', '--live')
            self.assertEqual((code, len(self.lines(tmp))), (1, 1))
            self.assertIn('already has a record', said)
            self.assertTrue(pending.path.is_file())


class ReadsAreTriedAgain(unittest.TestCase):
    def calls(self, failures, method='GET', tries=4):
        made, left = [], list(failures)

        def api(method, path, body=None, **params):
            made.append((method, path))
            if left:
                raise left.pop(0)
            return {'ok': True}
        return freya_client.patient(api, tries=tries, sleep=lambda seconds: made.append(seconds)), made

    def test_a_read_is_repeated_on_a_gateway_error_or_a_failed_connection(self):
        for failure in (RuntimeError('GET /api/v2/simulations/x -> 504: gateway'), RuntimeError('GET /x -> 502'), OSError('getaddrinfo failed'), ConnectionResetError('reset')):
            api, made = self.calls([failure, failure])
            self.assertEqual(api('GET', '/x'), {'ok': True})
            self.assertEqual(made, [('GET', '/x'), 6.0, ('GET', '/x'), 6.0, ('GET', '/x')])

    def test_nothing_else_is_repeated(self):
        for failure, method in ((RuntimeError('POST /api/v2/simulations -> 504'), 'POST'), (OSError('reset'), 'POST'), (RuntimeError('GET /x -> 404'), 'GET'), (RuntimeError('GET /x -> 401'), 'GET'),
                                (ValueError('not json'), 'GET')):
            api, made = self.calls([failure])
            with self.assertRaises(type(failure)):
                api(method, '/x')
            self.assertEqual(made, [(method, '/x')], 'a request that changes something is never sent twice, and a real refusal is not asked again')
        api, made = self.calls([OSError('down')] * 9, tries=3)
        with self.assertRaises(OSError):
            api('GET', '/x')
        self.assertEqual(len([m for m in made if m == ('GET', '/x')]), 3)

    def test_the_client_can_be_loaded_with_patient_reads_and_the_balance_has_one_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'freya.py').write_text('CALLS = []\ndef api(method, path, body=None, **params):\n    CALLS.append((method, path))\n    return {"creditsLeft": 4.5}\n'
                                               'def workspace_id():\n    return "ws-1"\ndef inside():\n    return api("GET", "/inside")\n')
            plain, patient = freya_client.load(tmp), freya_client.load(tmp, patiently=True)
            self.assertEqual(freya_client.credits(plain), {'creditsLeft': 4.5})
            self.assertEqual(plain.CALLS, [('GET', '/api/v2/workspaces/ws-1/credits')])
            self.assertEqual((patient.api.__name__, plain.api.__name__), ('call', 'api'))
            patient.inside()
            self.assertEqual(patient.CALLS, [('GET', '/inside')], "the kit's own functions go through the wrapped call too")
        self.assertIs(live.patient, freya_client.patient)


class FilesOfASlot(unittest.TestCase):
    def test_a_version_comes_back_from_its_archive_and_only_from_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = store.BuildStore(tmp, 's')
            files.archive('wf-one', {'spec': 1}, {'workflow': 1}, {'version_id': 'wf-one'})
            files.archive('wf-two', {'spec': 2}, {'workflow': 2}, {'version_id': 'wf-two'})
            for name, data in ((store.SPEC, {'spec': 2}), (store.WORKFLOW, {'workflow': 2}), (store.BUILD, {'version_id': 'wf-two'}), (store.EXPORT, {'export': 2})):
                files.write_json(name, data)
            self.assertEqual(files.keep('wf-two', store.EXPORT).name, store.EXPORT)
            files.write_json(store.EXPORT, {'export': 'changed later'})
            files.keep('wf-two', store.EXPORT)
            self.assertEqual(files.read(store.EXPORT, 'wf-two'), {'export': 2}, 'what is kept with a version is written once')
            done = files.restore('wf-one', {'operation': 'rollback'})
            self.assertEqual(done[:3], ['restored spec.json', 'restored workflow.json', 'restored build.json'])
            self.assertEqual((files.read(store.SPEC), files.read(store.BUILD), files.read(store.DEPLOYMENT)), ({'spec': 1}, {'version_id': 'wf-one'}, {'operation': 'rollback'}))
            self.assertFalse(files.path(store.EXPORT).exists())
            self.assertEqual(files.read('exported.not-wf-one.workflow.json'), {'export': 'changed later'}, 'an export of another version is moved aside, not deleted and not left in place')
            self.assertEqual(files.restore('wf-one'), [], 'a slot that is in line is left alone')
            self.assertEqual(files.restore('wf-two')[-1], 'restored exported.workflow.json')
            self.assertEqual(files.read(store.EXPORT), {'export': 2})
            with self.assertRaisesRegex(FileNotFoundError, 'has no archived spec.json, workflow.json, build.json'):
                files.restore('wf-never')
            self.assertEqual((files.read('nothing.json'), files.read('nothing.json', default={}), files.history()), (None, {}, []))
            files.append(store.HISTORY, {'operation': 'created'})
            self.assertEqual(files.history(), [{'operation': 'created'}])

    def test_a_run_adds_to_its_trace_and_a_build_starts_its_own_anew(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch('builtins.print'):
            path = Path(tmp) / 'run' / 'trace.txt'
            Trace(path, append=True)('first command')
            Trace(path, append=True)('second command')
            self.assertEqual([line.split(' ', 1)[1] for line in path.read_text(encoding='utf-8').splitlines()], ['first command', 'second command'])
            Trace(path)('a build')
            self.assertEqual(len(path.read_text(encoding='utf-8').splitlines()), 1)


class TheEnvironment(unittest.TestCase):
    def test_a_plan_reads_the_name_of_the_model_without_loading_a_key(self):
        """Found by the audit: printing a plan loaded the real environment file, keys included, into the test process."""
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'a.sh', Path(tmp) / 'b.sh'
            first.write_text('export FREYA_API_KEY="k-not-real-0001"\nBUILDER_LLM_MODEL=claude-opus-5-5  # the big one\n', encoding='utf-8')
            second.write_text('BUILDER_LLM_MODEL=claude-haiku-5-5\nOTHER=1\n', encoding='utf-8')
            self.assertEqual(settings.read_env([first, second, Path(tmp) / 'none']), {'FREYA_API_KEY': 'k-not-real-0001', 'BUILDER_LLM_MODEL': 'claude-opus-5-5', 'OTHER': '1'})
            self.assertEqual(settings.read_env([first, second], ('BUILDER_LLM_MODEL',)), {'BUILDER_LLM_MODEL': 'claude-opus-5-5'})
            with mock.patch.dict(os.environ, clear=False), mock.patch.object(settings, 'env_files', return_value=[first, second]):
                os.environ.pop('BUILDER_LLM_MODEL', None)
                os.environ.pop('FREYA_API_KEY', None)
                self.assertEqual((settings.configured_model(), settings.configured_model('asked-for'), experiment.configured_model()), ('claude-opus-5-5', 'asked-for', 'claude-opus-5-5'))
                self.assertNotIn('FREYA_API_KEY', os.environ)
                self.assertNotIn('BUILDER_LLM_MODEL', os.environ)
                os.environ['BUILDER_LLM_MODEL'] = 'from-the-environment'
                self.assertEqual(settings.configured_model(), 'from-the-environment', 'the process environment wins over the files')
                settings.load_env([first])
                self.assertEqual(os.environ['FREYA_API_KEY'], 'k-not-real-0001', 'a run that needs the keys still loads them')
                del os.environ['FREYA_API_KEY']
            with mock.patch.object(settings, 'load_env', side_effect=AssertionError('keys')), mock.patch('builtins.print'):
                self.assertEqual(experiment.main(['--plan']), 0)


if __name__ == '__main__':
    unittest.main()
