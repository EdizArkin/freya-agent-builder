# First offline identity slice compilation

`builder.compiler.identity_slice` lowers exactly three policy-executable/1 actions:
identity_check → protected_read → close. Failure of identity routes directly to
close. Both account outcomes route to close. Close performs a bank API request and
then a distinct Freya hang-up terminal. Extra actions, cycles, alternate edges,
extra effects, product state and unsupported conditions are rejected.

This is a working synthetic API-ready slice, not a complete bank phone conversation.
It does not ask questions, normalize spoken dates, enforce two question ordering,
retry answers, disclose debt, negotiate, or select bank-specific outcomes. Input
answers must already be supplied as canonical strings. The configured close outcome
is explicit policy data, not inferred from a public test or from account contents.
The independently authored synthetic contract uses non-bank enum values and a
non-routable example host. Do not deploy this fixture against the bank service.

## Resolved semantics

- Trusted state: an initial documented code node resets identity_verified to false,
  overwrites compiler-owned routing flags and validates required string inputs.
  Constants are initialized from explicit, schema-validated domains. Identity cannot
  be configured as an input or constant. Verification pre-hooks reset trusted flags
  again; no conversation extraction can overwrite them because there are no
  conversation nodes. Missing inputs hang up without calling bank tools.
- Identity: Brief A section 5 requires both questions to pass; section 6 requires
  account read afterward. Bank OpenAPI get_account summary explicitly refuses reads
  until both pass. Therefore the bank full-identity condition is all_verified, not
  the per-answer verified value. Configuration must explicitly select all_verified.
  A post-hook validates the documented envelope: status success, numeric status_code
  200, body ok exactly true and all_verified exactly true. False, missing, malformed
  or string truth values fail closed. The compiler-owned mapped result is a routing
  string; a following code gate sets the trusted boolean. No hook relies on mutation
  of undocumented flattened result fields.
- Bindings: explicit HTTPS base_url and method/path identities pinned to OpenAPI
  bytes. Required body fields, scalar types and enum constants are validated against
  that contract. Inputs with enum restrictions are rejected unless fixed constants
  prove the domain. Unsupported schema constraints are rejected. Runtime string
  inputs must be nonempty; caller-provided identity state is overwritten.
- Closing: close_call is __function__:api_call in an api_request node. It is never
  substituted with end_call. A separate end_call node uses a terminal function with
  __function__:end_call and message_mode none. Close tool errors still reach hang-up;
  this does not promise successful or exactly-once bank delivery. maxRetries is zero.
- Control flow: verification hook runs before node-level response_mapping; the gate
  runs afterward. Read precondition is enforced before entering the API node through
  the sole verified transition. Extra postconditions are rejected. Tool failures or
  unmapped responses retain the rejection flag and cannot enter account read.
  Init and gate snippets are compiler-owned, fast and wrapped with safe fallbacks.

Sources: official tools-and-code.md **Tool function shape**, **pre_actions versus
tools**, **response_mapping**, **What result looks like in a post-hook**, **Code
nodes and code_exec**; workflow-format.md **Passthrough nodes**, **Condition shapes**,
**End-call nodes**. Code nodes are necessary to initialize trusted state and avoid
Freya's boolean-to-string routing ambiguity. All passthrough nodes have explicit
fallbacks; mappings use data-prefixed paths. The compiler emits no agent_config and
does not change AGENT_LLM. Integration's stored-model checks remain required later.

## Offline commands

Use a new output filename if the example output already exists:

```powershell
python -B -m builder.compiler.identity_slice --brief examples/identity_slice/brief.txt --policy examples/identity_slice/policy.json --tool-spec examples/identity_slice/openapi.json --config examples/identity_slice/config.json --output outputs/identity-slice-workflow-002.json
python -B -m builder.compiler.validate outputs/identity-slice-workflow-002.json
python -B -m unittest discover -s tests -v
git diff --check
```

Output 001 was successfully generated and structurally validated locally. Tests also
inspect documented node/handler placement and execute hooks/gates with success,
failure, malformed responses, HTTP errors and spoofed initial state. There is no
official executable workflow schema validator in this toolkit; matching documented
structures plus the local validator does not establish Freya server/runtime acceptance.
No API, simulations or model calls were performed. Compiler acceptance is specific
to this profile; general executable IR reports remain non-compilable.

Remaining blockers to Brief A/B: interactive trusted answer collection, two questions
and retries, confirmation and promise effects, calendars, amount bounds, callbacks,
transfers, notes and independent product state. First add source-linked question
collection and bounded identity progression, then a reviewed LLM extractor producing
this constrained IR; never claim the synthetic slice implements all mandatory rules.
