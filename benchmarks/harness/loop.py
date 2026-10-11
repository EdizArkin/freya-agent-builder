"""Deprecated entry point of the optimization step. Use the builder's own command:

    python -m builder optimize --slot m18-opt-a --tool-spec API.json --suite public:A --suite scenarios:a [--live ...]

The step was moved into the builder (builder/optimization/), and the part of this file that the builder is handed,
`Evaluation`, is benchmarks/harness/evaluation.py. This file forwards to the builder's command so that an old
command line still runs. Three of its options no longer mean anything and are dropped with a note: --candidate and
--incumbent (results are bound to the version id of a build, not to a label a person chooses) and --brief (a
candidate is built from the incumbent's archived spec).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import evaluation  # noqa: E402
from benchmarks.harness.evaluation import Evaluation  # noqa: E402,F401  (kept under its old name)
from builder.optimization import cli  # noqa: E402


def main(argv=None):
    old = argparse.ArgumentParser(add_help=False)
    for flag in ('--candidate', '--incumbent'):
        old.add_argument(flag)
    old.add_argument('--brief', action='append')
    dropped, rest = old.parse_known_args(argv)
    print('benchmarks/harness/loop.py is deprecated: use `python -m builder optimize`.')
    for name, value in sorted(vars(dropped).items()):
        if value:
            print('  --%s is no longer used and was dropped' % name)
    return cli.main(rest, evaluation=evaluation)


if __name__ == '__main__':
    sys.exit(main())
