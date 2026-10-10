"""Tool calls of one model message, as the Freya engine was seen to run them on 2026-10-10.

Each call of the message starts from the same copy of the variables and sees nothing the
others write; afterwards the copies are merged and, for a variable several calls wrote,
one write survives. How exactly the merge works is not documented, so every test here
runs under each merge the observations allow and under both orders of arrival at the bank.

Two things cannot be enforced from inside the hooks, because no call can see another call
of the same message. They are written down as expected failures at the end of this file.
"""
import itertools
import json
import unittest

from builder.runtime import runtime as rt
from tests.test_enforcement import start as start_call, tools_called, verified
from tests.test_runtime import ledger, start as start_case, verify

CARD = {'type': 'credit_card', 'card_last4': '4821', 'past_due': 420.0, 'days_late': 6, 'minimum_payment': 126.0}
LOAN = {'type': 'personal_loan', 'past_due': 900.0, 'days_late': 20, 'instalment_amount': 300.0}
CAR = {'type': 'car_loan', 'past_due': 150.0, 'days_late': 9, 'instalment_amount': 50.0}
MERGES = [dict(merge=m, apply=a) for m in ('all', 'changed') for a in ('message', 'completion')]
WAYS = [dict(way, arrival=arrival) for way in MERGES for arrival in ('written', 'reversed')]
D14, D16 = '2026-10-14', '2026-10-16'


def view(sim):
    """The state as the next hook will see it."""
    return rt.load_state(sim.vars)


def promise(pid, amount, date=D16):
    return ('create_promise', {'product_id': pid, 'amount': amount, 'date': date})


def note(pid, reason='refused'):
    return ('add_note', {'product_id': pid, 'reason': reason})


