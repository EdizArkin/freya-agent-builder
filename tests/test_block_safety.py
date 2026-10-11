"""A call the policy refuses must not change anything at the bank, whatever the fields of the tool API are called.

Every place where the runtime blocks a call is played under several tool APIs (the kit's names, single
fields renamed, every field renamed, random names) and against two banks. The contract bank refuses a
call it does not know and a product it did not issue. The permissive bank carries out anything that
still names the real call and checks nothing else. Every block detaches the request, so no block may
depend on which of the two it meets: the criterion is no change at either bank, at every site.
"""
import ast
import copy
import json
import unittest

from benchmarks.harness.rules import MappedBank
from builder.compiler.compiler import runtime_cfg
from builder.pipeline import build_workflow
from builder.runtime import runtime as rt
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation import contract
from builder.validation.spec import validate
from tests.fixture_spec import spec as fixture
from tests.renamed import CALL, PLACEHOLDER, VARIANTS, PermissiveBank, Renamed, snapshot
from tests.support import OPENAPI


def changed(spec=None, **parts):
    out = copy.deepcopy(spec or fixture(True))
    for path, value in parts.items():
        node = out
        keys = path.split('__')
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return out


def with_value(name, expr, limit):
    spec = fixture(True)
    spec['account']['values'].append({'name': name, 'label': name.upper(), 'expr': expr})
    spec['account']['limits'][limit] = name
    return spec


