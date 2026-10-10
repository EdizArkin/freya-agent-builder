"""Offline stand-in for the Freya engine and the bank mock: runs a compiled workflow's code and hooks.

It executes every snippet with separate globals and locals, the least forgiving
way a sandbox can call exec, and it plays the phone model's tool calls by hand.
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

    def _api(self, tool, args):
        props = tool['function']['parameters']['properties']
        body = {}
        for name, leaf in props['body']['properties'].items():
            key = leaf['default'].strip('{}')
            if key in self.vars:
                body[name] = self.vars[key]
            elif name in args:
                body[name] = args[name]
        config = {'url': props['url']['default'], 'method': 'POST', 'headers': {}, 'body': body}
        if tool.get('pre_process_code'):
            run(tool['pre_process_code'], vars=self.vars, config=config)
        status, data = self.bank.call(config['url'].rsplit('/', 1)[-1], config['body'])
        result = {'status': 'success' if status == 200 else 'error', 'data': data, 'status_code': status,
                  'message': 'x', 'header': {'Content-Type': 'application/json'}}
        if tool.get('post_process_code'):
            run(tool['post_process_code'], vars=self.vars, result=result)
        name = tool['function']['name']
        if isinstance(result.get('data'), dict):
            for key, value in result['data'].items():
                self.vars[name + '_response.data.' + key] = value
        json.dumps(result)
        return result

    def tool(self, name, **args):
        """The phone model calls a tool in the current conversation node."""
        node = self.wf['nodes'][self.node]
        found = [t for t in node.get('tools') or [] if t['function']['name'] == name]
        if not found:
            raise LookupError(name + ' is not available in node ' + self.node)
        result = self._api(found[0], args)
        self.advance()
        return result['data'] if isinstance(result.get('data'), dict) else {}

    def prompt(self):
        text = self.wf['nodes'][self.node].get('task_message', '')
        for key, value in self.vars.items():
            text = text.replace('{{' + key + '}}', str(value))
        return text
