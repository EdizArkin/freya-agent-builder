"""Rule-level evaluation of a spec against the frozen key of a package.

    python benchmarks/harness/rules.py                          every package, its reference spec only
    python benchmarks/harness/rules.py --package c --spec x.json --purpose "..."   one extracted spec
    python benchmarks/harness/rules.py --mutations              is the evaluator sensitive? (reference specs, mutated)

Nothing here calls a model, Freya or the bank. For every rule of a package the key lists checks;
each check is run on the hand-written reference spec and, when one is given, on an extracted spec,
at two levels: on the spec itself through the runtime's own functions, and through the compiled
workflow in the offline engine. The first stage that fails owns the rule:

    source      the brief is ambiguous; nothing is asserted
    key         the reference spec fails where the key says the schema can carry the rule: the key is wrong
    schema      the key says agent-spec/1 cannot carry the rule
    extraction  the reference passes a check and the extracted spec does not
    compiler/runtime   the reference spec encodes the rule and the compiled workflow does not behave so; or
                       a value the extracted spec computes correctly comes out differently once compiled
    openapi request    a request sent during the rule's checks breaks the tool API
    conversational     only a live call could show it; not evaluated here

Three kinds of evidence are kept apart and never added up:

    policy       a value the brief prescribes (a limit, a date, a table), or the bank's records after
                 the calls a compliant agent would make
    enforcement  the calls include one no compliant agent makes, and the generated code stops or
                 corrects it. That is a property of this builder, not a sentence of a brief
    weak         a prescribed sentence is found in the spec (`text`) or in the prompt of one step of
                 the compiled call (`prompt`). It shows that an instruction reaches the phone model,
                 not what the model does with it

A rule is called enforced only where an enforcement check passed; a limit that is in the spec and
comes out of the compiled workflow, but was not tried with a call that breaks it, is called encoded.
A pass says what the generated code does with the tool calls a check plays. It says nothing about
what a phone model will say or which tools it will choose, and a reference spec that passes its own
key shows that the two are consistent, not that either is right: both have the same author.
"""
import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.harness import assertions as check  # noqa: E402
from benchmarks.harness import packages  # noqa: E402
from benchmarks.harness.run_benchmarks import run_steps  # noqa: E402
from benchmarks.harness.scenarios import CUSTOMER_ID, DEFAULT_VARIABLES, Call, resolve  # noqa: E402
from builder.compiler.compiler import runtime_cfg  # noqa: E402
from builder.pipeline import build_workflow  # noqa: E402
from builder.runtime import runtime as rt  # noqa: E402
from builder.spec.models import SpecError  # noqa: E402
from builder.spec.openapi import Api  # noqa: E402
from builder.validation import contract  # noqa: E402
from builder.validation.spec import validate  # noqa: E402
from tests.freya_sim import Bank  # noqa: E402

KINDS = ('fact', 'structure', 'calls', 'text', 'prompt', 'live')
STRONG = ('fact', 'structure', 'calls')
WEAK = ('text', 'prompt')
ASSERTS = ('policy', 'enforcement')
FITS = ('structured', 'text', 'partial', 'unsupported')
REPORTS = packages.BENCH / 'reports'
EXPOSURE = REPORTS / 'm15.exposure.jsonl'
PLACEHOLDER = 'blocked_by_policy'
CHANNEL = 'expected refused, got detached'


class MappedBank(Bank):
    """The stand-in bank behind a tool API whose fields have other names.

    The names belong to the answer key (key/bank.json): request fields are translated to the
    stand-in's own names on the way in, answers on the way out. The ledger keeps the stand-in's
    names, so the expected records of every package read alike."""

    def __init__(self, names, *args):
        Bank.__init__(self, *args)
        self.request, self.response = names.get('request') or {}, names.get('response') or {}

    def call(self, tool, body):
        # A field this API has renamed is unknown to its bank under the old name: such a stray field
        # is ignored, as a real service would ignore it. It must not pass for the renamed one.
        renamed = set(self.request.values())
        known = {self.request[k] if k in self.request else k: v for k, v in body.items() if k in self.request or k not in renamed}
        status, out = Bank.call(self, tool, known)
        return status, self._named(out)

    def _named(self, value):
        if isinstance(value, dict):
            return {self.response.get(k, k): self._named(v) for k, v in value.items()}
        return [self._named(v) for v in value] if isinstance(value, list) else value


class Recorded(Call):
    """The benchmark's call, keeping every request as it leaves its pre-hook."""

    def __init__(self, *args):
        self.requests = []
        Call.__init__(self, *args)

    def _respond(self, tool, config, variables=None):
        props = tool['function']['parameters']['properties']
        self.requests.append((tool['function']['name'], {'url': config.get('url'), 'method': props['method']['default'],
                                                         'body': copy.deepcopy(config.get('body'))}))
        return Call._respond(self, tool, config, variables)


