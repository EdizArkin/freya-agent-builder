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
HISTORY = 'history.jsonl'                # one line per deploy or rollback of the slot; only ever appended to


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

    def append(self, name, row):
        """Add one line to a file that is never rewritten."""
        with self.path(name).open('a', encoding='utf-8') as out:
            out.write(json.dumps(row, ensure_ascii=False) + '\n')
        return self.path(name)

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

    def keep(self, version_id, name):
        """Put a copy of a file of the latest build beside its version, once: the export of a deploy."""
        source, target = self.path(name), self.version(version_id) / name
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        return target

    def read(self, name, version_id=None, default=None):
        """A JSON file of the latest build, or of one archived version; `default` when it is not there."""
        path = (self.version(version_id) if version_id else self.dir) / name
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default

    def history(self):
        """The lines of the slot's history, oldest first."""
        path = self.path(HISTORY)
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []

    def restore(self, version_id, deployment=None):
        """Make an archived version the latest build of the slot again: what a rollback on the platform needs on
        this machine, so that the next command reads and stamps the version that runs. Returns what was done.

        The spec, the workflow and the build record come back from the archive. The export comes back when the
        version was deployed with its export kept; otherwise the export that is there belongs to another
        version, and it is moved aside under a name that says so instead of staying "the file to submit"."""
        folder = self.version(version_id)
        missing = [name for name in (SPEC, WORKFLOW, BUILD) if not (folder / name).is_file()]
        if missing:
            raise FileNotFoundError('version %s of this slot has no archived %s' % (version_id, ', '.join(missing)))
        done = []
        for name in (SPEC, WORKFLOW, BUILD):
            if not self.path(name).is_file() or self.path(name).read_bytes() != (folder / name).read_bytes():
                self.path(name).write_bytes((folder / name).read_bytes())
                done.append('restored ' + name)
        export = self.path(EXPORT)
        if (folder / EXPORT).is_file():
            if not export.is_file() or export.read_bytes() != (folder / EXPORT).read_bytes():
                export.write_bytes((folder / EXPORT).read_bytes())
                done.append('restored ' + EXPORT)
        elif export.is_file() and done:
            aside = self.path('exported.not-%s.workflow.json' % build_id(version_id))
            export.replace(aside)
            done.append('moved %s to %s: it is not the export of %s' % (EXPORT, aside.name, version_id))
        if deployment is not None:
            self.write_json(DEPLOYMENT, deployment)
            done.append('rewrote ' + DEPLOYMENT)
        return done
