# Freya Agent Builder

A builder for the Freya × YC "Build the Builder" hackathon. It reads a bank's brief and the
brief's tool API file and creates a Freya phone agent, with no manual step.

```text
brief(s) + OpenAPI ──► Claude ──► agent-spec/1 ──► compiler ──► Freya workflow ──► Freya
                      (one call)   (compact JSON)   (deterministic)  (prompts + embedded runtime)
```

How it works is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Setup

Python 3.11.

```text
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # only the anthropic SDK
```

Put the official starter kit in `local_freya/starter_kit/` (git-ignored). The builder uses its
`freya.py` to deploy and its `tools/run_cases.py` to test. Put keys in `.env.sh` at the repo
root, using the names in [.env.example](.env.example). Keys are read from there or from the
process environment and are never printed or written to an artifact.

## The one command

```text
python -m builder --brief <brief> [--brief <later brief>] --tool-spec <openapi.json> [options]
```

Nothing leaves the machine unless a flag allows it:

| Flag | Effect |
| --- | --- |
| `--live-extract` | Permit the Anthropic API call that produces the spec |
| `--spec FILE` | Reuse an already extracted spec instead (no model call) |
| `--deploy` | Permit Freya: dry-run validation, save **and publish**, export |
| `--test A` | After `--deploy`, run the kit's practice customers for that brief letter |
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

## Outputs

One folder per build, in `outputs/` (git-ignored). A file exists only if its stage ran.

| File | Content |
| --- | --- |
| `outputs/<slot>/spec.json` | The agent-spec/1 the build used |
| `outputs/<slot>/workflow.json` | The compiled, locally validated Freya workflow |
| `outputs/<slot>/trace.txt` | The run's trace: start and end, each model call, what was done on Freya, tests |
| `outputs/<slot>/deployment.json` | Workflow id, agent id, created or updated, platform version, hash of the local workflow |
| `outputs/<slot>/exported.workflow.json` | The workflow as stored on Freya. This is the file to submit |
| `outputs/<slot>/evaluation.json` | Practice-customer results, with `--test` |

The same slot always uses the same folder and the same Freya workflow; the workflow ids are
kept in `.freya-agents.json` (git-ignored).

A failed build exits with 1 and the trace says why: `SPEC ERROR` (the spec does not match the
tool API or cannot be executed), `COMPILE ERROR` (the workflow breaks a platform rule),
`STOPPED` (a missing flag), or `FAILED` (network or platform).

## Tests

```text
python -m unittest discover -s tests
```

The tests make no network call. They need the starter kit in place, because they use its
briefs, tool API file and practice cases.

| File | Area |
| --- | --- |
| `test_openapi.py`, `test_validation.py`, `test_safety.py` | Reading the tool API; spec and workflow validation; credential checks |
| `test_compiler.py` | What a compiled workflow contains |
| `test_runtime.py`, `test_enforcement.py` | The runtime: whole calls, authorization, blocking, failures, state |
| `test_extraction.py` | The extraction and repair loop, with replayed model replies |
| `test_pipeline.py`, `test_config.py`, `test_freya.py` | The command, key loading and tracing, deployment with a fake client |
| `test_architecture.py` | Import boundaries and the embedding of the runtime |

`tests/fixture_spec.py` is a hand-written spec for the kit's Brief A and B. It is test data,
never an input of a real build. `tests/freya_sim.py` stands in for the Freya engine and the
bank; it plays the phone model's tool calls by hand, so a pass there is not evidence that a
real phone agent behaves.

## What may not go into this repository

Everything from Freya stays under git-ignored `local_freya/`: starter kit, briefs, exports.
Keys, generated workflows and traces stay local. After the final briefs open, no rule, value
or name from them may be written into code or prompts; only bug fixes are allowed.
