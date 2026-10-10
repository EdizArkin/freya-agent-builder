# Executable profile foundation: policy-executable/1

This milestone introduces a separate typed finite-action graph. It does not make
candidate policies executable, emit Freya workflows or authorize runtime execution.
Even a structurally valid executable graph has `compilable=False`: semantic approval
and Freya lowering remain absent. This is a partial M4B implementation, not complete
bank-policy coverage.

## Schema

`ExecutablePolicy` contains schema_version, contract_sha256, variables, actions,
initial_action, identity_variable, foundation and lineage. Frozen dataclasses and
enums are in builder/policy/executable.py. The foundation supplies original document
metadata; each action supplies validated SourceRefs. The OpenAPI document hash pins
the operation contract. Actions bind exact loader operation identities, not guessed
operationIds. Changing the specification requires revalidation.

StateVariable has id, type (string/number/integer/boolean), and optional product.
Product labels are preserved but iteration is unsupported. Predicate is a typed
variable/equality comparison; no code, arbitrary operators or dynamic expressions.
Argument binds a request field to a state variable. ResponseEffect binds a documented
status and response JSON pointer to typed state. Action contains id, role, operation,
arguments, preconditions, postconditions, effects, success/failure target IDs,
mandatory flag and source_refs. A close action must have no outgoing edges. Other
actions need both paths. Unknown target IDs, cycles and unreachable mandatory
actions are rejected. All foundation rules must map to action IDs; this does not
prove semantic fidelity. Lineage is retained but any executable amendment lineage
remains unsupported rather than implicitly resolving precedence.

Strict loads/dumps supports deterministic round trips, finite JSON, typed enums,
unknown-field rejection and separate byte-backed provenance validation. Do not log
serialized policy content. Direct dataclass construction is intended for trusted
Python callers; use loads for untrusted JSON. Run validate with the actual contract
and original bytes. Validation reports only fixed codes, never values.

## Synthetic shape example

An action fragment, not a complete bank policy or Freya workflow:

```json
{
  "id": "lookup",
  "role": "protected_read",
  "operation": "POST /synthetic_lookup",
  "arguments": [{"field": "subject", "variable": "subject"}],
  "preconditions": [{"variable": "verified", "equals": true}],
  "postconditions": [],
  "effects": [],
  "success": "close",
  "failure": "close",
  "mandatory": true,
  "source_refs": []
}
```

Empty provenance in this illustrative fragment is invalid. Tests construct complete
synthetic profiles with hashes and source offsets; no toolkit content is copied.

## Supported checks versus bank blockers

Protected reads, promises and notes require an explicit true identity precondition.
Only identity-check actions may write identity state via documented response paths.
Request required fields, property existence and scalar types come from the OpenAPI
loader. Parameter locations other than JSON bodies are unsupported. Scalar schema
constraints (including enum/format/bounds) without proven state domains produce
argument_constraints_unproven; the validator never invents argument values. Response
pointer support is object properties only; arrays, escaped tokens and variant
responses are not inferred. HTTP success alone does not assert business success.

Brief A Identity section 5 and Stating the debt section 6 can now be represented as
response state followed by guarded account reads. This does not yet implement two
questions, date normalization, refusal counts or successful-verification semantics.
Sections 10 and 12 can express action order and terminal close labels, but promise
confirmation, exactly-once delivery and semantic outcomes are not approved.
Equality predicates and explicit success/failure paths are representable; action
roles are explicit author intent, not automatically proven by OpenAPI operation names.
No role annotation alone authorizes a bank side effect.

Exact remaining blockers:

- Brief A sections 4 and 7: callback windows, holidays, business-day arithmetic,
  deadlines and relative-date interpretation need calendar expressions.
- Sections 5 and 9: bounded retries, counters, refusal and hardship precedence need
  typed state updates and event semantics.
- Sections 8 and 10: amount bounds, product floors, explicit confirmation, business
  response predicates, idempotency and promise locking need approved semantics.
- Sections 11–12: transfer interruption, last-tool close and outcome selection need
  enforceable side-effect rules. Stateful action roles are retained but rejected with
  stateful_bank_semantics_unapproved.
- Brief B What changes 1–11 and Closing: per-product iteration, shared confirmation,
  pending notes, no-notes-on-transfer, aggregation and amendment semantics remain
  unsupported. Input order never invents override precedence.

Official workflow-format.md documents Node types, Transitions (string-based equals),
Variables (shared call state) and End-call nodes. tools-and-code.md documents Tool
function shape and runtime handlers. Typed boolean IR predicates must not be lowered
naively to Freya string comparisons; both runtime spelling/initialization and failure
paths need explicit compiler handling. No Freya schema or mandated model is changed.

## Next milestone

Implement reviewed semantic elaboration and Freya lowering for only the synthetic
identity-gated read/close subset: initialized state, trusted verification bindings,
business-response gates and runtime equivalence tests. Then add one bounded promise
confirmation path with proven OpenAPI domains. Do not compile Brief A/B until every
mandatory effective rule is supported. The existing compiler boundary still refuses
candidate input; there is no successful bank workflow generation yet.

```powershell
python -B -m unittest discover -s tests -v
git diff --check
```
