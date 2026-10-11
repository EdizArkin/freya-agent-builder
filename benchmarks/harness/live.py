"""Live runs: a build behind a spending limit, what Freya stored of a deployment, and named simulations.

    python benchmarks/harness/live.py plan      --scenarios benchmarks/live/m17.scenarios.json
    python benchmarks/harness/live.py build     --slot m17-a-v1 --brief B.md [--brief MEMO.md] --tool-spec API.json --max-dollars 2 --live [--deploy]
    python benchmarks/harness/live.py dev       --packages c d e --max-dollars 2 --live
    python benchmarks/harness/live.py dry-run   --slot m17-c --spec SPEC.json --tool-spec API.json [--best-effort] --live
    python benchmarks/harness/live.py inspect   --slot m17-a-v1 --live
    python benchmarks/harness/live.py simulate  --slot m17-a-v1 --public A --live                 one simulation: M17 | A | Regression | v1
    python benchmarks/harness/live.py simulate  --slot m17-m16-v1 --scenarios benchmarks/live/m17.scenarios.json --agent m16 --live
    python benchmarks/harness/live.py simulate  --slot m17-a-v1 --public A --only PUB-06 --each --live   one scenario as a simulation of its own
    python benchmarks/harness/live.py backfill  --live                                   transcripts that could not be read when a simulation ended
    python benchmarks/harness/live.py recover   --run M19 [--name NAME --simulation-id ID --iteration-id ID] --live   a simulation that was started and never recorded
    python benchmarks/harness/live.py summary
    python benchmarks/harness/live.py credits   --label "before the run" --live

Without --live nothing leaves this machine: `plan` and `summary` never call anything, the others refuse.
`build` is the builder's own command (python -m builder) with the extraction model behind a limit that holds
across commands: every call is written to outputs/<run>/spend.json and counted by the next command.
`dev` extracts the development packages with the same limit and judges them against their reference; it refuses
a selection or final package. `dry-run` asks the platform to validate a workflow and stores nothing. `simulate` starts one simulation per agent
and suite (Regression, Safety, Robustness), named by builder.evaluation.simulation.suite_name; every scenario of
the suite is one persona of it, with its own bank session, call, tool trace, ledger and verdict. It writes one
record per simulation. Before a simulation is started, what is known of it is written to outputs/<run>/pending/:
if the command does not live to see the result, `recover` lists what is pending and, given the two ids the
platform gave the simulation, reads its verdicts, calls and bank records into an ordinary record.

What is written, all under the git-ignored outputs/:
    outputs/<run>/spend.json                         every paid model call of the run
    outputs/<run>/simulations/<simulation id>.json   the whole record: transcript, tool calls, ledger, verdicts
    outputs/<run>/index.jsonl                        one line per simulation: id, name, workflow, version, result
    outputs/<run>/pending/<name>.json                a simulation that was started and has no record yet
    outputs/<slot>/versions/<version id>/results.jsonl   the same lines, kept with the version they tested
"""
import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import experiment, packages  # noqa: E402
from builder import pipeline  # noqa: E402
from builder.artifacts import store  # noqa: E402
from builder.config import settings  # noqa: E402
from builder.evaluation import simulation  # noqa: E402
from builder.extraction import claude  # noqa: E402
from builder.extraction.spend import Budget, spent  # noqa: E402,F401  (the limit of a run is the builder's; kept under these names)
from builder.freya import client as freya_client  # noqa: E402
from builder.freya import deployment, versioning  # noqa: E402

# How a failed scenario is filed. The first five are the builder's layers, the sixth is the phone model's
# conversation, the last two are outside the builder.
CLASSES = ('extraction', 'schema', 'compiler', 'runtime', 'deployment', 'conversation', 'voice_platform', 'harness_or_judge')
MUTATIONS = ('promises', 'notes', 'callbacks', 'transfers')


# ---------------------------------------------------------------- scenarios

def fill(value, account):
    """`$p1.past_due` in an expected record and `{p1.past_due}` in a script, from the account of the bank case."""
    def look(match):
        product, field = match.group(1), match.group(2)
        found = account['identity'].get(field) if product == 'identity' else account['products'][int(product[1:]) - 1].get(field)
        if found is None:
            raise KeyError('%s.%s is not in the account of the bank case' % (product, field))
        return found
    if isinstance(value, dict):
        return {k: fill(v, account) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, account) for v in value]
    if isinstance(value, str):
        whole = re.fullmatch(r'\$(p\d|identity)\.(\w+)', value)
        if whole:
            return look(whole)
        return re.sub(r'\{(p\d|identity)\.(\w+)\}', lambda m: str(look(m)), value)
    return value