V = 'verify'
PROMISE = dict(product_id='p1', amount=500, date='2026-10-15')
# name, spec (None: the fixture), bank faults, calls before, the call that must be blocked
SITES = [
    ('callback outside the window', None, {}, [], ('schedule_callback', dict(date='2026-10-20', time='03:00'))),
    ('second callback', None, {}, [('schedule_callback', dict(date='2026-10-14', time='15:00'))], ('schedule_callback', dict(date='2026-10-14', time='16:00'))),
    ('close with an outcome that does not exist', None, {}, [], ('close_call', dict(outcome='done'))),
    ('close without the reason its outcome needs', None, {}, [], ('close_call', dict(outcome='not_reached'))),
    ('transfer to a queue the tool API does not list', None, {}, [], ('transfer_to_agent', dict(queue='billing'))),
    ('security answer after the brief ended the check', changed(identity__max_wrong=2), {},
     [('verify_identity', dict(field='phone_last4', answer='0000')), ('verify_identity', dict(field='phone_last4', answer='0001'))],
     ('verify_identity', dict(field='phone_last4', answer='4821'))),
    ('promise for an unknown product', None, {}, [V], ('create_promise', dict(product_id='p9', amount=500, date='2026-10-15'))),
    ('promise below the floor', None, {}, [V], ('create_promise', dict(product_id='p1', amount=10, date='2026-10-15'))),
    ('promise above the amount due', None, {}, [V], ('create_promise', dict(product_id='p1', amount=9999, date='2026-10-15'))),
    ('promise in the past', None, {}, [V], ('create_promise', dict(product_id='p1', amount=500, date='2026-10-13'))),
    ('promise after the deadline', None, {}, [V], ('create_promise', dict(product_id='p1', amount=500, date='2026-12-01'))),
    ('promise on a day that is not a business day', None, {}, [V], ('create_promise', dict(product_id='p1', amount=500, date='2026-10-17'))),
    ('promise with a date that cannot be read', None, {}, [V], ('create_promise', dict(product_id='p1', amount=500, date='tomorrow'))),
    ('promise without an amount', None, {}, [V], ('create_promise', dict(product_id='p1', amount='a lot', date='2026-10-15'))),
    ('second promise for a product', None, {}, [V, ('create_promise', PROMISE)], ('create_promise', dict(product_id='p1', amount=400, date='2026-10-16'))),
    ('promise after a transfer', None, {}, [V, ('transfer_to_agent', dict(queue='support'))], ('create_promise', PROMISE)),
    ('promise after the attempts are used up', None, {'create_promise': 2}, [V, ('create_promise', PROMISE), ('create_promise', PROMISE)], ('create_promise', PROMISE)),
    ('second note for a product', None, {}, [V, ('add_note', dict(product_id='p2', reason='refused'))], ('add_note', dict(product_id='p2', reason='paid'))),
    ('note for a product with a promise', None, {}, [V, ('create_promise', PROMISE)], ('add_note', dict(product_id='p1', reason='refused'))),
    ('note after a transfer', None, {}, [V, ('transfer_to_agent', dict(queue='support'))], ('add_note', dict(product_id='p2', reason='refused'))),
    ('note with a reason the tool API does not list', None, {}, [V], ('add_note', dict(product_id='p2', reason='forgot'))),
    ('close while a product has neither a promise nor a note', None, {}, [V, ('create_promise', PROMISE)], ('close_call', dict(outcome='promise'))),
    # the rule kinds added in M15
    ('promise before the earliest date', with_value('earliest', {'kind': 'calendar_days_after', 'n': 1}, 'earliest_date'), {}, [V],
     ('create_promise', dict(product_id='p1', amount=500, date='2026-10-14'))),
    ('promise for a product that may not have one',
     with_value('eligible', {'kind': 'by', 'cases': [{'when': {'type': 'personal_loan'}, 'expr': {'kind': 'const', 'value': False}}, {'when': {}, 'expr': {'kind': 'const', 'value': True}}]}, 'promise_allowed'),
     {}, [V], ('create_promise', dict(product_id='p2', amount=900, date='2026-10-15'))),
    ('promise while a product that comes first is open', changed(promise__resolve_first=[{'type': 'personal_loan'}]), {}, [V], ('create_promise', PROMISE)),
    ('promise that leaves the required total out of reach', changed(promise__min_total={'percent': 90, 'of': 'full'}), {},
     [V, ('add_note', dict(product_id='p2', reason='refused'))], ('create_promise', PROMISE)),
    ('second transfer', changed(transfer__max_transfers=1), {}, [('transfer_to_agent', dict(queue='support'))], ('transfer_to_agent', dict(queue='general'))),
    # the rule kinds added in M16
    ('promise that is not a multiple of the step', changed(promise__amount_step=50), {}, [V], ('create_promise', dict(product_id='p1', amount=120, date='2026-10-15'))),
    ('promise for today below the amount today needs', changed(promise__min_amount_by_date=[{'on_or_before': 'today', 'min_amount': 'full'}]), {}, [V],
     ('create_promise', dict(product_id='p1', amount=400, date='2026-10-14'))),
    ('first promise for a product further down the list', changed(promise__same_date=True), {}, [V], ('create_promise', dict(product_id='p2', amount=900, date='2026-10-15'))),
    ('promise with another date than the one recorded', changed(promise__same_date=True), {}, [V, ('create_promise', PROMISE)],
     ('create_promise', dict(product_id='p2', amount=900, date='2026-10-16'))),
    ('promise when the recorded date is too late for this product', changed(promise__same_date=True), {},
     [V, ('create_promise', dict(product_id='p1', amount=500, date='2026-10-27'))], ('create_promise', dict(product_id='p2', amount=900, date='2026-10-22'))),
    ('callback in a closed period', changed(callback__closed=[{'from': '12:30', 'until': '13:30'}]), {}, [], ('schedule_callback', dict(date='2026-10-14', time='12:45'))),
]


_APIS = {}


def play(variant, site, bank):
    """Play one site. Returns (how the blocked request left, did the bank change, the request, the tool result)."""
    name, spec, faults, before, blocked = site
    key = (variant, name if spec is not None else None)
    if key not in _APIS:
        _APIS[key] = Renamed(*VARIANTS[variant], spec=spec)       # compiled once per tool API and spec
    api = _APIS[key]
    sim, held = api.start(bank, faults)
    for step in before:
        api.verify(sim) if step == V else api.call(sim, step[0], **step[1])
    was, sent = snapshot(held), len(sim.requests)
    data = api.call(sim, blocked[0], **blocked[1])
    # The request that was refused is the last one: for a promise, a check that passed may have gone out before it.
    assert len(sim.requests) > sent
    request = sim.requests[-1][1]
    body = request['body']
    how = 'detached' if body.get(api.r('customer_id')) == PLACEHOLDER else 'product replaced' if PLACEHOLDER in body.values() else 'sent'
    return how, snapshot(held) != was, request, data


