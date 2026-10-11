"""Extraction experiments on the evaluation packages: run a strategy, evaluate the spec, count the cost.

    python benchmarks/harness/experiment.py --plan                                   what a run would call and cost; calls nothing
    python benchmarks/harness/experiment.py --packages c d e --strategies single audit double --repeat 3 --live --max-dollars 8 --out outputs/experiments/dev
    python benchmarks/harness/experiment.py --batch f --candidates candidates.json --purpose "selection batch" --live --max-dollars 6 --out outputs/experiments/holdout

Without --live no model is called: the command refuses. Development packages can be run as often as
needed, and are judged against their latest reference (benchmarks/references/). A selection or final
package is run only as one batch of frozen candidates (--batch): the whole batch is written to the
exposure log before the first model call, every extraction of the batch is finished before the key is
read, a run that has a result is never repeated, and a second batch on the same package is refused
unless its results are relabelled. Nothing here reads a key before the extraction it judges has returned.
"""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import packages, rules  # noqa: E402
from builder.extraction import claude  # noqa: E402
from builder.spec.models import SpecError  # noqa: E402
from builder.spec.openapi import Api  # noqa: E402
from builder.validation.spec import digest, validate  # noqa: E402

# Dollars per million tokens, input and output, of the Anthropic API; compared with the published price list on
# 2026-10-11. Thinking is billed as output. No caching, batch, fast mode or US-only inference is used.
PRICES = {'claude-sonnet-5-5': (2.0, 10.0), 'claude-opus-5-5': (4.0, 20.0), 'claude-haiku-5-5': (0.1, 0.5)}
DEAREST = (4.0, 20.0)


def price(model):
    """The price of a model, also when the API names it with a date. An unknown model is taken as the dearest."""
    name = str(model or '')
    return next((p for known, p in PRICES.items() if name.startswith(known)), DEAREST)
# Planning figures for one call, stated so that they can be argued with: a brief with its tool API and the prompt
# are about 9,000 tokens in; a spec with adaptive thinking at high effort is taken as 12,000 tokens out, and can
# reach the cap of 32,000. An audit or adjudication answers with a short list.
TYPICAL = {'extract': (9000, 12000), 'check': (11000, 4000), 'revise': (15000, 12000), 'reflect': (12000, 4000)}
WORST_OUT = 32000
CALLS = {'single': (('extract', 1, 3),), 'audit': (('extract', 1, 3), ('check', 1, 1), ('revise', 0.5, 1)),
         'double': (('extract', 2, 6), ('check', 0.8, 1), ('revise', 0.5, 1))}


class BudgetExceeded(RuntimeError):
    pass


def dollars(usage, model=None):
    total = 0.0
    for u in usage:
        price_in, price_out = price(u.get('model') or model)
        total += (u.get('input_tokens') or 0) * price_in / 1e6 + (u.get('output_tokens') or 0) * price_out / 1e6
    return round(total, 4)


class Metered:
    """A model client behind a spending limit that cannot be passed.

    Before every call the most that call can cost is worked out: every character of the request counted as
    half a token, which is more than any text comes to, and the reply taken at the client's output cap. The
    call is made only if what is spent plus that amount stays inside the limit. A call that ends in an error
    is charged at that amount, since the API may have billed it and reported nothing."""

    def __init__(self, client, max_dollars=None, max_calls=None):
        self.client, self.max_dollars, self.max_calls = client, max_dollars, max_calls
        self.model, self.usage = getattr(client, 'model', None), []

    def most(self, system, messages):
        """(input tokens, output tokens) the next call cannot exceed."""
        chars = len(system) + sum(len(m['content']) if isinstance(m['content'], str) else len(json.dumps(m['content'], default=str)) for m in messages)
        return chars // 2 + 500, int(getattr(self.client, 'max_tokens', 0) or 0)

    def complete(self, system, messages):
        spent = dollars(self.usage, self.model)
        tokens_in, tokens_out = self.most(system, messages)
        reserve = dollars([{'input_tokens': tokens_in, 'output_tokens': tokens_out}], self.model)
        if self.max_dollars is not None and (spent >= self.max_dollars or spent + reserve > self.max_dollars):
            raise BudgetExceeded('the limit of %.2f dollars does not cover another call: %.2f spent in %d calls, the next could cost up to %.2f' % (
                self.max_dollars, spent, len(self.usage), reserve))
        if self.max_calls is not None and len(self.usage) >= self.max_calls:
            raise BudgetExceeded('the limit of %d model calls is reached' % self.max_calls)
        try:
            text, used, content = self.client.complete(system, messages)
        except Exception:
            self.usage.append({'model': self.model, 'input_tokens': tokens_in, 'output_tokens': tokens_out, 'stop_reason': 'error', 'assumed': True})
            raise
        self.usage.append(dict(used))
        return text, used, content


