"""The rule kinds agent-spec/1 gained in M15: each at its boundary, one step either side, absent, and invalid.

Seven of the nine are enforced. Two are enforced in part and are tested as such: a minimum on the total
of all promises (the tool API cannot take a promise back) and a promise after a transfer to a named
queue (whether it was confirmed before the transfer only a live call can show).
"""
import copy
import datetime
import json
import unittest

from builder.compiler.compiler import runtime_cfg
from builder.extraction import claude
from builder.runtime import runtime as rt
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import validate
from benchmarks.harness.fixture_spec import spec as fixture
from tests.renamed import PRODUCTS, Renamed
from tests.support import OPENAPI, refusal

API = Api(OPENAPI)
CONST = lambda value: {'kind': 'const', 'value': value}
CAL = {'business_weekdays': [0, 1, 2, 3, 4], 'holidays': [], 'week_start': 0}
WED = datetime.date(2026, 10, 14)


def spec_with(change, brief_b=True):
    spec = fixture(brief_b)
    change(spec)
    return spec


def value(spec, name, expr, limit=None):
    spec['account']['values'].append({'name': name, 'label': name.upper(), 'expr': expr})
    if limit:
        spec['account']['limits'][limit] = name


def problems(spec):
    with unittest.TestCase().assertRaises(SpecError) as ctx:
        validate(spec, API)
    return ' | '.join(ctx.exception.problems)


def call(spec, products=None, now=None):
    api = Renamed(spec=spec)
    sim, bank = api.start(products=products, variables={'now': now} if now else None)
    api.verify(sim)
    return api, sim, bank


def promise(api, sim, product='p1', amount=500, date='2026-10-15'):
    return api.call(sim, 'create_promise', product_id=product, amount=amount, date=date)


class AnswerForm(unittest.TestCase):
    def test_a_text_answer_is_sent_in_the_prescribed_form(self):
        def run(steps, said):
            spec = fixture()
            spec['identity']['questions'][1].update(format='text', **({'normalize': steps} if steps is not None else {}))
            cfg = runtime_cfg(spec, API)
            variables, config = {'now': '2026-10-14T11:00'}, {'body': {'customer_id': 'c', 'field': 'phone_last4', 'answer': said}}
            rt.setup(cfg, variables)
            rt.pre_identity(cfg, variables, config)
            return config['body']['answer']
        self.assertEqual(run(['uppercase', 'remove_spaces'], ' nw1 4rt '), 'NW14RT')
        self.assertEqual(run(['lowercase'], 'Ab Cd'), 'ab cd')
        self.assertEqual(run(['alphanumeric', 'uppercase'], 'nw1-4rt.'), 'NW14RT')
        self.assertEqual(run([], 'nw1 4rt'), 'nw1 4rt')
        self.assertEqual(run(None, ' nw1 4rt '), 'nw1 4rt', 'without the field an answer is only trimmed, as before')

    def test_an_unknown_step_is_refused(self):
        spec = fixture()
        spec['identity']['questions'][0]['normalize'] = ['titlecase']
        self.assertIn('identity question normalize may only list: uppercase, lowercase, remove_spaces, alphanumeric', problems(spec))


class EarliestDate(unittest.TestCase):
    tomorrow = spec_with(lambda s: value(s, 'earliest', {'kind': 'calendar_days_after', 'n': 1}, 'earliest_date'))

    def test_the_day_before_is_refused_and_the_day_itself_is_recorded(self):
        api, sim, bank = call(self.tomorrow)
        said = promise(api, sim, date='2026-10-14')
        self.assertIn('is before the earliest date that can be recorded for credit card ending 4821, which is Thu 2026-10-15', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])
        self.assertIn('is now recorded', promise(api, sim, date='2026-10-15')['what_to_do_next'])
        self.assertIn('earliest date Thu 2026-10-15', sim.vars['rt_account_facts'])

    def test_without_the_field_today_is_recorded_as_before(self):
        api, sim, bank = call(fixture(True))
        self.assertIn('is now recorded', promise(api, sim, date='2026-10-14')['what_to_do_next'])
        self.assertNotIn('earliest date', sim.vars['rt_account_facts'])

    def test_it_has_to_be_a_date_and_to_exist(self):
        self.assertIn("account.limits.earliest_date refers to undefined value 'soon'", problems(spec_with(lambda s: s['account']['limits'].update(earliest_date='soon'))))
        self.assertIn('account.limits.earliest_date must be a date', problems(spec_with(lambda s: s['account']['limits'].update(earliest_date='floor'))))


