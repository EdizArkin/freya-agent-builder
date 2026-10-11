"""The rule kinds added in M16, each played through the compiled workflow against the stand-in bank.

Four kinds of rule that an extraction could until now only report as not expressible: amounts in fixed steps,
a lowest amount that depends on the promise date, one date for all promises of a call, and times of day at
which no callback can be booked. Each is tested at its boundary and one step either side, with the bank's
ledger as the criterion: a refused call leaves nothing there. Also here: a product whose limits cannot be
worked out gets no promise, and a refused call is told apart from a failed one.

What these tests show is what the generated code does with the tool calls they play. They do not show that
an extraction writes these fields (the extraction prompt does not describe them yet), nor what a phone model
says in a call.
"""
import datetime
import unittest

from builder.compiler.compiler import runtime_cfg
from builder.runtime import runtime as rt
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import digest, validate
from tests.fixture_spec import spec as fixture
from tests.renamed import PRODUCTS, Renamed
from tests.support import OPENAPI, refusal

API = Api(OPENAPI)
D14, D15, D16 = '2026-10-14', '2026-10-15', '2026-10-16'      # Wednesday (today), Thursday, Friday


def spec_with(brief_b=True, **parts):
    spec = fixture(brief_b)
    for path, value in parts.items():
        node = spec
        keys = path.split('__')
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return spec


def problems(spec):
    with unittest.TestCase().assertRaises(SpecError) as ctx:
        validate(spec, API)
    return ' | '.join(ctx.exception.problems)


def call(spec, products=None, now=None, verify=True):
    api = Renamed(spec=spec)
    sim, bank = api.start(products=products, variables={'now': now} if now else None)
    if verify:
        api.verify(sim)
    return api, sim, bank


def promise(api, sim, product='p1', amount=500, date=D15):
    return api.call(sim, 'create_promise', product_id=product, amount=amount, date=date)


def recorded(said):
    return 'is now recorded' in said.get('what_to_do_next', '')


