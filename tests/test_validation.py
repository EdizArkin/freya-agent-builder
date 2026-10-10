"""builder/validation: a spec must agree with the tool API; a workflow must obey the platform rules."""
import copy
import json
import unittest

from builder.pipeline import build_workflow
from builder.spec.models import SpecError
from builder.spec.openapi import Api
from builder.validation.spec import validate
from builder.validation.workflow import check_workflow
from tests.fixture_spec import spec as fixture
from tests.support import OPENAPI


def body_schema(doc, tool):
    return doc['paths']['/' + tool]['post']['requestBody']['content']['application/json']['schema']


def problems(spec, doc):
    with unittest.TestCase().assertRaises(SpecError) as ctx:
        validate(spec, Api(doc))
    return ' | '.join(ctx.exception.problems)


def built(brief_b=True):
    return build_workflow(fixture(brief_b), OPENAPI)[0]


class Agreement(unittest.TestCase):
    """The spec may only name fields and values the tool API really has."""

    def test_changed_enums_reach_the_tools_the_model_sees(self):
        doc = copy.deepcopy(OPENAPI)
        body_schema(doc, 'transfer_to_agent')['properties']['queue']['enum'] = ['plans', 'people']
        body_schema(doc, 'add_note')['properties']['reason']['enum'] = ['no', 'later']
        s = fixture(True)
        s['close']['queue_reasons'] = {'plans': 'restructuring', 'people': 'human'}
        workflow, _ = build_workflow(s, doc)
        tools = {t['function']['name']: t['function']['parameters']['properties']['body'] for t in workflow['nodes']['negotiate']['tools']}
        self.assertEqual(tools['transfer_to_agent']['properties']['queue']['enum'], ['plans', 'people'])
        self.assertIn('One of: no, later.', tools['add_note']['properties']['reason']['description'])
        self.assertEqual(tools['create_promise']['required'], ['product_id', 'amount', 'date'])
        self.assertNotIn('description', tools['create_promise']['properties']['customer_id'])
        self.assertEqual(tools['create_promise']['properties']['customer_id']['default'], '{{customer_id}}')

    def test_values_outside_the_api_are_rejected(self):
        s = fixture(True)
        s['close']['queue_reasons']['billing'] = 'human'
        s['close']['forced'].append({'if': 'promise_recorded', 'outcome': 'promise', 'reason': 'made_up'})
        s['close']['forced'].append({'if': 'customer_happy', 'outcome': 'promise'})
        s['close']['forced'].append({'if': 'transferred', 'outcome': 'transferred'})
        s['identity']['response']['hint'] = 'clue'
        s['account']['products_path'] = 'items'
        s['callback']['start'] = '9am'
        s['note']['reason_param'] = 'why'
        text = problems(s, OPENAPI)
        for part in ("unknown queue 'billing'", "reason 'made_up' is not listed for outcome promise", "unknown flag 'customer_happy'",
                     'rule for transferred needs a reason or reason_from', "identity.response.hint='clue'",
                     'does not lead to a list of objects', 'callback.start must be HH:MM', "note.reason_param='why'"):
            self.assertIn(part, text)

    def test_expression_errors_name_the_value(self):
        s = fixture()
        values = s['account']['values']
        values[4]['expr'] = {'kind': 'by', 'cases': [{'when': {'days_late': {'min': 1, 'max': 7}}, 'expr': {'kind': 'business_days_after', 'n': 12}}]}
        self.assertIn('account value deadline cannot be computed for a product like', problems(s, OPENAPI))
        s = fixture()
        s['account']['values'][6]['expr']['not_after'] = 'cutoff'
        s['account']['values'][1]['expr'] = {'kind': 'moon_phase'}
        s['account']['values'][3]['expr']['cases'][0]['when'] = {'colour': 'red'}
        s['account']['limits']['latest_date'] = 'floor'
        s['account']['values'][0]['name'] = 'Label'
        text = problems(s, OPENAPI)
        for part in ("caps at undefined value 'cutoff'", "unknown expression kind 'moon_phase'", "tests unknown product field 'colour'",
                     'snake_case name', 'must define "label"'):
            self.assertIn(part, text)
        s = fixture()
        s['account']['limits']['latest_date'] = 'floor'
        self.assertIn('account.limits.latest_date must be a date', problems(s, OPENAPI))


