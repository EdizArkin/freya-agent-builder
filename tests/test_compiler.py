"""builder/compiler: what a compiled workflow contains, and the code embedded in it."""
import ast
import json
import re
import unittest

from builder.pipeline import build_workflow
from builder.spec.models import SpecError
from builder.validation.workflow import check_workflow
from tests.fixture_spec import spec as fixture
from tests.freya_sim import Bank, Sim
from tests.support import OPENAPI


def built(brief_b=True):
    return build_workflow(fixture(brief_b), OPENAPI)[0]


def snippets(workflow):
    for node in workflow['nodes'].values():
        for tool in (node.get('tools') or []) + (node.get('pre_actions') or []):
            for key in ('pre_process_code', 'post_process_code'):
                if key in tool:
                    yield node['name'] + '.' + tool['function']['name'] + '.' + key, tool[key]
            code = tool['function']['parameters']['properties'].get('code')
            if code:
                yield node['name'] + '.code', code['default']


class Workflow(unittest.TestCase):
    def test_compiles_and_passes_local_checks(self):
        for b in (False, True):
            workflow, report = build_workflow(fixture(b), OPENAPI)
            self.assertEqual(check_workflow(workflow), [])
            self.assertEqual('add_note' in report['tools']['negotiate'], b)
            self.assertNotIn('create_promise', report['tools']['contact'])
            self.assertNotIn('get_account', report['tools']['contact'] + report['tools']['negotiate'])
            self.assertNotIn('null', json.dumps(workflow).replace('"null"', ''))

    def test_spec_errors_are_explicit(self):
        bad = fixture()
        bad['identity']['questions'][0]['field'] = 'postcode'
        bad['close']['outcomes']['vanished'] = []
        bad['account']['values'][2]['expr'] = {'kind': 'field', 'field': 'nope'}
        bad['unsupported'] = ['a rule the runtime cannot hold']
        with self.assertRaises(SpecError) as ctx:
            build_workflow(bad, OPENAPI)
        text = str(ctx.exception)
        for part in ('postcode', 'vanished', 'nope', 'cannot hold'):
            self.assertIn(part, text)

    def test_another_brief_shape_needs_no_source_change(self):
        """Different tool fields, identity questions, calendar, floor rule and outcome table."""
        doc = json.loads(json.dumps(OPENAPI).replace('"field"', '"question"').replace('wrong_answers_left', 'tries_left')
                         .replace('"queue"', '"desk"').replace('past_due', 'arrears'))
        doc['paths']['/verify_identity']['post']['requestBody']['content']['application/json']['schema']['properties']['question']['enum'] = ['postcode', 'birth_year']
        doc['paths']['/close_call']['post']['requestBody']['content']['application/json']['schema']['properties']['outcome']['enum'] = ['agreed', 'unreached', 'declined']
        s = fixture()
        s['calendar'] = {'business_weekdays': [0, 1, 2, 3, 4, 5], 'holidays': ['2026-10-15']}
        s['identity'].update(field_param='question', questions=[{'field': 'postcode', 'ask': 'postcode', 'format': 'text'},
                                                                {'field': 'birth_year', 'ask': 'year of birth', 'format': 'digits'}])
        s['identity']['response']['wrong_left'] = 'tries_left'
        s['transfer'] = {'queue_param': 'desk'}
        s['account']['values'][1]['expr'] = {'kind': 'field', 'field': 'arrears'}
        s['account']['values'][2]['expr'] = {'kind': 'percent', 'of': {'kind': 'field', 'field': 'arrears'}, 'percent': 25}
        s['account']['values'][4]['expr'] = {'kind': 'calendar_days_after', 'n': 10, 'roll': 'previous_business'}
        s['close'].update(outcomes={'agreed': [], 'unreached': ['later'], 'declined': ['no']},
                          forced=[{'if': 'promise_recorded', 'outcome': 'agreed'}], queue_reasons={})
        workflow, _ = build_workflow(s, doc)
        bank = Bank('c1', {'postcode': 'AB1', 'birth_year': '1980'}, [])
        sim = Sim(workflow, bank, {'now': '2026-10-14T11:00'})
        self.assertIn('2026-10-17 Saturday 17 October - business day', sim.vars['rt_calendar'])
        self.assertIn('2026-10-15 Thursday 15 October - NOT a business day (bank holiday)', sim.vars['rt_calendar'])


class EmbeddedCode(unittest.TestCase):
    def test_every_snippet_is_plain_sandbox_safe_python(self):
        seen = 0
        for where, code in snippets(built()):
            seen += 1
            self.assertNotIn('{{', code, where)
            self.assertNotIn('}}', code, where)
            tree = ast.parse(code)
            imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)], [], where)
            self.assertLessEqual(imported, {'json', 're', 'datetime'}, where)
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            self.assertFalse(names & {'open', 'eval', 'exec', '__import__', 'compile', 'globals', 'os', 'sys'}, where)
            self.assertIsInstance(tree.body[-1], ast.Try, where + ': the whole snippet must be guarded')
        self.assertGreaterEqual(seen, 16)

    def test_a_snippet_carries_only_the_functions_it_needs(self):
        codes = dict(snippets(built()))
        close = codes['negotiate.close_call.pre_process_code']
        self.assertIn('def forced_close', close)
        self.assertNotIn('def calendar_text', close)
        self.assertNotIn('def pre_promise', close)
        self.assertNotIn('"identity"', close)

    def test_every_prompt_variable_is_set_before_the_model_sees_it(self):
        workflow = built()
        sim = Sim(workflow, Bank('c1', {'date_of_birth': '1990-01-01', 'phone_last4': '1234'}, []), {'now': '2026-10-14T11:00', 'title': 'Ms', 'last_name': 'Lee'})
        texts = [workflow['system_prompt']] + [n.get('task_message', '') + json.dumps(n.get('on_enter')) for n in workflow['nodes'].values()]
        used = set(re.findall(r'\{\{([A-Za-z0-9_.]+)\}\}', ' '.join(texts)))
        self.assertTrue({'rt_today', 'rt_calendar', 'rt_callback', 'rt_live', 'rt_account_facts'} <= used)
        self.assertEqual(used - set(sim.vars), set())

    def test_setup_survives_a_missing_or_broken_clock(self):
        for now in (None, '', 'tomorrow-ish', '2026-13-45T99:99', 12):
            variables = {} if now is None else {'now': now}
            sim = Sim(built(), Bank('c1', {'date_of_birth': '1990-01-01', 'phone_last4': '1234'}, []), variables)
            self.assertEqual(sim.node, 'contact')
            for key in ('rt_state', 'rt_identity', 'rt_closed', 'rt_calendar', 'rt_callback', 'rt_today', 'rt_live'):
                self.assertIsInstance(sim.vars[key], str)
            self.assertEqual(sim.vars['rt_identity'], 'pending')


if __name__ == '__main__':
    unittest.main()
