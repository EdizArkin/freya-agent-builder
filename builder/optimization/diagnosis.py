"""Which failed case the next candidate is for, and which failures no candidate can fix.

A candidate changes words the phone model reads (candidate.py). It cannot change what the runtime enforces, the
compiler, the tool API or the platform. So every failed case is filed first, from its own records:

    not addressable by a candidate
      no_result       the case has no complete result: there is nothing to compare; run it again
      no_transcript   the call cannot be read: there is nothing to diagnose from
      enforcement     the bank holds a record that a deterministic check of the runtime must refuse: a defect
                      of the builder's code. It is reported, never patched by a model
      tool_failure    a tool call failed that the policy check had not refused: the runtime, the tool API or
                      the service
    addressable by a change of words
      bank_forbidden  the bank holds a record the scenario forbids, and no deterministic check is named for it
      bank_missing    the bank lacks a record the scenario expects
      conversation    the bank is right and the judge found a check false in what was said

The next case is the worst addressable one: wrong bank records before a missing record before a judge's
finding, a safety case before another case of the same kind, then by id. A case that already had its
candidates for this incumbent (policy.ATTEMPTS_PER_CASE), or for which the proposing model found the
instructions not to be the cause, is not chosen again.

The filing is a first reading of the records, like the harness's `suggest`: it decides what is tried, not what
is accepted. Acceptance is policy.decide alone.
"""
from builder.optimization import policy

ADDRESSABLE = ('bank_forbidden', 'bank_missing', 'conversation')
NEEDS_CODE = ('enforcement', 'tool_failure')
WHY = {
    'no_result': 'no complete result; run the case again',
    'no_transcript': 'the call has no transcript to diagnose from',
    'enforcement': 'the bank holds a record that the runtime check "%s" must refuse: a defect of the builder code, not of the wording',
    'tool_failure': 'the tool call %s failed although the policy check had not refused it: the runtime, the tool API or the service, not the wording',
    'bank_forbidden': 'the bank holds a record the scenario forbids',
    'bank_missing': 'the bank lacks a record the scenario expects',
    'conversation': 'the bank records are right; the judge found a check false in what was said',
}


def classify(row):
    """(kind, why) of one failed case."""
    if not policy.complete(row):
        return 'no_result', WHY['no_result']
    if not [m for m in row.get('messages') or [] if m.get('role') in ('assistant', 'user')]:
        return 'no_transcript', WHY['no_transcript']
    if row['unexpected_records'] and row.get('gate'):
        return 'enforcement', WHY['enforcement'] % row['gate']
    broken = [t.get('tool') for t in row.get('tool_trace') or [] if t.get('ended') == 'error' and not t.get('blocked')]
    if broken:
        return 'tool_failure', WHY['tool_failure'] % ', '.join(str(t) for t in broken)
    if row['unexpected_records']:
        return 'bank_forbidden', WHY['bank_forbidden']
    if row['ledger_pass'] is False:
        return 'bank_missing', WHY['bank_missing']
    return 'conversation', WHY['conversation']


def plan(rows, safety_cases=(), tried=None, declined=()):
    """What to do about the failed cases of an incumbent.

    rows       its results per case
    tried      {case id: candidates that already reached a verdict or were refused for it, on this incumbent}
    declined   cases for which the proposing model found the instructions not to be the cause
    Returns {'failing': [...], 'next': the case to propose for or None, 'addressable': [...], 'needs_code_change': [...],
    'not_diagnosable': [...], 'given_up': [...]}; every entry is {'id', 'kind', 'why'}."""
    tried = tried or {}
    out = {'failing': [], 'next': None, 'addressable': [], 'needs_code_change': [], 'not_diagnosable': [], 'given_up': []}
    for cid, row in sorted(rows.items()):
        if policy.complete(row) and row['pass']:
            continue
        kind, why = classify(row)
        entry = {'id': cid, 'kind': kind, 'why': why}
        out['failing'].append(cid)
        if kind in NEEDS_CODE:
            out['needs_code_change'].append(entry)
        elif kind not in ADDRESSABLE:
            out['not_diagnosable'].append(entry)
        elif cid in declined:
            out['given_up'].append(dict(entry, why='the proposing model found that the instructions are not the cause'))
        elif tried.get(cid, 0) >= policy.ATTEMPTS_PER_CASE:
            out['given_up'].append(dict(entry, why='%d candidate(s) for this case did not replace the incumbent' % tried[cid]))
        else:
            out['addressable'].append(entry)
    out['addressable'].sort(key=lambda e: (ADDRESSABLE.index(e['kind']), e['id'] not in safety_cases, e['id']))
    if out['addressable']:
        out['next'] = dict(rows[out['addressable'][0]['id']], **out['addressable'][0])
    return out
