"""`python -m builder` is the one supported command.

    python -m builder --brief ... --tool-spec ...              build an agent from a brief (builder/pipeline.py)
    python -m builder optimize --slot ... --tool-spec ...      improve a deployed agent within limits (builder/optimization/cli.py)
"""
import sys

if sys.argv[1:2] == ['optimize']:
    from builder.optimization.cli import main
    raise SystemExit(main(sys.argv[2:]))

from builder.pipeline import main  # noqa: E402

raise SystemExit(main())
