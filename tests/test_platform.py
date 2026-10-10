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
from tests.freya_sim import Bank, Sim
from tests.support import OPENAPI, refusal
from tests.test_enforcement import after_error, start as start_call, tools_called, verified
from tests.test_runtime import ledger, start as start_case, verify


CFG = runtime_cfg(fixture(True), Api(OPENAPI))


def state(sim):
    return json.loads(sim.vars['rt_state'])


class BlockedCalls(unittest.TestCase):
    def test_a_blocked_promise_never_reaches_the_customer_at_the_bank_and_is_explained_in_the_live_state(self):
        # Until M15 a blocked promise kept its customer and named an unknown product, so that the bank's refusal
        # could carry the reason back. That relied on the bank refusing unknown products. It is detached now.
        sim, bank, case = start_case('PUB-04')
        verify(sim, bank)
        seen = len(bank.calls)
        late = sim.tool('create_promise', product_id='p1', amount=1250, date='2026-11-06')
        self.assertIn('is after the latest date', refusal(sim, late))
        self.assertIn('Fri 2026-10-23', refusal(sim, late))
        self.assertEqual(len(bank.calls), seen, 'the bank saw no request of this customer')
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
        self.assertIn('Do not try again for this product', refusal(sim, third))
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

    def test_a_close_after_a_failed_call_is_not_held_back(self):
        sim, bank = verified(faults={'transfer_to_agent': 1})
        self.assertEqual(sim.tool('transfer_to_agent', queue='support'), {})
        sim.tool('close_call', outcome='no_promise', reason='hardship')
        self.assertEqual((bank.ledger['outcome'], sim.node), ('no_promise', 'end'))

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


CARD = {'type': 'credit_card', 'card_last4': '4821', 'past_due': 420.0, 'days_late': 6, 'minimum_payment': 126.0}
LOAN = {'type': 'personal_loan', 'past_due': 900.0, 'days_late': 20, 'instalment_amount': 300.0}
WHO = {'date_of_birth': '1988-03-14', 'phone_last4': '4471'}


def call_with(spec):
    workflow, _ = build_workflow(spec, OPENAPI)
    bank = Bank('c_real', WHO, [CARD])
    return Sim(workflow, bank, {'now': '2026-10-14T11:00', 'title': 'Mr', 'last_name': 'Reyes'}), bank


class Reports(unittest.TestCase):
    """What the bank did is what the model is told. Calls of one message are in test_concurrency.py."""

    def test_a_retry_one_turn_after_a_failure_is_not_taken_for_a_duplicate(self):
        sim, bank = verified(faults={'create_promise': 1})
        self.assertEqual(sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14'), {})
        again = sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14')
        self.assertIn('is now recorded', again['what_to_do_next'])

    def test_a_reason_left_by_one_tool_is_never_read_by_another(self):
        variables = {'rt_state': json.dumps(dict(rt.load_state({}), identity='verified', today='2026-10-14', products=[
            {'id': 'p1', 'label': 'x', 'min_amount': 1.0, 'max_amount': 500.0, 'latest_date': '2026-10-23'}])),
            'rt_block_close': 'NOT CLOSED: held', 'rt_args_promise': json.dumps({'product_id': 'p1', 'amount': 420.0, 'date': '2026-10-14'})}
        result = {'status': 'success', 'data': {'ok': True, 'promise_id': 'pr1'}}
        rt.post_promise(CFG, variables, result)
        self.assertIn('is now recorded', result['data']['what_to_do_next'])
        self.assertEqual(variables['rt_block_close'], 'NOT CLOSED: held')

    def test_a_success_is_never_reported_as_a_rejection(self):
        for tool, post, args in (('promise', rt.post_promise, {'product_id': 'p1', 'amount': 420.0, 'date': '2026-10-14'}),
                                 ('note', rt.post_note, {'product_id': 'p1', 'reason': 'refused'}),
                                 ('callback', rt.post_callback, {'date': '2026-10-15', 'time': '11:00'})):
            variables = {'rt_state': json.dumps(dict(rt.load_state({}), identity='verified', products=[{'id': 'p1', 'label': 'x'}])),
                         'rt_block_' + tool: 'an older refusal', 'rt_args_' + tool: json.dumps(args)}
            result = {'status': 'success', 'data': {'ok': True}}
            post(CFG, variables, result)
            self.assertNotIn('rejected_by_policy', result['data'], tool)
            self.assertEqual(result['status'], 'success', tool)


class IdentityLimit(unittest.TestCase):
    def test_a_limit_below_the_service_limit_ends_the_check(self):
        spec = fixture()
        spec['identity']['max_wrong'] = 2
        sim, bank = call_with(spec)
        first = sim.tool('verify_identity', field='date_of_birth', answer='1990-01-01')
        self.assertIn('check ends: 1', first['what_to_do_next'])
        second = sim.tool('verify_identity', field='date_of_birth', answer='1990-01-02')
        self.assertIn('IDENTITY CHECK FAILED', second['what_to_do_next'])
        self.assertEqual((sim.vars['rt_identity'], bank.wrong_left), ('failed', 1))
        asked = len(bank.calls)
        self.assertEqual(sim.tool('verify_identity', field='date_of_birth', answer='1988-03-14'), {})
        self.assertEqual((len(bank.calls), sim.node, sim.vars['rt_identity']), (asked, 'contact', 'failed'))
        sim.tool('close_call', outcome='promise')
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('not_verified', 'failed'))
        self.assertNotIn('get_account', tools_called(bank))

    def test_a_failed_check_stays_failed_whatever_the_service_answers(self):
        variables = {'rt_state': json.dumps(dict(rt.load_state({}), identity='failed', wrong=2)),
                     'rt_args_identity': json.dumps({'field': 'phone_last4', 'answer': '4471'})}
        result = {'status': 'success', 'data': {'ok': True, 'verified': True, 'all_verified': True}}
        rt.post_identity(CFG, variables, result)
        self.assertEqual((result['data']['verified'], result['data']['all_verified'], result['data']['rt_identity'], variables['rt_identity']),
                         (False, False, 'failed', 'failed'))

    def test_the_service_limit_still_applies_when_the_brief_allows_more(self):
        spec = fixture()
        spec['identity']['max_wrong'] = 5
        sim, bank = call_with(spec)
        for answer in ('1990-01-01', '1990-01-02'):
            sim.tool('verify_identity', field='date_of_birth', answer=answer)
        self.assertIn('IDENTITY CHECK FAILED', sim.tool('verify_identity', field='date_of_birth', answer='1990-01-03')['what_to_do_next'])


