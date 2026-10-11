"""The one place where the builder changes something on Freya.

deploy() does, in this order:
1. dry run   - the platform validates the workflow and stores nothing;
2. save      - the kit's save_workflow. For a slot that is not in the registry it
               creates a new workflow and its agent. For a known slot it replaces
               the definition of the existing workflow AND publishes the new
               version. The kit offers no save without publish.
3. export    - reads the stored workflow back into a local file.

There is no approval step. The caller's --deploy flag is the only gate. `rollback` publishes an earlier
platform version of the same workflow again; no command calls it yet.
`departures` compares what the platform stores with what was sent, after the fact.
"""
from builder.freya import versioning


def departures(sent, stored, path='$'):
    """Where the workflow the platform stores differs from the one that was sent: (changed, added).

    `changed` lists every value of the sent workflow that is missing or different in the stored one: a
    deployment with an entry here does not run what was built. `added` lists what the platform put in by
    itself (default fields of a node, agent_config); it is expected and reported, not a defect."""
    changed, added = [], []
    if isinstance(sent, dict) and isinstance(stored, dict):
        for key, value in sent.items():
            if key not in stored:
                changed.append(path + '.' + str(key) + ': missing')
            else:
                more = departures(value, stored[key], path + '.' + str(key))
                changed += more[0]
                added += more[1]
        added += [path + '.' + str(key) for key in stored if key not in sent]
    elif isinstance(sent, list) and isinstance(stored, list):
        if len(sent) != len(stored):
            changed.append('%s: %d item(s) sent, %d stored' % (path, len(sent), len(stored)))
        for i, (a, b) in enumerate(zip(sent, stored)):
            more = departures(a, b, '%s[%d]' % (path, i))
            changed += more[0]
            added += more[1]
    elif sent != stored:
        changed.append('%s: sent %s, stored %s' % (path, repr(sent)[:80], repr(stored)[:80]))
    return changed, added


def deploy(freya, slot, workflow, export_path, trace, registry=versioning.REGISTRY):
    """Dry-run, save and publish, export. Returns the deployment record."""
    freya.validate_workflow(slot + ' dry run', workflow)
    trace('freya: dry-run validation accepted the workflow')
    created = versioning.known(slot, registry) is None
    saved = freya.save_workflow(slot, workflow, state_file=str(registry))
    trace('freya: saved and published workflow %s | agent %s | version %s | %s' % (
        saved['workflow_id'], saved['agent_id'], saved.get('version', 1),
        'new workflow created' if created else 'existing workflow updated'))
    freya.export_workflow(saved['workflow_id'], str(export_path))
    trace('freya: exported %s' % export_path)
    record = versioning.deployment_record(slot, saved, workflow, created)
    state = platform_versions(freya, saved['workflow_id'])
    if state:
        # The platform's own account of what is published now, kept with the deploy and in the slot's history.
        record['platform'] = state
        record['history'] = versioning.history_row('created' if created else 'updated', slot, saved['workflow_id'], state,
                                                   record['version_id'], record['local_workflow_sha256'])
        trace('freya: platform version %s is published (%s of %s)' % ((state['published'] or {}).get('number'), (state['published'] or {}).get('id'), state['count']))
    return record


def platform_versions(freya, workflow_id):
    """The platform versions of a workflow, or None with a client that cannot list them."""
    if not hasattr(freya, 'api') or not hasattr(freya, 'workspace_id'):
        return None
    listed = freya.api('GET', '/api/v2/versions/workflow/' + workflow_id, workspaceId=freya.workspace_id())
    return versioning.platform_state(listed.get('versions') if isinstance(listed, dict) else listed)


def rollback(freya, slot, workflow_id, to_number, trace, reason=''):
    """Make an earlier platform version of the same workflow the published one. Nothing is deleted and no version
    is made: the newer versions stay in the list. Returns the history line of the rollback.

    The platform runs calls on the published version. The definition that a read of the workflow returns stays
    the one that was saved last; it is not what runs."""
    listed = freya.api('GET', '/api/v2/versions/workflow/' + workflow_id, workspaceId=freya.workspace_id())
    versions = listed.get('versions') if isinstance(listed, dict) else listed
    before = versioning.platform_state(versions)
    target = [v for v in versions or [] if v.get('versionNumber') == to_number]
    if not target:
        raise ValueError('%s has no platform version %s' % (slot, to_number))
    if (before['published'] or {}).get('number') == to_number:
        raise ValueError('platform version %s of %s is already the published one' % (to_number, slot))
    freya.api('POST', '/api/v2/versions/workflow/%s/publish' % workflow_id, {'versionId': target[0]['id']})
    after = platform_versions(freya, workflow_id)
    if (after['published'] or {}).get('number') != to_number:
        raise RuntimeError('the platform did not publish version %s of %s' % (to_number, slot))
    trace('freya: rolled back %s from platform version %s to %s' % (slot, (before['published'] or {}).get('number'), to_number))
    return versioning.history_row('rollback', slot, workflow_id, after, provenance={
        'from_platform_version': (before['published'] or {}).get('number'), 'from_platform_version_id': (before['published'] or {}).get('id'),
        'to_platform_version': to_number, 'to_platform_version_id': target[0]['id'], 'reason': reason})
