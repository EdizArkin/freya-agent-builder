"""What reaches the bank when a pre-hook fails.

A pre-hook that raised used to leave the request as the phone model wrote it: unchecked. The compiled
wrapper now detaches the request in that case. These tests raise an error at every point of every
pre-hook and look at the bank afterwards.

What they cannot cover: a hook the platform does not run at all. Nothing inside a hook can act then;
that case is kept below as an expected failure.
"""
import copy
import json
import unittest
from unittest import mock

from builder.compiler import compiler
from tests import freya_sim
from tests.renamed import CALL, PLACEHOLDER, Renamed, snapshot

PROMISE = dict(product_id='p1', amount=500, date='2026-10-15')
# tool, verified first?, a request the policy refuses, a request it allows
CASES = [
    ('create_promise', True, dict(product_id='p1', amount=10, date='2026-10-15'), PROMISE),
    ('add_note', True, dict(product_id='p2', reason='forgot'), dict(product_id='p2', reason='refused')),
    ('schedule_callback', False, dict(date='2026-10-20', time='03:00'), dict(date='2026-10-15', time='10:00')),
    ('transfer_to_agent', False, dict(queue='billing'), dict(queue='support')),
    ('verify_identity', False, None, dict(field='phone_last4', answer='4821')),
]


class Faulty(dict):
    """Call variables that raise on their n-th use while a pre-hook runs; with `forever`, on every use after it too."""
    armed, left, fired, forever = False, 0, False, False

    def _use(self):
        if not self.armed:
            return
        if self.fired:
            if self.forever:
                raise RuntimeError('injected fault, still there')
            return
        self.left -= 1
        if self.left <= 0:
            self.fired = True
            raise RuntimeError('injected fault')

    def get(self, *args):
        self._use()
        return dict.get(self, *args)

    def __getitem__(self, key):
        self._use()
        return dict.__getitem__(self, key)

    def __setitem__(self, key, value):
        self._use()
        dict.__setitem__(self, key, value)

    def __contains__(self, key):
        self._use()
        return dict.__contains__(self, key)

    def keys(self):
        self._use()
        return dict.keys(self)


def faulted(api, sim, tool, args, at, forever=False, spoil=None):
    """Play one call with a fault in its pre-hook. Returns whether the fault fired."""
    variables = Faulty(sim.vars)
    variables.left, variables.forever = at, forever
    sim.vars = variables
    real = freya_sim.run

    def run(code, **names):
        pre = 'config' in names
        if pre and spoil:
            spoil(names)
        variables.armed = pre and at > 0
        try:
            real(code, **names)
        finally:
            variables.armed = False

    with mock.patch.object(freya_sim, 'run', run):
        api.call(sim, tool, **args)
    sim.vars = dict(variables)
    return variables.fired


def bank_state(bank):
    return snapshot(bank) + (bank.wrong_left, sorted(bank.passed))


