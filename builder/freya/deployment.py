"""The one place where the builder changes something on Freya.

deploy() does, in this order:
1. dry run   - the platform validates the workflow and stores nothing;
2. save      - the kit's save_workflow. For a slot that is not in the registry it
               creates a new workflow and its agent. For a known slot it replaces
               the definition of the existing workflow AND publishes the new
               version. The kit offers no save without publish.
3. export    - reads the stored workflow back into a local file.

There is no approval step and no rollback. The caller's --deploy flag is the only gate.
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
    return versioning.deployment_record(slot, saved, workflow, created)