class Target:
    """One spec against one package, validated and compiled once."""

    def __init__(self, name, spec, document, defaults, names):
        self.name, self.spec, self.document, self.defaults, self.names = name, spec, document, defaults, names
        self.api, self.problems, self.workflow, self.cfg = Api(document), [], None, None
        try:
            validate(spec, self.api, True)
            self.cfg = runtime_cfg(spec, self.api)
            self.workflow, _ = build_workflow(spec, document, True)
        except SpecError as exc:
            self.problems = list(exc.problems)
        except Exception as exc:        # a spec that crashes the builder is a result, not a reason to stop
            self.problems = ['%s: %s' % (type(exc).__name__, exc)]

    def start(self, customer, variables=None):
        values = dict(DEFAULT_VARIABLES, **(self.defaults.get('variables') or {}))
        values.update(customer.get('variables') or {})
        values.update(variables or {})
        for name in self.names.get('call_id_variables') or []:
            values[name] = CUSTOMER_ID
        bank = MappedBank(self.names, CUSTOMER_ID, dict(customer.get('identity') or self.defaults.get('identity') or {}),
                          copy.deepcopy(customer['products']), dict(customer.get('faults') or {}))
        return Recorded(copy.deepcopy(self.workflow), bank, values), bank


# ---------------------------------------------------------------- comparing

def norm(text):
    text = str(text).replace('{{', '{').replace('}}', '}').replace('’', "'").replace('“', '"').replace('”', '"')
    return re.sub(r'\s+', ' ', text).strip().casefold()


def all_text(spec):
    """Everything in a spec that the phone model reads or says."""
    parts = [spec.get('opening_line')] + list((spec.get('rules') or {}).values()) + list((spec.get('guidance') or {}).values())
    parts += list((spec.get('tool_descriptions') or {}).values())
    parts += [h.get('instruction') for h in ((spec.get('identity') or {}).get('hints') or []) if isinstance(h, dict)]
    return norm(' \n '.join(str(p) for p in parts if isinstance(p, str)))


def differences(want, got, shown):
    bad = []
    for name, value in want.items():
        if name == 'shown':
            bad += ['no computed value shows %r' % part for part in value if str(part) not in shown]
        elif name == 'label':
            # Words a person hears: capitals and spacing do not matter. Every other value is compared exactly.
            if norm(got.get(name) or '') != norm(value):
                bad.append('%s is %r, expected %r' % (name, got.get(name), value))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            have = rt.number(got.get(name))
            if have is None or abs(have - float(value)) > 0.005:
                bad.append('%s is %r, expected %r' % (name, got.get(name), value))
        elif (None if got.get(name) is None else str(got.get(name))) != (None if value is None else str(value)):
            bad.append('%s is %r, expected %r' % (name, got.get(name), value))
    return bad


def dig(node, path):
    for part in path.split('.'):
        if isinstance(node, list) and part.isdigit():
            node = node[int(part)] if int(part) < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
    return node


def asserts(c):
    """What a check is evidence of. `policy`: a value the brief prescribes, or the bank's records after the calls a
    compliant agent would make. `enforcement`: the calls include one no compliant agent makes, and the check is
    that the generated code stops or corrects it. That is a property of this builder, not a sentence of a brief."""
    return c.get('asserts') or 'policy'


def structure(target, c):
    how, want, got = c['compare'], c.get('expect'), dig(target.spec, c['path']) if c.get('path') else None
    if how == 'absent':
        ok = not got
    elif how == 'present':
        ok = bool(got)
    elif how == 'equal':
        ok = got == want
    elif how == 'set':
        ok = isinstance(got, list) and set(got) == set(want)
    elif how == 'includes':
        ok = isinstance(got, list) and set(want) <= set(got)
    elif how == 'set_map':
        ok = isinstance(got, dict) and all(isinstance(v, list) for v in got.values()) and {k: set(v) for k, v in got.items()} == {k: set(v) for k, v in want.items()}
    elif how == 'field_list':
        ok = isinstance(got, list) and [i.get(c['field']) if isinstance(i, dict) else None for i in got] == want
    elif how == 'contains':
        ok = isinstance(got, str) and all(norm(part) in norm(got) for part in want)
    elif how == 'callback_slots':
        got = rt.callback_slots(target.cfg, rt.parse_now(c['now'])) if target.spec.get('callback') else None
        ok = got == want
    else:
        raise ValueError('unknown comparison %r' % how)
    return [] if ok else ['%s (%s) is %s, expected %s' % (c.get('path') or how, how, json.dumps(got, default=str)[:300], json.dumps(want)[:300])]


# ---------------------------------------------------------------- running one check

