"""Run the benchmark scenarios offline and write a report.

    python benchmarks/harness/run_benchmarks.py                      every executable scenario
    python benchmarks/harness/run_benchmarks.py --only EC-DT-02      chosen scenarios
    python benchmarks/harness/run_benchmarks.py --category dates     one category
    python benchmarks/harness/run_benchmarks.py --spec B=path.json   run a brief's scenarios against another spec
    python benchmarks/harness/run_benchmarks.py --list               list the scenarios, run nothing
    python benchmarks/harness/run_benchmarks.py --export-live DIR    write case files for the kit's runner, run nothing

No network call is made by any of these. Exit code 0 means no unexpected result: every
executable scenario passed, or failed exactly where its definition says the builder has a
known gap.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import assertions as check  # noqa: E402
from benchmarks.harness import scenarios as defs  # noqa: E402

PASS, FAIL, GAP, FIXED, SPEC_ONLY = 'PASS', 'FAIL', 'GAP', 'UNEXPECTED PASS', 'NOT RUN'
GAP_KINDS = {'defect': 'the builder does something wrong', 'unenforced': 'left to the phone model although code could enforce it',
             'schema': 'agent-spec/1 cannot express the rule'}


def run_steps(scenario, sim, bank, products, identity):
    bad = []
    for number, step in enumerate(scenario['steps'], 1):
        step = defs.resolve(step, products, identity)
        label = 'step %d' % number
        if 'faults' in step:
            bank.faults.update(step['faults'])
        elif 'check' in step:
            bad += check.check_view(label + ' (check)', sim, step['check'])
        elif 'call' in step:
            label += ' (%s)' % step['call']
            before = len(bank.calls)
            try:
                data = sim.call(step['call'], step.get('args') or {}, time_passes=step.get('time_passes', True))
                got = check.classify(step['call'], data, bank.calls[before:])
            except LookupError:
                data, got = {}, 'unavailable'
            bad += check.check_outcome(label, got, step.get('expect'))
            bad += check.check_text(label, got, data, str(sim.vars.get('rt_live') or ''), step.get('says'), step.get('not_says'))
        else:
            calls = [(c['call'], c.get('args') or {}) for c in step['together']]
            before = len(bank.calls)
            try:
                results = sim.together(calls, step.get('arrival', 'reversed'))
            except LookupError as missing:
                bad.append('%s (together): %s' % (label, missing))
                continue
            new = bank.calls[before:]
            arrival = list(range(len(calls)))
            if step.get('arrival', 'reversed') == 'reversed':
                arrival.reverse()
            for position, ((name, _), data, part) in enumerate(zip(calls, results, step['together'])):
                # Several calls of one tool: the bank saw them in arrival order, so each call has its own entry.
                same = [i for i in arrival if calls[i][0] == name]
                entries = [c for c in new if c[0] == name]
                mine = [entries[same.index(position)]] if len(same) > 1 and len(entries) == len(same) else new
                got = check.classify(name, data, mine)
                bad += check.check_outcome('%s (%s, sent together)' % (label, name), got, part.get('expect'))
                bad += check.check_text('%s (%s, sent together)' % (label, name), got, data, str(sim.vars.get('rt_live') or ''), part.get('says'), part.get('not_says'))
    return bad


def run_scenario(scenario, expected, overrides):
    """Returns (status, failures, bank call log, spec source)."""
    if scenario['tier'] == 'live_only':
        return SPEC_ONLY, [], [], ''
    try:
        sim, bank, source = defs.start(scenario, overrides)
        products = bank.products
        bad = run_steps(scenario, sim, bank, products, bank.identity)
        bad += check.check_final(defs.resolve(expected, products), bank, sim)
        log = check.call_log(bank)
    except Exception as error:      # a crash is a result worth reporting, not a reason to stop the run
        return FAIL, ['the harness or the generated code raised %s: %s' % (type(error).__name__, error)], [], ''
    if scenario.get('known_gap'):
        return (GAP if bad else FIXED), bad, log, source
    return (FAIL if bad else PASS), bad, log, source


def select(scenarios, args):
    chosen = scenarios
    if args.only:
        wanted = set(args.only.split(','))
        chosen = [s for s in chosen if s['id'] in wanted]
    if args.category:
        chosen = [s for s in chosen if s['category'] == args.category]
    if args.brief:
        chosen = [s for s in chosen if s['brief'] == args.brief]
    if args.tier:
        chosen = [s for s in chosen if s['tier'] == args.tier]
    return chosen


def report_text(rows, sources, started):
    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    out = ['# Benchmark report', '',
           'Run at %s, offline. Nothing in this report measures what a phone model says or which tools it chooses; '
           'it reports what the generated runtime does with the tool calls each scenario plays.' % started, '']
    out += ['Specs used: ' + '; '.join('%s = %s' % item for item in sorted(sources.items())), '']
    out += ['| Result | Scenarios | Meaning |', '| --- | --- | --- |']
    meaning = {PASS: 'every assertion held', FAIL: 'an assertion failed and no known gap explains it',
               GAP: 'failed where the definition documents a gap of the builder', FIXED: 'has a documented gap but passed: the definition is out of date',
               SPEC_ONLY: 'specification only, cannot be executed faithfully offline'}
    for status in (PASS, FAIL, GAP, FIXED, SPEC_ONLY):
        out.append('| %s | %d | %s |' % (status, counts.get(status, 0), meaning[status]))
    out += ['', '## By category', '', '| Category | Brief | PASS | FAIL | GAP | NOT RUN |', '| --- | --- | --- | --- | --- | --- |']
    groups = {}
    for row in rows:
        groups.setdefault((row['category'], row['brief']), []).append(row['status'])
    for (category, brief), statuses in sorted(groups.items()):
        out.append('| %s | %s | %d | %d | %d | %d |' % (category, brief, statuses.count(PASS), statuses.count(FAIL) + statuses.count(FIXED), statuses.count(GAP), statuses.count(SPEC_ONLY)))
    out += ['', '## Scenarios', '', '| Id | Tier | Result | Title |', '| --- | --- | --- | --- |']
    for row in rows:
        out.append('| %s | %s | %s | %s |' % (row['id'], row['tier'], row['status'], row['title']))
    gaps = [row for row in rows if row['status'] == GAP]
    if gaps:
        out += ['', '## Known gaps confirmed by this run', '', '| Kind | Id | Title |', '| --- | --- | --- |']
        for kind in GAP_KINDS:
            out += ['| %s | %s | %s |' % (GAP_KINDS[kind], row['id'], row['title']) for row in gaps if row.get('gap_kind') == kind]
    detail = [row for row in rows if row['failures']]
    if detail:
        out += ['', '## Failed assertions', '']
        for row in detail:
            out.append('### %s — %s (%s)' % (row['id'], row['title'], row['status']))
            if row.get('known_gap'):
                out.append('Documented gap (%s): %s' % (GAP_KINDS.get(row.get('gap_kind'), row.get('gap_kind')), row['known_gap']))
            out += ['- ' + line for line in row['failures']]
            out.append('- bank calls: ' + (' > '.join(row['bank_calls']) or 'none'))
            out.append('')
    return '\n'.join(out) + '\n'


def export_live(scenarios, expected, folder):
    """Case files in the format of the kit's run_cases.py. A live case has to use the account of a
    public practice case, because the bank creates a session from a case id only; two cases on the
    same account cannot share a file, so the cases are spread over batches."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    batches = []
    for scenario in scenarios:
        if scenario['mode'] not in ('live', 'both') or not scenario.get('live'):
            continue
        base = defs.public_case(scenario['customer']['public_case'])
        ledger = defs.resolve(expected[scenario['id']].get('ledger') or {}, [dict(p, product_id='p%d' % (i + 1)) for i, p in enumerate(base['products'])])
        case = {key: base[key] for key in ('id', 'variables', 'identity', 'products') if key in base}
        if base.get('faults'):
            case['faults'] = base['faults']
        live = scenario['live']
        case.update({
            'brief': scenario['brief'], 'level': 'benchmark', 'title': scenario['id'] + ' ' + scenario['title'],
            'family': 'Third party' if live.get('third_party') else scenario['category'], 'benchmark_id': scenario['id'],
            'script': live['script'], 'checks': live.get('checks') or scenario['spoken'],
            'expected': {'outcome': ledger.get('outcome'), 'reason': ledger.get('reason'), 'promises': ledger.get('promises') or [],
                         'notes': ledger.get('notes') or [], 'callbacks': ledger.get('callbacks') or [], 'transfers': ledger.get('transfers') or []}})
        if live.get('style'):
            case['style'] = live['style']
        for batch in batches:
            if case['id'] not in [c['id'] for c in batch]:
                batch.append(case)
                break
        else:
            batches.append([case])
    paths = []
    for number, batch in enumerate(batches, 1):
        path = folder / ('live-batch-%02d.json' % number)
        path.write_text(json.dumps(batch, indent=1), encoding='utf-8')
        paths.append((path, [c['benchmark_id'] for c in batch]))
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description='Offline benchmark of the Freya agent builder.')
    ap.add_argument('--only', help='comma-separated scenario ids')
    ap.add_argument('--category')
    ap.add_argument('--brief')
    ap.add_argument('--tier', choices=defs.TIERS)
    ap.add_argument('--spec', action='append', default=[], metavar='BRIEF=PATH', help='run the scenarios of a brief against this spec file')
    ap.add_argument('--report-dir', default=str(defs.BENCH / 'reports'))
    ap.add_argument('--no-report', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--export-live', metavar='DIR')
    args = ap.parse_args(argv)

    scenarios, expected = defs.load_scenarios(), defs.load_expected()
    problems = defs.definition_problems(scenarios, expected)
    if problems:
        print('The benchmark definitions are inconsistent:')
        for line in problems:
            print('  - ' + line)
        return 2
    chosen = select(scenarios, args)
    if args.list:
        for s in chosen:
            print('%-10s %-9s %-7s %-13s %s%s' % (s['id'], s['tier'], s['mode'], s['category'], s['title'], '  [known gap]' if s.get('known_gap') else ''))
        print('%d scenarios, %d executable offline' % (len(chosen), sum(s['tier'] != 'live_only' for s in chosen)))
        return 0
    if args.export_live:
        for path, ids in export_live(chosen, expected, args.export_live):
            print('%s: %s' % (path, ', '.join(ids)))
        return 0

    overrides = dict(item.split('=', 1) for item in args.spec)
    started = time.strftime('%Y-%m-%d %H:%M:%S')
    rows, sources = [], {}
    for scenario in chosen:
        status, failures, log, source = run_scenario(scenario, expected[scenario['id']], overrides)
        if source:
            sources[scenario['brief']] = source
        rows.append({'id': scenario['id'], 'title': scenario['title'], 'category': scenario['category'], 'brief': scenario['brief'],
                     'tier': scenario['tier'], 'mode': scenario['mode'], 'status': status, 'failures': failures,
                     'bank_calls': log, 'known_gap': scenario.get('known_gap'), 'gap_kind': scenario.get('gap_kind'),
                     'requirements': scenario['requirements']})
        print('%-15s %-10s %s' % (status, scenario['id'], scenario['title']))
        for line in failures:
            print('      - ' + line)
    total = {s: sum(r['status'] == s for r in rows) for s in (PASS, FAIL, GAP, FIXED, SPEC_ONLY)}
    print('\n%d scenarios: %d passed, %d failed, %d known gaps confirmed, %d unexpected passes, %d not executable offline'
          % (len(rows), total[PASS], total[FAIL], total[GAP], total[FIXED], total[SPEC_ONLY]))
    if not args.no_report:
        folder = Path(args.report_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'latest.md').write_text(report_text(rows, sources, started), encoding='utf-8')
        (folder / 'latest.json').write_text(json.dumps({'at': started, 'specs': sources, 'totals': total, 'scenarios': rows}, indent=1), encoding='utf-8')
        print('report: %s' % (folder / 'latest.md'))
    return 1 if total[FAIL] or total[FIXED] else 0


if __name__ == '__main__':
    sys.exit(main())
