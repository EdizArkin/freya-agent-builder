"""Real phone-agent evaluation: customers played on Freya against a deployed agent.

This starts simulations on the platform and reads the bank mock's ledger. It is not the offline test suite
and not the offline engine stand-in used by the tests; a pass in one says nothing about the other.

Every simulation the builder starts is started by `run` and named by `simulation_name`, so the list in the
Freya UI says what each one tested. The platform calls go through the kit's own modules (`runner` is the
kit's tools/run_cases.py, loaded by builder.freya.client.load_runner); nothing here opens a connection itself.

A simulation is a text chat. What it shows about speech is what the transcript shows: `speech_findings`.
It cannot show audio: a sentence cut off by the speech engine, or by a person talking over the agent, leaves
a complete sentence in the transcript.
"""
import hashlib
import json
import re
import time
from pathlib import Path

NAME_LIMIT = 80        # the API states no limit for a simulation name; this one is ours, and short enough for the list in the UI
SEPARATOR = ' | '


def _part(text):
    """One part of a name: plain letters, digits and a few signs, never the separator."""
    text = re.sub(r'[^A-Za-z0-9 .:_/()+,-]+', ' ', str(text))
    return re.sub(r'\s+', ' ', text).strip() or '-'


def simulation_name(run, brief, scenario, expected, version):
    """'M17 | A | Happy Path | Success | v1': which run, which brief, what is tested, what must happen, which
    version of the workflow. The scenario is shortened when the name would pass NAME_LIMIT; the other parts,
    which carry the traceability, are never cut."""
    run, brief, scenario, expected, version = (_part(p) for p in (run, brief, scenario, expected, version))
    room = NAME_LIMIT - len(SEPARATOR.join((run, brief, '', expected, version)))
    if len(scenario) > room:
        scenario = scenario[:max(1, room)].rstrip(' .,:-_/(+')
    return SEPARATOR.join((run, brief, scenario, expected, version))


def suite_name(run, brief, suite, version):
    """'M17 | A | Regression | v1': the name of a simulation that holds several customers, one persona each. Which
    customer did what is in the persona names (the id and title of each case), not in the name of the simulation."""
    return SEPARATOR.join(_part(p) for p in (run, brief, suite, version))[:NAME_LIMIT]


class SharedCustomer(RuntimeError):
    """Two cases of one simulation would write to the same bank customer."""


def expected_label(case):
    """The expected result of a case in two or three words, from the bank records it must leave."""
    want = case.get('expected') or {}
    if case.get('expected_label'):
        return case['expected_label']
    outcome = str(want.get('outcome') or 'unknown').replace('_', ' ').title()
    reason = want.get('reason')
    return outcome + (' ' + str(reason).replace('_', ' ').title() if reason else '')


def personas(runner, cases, cache, style=None):
    """Persona ids for the cases, creating on Freya only those whose text is new. `cache` is a JSON file."""
    cache = Path(cache)
    known = json.loads(cache.read_text(encoding='utf-8')) if cache.is_file() else {}
    texts = {c['id']: runner.persona_for(c, {'identity': c['identity']}, style) for c in cases}
    key = {i: i + ':' + hashlib.sha1(json.dumps(p, sort_keys=True).encode('utf-8')).hexdigest()[:10] for i, p in texts.items()}
    new = [c for c in cases if key[c['id']] not in known]
    if new:
        made = runner.freya.create_personas([texts[c['id']] for c in new])['personas']
        known.update({key[c['id']]: p['id'] for c, p in zip(new, made)})
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, indent=1), encoding='utf-8')
    return {c['id']: known[key[c['id']]] for c in cases}


def events(messages):
    """The engine events of a call, in order: (seconds, event name, content)."""
    out = []
    for m in messages or []:
        if m.get('role') != 'system':
            continue
        try:
            event = json.loads(m.get('message') or '')
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append((m.get('secondsFromStart'), str(event.get('event')), event))
    return out


def tool_trace(messages, call_fields=('customer_id',)):
    """Every tool call of a call as the engine sent it: the tool, the request body, and how it ended.

    `blocked` is true when the request went out detached from the call: the runtime's policy check refused
    it and replaced the call's own fields, so the bank could not record anything for this customer."""
    out, open_calls = [], {}
    for seconds, name, event in events(messages):
        tool = event.get('function')
        if name == 'function_call_start' and tool not in ('code_exec', 'end_call'):
            args = event.get('args') if isinstance(event.get('args'), dict) else {}
            body = args.get('body') if isinstance(args.get('body'), dict) else {}
            row = {'tool': tool, 'at': seconds, 'body': body, 'result': None, 'ended': 'no result',
                   'blocked': any(body.get(f) == 'blocked_by_policy' for f in call_fields)}
            out.append(row)
            open_calls.setdefault(tool, []).append(row)
        elif name in ('function_call_complete', 'function_call_error') and open_calls.get(tool):
            row = open_calls[tool].pop(0)
            row['ended'] = 'error' if name == 'function_call_error' else 'answered'
            row['result'] = event.get('result') if name == 'function_call_complete' else (event.get('error') or event.get('message') or event.get('result'))
            row['seconds'] = round(float(seconds or 0) - float(row['at'] or 0), 2)
    return out


