"""Behaviour that only showed in live calls on 2026-10-10 and the builder's answer to it:
no post-hook after an HTTP error, tool calls of one message out of order, misread weekdays."""
import datetime
import json
import re
import unittest

from builder.compiler.compiler import runtime_cfg
from builder.pipeline import build_workflow
from builder.runtime import runtime as rt
from builder.spec.openapi import Api
from tests.fixture_spec import spec as fixture
from tests.support import OPENAPI
from tests.test_enforcement import after_error, start as start_call, tools_called, verified
from tests.test_runtime import ledger, start as start_case, verify


CFG = runtime_cfg(fixture(True), Api(OPENAPI))


def state(sim):
    return json.loads(sim.vars['rt_state'])


class BlockedCalls(unittest.TestCase):
    def test_a_blocked_promise_is_refused_by_the_bank_and_explained_in_the_result(self):
        sim, bank, case = start_case('PUB-04')
        verify(sim, bank)
        late = sim.tool('create_promise', product_id='p1', amount=1250, date='2026-11-06')
        self.assertTrue(late['rejected_by_policy'])
        self.assertIn('is after the latest date', late['what_to_do_next'])
        self.assertIn('Fri 2026-10-23', late['what_to_do_next'])
        tool, body, answer = bank.calls[-1]
        self.assertEqual((tool, body['customer_id'], body['product_id'], answer['ok']),
                         ('create_promise', bank.customer_id, 'blocked_by_policy', False))
        self.assertEqual(bank.ledger['promises'], [])
        good = sim.tool('create_promise', product_id='p1', amount=1250, date='2026-10-23')
        self.assertIn('is now recorded', good['what_to_do_next'])
        sim.tool('close_call', outcome='no_promise', reason='service_error')
        self.assertEqual(ledger(bank), case['expected'])

    def test_an_unanswered_block_does_not_spoil_the_next_valid_call(self):
        sim, bank = start_call()
        self.assertEqual(sim.tool('schedule_callback', date='2026-10-17', time='11:00'), {})
        self.assertIn('LAST REJECTED TOOL CALL: NOT BOOKED', sim.vars['rt_live'])
        booked = sim.tool('schedule_callback', date='2026-10-15', time='11:00')
        self.assertNotIn('rejected_by_policy', booked)
        self.assertIn('The callback is booked', booked['what_to_do_next'])
        self.assertNotIn('LAST REJECTED', sim.vars['rt_live'])
        self.assertEqual(bank.ledger['callbacks'], [{'date': '2026-10-15', 'time': '11:00'}])

    def test_a_call_before_verification_never_carries_the_customer(self):
        """The refusal by the bank is used only on a verified call; before that the call is detached."""
        for st in ('', json.dumps({'identity': 'pending', 'products': []}), json.dumps({'identity': 'failed'})):
            config = {'body': {'customer_id': 'c_real', 'product_id': 'p1', 'amount': 420, 'date': '2026-10-14'}}
            rt.pre_promise(CFG, {'rt_state': st}, config)
            self.assertEqual(config['body']['customer_id'], 'blocked_by_policy')

    def test_the_bank_limits_are_stated_with_the_account(self):
        sim, bank, _ = start_case('PUB-04')
        verify(sim, bank)
        self.assertIn('LIMITS THE BANK CHECKS ON EVERY PROMISE for this product: lowest amount 400.00; highest amount 1250.00; '
                      'latest date Fri 2026-10-23 (Friday 23 October)', sim.prompt())