class TimeOfDay(unittest.TestCase):
    def expr(self, test):
        return {'kind': 'by_time', 'cases': [{'when': {test: '16:00'}, 'expr': {'kind': 'next_business_day'}}, {'when': {}, 'expr': {'kind': 'today'}}]}

    def at(self, test, clock):
        return rt.evaluate(self.expr(test), WED, dict(CAL, clock=clock), {}, {})

    def test_after_is_strict_and_from_includes_the_minute(self):
        tomorrow = datetime.date(2026, 10, 15)
        self.assertEqual([self.at('after', c) for c in ('15:59', '16:00', '16:01')], [WED, WED, tomorrow])
        self.assertEqual([self.at('from', c) for c in ('15:59', '16:00', '16:01')], [WED, tomorrow, tomorrow])
        self.assertEqual([self.at('before', c) for c in ('15:59', '16:00')], [tomorrow, WED])
        self.assertEqual([self.at('until', c) for c in ('16:00', '16:01')], [tomorrow, WED])
        with self.assertRaises(ValueError):
            rt.evaluate({'kind': 'by_time', 'cases': [{'when': {'after': '16:00'}, 'expr': {'kind': 'today'}}]}, WED, dict(CAL, clock='09:00'), {}, {})

    def test_the_clock_of_the_call_decides_whether_today_can_be_promised(self):
        spec = spec_with(lambda s: value(s, 'earliest', self.expr('after'), 'earliest_date'))
        for now, today_ok in (('2026-10-14T16:00', True), ('2026-10-14T16:01', False), ('2026-10-14T09:00', True)):
            api, sim, bank = call(spec, now=now)
            said = promise(api, sim, date='2026-10-14')
            self.assertEqual('is now recorded' in said.get('what_to_do_next', ''), today_ok, now)
            if not today_ok:
                self.assertIn('is now recorded', promise(api, sim, date='2026-10-15')['what_to_do_next'])

    def test_a_case_has_to_test_a_time(self):
        bad = self.expr('after')
        bad['cases'][0]['when'] = {'past': '4pm'}
        self.assertIn('a by_time case tests from, after, until, before with HH:MM', problems(spec_with(lambda s: value(s, 'earliest', bad, 'earliest_date'))))
        open_ended = {'kind': 'by_time', 'cases': [{'when': {'after': '16:00'}, 'expr': {'kind': 'today'}}]}
        self.assertIn('account value earliest cannot be computed', problems(spec_with(lambda s: value(s, 'earliest', open_ended, 'earliest_date'))),
                      'a time of day no case covers is found at build time')


class Eligibility(unittest.TestCase):
    loans_excluded = spec_with(lambda s: (value(s, 'eligible', {'kind': 'by', 'cases': [{'when': {'type': 'personal_loan'}, 'expr': CONST(False)}, {'when': {}, 'expr': CONST(True)}]}, 'promise_allowed'),
                                          s['guidance'].update({'promise.not_allowed': 'Call transfer_to_agent with queue general.'})))

    def test_a_product_that_may_not_have_a_promise_gets_none(self):
        api, sim, bank = call(self.loans_excluded)
        said = promise(api, sim, 'p2', 900)
        self.assertIn('no promise can be recorded for personal loan', refusal(sim, said))
        self.assertIn('Call transfer_to_agent with queue general.', refusal(sim, said), 'the brief\'s own instruction for that moment')
        self.assertIn('NO PROMISE CAN BE RECORDED for this product', sim.vars['rt_account_facts'])
        self.assertIn('is now recorded', promise(api, sim, 'p1')['what_to_do_next'], 'the other product is not affected')
        self.assertEqual([p['product_id'] for p in bank.ledger['promises']], ['p1'])

    def test_the_value_has_to_be_true_or_false(self):
        spec = spec_with(lambda s: s['account']['limits'].update(promise_allowed='band'))
        self.assertIn('account.limits.promise_allowed must be true or false', problems(spec))