class Faults(unittest.TestCase):
    api = Renamed()

    def start(self, verified):
        sim, bank = self.api.start()
        if verified:
            self.api.verify(sim)
        return sim, bank

    def ready(self, tool, args, verified):
        """A call in which the request under test is the next one: a promise that will be recorded has been checked,
        read back and confirmed, as it must be since M18, so that the fault falls into the hook that is tested."""
        sim, bank = self.start(verified)
        if tool == 'create_promise':
            sim.confirm([self.api.args(**args)])
        sim.compliant = False
        return sim, bank

    def test_a_fault_at_any_point_of_a_pre_hook_leaves_the_bank_untouched(self):
        for tool, verified, refused, allowed in CASES:
            sim, bank = self.ready(tool, allowed, verified)
            sent = len(sim.requests)
            self.api.call(sim, tool, **allowed)
            checked = sim.requests[sent][1]['body']             # what leaves when every check ran
            for args in [a for a in (refused, allowed) if a]:
                for forever in (False, True):
                    points = 0
                    for at in range(1, 120):
                        sim, bank = self.ready(tool, args, verified)
                        was, sent = bank_state(bank), len(sim.requests)
                        if not faulted(self.api, sim, tool, args, at, forever):
                            break                   # the hook uses its variables fewer than `at` times: no fault any more
                        points += 1
                        body = sim.requests[sent][1]['body']
                        if args is refused:
                            self.assertEqual(bank_state(bank), was, (tool, args, at, forever))
                        elif bank_state(bank) != was:
                            # Some faults hit after the last check, when the request is already final.
                            self.assertEqual(body, checked, (tool, at, 'only a fully checked request may still go out'))
                        else:
                            self.assertNotIn(CALL, json.dumps(body), (tool, at))
                    self.assertGreaterEqual(points, 2, (tool, 'the hook was reached'))

    def test_without_a_fault_the_same_requests_go_through(self):
        for tool, verified, _, allowed in CASES:
            sim, bank = self.start(verified)
            was = bank_state(bank)
            self.assertFalse(faulted(self.api, sim, tool, allowed, 0))
            self.assertNotEqual(bank_state(bank), was, tool)

    def test_a_close_whose_check_failed_is_never_sent_and_a_later_sound_close_goes_through(self):
        close = dict(outcome='not_reached', reason='wrong_number')
        for forever in (False, True):
            sim, bank = self.start(False)
            for attempt in range(4):
                self.assertTrue(faulted(self.api, sim, 'close_call', close, 1, forever))
                self.assertEqual((bank.ledger['outcome'], sim.node), (None, 'contact'), (forever, attempt))
        # Fail closed, and not a loop of ours: the runtime retries nothing. Once the fault is gone the same close is checked and sent.
        self.assertEqual(sim.vars.get('rt_fault_close'), None)
        sim, bank = self.start(False)
        self.assertTrue(faulted(self.api, sim, 'close_call', close, 1))
        self.assertEqual(sim.vars.get('rt_fault_close'), 'yes')
        self.api.call(sim, 'close_call', **close)
        self.assertEqual((bank.ledger['outcome'], bank.ledger['reason'], sim.node), ('not_reached', 'wrong_number', 'end'))

    def test_a_request_body_that_is_not_an_object_is_not_sent_as_it_is(self):
        def spoil(names):
            names['config']['body'] = 'customer_id=' + CALL + '&product_id=p1&amount=10'
        for tool, verified, refused, allowed in CASES:
            sim, bank = self.start(verified)
            was, sent = bank_state(bank), len(sim.requests)
            faulted(self.api, sim, tool, allowed, 0, spoil=spoil)
            self.assertEqual(bank_state(bank), was, tool)
            self.assertIsInstance(sim.requests[sent][1]['body'], dict, tool)
            self.assertNotIn(CALL, json.dumps(sim.requests[sent][1]['body']), tool)

    def test_lost_state_does_not_open_the_door(self):
        for lost in ('', 'not json', '[]', '{"products": 5, "identity": "verified"}', '{"identity": "verified", "products": []}'):
            sim, bank = self.start(True)
            was = bank_state(bank)
            sim.vars['rt_state'] = lost
            said = self.api.call(sim, 'create_promise', product_id='p1', amount=10, date='2026-10-15')
            self.assertEqual(bank_state(bank), was, lost)
            self.assertTrue(said.get('rejected_by_policy') or said == {}, lost)

    def test_a_fault_while_reading_what_was_recorded_does_not_allow_a_second_promise(self):
        points = 0
        for at in range(1, 80):
            sim, bank = self.start(True)
            self.api.call(sim, 'create_promise', **PROMISE)
            if not faulted(self.api, sim, 'create_promise', dict(product_id='p1', amount=400, date='2026-10-16'), at):
                break
            points += 1
            self.assertEqual(len(bank.ledger['promises']), 1, at)
        self.assertGreaterEqual(points, 5)

    def test_before_the_correction_the_same_fault_let_the_request_through(self):
        with mock.patch.object(compiler, 'pre_fallback', return_value='pass'):
            old = Renamed()
            sim, bank = old.start()
            old.verify(sim)
        sim.compliant = False           # the one request, as it was sent then: the fault is in its own hook
        was = bank_state(bank)
        self.assertTrue(faulted(old, sim, 'create_promise', dict(product_id='p1', amount=10, date='2026-10-15'), 1))
        self.assertNotEqual(bank_state(bank), was)
        self.assertEqual(bank.ledger['promises'], [{'product_id': 'p1', 'amount': 10, 'date': '2026-10-15'}],
                         'a promise below the floor was recorded because its check had failed')

    def test_the_fallback_uses_only_the_request_and_one_variable(self):
        for role in ('promise', 'close'):
            code = compiler.pre_fallback(role, ['customer_id', 'session_id'])
            self.assertNotIn('CFG', code)
            self.assertNotIn('_rt', code)
            for end in ('{' * 2, '}' * 2):
                self.assertNotIn(end, code)
            config, variables = {'body': {'customer_id': CALL, 'session_id': 's', 'amount': 1}}, {}
            exec(compile(code, '<fallback>', 'exec'), {'__builtins__': __builtins__}, {'config': config, 'vars': variables})
            self.assertEqual(config['body'], {'customer_id': PLACEHOLDER, 'session_id': PLACEHOLDER, 'amount': 1})
            exec(compile(code, '<fallback>', 'exec'), {'__builtins__': __builtins__}, {'config': None, 'vars': None})       # must not raise


class NotCovered(unittest.TestCase):
    """Kept as an expected failure: the platform's contract does not say that a hook always runs."""

    @unittest.expectedFailure
    def test_a_pre_hook_the_platform_does_not_run_cannot_block(self):
        api = Renamed()
        workflow = copy.deepcopy(api.workflow)
        for node in workflow['nodes'].values():
            for tool in node.get('tools') or []:
                tool.pop('pre_process_code', None)
        api._workflow = workflow
        sim, bank = api.start()
        api.verify(sim)
        was = bank_state(bank)
        api.call(sim, 'create_promise', product_id='p1', amount=10, date='2026-10-15')
        self.assertEqual(bank_state(bank), was)


if __name__ == '__main__':
    unittest.main()
