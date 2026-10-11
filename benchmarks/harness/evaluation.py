"""The evaluation the builder's optimization run is handed: the suites of this harness as results per case.

The run itself is the builder's (builder/optimization/, `python -m builder optimize`): it diagnoses, proposes,
deploys, decides and rolls back. This module gives it the one thing it does not own. `Evaluation` runs suites of
the harness on Freya through `live.py simulate` and reads back what that command recorded: one row per case with
the bank's verdict, the judge's verdict, the records the scenario forbids, the transcript and the tool calls.
It holds no decision: which results count, and what follows from them, is builder/optimization/policy.py.

A row is evidence for one version. `stored(version_id)` returns only the rows of simulations that were recorded
for that version id of this slot, and stamps each row with the version id, the workflow id and the platform
version of its simulation, so that the run can check the binding itself.

The builder loads this module by name (builder.config.settings.EVALUATION, or --evaluation). What it needs from
a module that takes its place: `add_arguments(parser)` for the options that are its own, and `Evaluation(args)`
with cases(), safety_cases(), stored(version_id) and run(version_id, label, only=None).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import live  # noqa: E402

SUITE_KINDS = ('public', 'scenarios')
SAFETY = 'Safety'


def add_arguments(parser):
    parser.add_argument('--suite', action='append', default=[], help='what a result is made of: public:A, public:B or scenarios:<agent>; repeat')
    parser.add_argument('--scenarios', default='benchmarks/live/m17.scenarios.json', help='the file of the scenarios:<agent> suites')


class Evaluation:
    """Results per case for one slot: stored ones, or from simulations run now."""

    def __init__(self, args, simulate=live.main):
        self.args, self.simulate = args, simulate
        if not args.suite:
            raise ValueError('no suite was named: pass --suite public:A, public:B or scenarios:<agent>')
        self.suites = []
        for suite in args.suite:
            kind, _, what = suite.partition(':')
            # Checked here, before anything is deployed: a suite the simulate command would refuse must not be found out after the deploy.
            if kind not in SUITE_KINDS or not what or (kind == 'public' and what not in ('A', 'B')):
                raise ValueError('%r is not a suite: public:A, public:B or scenarios:<agent>' % suite)
            cases = live.public_cases(args.kit, what) if kind == 'public' else live.load_scenarios(args.scenarios, args.kit, what)
            if not cases:
                raise ValueError('the suite %s has no case' % suite)
            self.suites.append((kind, what, cases))

    def cases(self):
        return [c['id'] for _, _, cases in self.suites for c in cases]

    def safety_cases(self):
        """The cases of the Safety suite, and every case with a deterministic check that must hold."""
        return [c['id'] for _, _, cases in self.suites for c in cases if c.get('suite') == SAFETY or c.get('gate')]

    def stored(self, version_id):
        out = {}
        for line, record in live.records(argparse.Namespace(out=self.args.out, run=self.args.run)):
            if line.get('slot') != self.args.slot or line.get('version_id') != version_id or not version_id or not record:
                continue
            for row in record['rows']:          # a case that was run again counts with its last run
                out[row['id']] = dict(row, version_id=line.get('version_id'), workflow_id=line.get('workflow_id'), platform_version=line.get('platform_version'),
                                      label=line.get('label'), simulation_id=line.get('simulation_id'), simulation=line.get('name'))
        return out

    def run(self, version_id, label, only=None):
        """Simulate the suites on the published version of the slot, under `label`, and return what is stored for
        `version_id` afterwards. With `only`, just those cases, and no suite that has none of them."""
        for kind, what, cases in self.suites:
            ids = [c['id'] for c in cases if only is None or c['id'] in only]
            if not ids:
                continue
            argv = ['simulate', '--run', self.args.run, '--version', label, '--slot', self.args.slot, '--out', self.args.out, '--kit', self.args.kit, '--live']
            argv += ['--public', what] if kind == 'public' else ['--scenarios', self.args.scenarios, '--agent', what]
            if len(ids) < len(cases):
                argv += ['--only', ','.join(ids)]
            try:
                self.simulate(argv)
            except SystemExit as exc:           # the command refused its arguments: no result, and the run decides what that means
                print('NOT RUN %s:%s | the simulate command stopped: %s' % (kind, what, exc))
        return self.stored(version_id)
