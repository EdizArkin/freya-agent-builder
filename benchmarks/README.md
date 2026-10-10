# Benchmarks

An independent set of adversarial scenarios for the Freya agent builder, with a harness that runs them offline. It adds to the official practice cases of the starter kit and changes none of them.

Nothing here calls Anthropic, Freya or the bank. Nothing here changes the builder.

## What a result means

Read this before reading any number.

- **A PASS says what the generated code does.** The harness plays tool calls against the workflow the builder compiles and checks how each call ends, what the model would be told, and what the bank holds afterwards. That is evidence about the runtime and the graph.
- **A PASS says nothing about what a phone model will say or which tools it will choose.** The harness plays the model's part by hand. Whether a real agent reads a date back, counts its persuasion attempts or refuses a discount can only be seen in a live call.
- **A GAP is a failure that is written down.** The scenario states a rule of its brief, the builder does not make it hold, and the definition says why. A GAP stays a GAP until the builder changes.
- **NOT RUN is not a pass.** Those scenarios are specifications. They cannot be executed faithfully offline and no result is invented for them.

## Layout

```
benchmarks/
  README.md
  requirements/coverage_matrix.md     every rule of every brief: who enforces it, what covers it, what is missing
  edge_cases/*.yaml                   86 scenarios for the official Brief A and Brief B, in nine categories
  synthetic_briefs/
    brief-c.md, brief-d.md, brief-e.md       three briefs written for this benchmark
    brief-*.api.yaml                         what each assumes of the tool API, as changes to the shared file
    brief-*.spec.yaml                        a hand-written reference spec for each
    brief-*.scenarios.yaml                   37 scenarios
  expected/ledger_assertions.yaml     what the bank must hold after each scenario
  harness/
    scenarios.py                      loads and checks the definitions, builds the simulated call
    assertions.py                     what can be asserted and how
    run_benchmarks.py                 the command
  reports/latest.md, latest.json      written by the last run
```

The `.yaml` files are YAML written in its JSON subset. The harness reads them with the standard library, so no package has to be installed. With PyYAML present, any YAML syntax is accepted.

## Running

From the repository root, with the starter kit in `local_freya/starter_kit/` as for the tests:

```
python benchmarks/harness/run_benchmarks.py                       all scenarios
python benchmarks/harness/run_benchmarks.py --category dates      one category
python benchmarks/harness/run_benchmarks.py --brief D             one brief
python benchmarks/harness/run_benchmarks.py --only EC-RT-04       chosen scenarios
python benchmarks/harness/run_benchmarks.py --list                list, run nothing
```

The run takes a few seconds. Exit code 0 means no unexpected result: every executable scenario passed or failed exactly where a known gap is documented. Exit code 1 means a scenario failed without a documented gap, or passed although a gap is documented. Exit code 2 means the definitions contradict each other.

By default Brief A and B scenarios run against the hand-written spec of `tests/fixture_spec.py`. To run them against a spec the builder extracted:

```
python benchmarks/harness/run_benchmarks.py --spec A=outputs/<build>/spec.json --spec B=outputs/<build>/spec.json
```

The assertions use only text the runtime itself produces, so they hold for any spec of the same brief.

## The three kinds of scenario

| Tier | What the harness does | What it proves |
| --- | --- | --- |
| `runtime` | Plays tool calls, including ones no compliant agent would make | The generated code refuses, repairs or passes each call as the brief requires, whatever the model does |
| `scripted` | Plays the calls a compliant agent would make for the described customer | The runtime lets the correct path through and the bank ends in the expected state. Not that a model would take that path |
| `live_only` | Nothing | Nothing yet. The scenario is a specification for a live run |

Each scenario also says where it can be run: `offline`, `live`, `both`, or `specification` when neither is possible today.

## A scenario

```json
{
  "id": "EC-DT-02",
  "title": "A date after the deadline is refused and the deadline right after it is recorded",
  "brief": "A", "tier": "runtime", "mode": "offline",
  "requirements": ["A-18", "A-23", "P-05"],
  "customer": {"identity": {...}, "products": [...]},
  "behavior": "The customer wants a day after the latest date and then accepts the latest date.",
  "steps": [
    {"call": "verify_identity", "args": {...}, "expect": "ok"},
    {"call": "create_promise", "args": {...}, "expect": "refused", "says": ["is after the latest date"]},
    {"call": "create_promise", "args": {...}, "expect": "ok", "says": ["is now recorded"]},
    {"call": "close_call", "args": {"outcome": "promise"}, "expect": "ok"}
  ],
  "spoken": ["The agent says the latest date it can record and offers it."],
  "strategy": "Offline, deterministic: ..."
}
```

- `requirements` are rule ids of `requirements/coverage_matrix.md`.
- `customer` is synthetic data written in the file, or `{"public_case": "PUB-06"}`, which takes the account of a practice case from the local starter kit at run time. Nothing of the kit's test set is copied into this directory; `"$p1.past_due"` and `"$identity.phone_last4"` stand for its values.
- `steps` are what the harness plays. A step is a `call`, several calls `together` in one model message (with the order in which they reach the bank), a change of `faults`, or a `check` of what the model has in front of it.
- `expect` is how a call must end: `ok`, `refused` (blocked, with the reason as the tool result), `detached` (blocked, the bank never saw the customer), `blocked` (either), `error` (HTTP failure), `bank_refused`, `unavailable` (the tool does not exist at this point).
- `says` is text the model must be given: in the tool result when there is one, in the live state otherwise.
- `spoken` is what a correct agent says. It is never checked offline. In a live run it becomes the judge's criteria.
- `known_gap` and `gap_kind` mark a scenario that the builder is known to fail, and why: `defect` (the builder does something wrong), `unenforced` (left to the model although code could enforce it), `schema` (agent-spec/1 cannot express the rule).

