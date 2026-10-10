"""Where one build keeps its files: outputs/<build-id>/. A file exists only if its stage ran."""
import json
import re
from pathlib import Path

SPEC = 'spec.json'                       # the agent-spec/1 the build used
WORKFLOW = 'workflow.json'               # the compiled, locally validated workflow
TRACE = 'trace.txt'                      # the run's trace
DEPLOYMENT = 'deployment.json'           # which Freya workflow this build was saved to
EXPORT = 'exported.workflow.json'        # the workflow as stored on Freya; the file to submit
EVALUATION = 'evaluation.json'           # practice-customer results from the kit


def build_id(slot):
    """A slot name made safe as a directory name. The same slot always gives the same build id."""
    return re.sub(r'[^A-Za-z0-9._-]+', '-', str(slot)).strip('.-') or 'build'


class BuildStore:
    def __init__(self, out, slot):
        self.dir = Path(out) / build_id(slot)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        return self.dir / name

    def write_json(self, name, data):
        path = self.path(name)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')
        return path
