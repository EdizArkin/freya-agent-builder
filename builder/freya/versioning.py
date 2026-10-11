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

Version numbers on the platform are counted by the platform, per workflow. Nothing here rolls back.
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
            'spec_sha256': content_hash(spec), 'workflow_bytes': len(json.dumps(workflow)), 'source': dict(source),
            'unsupported': list(spec.get('unsupported') or []), 'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}


def deployment_record(slot, saved, workflow, created):
    return {'slot': slot, 'workflow_id': saved['workflow_id'], 'agent_id': saved['agent_id'],
            'operation': 'created' if created else 'updated', 'version_id': version_id(workflow),
            'platform_version': saved.get('version', 1), 'local_workflow_sha256': workflow_hash(workflow),
            'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