TERMINAL = re.compile(r'[.?!]["\')\]]?$')
TOOL_TEXT = re.compile(r'\b(?:verify_identity|get_account|create_promise|add_note|schedule_callback|transfer_to_agent|close_call)\s*\(|what_to_do_next|^\s*[{\[]')
FAREWELL = re.compile(r'\b(goodbye|good bye|bye|have a (?:good|nice|great) (?:day|evening|afternoon|one))\b', re.I)
LONG_TURN_WORDS = 45


def speech_findings(messages):
    """What the transcript of one call shows about how the agent spoke.

    cut_off: an agent turn that stops without ending its sentence. long: a turn of more than LONG_TURN_WORDS
    words, which on a voice call invites the person to talk over it. repeated: the same turn twice in a row.
    tool_text: a tool call or a tool result written out as speech. no_farewell: the call ended and the last
    thing the agent said was not a goodbye. silent_after_tool: a tool result after which the agent said
    nothing before the person spoke again or the call ended. Engine errors of the call are listed as well.

    customer_cut_off is about the other side: a turn of the simulated customer that stops in the middle of a
    sentence. The agent did not write it and cannot prevent it; it is counted because a cut-off sentence in a
    transcript is otherwise taken for the agent's, and because a customer turn that loses its end can lose the
    very request a scenario is about."""
    spoken = [(m.get('secondsFromStart'), str(m.get('message') or '').strip()) for m in messages or [] if m.get('role') == 'assistant']
    heard = [str(m.get('message') or '').strip() for m in messages or [] if m.get('role') == 'user' and str(m.get('message') or '').strip()]
    found = {'agent_turns': len(spoken), 'words': sum(len(t.split()) for _, t in spoken), 'longest_turn_words': max([len(t.split()) for _, t in spoken] or [0]),
             'cut_off': [], 'long': [], 'repeated': [], 'tool_text': [], 'empty': 0, 'no_farewell': False, 'silent_after_tool': 0,
             'customer_turns': len(heard), 'customer_cut_off': [t[-120:] for t in heard if not TERMINAL.search(t)],
             'engine_errors': [e.get('message') or e.get('details') or 'error' for _, name, e in events(messages) if name == 'error']}
    previous = None
    for _, text in spoken:
        if not text:
            found['empty'] += 1
            continue
        if TOOL_TEXT.search(text):
            found['tool_text'].append(text[-120:])
        elif not TERMINAL.search(text):
            found['cut_off'].append(text[-120:])
        if len(text.split()) > LONG_TURN_WORDS:
            found['long'].append(text[:120])
        if previous is not None and text == previous:
            found['repeated'].append(text[:120])
        previous = text
    if spoken and not any(FAREWELL.search(t) for _, t in spoken[-2:]):
        found['no_farewell'] = True
    # A tool result has to be followed by speech or by another tool call before the person speaks again.
    waiting = False
    for m in messages or []:
        role = m.get('role')
        if role == 'system':
            try:
                event = json.loads(m.get('message') or '')
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('event') in ('function_call_complete', 'function_call_error') and event.get('function') not in ('code_exec', 'end_call', 'api_call', 'close_call'):
                waiting = True
            elif event.get('event') == 'function_call_start':
                waiting = False
        elif role == 'assistant' and str(m.get('message') or '').strip():
            waiting = False
        elif role == 'user' and waiting:
            found['silent_after_tool'] += 1
            waiting = False
    return found


def slowest_turn(messages):
    """The longest time the person waited for the agent to speak after speaking, in seconds."""
    last_user, worst = None, 0.0
    for m in messages or []:
        at = m.get('secondsFromStart')
        if at is None:
            continue
        if m.get('role') == 'user':
            last_user = at
        elif m.get('role') == 'assistant' and last_user is not None:
            worst, last_user = max(worst, at - last_user), None
    return round(worst, 1)


def read_call(runner, call_id, trace=lambda line: None, tries=3, wait=3.0):
    """The call with its transcript, or {} when it cannot be read. The verdict of a case stands without the
    transcript; a row without one says so by having no messages, and can be read again later."""
    for attempt in range(tries if call_id else 0):
        try:
            return runner.freya.get_call(call_id)
        except Exception as exc:
            trace('simulation: call %s could not be read (try %d of %d): %s' % (call_id, attempt + 1, tries, str(exc)[:200]))
            if attempt + 1 < tries:
                time.sleep(wait)
    return {}


def spoken(messages):
    """What is worked out from a transcript, in one place: for the row of a run and for a transcript read later."""
    return {'slowest_turn_s': slowest_turn(messages), 'speech': speech_findings(messages), 'tool_trace': tool_trace(messages), 'messages': messages}