NEUTRAL = ('ambiguous source', 'not evaluated', 'unsupported by schema: not detectable')
HANDLED = {'declared': 1.0, 'text only': 0.5, 'silent': 0.0}


def score(report):
    """One number from 0 to 1 for an evaluated extraction, and the parts it is made of.

    It measures the extraction, not the builder: whether each rule of the brief is in the spec in the form
    the schema has for it. Whether the runtime then enforces it is the evaluator's layer and end-to-end
    columns, and is not in this number.

    - A rule the schema carries: correct 1, partial 0.5, incorrect or missing 0. Leaving it out, or naming
      it under "unsupported" instead of encoding it, is 0.
    - A rule the schema cannot carry: named under "unsupported" 1, only in the rule text 0.5, dropped
      without a word 0. So an extraction is not marked down for what the builder cannot do, and gains
      nothing by keeping quiet about it.
    - A critical rule counts twice.
    - Not counted, for every extraction alike: an ambiguous source, a rule only a live call could show, a
      rule the reference itself fails, an unsupported rule the key gives no words to look for.

    Which rules count is decided by the key and the reference alone, so the weight is the same for every
    extraction of a package and no omission can shrink it."""
    empty = {'score': 0.0, 'accuracy': 0.0, 'critical_recall': 0.0, 'correct_policy': 0.0, 'unsupported_declared': 0.0, 'judged': 0, 'weight': 0.0,
             'critical_missed': [], 'wrongly_unsupported': [], 'unsupported_silent': []}
    if report is None:
        return empty
    points = weight = critical = critical_right = 0.0
    missed, wrongly, silent = [], [], []
    for r in report['rules']:
        verdict = str(r['extraction'])
        if verdict.startswith(NEUTRAL):
            continue
        if verdict.startswith('unsupported by schema: '):
            value = HANDLED.get(verdict.split(': ')[1], 0.0)
            if verdict.endswith('silent'):
                silent.append(r['id'])
        else:
            value = {'correct': 1.0, 'partial': 0.5}.get(verdict.split(' (')[0], 0.0)
        if r.get('declared_unsupported'):
            wrongly.append(r['id'])
        w = 2.0 if r['critical'] else 1.0
        weight += w
        points += w * value
        if r['critical']:
            critical += 1
            critical_right += value == 1.0
            if value < 1.0:
                missed.append(r['id'])
    e = report['summary']['extraction']
    return {'score': round(points / weight, 4) if weight else 0.0, 'accuracy': e['accuracy'] or 0.0,
            'critical_recall': round(critical_right / critical, 4) if critical else 1.0,
            'correct_policy': round(1.0 - (e['incorrect_policy_rate'] or 0.0), 4),
            'unsupported_declared': round(e['unsupported_declared'] / e['unsupported_rules'], 4) if e['unsupported_rules'] else 1.0, 'judged': e['judged'],
            'weight': weight, 'critical_missed': missed, 'wrongly_unsupported': wrongly, 'unsupported_silent': silent}


def reference_for(pid, reference='latest'):
    return packages.references(pid)[-1] if reference == 'latest' else reference


