"""M18 phase 3: platform versions of one workflow, the history of a slot, and a rollback.

The platform is played by a fake that behaves as a probe on a disposable workflow showed on 2026-10-11: a save
makes an unpublished version whose parent is the one before, publishing a version unpublishes the others, and
an earlier version can be published again. No test here reaches Freya.
"""
import json
import tempfile
import unittest
from pathlib import Path

from builder.artifacts import store
from builder.freya import deployment, versioning


class Platform:
    """One workflow on a platform that keeps versions."""

    def __init__(self, obeys=True):
        self.versions, self.calls, self.obeys = [], [], obeys

    def workspace_id(self):
        return 'ws'

    def _save(self):
        self.versions.append({'id': 'pv-%d' % (len(self.versions) + 1), 'versionNumber': len(self.versions) + 1, 'isPublished': False,
                              'parentVersionId': self.versions[-1]['id'] if self.versions else None})

    def _publish(self, version_id):
        if self.obeys:
            for v in self.versions:
                v['isPublished'] = v['id'] == version_id

    def validate_workflow(self, name, workflow):
        self.calls.append('dry-run')

    def save_workflow(self, slot, workflow, state_file):
        path = Path(state_file)
        state = json.loads(path.read_text()) if path.exists() else {}
        self._save()
        self._publish(self.versions[-1]['id'])
        state[slot] = {'workflow_id': 'w-1', 'agent_id': 'a-1', 'version': len(self.versions)}
        path.write_text(json.dumps(state))
        return state[slot]

    def export_workflow(self, workflow_id, path):
        Path(path).write_text('{}')

    def api(self, method, path, body=None, **params):
        self.calls.append((method, path, body))
        if method == 'GET':
            return {'versions': [dict(v) for v in reversed(self.versions)]}
        self._publish(body['versionId'])
        return {}


class History(unittest.TestCase):
    def test_a_deploy_records_what_the_platform_published_and_which_builder_made_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            freya, lines = Platform(), []
            first = deployment.deploy(freya, 'slot', {'nodes': {'x': 1}}, Path(tmp) / 'e.json', lines.append, Path(tmp) / 'agents.json')
            second = deployment.deploy(freya, 'slot', {'nodes': {'x': 2}}, Path(tmp) / 'e.json', lines.append, Path(tmp) / 'agents.json')
        self.assertEqual(first['platform'], {'published': {'number': 1, 'id': 'pv-1', 'parent': None}, 'newest': {'number': 1, 'id': 'pv-1', 'parent': None}, 'count': 1})
        self.assertEqual(second['platform']['published'], {'number': 2, 'id': 'pv-2', 'parent': 'pv-1'})
        row = second['history']
        self.assertEqual((row['operation'], row['slot'], row['workflow_id'], row['platform_version'], row['platform_version_id'], row['platform_versions']),
                         ('updated', 'slot', 'w-1', 2, 'pv-2', 2))
        self.assertEqual((row['version_id'], row['local_workflow_sha256']), (versioning.version_id({'nodes': {'x': 2}}), versioning.workflow_hash({'nodes': {'x': 2}})))
        self.assertEqual(row['builder_commit'], versioning.builder_commit())
        self.assertEqual(first['history']['operation'], 'created')
        self.assertNotEqual(first['history']['version_id'], row['version_id'], 'a version id is the hash of what was sent, a platform version is a number of the platform')
        self.assertTrue(any('platform version 2 is published (pv-2 of 2)' in line for line in lines))

    def test_the_history_of_a_slot_is_only_ever_appended_to(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = store.BuildStore(tmp, 'slot')
            files.append(store.HISTORY, {'operation': 'created', 'platform_version': 1})
            files.append(store.HISTORY, {'operation': 'rollback', 'platform_version': 1})
            rows = [json.loads(line) for line in files.path(store.HISTORY).read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['operation'] for r in rows], ['created', 'rollback'])
        self.assertEqual(store.HISTORY, 'history.jsonl')

    def test_a_client_that_cannot_list_versions_still_deploys(self):
        class Bare:
            def validate_workflow(self, name, workflow): pass
            def save_workflow(self, slot, workflow, state_file): return {'workflow_id': 'w', 'agent_id': 'a'}
            def export_workflow(self, workflow_id, path): Path(path).write_text('{}')

        with tempfile.TemporaryDirectory() as tmp:
            record = deployment.deploy(Bare(), 's', {}, Path(tmp) / 'e.json', lambda line: None, Path(tmp) / 'agents.json')
        self.assertNotIn('platform', record)
        self.assertNotIn('history', record)

    def test_the_build_record_names_the_commit_of_the_builder(self):
        record = versioning.build_record('slot', {}, {'nodes': {}}, {'kind': 'reused spec'})
        self.assertEqual(record['builder_commit'], versioning.builder_commit())
        self.assertRegex(record['builder_commit'], r'^[0-9a-f]{40}$')

    def test_the_commit_is_read_from_the_repository_without_running_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp) / '.git'
            self.assertEqual(versioning.builder_commit(tmp), '')
            (git / 'refs' / 'heads').mkdir(parents=True)
            (git / 'HEAD').write_text('ref: refs/heads/main\n')
            self.assertEqual(versioning.builder_commit(tmp), '', 'a branch without a commit')
            (git / 'refs' / 'heads' / 'main').write_text('a' * 40 + '\n')
            self.assertEqual(versioning.builder_commit(tmp), 'a' * 40)
            (git / 'refs' / 'heads' / 'main').unlink()
            (git / 'packed-refs').write_text('# pack-refs\n' + 'b' * 40 + ' refs/heads/other\n' + 'c' * 40 + ' refs/heads/main\n')
            self.assertEqual(versioning.builder_commit(tmp), 'c' * 40)
            (git / 'HEAD').write_text('d' * 40 + '\n')
            self.assertEqual(versioning.builder_commit(tmp), 'd' * 40, 'a detached head')


