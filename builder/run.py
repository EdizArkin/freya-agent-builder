"""One command from a brief to a verified phone agent on Freya, with its export and its trace.

    python builder/build.py BRIEF TOOL_API [--amend BRIEF_B] [--suite public:A ...] [--export FILE] [--trace FILE]

This is the whole builder in one run, and it calls what it needs by itself: there is no flag to permit the model
or the platform (--plan prints what would be done and calls nothing).

    brief + tool API -> extraction (model) -> agent-spec/1 -> validation -> compiler -> workflow on Freya
        -> [self-tests -> diagnosis -> candidate (model) -> new version -> self-tests -> keep or roll back]*
        -> the published version is read back -> its export -> the trace

The part in brackets runs when suites of test customers are named (--suite): the optimization run of
builder/optimization/, within the limits given. A brief without test customers is built, deployed, verified and
exported; nothing is tested, and the trace says so.

What ends a run, and what it leaves:
- Every stage writes its line to the trace before the next one starts (tracing/runlog.py).
- The run has a deadline (--deadline-minutes, default 25 of the event's 30). The last --reserve-minutes of it
  are kept for the final read-back, the export and the trace: no simulation is started that could run into
  them, and no simulation is waited for beyond them.
- The export that is handed out is the one kept with the build that the platform reports as published, checked
  against that build's workflow. After a rollback the platform's own export is the definition saved last, not
  the published one, so it is never taken at face value. If the published version cannot be tied to a build,
  or its export is not there or differs, the run ends `incomplete` and hands out no export.
- Ctrl-C, or a file named STOP in the folder of the run, ends a run through the same path as any failure: a
  candidate that is published is rolled back first.

Exit code 0: an agent is published, verified and exported. 1: failed or incomplete. 130: interrupted.
"""
import argparse
import importlib
import json
import sys
import time
from pathlib import Path

from builder import pipeline
from builder.artifacts import store
from builder.config import settings
from builder.extraction import claude, spend
from builder.freya import client as freya_client
from builder.freya import deployment, versioning
from builder.optimization import loop, policy
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.tracing.runlog import RunLog
from builder.validation.spec import digest, validate

SUCCESS, INCOMPLETE, FAILED, INTERRUPTED = 'success', 'incomplete', 'failed', 'interrupted'


class Incomplete(Exception):
    """The run cannot hand out a verified agent, and says why."""


class Watched:
    """The model client of a run: a call that fails is written to the trace as well (a call that succeeds is
    written by the code that made it, with its purpose)."""

    def __init__(self, client, log):
        self.client, self.log, self.model = client, log, getattr(client, 'model', None)
        self.max_tokens = getattr(client, 'max_tokens', None)

    def complete(self, system, messages):
        try:
            return self.client.complete(system, messages)
        except Exception as exc:
            self.log('LLM call failed | %s | %s: %s' % (self.model, type(exc).__name__, str(exc)[:300]))
            raise