def run_check(target, c):
    """{'spec': pass|fail|na, 'compiled': pass|fail|na, 'detail': [...], 'requests': n, 'blocked': n, 'contract': [...]}"""
    out = {'spec': 'na', 'compiled': 'na', 'detail': [], 'requests': 0, 'blocked': 0, 'contract': [], 'channel': []}
    if c['kind'] == 'live':
        return out
    if target.problems:
        out.update(spec='fail', compiled='fail' if c['kind'] in ('fact', 'calls') else 'na', detail=['the spec is not valid: ' + '; '.join(target.problems)[:400]])
        return out

    def played(sim):
        for tool, request in sim.requests:
            out['requests'] += 1
            if PLACEHOLDER in [v for v in (request.get('body') or {}).values() if isinstance(v, str)]:
                out['blocked'] += 1
            else:
                out['contract'] += contract.check_request(target.api, tool, request)

    def level(name, bad):
        # Since M15 every block detaches the request, so a refusal reaches the model through the live state and
        # not as a tool result. A check that expected the reason as the tool result, and found the call blocked
        # with nothing at the bank, differs in the channel only. That is listed apart; it is not a failure of
        # enforcement, and the rest of the check (texts, later steps, the bank's records) is judged as written.
        out['channel'] += [line for line in bad if line.endswith(CHANNEL)]
        bad = [line for line in bad if not line.endswith(CHANNEL)]
        out[name] = 'fail' if bad else 'pass'
        out['detail'] += ['%s level: %s' % (name, line) for line in bad]

    try:
        if c['kind'] == 'structure':
            level('spec', structure(target, c))
        elif c['kind'] == 'text':
            level('spec', [] if norm(c['literal']) in all_text(target.spec) else ['the text of the spec lacks %r' % c['literal']])
        elif c['kind'] == 'fact':
            account = target.spec['account']
            when = rt.parse_now(c['now'])
            fact = (rt.product_facts(target.cfg, [dict(c['product'], **{account['id_field']: 'p1'})], when.date(), when.strftime('%H:%M')) or [{}])[0]
            level('spec', differences(c['expect'], fact, ' | '.join(pair[1] for pair in fact.get('shown') or [])))
        if c['kind'] == 'fact':
            sim, bank = target.start({'products': [c['product']]}, {'now': c['now']})
            bad = run_steps({'steps': target.defaults.get('verify') or []}, sim, bank, bank.products, bank.identity)
            state = json.loads(sim.vars.get('rt_state') or '{}')
            product = (state.get('products') or [{}])[0]
            level('compiled', bad + differences(c['expect'], product, str(sim.vars.get('rt_account_facts') or '')))
            played(sim)
        elif c['kind'] == 'calls':
            sim, bank = target.start(c['customer'])
            bad = run_steps(c, sim, bank, bank.products, bank.identity)
            bad += check.check_final(resolve(c['final'], bank.products), bank, sim)
            level('compiled', bad)
            played(sim)
        elif c['kind'] == 'prompt':
            # What the phone model is given at one point of the compiled call. Like `text` it is weak evidence:
            # it shows that an instruction reaches the model, not what the model does with it.
            sim, bank = target.start(c['customer'])
            level('spec', run_steps(c, sim, bank, bank.products, bank.identity))
            played(sim)
    except Exception as exc:
        name = 'compiled' if out['spec'] != 'na' or c['kind'] == 'calls' else 'spec'
        out[name] = 'fail'
        out['detail'].append('%s level: raised %s: %s' % (name, type(exc).__name__, exc))
    return out


# ---------------------------------------------------------------- one rule

def tally(results, mine, who, kinds, level):
    got = [results[c['id']][who][level] for c in mine if c['kind'] in kinds and results[c['id']][who][level] != 'na']
    return got.count('pass'), len(got)


