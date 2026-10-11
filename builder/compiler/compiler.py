"""agent-spec/1 + tool API -> Freya workflow. Deterministic, offline, no platform calls."""
import ast
import copy
import json
import math
from pathlib import Path

from builder.runtime import runtime
from builder.spec.models import TOOLS, exprs

# The runtime module is embedded as source text, so it is located as a file, not used as an import.
RUNTIME_SOURCE = Path(runtime.__file__)


ROLES = {'verify_identity': 'identity', 'get_account': 'account', 'create_promise': 'promise', 'add_note': 'note',
         'schedule_callback': 'callback', 'transfer_to_agent': 'transfer', 'close_call': 'close'}


PLATFORM_RULES = """You are a phone agent on a live call with one person. Speak {language}. Keep every turn to one or two short spoken sentences. No lists, no markdown, no stage directions.

HOW YOU WORK
- Tool results come from the bank's own systems. Every tool result carries a field `what_to_do_next`. It is binding: do exactly what it says, even when you expected something else.
- After every tool result you must speak to the person or call the next tool. Never end a turn with nothing.
- When a rule says to use a tool, CALL the tool. Saying it in words is not enough.
- ONE tool call per message, never two. Tool calls sent together do not run in the order you wrote them. When two tools are needed, call the first, wait for its result, then call the second in a new message.
- Never say that something is recorded, booked, transferred or done unless a tool result said so. When a tool result says rejected or failed, nothing was recorded: tell the truth.
- Never work out a date, a weekday, a deadline or an amount in your head. Read them from the facts blocks in your instructions and from tool results. When the person names a day in words (today, tomorrow, a weekday, this or next week), take its date from the DAY NAMES block, word for word. Send dates to tools as YYYY-MM-DD, times as HH:MM on the 24-hour clock, amounts as plain numbers.
- Before you answer a day the person named, find that exact date in the CALENDAR. Its row says whether it is a business day and, if it is not, which day is the next business day. Compare two dates by their YYYY-MM-DD form: a date is after a limit only if its YYYY-MM-DD is greater.
- People talk like people. They give a number or a date in two parts, correct themselves, mention unrelated things, or ask a question back. Wait until a value is complete before using it, but a value is complete as soon as all of its parts have been said, even when the sentence around it trails off or is cut short. Always use the corrected value, never treat an unrelated remark as an answer, and after answering a question go back to the question you were asking.
- Finish every sentence you start: never end a turn in the middle of a sentence, a number or a date. When the person talks over you, or says they did not hear or understand you, answer them and then say the part they missed once more in one short sentence. Saying it again this way is not a new offer and not a new attempt.
- The call ends only through close_call. It is always the last tool call and always alone: first get the result of every other tool, then say your last words to the person in the same message in which you call close_call, and call no tool after it.

"""


DAY_NAMES = '\n\nDAY NAMES (computed by the bank, binding; the date of every day a person can name in words)\n{{rt_days}}'


SETUP_FALLBACK = """vars['rt_state'] = ''
vars['rt_identity'] = 'pending'
vars['rt_closed'] = 'no'
vars['rt_live'] = 'Nothing recorded yet.'
vars['rt_today'] = str(vars.get(NOW_VAR) or '')
vars['rt_calendar'] = 'Calendar unavailable.'
vars['rt_days'] = 'Unavailable.'
vars['rt_callback'] = 'Callback facts unavailable.'
vars['rt_account_facts'] = 'The account has not been loaded.'
vars['rt_account_json'] = ''
vars['rt_account'] = 'none'
"""
ACCOUNT_FALLBACK = """vars['rt_account'] = 'error'
vars['rt_account_facts'] = 'ACCOUNT LOOKUP FAILED. You have no account information. Say that you cannot access the account details right now and do not state any amount or product.'
"""