class HttpErrors(unittest.TestCase):
    def test_an_http_error_reaches_the_model_without_any_hook_text(self):
        sim, bank = verified(faults={'create_promise': 1})
        self.assertEqual(sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14'), {})
        self.assertEqual(state(sim)['promises'], {})
        self.assertIn('Call create_promise again now with exactly the same product, amount and date', after_error(sim))
        again = sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14')
        self.assertIn('is now recorded', again['what_to_do_next'])
        self.assertNotIn('GAVE AN ERROR', sim.vars['rt_live'])
        self.assertEqual(len(bank.ledger['promises']), 1)

    def test_the_prompt_itself_says_what_to_do_after_a_failed_call(self):
        workflow, _ = build_workflow(fixture(True), OPENAPI)
        for name in ('contact', 'negotiate'):
            node = workflow['nodes'][name]
            rules = node['task_message'].split('WHEN A TOOL CALL FAILS', 1)[1].split('TODAY:', 1)[0]
            for tool in node['tools']:
                self.assertIn('  - ' + tool['function']['name'] + ': ', rules)
            self.assertIn('LAST REJECTED TOOL CALL', rules)
        self.assertIn('At most 2 attempt(s) for one product', rules)
        self.assertIn(fixture(True)['guidance']['promise.failed_final'], rules)

    def test_no_attempt_beyond_the_limit_reaches_the_ledger(self):
        sim, bank = verified(faults={'create_promise': 2})
        for _ in range(2):
            self.assertEqual(sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14'), {})
        third = sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14')
        self.assertTrue(third['rejected_by_policy'])
        self.assertIn('Do not try again for this product', third['what_to_do_next'])
        self.assertEqual(bank.ledger['promises'], [])
        self.assertEqual(state(sim)['fails'], {'p1': 2})

    def test_a_failure_of_one_product_is_not_counted_against_another(self):
        sim, bank = verified(products=({'type': 'credit_card', 'card_last4': '4821', 'past_due': 420.0, 'days_late': 6, 'minimum_payment': 126.0},
                                       {'type': 'personal_loan', 'past_due': 900.0, 'days_late': 20, 'instalment_amount': 300.0}),
                             brief_b=True, faults={'create_promise': 1})
        sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-16')
        sim.tool('create_promise', product_id='p2', amount=900, date='2026-10-16')
        self.assertEqual((state(sim)['fails'], list(state(sim)['promises'])), ({'p1': 1}, ['p2']))
        sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-16')
        self.assertEqual(len(bank.ledger['promises']), 2)


class CallOrder(unittest.TestCase):
    def test_a_close_sent_with_a_transfer_waits_for_the_transfer(self):
        sim, bank, case = start_case('PUB-09')
        verify(sim, bank)
        transfer, close = sim.together(('transfer_to_agent', {'queue': 'restructuring'}),
                                       ('close_call', {'outcome': 'transferred', 'reason': 'restructuring'}))
        self.assertIn('The transfer to queue restructuring is done', transfer['what_to_do_next'])
        self.assertEqual(close, {})
        self.assertEqual((bank.ledger['transfers'], bank.ledger['outcome'], sim.node), ([{'queue': 'restructuring'}], None, 'negotiate'))
        self.assertEqual(state(sim)['blocks'], 1)
        sim.tool('close_call', outcome='transferred', reason='restructuring')
        self.assertEqual(ledger(bank), case['expected'])
        self.assertEqual((tools_called(bank)[-2:], sim.node), (['transfer_to_agent', 'close_call'], 'end'))

    def test_a_close_sent_with_a_callback_waits_for_the_callback(self):
        sim, bank, case = start_case('PUB-02')
        sim.together(('schedule_callback', {'date': '2026-10-15', 'time': '11:00'}),
                     ('close_call', {'outcome': 'not_reached', 'reason': 'callback'}))
        self.assertEqual((len(bank.ledger['callbacks']), bank.ledger['outcome']), (1, None))
        sim.tool('close_call', outcome='not_reached', reason='callback')
        self.assertEqual(ledger(bank), case['expected'])

    def test_a_close_after_a_failed_call_is_not_held_back(self):
        sim, bank = verified(faults={'transfer_to_agent': 1})
        self.assertEqual(sim.tool('transfer_to_agent', queue='support'), {})
        sim.tool('close_call', outcome='no_promise', reason='hardship')
        self.assertEqual((bank.ledger['outcome'], sim.node), ('no_promise', 'end'))

    def test_the_close_is_held_back_once_at_most(self):
        """Without a usable clock the mark of a lost call cannot age; the close must still get through."""
        variables = {'rt_state': json.dumps(dict(rt.load_state({}), identity='verified', open=['transfer', '', None, '']))}
        first = {'body': {'customer_id': 'c_real', 'outcome': 'paid'}}
        rt.pre_close(CFG, variables, first)
        second = {'body': {'customer_id': 'c_real', 'outcome': 'paid'}}
        rt.pre_close(CFG, variables, second)
        self.assertEqual((first['body']['customer_id'], second['body']['customer_id']), ('blocked_by_policy', 'c_real'))

    def test_the_model_is_told_to_send_close_call_alone(self):
        workflow, _ = build_workflow(fixture(True), OPENAPI)
        self.assertIn('ONE tool call per message, never two', workflow['system_prompt'])
        for name in ('contact', 'negotiate'):
            close = [t for t in workflow['nodes'][name]['tools'] if t['function']['name'] == 'close_call'][0]
            self.assertIn('alone in its own message', close['function']['description'])


class DayNames(unittest.TestCase):
    def date_of(self, today, words):
        text = rt.day_names(CFG, datetime.date.fromisoformat(today))
        rows = [row for row in text.splitlines() if (words in row if words.startswith('"') else row.strip().startswith(words))]
        self.assertEqual(len(rows), 1, (words, text))
        return rows[0].split(' = ', 1)[1]

    def test_midweek(self):
        day = lambda words: self.date_of('2026-10-14', words)
        self.assertEqual(day('today'), 'Wednesday 14 October = 2026-10-14 - business day')
        self.assertEqual(day('tomorrow'), 'Thursday 15 October = 2026-10-15 - business day')
        self.assertEqual(day('"this Friday"'), 'Friday 16 October = 2026-10-16 - business day')
        self.assertEqual(day('"Friday next week"'), 'Friday 23 October = 2026-10-23 - business day')
        self.assertEqual(day('"this Monday"'), 'Monday 19 October = 2026-10-19 - business day')
        self.assertEqual(day('"this Tuesday"'), 'Tuesday 20 October = 2026-10-20 - business day')
        self.assertEqual(day('"this Wednesday"'), 'Wednesday 14 October = 2026-10-14 - business day')
        self.assertEqual(day('"Wednesday next week"'), 'Wednesday 21 October = 2026-10-21 - business day')
        self.assertEqual(day('"this Saturday"'), 'Saturday 17 October = 2026-10-17 - NOT a business day, next business day 2026-10-19 Monday')

    def test_week_boundaries(self):
        self.assertEqual(self.date_of('2026-10-16', '"this Friday"')[:28], 'Friday 16 October = 2026-10-')
        self.assertIn('2026-10-16', self.date_of('2026-10-16', '"this Friday"'))
        self.assertIn('2026-10-23', self.date_of('2026-10-16', '"Friday next week"'))
        self.assertIn('2026-10-19', self.date_of('2026-10-16', '"this Monday"'))
        self.assertIn('2026-10-19', self.date_of('2026-10-18', 'tomorrow'))
        self.assertIn('2026-10-23', self.date_of('2026-10-18', '"this Friday"'))
        self.assertIn('2026-10-19', self.date_of('2026-10-19', '"this Monday"'))
        self.assertIn('2026-10-25', self.date_of('2026-10-19', '"this Sunday"'))

    def test_holidays_and_the_turn_of_the_year(self):
        self.assertEqual(self.date_of('2026-10-25', 'tomorrow'), 'Monday 26 October = 2026-10-26 - NOT a business day, next business day 2026-10-27 Tuesday')
        self.assertIn('2027-01-01', self.date_of('2026-12-30', '"this Friday"'))
        self.assertIn('2027-01-04', self.date_of('2026-12-30', '"this Monday"'))

    def test_every_row_names_the_weekday_of_its_own_date(self):
        start = datetime.date(2026, 10, 10)
        for offset in range(21):
            today = start + datetime.timedelta(days=offset)
            rows = [r for r in rt.day_names(CFG, today).splitlines() if ' = ' in r]
            self.assertEqual(len(rows), 16)
            for row in rows:
                name, iso = re.search(r' = (\w+) \d+ \w+ = (\d{4}-\d{2}-\d{2})', row).groups()
                self.assertEqual(datetime.date.fromisoformat(iso).strftime('%A'), name, row)
                if '"this ' in row or '"the coming ' in row:
                    self.assertLess((datetime.date.fromisoformat(iso) - today).days, 7, row)

    def test_the_table_is_in_both_prompts_and_filled_before_the_first_turn(self):
        sim, bank, _ = start_case('PUB-06')
        self.assertIn('"this Friday", "on Friday", "the coming Friday" = Friday 16 October = 2026-10-16', sim.prompt())
        verify(sim, bank)
        self.assertIn('"this Friday", "on Friday", "the coming Friday" = Friday 16 October = 2026-10-16', sim.prompt())
        self.assertIn('take its date from the DAY NAMES block', sim.wf['system_prompt'])


class Conversation(unittest.TestCase):
    def test_a_complete_value_in_an_unfinished_sentence_is_used(self):
        workflow, _ = build_workflow(fixture(), OPENAPI)
        self.assertIn('a value is complete as soon as all of its parts have been said', workflow['system_prompt'])


if __name__ == '__main__':
    unittest.main()