def arguments(argv=None, provider=None):
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument('--evaluation', default=settings.EVALUATION)
    named = first.parse_known_args(argv)[0].evaluation
    provider = provider or importlib.import_module(named)
    p = argparse.ArgumentParser(prog='python builder/build.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('brief', help='the brief: one Markdown file')
    p.add_argument('tool_api', help="the brief's tool API file (OpenAPI)")
    p.add_argument('--amend', action='append', default=[], help='a later document that amends the brief (Brief B on Brief A); repeat, in order')
    p.add_argument('--slot', help='the name of the agent: its workflow on Freya and its folder under --out. Default: the file name of the brief')
    p.add_argument('--new', action='store_true', help='stop if the slot already has a workflow: this run must create its own')
    p.add_argument('--run-id', help='the id of this run in the trace and in every file it writes. Default: time and slot')
    p.add_argument('--trace', help='the .log file of this run. Default: <out>/<slot>/traces/<run id>.log')
    p.add_argument('--export', help='where the export of the final published version is copied to, e.g. submission/hidden-1.workflow.json')
    p.add_argument('--results', help='a folder for the report of this run: <run id>.json and <run id>.md')
    p.add_argument('--strategy', choices=claude.STRATEGIES, default='audit', help='how the spec is extracted (default: audit, the configuration selected in M15)')
    p.add_argument('--prompt-version', choices=sorted(claude.PROMPT_VERSIONS), default=claude.PROMPT_VERSION)
    p.add_argument('--model', help='the builder\'s model; default BUILDER_LLM_MODEL or ' + settings.DEFAULT_MODEL)
    p.add_argument('--max-tokens', type=int, default=settings.MAX_TOKENS)
    p.add_argument('--strict', action='store_true', help='fail the build when the extractor reports policy it could not express, instead of building with a warning')
    p.add_argument('--spec', help='reuse an extracted spec instead of calling the model for it')
    p.add_argument('--no-deploy', action='store_true', help='extract and compile only; nothing is sent to Freya')
    p.add_argument('--max-dollars', type=float, default=5.0, help='the limit of this run for paid model calls (default 5)')
    p.add_argument('--deadline-minutes', type=float, default=25.0, help='the whole run, self-tests included (default 25; the event allows 30)')
    p.add_argument('--reserve-minutes', type=float, default=4.0, help='kept at the end for the read-back, the export and the trace (default 4)')
    p.add_argument('--evaluation', default=settings.EVALUATION, help='the module that simulates test customers and keeps the results (default: %(default)s)')
    p.add_argument('--safety-case', action='append', default=[], help='a case that must pass for a candidate; repeat. Default: the safety cases the incumbent passes')
    p.add_argument('--max-candidates', type=int, default=policy.LIMITS['candidates'], help='candidates the optimization may deploy (default %(default)s)')
    p.add_argument('--max-credits', type=float, default=policy.LIMITS['credits'], help='Freya credits the self-tests may use, by projection and by the balance (default %(default)s)')
    p.add_argument('--rounds', type=int, help='stop the optimization after this many steps')
    p.add_argument('--no-optimize', action='store_true', help='with --suite: test the built agent once and propose nothing')
    p.add_argument('--repeat', type=int, default=1, help='test the final version this many times in all (default 1)')
    p.add_argument('--no-platform-version-check', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--plan', action='store_true', help='print what the run would do and call nothing')
    p.add_argument('--out', default=str(settings.OUTPUTS))
    p.add_argument('--kit', default=str(settings.KIT), help='starter kit folder holding freya.py and tools/run_cases.py')
    p.add_argument('--env-file', action='append', default=[], help='extra env file with API keys')
    if hasattr(provider, 'add_arguments'):
        provider.add_arguments(p)
    args = p.parse_args(argv)
    args.slot = store.build_id(args.slot or '-plus-'.join(Path(b).stem for b in [args.brief] + args.amend)).lower()
    args.run = args.slot                    # the evaluation keeps its records in the folder of the slot: one track, one folder
    args.run_id = store.build_id(args.run_id or '%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), args.slot))
    return args, provider


def counts(workflow):
    """(steps, tools the phone model can call) of a workflow."""
    nodes = workflow.get('nodes') or {}
    return len(nodes), sum(len(node.get('tools') or []) for node in nodes.values())


def score(cases):
    """'7 of 10' and the failed cases, from a vector as policy.shown writes it."""
    failed = sorted(cid for cid, state in (cases or {}).items() if state != 'pass')
    return '%d of %d' % (len(cases or {}) - len(failed), len(cases or {})), failed


def report(result):
    """The report of a run as text: the first version, every candidate, the final version, each with what was observed."""
    out = ['# Builder run %s' % result['run_id'], '', 'Status: **%s**%s' % (result['status'], ' (%s)' % result['reason'] if result.get('reason') else ''), '',
           '| | |', '| --- | --- |', '| Brief | %s |' % ', '.join(result['briefs']), '| Tool API | %s |' % result['tool_api'], '| Builder commit | %s |' % result['builder_commit'],
           '| Workflow | %s |' % result.get('workflow_id'), '| Agent | %s |' % result.get('agent_id'), '| Final build | %s |' % result.get('final_build'),
           '| Published platform version | %s |' % (result.get('published') or {}).get('number'), '| Export | %s |' % result.get('export'), '| Trace | %s |' % result['trace'],
           '| Elapsed | %s s |' % result.get('seconds'), '| Model calls | %s, $%s |' % (result.get('model_calls'), result.get('model_dollars')), '| Freya credits | %s |' % result.get('credits'), '']
    if not result.get('versions'):
        out += ['No self-test was run: %s.' % (result.get('untested') or 'none was asked for'), '']
    for version in result.get('versions') or []:
        got, failed = score(version['cases'])
        out += ['## %s: %s' % (version['role'], version['version_id']), '', '%s case(s) pass, one run each.%s' % (got, ' Failed: ' + ', '.join('%s (%s)' % (c, version['cases'][c]) for c in failed) if failed else ''),
                ''] + (['Change: %s' % version['change'], ''] if version.get('change') else []) + (['Decision: %s' % version['decision'], ''] if version.get('decision') else [])
    for number, cases in enumerate(result.get('repetitions') or [], 1):
        out += ['Repetition %d of the final version: %s case(s) pass.' % (number, score(cases)[0])]
    if result.get('repetitions'):
        always = sorted(c for c in result['repetitions'][0] if all(rep.get(c) == 'pass' for rep in result['repetitions']))
        out += ['', 'Pass in all %d repetitions: %d of %d.' % (len(result['repetitions']), len(always), len(result['repetitions'][0])), '']
    for entry in result.get('needs_code_change') or []:
        out += ['Needs a change of code, not of words: %s (%s)' % (entry['id'], entry['why'])]
    out += ['', 'Every figure above is what this run observed. A target that was not reached is not reported as reached.', '']
    return '\n'.join(out)


def main(argv=None, client=None, platform=None, evaluation=None, registry=versioning.REGISTRY):
    """`client`, `platform` and `evaluation` replace the model client, the function that loads the Freya client and
    the evaluation module: a test passes fakes, and nothing else differs from a real run."""
    settings.safe_console()
    args, provider = arguments(argv, evaluation)
    suites = list(getattr(args, 'suite', None) or [])
    if args.plan:
        print('plan for %s (run %s): %s the spec from %s with the tool API %s; validate and compile it; %s%s. Nothing was called.' % (
            args.slot, args.run_id, 'reuse' if args.spec else 'extract (model, strategy %s)' % args.strategy, ', '.join([args.brief] + args.amend), args.tool_api,
            'stop there' if args.no_deploy else 'deploy it to the workflow of slot "%s" on Freya, read the published version back and export it' % args.slot,
            '' if args.no_deploy or not suites else '; before that, test it with %s and %s within %d candidate(s), %s credits and %s minutes' % (
                ', '.join(suites), 'propose nothing' if args.no_optimize else 'improve it', args.max_candidates, args.max_credits, args.deadline_minutes)))
        return 0
    files = store.BuildStore(args.out, args.slot)
    log = RunLog(args.trace or files.path('traces') / (args.run_id + '.log'), args.run_id)
    started = time.time()
    deadline = started + 60.0 * args.deadline_minutes
    usable = deadline - 60.0 * args.reserve_minutes
    result = {'run_id': args.run_id, 'slot': args.slot, 'briefs': [args.brief] + args.amend, 'tool_api': args.tool_api, 'builder_commit': versioning.builder_commit(),
              'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'trace': str(log.path), 'status': FAILED, 'command': 'python builder/build.py ' + ' '.join(argv if argv is not None else sys.argv[1:])}
    log('run start | run %s | slot %s | builder commit %s' % (args.run_id, args.slot, result['builder_commit'] or 'unknown'))
    log('inputs | brief %s | tool API %s' % (', '.join(result['briefs']), args.tool_api))
    log('configuration | extraction %s, prompt %s | deadline %s min, %s min kept for the end | model calls up to $%s | self-tests: %s | up to %d candidate(s), %s credits | repeat %d' % (
        'from the file ' + args.spec if args.spec else args.strategy, args.prompt_version, args.deadline_minutes, args.reserve_minutes, args.max_dollars,
        ', '.join(suites) or 'none', args.max_candidates, args.max_credits, args.repeat))
    code, model = 1, None
    try:
        # 1. configuration and inputs
        settings.load_env(settings.env_files(args.env_file, args.kit))
        briefs = [(Path(b).name, Path(b).read_text(encoding='utf-8')) for b in [args.brief] + args.amend]
        document = json.loads(Path(args.tool_api).read_text(encoding='utf-8'))
        api = Api(document)
        best_effort = not args.strict
        known = versioning.known(args.slot, registry)
        if args.new and known:
            raise Incomplete('the slot %s already has the workflow %s; --new asks for a run that creates its own' % (args.slot, known.get('workflow_id')))
        if client is None and not (args.spec and (args.no_optimize or not suites)):
            if not claude.sdk_installed():
                raise Incomplete('the Anthropic SDK is not installed for this Python')
            client = spend.Budget(claude.AnthropicClient(args.model, args.max_tokens, fallback=False), args.max_dollars, files.path('spend.%s.json' % args.run_id))
        model = Watched(client, log) if client is not None else None

        # 2. the spec: extracted by the model and repaired until it validates
        if args.spec:
            spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))
            source = {'kind': 'reused spec', 'file': str(args.spec), 'run_id': args.run_id}
            log('spec: reused from %s (no model call)' % args.spec)
        else:
            source = {'kind': 'extraction', 'strategy': args.strategy, 'prompt_version': args.prompt_version, 'prompt_digest': claude.prompt_digest(version=args.prompt_version),
                      'model': model.model, 'run_id': args.run_id}
            log('extraction | strategy %s | prompt %s (digest %s) | model %s' % (args.strategy, args.prompt_version, source['prompt_digest'], model.model))
            spec = claude.run(args.strategy, briefs, json.dumps(document, separators=(',', ':')), model, lambda s: validate(s, api, best_effort), log,
                              digest=lambda one, other=None: digest(one, api, other), system=claude.system_prompt(version=args.prompt_version))
        for rule in spec.get('unsupported') or []:
            log('NOT ENFORCED | the extractor reports policy the builder cannot express: %s' % rule)

        # 3. validate, compile, validate, archive; 4. the platform
        load = platform or (lambda: freya_client.load(args.kit, patiently=True))
        workflow, built, deployed = pipeline.release(files, args.slot, spec, document, source, log, best_effort, None if args.no_deploy else load, registry)
        steps, tools = counts(workflow)
        result.update(final_build=built['version_id'], steps=steps, tools=tools)
        if deployed is None:
            log('build | %s | %d steps, %d tools | not deployed (--no-deploy)' % (built['version_id'], steps, tools))
            raise Incomplete('built and not deployed, as asked (--no-deploy)')
        result.update(workflow_id=deployed['workflow_id'], agent_id=deployed['agent_id'])
        log('freya | workflow %s | agent %s | workflow name "%s ..." | build %s | platform version %s | %d steps | %d tools | %s' % (
            deployed['workflow_id'], deployed['agent_id'], args.slot, built['version_id'], (deployed.get('platform') or {}).get('published', {}).get('number', deployed.get('platform_version')),
            steps, tools, deployed['operation']))
        freya = load()

        # 5. self-tests and optimization, when test customers are named and there is time
        records, limits = [], None
        if not suites:
            result['untested'] = 'no suite of test customers was named for this brief'
            log('self-test | none: %s' % result['untested'])
        elif (usable - time.time()) < 120:
            result['untested'] = 'no time left before the deadline'
            log('self-test | none: %s' % result['untested'])
        else:
            args.deadline = usable
            evaluate = provider.Evaluation(args)
            folder = Path(args.out) / args.slot
            limits = policy.Limits(folder / ('limits.%s.json' % args.run_id), {'candidates': args.max_candidates, 'credits': args.max_credits, 'minutes': max(1.0, (usable - time.time()) / 60.0)})
            credits = (lambda: freya_client.credits(freya).get('creditsLeft')) if hasattr(freya, 'api') else None
            records = loop.run(args.slot, document, evaluate, freya, files, limits, folder / 'optimization.jsonl', client=None if args.no_optimize else model,
                               safety_cases=args.safety_case, best_effort=best_effort, credits=credits, check_platform_version=not args.no_platform_version_check,
                               registry=registry, trace=log, rounds=1 if args.no_optimize else args.rounds)
            for number, record in enumerate(records, 1):
                log('optimization | step %d | %s | %s | remaining: %d candidate(s), %.2f credits, %.0f min' % (
                    number, 'case %s (%s)' % (record['diagnosed'], record.get('filed_as')) if record.get('diagnosed') else 'no candidate', record['status'],
                    limits.limits['candidates'] - limits.state['candidates'], limits.limits['credits'] - limits.state['credits'], max(0.0, (usable - time.time()) / 60.0)))
            result.update(versions=versions(records), needs_code_change=((records[-1].get('diagnosis') or {}).get('needs_code_change') or []) if records else [],
                          credits=limits.state['credits'], steps_taken=[r['status'] for r in records])

        # 6. the final version: what the platform publishes, which build that is, and that build's export
        state = deployment.platform_versions(freya, deployed['workflow_id'])
        published = (state or {}).get('published')
        if not published:
            raise Incomplete('the platform lists no published version of workflow %s' % deployed['workflow_id'])
        final = loop.published_build(files, published)
        if not final:
            raise Incomplete('the published platform version %s (%s) is not a build of this slot\'s history' % (published.get('number'), published.get('id')))
        result.update(final_build=final['version_id'], published=published)
        files.restore(final['version_id'])
        kept = files.read(store.EXPORT, final['version_id'])
        if kept is None:
            raise Incomplete('build %s has no export kept with it' % final['version_id'])
        changed, _ = deployment.departures(files.read(store.WORKFLOW, final['version_id']), kept.get('workflow'))
        if changed or kept.get('workflow_id', deployed['workflow_id']) != deployed['workflow_id']:
            raise Incomplete('the export kept with build %s is not that build on this workflow: %s' % (final['version_id'], '; '.join(changed[:3]) or kept.get('workflow_id')))
        if any(r['status'].startswith('ROLLBACK NOT VERIFIED') or 'NOT PUT BACK' in r['status'] for r in records):
            raise Incomplete('a rollback of this run was not verified; the published version must be checked by hand')
        if args.repeat > 1 and records and limits is not None:
            result['repetitions'] = repetitions(args, evaluate, final['version_id'], deployed['workflow_id'], limits, credits, usable, records, log)
        export = files.version(final['version_id']) / store.EXPORT
        if args.export:
            Path(args.export).parent.mkdir(parents=True, exist_ok=True)
            Path(args.export).write_bytes(export.read_bytes())
            export = Path(args.export)
        result.update(export=str(export), status=SUCCESS)
        log('final | build %s | platform version %s (%s) is published, read back from Freya | export %s | trace %s' % (
            final['version_id'], published.get('number'), published.get('id'), export, log.path))
        last = next((v for v in reversed(result.get('versions') or []) if v['kept']), None)
        if last:
            got, failed = score(last['cases'])
            log('final | self-tests of the published version: %s case(s) pass%s | scope: %s, one run each' % (got, ' | failed: ' + ', '.join(failed) if failed else '', ', '.join(suites)))
        code = 0
    except KeyboardInterrupt as exc:
        result.update(status=INTERRUPTED, reason=str(exc) or 'interrupted')
        log('INTERRUPTED | %s' % result['reason'])
        code = 130
    except Incomplete as exc:
        result.update(status=INCOMPLETE, reason=str(exc))
        log('INCOMPLETE | %s' % exc)
    except SpecError as exc:
        result.update(status=FAILED, reason='the spec is not valid')
        for problem in exc.problems:
            log('SPEC ERROR | ' + problem)
    except Exception as exc:
        result.update(status=FAILED, reason='%s: %s' % (type(exc).__name__, str(exc)[:500]))
        log('FAILED | %s' % result['reason'])
    usage = getattr(client, 'usage', None) or []
    result.update(seconds=round(time.time() - started), ended_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'), model_calls=log.model_calls, model_dollars=spend.dollars(usage) if usage else 0.0)
    if time.time() > deadline:
        log('DEADLINE | the run took %.1f min, more than the %.0f it was given' % ((time.time() - started) / 60.0, args.deadline_minutes))
    for folder in [files.path('runs')] + ([Path(args.results)] if args.results else []):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (args.run_id + '.json')).write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
        (folder / (args.run_id + '.md')).write_text(report(result), encoding='utf-8')
    log('run end | status %s | %d s | %d model call(s), $%.2f | workflow %s | agent %s | build %s | export %s' % (
        result['status'], result['seconds'], result['model_calls'], result['model_dollars'], result.get('workflow_id'), result.get('agent_id'), result.get('final_build'), result.get('export')))
    log.close()
    return code


def versions(records):
    """The versions a run tested, in order, each with what was observed: the first one, then every candidate."""
    out = []
    for number, record in enumerate(records):
        incumbent = record.get('incumbent') or {}
        if number == 0 and incumbent.get('cases'):
            out.append({'role': 'initial version', 'version_id': incumbent.get('version_id'), 'cases': incumbent['cases'], 'kept': True})
        tested = record.get('tested') or {}
        if tested.get('cases') is not None and record.get('decision'):
            change = record.get('modification') or {}
            accepted = record['decision']['decision'] == policy.ACCEPT
            if accepted:
                for earlier in out:
                    earlier['kept'] = False
            out.append({'role': 'candidate %d (%s)' % (sum(1 for v in out if v['role'].startswith('candidate')) + 1, 'accepted' if accepted else 'rejected, rolled back'),
                        'version_id': tested.get('version_id'), 'cases': tested['cases'], 'kept': accepted,
                        'change': '%s, for case %s: %s' % (change.get('field') or change.get('kind'), record.get('diagnosed'), change.get('hypothesis')),
                        'decision': '%s at the %s gate: %s' % (record['decision']['decision'], record['decision']['gate'], record['decision']['reason'])})
    return out


def repetitions(args, evaluate, version_id, workflow_id, limits, credits, usable, records, log):
    """Test the final version again, up to --repeat runs in all, while the credits and the time allow a whole run.
    Returns one vector per run, the first being the run the decision rested on."""
    def balance():
        try:
            value = credits() if credits else None
        except Exception:
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    final = next((v for v in reversed(versions(records)) if v['kept']), None)
    out = [final['cases']] if final else []
    cases = evaluate.cases()
    for number in range(2, args.repeat + 1):
        most = len(cases) * loop.CREDITS_PER_CASE
        stop = limits.exhausted(most, 0) or ('no time left before the deadline' if usable - time.time() < 180 else '')
        if stop:
            log('repetition %d | not run: %s' % (number, stop))
            break
        before = balance()
        rows, _ = loop.bound(evaluate.run(version_id, '%s-r%d' % (loop.label(version_id), number)), version_id, workflow_id)
        after = balance()
        limits.spend(credits=max(0.0, before - after) if before is not None and after is not None else most)
        out.append(policy.shown({c: rows.get(c) or {} for c in cases}))
        log('repetition %d | %s case(s) pass' % (number, score(out[-1])[0]))
    return out


if __name__ == '__main__':
    raise SystemExit(main())