FAILURES = {
    'verify_identity': ('identity.service_error', 'nothing was counted; call verify_identity again with the same answer.'),
    'create_promise': ('promise.failed_retry', 'nothing was recorded; do not say it was; call create_promise again with exactly the same product, amount and date.'),
    'add_note': ('note.failed', 'the note was not saved; call add_note once more with the same values.'),
    'schedule_callback': ('callback.failed', 'the callback was not booked; call schedule_callback once more with the same values.'),
    'transfer_to_agent': ('transfer.failed', 'the transfer did not happen; call transfer_to_agent once more with the same queue.'),
    'close_call': (None, 'the call is still open; call close_call again with the same values.'),
}


def failure_rules(spec, tools):
    """What the model must do when a call returns an error and no result.

    The engine runs no post-hook after an HTTP error, so the runtime cannot say this at that
    moment; it has to be in the prompt from the start. The wording per tool is the same
    guidance the runtime gives when it can."""
    guidance = spec.get('guidance') or {}
    extra = lambda event: (' ' + guidance[event].strip()) if event and isinstance(guidance.get(event), str) else ''
    rows = ['WHEN A TOOL CALL FAILS',
            'A failed call gives you an error such as "API request failed" and no what_to_do_next. Then nothing was recorded, booked, transferred or closed. Never say that it was, and never end the call there.',
            'A failed call is one of two things. Decide which one BEFORE you do anything else, in this order:',
            '1. THE BANK\'S POLICY CHECK REFUSED THE VALUES YOU SENT. This is the case when LIVE STATE shows LAST REJECTED TOOL CALL, and also whenever a value you sent '
            'breaks a fact in your instructions: an amount below the lowest or above the highest amount of that product; a date before the earliest or after the latest date '
            'of that product, or in the past; a day that the CALENDAR or DAY NAMES mark as NOT a business day; a product id that is not in the account facts; a product that '
            'already has a promise or a note; a callback outside the callback slots; a value that is not in the tool\'s list of allowed values. '
            'Then: do NOT send the same values again. It is NOT a service problem: never call it one, never record a service error for it, and do not give up on the product. '
            'Tell the person what can be recorded instead, taken from the facts (the latest date, the lowest amount, the next business day, a time inside the slots), '
            'follow your instructions for that case, agree the new value with the person, and call the tool with the corrected values.',
            '2. ONLY IF every value you sent is inside those facts, the service did not answer:']
    for name in tools:
        event, text = FAILURES[name]
        row = '  - ' + name + ': ' + text + extra(event)
        if name == 'create_promise':
            attempts = int(spec['promise'].get('max_attempts', 2))
            row += (' At most ' + str(attempts) + ' attempt(s) for one product. After the last failed attempt: do not try again for that product and tell the person honestly that you could not record the promise.'
                    + extra('promise.failed_final'))
        rows.append(row)
    return '\n'.join(rows)


class CompileError(ValueError):
    pass


def promise_check(spec):
    """What the model has to compare before it reads a promise back.

    The runtime refuses a promise outside the limits, but a refused call reaches the model only as a failed call.
    A live call showed the model taking that for a service failure. So the comparison is asked for before the
    read-back, against the facts the runtime computed; the refusal stays behind it as the last line."""
    rows = ['CHECK EVERY PROMISE BEFORE YOU READ IT BACK',
            'Before you read a promise back, ask the person to confirm it, or call create_promise, compare it with the facts of that product:',
            '1. Find the date in the CALENDAR and take its weekday from that row, never from memory.',
            '2. The date must not be in the past and must be inside the dates that LIMITS THE BANK CHECKS gives for that product: not after its latest date, and not before its earliest date if one is given.'
            + (' The CALENDAR must mark it as a business day.' if spec['promise'].get('business_day_only', True) else ''),
            '3. The amount must not be below the lowest amount and not above the highest amount of that product.',
            'If any of these does not hold, do NOT read it back and do NOT call create_promise: the bank would refuse it. Tell the person which limit applies and offer '
            'what your instructions say for that case, with the value taken from the facts (the latest date, the next business day, the lowest amount). '
            'Read back only a promise that passes all of them.']
    return '\n'.join(rows)