class Blocks(unittest.TestCase):
    def test_no_blocked_call_changes_anything_at_either_bank(self):
        self.assertEqual((len(SITES), len(VARIANTS)), (33, 8))
        played = 0
        for bank in (MappedBank, PermissiveBank):
            for variant in VARIANTS:
                for site in SITES:
                    how, mutated, request, data = play(variant, site, bank)
                    played += 1
                    self.assertFalse(mutated, (bank.__name__, variant, site[0]))
                    self.assertEqual(how, 'detached', (bank.__name__, variant, site[0]))
                    self.assertNotIn(CALL, json.dumps(request), 'no value of the call is left in a blocked request')
                    self.assertEqual(data, {}, 'a blocked call has no answer of the bank to pass on')
        self.assertEqual(played, 2 * 8 * 33)

    def test_the_reason_of_every_block_is_in_front_of_the_model_without_asking_the_bank(self):
        for site in SITES:
            name, spec, faults, before, blocked = site
            api = Renamed(*VARIANTS['every field renamed'], spec=spec)
            sim, bank = api.start(PermissiveBank, faults)
            for step in before:
                api.verify(sim) if step == V else api.call(sim, step[0], **step[1])
            api.call(sim, blocked[0], **blocked[1])
            self.assertIn('LAST REJECTED TOOL CALL: ', sim.vars['rt_live'], name)
            self.assertIn('LAST REJECTED TOOL CALL', sim.prompt(), name)

    def test_no_request_keeps_its_call_and_names_a_made_up_product(self):
        with open(rt.__file__, encoding='utf-8') as source:
            text = source.read()
        self.assertEqual(text.count("'blocked_by_policy'"), 3, 'the placeholder is written to call fields only, in block()')
        self.assertNotIn('body[field]', text)

    def test_every_place_where_the_runtime_blocks_is_played_here(self):
        with open(rt.__file__, encoding='utf-8') as source:
            tree = ast.parse(source.read())
        sites = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ('block', 'refuse_close', 'fail')]
        # 41 places where the runtime refuses: calls of block or refuse_close, and since M18 of fail, which holds the
        # 22 refusals of a promise that check_promise and create_promise share. A new one needs a site above; then
        # raise this number. Two have no site above because no valid spec and no listed product reaches them: a
        # product whose limits cannot be worked out, and an amount step that cannot be read;
        # tests/test_enforcement_m16.py plays both. The refusals of the confirmation itself are not calls of these
        # functions: tests/test_m18_confirmation.py plays each of them.
        self.assertEqual(len(sites), 41, 'a refusal was added to or removed from the runtime: add or remove its safety test')

    def test_two_fields_that_tie_a_request_to_the_call_are_both_replaced(self):
        api = Renamed({'customer_id': 'caller_ref'}, {}, second_call_field=True)
        self.assertEqual(runtime_cfg(api.spec, Api(api.document))['customer_fields'], ['caller_ref', 'session_id'])
        sim, bank = api.start(PermissiveBank)
        was, sent = snapshot(bank), len(sim.requests)
        api.call(sim, 'schedule_callback', date='2026-10-20', time='03:00')
        body = sim.requests[sent][1]['body']
        self.assertEqual((body['caller_ref'], body['session_id'], snapshot(bank)), (PLACEHOLDER, PLACEHOLDER, was))

    def test_a_block_that_finds_no_call_field_trusts_nothing_in_the_request(self):
        cfg = runtime_cfg(fixture(), Api(OPENAPI))
        variables = {'now': '2026-10-14T11:00'}
        rt.setup(cfg, variables)
        config = {'body': {'date': '2026-10-20', 'time': '03:00', 'someone': CALL}}
        rt.pre_callback(cfg, variables, config)
        self.assertEqual(set(config['body'].values()), {PLACEHOLDER})
        self.assertEqual(config['body']['customer_id'], PLACEHOLDER)
        config = {'body': 'customer_id=' + CALL}
        rt.pre_callback(cfg, variables, config)
        self.assertEqual(config['body'], {'customer_id': PLACEHOLDER}, 'a body that is not an object is replaced, not sent')