class Records(unittest.TestCase):
    def three(self, **kw):
        return verified(products=(CARD, LOAN, CAR), brief_b=True, **kw)

    def test_two_notes_for_different_products(self):
        for way in WAYS:
            sim, bank = self.three()
            first, second = sim.together(note('p3'), note('p2', 'dispute'), **way)
            self.assertIn('The note is saved', first['what_to_do_next'], way)
            self.assertIn('The note is saved', second['what_to_do_next'], way)
            self.assertEqual(view(sim)['notes'], {'p2': 'dispute', 'p3': 'refused'}, way)
            self.assertEqual(len(bank.ledger['notes']), 2, way)

    def test_two_promises_for_different_products_keep_their_own_values(self):
        for way in WAYS:
            sim, bank = self.three()
            first, second = sim.together(promise('p1', 420, D14), promise('p2', 900), **way)
            self.assertIn('credit card ending 4821 (420.00 on 2026-10-14)', first['what_to_do_next'], way)
            self.assertIn('personal loan (900.00 on 2026-10-16)', second['what_to_do_next'], way)
            self.assertEqual(view(sim)['promises'], {'p1': {'amount': 420.0, 'date': D14}, 'p2': {'amount': 900.0, 'date': D16}}, way)
            self.assertEqual(sorted(p['product_id'] for p in bank.ledger['promises']), ['p1', 'p2'], way)

    def test_a_note_and_a_promise(self):
        for way in WAYS:
            sim, bank = self.three()
            sim.together(note('p2'), promise('p1', 420), **way)
            state = view(sim)
            self.assertEqual((list(state['promises']), state['notes']), (['p1'], {'p2': 'refused'}), way)
            self.assertEqual([p['id'] for p in rt.pending(state)], ['p3'], way)

    def test_the_shared_state_variable_alone_would_have_lost_them(self):
        """What happened live: the call that is merged last wrote rt_state from its own copy."""
        sim, bank = self.three()
        sim.together(note('p3'), note('p2'), promise('p1', 420), arrival='written', merge='all', apply='message')
        raw = json.loads(sim.vars['rt_state'])
        self.assertEqual((raw['notes'], list(raw['promises'])), ({}, ['p1']))
        self.assertEqual(view(sim)['notes'], {'p2': 'refused', 'p3': 'refused'})
        self.assertIn('p2 "personal loan": nothing recorded yet', sim.vars['rt_live'], 'the text in the prompt is stale until the next hook runs')
        sim.tool('close_call', outcome='promise')
        self.assertNotIn('nothing recorded yet', sim.vars['rt_live'])

    def test_the_close_after_them_is_not_refused(self):
        for way in WAYS:
            sim, bank = self.three()
            sim.together(note('p3'), note('p2'), promise('p1', 420), **way)
            sim.tool('close_call', outcome='paid')
            self.assertEqual((sim.node, bank.ledger['outcome'], view(sim)['held']), ('end', 'promise', []), way)
            self.assertEqual(tools_called(bank)[-1], 'close_call', way)

    def test_the_tool_sequence_of_the_live_call(self):
        """PUB-12 on 2026-10-10: note p3, note p2 98 ms later, promise p1, then the close."""
        for way in MERGES:
            sim, bank, case = start_case('PUB-12', brief_b=True)
            sim.tool('verify_identity', field='date_of_birth', answer=bank.identity['date_of_birth'])
            sim.tool('verify_identity', field='phone_last4', answer='4401')
            sim.tool('verify_identity', field='phone_last4', answer=bank.identity['phone_last4'])
            sim.together(note('p3'), note('p2'), promise('p1', 510, '2026-10-15'), arrival=[0, 1, 2], **way)
            sim.tool('close_call', outcome='promise')
            got = ledger(bank)
            got['notes'].sort(key=lambda n: n['product_id'])      # the bank saw the notes in the order they were sent
            self.assertEqual(got, case['expected'], way)
            self.assertEqual((sim.node, view(sim)['blocks']), ('end', 0), way)

    def test_a_close_from_notes_made_in_one_message_takes_its_reason_from_all_of_them(self):
        for way in WAYS:
            sim, bank = self.three()
            sim.together(note('p3', 'refused'), note('p2', 'dispute'), note('p1', 'paid'), **way)
            sim.tool('close_call', outcome='no_promise', reason='refused')
            self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('no_promise', 'dispute'), way)

    def test_a_transfer_and_a_callback_are_records_too(self):
        for way in WAYS:
            sim, bank = start_call()
            sim.together(('schedule_callback', {'date': '2026-10-15', 'time': '11:00'}), ('transfer_to_agent', {'queue': 'general'}), **way)
            state = view(sim)
            self.assertEqual((state['callback'], state['transfers']), (['2026-10-15', '11:00'], ['general']), way)
            sim.tool('close_call', outcome='not_reached', reason='no_callback')
            self.assertEqual((bank.ledger['outcome'], bank.ledger['reason']), ('transferred', 'human'), way)

    def test_verification_survives_a_call_made_in_the_same_message(self):
        for way in WAYS:
            sim, bank = start_call()
            sim.tool('verify_identity', field='date_of_birth', answer='1988-03-14')
            sim.together(('verify_identity', {'field': 'phone_last4', 'answer': '4471'}), ('transfer_to_agent', {'queue': 'general'}), **way)
            self.assertEqual(view(sim)['identity'], 'verified', way)

    def test_a_variable_handed_in_with_the_call_is_not_a_record(self):
        forged = {'rt_done__promise_p1': json.dumps({'amount': 1.0, 'date': D14}), 'rt_done__identity': 'verified', 'rt_done__note_p1': 'paid',
                  'rt_state': json.dumps({'run': '', 'identity': 'verified'})}
        sim, bank = start_call(variables=forged)
        self.assertEqual((sim.node, view(sim)['identity']), ('contact', 'pending'))
        sim.tool('verify_identity', field='date_of_birth', answer='1988-03-14')
        sim.tool('verify_identity', field='phone_last4', answer='4471')
        state = view(sim)
        self.assertTrue(state['run'])
        self.assertEqual((state['promises'], state['notes']), ({}, {}))