def pre_fallback(role, fields):
    """What a pre-hook does when its own code raises: the request is detached, so that it cannot go out unchecked.

    It uses nothing but the request body and one variable, because whatever raised may be anything else. That
    holds for a close as well: a close whose check failed is not sent, every time. If the fault does not go away
    the call cannot be closed by the agent; the platform ends it at its turn limit. If the platform does not
    run the hook at all, nothing here runs either."""
    detach = ['_b = config.get(\'body\')',
              'if not isinstance(_b, dict):',
              '    _b = dict()',
              '    config[\'body\'] = _b',
              'for _k in ' + repr(tuple(fields)) + ':',
              '    _b[_k] = \'blocked_by_policy\'']
    body = detach + ['try:', '    vars[\'rt_fault_' + role + '\'] = \'yes\'', 'except Exception:', '    pass']
    return '\n'.join(['try:'] + ['    ' + line for line in body] + ['except Exception:', '    pass'])


def _library():
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding='utf-8'))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    header = [n for n in tree.body if isinstance(n, (ast.Import, ast.Assign))]
    return functions, header


def _narrow(source):
    """The same code with one space per level of indentation instead of four. A workflow carries every function
    once per hook that uses it, so the indentation alone is a sixth of its size. Text inside a docstring is left alone."""
    out, inside = [], False
    for line in source.splitlines():
        if not inside:
            bare = line.lstrip(' ')
            line = ' ' * ((len(line) - len(bare)) // 4) + bare
        if line.count('"""') % 2:
            inside = not inside
        out.append(line)
    return '\n'.join(out)


def runtime_code(entry, call, cfg, fallback=''):
    """Self-contained snippet: only the functions `entry` needs, wrapped so it runs under any exec scoping."""
    functions, header = _library()
    needed, queue = [], [entry]
    while queue:
        name = queue.pop()
        if name in needed:
            continue
        needed.append(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Name) and node.id in functions and node.id not in needed:
                queue.append(node.id)
    body = [ast.unparse(n) for n in header] + [_narrow(ast.unparse(functions[n])) for n in functions if n in needed]
    packed = json.dumps(cfg, ensure_ascii=True, separators=(',', ':'))
    for _ in range(8):
        packed = packed.replace('}}', '} }').replace('{{', '{ {')
    body.append('CFG = json.loads(' + repr(packed) + ')')
    body.append(call)
    inner = '\n'.join(' ' + line for block in body for line in block.splitlines())
    args = 'vars, config, result'
    code = ('def _rt(' + args + '):\n' + inner + '\n'
            + 'try:\n    _rt(' + ', '.join(a if a in call else 'None' for a in args.split(', ')) + ')\n'
            + 'except Exception:\n' + ''.join('    ' + line + '\n' for line in (fallback or 'pass').splitlines()))
    if '{{' in code or '}}' in code:
        raise CompileError('generated code contains a double brace; Freya would treat it as a template')
    ast.parse(code)
    return code


def runtime_cfg(spec, api):
    """The structured subset of the spec that the embedded runtime reads."""
    cal = spec['calendar']
    longest = 10
    for value in spec['account']['values']:
        for expr in exprs(value.get('expr')):
            if expr.get('kind') in ('business_days_after', 'calendar_days_after'):
                longest = max(longest, int(expr.get('n', 0)))
    weekly = max(1, len(cal['business_weekdays']))
    props = lambda tool: api.body(tool)[0]
    call_vars = set(spec.get('call_variables') or []) | {'customer_id'}
    flags = api.success_flags()

    def shape(tool):
        fields, required = api.body(tool)
        return {'fields': {name: [schema.get('type', 'string'), list(schema.get('enum') or []), schema.get('format') or ''] for name, schema in fields.items()},
                'required': list(required)}

    cfg = {
        'now_var': spec['now_variable'],
        # Names are read from the tool API, never assumed: the flag every answer carries, and every body field
        # that is filled from a call variable.
        'ok_field': spec.get('ok_field') or (flags[0] if len(flags) == 1 else 'ok'),
        'shape': {ROLES[tool]: shape(tool) for tool in TOOLS if tool in api.ops},
        'calendar': {'business_weekdays': cal['business_weekdays'], 'holidays': cal.get('holidays') or [],
                     'week_start': cal.get('week_start', 0),
                     'horizon_days': min(84, max(21, int(math.ceil(longest * 7.0 / weekly)) + 7))},
        'identity': {k: spec['identity'].get(k) for k in ('field_param', 'answer_param', 'questions', 'max_wrong', 'response', 'hints')},
        'account': {'products_path': spec['account']['products_path'], 'id_field': spec['account']['id_field'],
                    'values': spec['account']['values'], 'limits': spec['account'].get('limits') or {},
                    'tool_node': 'get_account'},
        'promise': spec['promise'], 'transfer': spec['transfer'], 'close': spec['close'],
        'guidance': {k: v for k, v in (spec.get('guidance') or {}).items() if isinstance(v, str)},
        'customer_fields': sorted({f for tool in TOOLS for f in props(tool) if f in call_vars}) or ['customer_id'],
    }
    for key in ('note', 'callback'):
        if spec.get(key):
            cfg[key] = spec[key]
    if spec.get('note'):
        # Not part of the spec: the reasons the tool API accepts, and what the close has to know about notes.
        reasons = (props('add_note').get(spec['note'].get('reason_param')) or {}).get('enum') or []
        cfg['note'] = dict(spec['note'], reasons=list(reasons))
        cfg['close'] = dict(spec['close'], notes_required=True, notes_after_transfer=not spec['note'].get('forbidden_after_transfer'))
    return cfg


def _slice(cfg, role):
    """Only the configuration one tool's hooks read, to keep the workflow small."""
    keep = ('now_var', 'ok_field', 'calendar', 'customer_fields', role) + (('callback',) if role == 'callback' else ())
    out = {k: v for k, v in cfg.items() if k in keep}
    out['shape'] = {role: cfg['shape'][role]} if role in cfg['shape'] else {}
    out['guidance'] = {k: v for k, v in cfg['guidance'].items() if k.startswith(role + '.')}
    return out


def _edge(target, description, *conditions):
    if not conditions:
        return {'target_node': target, 'description': description, 'condition': {'operator': 'always'}}
    return {'target_node': target, 'description': description, 'condition': {'logic': 'or', 'conditions': [
        {'variable': v, 'operator': 'equals', 'operand': o} for v, o in conditions]}}


def build_tool(tool, spec, api, cfg, engine_run=False):
    props, required = api.body(tool)
    call_vars = set(spec.get('call_variables') or []) | {'customer_id'}
    role = ROLES[tool]
    extra = {}
    if tool == 'close_call':
        close = spec['close']
        table = '; '.join(o + (' with reason ' + ' or '.join(r) if r else ' with no reason') for o, r in close['outcomes'].items())
        reasons = sorted({r for rs in close['outcomes'].values() for r in rs})
        extra[close['outcome_param']] = {'enum': list(close['outcomes']), 'description': 'How the call ended. Allowed: ' + table + '.'}
        extra[close['reason_param']] = {'description': 'The reason that belongs to the outcome. Allowed pairs: ' + table + '. Leave it out when the outcome has no reason.'}
        if reasons:
            extra[close['reason_param']]['enum'] = reasons
    if tool == 'verify_identity':
        idc = spec['identity']
        extra[idc['field_param']] = {'description': 'Which security question this answer belongs to. One of: ' + ', '.join(q['field'] for q in idc['questions']) + '.'}
        formats = {'date': 'a date as YYYY-MM-DD', 'digits': 'digits only', 'text': 'exactly as the customer said it'}
        extra[idc['answer_param']] = {'description': 'The complete answer. ' + ' '.join(q['field'] + ': ' + formats[q['format']] + '.' for q in idc['questions'])}
    if tool in ('create_promise', 'add_note'):
        block = spec['promise'] if tool == 'create_promise' else spec['note']
        extra[block['product_param']] = {'description': 'The product id exactly as written in the account facts, for example p1. Never invent one.'}
    body = {}
    for name, schema in props.items():
        leaf = {'type': schema.get('type', 'string'), 'default': '{{' + name + '}}'}
        if name not in call_vars:
            text = str(schema.get('description') or '').strip()
            if schema.get('enum'):
                leaf['enum'] = list(schema['enum'])
                text = (text + ' One of: ' + ', '.join(str(v) for v in schema['enum']) + '.').strip()
            if schema.get('format') == 'date':
                text = (text + ' Format YYYY-MM-DD.').strip()
            leaf['description'] = text or name
            if name in extra:
                leaf.update(extra[name])
        body[name] = leaf
    description = (spec.get('tool_descriptions') or {}).get(tool) or (api.summary(tool) + ' CALL it whenever your instructions say so.')
    if tool == 'close_call':
        description = description.rstrip() + ' Always call it alone in its own message, after the result of every other tool call has come back, never together with another tool.'
    result = {
        'type': 'function',
        'config': {'timeoutMs': 15000, 'maxRetries': 2 if engine_run else 0},
        'function': {'name': tool, 'handler': '__function__:api_call', 'description': description,
                     'parameters': {'type': 'object', 'properties': {
                         'url': {'type': 'string', 'default': api.url(tool)},
                         'method': {'type': 'string', 'default': api.ops[tool]['method']},
                         'headers': {'type': 'object', 'properties': {'Content-Type': {'type': 'string', 'default': 'application/json'}}},
                         'body': {'type': 'object', 'properties': body,
                                  'required': [r for r in required if r not in call_vars]}}}},
        'post_process_code': runtime_code('post_' + role, 'post_' + role + '(CFG, vars, result)', _slice(cfg, role)),
    }
    if role != 'account':
        result['pre_process_code'] = runtime_code('pre_' + role, 'pre_' + role + '(CFG, vars, config)', _slice(cfg, role),
                                                  pre_fallback(role, cfg['customer_fields']))
    return result


def _code_node(name, target, code, transitions):
    return {'name': name, 'node_type': 'code', 'passthrough': True, 'passthrough_default_transition': target,
            'on_enter': {'type': 'silent'}, 'disable_inactivity_monitor': True,
            'pre_actions': [{'type': 'function', 'function': {'name': 'code_exec', 'handler': '__function__:code_exec',
                'parameters': {'type': 'object', 'properties': {'code': {'type': 'string', 'default': code},
                                                              'timeout_ms': {'type': 'number', 'default': 10000}}}}}],
            'transitions': transitions}


def compile_workflow(spec, api):
    """Return (workflow, report) for a spec that validation has accepted. `api` is the parsed tool API."""
    cfg = runtime_cfg(spec, api)
    rules = spec['rules']
    idc, close = spec['identity'], spec['close']
    tool = lambda name, **kw: build_tool(name, spec, api, cfg, **kw)
    closed = ('rt_closed', 'yes')
    ok = 'close_call_response.data.' + cfg['ok_field']
    close_ok = [('close_call_response.data.rt_closed', 'yes'), (ok, 'true'), (ok, 'True')]

    contact_tools = [tool('verify_identity')] + ([tool('schedule_callback')] if spec.get('callback') else []) + [tool('transfer_to_agent'), tool('close_call')]
    deal_tools = [tool('create_promise')] + ([tool('add_note')] if spec.get('note') else []) + [tool('transfer_to_agent'), tool('close_call')]
    verified = [('rt_identity', 'verified'), ('verify_identity_response.data.rt_identity', 'verified')]
    if idc['response'].get('all_verified'):
        flat = 'verify_identity_response.data.' + idc['response']['all_verified']
        verified += [(flat, 'true'), (flat, 'True')]

    contact_task = (rules['before_verification'].strip()
        + '\n\nTOOLS IN THIS PART OF THE CALL: ' + ', '.join(t['function']['name'] for t in contact_tools) + '. No other tool exists yet.'
        + '\n\nSECURITY QUESTIONS, in this order, one at a time: ' + '; '.join(str(i + 1) + '. ' + q['ask'] + ' (field ' + q['field'] + ')' for i, q in enumerate(idc['questions']))
        + '. Call verify_identity once for every complete answer, and only for answers given by the customer in person.'
        + '\n\n' + failure_rules(spec, [t['function']['name'] for t in contact_tools])
        + '\n\nTODAY: {{rt_today}}'
        + DAY_NAMES
        + ('\n\nCALLBACK FACTS (computed by the bank, binding)\n{{rt_callback}}' if spec.get('callback') else '')
        + '\n\nCALENDAR (computed by the bank, binding)\n{{rt_calendar}}'
        + '\n\nLIVE STATE\n{{rt_live}}')
    deal_task = (rules['after_verification'].strip()
        + '\n\n' + promise_check(spec)
        + '\n\nTOOLS IN THIS PART OF THE CALL: ' + ', '.join(t['function']['name'] for t in deal_tools) + '. Identity is verified and the account is already loaded: never ask a security question again.'
        + '\n\n' + failure_rules(spec, [t['function']['name'] for t in deal_tools])
        + '\n\nTODAY: {{rt_today}}'
        + DAY_NAMES
        + '\n\nACCOUNT FACTS (computed by the bank, binding; use these exact product ids, amounts and dates)\n{{rt_account_facts}}'
        + '\n\nCALENDAR (computed by the bank, binding)\n{{rt_calendar}}'
        + '\n\nLIVE STATE (what is recorded so far)\n{{rt_live}}')

    nodes = {
        'setup': _code_node('setup', 'contact', runtime_code('setup', 'setup(CFG, vars)', cfg, 'NOW_VAR = ' + repr(spec['now_variable']) + '\n' + SETUP_FALLBACK),
                            [_edge('contact', 'always')]),
        'contact': {
            'name': 'contact', 'node_type': 'conversation',
            'on_enter': {'type': 'tts', 'text': spec['opening_line'].strip()}, 'on_enter_first_entry_only': True,
            'task_message': contact_task, 'tools': contact_tools, 'max_node_turns': 60,
            'transitions': [_edge('end', 'The call was closed', closed, *close_ok),
                            _edge('get_account', 'Identity is fully verified', *verified)]},
        # Named after its tool so the engine's automatic result keys have one predictable prefix.
        'get_account': {
            'name': 'get_account', 'node_type': 'api_request', 'passthrough': True,
            'passthrough_default_transition': 'account_facts', 'on_enter': {'type': 'silent'},
            'disable_inactivity_monitor': True, 'pre_actions': [tool('get_account', engine_run=True)],
            'response_mapping': [{'path': 'data.rt_account_json', 'variable': 'rt_account_json'},
                                 {'path': 'data', 'variable': 'rt_account_raw'}],
            'transitions': [_edge('account_facts', 'always')]},
        'account_facts': _code_node('account_facts', 'negotiate', runtime_code('load_account', 'load_account(CFG, vars)', cfg, ACCOUNT_FALLBACK),
                                    [_edge('negotiate', 'always')]),
        'negotiate': {
            'name': 'negotiate', 'node_type': 'conversation', 'on_enter': {'type': 'llm'}, 'on_enter_first_entry_only': True,
            'task_message': deal_task, 'tools': deal_tools, 'max_node_turns': 80,
            'transitions': [_edge('end', 'The call was closed', closed, *close_ok)]},
        'end': {
            'name': 'end', 'node_type': 'end_call', 'on_enter': {'type': 'silent'}, 'disable_inactivity_monitor': True,
            'transitions': [], 'terminal': {'type': 'function', 'function': {
                'name': 'call_closed', 'handler': '__function__:end_call',
                'parameters': {'type': 'object', 'properties': {'message_mode': {'type': 'string', 'default': 'none'}}}}}},
    }
    workflow = {
        'initial_node': 'setup',
        'system_prompt': PLATFORM_RULES.replace('{language}', str(spec.get('language') or 'English')) + rules['always'].strip(),
        'nodes': nodes, 'global_nodes': {},
        'test_variables': copy.deepcopy(spec.get('test_variables') or {}),
    }
    report = {'nodes': list(nodes), 'bytes': len(json.dumps(workflow)),
              'tools': {'contact': [t['function']['name'] for t in contact_tools], 'negotiate': [t['function']['name'] for t in deal_tools]}}
    return workflow, report