class AmountSteps(unittest.TestCase):
    """p1 is a credit card: lowest amount 50.00, highest 500.00."""

    def test_only_a_whole_multiple_is_recorded(self):
        api, sim, bank = call(spec_with(promise__amount_step=50))
        said = promise(api, sim, amount=120)
        self.assertIn('120.00 is not a whole multiple of 50.00', refusal(sim, said))
        self.assertIn('The nearest amounts that can be recorded are 100.00 and 150.00', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])
        self.assertTrue(recorded(promise(api, sim, amount=150)))
        self.assertEqual([p['amount'] for p in bank.ledger['promises']], [150.0])
        self.assertIn('the amount a whole multiple of 50.00', sim.vars['rt_account_facts'])

    def test_one_cent_either_side_and_amounts_written_in_other_ways(self):
        for amount, ok in ((149.99, False), (150.01, False), (150, True), ('150', True), ('150.00', True), (149.999, True), (150.004, True), (150.006, False)):
            api, sim, bank = call(spec_with(promise__amount_step=50))
            self.assertEqual(recorded(promise(api, sim, amount=amount)), ok, amount)
            self.assertEqual([p['amount'] for p in bank.ledger['promises']], [150.0] if ok else [], amount)

    def test_steps_of_cents_are_counted_in_cents(self):
        for step, amount, ok in ((0.1, 100.3, True), (0.1, 100.35, False), (0.5, 100.5, True), (0.5, 100.25, False), (2.5, 57.5, True), (2.5, 58, False)):
            api, sim, bank = call(spec_with(promise__amount_step=step))
            self.assertEqual(recorded(promise(api, sim, amount=amount)), ok, (step, amount))

    def test_the_full_amount_is_an_exception_only_where_the_spec_says_so(self):
        api, sim, bank = call(spec_with(promise__amount_step=30))
        self.assertIn('The nearest amounts that can be recorded are 480.00.', refusal(sim, promise(api, sim, amount=500)))
        api, sim, bank = call(spec_with(promise__amount_step=30, promise__amount_step_except_max=True))
        self.assertIn('480.00 and 500.00', refusal(sim, promise(api, sim, amount=490)))
        self.assertTrue(recorded(promise(api, sim, amount=500)))
        self.assertIn('(the highest amount itself is also accepted)', sim.vars['rt_account_facts'])

    def test_the_step_does_not_lift_the_other_limits(self):
        api, sim, bank = call(spec_with(promise__amount_step=50))
        self.assertIn('is more than the amount due', refusal(sim, promise(api, sim, amount=550)))
        self.assertIn('is below the lowest amount', refusal(sim, promise(api, sim, amount=0.5)))
        self.assertEqual(bank.ledger['promises'], [])

    def test_a_step_that_is_not_an_amount_is_refused_at_build_time_and_stops_every_promise_at_run_time(self):
        for bad in (0, -10, '10', 0.001, True):
            self.assertIn('promise.amount_step must be a positive amount in whole cents', problems(spec_with(promise__amount_step=bad)), bad)
        self.assertIn('promise.amount_step_except_max must be true or false', problems(spec_with(promise__amount_step=10, promise__amount_step_except_max='yes')))
        self.assertIn('promise.amount_step_except_max needs promise.amount_step', problems(spec_with(promise__amount_step_except_max=True)))
        warnings = validate(spec_with(promise__amount_step=1000), API)
        self.assertTrue(any('no multiple of 1000 lies between the lowest amount' in w and 'no promise can be recorded' in w for w in warnings))
        # A configuration that reached the runtime damaged is not read as "no rule".
        cfg, variables = runtime_cfg(fixture(True), API), {'now': '2026-10-14T11:00'}
        cfg['promise'] = dict(cfg['promise'], amount_step='ten')
        rt.setup(cfg, variables)
        state = rt.load_state(variables)
        state.update(identity='verified', products=[dict(id='p1', label='card', min_amount=50.0, max_amount=500.0, latest_date='2026-10-30')])
        rt.save_state(cfg, variables, state)
        config = {'body': {'customer_id': 'c_real', 'product_id': 'p1', 'amount': 100, 'date': D15}}
        rt.pre_promise(cfg, variables, config)
        self.assertEqual(config['body']['customer_id'], 'blocked_by_policy')
        self.assertIn('the amount rule of this bank could not be read', variables['rt_live'])