def classify(rule, checks, results, fit, candidate):
    mine = [c for c in checks if rule['id'] in c['rules']]
    strong = [c for c in mine if c['kind'] in STRONG]
    weak = [c for c in mine if c['kind'] in WEAK]
    who = 'extracted' if candidate is not None else 'reference'
    holds = lambda c, side: all(results[c['id']][side][lv] in ('pass', 'na') for lv in ('spec', 'compiled'))
    count = lambda rows: '%d/%d' % (sum(holds(c, who) for c in rows), len(rows)) if rows else 'none'
    unconfirmed = rule.get('offline_evidence') == 'insufficient'
    out = {'id': rule['id'], 'category': rule.get('category'), 'critical': bool(rule.get('critical')), 'fit': fit,
           'checks': [c['id'] for c in mine], 'layer': None, 'reference': None, 'extraction': None, 'end_to_end': None,
           # Two kinds of evidence, never added up: what the brief prescribes, and what this builder's code stops.
           'policy_evidence': count([c for c in strong if asserts(c) == 'policy']),
           'enforcement_evidence': count([c for c in strong if asserts(c) == 'enforcement']),
           'weak_evidence': count(weak), 'weak_only': not strong and bool(weak), 'unconfirmed': unconfirmed,
           'note': ('not confirmed by behaviour offline: ' + str(rule.get('offline_evidence_note') or '')) if unconfirmed else ''}
    full = lambda pair: pair[0] == pair[1]
    ref_spec = tally(results, mine, 'reference', ('fact', 'structure'), 'spec')
    ref_comp = tally(results, mine, 'reference', ('fact', 'calls'), 'compiled')
    ref_text = tally(results, mine, 'reference', WEAK, 'spec')

    if rule.get('ambiguous'):
        out.update(layer='source', reference='not asserted', extraction='ambiguous source', end_to_end='not evaluated', note=str(rule['ambiguous']))
        return out
    if not strong and not weak:
        out.update(layer='conversational', reference='not evaluated', extraction='not evaluated', end_to_end='not evaluated offline')
        return out
    if fit == 'unsupported':
        passes = bool(strong) and full(ref_spec) and full(ref_comp)
        out.update(layer='schema', reference='passes: the fit table is out of date' if passes else 'cannot carry the rule', end_to_end='unsupported')
        if candidate is not None:
            anchors = [norm(a) for a in rule.get('anchors') or []]
            declared = norm(' '.join(str(u) for u in candidate.spec.get('unsupported') or []))
            text = all_text(candidate.spec)
            if anchors and any(a in declared for a in anchors):
                how = 'declared'
            elif anchors and any(a in text for a in anchors):
                how = 'text only'
            else:
                how = 'silent' if anchors else 'not detectable (the key names no anchor)'
            out['extraction'] = 'unsupported by schema: ' + how
        return out

    schema_share = False
    if fit == 'partial':
        # The schema carries part of the rule. What the reference spec cannot do is the schema's share,
        # and an extraction is judged on the rest: no extraction can do better than the schema.
        passing = lambda c: all(results[c['id']]['reference'][lv] in ('pass', 'na') for lv in ('spec', 'compiled'))
        schema_share = any(not passing(c) for c in strong)
        mine = [c for c in mine if passing(c)]
        strong, weak = [c for c in mine if c['kind'] in STRONG], [c for c in mine if c['kind'] in WEAK]
        ref_spec = tally(results, mine, 'reference', ('fact', 'structure'), 'spec')
        ref_comp = tally(results, mine, 'reference', ('fact', 'calls'), 'compiled')
        ref_text = tally(results, mine, 'reference', WEAK, 'spec')
        if not strong and not weak:
            out.update(layer='schema', reference='carries none of what the checks ask', extraction='not evaluated' if candidate else None, end_to_end='partial')
            return out

    reference_ok = True
    if strong and not full(ref_spec):
        out.update(layer='key', reference='fails at spec level: the key or the reference spec is wrong', end_to_end='key defect')
        reference_ok = False
    elif strong and not full(ref_comp):
        out.update(layer='compiler/runtime', reference='encodes the rule, the compiled workflow does not behave so', end_to_end='failed')
        reference_ok = False
    elif not strong and not full(ref_text):
        out.update(layer='key', reference='the reference text lacks the prescribed words', end_to_end='key defect')
        reference_ok = False
    else:
        out['reference'] = 'passes' if strong else 'passes (text only, weak evidence)'
    if not strong or fit == 'text':
        ending = 'text only'
    elif fit == 'partial' or unconfirmed:
        ending = 'partial'
    else:
        # A limit that is in the spec and comes out of the compiled workflow is encoded. It is called enforced
        # only where a check also played a call that breaks it and the generated code stopped that call.
        ending = 'enforced' if any(asserts(c) == 'enforcement' for c in strong) else 'encoded, enforcement not played'
    if schema_share and reference_ok:
        out.update(layer='schema', reference='carries part of the rule')
        ending = 'partial'

    if candidate is None:
        if reference_ok:
            out['end_to_end'] = ending
        return out
    text = tally(results, mine, 'extracted', WEAK, 'spec')
    # An extraction is judged on the checks the reference spec passes: where the reference fails, an
    # earlier stage owns the failure and no extraction could have done better.
    reference_passes = lambda c: all(results[c['id']]['reference'][lv] in ('pass', 'na') for lv in ('spec', 'compiled'))
    mine_results = [(c, results[c['id']]['extracted']) for c in strong if reference_passes(c)]
    if strong and not mine_results:
        out['extraction'] = 'not evaluated'
        return out
    # The same fact read from the spec and from the compiled workflow: only a disagreement between the
    # two is the compiler's or the runtime's. Everything else the reference can do and this spec cannot
    # is a difference between the two specs.
    disagree = any(c['kind'] == 'fact' and r['spec'] == 'pass' and r['compiled'] == 'fail' for c, r in mine_results)
    if not strong:
        out['extraction'] = 'correct (text only, weak evidence)' if full(text) else 'missing'
    else:
        # The evidence for extraction: the spec itself where a check can read it, behaviour where it cannot.
        verdicts = [r['compiled'] if c['kind'] == 'calls' else r['spec'] for c, r in mine_results]
        passed, total = verdicts.count('pass'), len(verdicts)
        if passed == total:
            out['extraction'] = 'correct'
        elif passed:
            out['extraction'] = 'partial'
        else:
            anchors = [norm(a) for a in rule.get('anchors') or []]
            found = any(a in norm(json.dumps(candidate.spec)) for a in anchors)
            out['extraction'] = 'missing' if anchors and not found else 'incorrect'
    # A rule the schema carries, named under "unsupported" and not encoded: the extractor gave up on something it could do.
    listed = norm(' '.join(str(u) for u in candidate.spec.get('unsupported') or []))
    out['declared_unsupported'] = bool(listed) and any(norm(a) in listed for a in rule.get('anchors') or []) and not out['extraction'].startswith('correct')
    if not reference_ok:
        return out          # the earlier stage owns the rule; the extraction result is reported beside it
    if not out['extraction'].startswith('correct'):
        out.update(layer='extraction', end_to_end='failed')
    elif disagree:
        out.update(layer='compiler/runtime', end_to_end='failed', note='the spec computes the expected value, the compiled workflow does not')
    elif any(results[c['id']]['extracted']['contract'] for c in mine):
        out.update(layer='openapi request', end_to_end='failed')
    else:
        out['end_to_end'] = ending
    return out


