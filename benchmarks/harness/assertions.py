"""What a scenario may assert and how each assertion is checked.

Every check returns a list of plain sentences, empty when the check holds.

How one tool call ended, as seen from the bank and from the result the model would get:

  ok           the bank carried the call out
  refused      the runtime blocked the call and the reason is the tool result
  detached     the runtime blocked the call by detaching it from the customer; the bank
               never saw this customer and the model gets a bare error
  blocked      refused or detached, when the scenario does not care which
  error        the service failed with an HTTP error; no hook ran after it
  bank_refused the bank itself answered ok false (for example after the call was closed)
  unavailable  the tool does not exist in the current part of the call
"""
import json

OUTCOMES = ('ok', 'refused', 'detached', 'blocked', 'error', 'bank_refused', 'unavailable')
LEDGER_KEYS = ('outcome', 'reason', 'promises', 'notes', 'callbacks', 'transfers')


def call_log(bank):
    """The calls that reached the bank for this customer, in order: tool, tool!500 or tool!refused."""
    log = []
    for tool, _, answer in bank.calls:
        if answer == 500:
            log.append(tool + '!500')
        elif not isinstance(answer, dict) or answer.get('ok') is not True:
            log.append(tool + '!refused')
        else:
            log.append(tool)
    return log


def classify(tool, data, new_calls):
    mine = [c for c in new_calls if c[0] == tool]
    if not mine:
        return 'detached'
    answer = mine[-1][2]
    if answer == 500:
        return 'error'
    if data.get('rejected_by_policy'):
        return 'refused'
    if isinstance(answer, dict) and answer.get('ok') is True:
        return 'ok'
    return 'bank_refused'


def check_outcome(label, got, want):
    if want is None or got == want or (want == 'blocked' and got in ('refused', 'detached')):
        return []
    return ['%s: expected %s, got %s' % (label, want, got)]


def check_text(label, got, data, live, has, lacks):
    """Text the model is given: the tool result when there is one, the live state otherwise."""
    text = str(data.get('what_to_do_next') or '') if got in ('ok', 'refused', 'bank_refused') else live
    where = 'tool result' if got in ('ok', 'refused', 'bank_refused') else 'live state'
    bad = ['%s: %s lacks %r (it says: %s)' % (label, where, part, text[:240]) for part in has or [] if part not in text]
    bad += ['%s: %s must not contain %r' % (label, where, part) for part in lacks or [] if part in text]
    return bad


def check_state(label, sim, want):
    bad = []
    try:
        state = json.loads(sim.vars.get('rt_state') or '{}')
    except ValueError:
        state = {}
    for path, value in (want or {}).items():
        node = state
        for part in path.split('.'):
            node = node.get(part) if isinstance(node, dict) else None
        if node != value:
            bad.append('%s: state %s is %r, expected %r' % (label, path, node, value))
    return bad


def check_view(label, sim, check):
    """A look at what the model has in front of it at this point of the call."""
    bad = []
    node = sim.wf['nodes'][sim.node]
    if 'node' in check and sim.node != check['node']:
        bad.append('%s: the call is in %s, expected %s' % (label, sim.node, check['node']))
    if 'tools' in check:
        tools = [t['function']['name'] for t in node.get('tools') or []]
        if tools != check['tools']:
            bad.append('%s: tools here are %s, expected %s' % (label, tools, check['tools']))
    prompt = sim.prompt()
    bad += ['%s: prompt lacks %r' % (label, part) for part in check.get('prompt_has') or [] if part not in prompt]
    bad += ['%s: prompt must not contain %r' % (label, part) for part in check.get('prompt_lacks') or [] if part in prompt]
    system = sim.wf.get('system_prompt', '')
    bad += ['%s: system prompt lacks %r' % (label, part) for part in check.get('system_has') or [] if part not in system]
    live = str(sim.vars.get('rt_live') or '')
    bad += ['%s: live state lacks %r' % (label, part) for part in check.get('live_has') or [] if part not in live]
    return bad + check_state(label, sim, check.get('state'))


def ledger_view(ledger):
    """The bank's records in a form that does not depend on order or number formatting."""
    return {
        'outcome': ledger.get('outcome'), 'reason': ledger.get('reason'),
        'promises': sorted((p['product_id'], round(float(p['amount']), 2), p['date']) for p in ledger.get('promises') or []),
        'notes': sorted((n['product_id'], n['reason']) for n in ledger.get('notes') or []),
        'callbacks': sorted((c['date'], c['time']) for c in ledger.get('callbacks') or []),
        'transfers': [t['queue'] for t in ledger.get('transfers') or []],
    }


def check_final(expected, bank, sim):
    """The assertions of expected/ledger_assertions.yaml for one scenario."""
    bad = []
    want = ledger_view(expected.get('ledger') or {})
    got = ledger_view(bank.ledger)
    for key in LEDGER_KEYS:
        if want[key] != got[key]:
            bad.append('ledger %s is %r, expected %r' % (key, got[key], want[key]))
    log = call_log(bank)
    if 'bank_calls' in expected and log != expected['bank_calls']:
        bad.append('bank calls were %s, expected %s' % (log, expected['bank_calls']))
    if 'permitted_calls' in expected:
        for entry in log:
            if entry.split('!')[0] not in expected['permitted_calls']:
                bad.append('%s reached the bank but is not permitted in this scenario' % entry)
    for tool in expected.get('forbidden_calls') or []:
        if tool in log:
            bad.append('%s was carried out by the bank but is forbidden in this scenario' % tool)
    if 'ended' in expected and (sim.node == 'end') != expected['ended']:
        bad.append('the call %s, expected it %s' % ('ended' if sim.node == 'end' else 'is still open', 'ended' if expected['ended'] else 'open'))
    if expected.get('ended') and (not log or log[-1] != 'close_call'):
        bad.append('the last call at the bank is %s, expected close_call' % (log[-1] if log else 'none'))
    return bad
