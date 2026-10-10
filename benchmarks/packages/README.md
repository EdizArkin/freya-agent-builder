# Evaluation packages

Five briefs with their tool API and a rule-level answer key, for measuring whether the builder understands a brief it was not built on.

**Every package here is synthetic and was prepared by the author of the builder.** None is independently validated, and none is blind in the strict sense: the same author wrote the briefs, the keys and the builder. What the packages do guarantee is listed under "What is kept apart".

## The packages

| Id | Tier | Form | What it is |
| --- | --- | --- | --- |
| `c` | development | clean | Northwind Credit Union: another security check, conditions on taking a promise at all |
| `d` | development | clean | Harbor Lending: up to four products that depend on each other |
| `e` | development | clean | Meridian Energy: rules that replace other rules, a Sunday-to-Thursday week |
| `f` | selection | raw | Calder Savings Bank: a mail, a policy extract, a memo that amends both, a code list; every API field renamed |
| `g` | final | clean | Brightwater Mutual: up to three products, month-day-year dates, a Saturday working day |

`c`, `d` and `e` are the synthetic briefs of `../synthetic_briefs/` with their answers removed: the introduction that says what each brief tests, the scenario table and the table of what the schema can carry. Their tool API is the starter kit's file with the brief's overlay applied; the kit file is third-party material and is not copied here.

## Three kinds of evidence

| Tier | May be used for | Its results are called |
| --- | --- | --- |
| development | schema design, prompt changes, debugging, prompt optimisation; as often as needed | development results |
| selection | one choice among candidates that were frozen beforehand | model-selection evidence, never a final or unbiased test |
| final | one run of what was chosen, after everything is frozen | final holdout results |

The label follows the use. If anything is changed because of a result on the final package, that package's results are model-selection evidence from then on and the report has to say that no unbiased final test exists. `rules.py` writes every evaluation of an extracted spec on `f` or `g` to `../reports/m15.exposure.jsonl`, refuses `g` before the candidates are frozen, and refuses a second configuration on `g` unless it is relabelled.

## What is kept apart

- `input/` holds what an extraction may read: the brief documents and the tool API. `key/` holds the answers. `packages.inputs()` reads only the first and `packages.key()` only the second; a test runs an extraction with `key/` deleted.
- `MANIFEST.json` holds a hash of every input and key file. `python benchmarks/harness/packages.py verify` compares, and `rules.py` refuses to evaluate packages that differ from it.
- A correction to a frozen package raises its `version` and adds a line to the `changelog` in `package.json`. Results on the earlier version stay in the reports.

## A key

| File | Covered by the freeze | Content |
| --- | --- | --- |
| `rules.json` | yes | one entry per rule: id, source document and place, the passage itself (checked to be verbatim), category, whether it is critical, anchor words; where it applies, what it amends, a known gap, or that its offline evidence is insufficient |
| `checks.json` | yes | checks, each linked to rule ids. A `fact` is a product and a clock with the expected limit, date or name, and says how the number was worked out |
| `bank.json` | yes | only for a renamed tool API: its field names against those of the offline bank |
| `reference.spec.json` | recorded, may follow the schema | the best hand-written agent-spec/1 for the brief |
| `reference.fit.json` | recorded, may follow the schema | per rule, whether the schema carries it: `structured`, `text`, `partial`, `unsupported` |
| `mutations.json` | no | single changes to the reference spec, with the rules each has to flag; a test of the evaluator, not of the builder |

The reference spec and the fit table describe one state of the schema. When the schema learns to carry a rule, they may gain that encoding; the rules and the checks do not change.

Check kinds: `fact` (read from the spec and again from the compiled workflow), `structure` (a part of the spec compared by meaning: tables as sets, callback slots computed at a given time), `calls` (tool calls played against the compiled workflow, with the bank's records afterwards), `text` (a prescribed sentence present in the spec), `prompt` (a prescribed sentence present in the prompt of one step of the compiled call), `live` (a description only).

Spoken account labels are compared without regard to capitals and spacing. Identifiers, enum values, reason codes, dates and amounts are compared exactly.

## Three kinds of evidence inside a key

They are reported side by side and never added up.

| Kind | What it shows | Checks |
| --- | --- | --- |
| policy | a value the brief prescribes, or the bank's records after the calls a compliant agent would make | `fact`, `structure`, and `calls` marked `"asserts": "policy"` |
| enforcement | a call no compliant agent makes is stopped or corrected by the generated code. This is a property of the builder, not a sentence of a brief | `calls` marked `"asserts": "enforcement"` |
| weak | an instruction reaches the phone model. Not what the model does with it | `text`, `prompt` |

Example: the brief says every account without a promise gets a note. That the note is at the bank after an agent sends it is policy evidence. That a close sent before the note is refused once is enforcement evidence. A rule is reported as enforced only where an enforcement check passed; a limit that is in the spec and comes out of the compiled workflow, but was not tried with a call that breaks it, is reported as encoded.

A rule can be marked `"offline_evidence": "insufficient"`. Consent before a promise is recorded is such a rule: it stays critical, and the report says that it is not confirmed by behaviour offline, because whether the customer said yes can only be seen in a live call.

A rule without any check is reported as not evaluated offline. It counts in the number of rules and in no accuracy figure.

A reference spec that passes its own key shows that the two agree. It does not show that either is right: both have the same author. The review recorded in each `package.json` is the independent step.

## Running

```text
python benchmarks/harness/packages.py list | freeze | verify
python benchmarks/harness/rules.py                        every package, reference specs
python benchmarks/harness/rules.py --mutations            and the mutated reference specs
python benchmarks/harness/rules.py --package c --spec outputs/<run>/spec.json --out outputs/<run>/rules
```

Nothing here calls a model, Freya or the bank. A pass says what the generated code does with the tool calls a check plays. It says nothing about what a phone model will say or which tools it will choose.