# ---------------------------------------------------------------- one package

_REFERENCE = {}


def key_problems(pid, reference='v1'):
    """Everything wrong with a key itself, before anything is run."""
    documents, _ = packages.inputs(pid)
    k, bad, source = packages.key(pid, reference), [], norm(' \n '.join(text for _, text in documents))
    ids = [r['id'] for r in k['rules']]
    bad += ['%s: duplicate rule id' % i for i in set(ids) if ids.count(i) > 1]
    for rule in k['rules']:
        if norm(rule.get('evidence', '')) not in source or not rule.get('evidence'):
            bad.append('%s: the evidence is not a verbatim part of the input documents' % rule['id'])
        if rule['id'] not in k['fit'] or k['fit'][rule['id']] not in FITS:
            bad.append('%s: no valid entry in reference.fit.json' % rule['id'])
    seen = set()
    for c in k['checks']:
        if c['id'] in seen:
            bad.append('%s: duplicate check id' % c['id'])
        seen.add(c['id'])
        if c.get('kind') not in KINDS:
            bad.append('%s: unknown kind %r' % (c['id'], c.get('kind')))
        if c.get('kind') == 'calls' and c.get('asserts') not in ASSERTS:
            bad.append('%s: a calls check has to say whether it asserts policy or enforcement' % c['id'])
        bad += ['%s: refers to unknown rule %s' % (c['id'], r) for r in c.get('rules') or [] if r not in ids]
        if not c.get('rules'):
            bad.append('%s: linked to no rule' % c['id'])
    bad += ['%s: fit entry without a rule' % r for r in k['fit'] if r not in ids]
    return bad


def evaluate(pid, extracted=None, reference='v1'):
    """The report of one package: per check and per rule, for the reference spec and, if given, an extracted spec.
    `reference` names the reference spec and fit table to judge against; rules and checks are the frozen ones."""
    _, document = packages.inputs(pid)
    k = packages.key(pid, reference)
    version = reference
    if (pid, version) not in _REFERENCE:
        target = Target('reference', k['spec'], document, k['defaults'], k['bank'])
        _REFERENCE[(pid, version)] = (target, {c['id']: run_check(target, c) for c in k['checks']})
    reference, reference_results = _REFERENCE[(pid, version)]
    candidate = Target('extracted', extracted, document, k['defaults'], k['bank']) if extracted is not None else None
    results = {c['id']: {'reference': reference_results[c['id']], 'extracted': run_check(candidate, c) if candidate else None} for c in k['checks']}
    rules = [classify(rule, k['checks'], results, k['fit'].get(rule['id'], 'structured'), candidate) for rule in k['rules']]
    report = {'package': pid, 'tier': packages.tier(pid), 'version': packages.meta(pid)['version'], 'reference_version': version,
              'reference_problems': reference.problems, 'spec_problems': candidate.problems if candidate else None,
              'api_notes': contract.document_notes(reference.api),
              'static_contract': contract.check_tools((candidate or reference).workflow, reference.api) if (candidate or reference).workflow else None,
              'rules': rules, 'checks': [dict(id=c['id'], kind=c['kind'], asserts=asserts(c), rules=c['rules'], **results[c['id']]) for c in k['checks']]}
    report['summary'] = summary(report, 'extracted' if candidate else 'reference')
    return report


def summary(report, who):
    rules, checks = report['rules'], report['checks']
    count = lambda field, value: sum(str(r[field] or '').startswith(value) for r in rules)
    out = {'rules': len(rules), 'by_layer': {}, 'by_end_to_end': {}, 'by_category': {}}
    for r in rules:
        out['by_layer'][r['layer'] or 'none'] = out['by_layer'].get(r['layer'] or 'none', 0) + 1
        out['by_end_to_end'][r['end_to_end']] = out['by_end_to_end'].get(r['end_to_end'], 0) + 1
        row = out['by_category'].setdefault(r['category'] or '-', {})
        label = r['extraction'] if who == 'extracted' else r['end_to_end']
        row[label] = row.get(label, 0) + 1
    ran = [c[who] for c in checks if c[who]]
    behaviour = [c['compiled'] for c in ran if c['compiled'] != 'na']
    out['behaviour'] = {'passed': behaviour.count('pass'), 'run': len(behaviour)}
    for kind in ASSERTS:
        part = [c[who]['compiled'] for c in checks if c[who] and c['asserts'] == kind and c[who]['compiled'] != 'na']
        out['behaviour'][kind] = {'passed': part.count('pass'), 'run': len(part)}
    verdict = lambda r: str(r['extraction'] if who == 'extracted' else r['end_to_end'])
    out['critical'] = {'rules': sum(r['critical'] for r in rules),
                       'not_evaluated': [r['id'] for r in rules if r['critical'] and verdict(r).startswith('not evaluated')],
                       'weak_evidence_only': [r['id'] for r in rules if r['critical'] and r['weak_only']],
                       'not_confirmed_by_behaviour': [r['id'] for r in rules if r['critical'] and r['unconfirmed']]}
    out['refusal_channel'] = sorted(c['id'] for c in checks if c[who] and c[who]['channel'])
    out['contract'] = {'requests': sum(c['requests'] for c in ran), 'blocked_by_policy': sum(c['blocked'] for c in ran),
                       'problems': sorted({p for c in ran for p in c['contract']})}
    if who == 'extracted':
        judged = [r for r in rules if str(r['extraction']).split(' (')[0] in ('correct', 'partial', 'incorrect', 'missing')]
        critical = [r for r in judged if r['critical']]
        unsupported = [r for r in rules if r['fit'] == 'unsupported']
        right = lambda rows: sum(r['extraction'].startswith('correct') for r in rows)
        out['extraction'] = {
            'judged': len(judged), 'correct': right(judged), 'partial': count('extraction', 'partial'),
            'incorrect': count('extraction', 'incorrect'), 'missing': count('extraction', 'missing'),
            'accuracy': round(right(judged) / len(judged), 3) if judged else None,
            'critical_judged': len(critical), 'critical_recall': round(right(critical) / len(critical), 3) if critical else None,
            'incorrect_policy_rate': round(count('extraction', 'incorrect') / len(judged), 3) if judged else None,
            'unsupported_rules': len(unsupported), 'unsupported_declared': sum(str(r['extraction']).endswith('declared') for r in unsupported),
            'valid': not report['spec_problems']}
    return out


