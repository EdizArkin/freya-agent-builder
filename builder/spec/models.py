"""agent-spec/1: the one executable contract shared by extraction, validation and the compiler.

A spec is a plain JSON object (a dict). Structured fields drive the deterministic
runtime; consolidated rule text drives the phone model. This module names the
vocabulary of the format and walks its expressions. Nothing here knows a brief.
"""
from builder.runtime import runtime as rt


VERSION = 'agent-spec/1'
TOOLS = ('verify_identity', 'get_account', 'create_promise', 'add_note', 'schedule_callback',
         'transfer_to_agent', 'close_call')
EXPR_KINDS = ('by', 'by_time', 'const', 'text', 'field', 'percent', 'fixed_amount') + rt.DATE_KINDS
FLAGS = ('transferred', 'promise_recorded', 'identity_failed', 'callback_booked', 'all_products_noted', 'any_note', 'note_recorded')
NORMALIZERS = ('uppercase', 'lowercase', 'remove_spaces', 'alphanumeric')
TIME_TESTS = ('from', 'after', 'until', 'before')
GUIDANCE_EVENTS = (
    'identity.next_question', 'identity.all_verified', 'identity.wrong', 'identity.failed',
    'identity.unreadable', 'identity.service_error',
    'promise.recorded_more', 'promise.recorded_all', 'promise.failed_retry', 'promise.failed_final', 'promise.not_allowed',
    'note.recorded_more', 'note.recorded_all', 'note.failed',
    'callback.booked', 'callback.failed', 'transfer.done', 'transfer.failed')


class SpecError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


def exprs(expr):
    if isinstance(expr, dict):
        yield expr
        for case in expr.get('cases') or []:
            for e in exprs(case.get('expr')):
                yield e
        for e in expr.get('of') if isinstance(expr.get('of'), list) else [expr.get('of')]:
            for x in exprs(e):
                yield x


def whens(expr):
    for e in exprs(expr):
        if e.get('kind') == 'by_time':
            continue        # its cases test the clock, not a product
        for case in e.get('cases') or []:
            if isinstance(case.get('when'), dict):
                yield case['when']


def times(expr):
    """The clock tests of every by_time expression inside `expr`."""
    for e in exprs(expr):
        if e.get('kind') == 'by_time':
            for case in e.get('cases') or []:
                if isinstance(case.get('when'), dict):
                    yield case['when']
