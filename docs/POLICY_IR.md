# Policy IR foundation (Milestone 2A)

This is an offline envelope and provenance profile, not an extractor, policy engine,
or workflow compiler. It uses Python 3.11 dataclasses and enums without dependencies.

## Interfaces

- `builder.policy.models`: `SourceDocument`, `SourceRef`, `Rule`, `PolicyIR`,
  `BooleanLiteral`, `ValidationFinding`, `ValidationReport`, and vocabulary enums.
- `builder.policy.codec`: `loads(text)` returns typed `PolicyIR`; `dumps(policy)`
  returns canonical JSON. Invalid input raises `IRValidationError` with a sanitized
  `.report`. Neither operation reads files or performs provenance verification.
- `builder.policy.validation`: `validate_policy(policy, sources)` verifies the
  structural profile and original source bytes. `sources` maps document IDs to
  `bytes`. `validate_data(data)` is a structural check only.

Run all synthetic tests with:

```text
python -B -m unittest discover -s tests -v
```

## Supported document shape

The root requires exactly `schema_version`, `documents`, and `rules`.
The only supported version is `policy-ir/1`.

Each source document requires:

- `id`: nonempty string, unique among documents.
- `sha256`: lowercase 64-character SHA-256 hash of the exact original bytes.
- `input_order`: unique nonnegative integer; booleans are rejected.
- `document_kind`: nonempty descriptive string, not an execution instruction.

At least one document is required. Document and rule IDs have separate namespaces.
Array order is preserved; `input_order` records composition order but does not grant
authority or implement precedence. Empty rule arrays are allowed for an envelope
under construction and do not imply a complete policy.

Each rule requires `id`, `category`, `scope`, `status`, and nonempty `source_refs`.
Rule IDs are unique. Supported vocabularies are:

| Field | Values |
|---|---|
| Category | `guard`, `obligation`, `prohibition`, `calculation`, `ordering`, `outcome` |
| Scope | `call`, `product`, `question`, `action` |
| Status | `candidate`, `unresolved`, `superseded` |

These labels describe envelopes; none activates or authorizes a rule. There are no
bank-specific outcomes, questions, holidays, product types, or payment values.

`condition` may be omitted, null, or exactly an object with `op: "literal"` and a
boolean `value`. It is retained as typed data and never evaluated. Every other
operator or extra field is rejected. `effects` may be omitted or an empty array;
nonempty effects are explicitly unsupported. No Python, expression strings,
callables, code execution, or arbitrary payload interpretation is accepted.

Each source reference requires exactly `document_id`, `section`, `start_offset`,
`end_offset`, and `span_sha256`. The document ID must resolve; section is a nonempty
descriptive label. Every reference must pass validation, even when another reference
on the same rule is valid.

## Provenance conventions

Supplied bytes must decode as UTF-8. Whole-document hashes use bytes directly.
Offsets index Unicode code points in the exact decoded text, starting at zero;
the span is `[start_offset, end_offset)`, nonempty and within bounds. Span hashes
use SHA-256 of the selected text encoded as UTF-8.

No newline, Unicode, or BOM normalization occurs. A UTF-8 BOM, if supplied, counts
as a decoded character. Text-mode loading that changes CRLF to LF changes the
document identity; supply original bytes instead. Missing bytes, invalid encoding,
changed documents, invalid offsets, and mismatched spans produce errors.

A matching hash proves content identity, not source authority, correct section
labeling, rule interpretation, or completeness. Section-heading verification and
semantic support checks are future work.

## Serialization and reports

JSON keys are sorted, arrays preserve order, and output is compact with Unicode
preserved. Deserialization rejects duplicate keys at any depth, nonfinite constants,
overflowing numeric values, unknown fields, invalid types, and unsupported versions.
Optional absent payload fields serialize canonically as `condition: null` and
`effects: []`. Canonical serialization is stable after a typed round trip; it is
not a byte-for-byte reconstruction of the input JSON formatting.

Reports contain fixed diagnostic messages and indexed structural locations, never
input identifiers or source text. `valid` covers only checks performed by this
profile. `provenance_checked` indicates that source verification was attempted;
read it together with `valid` and findings. Parsing alone leaves it false.

Every report has `compilable: false`, `semantic_validation: "not_implemented"`, and
`capability_checks: "not_implemented"`. Successful provenance cannot promote a
candidate to an executable policy. A report is not a tool authorization token.

Models and serialized IR can contain private source metadata. Store artifacts only
under ignored locations and never commit original briefs or extracted private
policies. Current functions create no artifacts or logs.

## Deferred features

Semantic expression/effect evaluation, composition and overrides, authority/effective
dates, calendars, counters, confirmation state, tool bindings, idempotency,
LLM extraction, Freya capability checks, and compilation are not implemented.
Frozen dataclasses and tuple fields help preserve envelopes but are not a substitute
for validating objects constructed directly in Python.
