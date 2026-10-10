"""builder/runtime: calendar maths and whole calls, driven through the offline engine stand-in."""
import datetime
import unittest

from builder.pipeline import build_workflow
from builder.runtime import runtime as rt
from tests.fixture_spec import spec as fixture
from tests.freya_sim import Bank, Sim
from tests.support import CASES, OPENAPI


CAL = fixture()['calendar']


def start(case_id, brief_b=False, now=None):
    case = CASES[case_id]
    workflow, _ = build_workflow(fixture(brief_b), OPENAPI)
    bank = Bank('c_test', case['identity'], case['products'], {f['tool']: f['fail_first_n'] for f in case.get('faults', [])})
    variables = dict(case['variables'])
    if now:
        variables['now'] = now
    return Sim(workflow, bank, variables), bank, case


def verify(sim, bank):
    sim.tool('verify_identity', field='date_of_birth', answer=bank.identity['date_of_birth'])
    sim.tool('verify_identity', field='phone_last4', answer=bank.identity['phone_last4'])


def ledger(bank):
    led = bank.ledger
    return {'outcome': led['outcome'], 'reason': led['reason'], 'promises': led['promises'], 'notes': led['notes'],
            'callbacks': led['callbacks'], 'transfers': led['transfers']}


class Calendar(unittest.TestCase):
    def test_deadlines_and_offers_match_the_practice_cases(self):
        today = datetime.date(2026, 10, 14)
        self.assertEqual(rt.nth_biz_after(today, 7, CAL).isoformat(), '2026-10-23')
        self.assertEqual(rt.nth_biz_after(today, 12, CAL).isoformat(), '2026-11-02')
        self.assertEqual(rt.next_biz(datetime.date(2026, 10, 26), CAL).isoformat(), '2026-10-27')
        friday = {'kind': 'last_business_day_of_week', 'week': 0, 'if_today_or_past': 'following_week'}
        self.assertEqual(rt.evaluate(friday, today, CAL, {}, {}).isoformat(), '2026-10-16')
        self.assertEqual(rt.evaluate(friday, datetime.date(2026, 10, 16), CAL, {}, {}).isoformat(), '2026-10-23')

    def test_callback_window(self):
        cfg = {'calendar': CAL, 'callback': fixture()['callback']}
        now = rt.parse_now('2026-10-14T11:00')
        self.assertEqual(rt.check_callback(cfg, now, '2026-10-15', '11:00'), '')
        self.assertEqual(rt.check_callback(cfg, now, '2026-10-14', '19:00'), '')
        self.assertTrue(rt.check_callback(cfg, now, '2026-10-14', '11:00'))
        self.assertTrue(rt.check_callback(cfg, now, '2026-10-15', '09:30'))
        self.assertTrue(rt.check_callback(cfg, now, '2026-10-16', '12:00'))
        friday = rt.parse_now('2026-10-23T18:00')
        self.assertEqual(rt.check_callback(cfg, friday, '2026-10-27', '10:00'), '')
        self.assertTrue(rt.check_callback(cfg, friday, '2026-10-26', '10:00'))