class Order(unittest.TestCase):
    loan_first = spec_with(lambda s: s['promise'].update(resolve_first=[{'type': 'personal_loan'}]))

    def test_a_later_product_waits_until_the_first_has_a_promise(self):
        api, sim, bank = call(self.loan_first)
        said = promise(api, sim, 'p1')
        self.assertIn('has to wait. First record a promise or a note for: p2 "personal loan"', refusal(sim, said))
        promise(api, sim, 'p2', 900)
        self.assertIn('is now recorded', promise(api, sim, 'p1')['what_to_do_next'])
        self.assertEqual([p['product_id'] for p in bank.ledger['promises']], ['p2', 'p1'])

    def test_a_note_on_the_first_product_frees_the_later_one(self):
        api, sim, bank = call(self.loan_first)
        api.call(sim, 'add_note', product_id='p2', reason='refused')
        self.assertIn('is now recorded', promise(api, sim, 'p1')['what_to_do_next'])

    def test_products_of_the_same_group_do_not_wait_for_each_other(self):
        api, sim, bank = call(spec_with(lambda s: s['promise'].update(resolve_first=[{'days_late': {'min': 1}}])))
        self.assertIn('is now recorded', promise(api, sim, 'p1')['what_to_do_next'])

    def test_a_condition_on_an_unknown_field_is_refused(self):
        self.assertIn("promise.resolve_first tests unknown product field 'secured'", problems(spec_with(lambda s: s['promise'].update(resolve_first=[{'secured': True}]))))
        self.assertIn('promise.resolve_first must be a list of product conditions', problems(spec_with(lambda s: s['promise'].update(resolve_first={'type': 'x'}))))


class TotalOfAllPromises(unittest.TestCase):
    """Past due 500.00 and 900.00: 50 percent of 1400.00 is 700.00."""
    half = spec_with(lambda s: s['promise'].update(min_total={'percent': 50, 'of': 'full'}))

    def test_a_promise_is_refused_once_the_total_is_out_of_reach(self):
        api, sim, bank = call(self.half)
        self.assertIn('must add up to at least 700.00', sim.vars['rt_account_facts'])
        api.call(sim, 'add_note', product_id='p1', reason='refused')
        said = promise(api, sim, 'p2', 699.99)
        self.assertIn('must add up to at least 700.00, and with 699.99 for personal loan that can no longer be reached', refusal(sim, said))
        self.assertIn('is now recorded', promise(api, sim, 'p2', 700)['what_to_do_next'], 'exactly the total is enough')

    def test_a_promise_that_leaves_the_total_reachable_is_recorded(self):
        api, sim, bank = call(self.half)
        self.assertIn('is now recorded', promise(api, sim, 'p1', 50)['what_to_do_next'], '50.00 now, up to 900.00 still possible')
        said = promise(api, sim, 'p2', 649.99)
        self.assertIn('can no longer be reached', refusal(sim, said), '50.00 + 649.99 is one cent short and nothing else is open')
        self.assertIn('is now recorded', promise(api, sim, 'p2', 650)['what_to_do_next'])

    def test_all_or_none_is_not_enforced_and_the_agent_is_told_the_truth(self):
        # The part of the rule that cannot be kept: the first promise was allowed, the second product is then refused.
        # The tool API has no way to take the first promise back.
        api, sim, bank = call(self.half)
        promise(api, sim, 'p1', 50)
        api.call(sim, 'add_note', product_id='p2', reason='refused')
        self.assertEqual(bank.ledger['promises'], [{'product_id': 'p1', 'amount': 50.0, 'date': '2026-10-15'}], 'the promise stays at the bank')
        self.assertIn('THE RECORDED PROMISES ADD UP TO 50.00, LESS THAN THE REQUIRED 700.00. A recorded promise cannot be taken back', sim.vars['rt_live'])

    def test_a_fixed_amount_and_the_invalid_forms(self):
        api, sim, bank = call(spec_with(lambda s: s['promise'].update(min_total={'amount': 1000})))
        api.call(sim, 'add_note', product_id='p1', reason='refused')
        self.assertIn('at least 1000.00', refusal(sim, promise(api, sim, 'p2', 900)))
        for bad in ({'percent': 0, 'of': 'full'}, {'percent': 40, 'of': 'nothing'}, {'amount': -5}, 40):
            text = problems(spec_with(lambda s: s['promise'].update(min_total=bad)))
            self.assertIn('promise.min_total', text, bad)


class NoteAtTheClose(unittest.TestCase):
    def test_a_note_with_a_reason_decides_the_close_even_beside_a_promise(self):
        spec = spec_with(lambda s: s['close']['forced'].insert(0, {'if': 'note_recorded:dispute', 'outcome': 'no_promise', 'reason': 'dispute'}))
        api, sim, bank = call(spec)
        promise(api, sim, 'p1')
        api.call(sim, 'add_note', product_id='p2', reason='dispute')
        api.call(sim, 'close_call', outcome='promise')
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason'], len(bank.ledger['promises'])), ('no_promise', 'dispute', 1))
        api, sim, bank = call(spec)
        promise(api, sim, 'p1')
        api.call(sim, 'add_note', product_id='p2', reason='refused')
        api.call(sim, 'close_call', outcome='promise')
        self.assertEqual(bank.ledger['outcome'], 'promise', 'another reason does not trigger it')
        self.assertTrue(rt.flag_true({'notes': {'p1': 'paid'}}, 'note_recorded'))
        self.assertFalse(rt.flag_true({'notes': {}}, 'note_recorded:paid'))