def run(runner, agent_id, cases, name, persona_cache, trace=lambda line: None, style=None, max_seconds=420):
    """One simulation on Freya under the name `name`: every case once. Returns the record of the simulation.

    A case is in the kit's format (id, brief, title, variables, identity, script, expected, checks). Its bank
    account is that of the practice case `bank_case` (default: its own id): the bank opens a session only for
    a practice case. A case passes when the bank's ledger holds exactly the expected records and the platform's
    judge finds every check true in the transcript: the kit's rule, with the kit's scoring."""
    ids = personas(runner, cases, persona_cache, style)
    sessions = {c['id']: runner.http('/v1/practice/sessions', {'case': c.get('bank_case') or c['id']})['variables'] for c in cases}
    variables = {c['id']: dict(sessions[c['id']], **(c.get('override_variables') or {})) for c in cases}
    # Every case has its own persona and its own bank session, so the customers of one simulation are judged and
    # scored apart. That holds only while no two of them share a persona or a bank customer: checked, not assumed.
    customers = [sessions[c['id']]['customer_id'] for c in cases]
    if len(set(customers)) != len(cases) or len(set(ids.values())) != len(cases):
        raise SharedCustomer('two cases of "%s" share a bank customer or a persona; their records could not be told apart' % name)
    trace('simulation: starting "%s" | agent %s | %d case(s): %s' % (name, agent_id, len(cases), ', '.join(c['id'] for c in cases)))
    started, at = time.time(), time.strftime('%Y-%m-%dT%H:%M:%S%z')
    sim_id, iteration_id, status = runner.freya.run_simulation(agent_id, name, {ids[c['id']]: variables[c['id']] for c in cases}, max_seconds)
    seconds = round(time.time() - started)
    runs = {r['personaId']: r for r in runner.freya.get_iteration(sim_id, iteration_id)['runs']}
    rows = []
    for c in cases:
        customer = sessions[c['id']]['customer_id']
        ledger = runner.http('/v1/practice/ledger/' + customer)
        scored = runner.score_ledger(c, ledger)
        judged = runs.get(ids[c['id']], {})
        failed = list(scored['failed'])
        if judged.get('outcome') != 'pass':
            failed.append('conversation: ' + ' '.join(str(judged.get('reason') or 'no verdict').split())[:400])
        call = read_call(runner, judged.get('callId'), trace)
        messages = call.get('messages') or []
        rows.append({'id': c['id'], 'bank_case': c.get('bank_case') or c['id'], 'brief': c.get('brief'), 'title': c.get('title'), 'pass': not failed, 'failed': failed,
                     'ledger_pass': scored['pass'], 'judge_outcome': judged.get('outcome'), 'judge_reason': judged.get('reason'),
                     'call_id': judged.get('callId'), 'run_id': judged.get('id'), 'agent_version': judged.get('agentVersionNumber'),
                     'customer_id': customer, 'tools': [x['tool'] for x in ledger.get('calls') or []], 'ledger': ledger, 'expected': c.get('expected'),
                     'ended': call.get('endedReason'), 'duration': call.get('duration'), 'turns': judged.get('turnCount')})
        rows[-1].update(spoken(messages))
        trace('simulation: %s %s | %s%s' % ('PASS' if not failed else 'FAIL', c['id'], ' > '.join(rows[-1]['tools']) or '(no tool calls)',
                                            ''.join(' | ' + f[:200] for f in failed)))
    return {'simulation_id': sim_id, 'iteration_id': iteration_id, 'name': name, 'status': status, 'agent_id': agent_id, 'started_at': at,
            'seconds': seconds, 'passed': sum(r['pass'] for r in rows), 'total': len(rows), 'rows': rows}


def index_row(record, **about):
    """One line of the simulation index: which simulation, under which name, on which workflow, with which
    result. Transcripts and ledgers stay in the record; the index is what is looked up and compared."""
    return dict({'simulation_id': record['simulation_id'], 'iteration_id': record['iteration_id'], 'name': record['name'], 'status': record['status'],
                 'agent_id': record['agent_id'], 'started_at': record['started_at'], 'seconds': record['seconds'],
                 'passed': record['passed'], 'total': record['total'],
                 'scenarios': [{'id': r['id'], 'pass': r['pass'], 'failed': r['failed'], 'call_id': r['call_id'], 'agent_version': r['agent_version'],
                                'tools': r['tools'], 'slowest_turn_s': r['slowest_turn_s']} for r in record['rows']]}, **about)


def run_practice_cases(runner, agent_id, brief_letter, cases, results_path, trace, label, version):
    """The kit's practice customers of one brief as one named simulation. Writes the record; returns 0 when all pass."""
    chosen = [c for c in cases if c.get('brief') == brief_letter]
    if not chosen:
        trace('test: no practice case of brief %s' % brief_letter)
        return 1
    name = suite_name(label, brief_letter, 'Regression', version)
    record = run(runner, agent_id, chosen, name, Path(results_path).parent / 'personas.json', trace)
    Path(results_path).parent.mkdir(parents=True, exist_ok=True)
    Path(results_path).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding='utf-8')
    trace('test: "%s" | simulation %s | %d / %d passed | %d s' % (name, record['simulation_id'], record['passed'], record['total'], record['seconds']))
    return 0 if record['passed'] == record['total'] else 1
