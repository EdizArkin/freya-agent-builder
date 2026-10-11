# Freya Agent Builder

A builder for the Freya × YC "Build the Builder" hackathon. It reads a bank's brief and the
brief's tool API file and creates a Freya phone agent, with no manual step.

```text
brief(s) + OpenAPI ──► Claude ──► agent-spec/1 ──► compiler ──► Freya workflow ──► Freya
                      (one call)   (compact JSON)   (deterministic)  (prompts + embedded runtime)
```

A deployed agent can then be improved by the builder itself, within limits:

```text
published version ──► simulate ──► diagnose ──► one change of words ──► new version ──► simulate ──► keep or roll back ──► again
```

How it works is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

Python 3.11.

```text
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # the anthropic SDK; gepa only for one experiment script
```

Get the official starter kit from the event (the hackathon page, Data tab, "Download the kit"), unzip it,
and put it, unchanged, in `freya_starter_kit/` at the repository root (or name its folder with `--kit`).
Copy its `env.example` to `env.sh` there only if you keep keys in the kit; this builder reads `.env.sh`. It is not part of this repository: the kit states no terms for passing it on, so
it is not committed here. The builder uses its `freya.py` to deploy, and the customer text and ledger
scoring of its `tools/run_cases.py` to test. Without the kit no command can reach Freya. Put keys in
`.env.sh` at the repo root, using the names in [.env.example](.env.example): `FREYA_API_KEY`,
`FREYA_API_URL`, `FREYA_BANK_TEAM_KEY`, `ANTHROPIC_API_KEY`, and optionally `BUILDER_LLM_MODEL`.
Keys are read from there or from the process environment and are never printed or written to an
artifact.

## One command: from a brief to a phone agent

```text
python3 builder/build.py {brief_path} {tool_api_path}
```

This is the command of the submission. It needs no flag to do its work: it reads the brief and its
tool API, has a model write the agent's specification, validates and compiles it, puts the phone
agent on Freya, reads back which version is published, and leaves the export of that version and a
plain-text trace. It prints the trace as it goes, so `2>&1 | tee traces/hidden-1.log` keeps it too.

```text
python3 builder/build.py briefs/hidden-1.md briefs/hidden-1.openapi.json --export submission/hidden-1.workflow.json --trace traces/hidden-1.log
```

| Flag | Effect |
| --- | --- |
| `--export FILE` | Where the export of the final published version is copied to |
| `--trace FILE` | The `.log` of the run. A file that exists is never written to again: the new trace gets a number |
| `--amend FILE` | A later document that amends the brief (Brief B on Brief A); repeat, in order |
| `--slot NAME`, `--new` | The agent's name on Freya and its folder. Default: the brief's file name. `--new` stops if that slot already has a workflow |
| `--suite public:A` ... | Test customers for the built agent. With them the run tests the agent, files what failed, has a model propose changes, and keeps or rolls back each one (see Optimize) |
| `--max-candidates N`, `--max-credits N`, `--max-dollars N` | Limits of the run: candidates, Freya credits for self-tests, dollars for the builder's model. Defaults 3, 25, 5 |
| `--deadline-minutes N`, `--reserve-minutes N` | The whole run, and what is kept at its end for the read-back, the export and the trace. Defaults 25 and 4; the event allows 30 minutes per brief |
| `--repeat N` | Test the final version N times in all |
| `--strict` | Fail when the extractor reports policy it could not express. By default the agent is built and the trace names that policy under `NOT ENFORCED` |
| `--plan` | Print what the run would do and call nothing |

A brief without test customers (a hidden brief) is built, deployed, verified and exported; nothing is
tested and the trace says so. The run ends `success` only when the published version was read back
from Freya, tied to a build, and that build's export was checked against its workflow. Otherwise it
ends `incomplete` or `failed` and hands out no export. Ctrl-C, or a file named `STOP` in
`outputs/<slot>/`, ends a run through its rollback.

The two demonstration runs are in [demo/README.md](demo/README.md).