class Transfers(unittest.TestCase):
    def test_one_transfer_and_no_second(self):
        api = Renamed(spec=spec_with(lambda s: s['transfer'].update(max_transfers=1)))
        sim, bank = api.start()
        api.call(sim, 'transfer_to_agent', queue='restructuring')
        api.call(sim, 'transfer_to_agent', queue='general')
        self.assertEqual(bank.ledger['transfers'], [{'queue': 'restructuring'}])
        self.assertIn('NOT TRANSFERRED: the call was already handed over to restructuring', sim.vars['rt_live'])
        api.call(sim, 'close_call', outcome='promise')
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('transferred', 'restructuring'), 'the refused transfer does not decide the close')

    def test_a_limit_of_two_and_no_limit(self):
        for limit, done in ((2, 2), (None, 3)):
            api = Renamed(spec=spec_with(lambda s: s['transfer'].update(max_transfers=limit) if limit else None))
            sim, bank = api.start()
            for queue in ('restructuring', 'general', 'support'):
                api.call(sim, 'transfer_to_agent', queue=queue)
            self.assertEqual(len(bank.ledger['transfers']), done, limit)
        for bad in (0, '1', 1.5):
            self.assertIn('transfer.max_transfers must be a whole number of at least 1', problems(spec_with(lambda s: s['transfer'].update(max_transfers=bad))))

    def test_a_promise_after_a_transfer_only_to_a_named_queue(self):
        spec = spec_with(lambda s: s['promise'].update(allowed_after_transfer_to=['restructuring']))
        api, sim, bank = call(spec)
        api.call(sim, 'transfer_to_agent', queue='restructuring')
        self.assertIn('is now recorded', promise(api, sim)['what_to_do_next'])
        api, sim, bank = call(spec)
        api.call(sim, 'transfer_to_agent', queue='support')
        self.assertIn('No promise may be recorded after a transfer', refusal(sim, promise(api, sim)))
        api, sim, bank = call(spec)
        api.call(sim, 'transfer_to_agent', queue='restructuring')
        api.call(sim, 'transfer_to_agent', queue='support')
        self.assertIn('after a transfer', refusal(sim, promise(api, sim)), 'one transfer to another queue is enough to forbid it')
        api, sim, bank = call(fixture(True))
        api.call(sim, 'transfer_to_agent', queue='restructuring')
        self.assertIn('after a transfer', refusal(sim, promise(api, sim)), 'without the field every transfer forbids it, as before')
        self.assertIn("promise.allowed_after_transfer_to names unknown queue 'plans'", problems(spec_with(lambda s: s['promise'].update(allowed_after_transfer_to=['plans']))))


class Unchanged(unittest.TestCase):
    def test_a_spec_without_the_new_fields_has_the_state_and_the_facts_it_had(self):
        api, sim, bank = call(fixture(True))
        state = json.loads(sim.vars['rt_state'])
        self.assertEqual(sorted(state['products'][0]), ['id', 'label', 'latest_date', 'max_amount', 'min_amount'])
        self.assertNotIn('min_total', state)
        for part in ('earliest date', 'NO PROMISE', 'ALL PROMISES OF THIS CALL'):
            self.assertNotIn(part, sim.vars['rt_account_facts'])
        self.assertEqual(validate(fixture(True), API), [])

    def test_the_prompt_teaches_every_new_field_and_holds_no_brief_value(self):
        for name in ('"normalize"', '"earliest_date"', '"promise_allowed"', '"resolve_first"', '"min_total"', '"allowed_after_transfer_to"', '"max_transfers"',
                     '"kind":"by_time"', 'note_recorded:<reason>', 'promise.not_allowed'):
            self.assertIn(name, claude.SYSTEM)
        for brief_value in ('plan_active', 'arrears', 'secured', 'dispute', 'plans', '16:00', 'postcode', 'vulnerable'):
            self.assertNotIn(brief_value, claude.SYSTEM)


if __name__ == '__main__':
    unittest.main()
