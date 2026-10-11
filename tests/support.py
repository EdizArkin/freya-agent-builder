"""Inputs shared by the tests: the starter kit's tool API file and practice cases.

The kit is not part of this repository (it is the organisers' material and stays local). Almost every test
compiles against its tool API, so without it a test module is skipped with this reason instead of failing on
an import: `python -m unittest discover -s tests -t .` then reports skipped modules, not errors.
"""
import json
import unittest

from builder.config import settings

KIT = settings.KIT
if not (KIT / 'tools' / 'openapi.json').is_file() or not (KIT / 'public-test-set.json').is_file():
    raise unittest.SkipTest('the starter kit is not at %s: these tests need its tools/openapi.json and public-test-set.json' % KIT)
OPENAPI = json.loads((KIT / 'tools/openapi.json').read_text(encoding='utf-8'))
CASES = {c['id']: c for c in json.loads((KIT / 'public-test-set.json').read_text(encoding='utf-8'))}


def refusal(sim, data):
    """The reason of a call the runtime blocked, as the phone model gets it. A blocked call is detached, so the bank
    answers with an error and there is no tool result: the reason is in the live state of the prompt."""
    assert data == {}, 'a blocked call has no tool result: %r' % (data,)
    live = str(sim.vars.get('rt_live') or '')
    assert 'LAST REJECTED TOOL CALL: ' in live, live
    return live.split('LAST REJECTED TOOL CALL: ', 1)[1].split('\n')[0]
