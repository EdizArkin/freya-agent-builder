"""The one place where the builder changes something on Freya.

deploy() does, in this order:
1. dry run   - the platform validates the workflow and stores nothing;
2. save      - the kit's save_workflow. For a slot that is not in the registry it
               creates a new workflow and its agent. For a known slot it replaces
               the definition of the existing workflow AND publishes the new
               version. The kit offers no save without publish.
3. export    - reads the stored workflow back into a local file.

There is no diff against the deployed workflow, no approval step and no rollback.
The caller's --deploy flag is the only gate.
"""
from builder.freya import versioning


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
