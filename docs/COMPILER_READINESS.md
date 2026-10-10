# M4A compiler readiness boundary

This is a blocked compiler foundation, not a completed workflow generator.
`python -B -m builder.compiler.compile --brief <original-brief> --policy <candidate-json> --output outputs/workflow.json`
validates provenance, source coverage and explicit composition, reports blockers,
returns exit code 1 and writes no workflow. Repeat --brief in original input order.
No natural-language parser or fake bank runtime is substituted.

Current policy-ir/1 effects must be empty; policy-candidate/1 actions are opaque
labels. Successful structural validation never grants semantic or capability
approval. OpenAPI schemas describe HTTP operations but do not establish which
policy action invokes which operation or which response authorizes disclosure.

No Brief A/B runtime requirement is compiled yet. Brief A sections 5–10 require
identity attempts, tool-result checks, calendars, amount bounds, explicit confirmation
and retry state. Sections 4, 9, 11–12 require callbacks, transfer precedence and
exactly-one last close-call side effect. Brief B What changes 3–11 and Closing
require independent product state, combined confirmation, pending notes and transfer
restrictions. Current candidates cannot represent these safely.

The official workflow-format.md Node types, Transitions and End-call nodes document
valid runtime mechanisms; tools-and-code.md Tool function shape documents API calls
and code hooks. Those mechanisms do not supply missing policy semantics. Generating
prompts from statements would silently omit deterministic constraints and is unsafe.

Next implement one explicit executable profile: source-linked typed state variables,
OpenAPI operation bindings, deterministic guards, confirmation/response gates and
terminal side-effect ordering. Validate every effective rule against supported
capabilities before lowering to documented nodes. Keep bank values data driven.
Begin with a synthetic identity-gated read action and terminal close; reject payment,
calendar and multi-product requirements until implemented. This extension is needed
before a successful compilation test can honestly exist.

Offline checks:

```powershell
python -B -m unittest discover -s tests -v
git diff --check
```

No output is currently available for workflow validation. Once executable lowering
exists, validate its output with `python -B -m builder.compiler.validate outputs/workflow.json`.
Do not call Freya to bypass readiness. Model configuration remains untouched.
