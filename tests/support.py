"""Inputs shared by the tests: the starter kit's tool API file and practice cases."""
import json
from pathlib import Path

KIT = Path('local_freya/starter_kit')
OPENAPI = json.loads((KIT / 'tools/openapi.json').read_text(encoding='utf-8'))
CASES = {c['id']: c for c in json.loads((KIT / 'public-test-set.json').read_text(encoding='utf-8'))}


def refusal(sim, data):
    """The reason of a call the runtime blocked, as the phone model gets it. A blocked call is detached, so the bank
    answers with an error and there is no tool result: the reason is in the live state of the prompt."""
    assert data == {}, 'a blocked call has no tool result: %r' % (data,)
    live = str(sim.vars.get('rt_live') or '')
    assert 'LAST REJECTED TOOL CALL: ' in live, live
    return live.split('LAST REJECTED TOOL CALL: ', 1)[1].split('\n')[0]