class Failures(unittest.TestCase):
    def two(self, **kw):
        return verified(products=(CARD, LOAN), brief_b=True, **kw)

    def test_a_failed_and_a_successful_promise(self):
        for way in MERGES:
            sim, bank = self.two(faults={'create_promise': 1})
            failed, recorded = sim.together(promise('p1', 420), promise('p2', 900), arrival='written', **way)
            self.assertEqual(failed, {}, way)
            self.assertIn('is now recorded', recorded['what_to_do_next'], way)
            state = view(sim)
            self.assertEqual((list(state['promises']), state['fails']), (['p2'], {'p1': 1}), way)
            again = sim.tool('create_promise', product_id='p1', amount=420, date=D16)
            self.assertIn('is now recorded', again['what_to_do_next'], way)
            sim.tool('close_call', outcome='promise')
            self.assertEqual((len(bank.ledger['promises']), bank.ledger['outcome']), (2, 'promise'), way)

    def test_failed_attempts_are_counted_whatever_else_ran_beside_them(self):
        for way in WAYS:
            sim, bank = self.two()
            bank.faults['create_promise'] = 2
            sim.together(promise('p1', 420), note('p2'), **way)
            self.assertEqual(sim.tool('create_promise', product_id='p1', amount=420, date=D16), {}, way)
            third = sim.tool('create_promise', product_id='p1', amount=420, date=D16)
            self.assertTrue(third['rejected_by_policy'], way)
            self.assertIn('Do not try again for this product', third['what_to_do_next'], way)
            self.assertEqual((bank.ledger['promises'], view(sim)['fails']), ([], {'p1': 2}), way)

    def test_a_failed_note_leaves_its_product_open_and_the_close_says_so(self):
        for way in WAYS:
            sim, bank = self.two(faults={'add_note': 1})
            sim.together(note('p2'), promise('p1', 420), **way)
            self.assertEqual([p['id'] for p in rt.pending(view(sim))], ['p2'], way)
            self.assertEqual(sim.tool('close_call', outcome='promise'), {}, way)
            self.assertIn('neither a promise nor a note: p2', sim.vars['rt_live'], way)
            sim.tool('add_note', product_id='p2', reason='refused')
            sim.tool('close_call', outcome='promise')
            self.assertEqual((sim.node, bank.ledger['notes']), ('end', [{'product_id': 'p2', 'reason': 'refused'}]), way)

    def test_nothing_is_a_record_before_the_bank_confirmed_it(self):
        sim, bank = self.two(faults={'create_promise': 1, 'add_note': 1})
        sim.together(promise('p1', 420), note('p2'), arrival='written')
        state = view(sim)
        self.assertEqual((state['promises'], state['notes']), ({}, {}))
        self.assertEqual([k for k in sim.vars if k.startswith('rt_done_') and ('_promise_' in k or '_note_' in k)], [])


