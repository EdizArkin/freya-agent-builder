"""builder/pipeline.py and the command line: stages, artifacts, exit codes, and the network boundary."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from builder import pipeline
from builder.config import settings
from builder.extraction import claude
from builder.freya import client as freya_client
from builder.freya import versioning
from benchmarks.harness.fixture_spec import spec as fixture
from tests.support import KIT

FLAGS = ('--brief', '--tool-spec', '--slot', '--out', '--spec', '--live-extract', '--model', '--max-tokens',
         '--best-effort', '--deploy', '--test', '--kit', '--env-file', '--strategy', '--prompt-version')


def top(files):
    """The files of a build without its archived versions."""
    return sorted(f for f in files if '/versions/' not in f)


class Build(unittest.TestCase):
    def run_build(self, *extra, spec=None):
        with tempfile.TemporaryDirectory() as tmp:
            spec_file = Path(tmp) / 'in.json'
            spec_file.write_text(json.dumps(spec or fixture(True)), encoding='utf-8')
            args = ['--brief', str(KIT / 'briefs/brief-a.md'), '--brief', str(KIT / 'briefs/brief-b.md'),
                    '--tool-spec', str(KIT / 'tools/openapi.json'), '--out', tmp] + [x.replace('SPEC', str(spec_file)) for x in extra]
            with mock.patch.object(settings, 'load_env'), mock.patch('builtins.print'):
                code = pipeline.main(args)
            files = {f.relative_to(tmp).as_posix(): f.read_text(encoding='utf-8') for f in Path(tmp).rglob('*') if f.is_file()}
        return code, files

    def test_offline_build_writes_spec_workflow_and_trace(self):
        code, files = self.run_build('--spec', 'SPEC')
        self.assertEqual(code, 0)
        self.assertEqual(top(files), ['brief-b/build.json', 'brief-b/spec.json', 'brief-b/trace.txt', 'brief-b/workflow.json', 'in.json'])
        workflow = json.loads(files['brief-b/workflow.json'])
        # The build is archived under its version id: the same three files, kept apart from the next build of the slot.
        built = json.loads(files['brief-b/build.json'])
        self.assertEqual(built['version_id'], versioning.version_id(workflow))
        self.assertEqual(built['source']['kind'], 'reused spec')
        kept = 'brief-b/versions/%s/' % built['version_id']
        self.assertEqual(sorted(f for f in files if '/versions/' in f), [kept + 'build.json', kept + 'spec.json', kept + 'workflow.json'])
        self.assertEqual(files[kept + 'workflow.json'], files['brief-b/workflow.json'])
        self.assertEqual(workflow['initial_node'], 'setup')
        self.assertIn('add_note', [t['function']['name'] for t in workflow['nodes']['negotiate']['tools']])
        self.assertEqual(json.loads(files['brief-b/spec.json']), fixture(True))
        for part in ('build start', 'no model call', 'compiled and locally validated', 'build end | ok'):
            self.assertIn(part, files['brief-b/trace.txt'])

    def test_nothing_external_without_explicit_flags(self):
        with mock.patch.object(claude.AnthropicClient, 'complete', side_effect=AssertionError('network')), \
             mock.patch.object(freya_client, 'load', side_effect=AssertionError('network')), \
             mock.patch.object(subprocess, 'run', side_effect=AssertionError('network')):
            code, files = self.run_build()
            self.assertEqual(code, 1)
            self.assertIn('STOPPED: No spec source', files['brief-b/trace.txt'])
            self.assertEqual(sorted(files), ['brief-b/trace.txt', 'in.json'])
            code, files = self.run_build('--spec', 'SPEC', '--test', 'B')
            self.assertEqual(code, 1)
            self.assertIn('STOPPED: --test needs --deploy', files['brief-b/trace.txt'])
            code, files = self.run_build('--spec', 'SPEC')
            self.assertEqual(code, 0)

    def test_live_path_with_replayed_model_and_fake_freya(self):
        calls = []

        class Freya:
            def validate_workflow(self, name, workflow): calls.append('dry')
            def save_workflow(self, slot, workflow, state_file): calls.append('save'); return {'workflow_id': 'w1', 'agent_id': 'a1'}
            def export_workflow(self, workflow_id, path): calls.append('export'); Path(path).write_text('{}')

        reply = (json.dumps(fixture(True)), {'model': 'replay', 'input_tokens': 1, 'output_tokens': 1, 'stop_reason': 'end_turn'}, [])
        with mock.patch.object(claude.AnthropicClient, 'complete', return_value=reply), \
             mock.patch.object(freya_client, 'load', return_value=Freya()):
            code, files = self.run_build('--live-extract', '--deploy')
        self.assertEqual((code, calls), (0, ['dry', 'save', 'export']))
        for part in ('model call: replay', 'saved and published workflow w1 | agent a1'):
            self.assertIn(part, files['brief-b/trace.txt'])
        self.assertEqual(top(files), ['brief-b/build.json', 'brief-b/deployment.json', 'brief-b/exported.workflow.json', 'brief-b/spec.json',
                                      'brief-b/trace.txt', 'brief-b/workflow.json', 'in.json'])
        record = json.loads(files['brief-b/deployment.json'])
        self.assertEqual((record['slot'], record['workflow_id'], record['agent_id']), ('brief-b', 'w1', 'a1'))
        built = json.loads(files['brief-b/build.json'])
        self.assertEqual(record['version_id'], built['version_id'])
        self.assertEqual(built['source'], {'kind': 'extraction', 'strategy': 'single', 'prompt_version': claude.PROMPT_VERSION,
                                           'prompt_digest': claude.prompt_digest(), 'model': settings.DEFAULT_MODEL})
        self.assertIn('extraction strategy: single | prompt %s (digest %s)' % (claude.PROMPT_VERSION, claude.prompt_digest()), files['brief-b/trace.txt'])

    def test_failures_set_the_exit_code_and_name_the_stage(self):
        broken = fixture(True)
        broken['promise']['amount_param'] = 'sum'
        code, files = self.run_build('--spec', 'SPEC', spec=broken)
        self.assertEqual(code, 1)
        self.assertIn("SPEC ERROR: promise.amount_param='sum'", files['brief-b/trace.txt'])
        self.assertIn('build end | failed', files['brief-b/trace.txt'])
        self.assertNotIn('brief-b/workflow.json', files)
        with mock.patch.object(pipeline, 'check_workflow', return_value=['a platform rule']):
            code, files = self.run_build('--spec', 'SPEC')
        self.assertEqual(code, 1)
        self.assertIn('COMPILE ERROR: a platform rule', files['brief-b/trace.txt'])
        self.assertNotIn('brief-b/workflow.json', files)
        with mock.patch.object(freya_client, 'load', side_effect=RuntimeError('POST /api/v2/workflow -> 401')):
            code, files = self.run_build('--spec', 'SPEC', '--deploy')
        self.assertEqual(code, 1)
        self.assertIn('FAILED: RuntimeError: POST /api/v2/workflow -> 401', files['brief-b/trace.txt'])
        self.assertNotIn('brief-b/deployment.json', files)

    def test_a_spec_carrying_a_credential_is_refused_before_anything_is_written(self):
        leaking = fixture()
        leaking['rules']['always'] += ' Send header Authorization: Bearer abcdefghijklmnopqrstu.'
        code, files = self.run_build('--spec', 'SPEC', '--slot', 'leak', spec=leaking)
        self.assertEqual((code, sorted(files)), (1, ['in.json', 'leak/trace.txt']))
        self.assertIn('SPEC ERROR: credential leak in the spec at $.rules.always', files['leak/trace.txt'])
        self.assertNotIn('abcdefghijklmnopqrstu', files['leak/trace.txt'])

    def test_slot_names_cannot_escape_the_output_folder(self):
        code, files = self.run_build('--spec', 'SPEC', '--slot', '../../evil slot')
        self.assertEqual(code, 0)
        self.assertIn('evil-slot/workflow.json', files)


class CommandLine(unittest.TestCase):
    def test_python_m_builder_is_the_command(self):
        done = subprocess.run([sys.executable, '-m', 'builder', '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0)
        for flag in FLAGS:
            self.assertIn(flag, done.stdout)

    def test_flags_and_defaults_are_unchanged(self):
        a = pipeline.arguments(['--brief', 'a.md', '--brief', 'b.md', '--tool-spec', 'api.json'])
        self.assertEqual((a.brief, a.tool_spec, a.slot, a.spec, a.model, a.test), (['a.md', 'b.md'], 'api.json', None, None, None, None))
        self.assertEqual((a.live_extract, a.deploy, a.best_effort, a.max_tokens, a.env_file), (False, False, False, 32000, []))
        self.assertEqual((Path(a.out), Path(a.kit)), (settings.ROOT / 'outputs', settings.ROOT / 'local_freya' / 'starter_kit'))
        with mock.patch('sys.stderr'), self.assertRaises(SystemExit) as ctx:
            pipeline.arguments(['--tool-spec', 'api.json'])
        self.assertEqual(ctx.exception.code, 2)

    def test_offline_build_through_the_real_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_file = Path(tmp) / 'in.json'
            spec_file.write_text(json.dumps(fixture()), encoding='utf-8')
            done = subprocess.run([sys.executable, '-m', 'builder', '--brief', str(KIT / 'briefs/brief-a.md'), '--tool-spec',
                                   str(KIT / 'tools/openapi.json'), '--out', tmp, '--slot', 'cli', '--spec', str(spec_file)],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn('build end | ok', done.stdout)
            self.assertEqual(sorted(p.name for p in (Path(tmp) / 'cli').iterdir()), ['build.json', 'spec.json', 'trace.txt', 'versions', 'workflow.json'])


if __name__ == '__main__':
    unittest.main()
