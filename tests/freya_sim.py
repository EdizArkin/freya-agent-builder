"""Offline stand-in for the Freya engine and the bank mock: runs a compiled workflow's code and hooks.

It executes every snippet with separate globals and locals, the least forgiving
way a sandbox can call exec, and it plays the phone model's tool calls by hand.

Behaviour copied from live calls on 2026-10-10: the engine runs a post-hook only
after a 2xx answer, any other status reaches the model as a bare error with no
hook; the bank answers 404 for a customer it does not know and 200 with ok false
for an action it refuses.

Tool calls of one model message (`together`), as seen in three live calls:
- They can run side by side. Each call then has its own copy of the variables,
  taken before any of them started; its pre-hook and its post-hook share that
  copy and see nothing the other calls wrote.
- Afterwards the copies are merged into the call's variables. A variable only
  one call wrote keeps that value. For a variable several calls wrote, one
  write survives; in the two calls where it could be told, it was the write of
  the call that came last in the message, not of the call that finished last.
- Not known: whether a call writes back only what it changed or every variable
  of its copy, stale values included. Both are modelled (`merge`), and so are
  both orders (`apply`). A test of same-message behaviour has to hold in all four.
- The order in which the calls reach the bank is not the order written (`arrival`).
- In one live call two calls of one message ran one after the other, the second
  seeing what the first had written. That is the same as two `tool` calls.
- In another live call (M14 Brief B, repetition 2) two calls of the same tool that
  started within one millisecond were NOT isolated: both post-hooks read the
  arguments the second pre-hook had written. That is `hooks='shared'`: every
  pre-hook runs on the call's variables in the order written, then every
  post-hook. Whether hooks interleave in any other way is not known.
- Why the engine isolated the calls in one case and not in the other is not known.
  A test of same-message behaviour has to state which pattern it plays.

Assumed from the kit's README, not yet seen live: an unknown product id is one of
the bank's refusals.
"""
import copy
import json


class Bank:
    def __init__(self, customer_id, identity, products, faults=None):
        self.customer_id, self.identity, self.faults = customer_id, identity, dict(faults or {})
        self.products = [dict(p, product_id='p%d' % (i + 1)) for i, p in enumerate(products)]
        self.wrong_left, self.passed, self.calls = 3, set(), []
        self.ledger = {'promises': [], 'notes': [], 'callbacks': [], 'transfers': [], 'outcome': None, 'reason': None}

    def call(self, tool, body):
        if body.get('customer_id') != self.customer_id:
            return 404, {'ok': False, 'error': 'unknown customer'}
        if self.faults.get(tool, 0) > 0:
            self.faults[tool] -= 1
            self.calls.append((tool, body, 500))
            return 500, None
        if self.ledger['outcome'] is not None:
            out = {'ok': False, 'error': 'the call is already closed'}
        elif 'product_id' in body and body['product_id'] not in [p['product_id'] for p in self.products]:
            out = {'ok': False, 'error': 'unknown product_id'}
        else:
            out = getattr(self, tool)(body)
        self.calls.append((tool, body, out))
        return 200, out

    def verify_identity(self, b):
        if self.wrong_left <= 0:
            return {'ok': False, 'error': 'check ended'}
        field, answer = b['field'], b['answer']
        if field == 'date_of_birth':
            parts = str(answer).split('-')
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                return {'ok': False, 'error': 'unreadable date'}
            y, m, d = parts
            if (y, d, m) == tuple(self.identity[field].split('-')) and m != d:
                return {'ok': True, 'verified': False, 'hint': 'day_month_swapped', 'wrong_answers_left': self.wrong_left}
        if answer == self.identity[field]:
            self.passed.add(field)
            return {'ok': True, 'verified': True, 'wrong_answers_left': self.wrong_left, 'all_verified': self.passed == set(self.identity)}
        self.wrong_left -= 1
        return {'ok': True, 'verified': False, 'wrong_answers_left': self.wrong_left, 'all_verified': False}

    def get_account(self, b):
        if self.passed != set(self.identity):
            return {'ok': False, 'error': 'identity not verified'}
        return {'ok': True, 'products': copy.deepcopy(self.products)}

    def create_promise(self, b):
        self.ledger['promises'].append({'product_id': b['product_id'], 'amount': b['amount'], 'date': b['date']})
        return {'ok': True, 'promise_id': 'pr1'}

    def add_note(self, b):
        self.ledger['notes'].append({'product_id': b['product_id'], 'reason': b['reason']})
        return {'ok': True}

    def schedule_callback(self, b):
        self.ledger['callbacks'].append({'date': b['date'], 'time': b['time']})
        return {'ok': True}

    def transfer_to_agent(self, b):
        self.ledger['transfers'].append({'queue': b['queue']})
        return {'ok': True}

    def close_call(self, b):
        self.ledger['outcome'], self.ledger['reason'] = b['outcome'], b.get('reason')
        return {'ok': True}


def run(code, **names):
    exec(compile(code, '<freya>', 'exec'), {'__builtins__': __builtins__}, names)


