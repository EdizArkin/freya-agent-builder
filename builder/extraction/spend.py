"""Paid model calls behind a spending limit, and what they cost.

The builder calls a model in two places: the extraction of a spec from a brief, and the proposal of a candidate
in an optimization run. `Metered` puts either behind a limit that cannot be passed; `Budget` keeps that limit
across commands of one run by writing every call down. The benchmark harness measures its experiments with the
same meter.
"""
import json
from pathlib import Path

# Dollars per million tokens, input and output, of the Anthropic API; compared with the published price list on
# 2026-10-11. Thinking is billed as output. No caching, batch, fast mode or US-only inference is used.
PRICES = {'claude-sonnet-5-5': (2.0, 10.0), 'claude-opus-5-5': (4.0, 20.0), 'claude-haiku-5-5': (0.1, 0.5)}
DEAREST = (4.0, 20.0)


def price(model):
    """The price of a model, also when the API names it with a date. An unknown model is taken as the dearest."""
    name = str(model or '')
    return next((p for known, p in PRICES.items() if name.startswith(known)), DEAREST)


class BudgetExceeded(RuntimeError):
    pass


def dollars(usage, model=None):
    total = 0.0
    for u in usage:
        price_in, price_out = price(u.get('model') or model)
        total += (u.get('input_tokens') or 0) * price_in / 1e6 + (u.get('output_tokens') or 0) * price_out / 1e6
    return round(total, 4)


class Metered:
    """A model client behind a spending limit that cannot be passed.

    Before every call the most that call can cost is worked out: every character of the request counted as
    half a token, which is more than any text comes to, and the reply taken at the client's output cap. The
    call is made only if what is spent plus that amount stays inside the limit. A call that ends in an error
    is charged at that amount, since the API may have billed it and reported nothing."""

    def __init__(self, client, max_dollars=None, max_calls=None):
        self.client, self.max_dollars, self.max_calls = client, max_dollars, max_calls
        self.model, self.usage = getattr(client, 'model', None), []

    def most(self, system, messages):
        """(input tokens, output tokens) the next call cannot exceed."""
        chars = len(system) + sum(len(m['content']) if isinstance(m['content'], str) else len(json.dumps(m['content'], default=str)) for m in messages)
        return chars // 2 + 500, int(getattr(self.client, 'max_tokens', 0) or 0)

    def complete(self, system, messages):
        spent = dollars(self.usage, self.model)
        tokens_in, tokens_out = self.most(system, messages)
        reserve = dollars([{'input_tokens': tokens_in, 'output_tokens': tokens_out}], self.model)
        if self.max_dollars is not None and (spent >= self.max_dollars or spent + reserve > self.max_dollars):
            raise BudgetExceeded('the limit of %.2f dollars does not cover another call: %.2f spent in %d calls, the next could cost up to %.2f' % (
                self.max_dollars, spent, len(self.usage), reserve))
        if self.max_calls is not None and len(self.usage) >= self.max_calls:
            raise BudgetExceeded('the limit of %d model calls is reached' % self.max_calls)
        try:
            text, used, content = self.client.complete(system, messages)
        except Exception:
            self.usage.append({'model': self.model, 'input_tokens': tokens_in, 'output_tokens': tokens_out, 'stop_reason': 'error', 'assumed': True})
            raise
        self.usage.append(dict(used))
        return text, used, content


class Budget(Metered):
    """The spending limit of a run, across commands: what earlier commands spent is read before the first call
    and every call is written down before the next one. The rest is the meter of the experiments: a call is
    made only if the limit covers it at its most, and a failed call is charged at that amount."""

    def __init__(self, client, max_dollars, ledger):
        Metered.__init__(self, client, max_dollars)
        self.ledger = Path(ledger)
        self.usage = json.loads(self.ledger.read_text(encoding='utf-8')) if self.ledger.is_file() else []
        self.before = len(self.usage)

    def complete(self, system, messages):
        try:
            return Metered.complete(self, system, messages)
        finally:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            self.ledger.write_text(json.dumps(self.usage, indent=1), encoding='utf-8')


def spent(ledger):
    usage = json.loads(Path(ledger).read_text(encoding='utf-8')) if Path(ledger).is_file() else []
    return dollars(usage), len(usage)