def extract_one(pid, strategy, client, sections=None, purpose=None, config=None, candidates=None, relabel=None):
    """One extraction of one package. Reads the inputs of the package and nothing of its key."""
    problems = packages.verify(pid)
    if problems:
        raise RuntimeError('package %s differs from the frozen manifest: %s' % (pid, '; '.join(problems)))
    config = dict(config or {}, strategy=strategy, prompt=claude_hash(sections))
    # A selection or final package is on the record, or refused, before any model sees it.
    rules.expose(pid, purpose, config, relabel, candidates)
    documents, document = packages.inputs(pid)
    api, usage, lines = Api(document), [], []
    started, at = time.perf_counter(), time.strftime('%Y-%m-%dT%H:%M:%S%z')
    try:
        spec = claude.run(strategy, documents, json.dumps(document, separators=(',', ':')), client, lambda s: validate(s, api, True), lines.append,
                          digest=lambda one, other=None: digest(one, api, other), system=claude.system_prompt(sections), usage=usage)
        failure = None
    except SpecError as exc:
        spec, failure = None, 'no valid spec after the repair turns: ' + '; '.join(exc.problems)[:600]
    seconds = round(time.perf_counter() - started, 2)
    waits = [float(u.get('seconds') or 0.0) for u in usage]
    return {'package': pid, 'tier': packages.tier(pid), 'strategy': strategy, 'prompt': config['prompt'], 'started_at': at, 'spec': spec, 'failure': failure,
            'usage': usage, 'trace': lines, 'model': getattr(client, 'model', None),
            # Wall clock of the whole extraction, as a builder run would wait for it; the part spent waiting for
            # the model; and the slowest single call.
            'seconds': seconds, 'model_seconds': round(sum(waits), 2), 'slowest_call_seconds': round(max(waits), 2) if waits else 0.0}


def judge(done, reference='latest', label=''):
    """Evaluate a finished extraction against the key. Returns the row of the result table and the report."""
    pid, usage, spec = done['package'], done['usage'], done['spec']
    version = reference_for(pid, reference)
    started = time.perf_counter()
    report = rules.evaluate(pid, spec, version) if spec is not None else None
    row = dict({'package': pid, 'tier': done['tier'], 'reference': version, 'strategy': done['strategy'], 'label': label, 'prompt': done['prompt'],
                'valid': spec is not None, 'failure': done['failure'],
                'calls': len(usage), 'repair_turns': sum('repair the spec' in str(u.get('purpose')) for u in usage),
                'input_tokens': sum(u.get('input_tokens') or 0 for u in usage), 'output_tokens': sum(u.get('output_tokens') or 0 for u in usage),
                'seconds': done['seconds'], 'model_seconds': done['model_seconds'], 'slowest_call_seconds': done['slowest_call_seconds'],
                'evaluation_seconds': round(time.perf_counter() - started, 2), 'started_at': done['started_at'],
                'dollars': dollars(usage, done['model'])}, **score(report))
    if report:
        row['layers'] = report['summary']['by_layer']
        row['extraction'] = {k: report['summary']['extraction'][k] for k in ('judged', 'correct', 'partial', 'incorrect', 'missing', 'unsupported_rules', 'unsupported_declared')}
        row['behaviour'] = report['summary']['behaviour']
    return row, report


def save(folder, done, row=None, report=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'trace.txt').write_text('\n'.join(done['trace']) + '\n', encoding='utf-8')
    (folder / 'extraction.json').write_text(json.dumps({k: v for k, v in done.items() if k != 'trace'}, indent=1, ensure_ascii=False), encoding='utf-8')
    if done['spec'] is not None:
        (folder / 'spec.json').write_text(json.dumps(done['spec'], indent=1, ensure_ascii=False), encoding='utf-8')
    if row is not None:
        (folder / 'result.json').write_text(json.dumps({'row': row, 'usage': done['usage']}, indent=1), encoding='utf-8')
    if report is not None:
        (folder / 'rules.json').write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')


def run_one(pid, strategy, client, sections=None, purpose=None, config=None, candidates=None, relabel=None, out=None, label='', reference='latest'):
    """Extract one package with one strategy and evaluate it. Returns the row of the result table and the report."""
    done = extract_one(pid, strategy, client, sections, purpose, config, candidates, relabel)
    row, report = judge(done, reference, label)      # the key is read only now
    if out:
        save(Path(out) / ('%s-%s%s' % (pid, strategy, ('-' + label) if label else '')), done, row, report)
    return row, report


# ---------------------------------------------------------------- one batch on a selection or final package

