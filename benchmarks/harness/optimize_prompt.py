"""GEPA optimisation of the three guidance sections of the extraction prompt, on development packages only.

    python benchmarks/harness/optimize_prompt.py --plan
    python benchmarks/harness/optimize_prompt.py --live --max-metric-calls 25 --minutes 60 --max-dollars 10 --run-dir outputs/gepa/run1

GEPA (https://pypi.org/project/gepa/, a development dependency) proposes new text for one section at
a time from what went wrong, and keeps the candidates that are best on some example. Here a candidate
is {reading, rules_writing, structuring}; an example is a development package; the score is the
rule-level score of the spec the single-pass extraction returns with that prompt.

What it may not do, and what stops it:
- change the part of the prompt that defines agent-spec/1: a candidate holds the three sections only;
- see a selection or final package: `development` refuses any other tier, here and in the adapter;
- memorise the development briefs: a candidate that contains a name, code or sentence of one of them
  scores zero without a model call;
- spend without limit: metric calls, wall-clock time and dollars are all capped, and the run can be
  resumed from its run directory without paying twice.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import experiment, packages  # noqa: E402
from builder.extraction import claude  # noqa: E402

MAX_GROWTH = 1.5
REFLECT = 'You improve instructions that are given to another model. Reply as the request asks.'


def development(ids):
    """The package ids, or a refusal: an optimisation never sees a selection or final package."""
    for pid in ids:
        if packages.tier(pid) != 'development':
            raise PermissionError('%s is a %s package: only development packages may be used to optimise a prompt' % (pid, packages.tier(pid)))
    return list(ids)


def brief_values(ids):
    """Names, codes and prescribed sentences of the development briefs that the seed prompt does not contain.
    A candidate that contains one has copied a brief instead of learning how to read one."""
    seed, found = claude.SYSTEM.casefold(), set()
    for pid in ids:
        documents, document = packages.inputs(pid)
        text = '\n'.join(body for _, body in documents)
        found.update(re.findall(r'`([^`\n]{4,40})`', text))
        found.update(re.findall(r'"([A-Z][^"\n]{12,}[.?!])"', text))
        found.update(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text))
        title = re.search(r'^# (?:Brief \w+: )?([^,:\n]+)', text, re.M)
        if title:
            found.add(title.group(1).strip())
        for item in (document.get('paths') or {}).values():
            for op in item.values():
                body = (((op.get('requestBody') or {}).get('content') or {}).get('application/json') or {}).get('schema') or {}
                for schema in (body.get('properties') or {}).values():
                    found.update(str(v) for v in schema.get('enum') or [] if len(str(v)) >= 4)
    return sorted(v for v in found if v.casefold() not in seed)


def guard(candidate, forbidden):
    """Why a candidate may not be tried, or ''."""
    if sorted(candidate) != sorted(claude.MUTABLE):
        return 'a candidate holds exactly the sections %s' % ', '.join(claude.MUTABLE)
    for name in claude.MUTABLE:
        if not isinstance(candidate[name], str) or len(candidate[name].strip()) < 40:
            return 'section %s is empty' % name
        if len(candidate[name]) > MAX_GROWTH * len(claude.SECTIONS[name]) + 400:
            return 'section %s grew beyond %.1f times its length' % (name, MAX_GROWTH)
        text = candidate[name].casefold()
        for value in forbidden:
            if value.casefold() in text:
                return 'section %s contains %r, a value of a development brief' % (name, value)
    return ''


def feedback(report, key_rules):
    """What a reflection is told about one extraction: only what the extraction itself got wrong. A rule the
    schema cannot carry or the runtime does not enforce is no fault of the prompt and is left out."""
    if report is None:
        return 'No valid specification was produced.'
    sentence = {r['id']: r['evidence'] for r in key_rules}
    details = {c['id']: c['extracted']['detail'] for c in report['checks'] if c['extracted']}
    lines = []
    for r in report['rules']:
        if r['layer'] != 'extraction':
            continue
        why = '; '.join(d for c in r['checks'] for d in details.get(c, []))[:400]
        lines.append('%s (%s%s): the documents say "%s". The specification got it %s. %s' % (
            r['id'], r['category'], ', critical' if r['critical'] else '', sentence.get(r['id'], '')[:300], r['extraction'], why))
    gave_up = [r['id'] for r in report['rules'] if r.get('declared_unsupported')]
    if gave_up:
        lines.append('Rules that were reported under "unsupported" although a structured field carries them: ' + ', '.join(gave_up))
    silent = [r['id'] for r in report['rules'] if str(r['extraction']).endswith('silent')]
    if silent:
        lines.append('Rules that no structured field can carry and that were neither reported under "unsupported" nor described in the rule text: ' + ', '.join(silent))
    return '\n'.join(lines) or 'Every rule that could be judged was extracted correctly.'


class Adapter:
    """What GEPA needs: evaluate a candidate on a batch of packages, and say what went wrong."""

    propose_new_texts = None          # GEPA's own proposer writes the new text, through `reflect`

    def __init__(self, client, ids, trace=lambda line: None, out=None):
        self.client, self.ids, self.trace, self.out = client, development(ids), trace, out
        self.forbidden = brief_values(self.ids)
        self.rows, self.stopped = [], ''

    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa.core.adapter import EvaluationBatch
        development(batch)
        refused = guard(candidate, self.forbidden)
        outputs, scores, objectives, notes = [], [], [], []
        for pid in batch:
            if refused:
                row, text = dict(experiment.score(None), package=pid, refused=refused), 'This candidate was not tried: ' + refused + '.'
            else:
                try:
                    row, report = experiment.run_one(pid, 'single', self.client, candidate, out=self.out,
                                                     label='gepa-%s-%03d' % (experiment.claude_hash(candidate), len(self.rows) + 1))
                except experiment.BudgetExceeded as stop:
                    self.stopped = str(stop)        # the run ends here: see `optimize`
                    raise
                text = feedback(report, packages.key(pid)['rules'])       # the key is read after the extraction returned
            self.rows.append(row)
            if self.out:                            # every evaluation is kept, also when the run ends early
                Path(self.out).mkdir(parents=True, exist_ok=True)
                with (Path(self.out) / 'rows.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(dict(row, sections=experiment.claude_hash(candidate) if not refused else 'refused')) + '\n')
            self.trace('gepa: %s score %.3f%s' % (pid, row['score'], ' (refused: ' + refused + ')' if refused else ''))
            outputs.append({'package': pid, 'score': row['score']})
            scores.append(row['score'])
            objectives.append({k: row[k] for k in ('accuracy', 'critical_recall', 'correct_policy', 'unsupported_declared')})
            notes.append({'package': pid, 'feedback': text, 'score': row['score']})
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=notes if capture_traces else None, objective_scores=objectives)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        examples = [{'Inputs': 'The documents and the tool API of package %s.' % t['package'],
                     'Generated Outputs': 'A specification that scored %.2f of 1.' % t['score'], 'Feedback': t['feedback']} for t in eval_batch.trajectories or []]
        return {name: examples for name in components_to_update}


def optimize(client, reflect, ids=('c', 'd', 'e'), max_metric_calls=25, minutes=60, run_dir=None, trace=lambda line: None, seed=0, record=None):
    """Run GEPA. `client` extracts, `reflect(prompt) -> text` proposes; both should be metered. Returns (best sections, result)."""
    import gepa
    from gepa.utils.stop_condition import TimeoutStopCondition
    adapter = Adapter(client, ids, trace, out=str(Path(run_dir) / 'extractions') if run_dir else None)
    result = gepa.optimize(seed_candidate=dict(claude.SECTIONS), trainset=list(adapter.ids), valset=list(adapter.ids), adapter=adapter,
                           reflection_lm=reflect, max_metric_calls=max_metric_calls,
                           stop_callbacks=[TimeoutStopCondition(minutes * 60), lambda state: bool(adapter.stopped)],
                           reflection_minibatch_size=2, module_selector='round_robin', candidate_selection_strategy='pareto',
                           run_dir=run_dir, cache_evaluation=True, display_progress_bar=False, raise_on_exception=False, seed=seed)
    best = result.best_candidate
    refused = guard(best, adapter.forbidden)
    if refused:                       # cannot happen through evaluate; checked again because this text would ship
        raise RuntimeError('the best candidate is not usable: ' + refused)
    if record is not None:
        record.update(rows=adapter.rows, stopped=adapter.stopped)
    return best, result


def main(argv=None):
    ap = argparse.ArgumentParser(description='Optimise the guidance sections of the extraction prompt with GEPA, on development packages.')
    ap.add_argument('--packages', nargs='+', default=['c', 'd', 'e'])
    ap.add_argument('--max-metric-calls', type=int, default=25)
    ap.add_argument('--minutes', type=float, default=60)
    ap.add_argument('--max-dollars', type=float)
    ap.add_argument('--model')
    ap.add_argument('--reflection-model')
    ap.add_argument('--run-dir', default='outputs/gepa/run')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--live', action='store_true', help='permit the paid Anthropic calls')
    args = ap.parse_args(argv)
    ids = development(args.packages)
    model = experiment.configured_model(args.model)
    reflection_model = args.reflection_model or model
    price_in, price_out = experiment.price(model)
    each = lambda kind, out=None: (experiment.TYPICAL[kind][0] * price_in + (out or experiment.TYPICAL[kind][1]) * price_out) / 1e6
    rounds = max(1, (args.max_metric_calls - len(ids)) // 4)
    print('GEPA on %s: at most %d extractions and about %d reflections, at most %.0f minutes' % (', '.join(ids), args.max_metric_calls, rounds, args.minutes))
    print('extraction %s at $%s in / $%s out per million tokens; reflection %s at $%s / $%s' % ((model,) + experiment.price(model) + (reflection_model,) + experiment.price(reflection_model)))
    print('planned cost: $%.2f typical, $%.2f at the output cap' % (
        args.max_metric_calls * each('extract') + rounds * each('reflect'), args.max_metric_calls * each('extract', experiment.WORST_OUT) * 1.3 + rounds * each('reflect', 8000)))
    if args.plan:
        return 0
    if not args.live:
        print('Nothing was called. Pass --live to permit the paid Anthropic calls.')
        return 2
    if args.max_dollars is None:
        ap.error('--live needs --max-dollars')
    from builder.config import settings
    settings.load_env(settings.env_files([], settings.KIT))
    lines = []
    trace = lambda line: (lines.append(line), print(line))
    # One limit for extraction and reflection together: two meters over one list of what was spent. No model
    # fallback, so every call is billed at the price of the model that was asked for.
    meter = experiment.Metered(claude.AnthropicClient(model, fallback=False), args.max_dollars)
    reflector = experiment.Metered(claude.AnthropicClient(reflection_model, max_tokens=8000, fallback=False), args.max_dollars)
    reflector.usage = meter.usage

    def reflect(prompt):
        text = prompt if isinstance(prompt, str) else json.dumps(prompt)
        reply, used, _ = reflector.complete(REFLECT, [{'role': 'user', 'content': text}])
        trace('model call: %s | purpose: propose a new prompt section (GEPA reflection) | in %s out %s tokens' % (used.get('model'), used.get('input_tokens'), used.get('output_tokens')))
        return reply

    record = {}
    best, result = optimize(meter, reflect, ids, args.max_metric_calls, args.minutes, args.run_dir, trace, record=record)
    folder = Path(args.run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'best.sections.json').write_text(json.dumps(best, indent=1, ensure_ascii=False), encoding='utf-8')
    (folder / 'trace.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    scores = list(result.val_aggregate_scores)
    (folder / 'candidates.json').write_text(json.dumps([{'index': n, 'score': scores[n], 'prompt': experiment.claude_hash(c), 'is_seed': c == dict(claude.SECTIONS), 'sections': c}
                                                        for n, c in enumerate(result.candidates)], indent=1, ensure_ascii=False), encoding='utf-8')
    (folder / 'usage.json').write_text(json.dumps(meter.usage, indent=1), encoding='utf-8')
    if record.get('stopped'):
        print('STOPPED at the spending limit: %s' % record['stopped'])
    print('candidates: %d | seed score %.3f | best score %.3f | spent $%.2f in %d calls' % (
        len(scores), scores[0], max(scores), experiment.dollars(meter.usage, meter.model), len(meter.usage)))
    print('best sections: %s' % (folder / 'best.sections.json'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