class NotesAndClose(unittest.TestCase):
    def test_a_note_reason_outside_the_tool_api_is_refused(self):
        sim, bank = verified(products=(CARD, LOAN), brief_b=True)
        bad = sim.tool('add_note', product_id='p1', reason='angry')
        self.assertIn('refused, hardship, dispute, paid, service_error', refusal(sim, bad))
        self.assertEqual(bank.ledger['notes'], [])
        self.assertIn('The note is saved', sim.tool('add_note', product_id='p1', reason='refused')['what_to_do_next'])

    def test_the_compiler_adds_what_the_hooks_need_without_touching_the_spec(self):
        spec = fixture(True)
        cfg = runtime_cfg(spec, Api(OPENAPI))
        self.assertEqual(cfg['note']['reasons'], ['refused', 'hardship', 'dispute', 'paid', 'service_error'])
        self.assertEqual((cfg['close']['notes_required'], cfg['close']['notes_after_transfer']), (True, False))
        self.assertNotIn('reasons', spec['note'])
        self.assertNotIn('notes_required', spec['close'])
        self.assertNotIn('notes_required', runtime_cfg(fixture(), Api(OPENAPI))['close'])

    def test_a_close_with_open_products_is_refused_once(self):
        sim, bank = verified(products=(CARD, LOAN), brief_b=True)
        sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14')
        self.assertEqual(sim.tool('close_call', outcome='promise'), {})
        self.assertIn('NOT CLOSED: these products have neither a promise nor a note: p2 "personal loan"', sim.vars['rt_live'])
        self.assertEqual((sim.node, bank.ledger['outcome']), ('negotiate', None))
        sim.tool('add_note', product_id='p2', reason='refused')
        sim.tool('close_call', outcome='promise')
        self.assertEqual((sim.node, bank.ledger['outcome'], bank.ledger['notes']), ('end', 'promise', [{'product_id': 'p2', 'reason': 'refused'}]))

    def test_every_kind_of_refusal_is_used_once_and_the_call_then_closes(self):
        sim, bank = verified(products=(CARD, LOAN), brief_b=True)
        self.assertEqual(sim.tool('close_call', outcome='no_promise'), {})
        self.assertIn('neither a promise nor a note', sim.vars['rt_live'])
        self.assertEqual(sim.tool('close_call', outcome='no_promise'), {})
        self.assertIn('needs one of these reasons', sim.vars['rt_live'])
        self.assertEqual((sim.node, bank.ledger['outcome']), ('negotiate', None))
        sim.tool('close_call', outcome='no_promise')
        self.assertEqual((sim.node, bank.ledger['outcome']), ('end', 'no_promise'))

    def test_open_products_do_not_hold_a_close_after_a_transfer_or_without_notes_in_the_brief(self):
        sim, bank = verified(products=(CARD, LOAN), brief_b=True)
        sim.tool('transfer_to_agent', queue='restructuring')
        sim.tool('close_call', outcome='transferred', reason='restructuring')
        self.assertEqual(sim.node, 'end')
        sim, bank = verified()
        sim.tool('close_call', outcome='no_promise', reason='refused')
        self.assertEqual(sim.node, 'end')

    def test_a_close_needs_the_reason_its_outcome_lists(self):
        for args, part in (({'outcome': 'no_promise'}, 'needs one of these reasons: refused, hardship, dispute, service_error'),
                           ({'outcome': 'no_promise', 'reason': 'angry'}, 'needs one of these reasons'),
                           ({'outcome': 'finished'}, 'finished is not an outcome')):
            sim, bank = verified()
            self.assertEqual(sim.tool('close_call', **args), {})
            self.assertIn('NOT CLOSED: ', sim.vars['rt_live'])
            self.assertIn(part, sim.vars['rt_live'])
            self.assertEqual((sim.node, bank.ledger['outcome']), ('negotiate', None))
            sim.tool('close_call', outcome='no_promise', reason='refused')
            self.assertEqual((sim.node, bank.ledger['reason']), ('end', 'refused'))


if __name__ == '__main__':
    unittest.main()