def load_candidates(path):
    """The frozen candidates of a batch: {"candidates": [{"name", "strategy", "sections": file or null, "repeat": n}]}.
    A sections file is read here and its text becomes part of the candidate, so that a later edit of the file is
    another candidate and another batch."""
    path = Path(path)
    listed = json.loads(path.read_text(encoding='utf-8')).get('candidates') or []
    out = []
    for c in listed:
        unknown = set(c) - {'name', 'strategy', 'sections', 'repeat'}
        if unknown or not c.get('name') or c.get('strategy') not in claude.STRATEGIES or int(c.get('repeat', 1)) < 1:
            raise ValueError('candidate %r: needs a name, a strategy of %s and a repeat of 1 or more' % (c.get('name'), ', '.join(claude.STRATEGIES)))
        sections = c.get('sections')
        if isinstance(sections, str):
            sections = json.loads((path.parent / sections).read_text(encoding='utf-8'))
        out.append({'name': str(c['name']), 'strategy': c['strategy'], 'sections': sections, 'repeat': int(c.get('repeat', 1)), 'prompt': claude_hash(sections)})
    if not out or len({c['name'] for c in out}) != len(out):
        raise ValueError('the candidates file needs at least one candidate, each with its own name')
    if len({(c['strategy'], c['prompt']) for c in out}) != len(out):
        raise ValueError('two candidates are the same strategy with the same prompt: repeat one of them instead')
    return out


def batch_id(pid, candidates, model, commit):
    """What a batch is: the package, the code, the model and the candidates. Change any and it is another batch."""
    frozen = [pid, packages.digest(packages.MANIFEST), model, commit, [[c['name'], c['strategy'], c['prompt'], c['repeat']] for c in candidates]]
    return hashlib.sha256(json.dumps(frozen).encode('utf-8')).hexdigest()[:12]


