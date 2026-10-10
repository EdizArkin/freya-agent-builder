# Offline brief extraction and composition

Milestone 2B adds `policy-candidate/1`, a small extension that wraps unchanged
`policy-ir/1` rule envelopes. No live provider adapter, workflow compiler, policy
executor or deployment interface is included. All results remain non-compilable.

## API and source handling

`builder.brief.sources.load_briefs(paths)` accepts an ordered list of UTF-8 paths.
It retains original bytes, content-addressed document IDs and input order. Repeated
identical documents and empty documents are rejected. Offsets are half-open Unicode
code-point offsets in the exact decoded text, not byte offsets. Document hashes use
original bytes; span hashes use UTF-8 encoding of the cited text, as in the v1 IR.
Sections split at Markdown ATX headings, or retain an entire plain-text document.
All nonblank sections are conservatively considered potentially normative.

`builder.policy.pipeline.extract_briefs(paths, provider, config=ModelConfig(...),
max_repairs=1)` returns sources, candidate, effective composition, validation,
coverage and attempt count. Repairs are bounded to 0–2, meaning 1–3 adapter calls.
Without a provider it returns explicit missing-provider and coverage findings.
The pipeline itself performs no network operations. Adapters are trusted Python
code; this interface is not a sandbox and callers must choose an offline adapter
when offline operation is required. Provider exceptions receive sanitized messages.
Candidate JSON and source text remain private in-memory data; do not log them.

## Strict candidate profile

The extractor receives sections, original sources, model/provider configuration,
format instructions and prior validation findings. It returns JSON text only.
Duplicate keys, nonfinite numbers, unknown fields and unsupported versions fail.
The JSON root has exactly `schema_version: "policy-candidate/1"`, `rules` and
`coverage`. Document metadata is supplied by ingestion, never by the extractor.

Each rule has exactly:

- `envelope`: the existing v1 Rule (`id`, `category`, `scope`, `status`,
  `source_refs`, `condition`, `effects`). This profile requires null condition and
  empty effects. Original-source provenance and unique IDs are validated by v1.
- `statement`: nonempty source interpretation for human review.
- `action`: opaque nonempty action name; no tool binding is implied.
- `selector`: `*` for the entire scope or an opaque exact scope member.
- `guard`: null or `{field, op: "equals", value}` with a JSON scalar value.
- `parameters`: `{name, value, source_refs}` entries with unique names, scalar
  values and separately validated provenance.
- `overrides`: `{target, selector}` entries; selector is null for full replacement
  or an exact scope member for partial replacement.

Only obligation, prohibition and guard categories are interpreted. V1 scopes and
statuses remain unchanged. Other categories and non-candidate statuses are retained
but produce blocking diagnostics. Unrepresentable requirements should instead be
classified explicitly through source coverage, avoiding invented executable logic.

Coverage entries have exactly `section_id`, `status`, `rule_ids`, `reason`.
Status is represented, unresolved or unsupported. Reasons must be nonempty.
Represented entries need rule IDs citing spans inside that section. Missing,
duplicate or unknown sections, unsupported/unresolved entries and false citations
produce findings. Citation coverage cannot prove that an interpretation is complete
or semantically faithful; human review and future semantic validation remain needed.

`candidate.dumps` provides deterministic serialization of validated candidates;
`candidate.loads(text, bundle)` is the strict validation boundary. Direct dataclass
construction does not substitute for that boundary.

## Composition

Document order records context but never creates precedence. Replacement rules must
explicitly identify targets, preserve action and scope, and retain the exact
selector for full overrides. Scoped overrides subtract an exact member from a
wildcard target, preserving the remainder. Missing targets, cycles, incompatible
domains, different guard contexts and overlapping replacements block an effective view.
A wildcard with both partial and full replacements is explicitly unsupported. Candidate rules
remain intact; lineage identifies both endpoints and preserves their citations.
Provenance proves the spans exist, not that the source author authorized an override.

Contradiction checks cover obligation versus prohibition and incompatible scalar
parameters on overlapping obligations, with identical guards or an unconditional
context. They are deliberately incomplete: differing fields and guards receive no
symbolic reasoning. An effective view with contradiction findings is for inspection
only. No successful report enables compilation.

## Synthetic offline invocation

Run this from the repository in Python; it writes only a temporary synthetic brief.
The adapter deliberately classifies an unsupported instruction instead of inventing
its meaning. Unit tests also exercise represented rules and composed amendments.

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from builder.llm.extraction import ModelConfig
from builder.policy.pipeline import extract_briefs

class OfflineMock:
    def extract(self, request):
        return json.dumps({
            "schema_version": "policy-candidate/1", "rules": [],
            "coverage": [{"section_id": s.id, "status": "unsupported",
                          "rule_ids": [], "reason": "Synthetic mock has no interpreter."}
                         for s in request.sources.sections]})

with TemporaryDirectory() as directory:
    brief = Path(directory) / "arbitrary.md"
    brief.write_text("# Synthetic policy\nApply a calendar-dependent deadline.\n", encoding="utf-8")
    result = extract_briefs([brief], OfflineMock(),
                           config=ModelConfig("offline-mock", "synthetic"), max_repairs=0)
    print(result.attempts, result.coverage.valid, result.compilable)  # 1 False False
```

## Limits and next step

No dates, calendars, arithmetic, inequalities, compound guards, quantified scopes,
state machines, side effects, idempotency or tool semantics are implemented. Exact
selectors are opaque strings, not hierarchical product models. Markdown segmentation
does not parse fenced code or nested document structures. Conflicts outside the
small represented subset can remain undetected; multi-level scoped amendment chains
need further semantics before runtime use. Coverage never asserts natural-language
understanding. There are no hardcoded bank values or public-test responses.

The smallest next step is a configured structured-output extraction adapter with
explicitly authorized model access, retaining this validation and repair boundary,
followed by private review of extracted source-linked rules. A real workflow still
requires richer approved semantics where needed, OpenAPI action binding, runtime
capability checks and a compiler; no deployment readiness is claimed here.
