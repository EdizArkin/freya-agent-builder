"""The builder's optimization command: improve a deployed agent by itself, within limits.

    python -m builder optimize --slot brief-a --tool-spec openapi.json --suite public:A --suite scenarios:a \\
        [--safety-case ID ...] [--rebuild] [--best-effort] [--rounds N] \\
        [--max-candidates 3] [--max-credits 25] [--max-minutes 60] \\
        [--proposal FILE ... | --live-llm --max-dollars 1] [--live]

A run takes the version of the slot that is published on Freya as the incumbent, simulates it if it has no
results yet, files its failed cases, has a model propose one change of words for the worst one that words can
address, deploys that as a new version of the same workflow, simulates it, and keeps it or publishes the
incumbent again. Then it does the same from whatever is published now, until a limit is reached or nothing is
left that a candidate can address. What needs a change of code is reported, never patched.

Without --live nothing is called and nothing is written. --live permits the deploys,
the simulations and the rollbacks. The change comes from the builder's model (--live-llm, behind --max-dollars,
which holds across commands of the run) or from saved replies (--proposal, in order), never from a spec edited
by hand. --rebuild permits one more kind of candidate: this builder's build of the incumbent's own spec, when
the builder has changed since the incumbent was built.

The evaluation is loaded by name (--evaluation; default: the benchmark harness of this repository), and the
suite options are its own. Everything else is the builder's: builder/optimization/loop.py.

What is written, under the git-ignored outputs/:
    outputs/<run>/optimization.jsonl       one line per step: incumbent, diagnosis, change, results, decision, cost
    outputs/<run>/optimization.trace.txt   the trace of every step, credentials redacted
    outputs/<run>/limits.json              what the run has used of its limits
    outputs/<run>/spend.json               every paid model call of the run
"""
import argparse
import importlib
import json
from pathlib import Path

from builder.artifacts import store
from builder.config import settings
from builder.extraction import claude, spend
from builder.freya import client as freya_client
from builder.optimization import loop, policy
from builder.tracing.trace import Trace

# How a run may end without anything being wrong: it did what it could, or a limit stopped it.
ENDED_WELL = loop.CONTINUES + ('stopped: no case fails', 'stopped: the limit', 'stopped before deployment: the limit', 'stopped before the incumbent was evaluated: the limit',
                               'stopped: no failed case can be addressed')


