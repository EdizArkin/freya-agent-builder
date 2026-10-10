"""Which Freya workflow belongs to which slot, and which build was saved to it.

Two different things are kept apart:
- The slot registry (.freya-agents.json at the repo root) is written by the kit's
  save_workflow: slot -> workflow_id, agent_id and, after an update, the published
  platform version number. The builder only reads it.
- The deployment record (outputs/<build-id>/deployment.json) is the builder's own
  note of one deploy: the ids, plus the hash of the local workflow that was sent.

Versions themselves are managed by the platform. Nothing here can roll back.
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


def workflow_hash(workflow):
    return hashlib.sha256(json.dumps(workflow, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def deployment_record(slot, saved, workflow, created):
    return {'slot': slot, 'workflow_id': saved['workflow_id'], 'agent_id': saved['agent_id'],
            'operation': 'created' if created else 'updated',
            'platform_version': saved.get('version', 1), 'local_workflow_sha256': workflow_hash(workflow),
            'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
