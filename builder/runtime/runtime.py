"""Deterministic runtime embedded verbatim into Freya code nodes and tool hooks.

Constraints of the Freya sandbox: only json, re and datetime; the source must
never contain two consecutive braces; nothing here may raise into the engine.
Everything brief-specific arrives in CFG, never in this file.
"""
import json
import re
import datetime

WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
WDL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
       'October', 'November', 'December']
DATE_KINDS = ('today', 'named', 'business_days_after', 'calendar_days_after', 'next_business_day',
              'last_business_day_of_week', 'first_business_day_of_week', 'end_of_month', 'fixed_date',
              'earliest', 'latest')
DAY = datetime.timedelta(days=1)


def to_date(value):
    return datetime.date.fromisoformat(str(value).strip()[:10])


def is_date(value):
    return isinstance(value, datetime.date)


def is_biz(day, cal):
    return day.weekday() in cal['business_weekdays'] and day.isoformat() not in cal['holidays']


def next_biz(day, cal):
    for _ in range(400):
        if is_biz(day, cal):
            return day
        day = day + DAY
    return day


def prev_biz(day, cal):
    for _ in range(400):
        if is_biz(day, cal):
            return day
        day = day - DAY
    return day


def nth_biz_after(day, n, cal):
    n = int(n)
    if n <= 0:
        return next_biz(day, cal)
    for _ in range(2000):
        day = day + DAY
        if is_biz(day, cal):
            n -= 1
            if n == 0:
                return day
    return day


def week_days(today, offset, cal):
    start = today - datetime.timedelta(days=(today.weekday() - int(cal.get('week_start', 0))) % 7)
    start = start + datetime.timedelta(days=7 * int(offset))
    return [start + datetime.timedelta(days=i) for i in range(7)]


def biz_of_week(today, offset, cal, last):
    for step in range(60):
        days = [d for d in week_days(today, int(offset) + step, cal) if is_biz(d, cal)]
        if days:
            return days[-1] if last else days[0]
    return today


def say_date(day):
    return WD[day.weekday()] + ' ' + day.isoformat() + ' (' + WDL[day.weekday()] + ' ' + str(day.day) + ' ' + MON[day.month - 1] + ')'


def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(',', '').strip())
    except Exception:
        return None


def money(value):
    return '%.2f' % float(value)


def show(value):
    if is_date(value):
        return say_date(value)
    if isinstance(value, float):
        return money(value)
    return str(value)


def matches(when, obj):
    if not when:
        return True
    for key in when:
        want = when[key]
        got = obj.get(key)
        if isinstance(want, dict):
            num = number(got)
            if num is None:
                return False
            if want.get('min') is not None and num < float(want['min']):
                return False
            if want.get('max') is not None and num > float(want['max']):
                return False
        elif isinstance(want, list):
            if got not in want and str(got) not in [str(w) for w in want]:
                return False
        elif str(got) != str(want):
            return False
    return True


def fill(template, obj):
    text = str(template)
    for key in obj:
        text = text.replace('{' + str(key) + '}', show(obj[key]))
    return text