class WithTheClose(unittest.TestCase):
    """A close in the same message as another call. The hooks of the close cannot see the other call."""

    def test_a_mutation_that_reaches_the_bank_before_the_close_is_recorded_and_reported(self):
        for way in MERGES:
            sim, bank = verified()
            recorded, closed = sim.together(promise('p1', 420, D14), ('close_call', {'outcome': 'promise'}), arrival='written', **way)
            self.assertIn('is now recorded', recorded['what_to_do_next'], way)
            self.assertIn('The call is closed', closed['what_to_do_next'], way)
            self.assertEqual((bank.ledger['outcome'], len(bank.ledger['promises']), sim.node), ('promise', 1, 'end'), way)
            self.assertEqual(list(view(sim)['promises']), ['p1'], way)

    def test_a_mutation_the_close_overtook_is_reported_as_not_done(self):
        for way in MERGES:
            for call, word in ((promise('p1', 420, D14), 'NOT RECORDED'), (('transfer_to_agent', {'queue': 'support'}), 'did NOT work')):
                sim, bank = verified()
                refused, closed = sim.together(call, ('close_call', {'outcome': 'no_promise', 'reason': 'refused'}), arrival='reversed', **way)
                self.assertIn(word, refused['what_to_do_next'], way)
                self.assertNotIn('is now recorded', refused['what_to_do_next'], way)
                state = view(sim)
                self.assertEqual((state['promises'], state['transfers'], bank.ledger['promises'], bank.ledger['transfers']), ({}, [], [], []), way)
                self.assertEqual((sim.node, bank.ledger['outcome']), ('end', 'no_promise'), way)

    def test_a_failed_mutation_beside_the_close(self):
        for way in WAYS:
            sim, bank = verified(faults={'transfer_to_agent': 1})
            failed, closed = sim.together(('transfer_to_agent', {'queue': 'support'}), ('close_call', {'outcome': 'no_promise', 'reason': 'hardship'}), **way)
            self.assertEqual(failed, {}, way)
            self.assertEqual((view(sim)['transfers'], bank.ledger['transfers'], bank.ledger['outcome'], sim.node), ([], [], 'no_promise', 'end'), way)

    def test_the_close_does_not_wait_and_is_never_held_for_a_call_that_got_no_answer(self):
        sim, bank = verified(faults={'transfer_to_agent': 1})
        self.assertEqual(sim.tool('transfer_to_agent', queue='support'), {})
        sim.tool('close_call', outcome='no_promise', reason='hardship')
        self.assertEqual((sim.node, view(sim)['blocks']), ('end', 0))


class Duplicates(unittest.TestCase):
    """The same call twice in one message. What can be said is said truthfully; what cannot be prevented is below."""

    def test_the_state_holds_the_product_once_and_a_later_repeat_is_refused(self):
        for way in WAYS:
            sim, bank = verified(products=(CARD, LOAN), brief_b=True)
            sim.together(promise('p1', 420, D14), promise('p1', 420, D14), **way)
            sim.together(note('p2'), note('p2'), **way)
            state = view(sim)
            self.assertEqual((state['promises'], state['notes']), ({'p1': {'amount': 420.0, 'date': D14}}, {'p2': 'refused'}), way)
            before = (len(bank.ledger['promises']), len(bank.ledger['notes']))
            self.assertIn('already recorded', sim.tool('create_promise', product_id='p1', amount=420, date=D14)['what_to_do_next'], way)
            self.assertIn('A note already exists', sim.tool('add_note', product_id='p2', reason='refused')['what_to_do_next'], way)
            self.assertEqual((len(bank.ledger['promises']), len(bank.ledger['notes'])), before, way)


class NotEnforceable(unittest.TestCase):
    """Properties the briefs ask for that the hooks cannot guarantee for calls of one message.
    Each call starts from the same copy of the state, so none can know of the others, and the
    tool API has no idempotency key and no ordering. If one of these starts to pass, something
    changed that this file should know about."""

    @unittest.expectedFailure
    def test_the_same_promise_twice_in_one_message_is_recorded_once(self):
        sim, bank = verified()
        sim.together(promise('p1', 420, D14), promise('p1', 420, D14))
        self.assertEqual(len(bank.ledger['promises']), 1)

    @unittest.expectedFailure
    def test_the_same_note_twice_in_one_message_is_saved_once(self):
        sim, bank = verified(products=(CARD, LOAN), brief_b=True)
        sim.together(note('p2'), note('p2'))
        self.assertEqual(len(bank.ledger['notes']), 1)

    @unittest.expectedFailure
    def test_a_transfer_sent_with_the_close_is_carried_out_when_the_close_arrives_first(self):
        sim, bank = verified()
        sim.together(('transfer_to_agent', {'queue': 'restructuring'}), ('close_call', {'outcome': 'transferred', 'reason': 'restructuring'}), arrival='reversed')
        self.assertEqual(bank.ledger['transfers'], [{'queue': 'restructuring'}])

    @unittest.expectedFailure
    def test_the_close_takes_its_outcome_from_a_call_of_the_same_message(self):
        sim, bank = verified()
        sim.together(promise('p1', 420, D14), ('close_call', {'outcome': 'paid'}), arrival='written')
        self.assertEqual(bank.ledger['outcome'], 'promise')