class AmountThatDependsOnTheDate(unittest.TestCase):
    TODAY_FULL = [{'on_or_before': 'today', 'min_amount': 'full'}]

    def test_a_promise_for_today_needs_the_full_amount_and_a_lower_one_a_later_date(self):
        spec = spec_with(promise__min_amount_by_date=self.TODAY_FULL)
        api, sim, bank = call(spec)
        said = promise(api, sim, amount=499.99, date=D14)
        self.assertIn('a promise for credit card ending 4821 dated Wed 2026-10-14 (Wednesday 14 October) needs at least 500.00, and 499.99 is less', refusal(sim, said))
        self.assertIn('or the date is later than Wed 2026-10-14', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])
        self.assertTrue(recorded(promise(api, sim, amount=499.99, date=D15)), 'the same amount one day later')
        api, sim, bank = call(spec)
        self.assertTrue(recorded(promise(api, sim, amount=500, date=D14)), 'the full amount today')
        self.assertIn('a promise dated Wed 2026-10-14 (Wednesday 14 October) or earlier needs at least 500.00', sim.vars['rt_account_facts'])
        self.assertIn('needs at least 900.00', sim.vars['rt_account_facts'], 'worked out per product')

    def test_the_date_can_be_any_date_value_and_the_day_itself_is_included(self):
        spec = spec_with(promise__min_amount_by_date=[{'on_or_before': 'soon', 'min_amount': 'half'}])
        spec['account']['values'] += [{'name': 'soon', 'expr': {'kind': 'business_days_after', 'n': 2}},
                                      {'name': 'half', 'expr': {'kind': 'percent', 'of': 'full', 'percent': 50}}]
        for date, amount, ok in ((D16, 249.99, False), (D16, 250, True), ('2026-10-19', 249.99, True), (D15, 100, False)):
            api, sim, bank = call(spec)
            self.assertEqual(recorded(promise(api, sim, amount=amount, date=date)), ok, (date, amount))
            self.assertEqual(len(bank.ledger['promises']), int(ok))

    def test_several_dates_are_all_checked(self):
        spec = spec_with(promise__min_amount_by_date=self.TODAY_FULL + [{'on_or_before': 'soon', 'min_amount': 'half'}])
        spec['account']['values'] += [{'name': 'soon', 'expr': {'kind': 'business_days_after', 'n': 2}},
                                      {'name': 'half', 'expr': {'kind': 'percent', 'of': 'full', 'percent': 50}}]
        for date, amount, ok in ((D14, 400, False), (D14, 500, True), (D15, 400, True), (D15, 200, False), ('2026-10-19', 60, True)):
            api, sim, bank = call(spec)
            self.assertEqual(recorded(promise(api, sim, amount=amount, date=date)), ok, (date, amount))

    def test_what_it_names_has_to_exist_and_to_be_a_date_and_an_amount(self):
        self.assertIn('promise.min_amount_by_date must be a list of objects', problems(spec_with(promise__min_amount_by_date={'on_or_before': 'today'})))
        self.assertIn('"on_or_before" is "today" or the name of a date value, got \'tomorrow\'', problems(spec_with(promise__min_amount_by_date=[{'on_or_before': 'tomorrow', 'min_amount': 'full'}])))
        self.assertIn('"min_amount" is the name of an amount value, got 500', problems(spec_with(promise__min_amount_by_date=[{'on_or_before': 'today', 'min_amount': 500}])))
        self.assertIn('promise.min_amount_by_date needs a date and an amount for a product like', problems(spec_with(promise__min_amount_by_date=[{'on_or_before': 'full', 'min_amount': 'full'}])))
        self.assertIn('promise.min_amount_by_date needs a date and an amount for a product like', problems(spec_with(promise__min_amount_by_date=[{'on_or_before': 'today', 'min_amount': 'deadline'}])))


class OneDateForAllPromises(unittest.TestCase):
    """p1 can be promised until 2 November, p2 until 23 October."""
    SPEC = spec_with(promise__same_date=True)

    def test_a_second_promise_takes_the_date_of_the_first_or_is_refused(self):
        api, sim, bank = call(self.SPEC)
        self.assertTrue(recorded(promise(api, sim, 'p1', 500, D15)))
        said = promise(api, sim, 'p2', 900, D16)
        self.assertIn('all promises of this call carry one date, and Thu 2026-10-15 (Thursday 15 October) is already recorded', refusal(sim, said))
        self.assertIn('The promise for personal loan can only be recorded for Thu 2026-10-15', refusal(sim, said))
        self.assertEqual([(p['product_id'], p['date']) for p in bank.ledger['promises']], [('p1', D15)])
        self.assertTrue(recorded(promise(api, sim, 'p2', 900, D15)))
        self.assertEqual({p['date'] for p in bank.ledger['promises']}, {D15})

    def test_the_first_promise_goes_to_the_first_open_product(self):
        api, sim, bank = call(self.SPEC)
        said = promise(api, sim, 'p2', 900, D15)
        self.assertIn('recorded one at a time in the order of the list. First record a promise or a note for: p1 "credit card ending 4821"', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])
        api.call(sim, 'add_note', product_id='p1', reason='refused')
        self.assertTrue(recorded(promise(api, sim, 'p2', 900, D16)), 'with the first product noted, the second may set the date')

    def test_two_first_promises_in_one_message_cannot_record_two_dates(self):
        for merge, apply in (('all', 'message'), ('changed', 'message'), ('all', 'completion')):
            for arrival in ('written', 'reversed'):
                way = dict(arrival=arrival, merge=merge, apply=apply)
                api, sim, bank = call(self.SPEC)
                first = ('create_promise', api.args(product_id='p1', amount=500, date=D15))
                second = ('create_promise', api.args(product_id='p2', amount=900, date=D16))
                sim.together([first, second], **way)
                self.assertEqual([(p['product_id'], p['date']) for p in bank.ledger['promises']], [('p1', D15)], way)
                sim.together([second, first], **way)
                self.assertEqual({p['date'] for p in bank.ledger['promises']}, {D15}, 'nor does a repetition')

    def test_a_date_the_next_product_cannot_take_means_no_promise_for_it(self):
        api, sim, bank = call(self.SPEC)
        self.assertTrue(recorded(promise(api, sim, 'p1', 500, '2026-10-27')))
        self.assertIn('that date cannot be recorded for personal loan. No promise can be recorded for it in this call', refusal(sim, promise(api, sim, 'p2', 900, '2026-10-22')))
        self.assertIn('is after the latest date', refusal(sim, promise(api, sim, 'p2', 900, '2026-10-27')), 'the common date itself fails the product\'s own limit')
        self.assertEqual([p['product_id'] for p in bank.ledger['promises']], ['p1'])
        api.call(sim, 'add_note', product_id='p2', reason='refused')
        self.assertEqual(sim.vars['rt_live'].count('note recorded'), 1, 'the product can still be closed with a note')

    def test_the_model_is_told_before_the_first_promise(self):
        api, sim, bank = call(self.SPEC)
        facts = sim.vars['rt_account_facts']
        self.assertIn('ALL PROMISES OF THIS CALL MUST CARRY ONE AND THE SAME DATE', facts)
        self.assertIn('The latest date every product accepts is Fri 2026-10-23 (Friday 23 October).', facts)
        api, sim, bank = call(spec_with(False, promise__same_date=True), products=PRODUCTS[:1])
        self.assertNotIn('ONE AND THE SAME DATE', sim.vars['rt_account_facts'], 'one product needs no such sentence')
        self.assertTrue(recorded(promise(api, sim, 'p1', 500, D15)))

    def test_it_is_true_or_false(self):
        self.assertIn('promise.same_date must be true or false', problems(spec_with(promise__same_date='always')))


