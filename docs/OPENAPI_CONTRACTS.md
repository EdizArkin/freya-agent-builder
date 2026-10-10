# Offline OpenAPI contracts

Inspect a local specification without network access or output files:

```text
python -B -m builder.brief.openapi <specification.json>
python -B -m unittest discover -s tests -v
```

Exit status is 0 for a supported contract, 1 for input/profile errors, and 2 for CLI
usage errors. The summary prints only operation positions, HTTP methods, identity
source labels, counts, and fixed diagnostic messages. It omits endpoints, operation
names, schema defaults, enum values, document descriptions, and source filenames.
Diagnostic locations use numeric positions to avoid echoing private identifiers.

## Python interface

`load_contract(path)` reads strict UTF-8 JSON, rejects duplicate keys and nonfinite
numbers, and returns `LoadResult`. `load_document(document)` accepts parsed JSON
data without mutating it. Neither function changes the source or writes artifacts.

The result contains typed `Contract`, `Operation`, `Parameter`, `MediaSchema`, and
`Response` dataclasses plus `Diagnostic` records. `result.valid` is false when an
error exists; an incomplete contract must not be used for compilation. Warnings
identify omitted metadata that requires review before integration.

`result.to_dict()` is JSON-serializable. It is a full representation, not a sanitized
report: schema annotations, defaults, and enum values may be confidential. Keep any
serialization under ignored `outputs/` or `local_freya/`; do not commit it.

Operations are ordered by path and method, parameters by location/name, responses
by status, and content by media type. Explicit `operationId` takes precedence; the
fallback is `METHOD /path`. Collisions are errors. Path-level parameters are inherited
and operation-level entries override the same location/name pair. Required body
fields are preserved in the schema; required parameters and body flags are explicit.

File-based source hashes identify exact original bytes. In-memory source hashes
identify canonical JSON with sorted keys. Nested dictionaries are detached from the
source; frozen dataclasses do not imply deep immutability.

## Supported profile

- OpenAPI 3.0.x and 3.1.x JSON, standard HTTP operation methods, optional request
  bodies, multiple request/response media schemas, exact/status-range/default
  responses, and responses without a body schema.
- Path/query/header/cookie schema-based parameters; explicit serialization options
  are preserved and checked, but not executed. Path placeholders must match declarations.
- Finite same-document JSON pointers on path items, parameters, request bodies,
  responses, and nested schemas, including `~0`/`~1` escapes and array indexes.
- Object properties, nested arrays, required fields, additional properties, enums,
  types, numeric/length/count bounds, patterns, formats, nullable 3.0 declarations,
  annotations, and 3.1 boolean schemas, null type unions, and constants.

This is a contract extractor with basic declaration checks, not a complete OpenAPI
or JSON Schema validator. Constraints and formats are preserved, not applied to
customer data. Multiple media types remain separate; no serializer is selected.

## Explicit limitations

External/anchor/percent-encoded references, recursive references, reference siblings,
schema composition (`allOf`, `oneOf`, `anyOf`, `not`), discriminators, custom schema
dialects/base URIs, callbacks, webhooks, parameter content forms, and custom media
encoding produce errors. Other unrecognized schema keywords also produce errors.
Resolve these diagnostics before compiling; the loader never guesses their meaning.

Response headers/links, security requirements, and vendor schema extensions produce
warnings and are not normalized. Servers, documentation metadata, examples outside
schemas, and extension-defined operations are outside this contract's scope. It
does not establish request URLs, authentication, wire serialization, or policy rules.
Original specifications remain authoritative for those later integration steps.

Unit fixtures are independently authored synthetic data. Official toolkit materials
are inspected only locally and remain ignored. No dependencies beyond Python's
standard library or existing repository code are introduced.
