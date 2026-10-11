"""Is a spec executable against this tool API? Every problem is reported; none is guessed away."""
import datetime
import itertools
import math
import re

from builder.runtime import runtime as rt
from builder.spec.models import EXPR_KINDS, FLAGS, GUIDANCE_EVENTS, NORMALIZERS, TIME_TESTS, TOOLS, VERSION, SpecError, exprs, times, whens


def sample_products(spec, api):
    """Synthetic products covering every enum value and every numeric boundary named in the spec."""
    account = spec['account']
    props = api.product_schema(account['products_path'])
    edges = {}
    for value in account['values']:
        for when in whens(value.get('expr')):
            for field, want in when.items():
                if isinstance(want, dict):
                    edges.setdefault(field, set()).update(v for v in (want.get('min'), want.get('max')) if v is not None)
    axes = []
    for name, schema in props.items():
        if schema.get('enum'):
            axes.append([(name, v) for v in schema['enum']])
        elif schema.get('type') == 'boolean':
            axes.append([(name, True), (name, False)])
        elif name in edges:
            axes.append([(name, v) for v in sorted(edges[name])])
    base = {}
    for name, schema in props.items():
        kind = schema.get('type')
        base[name] = 250.0 if kind == 'number' else 5 if kind == 'integer' else False if kind == 'boolean' else 'p1' if name == account['id_field'] else '1234'
    products = []
    for combo in itertools.islice(itertools.product(*axes), 200) if axes else [()]:
        product = dict(base)
        product.update(dict(combo))
        products.append(product)
    return products