class ClosedCallbackPeriods(unittest.TestCase):
    LUNCH = [{'from': '12:30', 'until': '13:30'}]

    def book(self, closed, time, date=D14):
        api, sim, bank = call(spec_with(callback__closed=closed), verify=False)
        said = api.call(sim, 'schedule_callback', date=date, time=time)
        return 'is booked' in said.get('what_to_do_next', ''), bank.ledger['callbacks'], (sim.vars['rt_live'] if said == {} else '')

    def test_from_and_until_include_their_minute(self):
        for time, ok in (('12:29', True), ('12:30', False), ('12:45', False), ('13:30', False), ('13:31', True)):
            booked, ledger, why = self.book(self.LUNCH, time)
            self.assertEqual((booked, len(ledger)), (ok, int(ok)), time)
            if not ok:
                self.assertIn(time + ' is in a period in which no callback can be booked (from 12:30 up to and including 13:30)', why)
                self.assertIn('On every day NO callback can be booked from 12:30 up to and including 13:30', why)

    def test_after_and_before_do_not(self):
        for time, ok in (('12:30', True), ('12:31', False), ('13:29', False), ('13:30', True)):
            self.assertEqual(self.book([{'after': '12:30', 'before': '13:30'}], time)[0], ok, time)

    def test_it_holds_on_every_day_and_for_every_way_of_writing_a_time(self):
        self.assertEqual(self.book(self.LUNCH, '13:00', D15)[0], False)
        self.assertEqual(self.book(self.LUNCH, '14:00', D15)[0], True)
        for time in ('12.45', '12h45', ' 12:45', '12:45:00'):
            booked, ledger, why = self.book(self.LUNCH, time)
            self.assertEqual((booked, ledger), (False, []), time)

    def test_several_periods_and_the_window_itself_still_apply(self):
        closed = self.LUNCH + [{'from': '17:00'}]
        for time, ok in (('16:59', True), ('17:00', False), ('18:30', False), ('12:45', False), ('09:00', False), ('11:30', True)):
            self.assertEqual(self.book(closed, time)[0], ok, time)

    def test_a_period_has_to_test_a_time(self):
        self.assertIn('callback.closed must be a list of periods', problems(spec_with(callback__closed={'from': '12:30'})))
        self.assertIn('callback.closed must be a list of periods', problems(spec_with(callback__closed=[{}])))
        self.assertIn('callback.closed: a period tests from, after, until, before with HH:MM', problems(spec_with(callback__closed=[{'from': 'noon'}])))
        self.assertIn('callback.closed: a period tests from, after, until, before with HH:MM', problems(spec_with(callback__closed=[{'between': '12:30'}])))
        warnings = validate(spec_with(callback__closed=[{'from': '09:00', 'until': '20:00'}]), API)
        self.assertTrue(any('callback.closed covers the whole callback window' in w for w in warnings))


