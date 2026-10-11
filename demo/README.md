# The two demonstration runs

Each run starts from the official brief files and goes through the whole builder: extraction by the model,
validation, compiler, a NEW workflow on Freya, the practice customers, diagnosis, candidates proposed by the
model, and for each candidate keep or roll back. Nothing is copied from an earlier agent, and nothing is edited
by hand afterwards. The two tracks share nothing: each has its own slot, workflow, agent, version history,
records, trace and export.

Both are the builder's one command, `builder/build.py`, with test customers named. Run them from the repository
root, in a terminal of your own (Ctrl-C then ends a run through its rollback), with the keys in `.env.sh`.

## Demo A: Brief A, ten practice customers

```text
python builder/build.py freya_starter_kit/briefs/brief-a.md freya_starter_kit/tools/openapi.json \
    --slot demo-brief-a --new --suite public:A \
    --max-candidates 3 --max-credits 40 --max-dollars 5 --deadline-minutes 90 --reserve-minutes 5 \
    --trace demo/brief-a/traces/demo-brief-a.log --export demo/brief-a/workflows/demo-brief-a.workflow.json --results demo/brief-a/results
```

Target: 10 of 10. That is a target; the report says what was observed.

## Demo A+B: Brief A amended by Brief B, three more practice customers

```text
python builder/build.py freya_starter_kit/briefs/brief-a.md freya_starter_kit/tools/openapi.json --amend freya_starter_kit/briefs/brief-b.md \
    --slot demo-brief-a-plus-b --new --suite public:B --suite public:A \
    --max-candidates 3 --max-credits 55 --max-dollars 5 --deadline-minutes 120 --reserve-minutes 5 \
    --trace demo/brief-a-plus-b/traces/demo-brief-a-plus-b.log --export demo/brief-a-plus-b/workflows/demo-brief-a-plus-b.workflow.json --results demo/brief-a-plus-b/results
```

Target: 3 of 3 on Brief B's customers with no regression on Brief A's ten. Both suites are named, so a candidate
that breaks a Brief A customer is rolled back. The two documents are given to the model in order, the second as
an amendment of the first; they are not pasted together.

## What the options mean here

| Option | Why |
| --- | --- |
| `--new` | The run must create its own workflow. It stops if the slot already has one; use another `--slot` for another run |
| `--suite` | The practice customers of the kit for that brief. Without a suite nothing is tested |
| `--max-credits` | The projection is 1.25 credits per simulated call: 12.5 for Brief A's ten, 16.25 for thirteen. The first test and one candidate must fit; what a batch really cost is counted by the balance |
| `--max-candidates`, `--max-dollars` | How many candidates may be deployed, and what the builder's model may cost |
| `--deadline-minutes` | A demo may take longer than the 30 minutes a hidden brief gets. One test of ten customers can take up to 19 minutes |
| `--repeat 3` | Add it to test the final version three times; the report then says which customers pass in all three |
| `--trace`, `--export`, `--results` | Where the `.log`, the export of the final published version, and the report (`.json`, `.md`) go |

## What a run leaves

- `traces/<name>.log`: the trace, written line by line. A second run never writes into the first one's file.
- `workflows/<name>.workflow.json`: the export of the version that the platform reported as published at the end,
  checked against that build. Only a run that ends `success` writes it.
- `results/<run id>.md` and `.json`: the first version's results, each candidate with its change and the decision,
  the final version, and the repetitions if any.
- `outputs/<slot>/`: the builder's own records of the track (specs and workflows per version, history, simulations).

These files are ignored by git until they are added on purpose (`git add -f`).

## Status

No demo has been run. The commands above are verified offline, with a fake platform, replayed model replies and
prepared results (`tests/test_m19_run.py`).