The bank's final records, the calls that may or may not reach it, and whether the call must have ended are in `expected/ledger_assertions.yaml`, keyed by scenario id.

## What the last run showed

123 scenarios: 112 executable offline, 11 specification only.

| Result | Count |
| --- | --- |
| PASS | 99 |
| GAP, known and confirmed | 13 |
| FAIL | 0 |
| NOT RUN | 11 |

The same numbers hold when the Brief A and B scenarios run against the two specs Claude extracted on 2026-10-10.

**Nine gaps are rules agent-spec/1 cannot express**, all found by the synthetic briefs: text answers that need a fixed form, a promise that is not allowed for a product, an earliest promise date, a rule that depends on the time of day, a promise that depends on another product, a minimum across products, a closing rule on the reason of a note, a limit on the number of transfers, and an exception to "nothing after a transfer" for one queue.

**Four gaps cannot be closed from inside a tool hook** (`EC-RT-04`, `EC-TR-03`, `EC-TR-09`, `EC-RT-10`). A live call on 2026-10-10 showed that the tool calls of one model message run side by side, each on its own copy of the variables. No hook can see another call of the same message, so a close cannot wait for a promise, transfer or callback sent with it, and the same promise sent twice is carried out twice. Earlier versions of this suite counted three of these as passing, because the offline engine let the hooks of one message share their variables. The engine now copies what was seen live, and the scenarios keep their expectations and fail.

History: the first run of this suite, against the builder of the first commit of 2026-10-10, had 95 passes and 17 gaps. Eight of those were defects or missing checks in the runtime and were fixed. One scenario was changed afterwards: `EC-RT-06` asserted that a close right after a failed call is refused once, which was a side effect of a guard that has been removed; it now asserts that the close goes through.

Two of the fixes are deliberately bounded. A close that breaks a rule (open products, a missing or unlisted reason, an unknown outcome) is refused once per kind of problem and let through the next time, because a refused close cannot explain itself to the model and a call that never closes is the worse failure.

`reports/latest.md` has the full list and every failed assertion.

## The synthetic briefs

Briefs C, D and E were written for this benchmark. They are not hackathon material. Each is a complete policy in the form of the official briefs, with a section on what it assumes of the tool API and a section on which of its rules the schema can carry.

| Brief | What it is about | Scenarios | Pass | Gap | Not run |
| --- | --- | --- | --- | --- | --- |
| C | Another security check, conditions on taking a promise at all, no callbacks, no retry | 12 | 8 | 3 | 1 |
| D | Up to four products that depend on each other, notes after a transfer, a dispute that outranks a promise | 12 | 8 | 3 | 1 |
| E | Rules that replace other rules, a Sunday-to-Thursday week, two deadline rules, escalation priorities | 13 | 9 | 3 | 1 |

Their reference specs are written by hand. They show what the schema can hold for each brief; they are not builder output, and no extraction has been run on these briefs. A rule the schema cannot carry is not listed under `unsupported` in a reference spec, because validation refuses such a spec and nothing could then run. It is flagged in the brief, in the matrix and by a scenario that fails.

## Running scenarios on Freya

Not done by this harness, and not done so far.

The bank creates a practice session from a case id only, so a live scenario has to use the account of one of the kit's thirteen practice cases. Sixteen scenarios do. For them

```
python benchmarks/harness/run_benchmarks.py --export-live outputs/benchmarks/live
```

writes case files in the format of the kit's `tools/run_cases.py`, each case with the scenario's customer script, its expected bank records and its `spoken` lines as the judge's checks. Two scenarios on the same account cannot share a file, so the cases are spread over five files. Each can then be run with

```
python tools/run_cases.py <agent_id> --cases <file>
```

from the kit directory. That is a paid Freya simulation and needs an agent deployed from the matching brief. The files contain the kit's account data, so write them to a git-ignored place such as `outputs/`.

The other scenarios cannot be run live today: their accounts, faults or tool API do not exist at the bank. That includes every scenario of the synthetic briefs.

## Limits

- The offline engine and bank are stand-ins that copy what live calls showed on 2026-10-10. Two things they assume have not been seen live: how the bank answers an unknown product id, and whether a call of one message writes back variables it did not change.
- A scenario that plays several calls in one message fixes the order in which they reach the bank and how their variables are merged. The real engine promises neither; `tests/test_concurrency.py` runs the same situations under every merge the live observations allow.
- Thirty rules have no scenario at all. They are about wording and about recognising what a person means; the matrix lists them.
- The scenarios for Brief A and B state what the briefs require as this suite reads them. Where a brief is silent the suite asserts nothing.

## Changing the suite

Add a scenario to the file of its category and its expected records to `expected/ledger_assertions.yaml`. Every `requirements` id must exist in the matrix. The harness checks the definitions before it runs anything and stops with exit code 2 if they do not fit together.