class Rollback(unittest.TestCase):
    def platform(self, obeys=True):
        freya = Platform(obeys)
        for _ in range(3):
            freya._save()
        Platform._publish(Platform(), None)
        for v in freya.versions:
            v['isPublished'] = v['versionNumber'] == 3
        return freya

    def test_an_earlier_version_of_the_same_workflow_is_published_again(self):
        freya, lines = self.platform(), []
        row = deployment.rollback(freya, 'slot', 'w-1', 1, lines.append, reason='ADV-SKIP-CONFIRM failed on version 3')
        self.assertEqual([(v['versionNumber'], v['isPublished']) for v in freya.versions], [(1, True), (2, False), (3, False)], 'nothing is deleted, nothing is made')
        self.assertIn(('POST', '/api/v2/versions/workflow/w-1/publish', {'versionId': 'pv-1'}), freya.calls)
        self.assertEqual((row['operation'], row['workflow_id'], row['platform_version'], row['platform_version_id'], row['platform_versions']), ('rollback', 'w-1', 1, 'pv-1', 3))
        self.assertEqual(row['provenance'], {'from_platform_version': 3, 'from_platform_version_id': 'pv-3', 'to_platform_version': 1, 'to_platform_version_id': 'pv-1',
                                             'reason': 'ADV-SKIP-CONFIRM failed on version 3'})
        self.assertEqual(lines, ['freya: rolled back slot from platform version 3 to 1'])

    def test_what_cannot_be_rolled_back_is_refused_before_anything_is_published(self):
        freya = self.platform()
        with self.assertRaisesRegex(ValueError, 'has no platform version 7'):
            deployment.rollback(freya, 'slot', 'w-1', 7, lambda line: None)
        with self.assertRaisesRegex(ValueError, 'already the published one'):
            deployment.rollback(freya, 'slot', 'w-1', 3, lambda line: None)
        self.assertEqual([c for c in freya.calls if c[0] == 'POST'], [])

    def test_a_rollback_the_platform_did_not_carry_out_is_an_error_not_a_record(self):
        with self.assertRaisesRegex(RuntimeError, 'did not publish version 1'):
            deployment.rollback(self.platform(obeys=False), 'slot', 'w-1', 1, lambda line: None)

    def test_the_state_of_a_listing(self):
        self.assertEqual(versioning.platform_state([]), {'published': None, 'newest': None, 'count': 0})
        self.assertEqual(versioning.platform_state(None)['count'], 0)
        state = versioning.platform_state([{'id': 'b', 'versionNumber': 2, 'isPublished': False, 'parentVersionId': 'a'}, {'id': 'a', 'versionNumber': 1, 'isPublished': True}])
        self.assertEqual((state['published']['number'], state['newest']['number'], state['count']), (1, 2, 2), 'the published version need not be the newest')


if __name__ == '__main__':
    unittest.main()