class LimitsThatCannotBeWorkedOut(unittest.TestCase):
    def test_a_product_the_spec_has_no_rule_for_gets_no_promise(self):
        # The tool API lists the product types; a bank can still send one it does not list.
        products = [dict(PRODUCTS[0]), {'type': 'overdraft', 'past_due': 300.0, 'days_late': 9}]
        api, sim, bank = call(fixture(True), products=products)
        said = promise(api, sim, 'p2', 300, D15)
        self.assertIn('the bank could not work out the limits for car loan, so no promise can be recorded for it', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])
        self.assertIn('NO PROMISE CAN BE RECORDED for this product: the bank could not work out its limits', sim.vars['rt_account_facts'])
        self.assertTrue(recorded(promise(api, sim, 'p1', 500, D15)), 'the product whose limits are known is not affected')

    def test_every_limit_the_spec_names_has_to_have_a_value(self):
        cfg = runtime_cfg(fixture(True), API)
        today = datetime.date(2026, 10, 14)
        good = rt.product_facts(cfg, [dict(PRODUCTS[0], product_id='p1')], today, '11:00')[0]
        self.assertEqual((good['broken'], good['floors']), ([], []))
        for field, missing in (('minimum_payment', ['min_amount']), ('past_due', ['max_amount'])):
            product = dict(PRODUCTS[0], product_id='p1')
            product[field] = 'unknown'
            self.assertEqual(rt.product_facts(cfg, [product], today, '11:00')[0]['broken'], missing, field)


class ARefusalIsNotAFailure(unittest.TestCase):
    def test_a_refused_call_is_told_apart_from_a_service_failure(self):
        api, sim, bank = call(fixture(True))
        said = promise(api, sim, amount=10)
        self.assertEqual((said, bank.ledger['promises']), ({}, []), 'no tool result and nothing at the bank')
        live = sim.vars['rt_live']
        self.assertIn('THAT CALL WAS STOPPED BY THE BANK POLICY CHECK. It is NOT a service failure and NOTHING was recorded', live)
        self.assertNotIn('IF YOUR LAST TOOL CALL GAVE AN ERROR INSTEAD OF A RESULT', live, 'the advice for a failed service is not shown for a refusal')
        self.assertNotIn('PROMISE RECORDED', live)
        self.assertIn('STOPPED BY THE BANK POLICY CHECK', sim.prompt())

    def test_refusals_do_not_use_up_the_attempts_a_failing_service_is_given(self):
        api, sim, bank = call(fixture(True))
        for amount in (10, 9999, 20, 30):
            self.assertEqual(promise(api, sim, amount=amount), {})
        self.assertTrue(recorded(promise(api, sim, amount=500)), 'four refusals, and the valid promise is still taken')
        self.assertNotIn('STOPPED BY THE BANK POLICY CHECK', sim.vars['rt_live'], 'and the refusal is gone from the state once a call succeeded')

    def test_a_real_failure_is_still_called_a_failure(self):
        api = Renamed(spec=fixture(True))
        sim, bank = api.start(faults={'create_promise': 1})
        api.verify(sim)
        promise(api, sim)
        live = sim.vars['rt_live']
        self.assertNotIn('STOPPED BY THE BANK POLICY CHECK', live)
        self.assertIn('IF YOUR LAST TOOL CALL GAVE AN ERROR INSTEAD OF A RESULT', live)