class ValidCalls(unittest.TestCase):
    def test_a_compliant_call_ends_with_the_right_records_under_every_naming(self):
        for variant in VARIANTS:
            api = Renamed(*VARIANTS[variant])
            sim, bank = api.start()
            api.verify(sim)
            self.assertEqual(sim.node, 'negotiate', variant)
            said = api.call(sim, 'create_promise', product_id='p1', amount='500', date='2026-10-15')
            self.assertIn('is now recorded', said['what_to_do_next'], variant)
            api.call(sim, 'add_note', product_id='p2', reason='refused')
            api.call(sim, 'close_call', outcome='promise')
            self.assertEqual(bank.ledger, {'promises': [{'product_id': 'p1', 'amount': 500.0, 'date': '2026-10-15'}], 'notes': [{'product_id': 'p2', 'reason': 'refused'}],
                                           'callbacks': [], 'transfers': [], 'outcome': 'promise', 'reason': None}, variant)
            self.assertEqual(sim.node, 'end', variant)
            real = Api(api.document)
            for tool, request in sim.requests:
                self.assertEqual(contract.check_request(real, tool, request), [], (variant, tool))

    def test_a_callback_and_a_transfer_go_through_under_every_naming(self):
        for variant in VARIANTS:
            api = Renamed(*VARIANTS[variant])
            sim, bank = api.start()
            api.call(sim, 'schedule_callback', date='2026-10-15', time='10:00')
            api.call(sim, 'close_call', outcome='not_reached', reason='no_callback')
            self.assertEqual((bank.ledger['callbacks'], bank.ledger['outcome'], bank.ledger['reason']), ([{'date': '2026-10-15', 'time': '10:00'}], 'not_reached', 'callback'), variant)
            sim, bank = api.start()
            api.call(sim, 'transfer_to_agent', queue='general')
            api.call(sim, 'close_call', outcome='promise')
            self.assertEqual((bank.ledger['transfers'], bank.ledger['outcome'], bank.ledger['reason']), ([{'queue': 'general'}], 'transferred', 'human'), variant)

    def test_what_can_be_put_right_in_a_request_is_put_right_before_it_leaves(self):
        api = Renamed()
        sim, bank = api.start()
        api.verify(sim)
        api.call(sim, 'create_promise', product_id='p1', amount='500.00', date='2026-10-15T09:00')
        body = [r for t, r in sim.requests if t == 'create_promise'][0]['body']
        self.assertEqual((body['amount'], body['date']), (500.0, '2026-10-15'))
        self.assertEqual(contract.check_request(Api(api.document), 'create_promise', [r for t, r in sim.requests if t == 'create_promise'][0]), [])
        cfg = runtime_cfg(api.spec, Api(api.document))
        body = {'customer_id': 'c', 'queue': 'support', 'note': 'x'}
        self.assertEqual((rt.shape_problem(cfg, 'transfer', body), body), ('', {'customer_id': 'c', 'queue': 'support'}))
        self.assertIn('is not allowed for queue', rt.shape_problem(cfg, 'transfer', {'customer_id': 'c', 'queue': 'billing'}))
        self.assertEqual(rt.shape_problem(cfg, 'transfer', {'queue': 'support'}), 'the value customer_id is missing')
        self.assertIn('must be a date', rt.shape_problem(cfg, 'callback', {'customer_id': 'c', 'date': '15.10.2026', 'time': '10:00'}))
        body = {'customer_id': 'c', 'field': 'phone_last4', 'answer': 4821}
        self.assertEqual((rt.shape_problem(cfg, 'identity', body), body['answer']), ('', '4821'))


class SuccessFlag(unittest.TestCase):
    def test_the_flag_is_read_from_the_tool_api_in_both_directions(self):
        api = Renamed({}, {'ok': 'done'})
        self.assertEqual(runtime_cfg(api.spec, Api(api.document))['ok_field'], 'done')
        sim, bank = api.start()
        api.verify(sim)
        bank.create_promise = lambda body: {'ok': False, 'error': 'not today'}
        said = api.call(sim, 'create_promise', product_id='p1', amount=500, date='2026-10-15')
        self.assertIn('NOT RECORDED', said['what_to_do_next'], 'an answer that says no is never taken for a recorded promise')
        self.assertEqual(json.loads(sim.vars['rt_state'])['promises'], {})
        del bank.create_promise
        said = api.call(sim, 'create_promise', product_id='p1', amount=500, date='2026-10-15')
        self.assertIn('is now recorded', said['what_to_do_next'], 'an answer that says yes is never reported as a failure')
        self.assertEqual(list(json.loads(sim.vars['rt_state'])['promises']), ['p1'])


