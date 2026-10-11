"""The builder's command for one brief:

    python3 builder/build.py {brief_path} {tool_api_path}

It reads the brief and its tool API, writes the agent's specification with a model, compiles it, puts the phone
agent on Freya, reads back what is published, and leaves the export and a plain-text trace. Everything it does is
builder/run.py; this file only makes that module reachable when it is started as a script.

    python3 builder/build.py briefs/hidden-1.md briefs/hidden-1.openapi.json --export submission/hidden-1.workflow.json --trace traces/hidden-1.log
"""
import sys
from pathlib import Path

# Started as a script, Python puts this folder first on the path, where the builder's own packages would hide
# under bare names. The repository root takes its place.
sys.path[0] = str(Path(__file__).resolve().parents[1])

from builder.run import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