Credits: before each batch of simulated calls the run projects 1.25 credits per call (a call that
runs to the kit's 420-second limit was seen to cost about 1.03) and adds what the earlier batches
really cost, by the balance. A batch that would pass `--max-credits` is not started. That is a check
before a batch, not a cap: a batch that is running can cost more than was projected.

## Build, step by step

```text
python -m builder --brief <brief> [--brief <later brief>] --tool-spec <openapi.json> [options]
```

Nothing leaves the machine unless a flag allows it:

| Flag | Effect |
| --- | --- |
| `--live-extract` | Permit the Anthropic API call that produces the spec |
| `--spec FILE` | Reuse an already extracted spec instead (no model call) |
| `--deploy` | Permit Freya: dry-run validation, save **and publish**, export |
| `--test A` | After `--deploy`, run the kit's practice customers for that brief letter as one simulation, named after the slot, the brief and the version |
| `--strategy single\|audit\|double` | How the spec is extracted. Default `single`; `audit` is the configuration selected in M15 |
| `--prompt-version v1\|v2` | Version of the extraction prompt. Default `v2`, which describes the four rule fields of M16; `v1` is the prompt of M15 |
| `--slot NAME` | Name of the agent and of the build folder. Default: last brief's file name |
| `--model ID` | Extraction model. Default `BUILDER_LLM_MODEL`, else `claude-sonnet-5-5` |
| `--best-effort` | Compile even if the extractor reports policy it could not express |
| `--out DIR`, `--kit DIR`, `--env-file FILE`, `--max-tokens N` | Locations and limits |

Several `--brief` files are read in order; a later file amends an earlier one.

A full run for one brief:

```text
python -m builder --brief briefs/brief-a.md --tool-spec tools/openapi.json --slot brief-a --live-extract --deploy --test A
```

`--deploy` on a slot that was deployed before updates that workflow and publishes the new
version at once. There is no confirmation step.

## Optimize

```text
python -m builder optimize --slot <slot> --tool-spec <openapi.json> --suite public:A --suite scenarios:a [options]
```

The slot must be deployed. The run takes the version that is published on Freya as the incumbent,
simulates it if it has no results, files its failed cases, has a model propose one change of words
for the worst case that words can address, deploys it as a new version of the same workflow,
simulates it, and keeps it or publishes the incumbent again. Then it starts again from whatever is
published. Failures that need a change of code are reported, never patched.

| Flag | Effect |
| --- | --- |
| `--live` | Permit the deploys, the simulations and the rollbacks. Without it the command prints what it would do and calls nothing |
| `--live-llm --max-dollars N` | Permit the paid model call that proposes a change, one per candidate, behind a limit that holds across commands of the run |
| `--proposal FILE` | Use a saved reply of the proposing model instead; repeat, in order |
| `--suite public:A\|public:B\|scenarios:<agent>` | What a result is made of; repeat. `--scenarios FILE` names the scenario file |
| `--safety-case ID` | A case that must pass for a candidate; repeat. Default: the safety cases of the suites that the incumbent passes |
| `--rebuild` | Permit a candidate that is this builder's build of the incumbent's own spec, when the builder has changed since the incumbent was built |
| `--max-candidates N`, `--max-credits N`, `--max-minutes N` | Limits. Defaults 3 candidates and 25 Freya credits for the run, 60 minutes for the command |
| `--rounds N` | Stop after N steps |
| `--best-effort` | The slot was built with `--best-effort` |
| `--run NAME` | The folder of the run under `outputs/` and the first part of every simulation name. Default `M19` |

A run writes `outputs/<run>/optimization.jsonl` (one line per step: incumbent, diagnosis, change,
results, decision, cost), `optimization.trace.txt`, `limits.json` and `spend.json`.

This command is verified offline only: with a fake platform, replayed model replies and prepared
results. It has not yet run against Freya or a model.

If a simulation is cut short (a failed connection, a stopped process), what is known of it stays in
`outputs/<run>/pending/`. `python benchmarks/harness/live.py recover --run <run>` lists it, and with
`--live` reads the finished simulation into an ordinary record: the simulation id is taken from what the
platform answered when it was created, the iteration id is read from the platform, and both can be given
(`--simulation-id`, `--iteration-id`). A simulation is waited for at most as long as all its calls can
take, plus five minutes; after that the run goes on without its result.

## Outputs

One folder per build, in `outputs/` (git-ignored). A file exists only if its stage ran.

| File | Content |
| --- | --- |
| `outputs/<slot>/spec.json` | The agent-spec/1 the build used |
| `outputs/<slot>/workflow.json` | The compiled, locally validated Freya workflow |
| `outputs/<slot>/trace.txt` | The run's trace: start and end, each model call, what was done on Freya, tests |
| `outputs/<slot>/build.json` | The version id of the build (the hash of the workflow), and where the spec came from: a reused file, or the strategy, prompt version and model of the extraction |
| `outputs/<slot>/versions/<version-id>/` | The spec, workflow and build record of one version, and the export of its deploy. Written once and never changed |
| `outputs/<slot>/history.jsonl` | One line per deploy or rollback: platform version, version id, builder commit |
| `outputs/<slot>/deployment.json` | Workflow id, agent id, created or updated, version id, platform version, hash of the local workflow |
| `outputs/<slot>/exported.workflow.json` | The workflow as stored on Freya by the last deploy. This is the file to submit. After a rollback by an optimization run it is the export of the version that is published again |
| `outputs/<slot>/evaluation.json` | Practice-customer results, with `--test` |

The same slot always uses the same folder and the same Freya workflow; the workflow ids are
kept in `.freya-agents.json` (git-ignored).

A failed build exits with 1 and the trace says why: `SPEC ERROR` (the spec does not match the
tool API or cannot be executed), `COMPILE ERROR` (the workflow breaks a platform rule),
`STOPPED` (a missing flag), or `FAILED` (network or platform).

## Tests

```text
python -m unittest discover -s tests -t .
python benchmarks/harness/run_benchmarks.py --no-report       # the 124 offline scenarios
python benchmarks/harness/packages.py verify                  # the frozen evaluation packages
```

Run them from the repository root. The tests make no network call. They need the starter kit in
place, because they use its briefs, tool API file and practice cases; without it the test modules
are reported as skipped. The suite takes several minutes.

| File | Area |
| --- | --- |
| `test_openapi.py`, `test_validation.py`, `test_safety.py` | Reading the tool API; spec and workflow validation; credential checks |
| `test_compiler.py` | What a compiled workflow contains |
| `test_runtime.py`, `test_enforcement.py` | The runtime: whole calls, authorization, blocking, failures, state |
| `test_platform.py` | Behaviour first seen in live calls: no hook after an HTTP error, calls out of order, day names |
| `test_extraction.py` | The extraction and repair loop, with replayed model replies |
| `test_pipeline.py`, `test_config.py`, `test_freya.py` | The command, key loading and tracing, deployment with a fake client |
| `test_architecture.py` | Import boundaries and the embedding of the runtime |
| `test_block_safety.py`, `test_hook_faults.py` | No refused call changes the bank: renamed tool APIs, a permissive bank, a fault at every point of every pre-hook |
| `test_schema_m15.py`, `test_contract.py`, `test_packages.py` | The rule kinds added in M15; requests against the tool API; the evaluation packages and the rule-level evaluator |
| `test_enforcement_m16.py`, `test_concurrency.py` | The four rule kinds of M16; tool calls of one message that run side by side |
| `test_strategies.py`, `test_experiment.py` | The extraction strategies; the experiment runner, its spending meter and the exposure log |
| `test_m17.py` | Prompt versions, simulation names, version ids, what the platform stored, speech findings |
| `test_m18_confirmation.py`, `test_m18_versions.py` | The read-back and confirmation gate; platform versions, history and rollback |
| `test_m18_loop.py`, `test_recovery.py` | The optimization run: decision, limits, diagnosis, candidate, step, run, command, the harness evaluation; pending simulations, recovery, restoring a slot |
| `test_m19_run.py` | The one command from a brief to a verified export; the `.log` trace and its redaction; the bounded wait and the stop file; credits between batches; where the starter kit is |

`benchmarks/harness/fixture_spec.py` is a hand-written spec for the kit's Brief A and B. It is test data,
never an input of a real build. `benchmarks/harness/freya_sim.py` stands in for the Freya engine and the
bank as they were seen to behave in live calls (no post-hook after an HTTP error, a refusal
with ok false, calls of one message out of order). It plays the phone model's tool calls by
hand, so a pass there is not evidence that a real phone agent behaves.

## What may not go into this repository

Keys never: `.env.sh`, `freya_starter_kit/env.sh`, the slot registry and everything under `outputs/`
are git-ignored, and so are the persona ids and result logs a run leaves in the kit. Demo traces,
exports and reports under `demo/` are ignored too and are committed on purpose, never automatically.
After the final briefs open, no rule, value or name from them may be written into code or prompts;
only bug fixes are allowed. The phone agents of the hidden briefs come out of the submitted commit
with no manual edit.