class ReportedIsNotEnforced(unittest.TestCase):
    def test_a_spec_with_policy_it_cannot_express_does_not_build_unless_told_to(self):
        spec = spec_with(unsupported=['Promises are taken only from the account holder in person.'])
        self.assertIn('the extractor reported mandatory policy it could not express', problems(spec))
        warnings = validate(spec, API, True)
        self.assertTrue(any('could not express: Promises are taken only' in w for w in warnings), 'with --best-effort it builds, and says what is not enforced')

    def test_the_new_fields_are_part_of_what_an_audit_reads(self):
        plain = digest(fixture(True), API)
        self.assertEqual((plain['promise amounts'], plain['all promises of one call on one date'], plain['times of day at which no callback can be booked']),
                         ('any amount inside the limits', False, 'none'))
        full = digest(spec_with(promise__amount_step=10, promise__same_date=True, promise__min_amount_by_date=[{'on_or_before': 'today', 'min_amount': 'full'}],
                                callback__closed=[{'from': '12:30', 'until': '13:30'}]), API)
        self.assertEqual(full['promise amounts'], 'whole multiples of 10')
        self.assertEqual(full['all promises of one call on one date'], True)
        self.assertEqual(full['lowest amount that depends on the promise date'], [{'on_or_before': 'today', 'min_amount': 'full'}])
        self.assertEqual(full['times of day at which no callback can be booked'], [{'from': '12:30', 'until': '13:30'}])


