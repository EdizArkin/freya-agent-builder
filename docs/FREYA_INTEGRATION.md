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
  adapter preserves supplied configuration exactly and refuses conflicting input,
  reads it back after saving and checks it again before publication. Organizer
  environment overrides are honored; this project never sets alternative models.
- `docs/workflow-format.md`, **Top-level keys** advises omitting `agent_config`,
  whereas the client's `update_workflow` repairs platform defaults. This adapter
  omits configuration when absent from the input, preserves supplied configuration,
  and blocks publication if stored defaults differ.
- **Node types**, **Variables**, and API **Validate without saving**: unknown
  node fields can disappear silently. Readback/export supports private review;
  structural validation cannot establish runtime behavior. The official base
  example also contains null fields despite the format page prohibiting nulls.
  This adapter does not rewrite those fields; the first server dry-run must resolve
  actual acceptance. The successful user-run validation finding is recorded below.

## PowerShell commands

Run from the repository root. External integration commands automatically read the
project-root `.env.sh` before importing the official client. Do not source it in
PowerShell or print its contents. Existing nonempty process variables take precedence.
Alternatively, load the key through a hidden prompt into the process environment:

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

Traces contain HTTP method and completed/failed status. Failures additionally contain
only `category`, `http_status`, `endpoint` and a fixed generic `hint`. Endpoint
values are allowlisted route templates, excluding IDs and query parameters. HTTP
statuses are obtained from typed HTTP errors or the official client's exact fixed
error prefix, never from server-body content. Timeout and network exceptions are
classified by type; unrecognized failures remain unknown rather than guessed.
Neither traces nor CLI diagnostics contain bodies, names,
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

Environment configuration is read from the current Python process at client import,
after the project-root `.env.sh` has filled absent or empty variables. The path is
resolved from the package location, not the shell working directory. `.env` and the
toolkit's `env.sh` are not loaded. Local validation does not load credentials.
A missing `.env.sh` preserves the existing environment-only behavior. A missing or
whitespace-only API key is a configuration error. `FREYA_API_URL` is optional;
the official documented default is `https://staging-app.freyavoice.ai`. An explicitly
empty value does not use that default and is rejected. Set an HTTPS origin without
path, query, credentials or fragment. Do not print environment values to diagnose.
401/403 mean authentication/access failure (403 may include gateway restrictions),
404 means endpoint missing, 400/422 mean validation failure, 429 means rate limit,
and 5xx means server/gateway failure. These categories do not reveal raw response
details. The previous generic failed-POST trace cannot retrospectively establish
which failure occurred; a separately authorized dry-run retry is necessary.

For HTTP 400/422, a complete JSON body retained in the official client's exception
can additionally yield `validation_details`: bounded allowlisted field-path tokens
and fixed error codes only. Unknown path components become `*`; arbitrary messages,
received values and unknown codes are never emitted. The official client truncates
bodies to 1,500 characters, so incomplete JSON yields no details. No undocumented
server error shape is claimed: extraction supports only structured path/code records
under bounded `data`, `error`, `errors`, `issues` or `details` containers.

The local example contains four optional null fields contrary to the format guide.
An independently saved local correction at
`local_freya/workflows/base-agent-no-optional-nulls.json` omits only null
`max_turn_transition` and `passthrough_default_transition` fields. The official
example remains unchanged. The user reported a successful live staging dry-run in
`outputs/freya-dry-run-006.json`: POST completed with both optional null removal and
configuration omission. Null removal alone previously returned HTTP 400. This
supports omitting adapter-injected configuration for this request, but does not
establish that all configurations are invalid or that deployed behavior is correct.
No new network request was made while finalizing this change.

Omission is now the default whenever input has no `agent_config`. Supplied config
is preserved exactly after credential and model-conflict checks; missing LLM fields
are not silently filled. The legacy `--omit-injected-agent-config` option remains a
dry-run-only no-op for existing commands. Save and publish retain stored-model checks:
if platform defaults do not match the mandated model, publication is blocked and
requires a separately reviewed correction. The adapter never silently changes it.

```powershell
python -B -m builder.freya.integration dry-run --workflow local_freya/workflows/base-agent-no-optional-nulls.json --allow-external --trace outputs/freya-dry-run-007.json
```

The configuration parser supports blank lines, full-line comments and simple
`export KEY=value` assignments, with optional single or double quotes. Trailing
comments must be separated by whitespace for unquoted values. Quotes preserve
spaces and `#` inside values. UTF-8, BOM and Windows CRLF are supported. The exact
allowlist is `FREYA_API_KEY`, `FREYA_API_URL`, `FREYA_WORKSPACE_ID`,
`FREYA_AGENT_PROVIDER`, `FREYA_AGENT_MODEL`, and `FREYA_SIM_CONCURRENCY`, all read
by the official client. This does not authorize simulations or model changes.

Unrelated export assignments are ignored: only allowlisted Freya keys are loaded,
and ignored values are never expanded or executed. Duplicate allowlisted assignments,
malformed quoting, multiline values, shell
commands, variable substitution, backticks and escape syntax are unsupported.
The file is parsed as data, never executed. All lines are validated before any
environment changes; errors produce sanitized configuration diagnostics without
names, paths or values. Parsing diagnostics include a fixed reason code and line
number. Separate categories identify environment-file read/encoding failures,
missing client files, client initialization failures, missing/unreadable workflows
and invalid workflow configuration. A missing optional `.env.sh` remains harmless.
No file content is logged. A whitespace-only process value
is nonempty and is preserved (the API-key check subsequently rejects it). Keep the
file ignored and local; credentials are still plaintext on disk and in the Python
process. Python's environment loading does not modify the parent PowerShell session.

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