def load_scenarios(path, kit, agent=None, only=None):
    """The scenarios of a file as cases in the kit's format, each with the account of its bank case."""
    public = {c['id']: c for c in json.loads((Path(kit) / 'public-test-set.json').read_text(encoding='utf-8'))}
    out = []
    for s in json.loads(Path(path).read_text(encoding='utf-8'))['scenarios']:
        if (agent and s['agent'] != agent) or (only and s['id'] not in only):
            continue
        account = public[s['bank_case']]
        case = dict(s, variables=account['variables'], identity=account['identity'], products=account['products'])
        case['script'], case['expected'] = fill(s['script'], account), fill(s['expected'], account)
        out.append(case)
    return out


def public_cases(kit, brief, only=None):
    """The kit's practice cases of one brief, each with the short label its simulation is named by."""
    out = []
    for c in json.loads((Path(kit) / 'public-test-set.json').read_text(encoding='utf-8')):
        if c['brief'] == brief and (not only or c['id'] in only):
            out.append(dict(c, scenario='%s %s' % (c['id'], c['family']), agent=brief.lower(), gate='', suite='Regression'))
    return out


def suites(cases, each=False):
    """The simulations to start. One per brief and suite, each case a persona of it with its own bank session, call
    and verdict: what the list in the Freya UI then shows is one line per agent and test suite. With `each`, one
    simulation per case, named after the case."""
    if each:
        return [[c] for c in cases]
    groups = {}
    for c in cases:
        groups.setdefault((c.get('brief'), c.get('suite') or 'Regression'), []).append(c)
    return list(groups.values())


def name_of(group, args):
    first = group[0]
    if args.each:
        return simulation.simulation_name(args.run, first.get('brief'), first['scenario'], simulation.expected_label(first), args.version)
    return simulation.suite_name(args.run, first.get('brief'), first.get('suite') or 'Regression', args.version)


def unique(name, taken):
    """A name that is not in `taken`: the same scenario run again is 'r2', 'r3', ..."""
    if name not in taken:
        return name
    n = 2
    while '%s r%d' % (name, n) in taken:
        n += 1
    return '%s r%d' % (name, n)


def unexpected(row):
    """Records the bank holds although the scenario expects none of that kind or other ones: what a block must prevent."""
    want, have, out = row.get('expected') or {}, row.get('ledger') or {}, []
    key = {'promises': lambda p: (p.get('product_id'), round(float(p.get('amount') or 0), 2), p.get('date')),
           'notes': lambda n: (n.get('product_id'), n.get('reason')), 'callbacks': lambda c: (c.get('date'), c.get('time')),
           'transfers': lambda t: (t.get('queue'),)}
    for kind in MUTATIONS:
        allowed = [key[kind](x) for x in want.get(kind) or []]
        for item in have.get(kind) or []:
            if key[kind](item) in allowed:
                allowed.remove(key[kind](item))
            else:
                out.append('%s %s' % (kind[:-1], list(key[kind](item))))
    return out


def suggest(row, gate=''):
    """A first filing of a failed scenario, from the records alone. It is a suggestion: the report says where a
    reading of the transcript moved it."""
    if row['pass']:
        return None
    if unexpected(row):
        # The bank recorded something the scenario forbids. With a deterministic check for it, the check failed.
        return 'runtime' if gate else 'conversation'
    if row.get('ledger_pass'):
        return 'conversation'              # the bank is right and the judge found a check false in what was said
    if not row.get('call_id'):
        return 'harness_or_judge'
    return 'conversation'


# ---------------------------------------------------------------- commands

def need_live(args):
    if not args.live:
        print('Nothing was called. Pass --live to permit calls to Freya, the bank mock and, for build, Anthropic.')
        return False
    settings.load_env(settings.env_files([], args.kit))
    return True


