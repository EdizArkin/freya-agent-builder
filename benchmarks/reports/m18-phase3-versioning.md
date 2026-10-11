# M18 phase 3: platform versions, rollback, and the size of the phase 2 workflows

11 October 2026. One disposable workflow on Freya (`m18-version-probe`). No simulation, no model call, no bank request. No existing workflow was read for writing or changed. Built on `154fc35`.

## Result in short

| Question | Answer |
| --- | --- |
| Does Freya keep versions of one workflow under the same workflow id? | Yes. Verified |
| Is a local version id a platform version? | No. Ours is a hash of what was compiled; theirs is a number and an id the platform gives on every save |
| Is an earlier version preserved? | Yes: its content, read back by its id, equals what was sent |
| Is there a native rollback? | Yes: publishing an earlier version again. Verified, also through the builder's own function |
| Are the phase 2 workflows of 286 to 320 KB accepted? | Yes: all three pass the platform's validation, and the 320 KB one was created and stored unchanged |
| Is phase 4 unblocked? | Yes |

## What the platform does

All calls go through the kit's client. Routes: `POST /api/v2/workflow` (create), `PUT /api/v2/workflow/{id}` (save), `GET /api/v2/versions/workflow/{id}` (list), `GET /api/v2/versions/workflow/{id}/{versionId}` (one version with its content), `POST /api/v2/versions/workflow/{id}/publish` with `{versionId}`.

On workflow `af92e2b3-4d4a-4bf4-bc64-52c98e94d786`:

| Step | Platform versions afterwards |
| --- | --- |
| Create with v1 (the 320 KB workflow) | 1 `d8ce8487-84b9-4fd4-8d26-1ca27e14c265`, published, no parent |
| Save v2 (one sentence added to the system prompt) | 1 published; 2 `777ef36a-3c3a-4d0d-94d9-d8dbcf312b13`, **not published**, parent 1 |
| Publish 2 | 1 unpublished; 2 published |
| Read 1 and 2 by id | The content of 1 equals v1 as sent; the content of 2 equals v2 |
| Publish 1 again | 1 published; 2 unpublished. Nothing deleted, no version made |
| Save v1 again and publish | 3 `9e344470-21e3-4189-bf9f-426d88884970`, published, parent 2 |
| `deployment.rollback` to 1 | 1 published; 2 and 3 unpublished |

Facts that follow:

- A save alone changes nothing that runs: the new version is unpublished. (The kit's `save_workflow` saves and publishes in one step.)
- Exactly one version is published at a time. Publishing one unpublishes the other.
- A version has a number, an id, a parent, and a snapshot of the whole definition, `agent_config` included.
- **Reading the workflow returns the definition that was saved last, not the published one.** After publishing version 1 again, `GET /api/v2/workflow/{id}` still returned v2. What is published is told by the version list only. The kit's `export_workflow` reads the workflow, so after a rollback its export is not what runs.
- There are two ways to go back: publish the earlier version (no new version, history stays linear in numbers) or save its content again (a new version with the old content). The builder uses the first.

Not verified: that a call after a rollback runs the earlier version. The kit's documentation says calls and simulations run the published version, and a run reports the version it used; no simulation was allowed in this phase, so that is for phase 5. Also not tried: the create call's `agentVersionId`, branches, and the route that updates a version's metadata.

## Size

The three specs of M17, compiled with the builder of phase 2:

| Spec | Size | Platform validation (stores nothing) |
| --- | --- | --- |
| Brief A | 286,295 bytes | accepted |
| Brief A+B | 312,791 bytes | accepted |
| A+B with the memo | 320,286 bytes | accepted, then created as version 1 above and read back with no value changed or missing |

The largest workflow accepted before was 277 KB. The compiler was not touched.

## What the builder records now

| Where | What |
| --- | --- |
| `build.json` and the archived copy of a version | `builder_commit`: the commit that is checked out, read from `.git` without running anything |
| `deployment.json` | `platform`: the published version (number, id, parent), the newest one, and how many there are, as the platform lists them after the deploy |
| `outputs/<slot>/history.jsonl` | One line per deploy or rollback, only ever appended: time, operation, slot, workflow id, platform version number and id, our version id and content hash, builder commit, and for a rollback where it came from, where it went and why |
| `builder/freya/deployment.py` | `platform_versions` and `rollback(freya, slot, workflow_id, to_number, trace, reason)`. A rollback is refused before anything is published if the version does not exist or is already the published one, and is an error, not a history line, if the platform did not publish it |

Deliberately not built: a command for rollback, a comparison of two versions, a decision rule. Those are phase 4. A client that cannot list versions still deploys, without the platform part.

Limits of the metadata: the commit is the one checked out, and a working tree with uncommitted changes is not marked as such. The history is a local file under the git-ignored `outputs/`; the platform's own list is the authority, and `platform_versions` reads it.

## Tests

Focused runs only, as agreed; the full suite was not run in this phase.

| Run | Result |
| --- | --- |
| `tests/test_m18_versions.py` (new) | 9 tests, pass: what a deploy records, the append-only history, a client without versions, the commit in the build record, reading the commit from a branch, packed refs and a detached head, a rollback with its provenance, the three refusals, the state of a listing |
| `tests/test_freya.py`, `test_pipeline.py`, `test_architecture.py`, `test_m17.py` with it | 82 tests together, pass |
| Manifests | both match; the exposure log has four entries |

One expectation changed: the list of fields of a build record in `tests/test_m17.py` gains `builder_commit`.

Not rerun: the confirmation gate, the M16 enforcement and the offline scenarios. Nothing they use was touched (`runtime`, `compiler`, `validation` and the stand-in engine are unchanged since `154fc35`).

## Cost and what is left

Freya credits before and after: 491.2372 and 491.2372. Creating a workflow, saving, publishing and reading versions cost nothing that the balance shows. No Anthropic call. No bank request.

Left on Freya: the disposable workflow `m18-version-probe` with three versions, version 1 published. Evidence, git-ignored: `outputs/m18-version-probe/probe.json` and `history.jsonl`.

## For phase 4

- Deploy v2 to the same slot: a save and a publish, which the kit does. The history then has both lines.
- A simulation says which platform version it ran on (`agentVersionNumber`, already kept per scenario in the records of M17).
- Roll back with `deployment.rollback` and append its line to the slot's history.
- After a rollback, do not trust the export: check the version list.
- The M17 workflows are at platform version 1 with the builder of M17. Deploying the phase 2 build to them makes version 2 on the same ids, and version 1 stays there to go back to.