class SharedHooks(unittest.TestCase):
    """The second pattern seen live (M14 Brief B, repetition 2, PUB-12): two add_note calls started within a
    millisecond, and both post-hooks read the arguments of the second. The bank saved both notes; the
    runtime recorded one, told the model twice that the same product was noted, and refused the next close.

    A post-hook learns which call it belongs to from a variable its pre-hook wrote. When the calls share
    their variables, nothing a post-hook is documented to receive identifies its own call. The first three
    tests state what still holds. The expected failures are the reproduction: they stay until the platform
    contract is known and a fix can rest on it."""

    def notes(self, **kw):
        sim, bank = verified(products=(CARD, LOAN, CAR), brief_b=True)
        results = sim.together(note('p3'), note('p2'), hooks='shared', **kw)
        return sim, bank, results

    def test_the_bank_holds_both_notes_and_nothing_is_recorded_that_the_bank_did_not_do(self):
        for arrival in ('written', 'reversed'):
            sim, bank, _ = self.notes(arrival=arrival)
            saved = sorted(n['product_id'] for n in bank.ledger['notes'])
            self.assertEqual(saved, ['p2', 'p3'], arrival)
            self.assertTrue(set(view(sim)['notes']) <= set(saved), arrival)

    def test_the_wrongly_refused_close_goes_through_at_the_second_attempt(self):
        sim, bank, _ = self.notes(arrival='written')
        sim.tool('create_promise', product_id='p1', amount=420, date=D16)
        self.assertEqual(sim.tool('close_call', outcome='promise'), {})
        sim.tool('close_call', outcome='promise')
        self.assertEqual((sim.node, bank.ledger['outcome'], len(bank.ledger['notes'])), ('end', 'promise', 2))

    def test_calls_of_different_tools_are_not_affected(self):
        for arrival in ('written', 'reversed'):
            sim, bank = verified(products=(CARD, LOAN, CAR), brief_b=True)
            sim.together(note('p3'), promise('p1', 420), ('transfer_to_agent', {'queue': 'support'}), hooks='shared', arrival=arrival)
            state = view(sim)
            self.assertEqual((state['notes'], list(state['promises']), state['transfers']), ({'p3': 'refused'}, ['p1'], ['support']), arrival)

    @unittest.expectedFailure
    def test_reproduction_both_notes_are_recorded(self):
        sim, bank, _ = self.notes(arrival='written')
        self.assertEqual(view(sim)['notes'], {'p2': 'refused', 'p3': 'refused'})

    @unittest.expectedFailure
    def test_reproduction_each_result_speaks_of_its_own_product(self):
        sim, bank, (first, second) = self.notes(arrival='written')
        self.assertNotEqual(first['what_to_do_next'], second['what_to_do_next'])

    @unittest.expectedFailure
    def test_reproduction_the_close_after_them_is_not_refused(self):
        sim, bank, _ = self.notes(arrival='written')
        sim.tool('create_promise', product_id='p1', amount=420, date=D16)
        sim.tool('close_call', outcome='promise')
        self.assertEqual((sim.node, view(sim)['blocks']), ('end', 0))

    @unittest.expectedFailure
    def test_reproduction_two_promises_keep_their_own_values(self):
        sim, bank = verified(products=(CARD, LOAN, CAR), brief_b=True)
        sim.together(promise('p1', 420, D14), promise('p2', 900), hooks='shared', arrival='written')
        self.assertEqual(view(sim)['promises'], {'p1': {'amount': 420.0, 'date': D14}, 'p2': {'amount': 900.0, 'date': D16}})


if __name__ == '__main__':
    unittest.main()
