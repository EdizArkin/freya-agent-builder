"""Is a spec executable against this tool API? Every problem is reported; none is guessed away."""
import datetime
import itertools
import re

from builder.runtime import runtime as rt
from builder.spec.models import EXPR_KINDS, FLAGS, GUIDANCE_EVENTS, TOOLS, VERSION, SpecError, exprs, whens


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
        elif name in edges:
            axes.append([(name, v) for v in sorted(edges[name])])
    base = {}
    for name, schema in props.items():
        kind = schema.get('type')
        base[name] = 250.0 if kind == 'number' else 5 if kind == 'integer' else 'p1' if name == account['id_field'] else '1234'
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
            need(len(set(names)) == len(names), 'account value names must be unique')
            need('label' in names, 'account.values must define "label", the spoken name of a product')
        limits = account.get('limits') if isinstance(account.get('limits'), dict) else {}
        for key in ('min_amount', 'max_amount', 'latest_date'):
            if limits.get(key):
                need(limits[key] in names, 'account.limits.%s refers to undefined value %r' % (key, limits[key]))
        if not bad:
            calendar = {'business_weekdays': days, 'holidays': cal.get('holidays') or [], 'week_start': cal.get('week_start', 0)}
            today = datetime.date(2030, 1, 9)
            for product in sample_products(spec, api):
                named = {}
                for value in values:
                    try:
                        named[value['name']] = rt.evaluate(value['expr'], today, calendar, product, named)
                    except Exception as exc:
                        bad.append('account value %s cannot be computed for a product like %s: %s' % (value['name'], {k: product[k] for k in list(product)[:4]}, exc))
                        break
                else:
                    for key, kind in (('min_amount', float), ('max_amount', float), ('latest_date', datetime.date)):
                        if limits.get(key) and not isinstance(named.get(limits[key]), kind):
                            bad.append('account.limits.%s must be %s, got %r' % (key, 'a date' if kind is datetime.date else 'an amount', named.get(limits[key])))
                if bad:
                    break

    params('promise', 'create_promise', ('product_param', 'amount_param', 'date_param'))
    params('note', 'add_note', ('product_param', 'reason_param'), optional=True)
    callback = params('callback', 'schedule_callback', ('date_param', 'time_param'), optional=True)
    if callback:
        for key in ('start', 'end'):
            need(re.fullmatch(r'\d\d:\d\d', str(callback.get(key, ''))), 'callback.%s must be HH:MM' % key)
    transfer = params('transfer', 'transfer_to_agent', ('queue_param',))
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
    unsupported = spec.get('unsupported') or []
    if unsupported:
        (warn if best_effort else bad).append('the extractor reported mandatory policy it could not express: ' + ' | '.join(str(u) for u in unsupported))
    if bad:
        raise SpecError(bad)
    return warn