class Checks(unittest.TestCase):
    def broken(self, change):
        workflow = built()
        change(workflow)
        return ' | '.join(check_workflow(workflow))

    def test_generated_workflows_are_clean_and_not_modified_by_checking(self):
        workflow = built()
        before = json.dumps(workflow, sort_keys=True)
        self.assertEqual(check_workflow(workflow), [])
        self.assertEqual(before, json.dumps(workflow, sort_keys=True, allow_nan=False))
        self.assertNotIn('agent_config', workflow)

    def test_structure_rules(self):
        n = lambda w: w['nodes']
        cases = [
            (lambda w: n(w)['contact'].update(max_turn_transition=None), 'null value at $.nodes.contact.max_turn_transition'),
            (lambda w: w.update(agent_config={}), 'agent_config must be left out'),
            (lambda w: w.update(initial_node='start'), 'initial_node does not exist'),
            (lambda w: n(w)['contact']['transitions'][0].update(target_node='nowhere'), 'contact: transition to unknown node nowhere'),
            (lambda w: n(w)['contact']['transitions'][0]['condition'].update(logic='and'), 'top-level condition logic must be or'),
            (lambda w: n(w)['contact'].update(name='hello'), 'contact: name must equal the node key'),
            (lambda w: n(w).update(island=dict(n(w)['end'], name='island')), 'island: unreachable'),
            (lambda w: n(w)['contact'].update(pre_actions=[{}]), 'pre_actions are stripped from conversation nodes'),
            (lambda w: n(w)['negotiate'].update(transitions=[]), 'negotiate: conversation node cannot be left'),
            (lambda w: n(w)['get_account'].update(tools=[{}]), 'tools are stripped from non-conversation nodes'),
            (lambda w: n(w)['setup']['transitions'][-1].update(condition={'logic': 'or', 'conditions': []}), 'setup: passthrough node must end with an always transition'),
            (lambda w: n(w)['account_facts'].update(passthrough=False), 'account_facts: passthrough node must end'),
            (lambda w: n(w)['end'].update(transitions=[{'target_node': 'end'}]), 'end: end_call needs a terminal and no transitions'),
            (lambda w: n(w)['end'].update(node_type='transfer_call'), 'unsupported node_type transfer_call'),
            (lambda w: n(w)['end'].update(node_type='conversation'), 'no end_call node'),
        ]
        for change, expected in cases:
            self.assertIn(expected, self.broken(change))

    def test_tool_rules(self):
        body = lambda w: w['nodes']['negotiate']['tools'][0]['function']['parameters']['properties']

        def const(w):
            leaf = body(w)['body']['properties']['amount']
            leaf['const'] = leaf.pop('default')
        self.assertIn('body field amount must use default, never const', self.broken(const))
        self.assertIn('api_call create_promise has no baked https url', self.broken(lambda w: body(w)['url'].update(default='http://x.invalid')))
        self.assertIn('double brace in post_process_code', self.broken(lambda w: w['nodes']['negotiate']['tools'][0].update(post_process_code='x = {{y}}')))
        code = lambda w: w['nodes']['setup']['pre_actions'][0]['function']['parameters']['properties']['code']
        self.assertIn('setup: code node needs template-free code', self.broken(lambda w: code(w).update(default="d = {'a': {'b': 1}}")))
        self.assertIn('setup: code node needs template-free code', self.broken(lambda w: code(w).update(default='')))

    def test_documented_placement_of_tools_actions_and_mappings(self):
        workflow = built()
        for name, node in workflow['nodes'].items():
            kind = node['node_type']
            self.assertEqual('tools' in node, kind == 'conversation', name)
            self.assertEqual('pre_actions' in node, kind in ('code', 'api_request'), name)
            if kind in ('code', 'api_request'):
                self.assertEqual(node['on_enter'], {'type': 'silent'})
                self.assertEqual(node['passthrough_default_transition'], node['transitions'][-1]['target_node'])
        account = workflow['nodes']['get_account']
        self.assertTrue(all(m['path'].startswith('data') for m in account['response_mapping']))
        self.assertNotIn('response_mapping', account['pre_actions'][0])
        self.assertEqual(account['pre_actions'][0]['config']['maxRetries'], 2)
        for node in ('contact', 'negotiate'):
            for tool in workflow['nodes'][node]['tools']:
                self.assertEqual(tool['config']['maxRetries'], 0, 'a write must never be retried by the engine')
                self.assertIn('CALL', tool['function']['description'])
        self.assertEqual(workflow['nodes']['end']['terminal']['function']['parameters']['properties']['message_mode']['default'], 'none')


if __name__ == '__main__':
    unittest.main()