class Sim:
    def __init__(self, workflow, bank, variables):
        self.wf, self.bank = workflow, bank
        self.vars = dict(variables, customer_id=bank.customer_id)
        self.node = workflow['initial_node']
        self.advance()

    def advance(self):
        """Run passthrough nodes and follow deterministic transitions until a conversation or end node."""
        for _ in range(20):
            node = self.wf['nodes'][self.node]
            for action in node.get('pre_actions') or []:
                fn = action['function']
                if fn['handler'].endswith('code_exec'):
                    run(fn['parameters']['properties']['code']['default'], vars=self.vars)
                else:
                    result = self._api(action, {})
                    for m in node.get('response_mapping') or []:
                        value = result
                        for part in m['path'].split('.'):
                            value = value.get(part) if isinstance(value, dict) else None
                        if value is not None:
                            self.vars[m['variable']] = value
            target = None
            for t in node.get('transitions') or []:
                cond = t['condition']
                if cond.get('operator') == 'always' or any(str(self.vars.get(c['variable'])) == str(c['operand']) for c in cond.get('conditions', [])):
                    target = t['target_node']
                    break
            if target is None or (node['node_type'] == 'conversation' and target == self.node):
                return
            self.node = target
            if self.wf['nodes'][target]['node_type'] == 'end_call':
                return

    def _request(self, tool, args, variables=None):
        variables = self.vars if variables is None else variables
        props = tool['function']['parameters']['properties']
        body = {}
        for name, leaf in props['body']['properties'].items():
            key = leaf['default'].strip('{}')
            if key in variables:
                body[name] = variables[key]
            elif name in args:
                body[name] = args[name]
        config = {'url': props['url']['default'], 'method': 'POST', 'headers': {}, 'body': body}
        if tool.get('pre_process_code'):
            run(tool['pre_process_code'], vars=variables, config=config)
        return config

    def _respond(self, tool, config, variables=None):
        variables = self.vars if variables is None else variables
        status, data = self.bank.call(config['url'].rsplit('/', 1)[-1], config['body'])
        if status != 200:
            return {'status': 'error', 'error': 'API request failed: ' + repr(data)}
        result = {'status': 'success', 'data': data, 'status_code': status,
                  'message': 'x', 'header': {'Content-Type': 'application/json'}}
        if tool.get('post_process_code'):
            run(tool['post_process_code'], vars=variables, result=result)
        name = tool['function']['name']
        if isinstance(result.get('data'), dict):
            for key, value in result['data'].items():
                variables[name + '_response.data.' + key] = value
        json.dumps(result)
        return result

    def _api(self, tool, args):
        return self._respond(tool, self._request(tool, args))

    def _find(self, name):
        node = self.wf['nodes'][self.node]
        found = [t for t in node.get('tools') or [] if t['function']['name'] == name]
        if not found:
            raise LookupError(name + ' is not available in node ' + self.node)
        return found[0]

    def _new_message(self):
        """A model turn lies between two messages. Kept for callers; the runtime no longer reads a clock."""

    def tool(self, name, **args):
        """The phone model calls a tool in the current conversation node."""
        result = self._api(self._find(name), args)
        self.advance()
        return result['data'] if isinstance(result.get('data'), dict) else {}

    def together(self, *calls, arrival='reversed', merge='all', apply='message', hooks='isolated'):
        """The phone model puts several tool calls into one message and the engine runs them side by side.

        hooks    'isolated': each call on its own copy of the variables (seen live on 2026-10-10, M13);
                 'shared': all hooks on the same variables, pre-hooks first (seen live the same day, M14)
        arrival  the order in which the requests reach the bank: 'written', 'reversed' or a list of positions
        merge    'all': a call writes back every variable of its copy; 'changed': only those it changed
        apply    whose write survives for a shared variable: the last call of the 'message', or the last 'completion'
        """
        start = copy.deepcopy(self.vars)
        runs = []
        for name, args in calls:
            tool = self._find(name)
            local = self.vars if hooks == 'shared' else copy.deepcopy(start)
            runs.append([tool, self._request(tool, args, local), local, None])
        order = list(range(len(runs)))
        if arrival == 'reversed':
            order.reverse()
        elif arrival != 'written':
            order = list(arrival)
        if hooks == 'shared':
            for index in order:
                runs[index][3] = self._respond(runs[index][0], runs[index][1], self.vars)
            self.advance()
            return [r[3]['data'] if isinstance(r[3].get('data'), dict) else {} for r in runs]
        for index in order:
            tool, config, local, _ = runs[index]
            runs[index][3] = self._respond(tool, config, local)
        for index in (order if apply == 'completion' else range(len(runs))):
            for key, value in runs[index][2].items():
                if merge == 'all' or key not in start or start[key] != value:
                    self.vars[key] = value
        self.advance()
        return [r[3]['data'] if isinstance(r[3].get('data'), dict) else {} for r in runs]

    def prompt(self):
        text = self.wf['nodes'][self.node].get('task_message', '')
        for key, value in self.vars.items():
            text = text.replace('{{' + key + '}}', str(value))
        return text