class Calls(unittest.TestCase):
    def test_happy_path(self):
        sim, bank, case = start('PUB-01')
        self.assertEqual(sim.node, 'contact')
        with self.assertRaises(LookupError):
            sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14')
        first = sim.tool('verify_identity', field='date_of_birth', answer='14/03/1988')
        self.assertIn('next security question', first['what_to_do_next'])
        sim.tool('verify_identity', field='phone_last4', answer='4471')
        self.assertEqual(sim.node, 'negotiate')
        facts = sim.prompt()
        for part in ('credit card ending 4821', 'FULL past-due amount: 420.00', 'FLOOR (lowest amount that can be recorded): 126.00',
                     'DEADLINE (latest date that can be recorded): Mon 2026-11-02', 'attempt_1_date: Fri 2026-10-16', 'persuasion attempts: 1'):
            self.assertIn(part, facts)
        done = sim.tool('create_promise', product_id='p1', amount=420, date='2026-10-14')
        self.assertIn('text message', done['what_to_do_next'])
        sim.tool('close_call', outcome='no_promise', reason='refused')
        self.assertEqual(sim.node, 'end')
        self.assertEqual(ledger(bank), case['expected'])

    def test_three_wrong_answers_end_the_check(self):
        sim, bank, case = start('PUB-03')
        for answer in ('1991-07-12', '1992-08-21'):
            self.assertIn('WRONG', sim.tool('verify_identity', field='date_of_birth', answer=answer)['what_to_do_next'])
        last = sim.tool('verify_identity', field='date_of_birth', answer='1990-07-21')
        self.assertIn('IDENTITY CHECK FAILED', last['what_to_do_next'])
        self.assertIn('mobile app', last['what_to_do_next'])
        self.assertEqual(sim.node, 'contact')
        sim.tool('close_call', outcome='not_verified', reason='declined')
        self.assertEqual(ledger(bank), case['expected'])

    def test_swapped_day_and_month_is_not_counted(self):
        sim, bank, _ = start('PUB-01')
        hint = sim.tool('verify_identity', field='date_of_birth', answer='the fourteenth')
        self.assertIn('not counted', hint['what_to_do_next'])
        bank.identity['date_of_birth'] = '1988-03-05'
        hint = sim.tool('verify_identity', field='date_of_birth', answer='1988-05-03')
        self.assertIn('month as a word', hint['what_to_do_next'])
        self.assertEqual(bank.wrong_left, 3)

    def test_relative_books_a_callback_inside_the_window_only(self):
        sim, bank, case = start('PUB-02')
        self.assertIn('Thu 2026-10-15 (Thursday 15 October): from 10:00', sim.prompt())
        self.assertIn('later than 11:00', sim.prompt())
        rejected = sim.tool('schedule_callback', date='2026-10-17', time='11:00')
        self.assertTrue(rejected['rejected_by_policy'])
        self.assertEqual(bank.ledger['callbacks'], [])
        sim.tool('schedule_callback', date='2026-10-15', time='11.00')
        sim.tool('close_call', outcome='not_reached', reason='no_callback')
        self.assertEqual(ledger(bank), case['expected'])

    def test_date_after_the_deadline_and_holiday_are_blocked(self):
        sim, bank, case = start('PUB-04')
        verify(sim, bank)
        late = sim.tool('create_promise', product_id='p1', amount=1250, date='2026-11-06')
        self.assertIn('Fri 2026-10-23', late['what_to_do_next'])
        self.assertEqual(bank.ledger['promises'], [])
        sim.tool('create_promise', product_id='p1', amount=1250, date='2026-10-23')
        again = sim.tool('create_promise', product_id='p1', amount=1250, date='2026-10-22')
        self.assertIn('already recorded', again['what_to_do_next'])
        sim.tool('close_call', outcome='promise')
        self.assertEqual(ledger(bank), case['expected'])
        sim, bank, case = start('PUB-07')
        verify(sim, bank)
        holiday = sim.tool('create_promise', product_id='p1', amount=540, date='2026-10-26')
        self.assertIn('Offer Tue 2026-10-27', holiday['what_to_do_next'])
        sim.tool('create_promise', product_id='p1', amount=540, date='2026-10-27')
        sim.tool('close_call', outcome='promise')
        self.assertEqual(ledger(bank), case['expected'])

    def test_amount_limits(self):
        sim, bank, case = start('PUB-06')
        verify(sim, bank)
        self.assertTrue(sim.tool('create_promise', product_id='p1', amount=500, date='2026-10-16')['rejected_by_policy'])
        self.assertTrue(sim.tool('create_promise', product_id='p1', amount=2200, date='2026-10-16')['rejected_by_policy'])
        sim.tool('create_promise', product_id='card', amount='637.50', date='2026-10-16')
        sim.tool('close_call', outcome='promise', reason='refused')
        self.assertEqual(ledger(bank), case['expected'])

    def test_promise_service_failure_then_retry(self):
        sim, bank, case = start('PUB-10')
        verify(sim, bank)
        failed = sim.tool('create_promise', product_id='p1', amount=365, date='2026-10-16')
        self.assertIn('Call create_promise again', failed['what_to_do_next'])
        sim.tool('create_promise', product_id='p1', amount=365, date='2026-10-16')
        sim.tool('close_call', outcome='promise')
        self.assertEqual(ledger(bank), case['expected'])
        sim, bank, _ = start('PUB-10')
        bank.faults['create_promise'] = 2
        verify(sim, bank)
        sim.tool('create_promise', product_id='p1', amount=365, date='2026-10-16')
        final = sim.tool('create_promise', product_id='p1', amount=365, date='2026-10-16')
        self.assertIn('service_error', final['what_to_do_next'])

    def test_transfer_wins_the_close(self):
        sim, bank, case = start('PUB-09')
        verify(sim, bank)
        sim.tool('transfer_to_agent', queue='restructuring')
        sim.tool('close_call', outcome='no_promise', reason='refused')
        self.assertEqual(ledger(bank), case['expected'])
        sim, bank, _ = start('PUB-01')
        sim.tool('transfer_to_agent', queue='general')
        sim.tool('close_call', outcome='transferred', reason='support')
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('transferred', 'human'))

    def test_model_chosen_closes_pass_through(self):
        for case_id, outcome, reason in (('PUB-05', 'no_promise', 'hardship'), ('PUB-08', 'paid', 'refused')):
            sim, bank, case = start(case_id)
            verify(sim, bank)
            sim.tool('close_call', outcome=outcome, reason=reason)
            self.assertEqual(ledger(bank), case['expected'])

    def test_multi_product_dates_notes_and_close(self):
        sim, bank, case = start('PUB-13', brief_b=True)
        verify(sim, bank)
        self.assertIn('2 late product(s)', sim.prompt())
        self.assertTrue(sim.tool('create_promise', product_id='p2', amount=900, date='2026-10-30')['rejected_by_policy'])
        more = sim.tool('create_promise', product_id='p1', amount=300, date='2026-10-30')
        self.assertIn('p2 "personal loan"', more['what_to_do_next'])
        sim.tool('create_promise', product_id='p2', amount=900, date='2026-10-23')
        sim.tool('close_call', outcome='promise')
        self.assertEqual(ledger(bank), case['expected'])
        sim, bank, case = start('PUB-12', brief_b=True)
        verify(sim, bank)
        sim.tool('create_promise', product_id='p1', amount=510, date='2026-10-15')
        self.assertTrue(sim.tool('add_note', product_id='p1', reason='refused')['rejected_by_policy'])
        sim.tool('add_note', product_id='p2', reason='refused')
        self.assertTrue(sim.tool('add_note', product_id='p2', reason='refused')['rejected_by_policy'])
        self.assertIn('Every product', sim.tool('add_note', product_id='p3', reason='refused')['what_to_do_next'])
        sim.tool('close_call', outcome='no_promise', reason='refused')
        self.assertEqual(ledger(bank), case['expected'])

    def test_close_from_notes_when_nothing_is_promised(self):
        sim, bank, _ = start('PUB-12', brief_b=True)
        verify(sim, bank)
        for pid, reason in (('p1', 'paid'), ('p2', 'dispute'), ('p3', 'refused')):
            sim.tool('add_note', product_id=pid, reason=reason)
        sim.tool('close_call', outcome='paid')
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('no_promise', 'dispute'))
        sim, bank, _ = start('PUB-13', brief_b=True)
        verify(sim, bank)
        for pid in ('p1', 'p2'):
            sim.tool('add_note', product_id=pid, reason='paid')
        sim.tool('close_call', outcome='no_promise', reason='refused')
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('paid', None))


if __name__ == '__main__':
    unittest.main()