class Refused(unittest.TestCase):
    """A tool API under which a block could not be relied on is not built at all."""

    def problems(self, doc, spec=None, best_effort=True):
        spec = spec or fixture(True)
        with self.assertRaises(SpecError) as ctx:
            build_workflow(spec, doc, best_effort)
        with self.assertRaises(SpecError):
            validate(spec, Api(doc), best_effort)
        return ' | '.join(ctx.exception.problems)

    def body(self, doc, tool):
        return doc['paths']['/' + tool]['post']['requestBody']['content']['application/json']['schema']

    def test_the_call_outside_the_body(self):
        doc = copy.deepcopy(OPENAPI)
        doc['paths']['/calls/{customer_id}/close_call'] = doc['paths'].pop('/close_call')
        doc['paths']['/calls/{customer_id}/close_call']['post']['operationId'] = 'close_call'
        self.assertIn('a policy block cannot be guaranteed for close_call: the request carries a path template', self.problems(doc))
        for place in ('header', 'query'):
            doc = copy.deepcopy(OPENAPI)
            doc['paths']['/create_promise']['post']['parameters'] = [{'name': 'X-Call', 'in': place}]
            self.assertIn('a policy block cannot be guaranteed for create_promise: the request carries %s parameter X-Call' % place, self.problems(doc))
        doc = copy.deepcopy(OPENAPI)
        doc['paths']['/add_note']['post']['requestBody']['content'] = {'application/x-www-form-urlencoded': doc['paths']['/add_note']['post']['requestBody']['content']['application/json']}
        self.assertIn('a policy block cannot be guaranteed for add_note: the request carries a body that is not JSON', self.problems(doc))

    def test_a_call_field_that_cannot_carry_a_block(self):
        doc = copy.deepcopy(OPENAPI)
        self.body(doc, 'schedule_callback')['required'].remove('customer_id')
        self.assertIn('for schedule_callback: none of its call fields (customer_id) is a required free-text field', self.problems(doc))
        doc = copy.deepcopy(OPENAPI)
        self.body(doc, 'transfer_to_agent')['properties']['customer_id'] = {'type': 'integer'}
        self.assertIn('for transfer_to_agent: none of its call fields', self.problems(doc))
        renamed = Renamed({'customer_id': 'caller_ref'}, {})
        text = self.problems(renamed.document, fixture(True))      # the spec still says customer_id
        self.assertIn('for close_call: no body field is filled from a call variable', text)

    def test_no_flag_or_more_than_one(self):
        doc = copy.deepcopy(OPENAPI)
        props = doc['paths']['/add_note']['post']['responses']['200']['content']['application/json']['schema']['properties']
        props['saved'] = props.pop('ok')
        spec = fixture(True)
        spec.pop('ok_field')
        self.assertIn('no single true/false field that every answer carries (found: none)', self.problems(doc, spec))
        self.assertIn("ok_field='ok' is not a true/false field of every answer", self.problems(doc))
        doc = copy.deepcopy(OPENAPI)
        for item in doc['paths'].values():
            item['post']['responses']['200']['content']['application/json']['schema']['properties']['accepted'] = {'type': 'boolean'}
        self.assertIn('found: accepted, ok', self.problems(doc, spec))
        build_workflow(fixture(True), doc)       # a spec that names the flag settles it

    def test_the_kit_api_is_read_as_before(self):
        cfg = runtime_cfg(fixture(True), Api(OPENAPI))
        self.assertEqual((cfg['ok_field'], cfg['customer_fields']), ('ok', ['customer_id']))
        self.assertNotIn('soft_block', cfg)
        self.assertEqual(Api(OPENAPI).success_flags(), ['ok'])


if __name__ == '__main__':
    unittest.main()
