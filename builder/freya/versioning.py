"""Which Freya workflow belongs to which slot, which build was saved to it, and what a version is.

Three different things are kept apart:
- The slot registry (.freya-agents.json at the repo root) is written by the kit's
  save_workflow: slot -> workflow_id, agent_id and, after an update, the published
  platform version number. The builder only reads it.
- The deployment record (outputs/<build-id>/deployment.json) is the builder's own
  note of one deploy: the ids, plus the hash of the local workflow that was sent.
- The version id names one compiled workflow by its content. The same spec compiled by the
  same builder has the same id on every machine; any change of the workflow is another id.
  A build archives its spec and workflow under that id (artifacts/store.py) and never
  changes them afterwards, so a version that was tested can be compared or deployed again.

A platform version is another thing than a version id. Freya keeps every saved definition of a workflow
as a numbered version with an id of its own, a parent and one of them published; calls run the published
one. Verified on 2026-10-11 on a disposable workflow: a save makes an unpublished version, publishing
switches, an earlier version keeps its content, and publishing it again makes it the running one. The
history of a slot (outputs/<build-id>/history.jsonl) ties the two together: one line per deploy or
rollback with the workflow id, the platform version, our version id and the builder's commit.
"""
import hashlib
import json
import time
from pathlib import Path

from builder.config import settings

REGISTRY = settings.ROOT / '.freya-agents.json'


def known(slot, registry=REGISTRY):
    """The registry entry of a slot, or None if the slot was never saved from this machine."""
    path = Path(registry)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8')).get(slot)
    except (ValueError, AttributeError):
        return None


def builder_commit(root=None):
    """The commit of the builder that is checked out, read from .git without running anything; '' if it cannot be read.
    The same spec compiles to another workflow after a change of the builder: only this tells two such builds apart."""
    git = Path(root or settings.ROOT) / '.git'
    try:
        head = (git / 'HEAD').read_text(encoding='utf-8').strip()
        if not head.startswith('ref:'):
            return head
        ref = head.split(':', 1)[1].strip()
        if (git / ref).is_file():
            return (git / ref).read_text(encoding='utf-8').strip()
        for line in (git / 'packed-refs').read_text(encoding='utf-8').splitlines():
            if line.endswith(' ' + ref):
                return line.split(' ', 1)[0]
    except (OSError, ValueError):
        pass
    return ''


def platform_state(versions):
    """What a listing of a workflow's platform versions says: the published one, the newest one, how many."""
    rows = sorted([v for v in versions or [] if isinstance(v, dict)], key=lambda v: v.get('versionNumber') or 0)
    live = [v for v in rows if v.get('isPublished')]
    brief = lambda v: {'number': v.get('versionNumber'), 'id': v.get('id'), 'parent': v.get('parentVersionId')} if v else None
    return {'published': brief(live[-1] if live else None), 'newest': brief(rows[-1] if rows else None), 'count': len(rows)}


def history_row(operation, slot, workflow_id, state, version_id='', sha256='', provenance=None):
    """One line of a slot's history. `provenance` says where a rollback came from and went to."""
    return {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'operation': operation, 'slot': slot, 'workflow_id': workflow_id,
            'platform_version': (state.get('published') or {}).get('number'), 'platform_version_id': (state.get('published') or {}).get('id'),
            'platform_versions': state.get('count'), 'version_id': version_id, 'local_workflow_sha256': sha256,
            'builder_commit': builder_commit(), 'provenance': dict(provenance or {})}


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def workflow_hash(workflow):
    return content_hash(workflow)


def version_id(workflow):
    """The stable name of one compiled workflow: 'wf-' and the first 12 hex digits of its hash."""
    return 'wf-' + workflow_hash(workflow)[:12]


def build_record(slot, spec, workflow, source):
    """What one build made and from what. `source` says where the spec came from: a file that was reused, or
    the extraction (strategy, prompt version and digest, model)."""
    return {'slot': slot, 'version_id': version_id(workflow), 'local_workflow_sha256': workflow_hash(workflow),
            'spec_sha256': content_hash(spec), 'workflow_bytes': len(json.dumps(workflow)), 'source': dict(source), 'builder_commit': builder_commit(),
            'unsupported': list(spec.get('unsupported') or []), 'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}


def deployment_record(slot, saved, workflow, created):
    return {'slot': slot, 'workflow_id': saved['workflow_id'], 'agent_id': saved['agent_id'],
            'operation': 'created' if created else 'updated', 'version_id': version_id(workflow),
            'platform_version': saved.get('version', 1), 'local_workflow_sha256': workflow_hash(workflow),
            'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
