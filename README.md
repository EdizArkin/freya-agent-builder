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

Put the official starter kit in `local_freya/starter_kit/` (git-ignored). The builder uses its
`freya.py` to deploy, and the customer text and ledger scoring of its `tools/run_cases.py` to test. Put keys in `.env.sh` at the repo
root, using the names in [.env.example](.env.example). Keys are read from there or from the
process environment and are never printed or written to an artifact.

## Build

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
`--simulation-id`, `--iteration-id` and `--live` reads the finished simulation into an ordinary record.

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

`benchmarks/harness/fixture_spec.py` is a hand-written spec for the kit's Brief A and B. It is test data,
never an input of a real build. `benchmarks/harness/freya_sim.py` stands in for the Freya engine and the
bank as they were seen to behave in live calls (no post-hook after an HTTP error, a refusal
with ok false, calls of one message out of order). It plays the phone model's tool calls by
hand, so a pass there is not evidence that a real phone agent behaves.

## What may not go into this repository

Everything from Freya stays under git-ignored `local_freya/`: starter kit, briefs, exports.
Keys, generated workflows and traces stay local. After the final briefs open, no rule, value
or name from them may be written into code or prompts; only bug fixes are allowed.
