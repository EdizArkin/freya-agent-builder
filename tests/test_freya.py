"""builder/freya and builder/evaluation with a fake platform client. No test here reaches Freya."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from builder.artifacts import store
from builder.freya import client, deployment, versioning


class FakeFreya:
    """Behaves like the kit's save_workflow: creates for an unknown slot, updates and publishes for a known one."""

    def __init__(self):
        self.calls = []

    def validate_workflow(self, name, workflow):
        self.calls.append(('dry-run', name))

    def save_workflow(self, slot, workflow, state_file):
        path = Path(state_file)
        state = json.loads(path.read_text()) if path.exists() else {}
        if slot in state:
            state[slot]['version'] = state[slot].get('version', 1) + 1
            self.calls.append(('update+publish', slot))
        else:
            state[slot] = {'workflow_id': 'w-' + slot, 'agent_id': 'a-' + slot}
            self.calls.append(('create', slot))
        path.write_text(json.dumps(state))
        return state[slot]

    def export_workflow(self, workflow_id, path):
        self.calls.append(('export', workflow_id))
        Path(path).write_text('{}')


class Deployment(unittest.TestCase):
    def test_first_deploy_creates_and_the_next_updates_the_same_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry, export, lines, freya = Path(tmp) / 'agents.json', Path(tmp) / 'export.json', [], FakeFreya()
            self.assertIsNone(versioning.known('brief-a', registry))
            first = deployment.deploy(freya, 'brief-a', {'nodes': {'x': 1}}, export, lines.append, registry)
            second = deployment.deploy(freya, 'brief-a', {'nodes': {'x': 2}}, export, lines.append, registry)
            other = deployment.deploy(freya, 'brief-b', {'nodes': {'x': 2}}, export, lines.append, registry)
            self.assertEqual(versioning.known('brief-a', registry)['workflow_id'], 'w-brief-a')
        self.assertEqual([c[0] for c in freya.calls], ['dry-run', 'create', 'export', 'dry-run', 'update+publish', 'export', 'dry-run', 'create', 'export'])
        self.assertEqual((first['operation'], first['platform_version'], first['workflow_id']), ('created', 1, 'w-brief-a'))
        self.assertEqual((second['operation'], second['platform_version'], second['workflow_id']), ('updated', 2, 'w-brief-a'))
        self.assertEqual((other['operation'], other['workflow_id']), ('created', 'w-brief-b'))
        self.assertNotEqual(first['local_workflow_sha256'], second['local_workflow_sha256'])
        self.assertEqual(second['local_workflow_sha256'], other['local_workflow_sha256'])
        self.assertIn('new workflow created', lines[1])
        self.assertIn('existing workflow updated', lines[4])

    def test_a_rejected_dry_run_stops_before_anything_is_saved(self):
        freya = FakeFreya()
        freya.validate_workflow = mock.Mock(side_effect=RuntimeError('POST /api/v2/workflow -> 400'))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
            deployment.deploy(freya, 's', {}, Path(tmp) / 'e.json', lambda line: None, Path(tmp) / 'agents.json')
        self.assertEqual(freya.calls, [])

    def test_registry_reading_is_tolerant_and_the_record_holds_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'agents.json'
            for text in ('', 'not json', '[]'):
                path.write_text(text)
                self.assertIsNone(versioning.known('s', path))
        self.assertEqual(versioning.REGISTRY.name, '.freya-agents.json')
        record = versioning.deployment_record('s', {'workflow_id': 'w', 'agent_id': 'a', 'version': 4}, {'b': 1, 'a': 2}, False)
        self.assertEqual(sorted(record), ['agent_id', 'deployed_at', 'local_workflow_sha256', 'operation', 'platform_version', 'slot', 'version_id', 'workflow_id'])
        self.assertEqual(record['local_workflow_sha256'], versioning.workflow_hash({'a': 2, 'b': 1}))
        self.assertEqual(record['version_id'], 'wf-' + record['local_workflow_sha256'][:12])

    def test_the_client_is_the_kit_module_loaded_from_the_given_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'freya.py').write_text('MARK = 41 + 1\ndef save_workflow(*a, **k):\n    return MARK\n')
            module = client.load(tmp)
            self.assertEqual(module.save_workflow(), 42)
            with self.assertRaises(FileNotFoundError):
                client.load(Path(tmp) / 'nowhere')


class Evaluation(unittest.TestCase):
    def test_the_runner_is_the_kit_module_and_is_loaded_without_running_it(self):
        # What a simulation does with the runner, and how it is named, is in tests/test_m17.py.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'tools').mkdir()
            (Path(tmp) / 'tools' / 'run_cases.py').write_text(
                'RAN = []\ndef persona_for(case, ident, style=None):\n    return case["id"]\nif __name__ == "__main__":\n    RAN.append(1)\n')
            runner = client.load_runner(tmp)
        self.assertEqual((runner.persona_for({'id': 'PUB-01'}, {}), runner.RAN), ('PUB-01', []))


class Artifacts(unittest.TestCase):
    def test_one_folder_per_build_with_fixed_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = store.BuildStore(tmp, 'brief-a')
            path = files.write_json(store.SPEC, {'k': 'ü'})
            self.assertEqual(path, Path(tmp) / 'brief-a' / 'spec.json')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'k': 'ü'})
            self.assertEqual(sorted(p.name for p in files.dir.iterdir()), ['spec.json'])
        self.assertEqual((store.SPEC, store.WORKFLOW, store.TRACE, store.DEPLOYMENT, store.EXPORT, store.EVALUATION),
                         ('spec.json', 'workflow.json', 'trace.txt', 'deployment.json', 'exported.workflow.json', 'evaluation.json'))
        for slot, expected in (('brief-a', 'brief-a'), ('Final Brief #2', 'Final-Brief-2'), ('../..', 'build'), ('a/b\\c', 'a-b-c')):
            self.assertEqual(store.build_id(slot), expected)


if __name__ == '__main__':
    unittest.main()