def arguments(argv=None, provider=None):
    """The parsed command line and the evaluation module. The module is named on the same command line, so the
    line is read twice: once for its name, then with the options the module adds."""
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument('--evaluation', default=settings.EVALUATION)
    named = first.parse_known_args(argv)[0].evaluation
    provider = provider or importlib.import_module(named)
    p = argparse.ArgumentParser(prog='python -m builder optimize', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--slot', required=True, help='the deployed agent: its folder under --out and its entry in the slot registry')
    p.add_argument('--tool-spec', required=True, help="the slot's OpenAPI tool file")
    p.add_argument('--evaluation', default=settings.EVALUATION, help='the module that simulates and keeps the results (default: %(default)s)')
    p.add_argument('--safety-case', action='append', default=[], help='a case that must pass for a candidate; repeat. Default: the safety cases of the evaluation that the incumbent passes')
    p.add_argument('--rebuild', action='store_true', help="permit a candidate that is this builder's build of the incumbent's own spec, when the builder has changed")
    p.add_argument('--best-effort', action='store_true', help='the slot was built with --best-effort')
    p.add_argument('--rounds', type=int, help='stop after this many steps; default: until a limit or nothing is left to address')
    p.add_argument('--max-candidates', type=int, help='limit of the run; default %d' % policy.LIMITS['candidates'])
    p.add_argument('--max-credits', type=float, help='limit of the run in Freya credits; default %s' % policy.LIMITS['credits'])
    p.add_argument('--max-minutes', type=float, help='limit of this command; default %d' % policy.LIMITS['minutes'])
    p.add_argument('--proposal', action='append', default=[], help='a saved reply of the proposing model, used instead of a model call; repeat, in order')
    p.add_argument('--live-llm', action='store_true', help='permit the paid model call that proposes a change, one per candidate')
    p.add_argument('--max-dollars', type=float, help='the limit of the run for paid model calls; needed with --live-llm')
    p.add_argument('--model', help='the proposing model; default BUILDER_LLM_MODEL or ' + settings.DEFAULT_MODEL)
    p.add_argument('--no-platform-version-check', action='store_true',
                   help='count results by the version id alone, also when the calls report another platform version than the one published for them')
    p.add_argument('--run', default='M19', help='the first part of every simulation name, and the folder of the run under --out')
    p.add_argument('--out', default=str(settings.OUTPUTS))
    p.add_argument('--kit', default=str(settings.KIT), help='starter kit folder holding freya.py and tools/run_cases.py')
    p.add_argument('--env-file', action='append', default=[], help='extra env file with API keys')
    p.add_argument('--live', action='store_true', help='permit the deploys, the simulations and the rollbacks')
    if hasattr(provider, 'add_arguments'):
        provider.add_arguments(p)
    return p.parse_args(argv), provider


def report(records, say=print):
    """What a run did, step by step, and what is left for a person."""
    for number, record in enumerate(records, 1):
        change = record.get('modification') or {}
        say('step %d | %s%s | %s' % (number, 'case %s (%s) | ' % (record['diagnosed'], record.get('filed_as')) if record.get('diagnosed') else '',
                                    'builder rebuild' if change.get('kind') == 'builder' else change.get('field') or 'no change', record['status']))
    found = (records[-1].get('diagnosis') if records else None) or {}
    for title, key in (('needs a change of code, not of words', 'needs_code_change'), ('cannot be diagnosed', 'not_diagnosable'), ('not tried again', 'given_up')):
        for entry in found.get(key) or []:
            say('  %s: %s | %s' % (title, entry['id'], entry['why']))


def main(argv=None, evaluation=None):
    """`evaluation` replaces the module named by --evaluation: the harness passes its own, a test passes a fake."""
    settings.safe_console()
    args, provider = arguments(argv, evaluation)
    folder = Path(args.out) / store.build_id(args.run.lower())
    try:
        evaluate = provider.Evaluation(args)
        limits = policy.Limits(folder / 'limits.json', {'candidates': args.max_candidates, 'credits': args.max_credits, 'minutes': args.max_minutes})
    except (ValueError, OSError) as exc:
        print('STOP: %s' % exc)
        return 2
    print('optimize %s | run %s | used so far: %d of %d candidate(s), %.2f of %s credits | this command may take %d min' % (
        args.slot, args.run, limits.state['candidates'], limits.limits['candidates'], limits.state['credits'], limits.limits['credits'], limits.limits['minutes']))
    if not args.live:
        print('dry run: would take the published version of %s as the incumbent, simulate %d case(s) for it if it has no results, file its failed cases, have one change '
              'proposed, deploy it as a new version of the same workflow, simulate it, and keep it or publish the incumbent again; then again, within the limits. '
              'Nothing was called.' % (args.slot, len(evaluate.cases())))
        return 0
    # Keys and the model's name come from the environment files: read before anything that uses them is made.
    settings.load_env(settings.env_files(args.env_file, args.kit))
    if args.proposal:
        client = claude.ReplayClient([Path(p).read_text(encoding='utf-8') for p in args.proposal])
    elif args.live_llm:
        if args.max_dollars is None:
            print('STOP: --live-llm needs --max-dollars: the limit of the whole run for paid model calls.')
            return 2
        if not claude.sdk_installed():
            print('STOP: the Anthropic SDK is not installed for this Python. Run the command with the Python that has it.')
            return 2
        client = spend.Budget(claude.AnthropicClient(args.model, fallback=False), args.max_dollars, folder / 'spend.json')
    elif args.rebuild:
        client = None               # the rebuild needs no model; a step after it stops for want of one
    else:
        print('STOP: no source for a candidate. Pass --proposal FILE, or --live-llm with --max-dollars to permit the paid model call, or --rebuild.')
        return 2
    freya = freya_client.load(args.kit, patiently=True)
    document = json.loads(Path(args.tool_spec).read_text(encoding='utf-8'))
    audit = folder / 'optimization.jsonl'
    try:
        records = loop.run(args.slot, document, evaluate, freya, store.BuildStore(args.out, args.slot), limits, audit, client=client, safety_cases=args.safety_case,
                           best_effort=args.best_effort, rebuild=args.rebuild, credits=lambda: freya_client.credits(freya).get('creditsLeft'),
                           check_platform_version=not args.no_platform_version_check, rounds=args.rounds, trace=Trace(folder / 'optimization.trace.txt', append=True))
    except KeyboardInterrupt:
        print('interrupted. The step that was running was settled before it stopped: its line is the last one of %s' % audit)
        return 130
    report(records)
    print('%d step(s) | used: %d of %d candidate(s), %.2f of %s credits | audit: %s' % (
        len(records), limits.state['candidates'], limits.limits['candidates'], limits.state['credits'], limits.limits['credits'], audit))
    return 0 if records and all(r['status'].startswith(ENDED_WELL) and 'NOT PUT BACK' not in r['status'] for r in records) else 1
