"""Where one build keeps its files: outputs/<build-id>/. A file exists only if its stage ran.

The files at the top of the folder are the latest build of the slot and are replaced by the next one.
outputs/<build-id>/versions/<version-id>/ is what is kept: the spec, the workflow and the build record of
one version, written once and never changed. Results of tests on a version are added beside them.
"""
import json
import re
from pathlib import Path

SPEC = 'spec.json'                       # the agent-spec/1 the build used
WORKFLOW = 'workflow.json'               # the compiled, locally validated workflow
TRACE = 'trace.txt'                      # the run's trace
BUILD = 'build.json'                     # the version id of this build and where its spec came from
DEPLOYMENT = 'deployment.json'           # which Freya workflow this build was saved to
EXPORT = 'exported.workflow.json'        # the workflow as stored on Freya; the file to submit
EVALUATION = 'evaluation.json'           # practice-customer results
VERSIONS = 'versions'                    # one folder per version id; immutable


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

    def version(self, version_id):
        return self.dir / VERSIONS / build_id(version_id)

    def archive(self, version_id, spec, workflow, record):
        """Keep one version for good. A version that is already archived is left exactly as it is: the id is
        the hash of the workflow, so the files could only be the same."""
        folder = self.version(version_id)
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in ((SPEC, spec), (WORKFLOW, workflow), (BUILD, record)):
            if not (folder / name).exists():
                (folder / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')
        return folder