def need_sdk():
    """A paid command stops here when this Python cannot make the call at all. The meter charges a call that
    ends in an error at its most, because the API may have billed it; a call that fails on the import was never
    sent, and must not be found out by the meter."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print('The Anthropic SDK is not installed for this Python (%s). Run the command with the Python that has it.' % sys.executable)
        return False
    return True


def run_dir(args):
    folder = Path(args.out) / store.build_id(args.run.lower())
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def slot_files(args):
    return store.BuildStore(args.out, args.slot)


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def cmd_plan(args):
    cases = public_cases(args.kit, 'A') + public_cases(args.kit, 'B') + (load_scenarios(args.scenarios, args.kit) if args.scenarios else [])
    groups = suites(cases, args.each)
    for group in groups:
        print('%-4s %-52s %d persona(s): %s' % (group[0]['agent'], name_of(group, args), len(group),
                                                ', '.join(c['id'] + (' on ' + c['bank_case'] if c.get('bank_case') else '') for c in group)))
    done, calls = spent(run_dir(args) / 'spend.json')
    print('%d simulation(s), %d call(s), one per persona. Paid model calls of this run so far: %d, $%.2f.' % (len(groups), len(cases), calls, done))
    return 0


def cmd_build(args):
    if not need_live(args):
        return 2
    ledger = run_dir(args) / 'spend.json'
    argv = ['--slot', args.slot, '--tool-spec', args.tool_spec, '--out', args.out, '--kit', args.kit, '--strategy', args.strategy, '--prompt-version', args.prompt_version]
    for brief in args.brief:
        argv += ['--brief', brief]
    if args.spec:
        argv += ['--spec', args.spec]
    else:
        argv += ['--live-extract']
    if args.best_effort:
        argv += ['--best-effort']
    if args.deploy:
        argv += ['--deploy']
    client = None
    if not args.spec:
        if args.max_dollars is None:
            print('build needs --max-dollars: the limit of the whole run, not of this command.')
            return 2
        if not need_sdk():
            return 2
        client = Budget(claude.AnthropicClient(args.model, fallback=False), args.max_dollars, ledger)
    before = spent(ledger)
    code = pipeline.main(argv, client=client)
    after = spent(ledger)
    print('paid model calls of this build: %d, $%.4f | of the run: %d, $%.4f%s' % (
        after[1] - before[1], after[0] - before[0], after[1], after[0], '' if args.max_dollars is None else ' of $%.2f' % args.max_dollars))
    return code


def cmd_credits(args):
    """Read the workspace's credit balance on Freya and note it with a label. Two readings around a run show
    what the run cost on the platform; the platform states no price for a simulation."""
    if not need_live(args):
        return 2
    got = freya_client.credits(freya_client.load(args.kit, patiently=True))
    row = {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'label': args.label, 'creditsLeft': got.get('creditsLeft'), 'creditUsdRate': got.get('creditUsdRate')}
    with (run_dir(args) / 'credits.jsonl').open('a', encoding='utf-8') as out:
        out.write(json.dumps(row) + '\n')
    print('credits left: %s | rate: %s | noted as "%s"' % (row['creditsLeft'], row['creditUsdRate'], args.label))
    return 0


def cmd_dev(args):
    """The development packages through the extraction a build uses, judged rule by rule against their latest
    reference. A selection or final package is refused here: those are run only as the logged batches of M15."""
    for pid in args.packages:
        if pid not in packages.ids() or packages.tier(pid) != 'development':
            print('%s is not a development package: it is not run here' % pid)
            return 2
    if not need_live(args):
        return 2
    if args.max_dollars is None:
        print('dev needs --max-dollars: the limit of the whole run, not of this command.')
        return 2
    if not need_sdk():
        return 2
    ledger = run_dir(args) / 'spend.json'
    client = Budget(claude.AnthropicClient(args.model, fallback=False), args.max_dollars, ledger)
    rows = []
    try:
        for pid in args.packages:
            row, _ = experiment.run_one(pid, args.strategy, client, None, None, {'model': client.model}, out=run_dir(args) / 'dev', label='r1', version=args.prompt_version)
            rows.append(row)
            print('%s %s prompt %s | valid %s | score %.3f | critical recall %.3f | incorrect policy %.3f | calls %d | $%.2f%s' % (
                pid, args.strategy, row['prompt_version'], row['valid'], row['score'], row['critical_recall'], 1 - row['correct_policy'], row['calls'], row['dollars'],
                ' | critical missed: ' + ', '.join(row['critical_missed']) if row['critical_missed'] else ''))
    except experiment.BudgetExceeded as stop:
        print('STOPPED: %s' % stop)
    (run_dir(args) / 'dev' / 'summary.json').write_text(json.dumps({'rows': rows, 'summary': experiment.summarise(rows)}, indent=1), encoding='utf-8')
    done, calls = spent(ledger)
    print('paid model calls of the run: %d, $%.4f of $%.2f' % (calls, done, args.max_dollars))
    return 0 if len(rows) == len(args.packages) and all(r['valid'] for r in rows) else 1


def cmd_dry_run(args):
    """Compile a spec and let the platform validate the workflow. Nothing is stored on Freya."""
    if not need_live(args):
        return 2
    # The tool API of an evaluation package is part of its inputs; only its inputs are read here, never its key.
    spec, document = read(args.spec), packages.inputs(args.package)[1] if args.package else read(args.tool_spec)
    workflow, report = pipeline.build_workflow(spec, document, args.best_effort)
    files = slot_files(args)
    built = versioning.build_record(args.slot, spec, workflow, {'kind': 'reused spec', 'file': str(args.spec)})
    files.archive(built['version_id'], spec, workflow, built)
    freya = freya_client.load(args.kit)
    started = time.time()
    try:
        freya.validate_workflow(args.slot + ' dry run', workflow)
        accepted, problem = True, ''
    except RuntimeError as exc:
        accepted, problem = False, str(exc)[:600]
    record = dict(built, platform_dry_run='accepted' if accepted else 'refused', problem=problem, seconds=round(time.time() - started, 1),
                  nodes=report['nodes'], tools=report['tools'], warnings=report['warnings'], stored_on_freya=False)
    files.write_json('dry-run.json', record)
    print('%s | %s | %d bytes | dry run %s%s | unsupported rules in the spec: %d' % (
        args.slot, built['version_id'], built['workflow_bytes'], record['platform_dry_run'], ' | ' + problem if problem else '', len(built['unsupported'])))
    return 0 if accepted else 1


def shape(workflow):
    """The structure of a workflow in the terms a person checks in the UI."""
    nodes = {}
    for name, node in (workflow.get('nodes') or {}).items():
        tools = []
        for tool in (node.get('tools') or []) + (node.get('pre_actions') or []):
            fn = tool.get('function') or {}
            props = (fn.get('parameters') or {}).get('properties') or {}
            body = (props.get('body') or {})
            tools.append({'name': fn.get('name'), 'handler': fn.get('handler'), 'method': (props.get('method') or {}).get('default'),
                          'url': (props.get('url') or {}).get('default'), 'body_fields': sorted((body.get('properties') or {})),
                          'required': sorted(body.get('required') or []), 'pre_hook': bool(tool.get('pre_process_code')), 'post_hook': bool(tool.get('post_process_code'))})
        nodes[name] = {'type': node.get('node_type'), 'tools': tools,
                       'transitions': [{'to': t.get('target_node'), 'when': 'always' if (t.get('condition') or {}).get('operator') == 'always' else
                                        ' or '.join('%s = %s' % (c.get('variable'), c.get('operand')) for c in (t.get('condition') or {}).get('conditions') or [])}
                                       for t in node.get('transitions') or []],
                       'max_node_turns': node.get('max_node_turns'), 'on_enter': (node.get('on_enter') or {}).get('type')}
    return {'initial_node': workflow.get('initial_node'), 'nodes': nodes, 'system_prompt_sha256': versioning.content_hash(workflow.get('system_prompt'))[:16],
            'test_variables': sorted(workflow.get('test_variables') or {})}


def voice_settings(workflow):
    """The speech and turn-taking settings the platform filled in for the agent. Read, never written."""
    config = workflow.get('agent_config') or {}
    stt, tts, llm, limits = (config.get(k) or {} for k in ('sttConfig', 'ttsConfig', 'llmConfig', 'callTimeoutSettings'))
    stop, start = tts.get('stopSpeakingPlan') or {}, tts.get('startSpeakingPlan') or {}
    return {'llm': {k: llm.get(k) for k in ('provider', 'model', 'maxTokens', 'temperature', 'firstMessageMode')},
            'stt': {'language': stt.get('language'), 'model': stt.get('model'), 'smartTurnEnabled': (stt.get('additionalSettings') or {}).get('smartTurnEnabled')},
            'tts': {'language': tts.get('language'), 'voiceId': tts.get('voiceId'), 'model': tts.get('model'), 'speed': (tts.get('additionalSettings') or {}).get('speed')},
            'stop_speaking': {'voiceSeconds': stop.get('voiceSeconds'), 'numberOfWords': stop.get('numberOfWords'), 'backOffSeconds': stop.get('backOffSeconds'),
                              'acknowledgementPhrases': stop.get('acknowledgementPhrases'), 'interruptionPhrases': stop.get('interruptionPhrases')},
            'start_speaking': {'waitSeconds': start.get('waitSeconds')},
            'timeouts': {k: limits.get(k) for k in ('silenceTimeout', 'maximumDuration', 'maxRetries')},
            'disableInterruptionsDuringFirstMessage': (llm.get('additionalSettings') or {}).get('disableInterruptionsDuringFirstMessage', llm.get('disableInterruptionsDuringFirstMessage'))}


def cmd_inspect(args):
    """Read the workflow back from Freya and compare it with the one the build sent."""
    if not need_live(args):
        return 2
    files = slot_files(args)
    entry, sent, built = versioning.known(args.slot), read(files.path(store.WORKFLOW)), read(files.path(store.BUILD), {})
    if not entry or sent is None:
        print('%s is not deployed from this machine, or has no local workflow' % args.slot)
        return 1
    freya = freya_client.load(args.kit)
    got = freya.api('GET', '/api/v2/workflow/' + entry['workflow_id'], workspaceId=freya.workspace_id())['workflow']
    stored = got['workflow']
    versions = freya.api('GET', '/api/v2/versions/workflow/' + entry['workflow_id'], workspaceId=freya.workspace_id()).get('versions') or []
    changed, added = deployment.departures(sent, stored)
    published = [v for v in versions if v.get('isPublished')]
    newest = max(versions, key=lambda v: v.get('versionNumber') or 0) if versions else {}
    record = {'slot': args.slot, 'workflow_id': entry['workflow_id'], 'agent_id': entry['agent_id'], 'name_on_freya': got.get('name'),
              'version_id': built.get('version_id'), 'sent_sha256': versioning.workflow_hash(sent), 'updated_at': got.get('updatedAt'),
              'platform_versions': [{'number': v.get('versionNumber'), 'published': bool(v.get('isPublished')), 'id': v.get('id')} for v in versions],
              'published_version': published[0].get('versionNumber') if published else None, 'newest_is_published': bool(newest.get('isPublished')),
              'changed': changed, 'added_by_platform': sorted({re.sub(r'\[\d+\]', '[]', a) for a in added}),
              'structure_sent': shape(sent), 'structure_stored': shape(stored), 'structure_equal': shape(sent) == shape(stored),
              'voice_settings': voice_settings(stored), 'inspected_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    files.write_json('inspection.json', record)
    print('%s | workflow %s | "%s" | published version %s (newest published: %s) | changed %d | structure equal %s' % (
        args.slot, entry['workflow_id'], got.get('name'), record['published_version'], record['newest_is_published'], len(changed), record['structure_equal']))
    for line in changed[:20]:
        print('  CHANGED ' + line)
    for name, node in record['structure_stored']['nodes'].items():
        print('  %-14s %-12s tools: %s | to: %s' % (name, node['type'], ', '.join('%s %s' % (t['method'] or '', t['name']) for t in node['tools']) or '-',
                                                    ', '.join(t['to'] for t in node['transitions']) or '-'))
    return 0 if not changed and record['newest_is_published'] else 1


def cmd_simulate(args):
    if not need_live(args):
        return 2
    only = set(args.only.split(',')) if args.only else None
    cases = public_cases(args.kit, args.public, only) if args.public else load_scenarios(args.scenarios, args.kit, args.agent, only)
    if not cases:
        print('no scenario selected')
        return 1
    files = slot_files(args)
    entry, built, deployed = versioning.known(args.slot), read(files.path(store.BUILD), {}), read(files.path(store.DEPLOYMENT), {})
    if not entry:
        print('%s is not deployed from this machine' % args.slot)
        return 1
    folder = run_dir(args)
    taken = names_taken(folder)
    runner = freya_client.load_runner(args.kit)
    reads = freya_client.patient(runner.freya.api) if hasattr(runner.freya, 'api') else None
    failed, not_run = 0, []
    groups = suites(cases, args.each)
    while groups:
        group = groups.pop(0)
        name = unique(name_of(group, args), taken)
        about = {'slot': args.slot, 'workflow_id': entry['workflow_id'], 'version_id': built.get('version_id'), 'platform_version': deployed.get('platform_version'),
                 'run': args.run, 'label': args.version, 'style': args.style}
        pending = simulation.Pending(folder / 'pending' / (store.build_id(name) + '.json'), about=about, gates={c['id']: c.get('gate') or '' for c in group},
                                     source={'public': args.public, 'scenarios': args.scenarios, 'agent': args.agent})
        if reads:
            runner.freya.api = pending.watch(reads)
        try:
            record = simulation.run(runner, entry['agent_id'], group, name, folder / 'personas.json', print, style=args.style, pending=pending)
        except simulation.SharedCustomer as exc:
            # Nothing was started. The cases of this suite run one by one instead, so that each keeps a ledger of its own.
            print('NOT GROUPED %s: %s' % (name, exc))
            if len(group) == 1:
                not_run += [c['id'] for c in group]
            else:
                groups = [[c] for c in group] + groups
            continue
        except Exception as exc:
            # A simulation that could not be started or read has no result. It is named here and never counted. If it
            # was started on Freya, what is known of it stays in outputs/<run>/pending/ and `recover` can read it later.
            not_run += [c['id'] for c in group]
            print('NOT RUN %s | %s: %s' % (', '.join(c['id'] for c in group), type(exc).__name__, str(exc)[:200]))
            if pending.path.is_file():
                print('  it may have run on Freya; kept for `recover`: %s' % pending.path)
            continue
        taken.add(name)
        write_record(folder, files, record, about, {c['id']: c.get('gate') or '' for c in group})
        pending.clear()
        failed += record['total'] - record['passed']
        print('%s | simulation %s | %d / %d' % (name, record['simulation_id'], record['passed'], record['total']))
    if not_run:
        print('not run, to be repeated: --only ' + ','.join(not_run))
    return 1 if failed or not_run else 0


def names_taken(folder):
    index = Path(folder) / 'index.jsonl'
    return {json.loads(line)['name'] for line in index.read_text(encoding='utf-8').splitlines() if line.strip()} if index.is_file() else set()


def write_record(folder, files, record, about, gates):
    """Put the record of one simulation on disk: the whole record, its line in the index of the run, and the same
    line beside the version it tested. `about` says which slot, workflow and version it ran on."""
    for row in record['rows']:
        annotate(row, gates.get(row['id']) or '')
    record.update(about)
    (folder / 'simulations').mkdir(parents=True, exist_ok=True)
    (folder / 'simulations' / (record['simulation_id'] + '.json')).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding='utf-8')
    line = simulation.index_row(record, **about)
    for item, row in zip(line['scenarios'], record['rows']):
        item.update(in_brief(row))
    for target in (folder / 'index.jsonl', files.version(about.get('version_id') or 'unversioned') / 'results.jsonl'):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('a', encoding='utf-8') as out:
            out.write(json.dumps(line, ensure_ascii=False) + '\n')
    return line


def cmd_recover(args):
    """A simulation that was started and has no record: list what is pending, or read one into a record.

    Reading needs the simulation id and the iteration id the platform gave it. The pending file lists the ids the
    platform answered with when the simulation was created; the Freya UI shows them as well. Nothing is started."""
    folder = run_dir(args)
    waiting = sorted((folder / 'pending').glob('*.json')) if (folder / 'pending').is_dir() else []
    if not waiting:
        print('nothing is pending in %s' % folder)
        return 0
    chosen = [p for p in waiting if not args.name or read(p).get('name') == args.name]
    if not (args.simulation_id and args.iteration_id) or len(chosen) != 1:
        for path in chosen or waiting:
            kept = read(path)
            print('%s | started %s | slot %s | version %s | cases %s' % (kept.get('name'), kept.get('started_at'), (kept.get('about') or {}).get('slot'),
                                                                        (kept.get('about') or {}).get('version_id'), ', '.join(c['id'] for c in kept.get('cases') or [])))
            for request in kept.get('requests') or []:
                print('   %s %s -> %s' % (request.get('method'), request.get('path'), json.dumps(request.get('ids'))))
        print('%d pending. To read one: recover --name "<name>" --simulation-id ID --iteration-id ID --live' % len(waiting))
        return 0 if not args.name else 1
    if not need_live(args):
        return 2
    kept = read(chosen[0])
    about, source = kept.get('about') or {}, kept.get('source') or {}
    only = {c['id'] for c in kept.get('cases') or []}
    cases = public_cases(args.kit, source['public'], only) if source.get('public') else load_scenarios(source.get('scenarios'), args.kit, source.get('agent'), only)
    runner = freya_client.load_runner(args.kit)
    if hasattr(runner.freya, 'api'):
        runner.freya.api = freya_client.patient(runner.freya.api)
    if any(line['simulation_id'] == args.simulation_id for line, _ in records(args)):
        print('simulation %s already has a record in this run: nothing is written twice' % args.simulation_id)
        return 1
    record = simulation.recover(runner, kept, cases, args.simulation_id, args.iteration_id, print)
    write_record(folder, store.BuildStore(args.out, about.get('slot')), record, about, kept.get('gates') or {})
    chosen[0].unlink()
    print('%s | simulation %s | %d / %d | recovered' % (record['name'], record['simulation_id'], record['passed'], record['total']))
    return 0


patient = freya_client.patient          # where it was before it was the builder's; kept under this name


def annotate(row, gate):
    """What the driver adds to the row of a scenario: its check, forbidden records, refused calls, a first filing."""
    row['gate'], row['unexpected_records'] = gate, unexpected(row)
    row['blocked_calls'] = [t['tool'] for t in row.get('tool_trace') or [] if t['blocked']]
    row['suggested_class'] = suggest(row, gate)
    return row


def in_brief(row):
    """The part of a row that is kept in the index and compared between versions: no transcript, no ledger."""
    return {'gate': row['gate'], 'unexpected_records': row['unexpected_records'], 'blocked_calls': row['blocked_calls'], 'suggested_class': row['suggested_class'],
            'ledger_pass': row['ledger_pass'], 'judge_outcome': row['judge_outcome'], 'ended': row['ended'], 'has_transcript': bool(row.get('messages')),
            'speech': {k: (len(v) if isinstance(v, list) else v) for k, v in row['speech'].items()}}


def records(args):
    """Every simulation of the run, in the order of the index, as its full record (or its index line if the record is gone)."""
    folder = run_dir(args)
    index = folder / 'index.jsonl'
    lines = [json.loads(line) for line in index.read_text(encoding='utf-8').splitlines() if line.strip()] if index.is_file() else []
    out = []
    for line in lines:
        record = read(folder / 'simulations' / (line['simulation_id'] + '.json'))
        for row in (record or {}).get('rows') or []:
            if row.get('messages'):         # worked out again from the transcript, so that a check added later covers earlier runs
                row.update(simulation.spoken(row['messages']))
        out.append((line, record))
    return out


def cmd_backfill(args):
    """Read again the transcripts that could not be read when their simulation ended. The verdicts of those runs
    stand as they were; only what is worked out from the transcript is filled in, and the row says so."""
    if not need_live(args):
        return 2
    runner, filled, missing = freya_client.load_runner(args.kit), 0, 0
    for line, record in records(args):
        changed = False
        for row in (record or {}).get('rows') or []:
            if row.get('messages') or not row.get('call_id'):
                continue
            call = simulation.read_call(runner, row['call_id'], print)
            if not call.get('messages'):
                missing += 1
                continue
            row.update(simulation.spoken(call['messages']), ended=call.get('endedReason'), duration=call.get('duration'), transcript_read_later=True)
            annotate(row, row.get('gate') or '')
            filled, changed = filled + 1, True
        if changed:
            (run_dir(args) / 'simulations' / (record['simulation_id'] + '.json')).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding='utf-8')
    print('transcripts read later: %d | still missing: %d' % (filled, missing))
    return 0 if not missing else 1


def cmd_summary(args):
    """Per workflow version: what was run and how it ended, in numbers that two versions can be compared by.
    Worked out from the full records, so a transcript that was read later counts."""
    versions = {}
    for line, record in records(args):
        v = versions.setdefault((line['slot'], line.get('version_id')), {'slot': line['slot'], 'version_id': line.get('version_id'), 'workflow_id': line['workflow_id'],
                                                                         'platform_version': line.get('platform_version'), 'simulations': 0, 'scenarios': []})
        v['simulations'] += 1
        rows = [dict(s, **in_brief(row)) for s, row in zip(line['scenarios'], record['rows'])] if record else line['scenarios']
        for s in rows:
            v['scenarios'].append(dict(s, simulation_id=line['simulation_id'], name=line['name']))
    out = []
    for v in versions.values():
        rows = v.pop('scenarios')
        latest = {}
        for r in rows:                    # every run is kept in the index; the summary also shows the last run of each scenario
            latest[r['id']] = r
        waits = [r['slowest_turn_s'] for r in rows if r.get('slowest_turn_s') is not None]
        speech = lambda key: sum((r.get('speech') or {}).get(key) or 0 for r in rows)
        out.append(dict(v, runs=len(rows), runs_passed=sum(r['pass'] for r in rows), scenarios=len(latest), scenarios_passing_last_run=sum(r['pass'] for r in latest.values()),
                        ledger_passed=sum(bool(r.get('ledger_pass')) for r in rows), judge_passed=sum(r.get('judge_outcome') == 'pass' for r in rows),
                        runs_with_unexpected_records=sum(bool(r.get('unexpected_records')) for r in rows), runs_with_blocked_calls=sum(bool(r.get('blocked_calls')) for r in rows),
                        failure_classes={c: sum(r.get('suggested_class') == c for r in rows) for c in CLASSES if any(r.get('suggested_class') == c for r in rows)},
                        slowest_turn_median_s=round(statistics.median(waits), 1) if waits else None, slowest_turn_worst_s=max(waits) if waits else None,
                        agent_turns=speech('agent_turns'), cut_off_turns=speech('cut_off'), long_turns=speech('long'), repeated_turns=speech('repeated'),
                        tool_text_turns=speech('tool_text'), silent_after_tool=speech('silent_after_tool'), calls_without_farewell=sum(bool((r.get('speech') or {}).get('no_farewell')) for r in rows),
                        customer_turns=speech('customer_turns'), customer_cut_off_turns=speech('customer_cut_off'),
                        failed=[{'id': r['id'], 'name': r['name'], 'simulation_id': r['simulation_id'], 'class': r.get('suggested_class'), 'failed': r['failed']} for r in rows if not r['pass']]))
    done, calls = spent(run_dir(args) / 'spend.json')
    summary = {'run': args.run, 'versions': out, 'paid_model_calls': calls, 'paid_model_dollars': done, 'written_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    (run_dir(args) / 'summary.json').write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding='utf-8')
    for v in out:
        print('%-22s %s | %d run(s), %d passed | scenarios passing on their last run %d / %d | unexpected records in %d run(s) | blocked calls seen in %d run(s) '
              '| agent turns cut off %d of %d | customer turns cut off %d of %d' % (
                  v['slot'], v['version_id'], v['runs'], v['runs_passed'], v['scenarios_passing_last_run'], v['scenarios'], v['runs_with_unexpected_records'],
                  v['runs_with_blocked_calls'], v['cut_off_turns'], v['agent_turns'], v['customer_cut_off_turns'], v['customer_turns']))
        for f in v['failed']:
            print('   FAIL %-18s %-14s %s' % (f['id'], f['class'], ' | '.join(f['failed'])[:230]))
    print('paid model calls: %d, $%.2f' % (calls, done))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('plan', 'build', 'dev', 'dry-run', 'inspect', 'simulate', 'backfill', 'recover', 'summary', 'credits'))
    ap.add_argument('--live', action='store_true', help='permit calls to Freya, the bank mock and, for build, Anthropic')
    ap.add_argument('--run', default='M17', help='the first part of every simulation name, and the folder of the run under --out')
    ap.add_argument('--version', default='v1', help='the last part of every simulation name: the version label of the workflow under test')
    ap.add_argument('--slot', help='the agent: its folder under --out and its entry in the slot registry')
    ap.add_argument('--out', default=str(settings.OUTPUTS))
    ap.add_argument('--kit', default=str(settings.KIT))
    ap.add_argument('--brief', action='append', default=[], help='build: a document, in order; later ones amend earlier ones')
    ap.add_argument('--tool-spec', help='build, dry-run: the tool API file')
    ap.add_argument('--spec', help='build, dry-run: reuse a spec instead of extracting one')
    ap.add_argument('--strategy', choices=claude.STRATEGIES, default='audit')
    ap.add_argument('--prompt-version', choices=sorted(claude.PROMPT_VERSIONS), default=claude.PROMPT_VERSION)
    ap.add_argument('--model')
    ap.add_argument('--max-dollars', type=float, help='build, dev: the limit of the whole run for paid model calls')
    ap.add_argument('--packages', nargs='+', default=['c', 'd', 'e'], help='dev: development packages to extract and judge')
    ap.add_argument('--package', help='dry-run: take the tool API from the inputs of this evaluation package')
    ap.add_argument('--label', default='reading', help='credits: what this reading of the balance is')
    ap.add_argument('--best-effort', action='store_true', help='build, dry-run: compile although the spec reports policy it cannot express')
    ap.add_argument('--deploy', action='store_true', help='build: create or update the workflow of the slot on Freya')
    ap.add_argument('--public', choices=('A', 'B'), help='simulate: the practice cases of this brief')
    ap.add_argument('--scenarios', help='plan, simulate: a file of scenarios')
    ap.add_argument('--agent', help='simulate: only the scenarios of the file that are for this agent')
    ap.add_argument('--only', help='simulate: comma-separated scenario ids')
    ap.add_argument('--style', help="simulate: a way of talking of the kit's customers (pieces, corrects, talkative, asks_back, in_words)")
    ap.add_argument('--each', action='store_true', help='plan, simulate: one simulation per scenario, named after it, instead of one per suite')
    ap.add_argument('--name', help='recover: the name of the pending simulation')
    ap.add_argument('--simulation-id', help='recover: the id the platform gave the simulation')
    ap.add_argument('--iteration-id', help='recover: the id of its iteration')
    args = ap.parse_args(argv)
    if args.command in ('build', 'dry-run', 'inspect', 'simulate') and not args.slot:
        ap.error(args.command + ' needs --slot')
    if args.command == 'build' and not (args.brief and args.tool_spec):
        ap.error('build needs --brief and --tool-spec')
    if args.command == 'dry-run' and not (args.spec and (args.tool_spec or args.package)):
        ap.error('dry-run needs --spec and --tool-spec or --package')
    if args.command == 'simulate' and not (args.public or args.scenarios):
        ap.error('simulate needs --public or --scenarios')
    return {'plan': cmd_plan, 'build': cmd_build, 'dev': cmd_dev, 'credits': cmd_credits, 'dry-run': cmd_dry_run, 'inspect': cmd_inspect, 'simulate': cmd_simulate, 'backfill': cmd_backfill, 'recover': cmd_recover, 'summary': cmd_summary}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