# ---------------------------------------------------------------- is the evaluator sensitive?

def mutate(spec, ops):
    """A copy of `spec` with JSON-pointer edits applied: {"path": "/a/0/b", "set": value} or {"path": ..., "remove": true}."""
    spec = copy.deepcopy(spec)
    for op in ops:
        parts = [p for p in op['path'].split('/') if p != '']
        node = spec
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        last = int(parts[-1]) if isinstance(node, list) else parts[-1]
        if op.get('remove'):
            del node[last]
        else:
            node[last] = op['set']
    return spec


def mutation_baseline(pid, reference='v1'):
    """Each mutation of the reference spec must turn its own rules from correct to something else.

    A check that plays a whole call can speak for several rules, so a mutation may also flag a rule
    that shares a failed check with one of its own. Those are listed as coupled. A rule flagged
    without such a shared check, or an expected rule that is not flagged, makes the row wrong."""
    k = packages.key(pid, reference)
    good = lambda report: {r['id'] for r in report['rules'] if str(r['extraction']).startswith('correct')}
    base, rows = good(evaluate(pid, k['spec'], reference)), []
    for m in k['mutations']:
        report = evaluate(pid, mutate(k['spec'], m['ops']), reference)
        hit, want = base - good(report), set(m['rules'])
        failed = [set(c['rules']) for c in report['checks'] if 'fail' in (c['extracted']['spec'], c['extracted']['compiled'])]
        coupled = {r for r in hit - want if any(r in rules and rules & want for rules in failed)}
        rows.append({'id': m['id'], 'what': m.get('what'), 'expected': sorted(want), 'flagged': sorted(hit), 'coupled': sorted(coupled),
                     'ok': want <= hit and hit - want == coupled, 'layers': {r['id']: r['layer'] for r in report['rules'] if r['id'] in hit}})
    return rows


# ---------------------------------------------------------------- the evidence tiers

def exposures():
    if not EXPOSURE.exists():
        return []
    return [json.loads(line) for line in EXPOSURE.read_text(encoding='utf-8').splitlines() if line.strip()]


LABELS = {'selection': 'model-selection evidence', 'final': 'final holdout'}