def validate(spec, api, best_effort=False):
    """Return warnings; raise SpecError listing every problem that makes the spec non-executable."""
    bad, warn = [], []

    def need(cond, message):
        if not cond:
            bad.append(message)
        return cond

    if not isinstance(spec, dict):
        raise SpecError(['spec must be a JSON object'])
    need(spec.get('spec_version') == VERSION, 'spec_version must be ' + VERSION)
    for tool in TOOLS:
        need(tool in api.ops, 'tool API has no operation ' + tool)
    need(api.base.startswith('https://'), 'tool API has no https server url')
    if bad:
        raise SpecError(bad)

    # Two names the runtime must not guess: the flag that says the bank did it, and the fields that tie a
    # request to this call. Without them a refusal could be taken for a success, or a blocked request could
    # still reach the bank. Nothing overrides these: a build that cannot block reliably is not built.
    flags = api.success_flags()
    if spec.get('ok_field'):
        need(spec['ok_field'] in flags, 'ok_field=%r is not a true/false field of every answer of the tool API (%s)' % (spec['ok_field'], ', '.join(flags) or 'none'))
    else:
        need(len(flags) == 1, 'the tool API has no single true/false field that every answer carries (found: %s)' % (', '.join(flags) or 'none'))
    call_vars = set(spec.get('call_variables') or []) | {'customer_id'}
    for tool in TOOLS:
        fields, required = api.body(tool)
        mine = [f for f in fields if f in call_vars]
        outside = api.outside_body(tool)
        if outside:
            bad.append('a policy block cannot be guaranteed for %s: the request carries %s, which a hook cannot rewrite' % (tool, ', '.join(outside)))
        elif fields and not mine:
            bad.append('a policy block cannot be guaranteed for %s: no body field is filled from a call variable (call_variables: %s; body fields: %s)' % (
                tool, ', '.join(sorted(call_vars)), ', '.join(fields)))
        elif fields and not [f for f in mine if f in required and fields[f].get('type', 'string') == 'string' and not fields[f].get('enum')]:
            bad.append('a policy block cannot be guaranteed for %s: none of its call fields (%s) is a required free-text field' % (tool, ', '.join(mine)))

    def params(section, tool, keys, optional=False):
        block = spec.get(section)
        if block is None and optional:
            return None
        if not need(isinstance(block, dict), section + ' must be an object'):
            return None
        props, _ = api.body(tool)
        for key in keys:
            need(block.get(key) in props, '%s.%s=%r is not a body field of %s (fields: %s)' % (section, key, block.get(key), tool, ', '.join(props)))
        return block

    need(isinstance(spec.get('opening_line'), str) and spec['opening_line'].strip(), 'opening_line is required')
    need(isinstance(spec.get('now_variable'), str) and spec['now_variable'], 'now_variable is required')
    rules = spec.get('rules') if isinstance(spec.get('rules'), dict) else {}
    for key in ('always', 'before_verification', 'after_verification'):
        need(isinstance(rules.get(key), str) and len(rules[key].strip()) > 40, 'rules.' + key + ' must be substantial text')
    for key, text in rules.items():
        if isinstance(text, str) and '{{rt_' in text:
            bad.append('rules.' + key + ' must not reference runtime variables; the compiler appends the facts blocks')

    cal = spec.get('calendar') if isinstance(spec.get('calendar'), dict) else {}
    days = cal.get('business_weekdays')
    need(isinstance(days, list) and days and all(type(d) is int and 0 <= d <= 6 for d in days), 'calendar.business_weekdays must list weekday numbers, Monday=0')
    for day in cal.get('holidays') or []:
        try:
            datetime.date.fromisoformat(day)
        except (ValueError, TypeError):
            bad.append('calendar.holidays has a non ISO date: %r' % (day,))
    need(isinstance(cal.get('holidays', []), list), 'calendar.holidays must be a list')

    identity = params('identity', 'verify_identity', ('field_param', 'answer_param'))
    if identity:
        props, _ = api.body('verify_identity')
        questions = identity.get('questions')
        if need(isinstance(questions, list) and questions, 'identity.questions must be a non-empty list'):
            allowed = (props.get(identity.get('field_param')) or {}).get('enum')
            for q in questions:
                need(isinstance(q, dict) and q.get('field') and q.get('ask'), 'each identity question needs field and ask')
                need(q.get('format') in ('date', 'digits', 'text'), 'identity question format must be date, digits or text')
                steps = q.get('normalize') if isinstance(q, dict) else None
                need(steps is None or (isinstance(steps, list) and all(s in NORMALIZERS for s in steps)), 'identity question normalize may only list: ' + ', '.join(NORMALIZERS))
                if allowed:
                    need(q.get('field') in allowed, 'identity question field %r is not allowed by the tool API (%s)' % (q.get('field'), ', '.join(allowed)))
        need(type(identity.get('max_wrong')) is int and 1 <= identity['max_wrong'] <= 20, 'identity.max_wrong must be a small integer')
        response = identity.get('response') if isinstance(identity.get('response'), dict) else {}
        fields = (api.response('verify_identity').get('properties') or {})
        need(response.get('verified') in fields, 'identity.response.verified must name a verify_identity response field')
        for key in ('all_verified', 'wrong_left', 'hint'):
            if response.get(key):
                need(response[key] in fields, 'identity.response.%s=%r is not a verify_identity response field' % (key, response[key]))

    account = spec.get('account') if isinstance(spec.get('account'), dict) else {}
    if need(isinstance(account.get('products_path'), str) and isinstance(account.get('id_field'), str), 'account.products_path and account.id_field are required'):
        props = api.product_schema(account['products_path'])
        need(props, 'account.products_path does not lead to a list of objects in the get_account response')
        need(account['id_field'] in props, 'account.id_field is not a product field')
        values = account.get('values')
        names = []
        if need(isinstance(values, list) and values, 'account.values must be a non-empty list'):
            for value in values:
                if not need(isinstance(value, dict) and re.fullmatch(r'[a-z][a-z0-9_]*', str(value.get('name', ''))), 'each account value needs a snake_case name'):
                    continue
                names.append(value['name'])
                for e in exprs(value.get('expr')):
                    need(e.get('kind') in EXPR_KINDS, 'account value %s uses unknown expression kind %r' % (value['name'], e.get('kind')))
                    if e.get('kind') == 'field':
                        need(e.get('field') in props, 'account value %s reads unknown product field %r' % (value['name'], e.get('field')))
                    if e.get('kind') == 'named':
                        need(e.get('name') in names, 'account value %s refers to %r before it is defined' % (value['name'], e.get('name')))
                    if e.get('not_after'):
                        need(e['not_after'] in names, 'account value %s caps at undefined value %r' % (value['name'], e['not_after']))
                for when in whens(value.get('expr')):
                    for field in when:
                        need(field in props, 'account value %s tests unknown product field %r' % (value['name'], field))
                for when in times(value.get('expr')):
                    for test, clock in when.items():
                        need(test in TIME_TESTS and re.fullmatch(r'\d\d:\d\d', str(clock)), 'account value %s: a by_time case tests %s with HH:MM, got %r: %r' % (
                            value['name'], ', '.join(TIME_TESTS), test, clock))
            need(len(set(names)) == len(names), 'account value names must be unique')
            need('label' in names, 'account.values must define "label", the spoken name of a product')
        limits = account.get('limits') if isinstance(account.get('limits'), dict) else {}
        for key in ('min_amount', 'max_amount', 'latest_date', 'earliest_date', 'promise_allowed'):
            if limits.get(key):
                need(limits[key] in names, 'account.limits.%s refers to undefined value %r' % (key, limits[key]))
        if not bad:
            today = datetime.date(2030, 1, 9)
            # Every value has to compute for every kind of product, and at every time of day a by_time case names.
            clocks = sorted({'00:00', '23:59'} | {str(clock) for value in values for when in times(value.get('expr')) for clock in when.values()})
            for product, clock in itertools.product(sample_products(spec, api), clocks):
                calendar = {'business_weekdays': days, 'holidays': cal.get('holidays') or [], 'week_start': cal.get('week_start', 0), 'clock': clock}
                named = {}
                for value in values:
                    try:
                        named[value['name']] = rt.evaluate(value['expr'], today, calendar, product, named)
                    except Exception as exc:
                        bad.append('account value %s cannot be computed for a product like %s: %s' % (value['name'], {k: product[k] for k in list(product)[:4]}, exc))
                        break
                else:
                    for key, kind in (('min_amount', float), ('max_amount', float), ('latest_date', datetime.date), ('earliest_date', datetime.date)):
                        if limits.get(key) and not isinstance(named.get(limits[key]), kind):
                            bad.append('account.limits.%s must be %s, got %r' % (key, 'a date' if kind is datetime.date else 'an amount', named.get(limits[key])))
                    allowed = named.get(limits.get('promise_allowed')) if limits.get('promise_allowed') else True
                    if not isinstance(allowed, bool) and str(allowed).lower() not in ('yes', 'no', 'true', 'false'):
                        bad.append('account.limits.promise_allowed must be true or false, got %r' % (allowed,))
                    early, late = named.get(limits.get('earliest_date')), named.get(limits.get('latest_date'))
                    if isinstance(early, datetime.date) and isinstance(late, datetime.date) and early > late:
                        warn.append('for a product like %s at %s the earliest date %s is after the latest date %s: no promise can be recorded' % (
                            {k: product[k] for k in list(product)[:4]}, clock, early, late))
                if bad:
                    break
        rule = spec.get('promise') if isinstance(spec.get('promise'), dict) else {}
        groups = rule.get('resolve_first')
        if groups is not None and need(isinstance(groups, list) and all(isinstance(g, dict) and g for g in groups), 'promise.resolve_first must be a list of product conditions'):
            for group in groups:
                for field in group:
                    need(field in props, 'promise.resolve_first tests unknown product field %r' % field)
        total = rule.get('min_total')
        if total is not None and need(isinstance(total, dict), 'promise.min_total must be an object'):
            if 'amount' in total:
                need(type(total['amount']) in (int, float) and total['amount'] > 0, 'promise.min_total.amount must be a positive amount')
            else:
                need(type(total.get('percent')) in (int, float) and 0 < total['percent'] <= 100 and total.get('of') in names,
                     'promise.min_total needs a percent from 0 to 100 and "of", the name of an amount value')

        step = rule.get('amount_step')
        need(step is None or (type(step) in (int, float) and step > 0 and abs(step * 100 - round(step * 100)) < 1e-9), 'promise.amount_step must be a positive amount in whole cents')
        for key in ('amount_step_except_max', 'same_date'):
            need(type(rule.get(key, False)) is bool, 'promise.%s must be true or false' % key)
        need(not rule.get('amount_step_except_max') or (step is not None and limits.get('max_amount')), 'promise.amount_step_except_max needs promise.amount_step and account.limits.max_amount')
        floors = rule.get('min_amount_by_date')
        if floors is not None and need(isinstance(floors, list) and all(isinstance(f, dict) for f in floors), 'promise.min_amount_by_date must be a list of objects'):
            for floor in floors:
                need(floor.get('on_or_before') == 'today' or floor.get('on_or_before') in names, 'promise.min_amount_by_date: "on_or_before" is "today" or the name of a date value, got %r' % (floor.get('on_or_before'),))
                need(floor.get('min_amount') in names, 'promise.min_amount_by_date: "min_amount" is the name of an amount value, got %r' % (floor.get('min_amount'),))
        if not bad and (floors or step is not None):
            # The same computation the deployed runtime makes, on every kind of product: a limit that cannot be
            # worked out here would make the runtime refuse every promise for such a product.
            cfg = {'calendar': {'business_weekdays': days, 'holidays': cal.get('holidays') or [], 'week_start': cal.get('week_start', 0)}, 'account': account, 'promise': rule}
            for product in sample_products(spec, api):
                fact = (rt.product_facts(cfg, [product], datetime.date(2030, 1, 9), '09:00') or [{}])[0]
                if 'min_amount_by_date' in (fact.get('broken') or []):
                    bad.append('promise.min_amount_by_date needs a date and an amount for a product like %s' % ({k: product[k] for k in list(product)[:4]},))
                    break
                lo, hi = fact.get('min_amount'), fact.get('max_amount')
                if step is not None and isinstance(lo, float) and isinstance(hi, float) and not rule.get('amount_step_except_max'):
                    if math.floor(hi / step + 1e-9) * step < lo - 0.004:
                        warn.append('for a product like %s no multiple of %s lies between the lowest amount %s and the highest %s: no promise can be recorded' % (
                            {k: product[k] for k in list(product)[:4]}, step, lo, hi))

    params('promise', 'create_promise', ('product_param', 'amount_param', 'date_param'))
    params('note', 'add_note', ('product_param', 'reason_param'), optional=True)
    callback = params('callback', 'schedule_callback', ('date_param', 'time_param'), optional=True)
    if callback:
        for key in ('start', 'end'):
            need(re.fullmatch(r'\d\d:\d\d', str(callback.get(key, ''))), 'callback.%s must be HH:MM' % key)
        closed = callback.get('closed')
        if closed is not None and need(isinstance(closed, list) and all(isinstance(w, dict) and w for w in closed), 'callback.closed must be a list of periods, each with at least one of: ' + ', '.join(TIME_TESTS)):
            for w in closed:
                for test, clock in w.items():
                    need(test in TIME_TESTS and re.fullmatch(r'\d\d:\d\d', str(clock)), 'callback.closed: a period tests %s with HH:MM, got %r: %r' % (', '.join(TIME_TESTS), test, clock))
            minutes = ['%02d:%02d' % (m // 60, m % 60) for m in range(24 * 60)]
            if not bad and all(any(rt.clock_in(w, t) for w in closed) for t in minutes if str(callback['start']) <= t <= str(callback['end'])):
                warn.append('callback.closed covers the whole callback window: no callback can be booked')
    transfer = params('transfer', 'transfer_to_agent', ('queue_param',))
    if transfer:
        limit = transfer.get('max_transfers')
        need(limit is None or (type(limit) is int and limit >= 1), 'transfer.max_transfers must be a whole number of at least 1')
        still = (spec.get('promise') or {}).get('allowed_after_transfer_to') if isinstance(spec.get('promise'), dict) else None
        known = (api.body('transfer_to_agent')[0].get(transfer.get('queue_param')) or {}).get('enum')
        if still is not None and need(isinstance(still, list), 'promise.allowed_after_transfer_to must be a list of queues'):
            for queue in still:
                if known:
                    need(queue in known, 'promise.allowed_after_transfer_to names unknown queue %r' % (queue,))
    close = params('close', 'close_call', ('outcome_param', 'reason_param'))
    if close:
        props, _ = api.body('close_call')
        outcomes = close.get('outcomes')
        if need(isinstance(outcomes, dict) and outcomes and all(isinstance(v, list) for v in outcomes.values()), 'close.outcomes must map each outcome to its list of reasons (empty list for none)'):
            allowed = (props.get(close['outcome_param']) or {}).get('enum')
            for outcome in outcomes:
                if allowed:
                    need(outcome in allowed, 'close outcome %r is not allowed by the tool API (%s)' % (outcome, ', '.join(allowed)))
            queues = ((api.body('transfer_to_agent')[0].get((transfer or {}).get('queue_param')) or {}).get('enum')) if transfer else None
            for queue in (close.get('queue_reasons') or {}):
                if queues:
                    need(queue in queues, 'close.queue_reasons names unknown queue %r' % queue)
            for rule in close.get('forced') or []:
                if not need(isinstance(rule, dict) and str(rule.get('if', '')).split(':')[0] in FLAGS, 'close.forced rule has unknown flag %r' % (rule.get('if') if isinstance(rule, dict) else rule)):
                    continue
                if need(rule.get('outcome') in outcomes, 'close.forced outcome %r is not in close.outcomes' % rule.get('outcome')):
                    reasons = outcomes[rule['outcome']]
                    if rule.get('reason') is not None:
                        need(rule['reason'] in reasons, 'close.forced reason %r is not listed for outcome %s' % (rule['reason'], rule['outcome']))
                    elif not rule.get('reason_from'):
                        need(not reasons, 'close.forced rule for %s needs a reason or reason_from' % rule['outcome'])

    for event in (spec.get('guidance') or {}):
        if event not in GUIDANCE_EVENTS and not event.startswith('identity.hint.'):
            warn.append('guidance event %r is unknown and will be ignored' % event)
    if str(spec.get('language') or 'English').strip().lower() not in ('english', 'en'):
        # The runtime records a promise only after a yes it can recognise, and it knows the words of one language.
        warn.append('the agent speaks %s: the confirmation of a promise recognises a clear yes in English only, so no promise can be recorded in this language' % spec.get('language'))
    unsupported = spec.get('unsupported') or []
    if unsupported:
        (warn if best_effort else bad).append('the extractor reported mandatory policy it could not express: ' + ' | '.join(str(u) for u in unsupported))
    if bad:
        raise SpecError(bad)
    return warn


def digest(spec, api, other=None):
    """What a valid spec makes the agent do, as plain values per item: the content in which a mistake changes
    what the bank records. Two specs that mean the same have the same digest, however they are written.
    With `other`, the products of both specs are sampled, so that two digests can be compared item by item."""
    cal, idc, close = spec.get('calendar') or {}, spec.get('identity') or {}, spec.get('close') or {}
    promise, callback = spec.get('promise') or {}, spec.get('callback')
    out = {
        'business weekdays (Monday=0)': sorted(cal.get('business_weekdays') or []),
        'holidays': sorted(cal.get('holidays') or []),
        'security questions, in order (field, format, numeric date order, normalisation)':
            [[q.get('field'), q.get('format'), q.get('numeric_order'), q.get('normalize')] for q in idc.get('questions') or [] if isinstance(q, dict)],
        'wrong answers that end the check': idc.get('max_wrong'),
        'callback': {k: callback.get(k) for k in ('include_today', 'next_business_days', 'start', 'end', 'after_now')} if callback else 'never used',
        'times of day at which no callback can be booked': (callback or {}).get('closed') or 'none',
        'promise amounts': ('whole multiples of %s' % promise['amount_step'] + (', or the highest amount' if promise.get('amount_step_except_max') else '')) if promise.get('amount_step') is not None else 'any amount inside the limits',
        'lowest amount that depends on the promise date': promise.get('min_amount_by_date') or 'none',
        'all promises of one call on one date': bool(promise.get('same_date')),
        # Named by what it counts. As "promise attempts per product" a live audit took it for the number of times
        # the agent may try to persuade, and reported a defect that was none.
        'create_promise calls per product before giving up on a failing service': promise.get('max_attempts', 2),
        'promise only on a business day': promise.get('business_day_only', True),
        'promise after a transfer': 'never' if not promise.get('allowed_after_transfer_to') else 'only after a transfer to ' + ', '.join(sorted(promise['allowed_after_transfer_to'])),
        'products that come first': promise.get('resolve_first') or 'no order',
        'minimum on the total of all promises': promise.get('min_total') or 'none',
        'notes': ('used; after a transfer: ' + ('forbidden' if spec['note'].get('forbidden_after_transfer') else 'allowed')) if spec.get('note') else 'never used',
        'transfers per call': (spec.get('transfer') or {}).get('max_transfers') or 'no limit',
        'close outcomes and their reasons': {k: sorted(v) for k, v in (close.get('outcomes') or {}).items()},
        'close reason of each transfer queue': close.get('queue_reasons') or {},
        'close rules forced from what was recorded, first match wins': close.get('forced') or [],
        'policy reported as not expressible': spec.get('unsupported') or [],
    }
    products = sample_products(spec, api)
    if other is not None:
        products += [p for p in sample_products(other, api) if p not in products]
    account = spec['account']
    cfg = {'calendar': {'business_weekdays': cal.get('business_weekdays') or [], 'holidays': cal.get('holidays') or [], 'week_start': cal.get('week_start', 0)},
           'account': account, 'promise': promise}
    clocks = sorted({str(clock) for o in (spec, other) if o for value in o['account']['values'] for when in times(value.get('expr')) for clock in when.values()})
    clocks = ['09:00'] + [c for c in clocks] + [(datetime.datetime.strptime(c, '%H:%M') + datetime.timedelta(minutes=1)).strftime('%H:%M') for c in clocks]
    today = datetime.date(2030, 1, 9)
    for product in products[:40]:
        about = ', '.join('%s=%s' % (k, v) for k, v in product.items() if k != account['id_field'])
        for clock in sorted(set(clocks)):
            fact = (rt.product_facts(cfg, [product], today, clock) or [{}])[0]
            out['a product with %s, called on Wednesday 2030-01-09 at %s' % (about, clock)] = {
                'spoken name': fact.get('label'), 'lowest amount': fact.get('min_amount'), 'highest amount': fact.get('max_amount'),
                'earliest date': fact.get('earliest_date') or 'today', 'latest date': fact.get('latest_date'), 'promise allowed': fact.get('promise_allowed')}
    return out
