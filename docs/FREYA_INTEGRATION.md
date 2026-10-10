# Freya integration vertical slice

This adapter loads a trusted local `freya.py` at runtime; the toolkit is neither
copied nor modified. Import alone makes no requests. No third-party dependencies
are needed. Local validation does not import the client or require credentials.

## Official contracts and deliberate separation

References below are local, ignored organizer materials:

- `local_freya/starter_kit/docs/api.md`, **Workflows / Validate without saving**:
  POST `/api/v2/workflow?dryRun=true` stores nothing. Its result fields are
  undocumented; the adapter suppresses them. HTTP errors and explicit failure
  envelopes fail. A successful request is not proof of policy compliance.
- **Create**, **Update**: POST creates workflow and agent; PUT replaces the whole
  workflow and saves a version. `workflow.id` and `workflow.agentId` are separate.
- `local_freya/starter_kit/freya.py`, `update_workflow`: GET workflow and versions
  with `workspaceId`; publish with POST and `{versionId}`. The helper automatically
  publishes, so this adapter reuses `api` rather than that helper. Likewise it does
  not use `save_workflow` or the raw artifact writer `export_workflow`.
- `freya.py`, `AGENT_LLM`: supplies the event phone-agent configuration. The
  adapter injects it into a copy before submission, refuses conflicting input,
  reads it back after saving and checks it again before publication. Organizer
  environment overrides are honored; this project never sets alternative models.
- `docs/workflow-format.md`, **Top-level keys** advises omitting `agent_config`,
  whereas the client's `update_workflow` repairs platform defaults. This adapter
  supplies only the required LLM fields, preserving other supplied configuration,
  and blocks publication if stored defaults differ.
- **Node types**, **Variables**, and API **Validate without saving**: unknown
  node fields can disappear silently. Readback/export supports private review;
  structural validation cannot establish runtime behavior. The official base
  example also contains null fields despite the format page prohibiting nulls.
  This adapter does not rewrite those fields; the first server dry-run must resolve
  actual acceptance. No claim of server acceptance has been tested locally.

## PowerShell commands

Run from the repository root. Do not source `env.sh` in PowerShell or print its
contents. Load the key through a hidden prompt into the process environment:

```powershell
$freyaSecureKey = Read-Host 'Freya API key' -AsSecureString
$freyaKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($freyaSecureKey)
try {
    $env:FREYA_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($freyaKeyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($freyaKeyPointer)
    Remove-Variable freyaSecureKey, freyaKeyPointer
}
$env:FREYA_API_URL = 'https://staging-app.freyavoice.ai'
$env:FREYA_WORKSPACE_ID = Read-Host 'Authorized workspace ID'
```

Only key and HTTPS URL are needed for server dry-run. An explicit workspace ID is
required for saving, publishing and export; first-workspace selection is avoided.
The official client captures environment at import, so set it before each command.
Keep any organizer-supplied `FREYA_AGENT_PROVIDER` / `FREYA_AGENT_MODEL` values;
leave them unset otherwise. Do not set builder-model values in those variables.

Local validation, no external operations:

```powershell
python -B -m builder.freya.integration local --workflow local_freya/starter_kit/examples/base-agent.json
```

First server-side dry-run, **only when authorized**:

```powershell
python -B -m builder.freya.integration dry-run --workflow local_freya/starter_kit/examples/base-agent.json --allow-external --trace outputs/freya-dry-run-001.json
```

Save without publishing, **only when creation/update is authorized**:

```powershell
python -B -m builder.freya.integration save --workflow local_freya/starter_kit/examples/base-agent.json --allow-external --trace outputs/freya-save-001.json
```

For subsequent saves add `--workflow-id <existing-workflow-id>` to avoid allocating
another agent. Save prints workflow ID, agent ID, version ID and version number.
Keep those identifiers for the following commands. The adapter does not maintain a
hidden slot file. A successful save does not promise the platform has not already
published an initial version; initial creation behavior is not documented.

Export the stored definition for review, **only when reads are authorized**:

```powershell
python -B -m builder.freya.integration export --workflow-id <workflow-id> --output outputs/freya-export-001.json --allow-external --trace outputs/freya-export-trace-001.json
```

Publish **only after explicit approval of the saved workflow/version**:

```powershell
python -B -m builder.freya.integration publish --workflow-id <workflow-id> --agent-id <agent-id> --version-id <version-id> --version-number <version-number> --allow-external --approve-publish --trace outputs/freya-publish-001.json
```

Angle-bracket identifiers above are placeholders: replace them with the exact save
result before executing. Publish checks the selected version is still newest and
targets that exact version ID; it never silently switches to a newer version.
Use new artifact filenames for every command; existing artifacts are not overwritten.

Afterwards remove the process key:

```powershell
Remove-Item Env:FREYA_API_KEY
```

## Safety, artifacts and remaining limits

`FreyaAdapter(client)` disables external operations by default. Explicit external
opt-in still leaves it dry-run-only unless write mode is selected. Dry-run mode
allows exactly the documented validation POST and forbids all reads/writes/publish.
Publication additionally requires `approved=True` or `--approve-publish`. No
simulation or call methods are exposed.

Traces contain only HTTP method and completed/failed status, never bodies, names,
URLs, headers, exceptions, customer values or server responses. Workflow exports
retain separate workflow and agent IDs but strip `test_variables`. Export is a
sanitized review artifact, not a byte-identical submission export. Literal credential
fields, known process secrets, credential URL userinfo and recognizable bearer/key
strings are rejected before submission/export. This conservative check may reject
legitimate parameter names. It cannot classify every arbitrary secret embedded in
free text; privately review workflow content and never embed credentials. Supply
runtime secrets through officially supported external configuration instead.

Artifacts are restricted to resolved paths below ignored `outputs/`. Neither
artifacts nor toolkit content belong in Git. Python import executes a trusted local
client: do not point `--client` at untrusted code. TLS origins are required, but
HTTPS redirects follow the official client's behavior; use only the authorized
Freya endpoint. Files have normal OS permissions, not encrypted storage.

Version discovery compares pre/post version IDs and requires one unambiguous new
newest version. This is not a transactional lock: concurrent editing can race the
readback or publication checks. Avoid concurrent writers. Failed writes are not
retried automatically; inspect the workspace before retrying creation. The API docs
leave readback fields partly unconfirmed; the adapter follows the actual client and
blocks on incompatible shapes. Unsupported successful response shapes can therefore
leave a saved workflow requiring manual inspection. The CLI suppresses exception
content and returns failure, while reusable API methods provide sanitized diagnostics.

A real dry-run is still needed to establish acceptance of the local base workflow
and model configuration. Brief extraction, policy compilation and runtime safety
checks remain separate unfinished work. No network calls were made during this
milestone, and mocked tests cannot establish server behavior.