class EdgesOfDatesAmountsAndOrder(unittest.TestCase):
    """What was already enforced, pinned at its edges."""
    CAL = {'calendar': {'business_weekdays': [0, 1, 2, 3, 4], 'holidays': ['2026-10-19'], 'week_start': 0, 'horizon_days': 21}}

    def test_the_day_of_a_call_is_the_day_its_clock_says_whatever_the_zone_suffix(self):
        for raw, day, clock in (('2026-10-14T23:59', '2026-10-14', '23:59'), ('2026-10-14T23:59:30+03:00', '2026-10-14', '23:59'), ('2026-10-15T00:00:00Z', '2026-10-15', '00:00'),
                                ('2026-10-14 08:05', '2026-10-14', '08:05'), ('2026-10-14', '2026-10-14', '00:00')):
            now = rt.parse_now(raw)
            self.assertEqual((now.date().isoformat(), now.strftime('%H:%M')), (day, clock), raw)

    def test_one_minute_before_midnight_today_still_counts_as_today(self):
        api, sim, bank = call(fixture(True), now='2026-10-14T23:59')
        self.assertTrue(recorded(promise(api, sim, date=D14)))
        api, sim, bank = call(fixture(True), now='2026-10-15T00:00')
        self.assertIn('is in the past. Today is Thu 2026-10-15', refusal(sim, promise(api, sim, date=D14)))

    def test_weekdays_a_person_names_are_given_to_the_model_as_dates(self):
        names = rt.day_names(self.CAL, datetime.date(2026, 10, 14))          # a Wednesday
        self.assertIn('"Friday", "this Friday", "on Friday", "the coming Friday" = Friday 16 October = 2026-10-16 - business day', names)
        self.assertIn('"Friday next week", "Friday week" = Friday 23 October = 2026-10-23', names)
        self.assertIn('today, "this Wednesday" = Wednesday 14 October = 2026-10-14', names)
        self.assertIn('"Monday", "this Monday", "on Monday", "the coming Monday" = Monday 19 October = 2026-10-19 - NOT a business day, next business day 2026-10-20 Tuesday', names)
        self.assertIn('"next <weekday>" can mean either of the two dates', names)
        friday = rt.day_names(self.CAL, datetime.date(2026, 10, 16))
        self.assertIn('today, "this Friday" = Friday 16 October = 2026-10-16', friday)
        self.assertIn('"Friday next week", "a week from today" = Friday 23 October = 2026-10-23', friday)
        self.assertIn('tomorrow = Saturday 17 October = 2026-10-17 - NOT a business day, next business day 2026-10-20 Tuesday', friday, 'over the weekend and the holiday')

    def test_a_weekday_that_was_counted_wrongly_is_caught_when_it_falls_on_a_closed_day(self):
        api, sim, bank = call(fixture(True))
        said = promise(api, sim, date='2026-10-17')
        self.assertIn('Sat 2026-10-17 (Saturday 17 October) is not a business day. Offer Mon 2026-10-19 (Monday 19 October) instead', refusal(sim, said))
        self.assertEqual(bank.ledger['promises'], [])

    def test_amount_limits_include_their_value_and_one_cent_outside_is_refused(self):
        for amount, ok in ((50, True), (49.99, False), (49.996, True), (500, True), (500.004, True), (500.01, False), (0, False), (-50, False), ('fifty', False)):
            api, sim, bank = call(fixture(True))
            self.assertEqual(recorded(promise(api, sim, amount=amount)), ok, amount)
            self.assertEqual(len(bank.ledger['promises']), int(ok), amount)
        api, sim, bank = call(fixture(True))
        promise(api, sim, amount=499.996)
        self.assertEqual(bank.ledger['promises'][0]['amount'], 500.0, 'what is sent is the amount in whole cents')

    def test_the_latest_date_is_included_and_the_next_day_is_not(self):
        for date, ok in (('2026-11-02', True), ('2026-11-03', False), (D14, True), ('2026-10-13', False), ('14/10/2026', False), ('2026-10-32', False)):
            api, sim, bank = call(fixture(True))
            self.assertEqual(recorded(promise(api, sim, date=date)), ok, date)
            self.assertEqual(len(bank.ledger['promises']), int(ok), date)

    def test_order_total_and_date_rules_hold_together(self):
        spec = spec_with(promise__resolve_first=[{'type': 'personal_loan'}], promise__same_date=True, promise__amount_step=50,
                         promise__min_total={'percent': 60, 'of': 'full'}, promise__min_amount_by_date=[{'on_or_before': 'today', 'min_amount': 'full'}])
        api, sim, bank = call(spec)
        self.assertIn('has to wait', refusal(sim, promise(api, sim, 'p1', 500, D15)))
        self.assertIn('not a whole multiple of 50.00', refusal(sim, promise(api, sim, 'p2', 320, D15)))
        self.assertIn('needs at least 900.00', refusal(sim, promise(api, sim, 'p2', 350, D14)))
        self.assertIn('can no longer be reached', refusal(sim, promise(api, sim, 'p2', 300, D15)), '300 + 500 is below 60 percent of 1400')
        self.assertEqual(bank.ledger['promises'], [])
        self.assertTrue(recorded(promise(api, sim, 'p2', 350, D15)))
        self.assertIn('can no longer be reached', refusal(sim, promise(api, sim, 'p1', 450, D15)))
        self.assertIn('Thu 2026-10-15 (Thursday 15 October) is already recorded', refusal(sim, promise(api, sim, 'p1', 500, D16)))
        self.assertTrue(recorded(promise(api, sim, 'p1', 500, D15)))
        self.assertEqual([(p['product_id'], p['amount'], p['date']) for p in bank.ledger['promises']], [('p2', 350.0, D15), ('p1', 500.0, D15)])

    def test_an_exception_for_one_queue_does_not_open_the_others(self):
        spec = spec_with(promise__allowed_after_transfer_to=['support'], promise__amount_step=50)
        api, sim, bank = call(spec)
        api.call(sim, 'transfer_to_agent', queue='support')
        self.assertIn('not a whole multiple', refusal(sim, promise(api, sim, amount=120)), 'the exception lifts the transfer rule and no other')
        self.assertTrue(recorded(promise(api, sim, amount=150)))
        api, sim, bank = call(spec)
        api.call(sim, 'transfer_to_agent', queue='general')
        self.assertIn('No promise may be recorded after a transfer', refusal(sim, promise(api, sim, amount=150)))
        self.assertEqual(bank.ledger['promises'], [])


if __name__ == '__main__':
    unittest.main()