def _log(entry):
    entry = dict({'at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}, **entry)
    EXPOSURE.parent.mkdir(parents=True, exist_ok=True)
    with EXPOSURE.open('a', encoding='utf-8') as out:
        out.write(json.dumps(entry) + '\n')
    return entry


def batch_state(pid, batch):
    """'none', 'open' (started, not every run done) or 'completed', from the log."""
    events = [e.get('event') for e in exposures() if e['package'] == pid and e.get('batch') == batch]
    return 'none' if not events else 'completed' if 'completed' in events else 'open'


def open_batch(pid, purpose, batch, candidates, relabel=None, resume=False):
    """Put a whole batch of frozen candidates on the record before any of it runs, or refuse.

    A selection or final package is opened once, by one batch. `batch` is the hash of the frozen candidates,
    so a batch can only be continued with exactly what it was started with. Continuing an unfinished batch
    needs `resume` and is logged; it runs what is missing and repeats nothing. Any other evaluation after
    the first is a new look at a package that was already seen and needs `relabel`, which is what its
    results are called from then on."""
    level = packages.tier(pid)
    if level == 'development':
        raise PermissionError('%s is a development package: it needs no batch' % pid)
    if not purpose:
        raise PermissionError('%s is a %s package: say what the evaluation is for' % (pid, level))
    earlier, state = [e for e in exposures() if e['package'] == pid], batch_state(pid, batch)
    if resume:
        if state != 'open':
            raise PermissionError('there is no unfinished batch %s on %s to continue: %s' % (
                batch, pid, 'it is completed' if state == 'completed' else 'no batch was started with these candidates'))
        label = next(e['label'] for e in earlier if e.get('batch') == batch and e.get('event') == 'started')
        return _log({'package': pid, 'tier': level, 'event': 'resumed', 'batch': batch, 'label': label, 'purpose': purpose})
    if state == 'open':
        raise PermissionError('batch %s on %s was started and not finished: continue it with resume; it is not run again from the start' % (batch, pid))
    if earlier and not relabel:
        raise PermissionError('%s was already evaluated (%d log entries): a further evaluation is post-exposure and has to be relabelled' % (pid, len(earlier)))
    return _log({'package': pid, 'tier': level, 'event': 'started', 'batch': batch, 'label': relabel or LABELS[level], 'purpose': purpose, 'candidates': candidates})


def close_batch(pid, batch, done, planned, note=''):
    """The end of one run of a batch: completed when every planned run has a result, otherwise left open."""
    return _log({'package': pid, 'tier': packages.tier(pid), 'event': 'completed' if done == planned else 'interrupted', 'batch': batch,
                 'runs_done': done, 'runs_planned': planned, 'note': note})


def expose(pid, purpose, config, relabel=None, frozen_candidates=None):
    """Record that a package of the selection or final tier is about to be evaluated on one extracted spec,
    or refuse. Inside an open batch (config names it) nothing more is logged: the batch is the record.
    Outside a batch the first evaluation of a final package needs the frozen candidates, and every
    evaluation of a package that was already seen needs `relabel`."""
    level = packages.tier(pid)
    if level == 'development':
        return None
    if not purpose:
        raise PermissionError('%s is a %s package: say what the evaluation is for' % (pid, level))
    if (config or {}).get('batch'):
        if batch_state(pid, config['batch']) != 'open':
            raise PermissionError('%s: batch %s is not open' % (pid, config['batch']))
        return None
    earlier = [e for e in exposures() if e['package'] == pid]
    if level == 'final' and not frozen_candidates and not relabel:
        raise PermissionError('%s is the final holdout: freeze the candidates first' % pid)
    if earlier and not relabel:
        raise PermissionError('%s was already evaluated (%d log entries): a further evaluation is post-exposure and has to be relabelled' % (pid, len(earlier)))
    return _log({'package': pid, 'tier': level, 'event': 'single', 'label': relabel or LABELS[level], 'purpose': purpose, 'config': config})


# ---------------------------------------------------------------- reports

def report_text(reports, mutations=None):
    out = ['# Rule-level evaluation', '', 'Run at %s, offline. All packages are synthetic and author-prepared, not independently validated.' % time.strftime('%Y-%m-%d %H:%M:%S'), '']
    for rep in reports:
        who = 'extracted' if rep['spec_problems'] is not None else 'reference'
        s = rep['summary']
        out += ['## Package %s (%s, v%s, reference %s): %s spec' % (rep['package'], rep['tier'], rep['version'], rep.get('reference_version', 'v1'), who), '']
        if rep['reference_problems'] or rep['spec_problems']:
            out += ['Not valid: ' + '; '.join(rep['spec_problems'] or rep['reference_problems'])[:600], '']
        out += ['| | |', '| --- | --- |', '| Rules | %d |' % s['rules'],
                '| First failing layer | %s |' % ', '.join('%s %d' % kv for kv in sorted(s['by_layer'].items())),
                '| End to end | %s |' % ', '.join('%s %d' % kv for kv in sorted(s['by_end_to_end'].items(), key=lambda kv: str(kv[0]))),
                '| Checks through the compiled workflow | %d of %d pass |' % (s['behaviour']['passed'], s['behaviour']['run']),
                '| of these: policy (prescribed values, records after compliant calls) | %d of %d pass |' % (s['behaviour']['policy']['passed'], s['behaviour']['policy']['run']),
                '| of these: enforcement (a call no compliant agent makes is stopped or corrected) | %d of %d pass |' % (s['behaviour']['enforcement']['passed'], s['behaviour']['enforcement']['run']),
                '| Critical rules | %d; not evaluated offline: %s; weak evidence only: %s; marked not confirmed by behaviour: %s |' % (
                    s['critical']['rules'], ', '.join(s['critical']['not_evaluated']) or 'none', ', '.join(s['critical']['weak_evidence_only']) or 'none',
                    ', '.join(s['critical']['not_confirmed_by_behaviour']) or 'none'),
                '| Requests checked against the tool API | %d sent, %d blocked by policy, %d kind(s) of problem |' % (
                    s['contract']['requests'], s['contract']['blocked_by_policy'], len(s['contract']['problems']))]
        if s['refusal_channel']:
            out.append('| Checks that expected a refusal as the tool result and found it in the live state (blocked, nothing at the bank) | %d: %s |' % (
                len(s['refusal_channel']), ', '.join(s['refusal_channel'])))
        if 'extraction' in s:
            e = s['extraction']
            out += ['| Extraction, rules judged | %d: %d correct, %d partial, %d incorrect, %d missing |' % (e['judged'], e['correct'], e['partial'], e['incorrect'], e['missing']),
                    '| Accuracy / critical-rule recall / incorrect-policy rate | %s / %s / %s |' % (e['accuracy'], e['critical_recall'], e['incorrect_policy_rate']),
                    '| Unsupported rules declared by the extractor | %d of %d |' % (e['unsupported_declared'], e['unsupported_rules'])]
        out += ['', 'Policy, enforcement and weak evidence are counted per rule as checks passed of checks linked, for the %s spec. They are different kinds of evidence and are not added up.' % who, '',
                '| Rule | Category | Critical | Schema fit | Reference | Extraction | Policy | Enforcement | Weak | First failing layer | End to end | Note |',
                '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for r in rep['rules']:
            out.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                r['id'], r['category'], 'yes' if r['critical'] else '', r['fit'], r['reference'], r['extraction'] or '-', r['policy_evidence'],
                r['enforcement_evidence'], r['weak_evidence'], r['layer'] or '-', r['end_to_end'], r['note']))
        failed = [c for c in rep['checks'] if (c[who] or {}).get('detail')]
        if failed:
            out += ['', 'Failed checks:', '']
            out += ['- %s (%s; %s): %s' % (c['id'], c['kind'], ', '.join(c['rules']), ' / '.join(c[who]['detail'])[:500]) for c in failed]
        for line in s['contract']['problems'] + (rep['static_contract'] or []):
            out.append('- tool API contract: ' + line)
        for line in rep['api_notes']:
            out.append('- tool API document: ' + line)
        out.append('')
    if mutations:
        out += ['## Mutated reference specs', '',
                'Each mutation changes one thing in a reference spec. The evaluator has to flag the rules that thing belongs to. '
                'A rule that shares a failed whole-call check with one of them is flagged too and is listed as coupled.', '',
                '| Package | Mutation | Expected | Also flagged (coupled) | Result |', '| --- | --- | --- | --- | --- |']
        for pid, rows in mutations.items():
            out += ['| %s | %s | %s | %s | %s |' % (pid, m['what'] or m['id'], ', '.join(m['expected']), ', '.join(m['coupled']) or '-',
                                                 'ok' if m['ok'] else 'WRONG: flagged ' + (', '.join(m['flagged']) or 'nothing')) for m in rows]
        out.append('')
    return '\n'.join(out) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Rule-level evaluation of a spec against a frozen package key. Offline.')
    ap.add_argument('--package', action='append', help='package id; default: all')
    ap.add_argument('--spec', help='an extracted spec to evaluate; needs exactly one --package')
    ap.add_argument('--purpose', help='why a selection or final package is evaluated; written to the exposure log')
    ap.add_argument('--config', default='{}', help='JSON naming the configuration (prompt hash, strategy, commit), for the exposure log')
    ap.add_argument('--frozen-candidates', help='file listing the frozen candidates; required for the final package')
    ap.add_argument('--relabel', help='evaluate a final package again and call its results this from now on')
    ap.add_argument('--mutations', action='store_true', help='also evaluate the mutated reference specs')
    ap.add_argument('--out', help='write <out>.json and <out>.md')
    ap.add_argument('--skip-verify', action='store_true', help='do not compare the packages with the manifest (before the first freeze)')
    ap.add_argument('--reference', default='v1', help="'v1' (frozen with the package), a later version such as 'v2', or 'latest'")
    args = ap.parse_args(argv)
    chosen = args.package or packages.ids()
    version = {pid: packages.references(pid)[-1] if args.reference == 'latest' else args.reference for pid in chosen}
    problems = [line for pid in chosen for line in ['%s: %s' % (pid, p) for p in key_problems(pid, version[pid])]]
    if not args.skip_verify:
        problems += [line for pid in chosen for line in packages.verify(pid)] + packages.verify_references()
    if problems:
        print('The packages cannot be evaluated:')
        for line in problems:
            print('  - ' + line)
        return 2
    extracted = None
    if args.spec:
        if len(chosen) != 1:
            ap.error('--spec needs exactly one --package')
        expose(chosen[0], args.purpose, json.loads(args.config), args.relabel, args.frozen_candidates)
        extracted = json.loads(Path(args.spec).read_text(encoding='utf-8'))
    reports = [evaluate(pid, extracted, version[pid]) for pid in chosen]
    mutations = {pid: mutation_baseline(pid, version[pid]) for pid in chosen} if args.mutations else None
    text = report_text(reports, mutations)
    for rep in reports:
        s = rep['summary']
        print('%s %-11s rules %2d | layers %s | behaviour %d/%d | %s' % (
            rep['package'], rep['tier'], s['rules'], ' '.join('%s=%d' % kv for kv in sorted(s['by_layer'].items())),
            s['behaviour']['passed'], s['behaviour']['run'], ('extraction ' + json.dumps(s['extraction'])) if 'extraction' in s else 'reference'))
    wrong = [m for rows in (mutations or {}).values() for m in rows if not m['ok']]
    if mutations:
        print('mutations: %d, attributed wrongly: %d' % (sum(len(r) for r in mutations.values()), len(wrong)))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out + '.json').write_text(json.dumps({'reports': reports, 'mutations': mutations}, indent=1, default=str), encoding='utf-8')
        Path(args.out + '.md').write_text(text, encoding='utf-8')
        print('report: %s.md' % args.out)
    defects = [r for rep in reports for r in rep['rules'] if r['layer'] == 'key']
    return 1 if defects or wrong else 0


if __name__ == '__main__':
    sys.exit(main())
