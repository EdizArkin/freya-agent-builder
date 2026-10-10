"""Test helpers: the kit's tool API and the fixture spec under other field names, and two stand-in banks.

A contract bank refuses a call it does not know and a product it did not issue. A permissive bank
carries out anything that still names the real call and checks nothing else: against it, only a
block that removes the call from the request holds.
"""
import collections
import copy
import json
import random

from benchmarks.harness.rules import MappedBank, Recorded
from builder.pipeline import build_workflow
from tests.fixture_spec import spec as fixture
from tests.support import OPENAPI

CALL = 'c_real_call'
PLACEHOLDER = 'blocked_by_policy'
REQUEST = ('customer_id', 'field', 'answer', 'product_id', 'amount', 'date', 'time', 'queue', 'outcome', 'reason')
ALL_REQUEST = dict(customer_id='caller_ref', field='question', answer='given', product_id='account_ref', amount='pay_amount', date='pay_date',
                   time='slot', queue='team', outcome='result', reason='code')
ALL_RESPONSE = dict(ok='done', verified='match', all_verified='all_passed', wrong_answers_left='tries_left', hint='clue', products='accounts',
                    product_id='account_ref', promise_id='ref')
IDENTITY = {'date_of_birth': '1980-05-17', 'phone_last4': '4821'}
PRODUCTS = [{'type': 'credit_card', 'card_last4': '4821', 'past_due': 500.0, 'days_late': 5, 'minimum_payment': 50.0},
            {'type': 'personal_loan', 'past_due': 900.0, 'days_late': 20, 'instalment_amount': 300.0}]
VARIABLES = {'now': '2026-10-14T11:00', 'title': 'Ms', 'first_name': 'Ada', 'last_name': 'Okafor'}
MUTATIONS = ('create_promise', 'add_note', 'schedule_callback', 'transfer_to_agent', 'close_call')


def random_names(seed):
    rnd = random.Random(seed)
    word = lambda: 'f_' + ''.join(rnd.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(7))
    request = {name: word() for name in REQUEST}
    response = {name: word() for name in ALL_RESPONSE}
    response['product_id'] = request['product_id']
    return request, response


VARIANTS = collections.OrderedDict([
    ('the kit\'s names', ({}, {})),
    ('call id renamed', ({'customer_id': 'caller_ref'}, {})),
    ('product field renamed', ({'product_id': 'account_ref'}, {'product_id': 'account_ref'})),
    ('success flag renamed', ({}, {'ok': 'done'})),
    ('every field renamed', (ALL_REQUEST, ALL_RESPONSE)),
    ('random names 1', random_names(1)),
    ('random names 2', random_names(2)),
    # The request names the product with another word than the account list does: it is not the id the bank issued.
    ('product named differently in request and account', ({'product_id': 'target'}, {'product_id': 'account_ref'})),
])


class Renamed:
    """The kit's tool API and a spec for it, with request and response fields renamed."""

    def __init__(self, request=None, response=None, spec=None, second_call_field=False):
        self.request, self.response = dict(request or {}), dict(response or {})
        r, q = lambda n: self.request.get(n, n), lambda n: self.response.get(n, n)
        self.r = r
        doc = copy.deepcopy(OPENAPI)
        for item in doc['paths'].values():
            op = item['post']
            body = op['requestBody']['content']['application/json']['schema']
            body['properties'] = {r(k): v for k, v in body['properties'].items()}
            body['required'] = [r(k) for k in body.get('required') or []]
            if second_call_field:
                body['properties']['session_id'] = {'type': 'string'}
                body['required'].append('session_id')
            answer = op['responses']['200']['content']['application/json']['schema']
            answer['properties'] = {q(k): v for k, v in answer['properties'].items()}
            products = answer['properties'].get(q('products'))
            if products:
                products['items']['properties'] = {q(k) if k == 'product_id' else k: v for k, v in products['items']['properties'].items()}
        s = copy.deepcopy(spec if spec is not None else fixture(True))
        s.pop('ok_field', None)                 # the builder has to find the flag in the tool API
        s['call_variables'] = [r(v) if v == 'customer_id' else v for v in s['call_variables']] + (['session_id'] if second_call_field else [])
        idc = s['identity']
        idc.update(field_param=r('field'), answer_param=r('answer'), response={k: q(v) for k, v in idc['response'].items()})
        s['account'].update(products_path=q('products'), id_field=q('product_id'))
        s['promise'].update(product_param=r('product_id'), amount_param=r('amount'), date_param=r('date'))
        if s.get('note'):
            s['note'].update(product_param=r('product_id'), reason_param=r('reason'))
        if s.get('callback'):
            s['callback'].update(date_param=r('date'), time_param=r('time'))
        s['transfer']['queue_param'] = r('queue')
        s['close'].update(outcome_param=r('outcome'), reason_param=r('reason'))
        self.document, self.spec, self.second = doc, s, second_call_field
        self.names = {'request': {new: old for old, new in self.request.items()}, 'response': self.response}
        self._workflow = None

    @property
    def workflow(self):
        if self._workflow is None:
            self._workflow = build_workflow(self.spec, self.document)[0]
        return self._workflow

    def args(self, **canonical):
        return {self.r(k): v for k, v in canonical.items()}

    def start(self, bank=MappedBank, faults=None, products=None, variables=None):
        held = bank(self.names, CALL, dict(IDENTITY), copy.deepcopy(products or PRODUCTS), dict(faults or {}))
        values = dict(VARIABLES, **(variables or {}))
        values[self.r('customer_id')] = CALL
        if self.second:
            values['session_id'] = 'sess_real'
        return Recorded(copy.deepcopy(self.workflow), held, values), held

    def call(self, sim, tool, **canonical):
        return sim.call(tool, self.args(**canonical))

    def verify(self, sim):
        for field, answer in IDENTITY.items():
            self.call(sim, 'verify_identity', field=field, answer=answer)


class PermissiveBank(MappedBank):
    """Carries out every mutation that still names the real call. It knows no product, no window and no state."""

    def call(self, tool, body):
        if tool not in MUTATIONS:
            return MappedBank.call(self, tool, body)
        renamed = set(self.request.values())
        known = {self.request[k] if k in self.request else k: v for k, v in body.items() if k in self.request or k not in renamed}
        if known.get('customer_id') != self.customer_id:
            return 404, {'ok': False, 'error': 'unknown customer'}
        if self.faults.get(tool, 0) > 0:
            self.faults[tool] -= 1
            self.calls.append((tool, known, 500))
            return 500, None
        out = getattr(self, tool)(collections.defaultdict(lambda: None, known))
        self.calls.append((tool, known, out))
        return 200, self._named(out)


def snapshot(bank):
    """What the bank holds, and how many calls it carried out."""
    done = sum(1 for _, _, answer in bank.calls if isinstance(answer, dict) and answer.get('ok') is True)
    return json.dumps(bank.ledger, sort_keys=True), done