def run_batch(pid, candidates, client, purpose, out, commit, relabel=None, resume=False, max_dollars=None):
    """All frozen candidates on one selection or final package, as one evaluation.

    Order of events: everything that can be refused is refused first; the batch is written to the exposure
    log; every extraction is made and saved; only when all of them exist is the key read and every one
    evaluated. An interruption leaves the batch open: it can be continued with exactly the same candidates,
    code and model, which runs what is missing and repeats nothing. No score exists before the batch is
    complete, so nothing can be chosen, dropped or retried on a partial look.
    Returns (rows, summary) when complete and (None, state) when interrupted."""
    level = packages.tier(pid)
    if level == 'development':
        raise PermissionError('%s is a development package: it needs no batch' % pid)
    problems = packages.verify(pid)
    if problems:
        raise RuntimeError('package %s differs from the frozen manifest: %s' % (pid, '; '.join(problems)))
    model = getattr(client, 'model', None)
    batch = batch_id(pid, candidates, model, commit)
    folder = Path(out) / ('%s-%s' % (pid, batch))
    plan = [(c, n + 1) for c in candidates for n in range(c['repeat'])]
    place = lambda c, n: folder / ('%s-r%d' % (c['name'], n))
    have = lambda: [(c, n) for c, n in plan if (place(c, n) / 'extraction.json').exists()]
    if not resume and folder.exists() and any(folder.iterdir()):
        raise PermissionError('%s already holds results of batch %s: continue it with resume' % (folder, batch))
    if resume:
        logged = max([e.get('runs_done', 0) for e in rules.exposures() if e['package'] == pid and e.get('batch') == batch] or [0])
        if len(have()) < logged:
            raise PermissionError('%d run(s) of batch %s were made and their results are not in %s: they are not made again' % (logged, batch, folder))
    missing = len(plan) - len(have())
    if max_dollars is not None:
        need = sum(estimate([c['strategy']], 1, 1, model if model in PRICES else 'claude-sonnet-5-5')[1]['dollars'] for c, n in plan if (c, n) not in have())
        if need * 1.5 > max_dollars:
            raise BudgetExceeded('the limit of %.2f dollars would probably end this batch unfinished: %d run(s) are planned at about %.2f; allow at least %.2f' % (
                max_dollars, missing, need, need * 1.5))
    public = [{k: c[k] for k in ('name', 'strategy', 'prompt', 'repeat')} for c in candidates]
    rules.open_batch(pid, purpose, batch, {'commit': commit, 'model': model, 'candidates': public, 'runs': len(plan)}, relabel, resume)
    config = {'batch': batch, 'commit': commit, 'model': model}
    try:
        for c, n in plan:
            if (place(c, n) / 'extraction.json').exists():
                continue                  # a run that was made is not made again
            done = extract_one(pid, c['strategy'], client, c['sections'], purpose, dict(config, candidate=c['name']))
            save(place(c, n), done)
    except BaseException as stop:         # the log has to say that the batch is unfinished, whatever ended it
        rules.close_batch(pid, batch, len(have()), len(plan), '%s: %s' % (type(stop).__name__, str(stop)[:200]))
        if isinstance(stop, BudgetExceeded):
            return None, {'batch': batch, 'state': 'open', 'runs_done': len(have()), 'runs_planned': len(plan), 'stopped': str(stop), 'folder': str(folder)}
        raise
    rows = []
    for c, n in plan:                     # every extraction exists: the key is read now, for all of them at once
        done = json.loads((place(c, n) / 'extraction.json').read_text(encoding='utf-8'))
        done['trace'] = (place(c, n) / 'trace.txt').read_text(encoding='utf-8').splitlines()
        row, report = judge(done, 'v1', 'r%d' % n)
        row['candidate'] = c['name']
        save(place(c, n), done, row, report)
        rows.append(row)
    summary = {'batch': batch, 'state': 'completed', 'package': pid, 'tier': level, 'commit': commit, 'model': model, 'candidates': public,
               'rows': rows, 'summary': summarise(rows, 'candidate'), 'folder': str(folder)}
    (folder / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    rules.close_batch(pid, batch, len(plan), len(plan))
    return rows, summary


def claude_hash(sections=None):
    import hashlib
    return hashlib.sha256(claude.system_prompt(sections).encode('utf-8')).hexdigest()[:16]


def summarise(rows, by='strategy'):
    """Per strategy (or candidate) and prompt: means over the rows, the spread of the score, and how long it took."""
    groups = {}
    for r in rows:
        groups.setdefault((r[by], r['prompt'], r['tier'], r['reference']), []).append(r)
    out = []
    for (name, prompt, tier, reference), rs in sorted(groups.items()):
        mean = lambda key: round(sum(r[key] for r in rs) / len(rs), 4)
        seconds = [r['seconds'] for r in rs]
        out.append({by: name, 'prompt': prompt, 'tier': tier, 'reference': reference, 'runs': len(rs), 'packages': sorted({r['package'] for r in rs}),
                    'valid': sum(r['valid'] for r in rs), 'score': mean('score'), 'score_min': min(r['score'] for r in rs), 'score_max': max(r['score'] for r in rs),
                    'critical_recall': mean('critical_recall'), 'critical_recall_min': min(r['critical_recall'] for r in rs),
                    'correct_policy': mean('correct_policy'), 'accuracy': mean('accuracy'),
                    'critical_missed': sorted({i for r in rs for i in r['critical_missed']}), 'wrongly_unsupported': sorted({i for r in rs for i in r['wrongly_unsupported']}),
                    'calls': mean('calls'), 'seconds': mean('seconds'), 'seconds_median': round(statistics.median(seconds), 2), 'seconds_max': max(seconds),
                    'model_seconds': mean('model_seconds'), 'slowest_call_seconds': max(r['slowest_call_seconds'] for r in rs),
                    'dollars': mean('dollars'), 'total_dollars': round(sum(r['dollars'] for r in rs), 2)})
    return out


def commit_now():
    """(commit, True when the working tree differs from it). Frozen candidates mean frozen code."""
    git = lambda *args: subprocess.run(('git',) + args, cwd=str(packages.ROOT), capture_output=True, text=True).stdout.strip()
    return git('rev-parse', 'HEAD'), bool(git('status', '--porcelain', '--untracked-files=no'))


def configured_model(requested=None):
    """The model a run would really use: --model, else BUILDER_LLM_MODEL from the environment files, else the default."""
    from builder.config import settings
    try:
        settings.load_env(settings.env_files([], settings.KIT))
    except Exception:
        pass
    return settings.extraction_model(requested)


def estimate(strategies, n_packages, repeat, model='claude-sonnet-5-5'):
    """Planned calls and dollars of a run, from the stated planning figures. Returns (rows, totals)."""
    price_in, price_out = price(model)
    rows, totals = [], {'calls': 0.0, 'calls_max': 0, 'dollars': 0.0, 'dollars_max': 0.0}
    for strategy in strategies:
        calls = calls_max = cost = cost_max = 0.0
        for kind, expected, most in CALLS[strategy]:
            tokens_in, tokens_out = TYPICAL[kind]
            calls += expected
            calls_max += most
            cost += expected * (tokens_in * price_in + tokens_out * price_out) / 1e6
            cost_max += most * (tokens_in * 2 * price_in + WORST_OUT * price_out) / 1e6
        runs = n_packages * repeat
        rows.append({'strategy': strategy, 'runs': runs, 'calls': round(calls * runs, 1), 'calls_max': int(calls_max * runs),
                     'dollars': round(cost * runs, 2), 'dollars_max': round(cost_max * runs, 2)})
        for key in totals:
            totals[key] += rows[-1][key]
    return rows, {k: round(v, 2) for k, v in totals.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Run extraction strategies on evaluation packages and evaluate the specs.')
    ap.add_argument('--packages', nargs='+', default=['c', 'd', 'e'])
    ap.add_argument('--strategies', nargs='+', default=['single'], choices=claude.STRATEGIES)
    ap.add_argument('--repeat', type=int, default=1)
    ap.add_argument('--sections', help='JSON file with replacement prompt sections (reading, rules_writing, structuring)')
    ap.add_argument('--model')
    ap.add_argument('--plan', action='store_true', help='print planned calls and cost and call nothing')
    ap.add_argument('--live', action='store_true', help='permit the paid Anthropic calls')
    ap.add_argument('--max-dollars', type=float, help='stop before the call that would start above this')
    ap.add_argument('--reference', default='latest', help="reference of the development packages: 'latest' (default), 'v1' or 'v2'")
    ap.add_argument('--batch', help='a selection or final package: run all frozen candidates of --candidates on it as one logged batch')
    ap.add_argument('--candidates', help='the file of frozen candidates of a batch')
    ap.add_argument('--purpose', help='what a batch is for; written to the exposure log')
    ap.add_argument('--resume', action='store_true', help='continue an interrupted batch with the same candidates; repeats nothing')
    ap.add_argument('--relabel', help='run a further batch on a package that was already evaluated, and call its results this')
    ap.add_argument('--allow-dirty', action='store_true', help='run a batch although the working tree has uncommitted changes (recorded in the log)')
    ap.add_argument('--out', default='outputs/experiments/run')
    args = ap.parse_args(argv)
    if args.batch:
        return batch_main(ap, args)
    for pid in args.packages:
        if pid not in packages.ids():
            ap.error('no package %r' % pid)
        if packages.tier(pid) != 'development':
            ap.error('%s is a %s package: it is run only as one batch of frozen candidates, with --batch %s --candidates FILE --purpose TEXT' % (pid, packages.tier(pid), pid))
    model = configured_model(args.model)
    rows, totals = estimate(args.strategies, len(args.packages), args.repeat, model)
    print('planned for %s at $%s in / $%s out per million tokens, from the figures in this file (typical / most):' % ((model,) + price(model)))
    for r in rows:
        print('  %-7s %2d run(s): %5.1f / %3d calls, $%.2f / $%.2f' % (r['strategy'], r['runs'], r['calls'], r['calls_max'], r['dollars'], r['dollars_max']))
    print('  total: %.1f / %d calls, $%.2f / $%.2f' % (totals['calls'], totals['calls_max'], totals['dollars'], totals['dollars_max']))
    print('  judged against reference: %s' % ', '.join('%s %s' % (pid, reference_for(pid, args.reference)) for pid in args.packages))
    if args.max_dollars is not None:
        one = dollars([{'input_tokens': TYPICAL['extract'][0] * 2, 'output_tokens': WORST_OUT}], model)
        typical = dollars([{'input_tokens': TYPICAL['extract'][0], 'output_tokens': TYPICAL['extract'][1]}], model)
        fits = 0
        while fits * typical + one <= args.max_dollars:
            fits += 1
        print('  limit $%.2f: a call is made only while the limit covers it at its most (about $%.2f); at typical cost that allows %d of the %s planned call(s)' % (
            args.max_dollars, one, fits, ('%.0f' % totals['calls'])))
    if args.plan:
        return 0
    if not args.live:
        print('Nothing was called. Pass --live to permit the paid Anthropic calls, or --plan to see only the plan.')
        return 2
    if args.max_dollars is None:
        ap.error('--live needs --max-dollars')
    from builder.config import settings
    settings.load_env(settings.env_files([], settings.KIT))
    client = Metered(claude.AnthropicClient(args.model, fallback=False), args.max_dollars)
    sections = json.loads(Path(args.sections).read_text(encoding='utf-8')) if args.sections else None
    results = []
    try:
        for pid in args.packages:
            for strategy in args.strategies:
                for n in range(args.repeat):
                    row, _ = run_one(pid, strategy, client, sections, None, {'model': client.model}, out=args.out, label='r%d' % (n + 1), reference=args.reference)
                    results.append(row)
                    print('%s %-7s r%d  valid %-5s score %.3f  critical %.3f  calls %d  %.0f s (model %.0f s)  $%.2f%s' % (
                        pid, strategy, n + 1, row['valid'], row['score'], row['critical_recall'], row['calls'], row['seconds'], row['model_seconds'], row['dollars'],
                        '  critical missed: ' + ', '.join(row['critical_missed']) if row['critical_missed'] else ''))
    except BudgetExceeded as stop:
        print('STOPPED: %s' % stop)
    except Exception as stop:           # an API error ends the run; what finished is still written and the money counted
        print('STOPPED by an error: %s: %s' % (type(stop).__name__, str(stop)[:300]))
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / 'summary.json').write_text(json.dumps({'rows': results, 'summary': summarise(results), 'spent': dollars(client.usage, client.model)}, indent=1), encoding='utf-8')
    for s in summarise(results):
        print(json.dumps(s))
    print('spent: $%.2f in %d calls' % (dollars(client.usage, client.model), len(client.usage)))
    return 0


def batch_main(ap, args):
    if args.batch not in packages.ids() or packages.tier(args.batch) == 'development':
        ap.error('--batch takes a selection or final package')
    if not args.candidates or not args.purpose:
        ap.error('--batch needs --candidates and --purpose')
    candidates = load_candidates(args.candidates)
    model = configured_model(args.model)
    typical = sum(estimate([c['strategy']], 1, c['repeat'], model)[1]['dollars'] for c in candidates)
    most = sum(estimate([c['strategy']], 1, c['repeat'], model)[1]['dollars_max'] for c in candidates)
    commit, dirty = commit_now()
    print('batch on %s (%s): %d candidate(s), %d run(s), about $%.2f, at most $%.2f' % (
        args.batch, packages.tier(args.batch), len(candidates), sum(c['repeat'] for c in candidates), typical, most))
    for c in candidates:
        print('  %-12s %-7s prompt %s x%d' % (c['name'], c['strategy'], c['prompt'], c['repeat']))
    print('  batch id %s at commit %s%s' % (batch_id(args.batch, candidates, model, commit + ('+dirty' if dirty else '')), commit[:10], ', working tree DIRTY' if dirty else ''))
    if args.plan:
        return 0
    if not args.live:
        print('Nothing was called and nothing was logged. Pass --live to permit the paid Anthropic calls.')
        return 2
    if args.max_dollars is None:
        ap.error('--live needs --max-dollars')
    if dirty and not args.allow_dirty:
        ap.error('the working tree has uncommitted changes: candidates are frozen with the code. Commit first, or pass --allow-dirty.')
    from builder.config import settings
    settings.load_env(settings.env_files([], settings.KIT))
    client = Metered(claude.AnthropicClient(args.model, fallback=False), args.max_dollars)
    rows, result = run_batch(args.batch, candidates, client, args.purpose, args.out, commit + ('+dirty' if dirty else ''), args.relabel, args.resume, args.max_dollars)
    print('spent: $%.2f in %d calls' % (dollars(client.usage, client.model), len(client.usage)))
    if rows is None:
        print('INTERRUPTED: %s. %d of %d runs are saved. Nothing was evaluated. Continue with the same command and --resume.' % (
            result['stopped'], result['runs_done'], result['runs_planned']))
        return 3
    for s in result['summary']:
        print(json.dumps(s))
    print('batch %s completed: %s' % (result['batch'], result['folder']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