def evaluate(expr, today, cal, product, named):
    """Evaluate one configured expression to a date, a number or a string."""
    if not isinstance(expr, dict):
        return expr
    kind = expr.get('kind')
    out = None
    if kind == 'by':
        for case in expr.get('cases', []):
            if matches(case.get('when'), product):
                return evaluate(case.get('expr'), today, cal, product, named)
        raise ValueError('no case matched')
    if kind == 'const':
        return expr.get('value')
    if kind == 'text':
        both = dict(product)
        both.update(named)
        return fill(expr.get('template', ''), both)
    if kind == 'field':
        out = number(product.get(expr.get('field')))
        if out is None:
            raise ValueError('field not numeric')
    elif kind == 'percent':
        base = evaluate(expr.get('of'), today, cal, product, named)
        if isinstance(base, str):
            base = named.get(base, product.get(base))
        out = round(float(number(base)) * float(expr.get('percent')) / 100.0, 2)
    elif kind == 'fixed_amount':
        out = float(expr.get('value'))
    elif kind == 'today':
        out = today
    elif kind == 'named':
        out = named[expr.get('name')]
    elif kind == 'business_days_after':
        out = nth_biz_after(today, expr.get('n', 1), cal)
    elif kind == 'next_business_day':
        out = nth_biz_after(today, 1, cal)
    elif kind == 'calendar_days_after':
        out = today + datetime.timedelta(days=int(expr.get('n', 0)))
    elif kind in ('last_business_day_of_week', 'first_business_day_of_week'):
        last = kind.startswith('last')
        week = int(expr.get('week', 0))
        out = biz_of_week(today, week, cal, last)
        if expr.get('if_today_or_past') == 'following_week' and out <= today:
            out = biz_of_week(today, week + 1, cal, last)
    elif kind == 'end_of_month':
        month = today.month - 1 + int(expr.get('months_ahead', 0))
        first = datetime.date(today.year + month // 12, month % 12 + 1, 1)
        nxt = (first + datetime.timedelta(days=32)).replace(day=1)
        out = nxt - DAY
    elif kind == 'fixed_date':
        out = to_date(expr.get('date'))
    elif kind in ('earliest', 'latest'):
        items = [evaluate(e, today, cal, product, named) for e in expr.get('of', [])]
        out = min(items) if kind == 'earliest' else max(items)
    else:
        raise ValueError('unknown expression kind')
    if is_date(out):
        if expr.get('roll') == 'next_business':
            out = next_biz(out, cal)
        elif expr.get('roll') == 'previous_business':
            out = prev_biz(out, cal)
        cap = expr.get('not_after')
        if cap and is_date(named.get(cap)) and out > named[cap]:
            out = named[cap]
    return out


def parse_now(raw):
    text = str(raw or '').strip().replace(' ', 'T')
    try:
        return datetime.datetime.fromisoformat(text[:16])
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(text[:10] + 'T00:00')
    except Exception:
        return datetime.datetime.utcnow().replace(second=0, microsecond=0)


def calendar_text(cfg, today):
    cal = cfg['calendar']
    lines = []
    names = ['THIS WEEK', 'NEXT WEEK']
    weeks = (int(cal.get('horizon_days', 28)) + 6) // 7 + 1
    for w in range(weeks):
        title = names[w] if w < 2 else 'WEEK +' + str(w)
        lines.append(title + ':')
        for day in week_days(today, w, cal):
            if day < today:
                continue
            row = '  ' + day.isoformat() + ' ' + WDL[day.weekday()] + ' ' + str(day.day) + ' ' + MON[day.month - 1]
            if day == today:
                row += ' = TODAY'
            if is_biz(day, cal):
                row += ' - business day'
            else:
                nxt = next_biz(day, cal)
                row += ' - NOT a business day (' + ('bank holiday' if day.isoformat() in cal['holidays'] else 'closed') + '), next business day ' + nxt.isoformat() + ' ' + WDL[nxt.weekday()]
            lines.append(row)
    return '\n'.join(lines)


def day_names(cfg, today):
    """Every way a person names a nearby day, already turned into a date, so the model never counts days."""
    cal = cfg['calendar']

    def row(words, day):
        text = '  ' + words + ' = ' + WDL[day.weekday()] + ' ' + str(day.day) + ' ' + MON[day.month - 1] + ' = ' + day.isoformat()
        if is_biz(day, cal):
            return text + ' - business day'
        nxt = next_biz(day, cal)
        return text + ' - NOT a business day, next business day ' + nxt.isoformat() + ' ' + WDL[nxt.weekday()]

    name = WDL[today.weekday()]
    rows = [row('today, "this ' + name + '"', today), row('tomorrow', today + DAY), row('the day after tomorrow', today + DAY + DAY)]
    for step in range(1, 7):
        day = today + datetime.timedelta(days=step)
        name = WDL[day.weekday()]
        rows.append(row('"' + name + '", "this ' + name + '", "on ' + name + '", "the coming ' + name + '"', day))
        rows.append(row('"' + name + ' next week", "' + name + ' week"', day + datetime.timedelta(days=7)))
    rows.append(row('"' + WDL[today.weekday()] + ' next week", "a week from today"', today + datetime.timedelta(days=7)))
    rows.append('  "next <weekday>" can mean either of the two dates of that weekday above: say the date and let the person confirm it.')
    return '\n'.join(rows)


def callback_slots(cfg, now):
    cb = cfg.get('callback') or dict()
    cal = cfg['calendar']
    today = now.date()
    days = []
    if cb.get('include_today', True):
        days.append(today)
    last = today
    for _ in range(int(cb.get('next_business_days', 1))):
        last = nth_biz_after(last, 1, cal)
        days.append(last)
    if cb.get('today_must_be_business_day') and days and days[0] == today and not is_biz(today, cal):
        days = days[1:]
    start = str(cb.get('start', '00:00'))
    end = str(cb.get('end', '23:59'))
    slots = []
    for day in days:
        lo = start
        strict = False
        if day == today and cb.get('after_now', True):
            mark = now.strftime('%H:%M')
            if mark >= lo:
                lo = mark
                strict = True
        if lo > end or (strict and lo == end):
            continue
        slots.append([day.isoformat(), lo, end, strict])
    return slots


def callback_text(cfg, now):
    slots = callback_slots(cfg, now)
    if not cfg.get('callback'):
        return 'No callback rules.'
    if not slots:
        return 'No callback slot is available.'
    rows = []
    for s in slots:
        rows.append('  ' + say_date(to_date(s[0])) + ': ' + ('later than ' if s[3] else 'from ') + s[1] + ' up to and including ' + s[2])
    return 'The current time is ' + now.strftime('%H:%M') + '. A callback may ONLY be booked in one of these slots (24-hour clock):\n' + '\n'.join(rows) + '\nAny other day or time is outside the window.'


def check_callback(cfg, now, date, time):
    try:
        day = to_date(date).isoformat()
        m = re.match(r'^\s*(\d{1,2})[:.h](\d{2})', str(time))
        if not m:
            return 'the time must be given as HH:MM'
        hhmm = '%02d:%02d' % (int(m.group(1)), int(m.group(2)))
    except Exception:
        return 'the date must be YYYY-MM-DD and the time HH:MM'
    for s in callback_slots(cfg, now):
        if s[0] == day and hhmm <= s[2] and (hhmm > s[1] if s[3] else hhmm >= s[1]):
            return ''
    return 'that day or time is outside the allowed callback window'


def safe(name):
    return re.sub(r'[^A-Za-z0-9]', '_', str(name))


def rec(st, kind, name=''):
    """The variable that holds one confirmed record of this call."""
    # Tool calls of one model message can run side by side. Each then works on its own copy of
    # the variables, taken before any of them started, and what they wrote is merged afterwards:
    # for a variable several of them wrote, one write survives. rt_state is such a variable, so
    # it cannot be the record of what happened. A record therefore gets a variable of its own
    # that is written once, by the post-hook that saw the bank confirm it, and that does not
    # exist before. The name carries an id made at the start of the call, so that a variable
    # handed in with the call cannot pass for a record.
    return 'rt_done_' + str(st.get('run') or '') + '_' + kind + ('_' + safe(name) if name != '' else '')


def attempts(vars, st, pid):
    """How many create_promise requests went out for this product. Each leaves a variable of its own."""
    count = 0
    for i in range(1, 20):
        if not vars.get(rec(st, 'try', str(pid) + '_' + str(i))):
            break
        count = i
    return count


def absorb(vars, st):
    """Complete the state from the records: what any earlier call confirmed, whoever wrote rt_state last."""
    for p in st['products']:
        if not isinstance(p, dict) or 'id' not in p:
            continue
        pid = p['id']
        raw = vars.get(rec(st, 'promise', pid))
        if raw:
            try:
                value = json.loads(raw)
                if isinstance(value, dict):
                    st['promises'][pid] = value
            except Exception:
                pass
        raw = vars.get(rec(st, 'note', pid))
        if raw:
            st['notes'][pid] = raw
        tries = attempts(vars, st, pid)
        if tries and pid not in st['promises']:
            st['fails'][pid] = tries
        else:
            st['fails'].pop(pid, None)
    queues = []
    for i in range(1, 10):
        raw = vars.get(rec(st, 'transfer', i))
        if not raw:
            break
        queues.append(str(raw))
    if queues:
        st['transfers'] = queues
    raw = vars.get(rec(st, 'callback'))
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                st['callback'] = value
        except Exception:
            pass
    status = vars.get(rec(st, 'identity'))
    if status in ('verified', 'failed'):
        st['identity'] = status


def load_state(vars):
    fresh = dict(wrong=0, identity='pending', verified=[], products=[], promises=dict(), notes=dict(),
                 fails=dict(), transfers=[], callback=None, closed=False, blocks=0, open=None, held=[], run='')
    st = fresh
    try:
        got = json.loads(vars.get('rt_state') or '')
        if isinstance(got, dict):
            for key in fresh:
                if not isinstance(got.get(key), type(fresh[key])) and fresh[key] is not None:
                    got[key] = fresh[key]
            st = got
    except Exception:
        pass
    try:
        absorb(vars, st)
    except Exception:
        pass
    return st


def run_id():
    try:
        return datetime.datetime.utcnow().strftime('%d%H%M%S%f')
    except Exception:
        return 'r'


def opened(st, tool, key, hint):
    # The engine runs no post-hook when the service answers with an HTTP error. This mark keeps
    # what to do in that case in front of the model until some call gets an answer.
    st['open'] = [tool, str(key), hint]


def product_facts(cfg, products, today):
    cal = cfg['calendar']
    out = []
    for raw in products:
        if not isinstance(raw, dict):
            continue
        named = dict()
        shown = []
        for item in cfg['account']['values']:
            try:
                value = evaluate(item['expr'], today, cal, raw, named)
            except Exception:
                value = None
            named[item['name']] = value
            if value is not None and item['name'] != 'label':
                shown.append([item.get('label') or item['name'], show(value)])
        pid = str(raw.get(cfg['account']['id_field']))
        limits = cfg['account'].get('limits') or dict()
        fact = dict(id=pid, label=str(named.get('label') or pid), shown=shown, raw=raw)
        for key in ('min_amount', 'max_amount', 'latest_date'):
            value = named.get(limits.get(key)) if limits.get(key) else None
            fact[key] = value.isoformat() if is_date(value) else value
        out.append(fact)
    return out


def account_text(cfg, facts):
    if not facts:
        return 'ACCOUNT LOOKUP FAILED. You have no account information. Say that you cannot access the account details right now and do not state any amount or product.'
    blocks = [str(len(facts)) + ' late product(s), in this order:']
    for f in facts:
        rows = ['PRODUCT ' + f['id'] + ' - always call it: "' + f['label'] + '"']
        for key in f['raw']:
            if key != cfg['account']['id_field']:
                rows.append('  ' + str(key) + ': ' + show(f['raw'][key]))
        for pair in f['shown']:
            rows.append('  ' + pair[0] + ': ' + pair[1])
        limits = []
        if f.get('min_amount') is not None:
            limits.append('lowest amount ' + money(f['min_amount']))
        if f.get('max_amount') is not None:
            limits.append('highest amount ' + money(f['max_amount']))
        if f.get('latest_date'):
            limits.append('latest date ' + say_date(to_date(f['latest_date'])) + ' (no later date can be recorded, whatever the person asks for)')
        if limits:
            rows.append('  LIMITS THE BANK CHECKS ON EVERY PROMISE for this product: ' + '; '.join(limits))
        blocks.append('\n'.join(rows))
    return '\n'.join(blocks)


def pending(st):
    return [p for p in st['products'] if p['id'] not in st['promises'] and p['id'] not in st['notes']]


def live_text(cfg, st):
    rows = []
    for p in st['products']:
        if p['id'] in st['promises']:
            pr = st['promises'][p['id']]
            rows.append(p['id'] + ' "' + p['label'] + '": PROMISE RECORDED ' + money(pr['amount']) + ' on ' + str(pr['date']) + ' (cannot be changed in this call)')
        elif p['id'] in st['notes']:
            rows.append(p['id'] + ' "' + p['label'] + '": note recorded, reason ' + str(st['notes'][p['id']]))
        else:
            rows.append(p['id'] + ' "' + p['label'] + '": nothing recorded yet')
    if st['transfers']:
        rows.append('Transfer done to queue: ' + ', '.join(st['transfers']))
    if st.get('last_block'):
        rows.append('LAST REJECTED TOOL CALL: ' + st['last_block'])
    mark = st.get('open')
    if isinstance(mark, list) and len(mark) > 2 and mark[2]:
        rows.append('IF YOUR LAST TOOL CALL GAVE AN ERROR INSTEAD OF A RESULT: ' + str(mark[2]))
    return '\n'.join(rows) or 'Nothing recorded yet.'


def save_state(cfg, vars, st):
    vars['rt_state'] = json.dumps(st)
    vars['rt_live'] = live_text(cfg, st)
    vars['rt_identity'] = st['identity']
    vars['rt_closed'] = 'yes' if st['closed'] else 'no'


def setup(cfg, vars):
    now = parse_now(vars.get(cfg['now_var']))
    st = load_state(dict())
    st['run'] = run_id()
    st['now'] = now.strftime('%Y-%m-%dT%H:%M')
    st['today'] = now.date().isoformat()
    vars['rt_today'] = say_date(now.date()) + ', current time ' + now.strftime('%H:%M')
    vars['rt_calendar'] = calendar_text(cfg, now.date())
    vars['rt_days'] = day_names(cfg, now.date())
    vars['rt_callback'] = callback_text(cfg, now)
    vars['rt_account_facts'] = 'The account has not been loaded.'
    vars['rt_account_json'] = ''
    vars['rt_account'] = 'none'
    save_state(cfg, vars, st)


def find_products(cfg, vars):
    data = None
    for key in ('rt_account_json', 'rt_account_raw'):
        value = vars.get(key)
        if isinstance(value, str) and value.strip():
            try:
                value = json.loads(value)
            except Exception:
                value = None
        if isinstance(value, (dict, list)):
            data = value
            break
    path = cfg['account']['products_path']
    if isinstance(data, dict):
        for part in [p for p in path.split('.') if p]:
            data = data.get(part) if isinstance(data, dict) else None
    if isinstance(data, list) and data:
        return data
    # Last resort: rebuild the list from the engine's flattened result keys.
    prefix = cfg['account']['tool_node'] + '_response.data.' + path + '.'
    rows = dict()
    for key in list(vars.keys()):
        if isinstance(key, str) and key.startswith(prefix):
            rest = key[len(prefix):].split('.', 1)
            if len(rest) == 2 and rest[0].isdigit():
                value = vars.get(key)
                num = number(value)
                rows.setdefault(int(rest[0]), dict())[rest[1]] = value if num is None or rest[1] == cfg['account']['id_field'] else num
    return [rows[i] for i in sorted(rows)]


def load_account(cfg, vars):
    st = load_state(vars)
    facts = product_facts(cfg, find_products(cfg, vars), to_date(st['today']))
    st['products'] = [dict(id=f['id'], label=f['label'], min_amount=f['min_amount'], max_amount=f['max_amount'],
                           latest_date=f['latest_date']) for f in facts]
    vars['rt_account_facts'] = account_text(cfg, facts)
    vars['rt_account'] = 'ok' if facts else 'error'
    save_state(cfg, vars, st)


def body_of(config):
    body = config.get('body') if isinstance(config, dict) else None
    return body if isinstance(body, dict) else dict()


def block(cfg, vars, config, st, tool, message, field=None):
    """Stop a call that would break policy, so that nothing can be recorded, and tell the model why."""
    # With `field`, on a verified call, the request keeps its customer but names a thing the bank
    # does not know: the bank refuses it with an ordinary answer and the post-hook can explain.
    # Otherwise the request is detached from the customer; the bank then answers with an HTTP
    # error, no post-hook runs, and the reason is left in the live state only.
    # The reason is kept per tool, so that the post-hook of another call cannot take it for its own.
    body = body_of(config)
    if field and st.get('identity') == 'verified':
        body[field] = 'blocked_by_policy'
    else:
        for name in cfg['customer_fields']:
            body[name] = 'blocked_by_policy'
    st['open'] = None
    st['blocks'] = int(st.get('blocks', 0)) + 1
    st['last_block'] = message
    vars['rt_block_' + tool] = message
    save_state(cfg, vars, st)


def guide(cfg, event, builtin):
    extra = (cfg.get('guidance') or dict()).get(event) or ''
    return (builtin + ' ' + extra).strip()


def finish(cfg, vars, result, st, text, extra=None):
    st['last_block'] = ''
    st['open'] = None
    save_state(cfg, vars, st)
    if isinstance(result, dict):
        result.pop('header', None)
        data = result.get('data')
        if not isinstance(data, dict):
            data = dict()
            result['data'] = data
        data['what_to_do_next'] = text
        if extra:
            data.update(extra)


def blocked(cfg, vars, result, tool):
    """Turn the bank's refusal of a blocked request into the reason for the block."""
    # An answer that says the bank carried the call out is never the blocked request: it belongs
    # to another call of the same tool, and what the bank did is reported as it happened.
    message = vars.get('rt_block_' + tool) or ''
    if not message or succeeded(cfg, result):
        return False
    vars['rt_block_' + tool] = ''
    if isinstance(result, dict):
        result.pop('header', None)
        result['status'] = 'rejected'
        result['message'] = 'Rejected by the bank policy check. Nothing was recorded.'
        result['data'] = dict(ok=False, rejected_by_policy=True, nothing_was_recorded=True, what_to_do_next=message)
    return True


def succeeded(cfg, result):
    if not isinstance(result, dict) or result.get('status') != 'success':
        return False
    data = result.get('data')
    if not isinstance(data, dict):
        return False
    flag = data.get(cfg.get('ok_field', 'ok'))
    return flag is True or str(flag).lower() == 'true'


def truthy(value):
    return value is True or str(value).lower() == 'true'


def last_args(vars, tool):
    try:
        value = json.loads(vars.get('rt_args_' + tool) or '')
        return value if isinstance(value, dict) else dict()
    except Exception:
        return dict()


def keep_args(vars, tool, config):
    vars['rt_args_' + tool] = json.dumps(body_of(config))
    vars['rt_block_' + tool] = ''


def send(cfg, vars, config, st, tool, key, hint):
    """A call that passed every check goes out: keep its final values and mark it as on its way."""
    keep_args(vars, tool, config)
    st['last_block'] = ''
    opened(st, tool, key, hint)
    save_state(cfg, vars, st)


# ---------------------------------------------------------------- identity

def question_for(cfg, field):
    for q in cfg['identity']['questions']:
        if q['field'] == field:
            return q
    return None


def identity_failure(cfg):
    return guide(cfg, 'identity.service_error', 'The identity service did not answer. Nothing was counted. Call verify_identity again with the same answer.')


def identity_over(cfg):
    return guide(cfg, 'identity.failed', 'IDENTITY CHECK FAILED: no wrong answers are left. Do not ask any further security question and never disclose anything about the account.')


def pre_identity(cfg, vars, config):
    idc = cfg['identity']
    body = body_of(config)
    q = question_for(cfg, body.get(idc['field_param']))
    answer = body.get(idc['answer_param'])
    if q and isinstance(answer, str):
        text = answer.strip()
        if q.get('format') == 'date':
            m = re.match(r'^(\d{1,4})\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{1,4})$', text)
            if m and len(m.group(1)) <= 2:
                a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
                order = q.get('numeric_order', 'DMY')
                day, month = (a, b) if order == 'DMY' else (b, a)
                if c < 100:
                    c += 1900 if c > 30 else 2000
                text = '%04d-%02d-%02d' % (c, month, day)
            elif m:
                text = '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        elif q.get('format') == 'digits':
            digits = re.sub(r'\D', '', text)
            if digits:
                text = digits
        body[idc['answer_param']] = text
    st = load_state(vars)
    left = number(st.get('left'))
    if st['identity'] == 'failed' and (left is None or left > 0):
        # The brief ended the check before the service would: the service must not be asked again.
        return block(cfg, vars, config, st, 'identity', identity_over(cfg))
    send(cfg, vars, config, st, 'identity', body.get(idc['field_param']), identity_failure(cfg))


def post_identity(cfg, vars, result):
    idc = cfg['identity']
    st = load_state(vars)
    args = last_args(vars, 'identity')
    field = args.get(idc['field_param'])
    resp = idc['response']
    data = result.get('data') if isinstance(result, dict) and isinstance(result.get('data'), dict) else dict()
    order = [q['field'] for q in idc['questions']]

    def ask(name):
        q = question_for(cfg, name) or dict()
        return '"' + str(q.get('ask') or name) + '" (field ' + str(name) + ')'

    if not isinstance(result, dict) or result.get('status') != 'success':
        return finish(cfg, vars, result, st, identity_failure(cfg))
    if st['identity'] == 'failed':
        for key in ('verified', 'all_verified'):
            if resp.get(key) and resp[key] in data:
                data[resp[key]] = False
        return finish(cfg, vars, result, st, identity_over(cfg), dict(rt_identity='failed'))
    verified = truthy(data.get(resp.get('verified')))
    all_ok = truthy(data.get(resp.get('all_verified'))) if resp.get('all_verified') else False
    hint = data.get(resp.get('hint')) if resp.get('hint') else None
    if verified or all_ok:
        if field and field not in st['verified']:
            st['verified'].append(field)
        missing = [f for f in order if f not in st['verified']]
        if all_ok or not missing:
            st['identity'] = 'verified'
            vars[rec(st, 'identity')] = 'verified'
            return finish(cfg, vars, result, st, guide(cfg, 'identity.all_verified', 'Identity is fully verified. The account is being loaded now.'), dict(rt_identity='verified'))
        return finish(cfg, vars, result, st, guide(cfg, 'identity.next_question', 'That answer is correct. Do not ask it again. Now ask the next security question: ' + ask(missing[0]) + '.'))
    for h in idc.get('hints') or []:
        if hint is not None and str(hint) == str(h.get('value')) and not h.get('counts_as_wrong'):
            return finish(cfg, vars, result, st, guide(cfg, 'identity.hint.' + str(hint), 'The answer was not accepted, but this does NOT count as a wrong answer. ' + str(h.get('instruction') or '') + ' Then call verify_identity again for the same question ' + ask(field) + '.'))
    left = number(data.get(resp.get('wrong_left'))) if resp.get('wrong_left') else None
    limit = int(idc.get('max_wrong', 3))
    before = st.get('left')
    if left is not None:
        st['left'] = left
    counted = succeeded(cfg, result) or (left is not None and left < (limit if before is None else before))
    if not counted and st['identity'] != 'failed':
        return finish(cfg, vars, result, st, guide(cfg, 'identity.unreadable', 'The service could not read that answer, so it was not counted as wrong. Check the format, ask the customer to repeat it if needed, and call verify_identity again for ' + ask(field) + '.'))
    st['wrong'] = int(st.get('wrong', 0)) + 1
    own = limit - st['wrong']
    left = own if left is None else min(left, own)
    if left <= 0 or st['identity'] == 'failed':
        st['identity'] = 'failed'
        vars[rec(st, 'identity')] = 'failed'
        return finish(cfg, vars, result, st, identity_over(cfg), dict(rt_identity='failed'))
    return finish(cfg, vars, result, st, guide(cfg, 'identity.wrong', 'That answer is WRONG. Wrong answers left before the check ends: ' + str(int(left)) + '. Do not move to another question. Ask the same question again: ' + ask(field) + '.'))


# ---------------------------------------------------------------- account

def post_account(cfg, vars, result):
    if isinstance(result, dict):
        result.pop('header', None)
        data = result.get('data')
        if isinstance(data, dict) and result.get('status') == 'success':
            vars['rt_account_json'] = json.dumps(data)
            data['rt_account_json'] = vars['rt_account_json']


# ---------------------------------------------------------------- promise

def resolve_product(st, value):
    ids = [p['id'] for p in st['products']]
    text = str(value or '').strip()
    if text in ids:
        return text
    if len(ids) == 1:
        return ids[0]
    low = text.lower()
    hits = [p['id'] for p in st['products'] if low and (low == p['label'].lower() or low in p['label'].lower())]
    return hits[0] if len(hits) == 1 else ''


def promise_failure(cfg, label, failures):
    """What to do after the promise service has failed `failures` times for one product."""
    if failures < int(cfg['promise'].get('max_attempts', 2)):
        return guide(cfg, 'promise.failed_retry', 'NOT RECORDED: the service failed and nothing was recorded for ' + label + '. Do NOT tell the customer it is recorded. Call create_promise again now with exactly the same product, amount and date.')
    return guide(cfg, 'promise.failed_final', 'NOT RECORDED: the service failed again for ' + label + '. Do not try again for this product. Tell the customer honestly that you could not record the promise.')


def pre_promise(cfg, vars, config):
    pc = cfg['promise']
    cal = cfg['calendar']
    st = load_state(vars)
    body = body_of(config)
    soft = pc['product_param']
    keep_args(vars, 'promise', config)
    if st['identity'] != 'verified' or not st['products']:
        return block(cfg, vars, config, st, 'promise', 'A promise cannot be recorded before identity is verified and the account is loaded.')
    pid = resolve_product(st, body.get(pc['product_param']))
    if not pid:
        return block(cfg, vars, config, st, 'promise', 'Unknown product. Use one of these product ids exactly: ' + ', '.join([p['id'] + ' = ' + p['label'] for p in st['products']]) + '.', soft)
    body[pc['product_param']] = pid
    prod = [p for p in st['products'] if p['id'] == pid][0]
    if pid in st['promises']:
        return block(cfg, vars, config, st, 'promise', 'A promise is already recorded for ' + prod['label'] + '. It cannot be recorded again or changed. Do not call create_promise for this product again.', soft)
    if st['transfers'] and pc.get('forbidden_after_transfer', True):
        return block(cfg, vars, config, st, 'promise', 'The call has been transferred. No promise may be recorded after a transfer.', soft)
    # Every request that went out for this product and did not end in a recorded promise has failed,
    # with or without an answer.
    failures = attempts(vars, st, pid)
    if failures >= int(pc.get('max_attempts', 2)):
        return block(cfg, vars, config, st, 'promise', promise_failure(cfg, prod['label'], failures), soft)
    amount = number(body.get(pc['amount_param']))
    if amount is None or amount <= 0:
        return block(cfg, vars, config, st, 'promise', 'The amount must be a positive number.', soft)
    amount = round(amount, 2)
    lo, hi = number(prod.get('min_amount')), number(prod.get('max_amount'))
    if lo is not None and amount < lo - 0.004:
        return block(cfg, vars, config, st, 'promise', 'NOT RECORDED: ' + money(amount) + ' is below the lowest amount that can be recorded for ' + prod['label'] + ', which is ' + money(lo) + '. Follow the amount rules of your instructions.', soft)
    if hi is not None and amount > hi + 0.004:
        return block(cfg, vars, config, st, 'promise', 'NOT RECORDED: ' + money(amount) + ' is more than the amount due for ' + prod['label'] + ', which is ' + money(hi) + '. Read back the correct amount and get a new confirmation.', soft)
    body[pc['amount_param']] = amount
    try:
        day = to_date(body.get(pc['date_param']))
    except Exception:
        return block(cfg, vars, config, st, 'promise', 'The date must be sent as YYYY-MM-DD. Look it up in the calendar facts.', soft)
    today = to_date(st['today'])
    latest = to_date(prod['latest_date']) if prod.get('latest_date') else None
    if day < today:
        return block(cfg, vars, config, st, 'promise', 'NOT RECORDED: ' + say_date(day) + ' is in the past. Today is ' + say_date(today) + '.', soft)
    if latest and day > latest:
        return block(cfg, vars, config, st, 'promise', 'NOT RECORDED: ' + say_date(day) + ' is after the latest date that can be recorded for ' + prod['label'] + ', which is ' + say_date(latest) + '. Tell the customer the latest date you can record is ' + say_date(latest) + ', offer it, read it back and get a clear yes before calling create_promise again.', soft)
    if pc.get('business_day_only', True) and not is_biz(day, cal):
        alt = next_biz(day, cal)
        if latest and alt > latest:
            alt = latest
        return block(cfg, vars, config, st, 'promise', 'NOT RECORDED: ' + say_date(day) + ' is not a business day. Offer ' + say_date(alt) + ' instead, read it back and get a clear yes before calling create_promise again.', soft)
    body[pc['date_param']] = day.isoformat()
    vars[rec(st, 'try', pid + '_' + str(failures + 1))] = 'sent'
    send(cfg, vars, config, st, 'promise', pid, promise_failure(cfg, prod['label'], failures + 1))


def post_promise(cfg, vars, result):
    if blocked(cfg, vars, result, 'promise'):
        return
    pc = cfg['promise']
    st = load_state(vars)
    args = last_args(vars, 'promise')
    pid = str(args.get(pc['product_param']))
    label = ([p['label'] for p in st['products'] if p['id'] == pid] or [pid])[0]
    if succeeded(cfg, result):
        st['promises'][pid] = dict(amount=args.get(pc['amount_param']), date=args.get(pc['date_param']))
        vars[rec(st, 'promise', pid)] = json.dumps(st['promises'][pid])
        st['fails'].pop(pid, None)
        rest = pending(st)
        base = 'RECORDED: the promise for ' + label + ' (' + money(args.get(pc['amount_param'])) + ' on ' + str(args.get(pc['date_param'])) + ') is now recorded. Do not call create_promise for this product again.'
        if rest:
            return finish(cfg, vars, result, st, guide(cfg, 'promise.recorded_more', base + ' Products still without a promise or a note: ' + ', '.join([p['id'] + ' "' + p['label'] + '"' for p in rest]) + '.'))
        return finish(cfg, vars, result, st, guide(cfg, 'promise.recorded_all', base + ' Every product is now handled.'))
    st['fails'][pid] = max(1, attempts(vars, st, pid))
    return finish(cfg, vars, result, st, promise_failure(cfg, label, st['fails'][pid]))


# ---------------------------------------------------------------- notes

def note_failure(cfg):
    return guide(cfg, 'note.failed', 'The note was NOT saved. Call add_note once more with the same values.')


def pre_note(cfg, vars, config):
    nc = cfg['note']
    st = load_state(vars)
    body = body_of(config)
    soft = nc['product_param']
    keep_args(vars, 'note', config)
    pid = resolve_product(st, body.get(nc['product_param']))
    if not pid:
        return block(cfg, vars, config, st, 'note', 'Unknown product. Use one of these product ids exactly: ' + ', '.join([p['id'] + ' = ' + p['label'] for p in st['products']]) + '.', soft)
    body[nc['product_param']] = pid
    if pid in st['notes']:
        return block(cfg, vars, config, st, 'note', 'A note already exists for this product. Do not add another one.', soft)
    if pid in st['promises']:
        return block(cfg, vars, config, st, 'note', 'This product has a recorded promise, so it needs no note.', soft)
    if st['transfers'] and nc.get('forbidden_after_transfer'):
        return block(cfg, vars, config, st, 'note', 'The call has been transferred. No note may be added.', soft)
    reasons = nc.get('reasons') or []
    if reasons and body.get(nc['reason_param']) not in reasons:
        return block(cfg, vars, config, st, 'note', 'NOT SAVED: ' + str(body.get(nc['reason_param'])) + ' is not a reason that can be noted. Use one of: ' + ', '.join([str(r) for r in reasons]) + '.', soft)
    send(cfg, vars, config, st, 'note', pid, note_failure(cfg))


def post_note(cfg, vars, result):
    if blocked(cfg, vars, result, 'note'):
        return
    nc = cfg['note']
    st = load_state(vars)
    args = last_args(vars, 'note')
    pid = str(args.get(nc['product_param']))
    if succeeded(cfg, result):
        st['notes'][pid] = args.get(nc['reason_param'])
        vars[rec(st, 'note', pid)] = str(st['notes'][pid])
        rest = pending(st)
        if rest:
            return finish(cfg, vars, result, st, guide(cfg, 'note.recorded_more', 'The note is saved. Products still without a promise or a note: ' + ', '.join([p['id'] + ' "' + p['label'] + '"' for p in rest]) + '.'))
        return finish(cfg, vars, result, st, guide(cfg, 'note.recorded_all', 'The note is saved. Every product now has a promise or a note.'))
    return finish(cfg, vars, result, st, note_failure(cfg))


# ---------------------------------------------------------------- callback, transfer

def callback_failure(cfg):
    return guide(cfg, 'callback.failed', 'The callback was NOT booked. Call schedule_callback once more with the same values.')


def pre_callback(cfg, vars, config):
    cb = cfg['callback']
    st = load_state(vars)
    body = body_of(config)
    keep_args(vars, 'callback', config)
    problem = check_callback(cfg, parse_now(st.get('now')), body.get(cb['date_param']), body.get(cb['time_param']))
    if problem:
        return block(cfg, vars, config, st, 'callback', 'NOT BOOKED: ' + problem + '. ' + callback_text(cfg, parse_now(st.get('now'))) + ' Follow your callback instructions for a time outside the window.')
    if st.get('callback'):
        return block(cfg, vars, config, st, 'callback', 'A callback is already booked. Do not book another one.')
    m = re.match(r'^\s*(\d{1,2})[:.h](\d{2})', str(body.get(cb['time_param'])))
    body[cb['time_param']] = '%02d:%02d' % (int(m.group(1)), int(m.group(2)))
    body[cb['date_param']] = to_date(body.get(cb['date_param'])).isoformat()
    send(cfg, vars, config, st, 'callback', '', callback_failure(cfg))


def post_callback(cfg, vars, result):
    if blocked(cfg, vars, result, 'callback'):
        return
    cb = cfg['callback']
    st = load_state(vars)
    args = last_args(vars, 'callback')
    if succeeded(cfg, result):
        st['callback'] = [args.get(cb['date_param']), args.get(cb['time_param'])]
        vars[rec(st, 'callback')] = json.dumps(st['callback'])
        return finish(cfg, vars, result, st, guide(cfg, 'callback.booked', 'The callback is booked for ' + str(st['callback'][0]) + ' at ' + str(st['callback'][1]) + '.'))
    return finish(cfg, vars, result, st, callback_failure(cfg))


def transfer_failure(cfg):
    return guide(cfg, 'transfer.failed', 'The transfer did NOT work. Call transfer_to_agent once more with the same queue.')


def pre_transfer(cfg, vars, config):
    send(cfg, vars, config, load_state(vars), 'transfer', '', transfer_failure(cfg))


def post_transfer(cfg, vars, result):
    tc = cfg['transfer']
    st = load_state(vars)
    args = last_args(vars, 'transfer')
    if succeeded(cfg, result):
        st['transfers'].append(str(args.get(tc['queue_param'])))
        vars[rec(st, 'transfer', len(st['transfers']))] = st['transfers'][-1]
        return finish(cfg, vars, result, st, guide(cfg, 'transfer.done', 'The transfer to queue ' + str(args.get(tc['queue_param'])) + ' is done.'))
    return finish(cfg, vars, result, st, transfer_failure(cfg))


# ---------------------------------------------------------------- close

def flag_true(st, flag):
    name, _, arg = str(flag).partition(':')
    if name == 'transferred':
        return bool(st['transfers']) and (not arg or arg in st['transfers'])
    if name == 'promise_recorded':
        return bool(st['promises'])
    if name == 'identity_failed':
        return st['identity'] == 'failed'
    if name == 'callback_booked':
        return bool(st.get('callback'))
    if name == 'all_products_noted':
        ids = [p['id'] for p in st['products']]
        return bool(ids) and not st['promises'] and all([i in st['notes'] and (not arg or str(st['notes'][i]) == arg) for i in ids])
    if name == 'any_note':
        return bool(st['notes']) and not st['promises']
    return False


def forced_close(cfg, st):
    cc = cfg['close']
    for rule in cc.get('forced') or []:
        if not flag_true(st, rule.get('if')) or (rule.get('unless') and flag_true(st, rule.get('unless'))):
            continue
        reason = rule.get('reason')
        source = str(rule.get('reason_from') or '')
        if source == 'last_transfer_queue':
            reason = (cc.get('queue_reasons') or dict()).get(st['transfers'][-1], st['transfers'][-1])
        elif source.startswith('first_note_not:'):
            skip = source.split(':', 1)[1]
            reason = None
            for p in st['products']:
                note = st['notes'].get(p['id'])
                if note is not None and str(note) != skip:
                    reason = note
                    break
            if reason is None:
                continue
        return [rule.get('outcome'), reason]
    return None


def close_failure():
    return 'close_call did NOT work. The call is still open. Call close_call again with the same values.'


def refuse_close(cfg, vars, config, st, kind, message):
    """Refuse a close that breaks a rule, once per kind of problem in a call."""
    # close_call has no answer through which the reason could reach the model: a refused close
    # looks like a failed one. The second close with the same problem is therefore let through,
    # because a call that is never closed is worse than a call closed imperfectly.
    if kind in st['held']:
        return False
    st['held'].append(kind)
    block(cfg, vars, config, st, 'close', message)
    return True


def pre_close(cfg, vars, config):
    cc = cfg['close']
    st = load_state(vars)
    body = body_of(config)
    after_transfer = bool(st['transfers']) and not cc.get('notes_after_transfer')
    rest = pending(st) if cc.get('notes_required') and st['identity'] == 'verified' and not after_transfer else []
    if rest and refuse_close(cfg, vars, config, st, 'open_products', 'NOT CLOSED: these products have neither a promise nor a note: '
                             + ', '.join([p['id'] + ' "' + p['label'] + '"' for p in rest])
                             + '. Call add_note for each of them with its reason, one call per message, then call close_call again.'):
        return
    forced = forced_close(cfg, st)
    if forced:
        body[cc['outcome_param']] = forced[0]
        if forced[1] is None:
            body.pop(cc['reason_param'], None)
        else:
            body[cc['reason_param']] = forced[1]
    outcomes = cc.get('outcomes') or dict()
    outcome = str(body.get(cc['outcome_param']))
    allowed = outcomes.get(outcome)
    if allowed is not None:
        reason = body.get(cc['reason_param'])
        if not allowed:
            body.pop(cc['reason_param'], None)
        elif reason not in allowed and len(allowed) == 1:
            body[cc['reason_param']] = allowed[0]
    if body.get(cc['reason_param']) in (None, ''):
        body.pop(cc['reason_param'], None)
    table = '; '.join([o + (' with reason ' + ' or '.join([str(r) for r in outcomes[o]]) if outcomes[o] else ' with no reason') for o in outcomes])
    if outcomes and allowed is None:
        if refuse_close(cfg, vars, config, st, 'outcome', 'NOT CLOSED: ' + outcome + ' is not an outcome. Call close_call again with one of: ' + table + '.'):
            return
    elif allowed and body.get(cc['reason_param']) not in allowed:
        if refuse_close(cfg, vars, config, st, 'reason', 'NOT CLOSED: outcome ' + outcome + ' needs one of these reasons: '
                        + ', '.join([str(r) for r in allowed]) + '. Call close_call again with the outcome and its reason.'):
            return
    send(cfg, vars, config, st, 'close', '', close_failure())


def post_close(cfg, vars, result):
    st = load_state(vars)
    if succeeded(cfg, result):
        st['closed'] = True
        return finish(cfg, vars, result, st, 'The call is closed. Do not call any tool again.', dict(rt_closed='yes'))
    return finish(cfg, vars, result, st, close_failure(), dict(rt_closed='no'))
