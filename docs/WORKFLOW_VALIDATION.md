# Offline workflow validation

The standard-library validator in `builder/compiler/validate.py` checks a limited
structural profile. It makes no network calls and imports no old agent runtime.

```text
python -B -m builder.compiler.validate <local-workflow.json>
python -B -m builder.compiler.validate <local-workflow.json> --json
python -B -m unittest discover -s tests -v
```

Exit status is 0 on success, 1 for validation/read errors, and 2 for CLI usage errors.
JSON reports contain `valid`, `scope`, and `errors`; each error has a stable `code`,
structural `path`, and human-readable `message`. Diagnostics exclude document values
and node identifiers. Reports go to stdout; no snapshots, logs, or files are written.

## Supported checks

- UTF-8 loading, JSON syntax, duplicate keys at every nesting level, nonfinite
  constants, and overflowing floating-point numbers.
- Object root; nonempty string `initial_node`; nonempty object `nodes`; initial-node
  membership in `nodes`; object nodes and nonempty node identifiers.
- Optional object `global_nodes`; optional transition arrays with object entries
  and nonempty string `target_node` references in the combined node mappings.
- Optional root/node tool arrays with object entries, nonempty string `type`, object
  `function`, and nonempty string function name. Optional function `parameters` and
  tool `config` must be objects; optional `handler` must be a string.

These checks define our supported offline profile, not universal Freya requirements.
Optional fields are not made mandatory merely because a reference contains them.
Unknown fields and node/tool type values remain allowed. Global-target membership
is structural; runtime semantics remain unverified.

## Limitations and evidence boundaries

Local reference inspection provides observations, not official API verification.
Passing this validator does not establish official import/deployment acceptance.
It does not validate condition expressions, handler implementations, parameter
schema dialects, model settings, state propagation, code safety, reachability,
policy enforcement, customer identity, or required bank records. It does not scan
arbitrary workflow content for secrets.

Official contracts remain necessary for compiler output, deterministic call-time
guards, deployment/export, simulation, and submission. No supported-node allowlist
or private endpoint assumptions are embedded here.

All unit fixtures are synthetic. Real references stay under ignored `local_freya/`
and may be validated separately without snapshots. Keep this separation even when
the GitHub remote is private. Do not redirect private content into tracked output.
